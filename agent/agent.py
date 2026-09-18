# -*- coding: utf-8 -*-
"""
agent.py —— 基于 openJiuwen 的「个人学习规划 Agent」（决策层）。

使用 openJiuwen 新 API（AgentCard + ReActAgentConfig + ReActAgent），把
skills.py 的两个 Skill 包装成工具，让 Agent 在 ReAct 循环里自主完成：

    思考 → 调用 diagnose（学习诊断）→ 观察 → 调用 replan（动态重规划）→ 观察 → 输出决策日志

这体现了 openJiuwen 的作用：它是「决策编排层」，负责在合适时机调度 Skill、
管理 ReAct 循环、把工具结果喂回模型，最终生成带理由与证据的可解释决策日志；
而 skills.py 是「可解释规则引擎」，保证每一步调整都有据可查。
"""

import logging
import os

import requests

# 静默 openjiuwen 导入时的 INFO 日志（CLI / 演示输出更干净；WARNING/ERROR 仍显示）
logging.disable(logging.WARNING)

from dotenv import load_dotenv

from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.foundation.tool import tool

import skills

SYSTEM_PROMPT = (
    "你是考研学习规划 Agent 的决策层。用户会给你当前学习状态摘要，"
    "你可以按需调用以下工具（Skill）："
    "1) diagnose_skill 学习诊断；2) replan_skill 动态重规划；"
    "3) allocate_skill 多科目冲突时的全局资源再分配；"
    "4) interpret_feedback_skill 理解用户自然语言反馈；"
    "5) proactive_scan_skill 事前冲突扫描与削峰填谷；"
    "6) reschedule_for_calendar_change_skill 课表变动时重排受影响任务。"
    "请基于工具返回的数据，用中文输出一份可解释决策日志，"
    "逐条说明「调整了什么、为什么调整、依据是什么」，并引用反思原文。"
    "不要编造工具结果，只基于工具返回的数据作答。"
)

FEEDBACK_SYSTEM_PROMPT = (
    "你是学习规划 Agent 的反馈理解器。用户会给你一段学习反思原文，"
    "请抽取为 JSON：{\"attribution\": 归因(方法/时间/目标/其他), "
    "\"weak_topics\": [弱知识点列表], \"mood\": 情绪(低落/焦虑/平静/积极), "
    "\"suggestion\": 一句话建议}。只输出 JSON，不要多余文字。"
)


def _load_env():
    """读取 .env，返回模型连接配置；缺少 key 时 ready=False（demo 走离线降级）。"""
    load_dotenv()
    provider = os.getenv("MODEL_PROVIDER", "siliconflow")
    api_base = os.getenv("API_BASE", "https://api.siliconflow.cn/v1")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "Qwen/Qwen3-32B")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "false").lower() == "true"
    ready = bool(api_key) and not api_key.startswith("sk-xxxx")
    return {
        "provider": provider,
        "api_base": api_base,
        "api_key": api_key,
        "model_name": model_name,
        "verify_ssl": verify_ssl,
        "ready": ready,
    }


class LearningPlannerAgent:
    """把 memory 状态绑定到 openJiuwen ReActAgent 上，对外提供 decide() 决策入口。"""

    def __init__(self, memory):
        self.memory = memory          # 复用同一份 dict，工具闭包实时读到最新状态
        self.env = _load_env()
        self._llm_fail_streak = 0     # interpret_feedback 的连续失败计数（≥3 次触发降级）
        self.agent = self._build_agent()

    # ---------- 工具：用闭包绑定当前 memory，读取最新状态 ----------
    def _build_agent(self):
        @tool(description=(
            "对当前学习记忆做诊断：返回整体状态、预警科目"
            "（严重度 / 连续失败天数 / 近7天完成率 / 原因 / 主因 / 反思原文引用）与健康科目。"
        ))
        def diagnose_skill() -> dict:
            return skills.diagnose(
                self.memory.get("learning_memory", {}),
                self.memory.get("rules"),
            )

        @tool(description=(
            "根据最新诊断动态重规划当前学习计划：返回调整后的计划与逐条调整明细"
            "（task_id / 调整前 / 调整后 / 理由 / 依据）。"
        ))
        def replan_skill() -> dict:
            diag = skills.diagnose(
                self.memory.get("learning_memory", {}),
                self.memory.get("rules"),
            )
            replan_count = len(self.memory.get("replanning_log", []))
            new_plan, adjustments = skills.replan(
                self.memory.get("current_plan", {}),
                diag,
                self.memory.get("learning_memory", {}),
                replan_count,
                rules=self.memory.get("rules"),
            )
            return {"diagnosis": diag, "new_plan": new_plan, "adjustments": adjustments}

        # 升级 ①：多科目冲突 → 全局资源再分配
        @tool(description=(
            "多科目冲突时做全局资源再分配：按 优先级×阶段紧迫度×连续失败严重度 计算每科权重，"
            "在每日可用时长内给每科分配时长（带最低保底 0.5h），返回每科分配时长与可解释理由。"
        ))
        def allocate_skill() -> dict:
            return skills.allocate(
                self.memory.get("learning_memory", {}),
                self.memory.get("current_plan", {}),
                self.memory.get("user_profile", {}).get("daily_available_hours", 6),
                rules=self.memory.get("rules"),
            )

        # 升级 ②：用户自然语言反馈理解
        @tool(description=(
            "理解用户自然语言反馈（反思/碎碎念原文）：抽取结构化信号"
            "（归因 / 弱知识点 / 情绪 / 建议）。"
        ))
        def interpret_feedback_skill(text: str) -> dict:
            return self._interpret_feedback(text)

        # 升级 ⑤：事前冲突处理
        @tool(description=(
            "事前冲突扫描：预测未来 7 天计划负荷冲突，输出削峰填谷方案与降级决策"
            "（带负载上限 = 每日可用时长 × 1.2）。"
        ))
        def proactive_scan_skill(days: list, backlog: int = 0) -> dict:
            return skills.proactive_scan(
                days, backlog=backlog,
                daily_available_hours=self.memory.get("user_profile", {}).get("daily_available_hours", 6),
                rules=self.memory.get("rules"),
            )

        # 升级 ⑦：课表变动 → 冲突检测 + 重排
        @tool(description=(
            "课表变动处理：对比旧课表与新课表（version 变化即自动触发），"
            "检测冲突、就近平移重排未来受影响日期的学习任务，必要时触发 allocate 全局再分配。"
        ))
        def reschedule_for_calendar_change_skill(old_schedule: dict, new_schedule: dict) -> dict:
            return skills.reschedule_for_calendar_change(
                old_schedule, new_schedule,
                self.memory.get("current_plan", {}),
                self.memory.get("learning_memory", {}),
                daily_available_hours=self.memory.get("user_profile", {}).get("daily_available_hours", 6),
                rules=self.memory.get("rules"),
            )

        # 新 API：AgentCard + ReActAgentConfig + ReActAgent
        config = ReActAgentConfig()
        if self.env["ready"]:
            config.configure_model_client(
                provider=self.env["provider"],
                api_key=self.env["api_key"],
                api_base=self.env["api_base"],
                model_name=self.env["model_name"],
                verify_ssl=self.env["verify_ssl"],
            )
        config.configure_prompt_template([{"role": "system", "content": SYSTEM_PROMPT}])
        config.configure_max_iterations(8)

        card = AgentCard(
            id="learning_planner",
            name="学习规划 Agent",
            description="基于 openJiuwen 的个人学习规划 Agent：记忆学习状态，动态规划与调整考研学习路径。",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户的学习规划请求"}
                },
                "required": ["query"],
            },
            output_params={
                "type": "object",
                "properties": {
                    "decision_log": {"type": "string", "description": "可解释的决策日志"}
                },
            },
        )

        agent = ReActAgent(card)
        agent.configure(config)
        # 注册 6 个 Skill 工具（stateful，绑定到本 agent）
        agent.ability_manager.add_ability(diagnose_skill.card, diagnose_skill)
        agent.ability_manager.add_ability(replan_skill.card, replan_skill)
        agent.ability_manager.add_ability(allocate_skill.card, allocate_skill)
        agent.ability_manager.add_ability(interpret_feedback_skill.card, interpret_feedback_skill)
        agent.ability_manager.add_ability(proactive_scan_skill.card, proactive_scan_skill)
        agent.ability_manager.add_ability(reschedule_for_calendar_change_skill.card, reschedule_for_calendar_change_skill)
        return agent

    # ---------- 确定性 Skill 执行：保证 demo 即使无 LLM 也能跑通 ----------
    def run_skills(self):
        """直接执行两个 Skill，返回结构化结果（用于打印调整前后对比）。"""
        diag = skills.diagnose(
            self.memory.get("learning_memory", {}),
            self.memory.get("rules"),
        )
        replan_count = len(self.memory.get("replanning_log", []))
        new_plan, adjustments = skills.replan(
            self.memory.get("current_plan", {}),
            diag,
            self.memory.get("learning_memory", {}),
            replan_count,
            rules=self.memory.get("rules"),
        )
        return {"diagnosis": diag, "new_plan": new_plan, "adjustments": adjustments}

    # ---------- 升级 ②：自然语言反馈的 LLM 抽取（带 4 条降级条件） ----------
    def _call_llm_json(self, prompt):
        """同步调用 LLM（OpenAI 兼容 /chat/completions），返回内容字符串；失败抛异常。"""
        if not self.env["ready"]:
            raise RuntimeError("未配置 API key")
        url = self.env["api_base"].rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": "Bearer " + self.env["api_key"],
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.env["model_name"],
            "messages": [
                {"role": "system", "content": FEEDBACK_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=5)  # 降级条件①：超时 > 5s
        resp.raise_for_status()  # 降级条件②：API 返回错误
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _interpret_feedback(self, text):
        """Skill 4 执行体：优先 LLM 抽取，满足任一降级条件则走关键词匹配。"""
        # 降级条件④：连续失败 3 次
        if self._llm_fail_streak >= 3:
            result = skills.interpret_feedback(text)
            result["degraded"] = True
            result["degrade_reason"] = "LLM 调用失败（连续失败 3 次），已降级为关键词匹配模式"
            return result
        # 降级条件③：未配置 API key
        if not self.env["ready"]:
            result = skills.interpret_feedback(text)
            result["degraded"] = True
            result["degrade_reason"] = "LLM 调用失败（未配置 API key），已降级为关键词匹配模式"
            return result
        try:
            llm_text = self._call_llm_json(text)
            result = skills.interpret_feedback(text, llm_text)
            if result.get("source") != "llm":
                raise RuntimeError("LLM 输出解析失败")
            self._llm_fail_streak = 0
            result["degraded"] = False
            return result
        except requests.exceptions.Timeout:
            self._llm_fail_streak += 1
            result = skills.interpret_feedback(text)
            result["degraded"] = True
            result["degrade_reason"] = "LLM 调用失败（超时 > 5s），已降级为关键词匹配模式"
            return result
        except Exception as exc:
            self._llm_fail_streak += 1
            result = skills.interpret_feedback(text)
            result["degraded"] = True
            result["degrade_reason"] = f"LLM 调用失败（API 返回错误：{exc}），已降级为关键词匹配模式"
            return result

    @staticmethod
    def _deterministic_log(structured):
        """离线/降级时的确定性决策日志（规则生成，非 LLM）。"""
        diag = structured["diagnosis"]
        lines = ["（离线降级：以下为确定性规则输出，未调用 LLM）"]
        for a in diag["alert_subjects"]:
            lines.append(
                f"【{a['subject']}】严重度 {a['severity']}，连续失败 {a['consecutive_failures']} 天，"
                f"主因：{a['primary_cause']}。"
            )
        for adj in structured["adjustments"]:
            ev = adj.get("evidence", {})
            if isinstance(ev, dict):
                parts = [
                    f"连续失败 {ev.get('consecutive_failures')} 天",
                    f"完成率 {ev.get('completion_rate', 0):.0%}",
                ]
                quotes = ev.get("reflection_quotes") or []
                if quotes:
                    parts.append(f"反思「{quotes[-1]}」")
                parts.append(f"结论：{ev.get('conclusion', '')}")
                ev_str = "；".join(parts)
            else:
                ev_str = ev
            lines.append(f"调整 {adj['task_id']}：{adj['reason']} 依据：{ev_str}")
        return "\n".join(lines)

    async def decide(self, query):
        """决策入口：先确定性执行 Skill（保证数据正确），再调用 openJiuwen 生成决策日志。"""
        structured = self.run_skills()

        if not self.env["ready"]:
            return {**structured, "decision_log": self._deterministic_log(structured), "used_llm": False}

        try:
            result = await self.agent.invoke({"query": query})
            output = result.get("output", "")
            if result.get("result_type") == "error" or not output:
                return {**structured, "decision_log": self._deterministic_log(structured), "used_llm": False}
            return {**structured, "decision_log": output, "used_llm": True}
        except Exception as exc:  # 网络 / API 异常时降级，保证 demo 能跑完
            return {
                **structured,
                "decision_log": self._deterministic_log(structured) + f"\n（LLM 调用失败：{exc}）",
                "used_llm": False,
            }


def _today():
    from datetime import date
    return date.today().isoformat()


def _build_cli_query(memory, diagnosis):
    """给 --llm 模式的当日状态摘要。"""
    goal = memory.get("user_profile", {}).get("goal", "")
    alert = diagnosis["alert_subjects"][0] if diagnosis["alert_subjects"] else None
    if alert:
        return (
            f"用户目标是「{goal}」。{alert['subject']}已连续未完成 {alert['consecutive_failures']} 天，"
            f"近 7 天完成率 {alert['recent_7d_completion_rate']:.0%}。"
            f"请先调用 diagnose_skill 再调用 replan_skill，然后输出中文决策日志。"
        )
    return f"用户目标是「{goal}」。请先调用 diagnose_skill 再调用 replan_skill，然后输出中文决策日志。"


def main():
    """CLI 入口：读取 HTML 端导出的 memory.json，跑 diagnose + replan，输出 new_plan.json。"""
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(
        description="基于 openJiuwen 的学习规划 Agent CLI：读取 memory.json，运行 diagnose + replan，输出 new_plan.json"
    )
    parser.add_argument("input", nargs="?", default="memory.json", help="HTML 端导出的记忆文件")
    parser.add_argument("-o", "--output", default="new_plan.json", help="输出文件（默认 new_plan.json）")
    parser.add_argument("--llm", action="store_true", help="用 openJiuwen + LLM 生成决策日志（默认走确定性规则，无需 API）")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        memory = json.load(f)

    planner = LearningPlannerAgent(memory)
    structured = planner.run_skills()
    diagnosis = structured["diagnosis"]
    new_plan = structured["new_plan"]
    adjustments = structured["adjustments"]

    alert = diagnosis["alert_subjects"][0] if diagnosis["alert_subjects"] else None
    trigger = f"连续 {alert['consecutive_failures']} 天未完成" if alert else "无预警科目"

    replanning_log = list(memory.get("replanning_log", [])) + [{
        "date": memory.get("current_plan", {}).get("date") or _today(),
        "trigger": trigger,
        "adjustments": adjustments,
    }]

    if args.llm:
        decision_log = asyncio.run(planner.decide(_build_cli_query(memory, diagnosis)))["decision_log"]
    else:
        decision_log = planner._deterministic_log(structured)

    out = {
        "user_profile": memory.get("user_profile", {}),
        "current_plan": new_plan,
        "replanning_log": replanning_log,
        "diagnosis": diagnosis,
        "decision_log": decision_log,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("✅ 已生成", args.output)
    print("   调整条数：", len(adjustments))
    if alert:
        print(f"   预警科目：{alert['subject']}（连续失败 {alert['consecutive_failures']} 天，主因：{alert['primary_cause']}）")
    for adj in adjustments:
        print(f"   - [{adj['subject']}] {adj['before']}  →  {adj['after']}")


if __name__ == "__main__":
    main()
