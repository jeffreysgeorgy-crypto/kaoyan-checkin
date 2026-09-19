# -*- coding: utf-8 -*-
"""
agent.py —— 基于 openJiuwen 的「个人学习规划 Agent」（决策层）。

使用 openJiuwen 新 API（AgentCard + ReActAgentConfig + ReActAgent），把
skills.py 的 8 个 Skill 包装成工具，让 Agent 在 ReAct 循环里自主完成：

    思考 → 调用 diagnose（学习诊断）→ 观察 → 调用 replan（动态重规划）→ 观察 → 输出决策日志

这体现了 openJiuwen 的作用：它是「决策编排层」，负责在合适时机调度 Skill、
管理 ReAct 循环、把工具结果喂回模型，最终生成带理由与证据的可解释决策日志；
而 skills.py 是「可解释规则引擎」，保证每一步调整都有据可查。
"""

import json
import logging
import os
import re

import requests

# 静默 openjiuwen 导入时的 INFO 日志（CLI / 演示输出更干净；WARNING/ERROR 仍显示）
logging.disable(logging.WARNING)

from dotenv import load_dotenv

from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.core.foundation.tool import tool

import skills

SYSTEM_PROMPT = (
    "你是考研学习规划 Agent 的决策层。用户会给你当前学习状态摘要，"
    "你可以按需调用以下工具（Skill）："
    "1) diagnose_skill 学习诊断；2) replan_skill 动态重规划；"
    "3) allocate_skill 多科目冲突时的全局资源再分配；"
    "4) interpret_feedback_skill 理解用户自然语言反馈；"
    "5) proactive_scan_skill 事前冲突扫描与削峰填谷；"
    "6) reschedule_for_calendar_change_skill 课表变动时重排受影响任务；"
    "7) decompose_goal_skill 目标拆解与模块覆盖检查；"
    "8) aggregate_resources_skill 按弱知识点聚合分散资源。"
    "请基于工具返回的数据，用中文输出一份可解释决策日志，"
    "逐条说明「调整了什么、为什么调整、依据是什么」，只引用工具返回的提炼结论"
    "（主因/次因/弱知识点/情绪），不要搬运或复述反思原文。"
    "不要编造工具结果，只基于工具返回的数据作答。"
)

FEEDBACK_SYSTEM_PROMPT = (
    "你是学习规划 Agent 的反馈理解器。用户会给你一段学习反思原文，"
    "请从「方法不当 / 情绪干扰 / 精力不足 / 目标不清晰 / 时间投入不足 / "
    "任务过载 / 基础薄弱 / 环境干扰」等维度判断归因，"
    "并只输出一个 JSON，不要多余文字，格式："
    "{\"primary_cause\": 主因, \"secondary_cause\": 次因(可为空字符串), "
    "\"weak_topics\": [弱知识点列表], \"emotion\": 情绪(低落/焦虑/疲惫/积极/平静), "
    "\"evidence_summary\": 一句话提炼结论}。"
    "注意：不要复述反思原文，evidence_summary 要概括而不是逐字引用。"
)

# ---------- 自由对话（前端聊天页）----------
# 意图清单：规则层按意图执行对应 Skill，LLM 只负责把结构化结果讲成口语
CHAT_INTENT_RULES = """\
status  询问当前学习状态/诊断/最近学得怎么样
plan    觉得计划跟不上，要求重新规划或调整计划
load    询问未来几天忙不忙/任务负荷/时间够不够
goal    询问考研目标还差哪些模块/当前进度
resource 某知识点学不懂/不会，想要学习资源或补弱方法
emotion 表达疲惫/焦虑/烦躁/不想学/坚持不下去等情绪
chat    其他闲聊、打招呼、求鼓励"""

CHAT_INTENT_PROMPT = (
    "你是学习规划 Agent 的意图识别器。用户会用任意自然语言说话，"
    "请只输出一个 JSON，不要多余文字：\n"
    + CHAT_INTENT_RULES + "\n"
    "格式：{\"intent\": 上述 7 个意图之一, \"subject\": 涉及科目(没有则空字符串), "
    "\"topic\": 涉及知识点(没有则空字符串)}。"
)

CHAT_REPLY_PROMPT = (
    "你是一个陪伴考研学生的个人学习规划 Agent，像耐心的学长/学姐一样用中文口语聊天，"
    "亲切但不啰嗦（一般 2~4 句）。你会收到「用户的话 + 刚用规则引擎算出的结构化分析结果 JSON」。"
    "要求：①必须依据 JSON 里的真实数据回答，数字和科目名不许编造；"
    "②结构为空时可正常闲聊鼓励，但不要虚构学习数据或承诺；"
    "③结构化结果里没有的具体书名、网课、老师、链接一律不许自行编造；"
    "数据不足时（如资源类结果为空）说明缺什么数据，并引导用户先做前置操作（如去错题本给错题打知识点标签）；"
    "④情绪类先共情再给一条具体的小建议；"
    "⑤plan 类要说明给出了什么调整，并提醒对方需要点确认才会应用到计划；"
    "⑥不要输出 JSON、不要分点编号，自然成段。"
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
        self.trace = []               # ReAct 编排轨迹：思考 → 选工具 → 观察 → 输出
        self._trace_hooks_registered = False

    # ---------- 工具：用闭包绑定当前 memory，读取最新状态 ----------
    def _build_agent(self):
        @tool(description=(
            "对当前学习记忆做诊断：返回整体状态、预警科目"
            "（严重度 / 连续失败天数 / 近7天完成率 / 原因 / 多维度归因）与健康科目。"
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
                user_profile=self.memory.get("user_profile"),
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
            "（主因 / 次因 / 弱知识点 / 情绪 / 提炼结论）。"
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

        # 升级 ⑧：目标拆解（命题背景「目标不清」）——检查大目标是否被完整拆成模块
        @tool(description=(
            "目标拆解：把考研大目标拆成知识模块 + 阶段里程碑，检查当前计划的覆盖缺口"
            "（需覆盖模块 / 已覆盖 / 缺失模块 / 当前阶段 / 建议）。"
        ))
        def decompose_goal_skill() -> dict:
            return skills.decompose_goal(
                self.memory.get("user_profile", {}),
                self.memory.get("current_plan", {}),
                self.memory.get("learning_memory", {}),
                rules=self.memory.get("rules"),
                schedule=self.memory.get("schedule"),
            )

        # 升级 ⑨：资源聚合（命题背景「资源分散」）——按弱知识点收拢视频/课后题/错题本/单词本
        @tool(description=(
            "资源聚合：按弱知识点把分散资源（视频/课后题/错题本/单词本）收拢成一份补齐清单。"
            "入参为弱知识点列表。"
        ))
        def aggregate_resources_skill(weak_topics: list) -> dict:
            return skills.aggregate_resources(weak_topics)

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
        # 注册 8 个 Skill 工具（stateful，绑定到本 agent）
        agent.ability_manager.add_ability(diagnose_skill.card, diagnose_skill)
        agent.ability_manager.add_ability(replan_skill.card, replan_skill)
        agent.ability_manager.add_ability(allocate_skill.card, allocate_skill)
        agent.ability_manager.add_ability(interpret_feedback_skill.card, interpret_feedback_skill)
        agent.ability_manager.add_ability(proactive_scan_skill.card, proactive_scan_skill)
        agent.ability_manager.add_ability(reschedule_for_calendar_change_skill.card, reschedule_for_calendar_change_skill)
        agent.ability_manager.add_ability(decompose_goal_skill.card, decompose_goal_skill)
        agent.ability_manager.add_ability(aggregate_resources_skill.card, aggregate_resources_skill)
        return agent

    # ---------- openJiuwen 编排轨迹捕获：观察 ReAct 循环里的「思考 → 选工具 → 观察」 ----------
    async def _ensure_trace_hooks(self):
        """注册 openJiuwen 回调，把 ReAct 循环的真实编排轨迹记录到 self.trace。

        只用于演示/审计「openJiuwen 承担的作用」，不改变任何确定性结果。
        回调内部全部 try/except，绝不让轨迹捕获打断主流程。
        """
        if self._trace_hooks_registered:
            return

        def _on_after_model_call(ctx):
            try:
                inputs = getattr(ctx, "inputs", None)
                resp = getattr(inputs, "response", None)
                if resp is None:
                    return
                step = {"step": "thinking", "iteration": getattr(inputs, "react_iteration", 0)}
                reasoning = getattr(resp, "reasoning_content", None)
                if reasoning:
                    step["reasoning"] = reasoning
                tool_calls = getattr(resp, "tool_calls", None)
                if tool_calls:
                    step["plan"] = [
                        {"name": getattr(tc, "name", "?"), "arguments": getattr(tc, "arguments", "")}
                        for tc in tool_calls
                    ]
                content = getattr(resp, "content", None)
                if content and not tool_calls:
                    step["answer"] = content
                self.trace.append(step)
            except Exception:
                pass

        def _on_after_tool_call(ctx):
            try:
                inputs = getattr(ctx, "inputs", None)
                self.trace.append({
                    "step": "tool_call",
                    "iteration": getattr(inputs, "react_iteration", 0),
                    "tool": getattr(inputs, "tool_name", "?"),
                    "arguments": getattr(inputs, "tool_args", None),
                    "observation": getattr(inputs, "tool_result", None),
                })
            except Exception:
                pass

        await self.agent.register_callback(AgentCallbackEvent.AFTER_MODEL_CALL, _on_after_model_call, priority=200)
        await self.agent.register_callback(AgentCallbackEvent.AFTER_TOOL_CALL, _on_after_tool_call, priority=200)
        self._trace_hooks_registered = True

    # ---------- 确定性 Skill 执行：保证 demo 即使无 LLM 也能跑通 ----------
    def run_skills(self):
        """直接执行核心的两个 Skill（diagnose + replan），返回结构化结果（用于打印调整前后对比）。"""
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
            user_profile=self.memory.get("user_profile"),
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
            cause = f"主因：{a['primary_cause']}"
            if a.get("secondary_cause"):
                cause += f"；次因：{a['secondary_cause']}"
            if a.get("weak_topics"):
                cause += f"；弱知识点：{'、'.join(a['weak_topics'])}"
            lines.append(
                f"【{a['subject']}】严重度 {a['severity']}，连续失败 {a['consecutive_failures']} 天，"
                f"{cause}；情绪：{a.get('emotion', '')}。"
            )
        for adj in structured["adjustments"]:
            ev = adj.get("evidence", {})
            if isinstance(ev, dict):
                parts = [
                    f"连续失败 {ev.get('consecutive_failures')} 天",
                    f"完成率 {ev.get('completion_rate', 0):.0%}",
                ]
                if ev.get("primary_cause"):
                    parts.append(f"主因：{ev.get('primary_cause')}")
                if ev.get("secondary_cause"):
                    parts.append(f"次因：{ev.get('secondary_cause')}")
                if ev.get("weak_topics"):
                    parts.append(f"弱知识点：{'、'.join(ev.get('weak_topics'))}")
                if ev.get("emotion"):
                    parts.append(f"情绪：{ev.get('emotion')}")
                if ev.get("conclusion"):
                    parts.append(f"结论：{ev.get('conclusion')}")
                ev_str = "；".join(parts)
            else:
                ev_str = ev
            lines.append(f"调整 {adj['task_id']}：{adj['reason']} 依据：{ev_str}")
        return "\n".join(lines)

    async def decide(self, query):
        """决策入口：先确定性执行 Skill（保证数据正确），再调用 openJiuwen 生成决策日志。"""
        structured = self.run_skills()
        self.trace = []  # 清空上一次调用的编排轨迹

        if not self.env["ready"]:
            return {**structured, "decision_log": self._deterministic_log(structured), "used_llm": False, "trace": []}

        try:
            await self._ensure_trace_hooks()
            result = await self.agent.invoke({"query": query})
            output = result.get("output", "")
            if result.get("result_type") == "error" or not output:
                return {**structured, "decision_log": self._deterministic_log(structured), "used_llm": False, "trace": self.trace}
            return {**structured, "decision_log": output, "used_llm": True, "trace": self.trace}
        except Exception as exc:  # 网络 / API 异常时降级，保证 demo 能跑完
            return {
                **structured,
                "decision_log": self._deterministic_log(structured) + f"\n（LLM 调用失败：{exc}）",
                "used_llm": False,
                "trace": self.trace,
            }

    # ==================== 自由对话（前端聊天页 /api/chat）====================
    # 设计原则与 decide() 一致：规则层先把数据算对，LLM 只负责把结果讲成口语；
    # 任何 LLM 故障都降级为模板回复。plan 意图只出建议，不写文件（用户在前端点确认才应用）。

    def chat(self, message, history=None, scan_days=None, backlog=0):
        """自由对话入口。返回 {reply, intent, skill, payload, type, used_llm}。"""
        history = history or []
        intent, slots = self._classify_chat_intent(message)
        skill_name, structured = self._run_chat_skill(
            intent, message, slots, scan_days or [], int(backlog or 0))
        reply, used_llm = self._compose_chat_reply(message, history, intent, structured)

        if intent == "plan":
            msg_type = "plan_pending"
        elif structured is not None:
            msg_type = "card"
        else:
            msg_type = "text"
        return {
            "reply": reply,
            "intent": intent,
            "skill": skill_name,
            "payload": structured,
            "type": msg_type,
            "used_llm": used_llm,
        }

    # ---------- 1) 意图识别：LLM JSON 优先，关键词规则兜底 ----------

    _INTENT_KEYWORDS = [
        ("plan", ("重新规划", "重排", "调整计划", "改计划", "跟不上", "赶不上", "来不及",
                    "计划太多", "完不成计划", "重新安排")),
        ("load", ("未来", "接下来", "后面几天", "这几天", "忙不忙", "负荷", "负荷",
                    "时间够", "排得满", "超载")),
        ("goal", ("目标", "还差", "模块", "覆盖", "进度", "考什么", "要学哪些")),
        ("resource", ("学不懂", "学不会", "听不懂", "不会做", "好难", "卡住了", "卡壳",
                      "不懂", "资源", "看什么", "怎么补", "怎么学", "薄弱")),
        ("emotion", ("累", "困", "疲惫", "焦虑", "烦躁", "烦", "不想学", "坚持不下去",
                      "崩溃", "难过", "低落", "压力", "放弃", "emo", "emo了", "心情",
                      "没动力", "泄气")),
        ("status", ("状态", "怎么样", "诊断", "最近", "帮我看看", "学情")),
    ]
    _SUBJECT_KEYWORDS = {
        "数学": ("数学", "高数", "线代", "概率"),
        "数据结构": ("数据结构",),
        "计算机组成原理": ("计组", "组成原理", "计算机组成"),
        "英语": ("英语", "单词"),
        "操作系统": ("操作系统",),
        "计算机网络": ("计算机网络", "计网"),
    }

    def _classify_chat_intent(self, message):
        """返回 (intent, {"subject": ..., "topic": ...})。LLM 失败/无 key 走关键词。"""
        slots = {"subject": "", "topic": ""}
        for name, kws in self._SUBJECT_KEYWORDS.items():
            if any(k in message for k in kws):
                slots["subject"] = name
                break

        if self.env["ready"] and self._llm_fail_streak < 3:
            try:
                text = self._call_llm(CHAT_INTENT_PROMPT, message, timeout=30, max_tokens=1024)
                data = json.loads(text.strip().strip("`").removeprefix("json").strip())
                intent = data.get("intent", "chat")
                if intent not in {"status", "plan", "load", "goal", "resource", "emotion", "chat"}:
                    intent = "chat"
                slots["subject"] = data.get("subject") or slots["subject"]
                slots["topic"] = data.get("topic") or ""
                self._llm_fail_streak = 0
                return intent, slots
            except Exception:
                self._llm_fail_streak += 1

        # 关键词兜底
        for intent, kws in self._INTENT_KEYWORDS:
            if any(k in message for k in kws):
                return intent, slots
        return "chat", slots

    # ---------- 2) 规则层执行对应 Skill（确定性数据，不落盘）----------

    def _run_chat_skill(self, intent, message, slots, days, backlog):
        lm = self.memory.get("learning_memory", {})
        rules = self.memory.get("rules")
        up = self.memory.get("user_profile", {})
        plan = self.memory.get("current_plan", {})

        if intent == "status":
            return "diagnose", {"diagnosis": skills.diagnose(lm, rules)}

        if intent == "plan":
            diag = skills.diagnose(lm, rules)
            new_plan, adjustments = skills.replan(
                plan, diag, lm, len(self.memory.get("replanning_log", [])),
                rules=rules, user_profile=up, backlog=backlog,
            )
            # 只读建议：不落盘 new_plan.json、不追加 replanning_log
            return "replan", {
                "diagnosis": diag,
                "current_plan": new_plan,
                "adjustments": adjustments,
            }

        if intent == "load":
            if not days:
                return None, None
            daily_hours = float(up.get("daily_available_hours") or 6.0)
            return "proactive_scan", skills.proactive_scan(
                days, backlog=backlog, daily_available_hours=daily_hours, rules=rules)

        if intent == "goal":
            return "decompose_goal", skills.decompose_goal(
                up, plan, lm, rules=rules, schedule=self.memory.get("schedule"))

        if intent == "resource":
            weak = []
            if slots.get("topic"):
                weak.append(slots["topic"])
            for info in lm.get("subjects", {}).values():
                for w in info.get("weak_topics") or []:
                    if w not in weak:
                        weak.append(w)
            return "aggregate_resources", skills.aggregate_resources(weak)

        if intent == "emotion":
            return "interpret_feedback", skills.interpret_feedback(message)

        return None, None

    # ---------- 3) LLM 生成口语回复；失败走模板 ----------

    def _call_llm(self, system_prompt, user_content, timeout=45, max_tokens=2048, temperature=0.3):
        """通用 OpenAI 兼容调用（推理模型需要较大 max_tokens，否则思考链耗尽预算无正文）。"""
        if not self.env["ready"]:
            raise RuntimeError("未配置 API key")
        url = self.env["api_base"].rstrip("/") + "/chat/completions"
        resp = requests.post(
            url,
            headers={"Authorization": "Bearer " + self.env["api_key"], "Content-Type": "application/json"},
            json={
                "model": self.env["model_name"],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""

    def _compose_chat_reply(self, message, history, intent, structured):
        if not self.env["ready"] or self._llm_fail_streak >= 3:
            return self._template_chat_reply(intent, structured), False
        try:
            msgs = [{"role": "system", "content": CHAT_REPLY_PROMPT}]
            for h in history[-10:]:  # 只带最近 5 轮，控制 token
                if h.get("role") in ("user", "assistant") and h.get("content"):
                    msgs.append({"role": h["role"], "content": h["content"][:500]})
            brief = json.dumps(self._shrink_for_prompt(intent, structured), ensure_ascii=False)
            msgs.append({"role": "user", "content": f"【用户的话】{message}\n【结构化分析结果】{brief}"})
            url = self.env["api_base"].rstrip("/") + "/chat/completions"
            resp = requests.post(
                url,
                headers={"Authorization": "Bearer " + self.env["api_key"], "Content-Type": "application/json"},
                json={"model": self.env["model_name"], "messages": msgs,
                      "temperature": 0.5, "max_tokens": 2048},
                timeout=60,
            )
            resp.raise_for_status()
            text = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            if not text:
                raise RuntimeError("模型只返回了思考链，没有正文")
            self._llm_fail_streak = 0
            return text, True
        except Exception:
            self._llm_fail_streak += 1
            return self._template_chat_reply(intent, structured), False

    @staticmethod
    def _shrink_for_prompt(intent, structured):
        """给 LLM 的结构化结果做瘦身：只留生成回答必需的字段，省 token。"""
        if not structured:
            return None
        if intent == "status":
            d = structured["diagnosis"]
            return {
                "overall_status": d.get("overall_status"),
                "alert_subjects": [{
                    "subject": a["subject"], "severity": a["severity"],
                    "consecutive_failures": a["consecutive_failures"],
                    "recent_7d_completion_rate": a["recent_7d_completion_rate"],
                    "primary_cause": a.get("primary_cause"), "emotion": a.get("emotion"),
                } for a in d.get("alert_subjects", [])],
                "healthy_subjects": [h["subject"] for h in d.get("healthy_subjects", [])],
            }
        if intent == "plan":
            return {
                "overall_status": structured["diagnosis"].get("overall_status"),
                "adjustments": [{
                    "subject": a["subject"], "level": a["level"],
                    "before": a["before"], "after": a["after"], "reason": a["reason"],
                } for a in structured.get("adjustments", [])],
            }
        if intent == "load":
            return {k: structured.get(k) for k in
                   ("cap", "overload_count", "conclusion", "backlog_note")}
        if intent == "goal":
            return {k: structured.get(k) for k in
                    ("required_modules", "covered_modules", "missing_modules",
                     "coverage_rate", "public_courses", "recommendation")}
        if intent == "resource":
            return {"plan": [{"topic": p["topic"],
                              "resources": [r["type"] + "·" + r["name"] for r in p["resources"]]}
                             for p in structured.get("plan", [])]}
        if intent == "emotion":
            return {k: structured.get(k) for k in
                    ("primary_cause", "secondary_cause", "emotion", "suggestion", "weak_topics")}
        return None

    @staticmethod
    def _template_chat_reply(intent, structured):
        """无 key / LLM 失败时的模板回复（仍全部基于规则引擎算出的真实数据）。"""
        if intent == "status":
            d = structured["diagnosis"]
            alerts = d.get("alert_subjects", [])
            if not alerts:
                return "看了你的学习记忆，目前没有预警科目，节奏保持得不错，继续按计划走就行。"
            parts = [f"{a['subject']}（{a['severity']}，连续 {a['consecutive_failures']} 天未完成）"
                     for a in alerts]
            top = alerts[0]
            return (f"帮你看了下，{len(alerts)} 个科目预警：{'、'.join(parts)}。"
                    f"主要原因像是「{top.get('primary_cause') or '暂无明确归因'}」，"
                    f"可以先点「🔍 学习诊断」看完整分析，或者直接跟我说「帮我调整计划」。")
        if intent == "plan":
            adjs = structured.get("adjustments", [])
            if not adjs:
                return "我检查了一遍，目前没有需要调整的科目，继续执行现有计划就好。"
            lines = [f"· {a['subject']}：{a['before']} → {a['after']}" for a in adjs[:5]]
            return ("我按规则给你拟了调整建议（还没生效）：\n" + "\n".join(lines) +
                    "\n确认没问题的话，点下面卡片的「应用到计划」按钮，计划才会真正更新。")
        if intent == "load":
            if not structured:
                return "我还没拿到未来几天的计划数据，回到首页稍等一下再问我「未来几天忙不忙」就行。"
            n = structured.get("overload_count", 0)
            if n == 0:
                return f"未来 7 天没有超载日（每日上限约 {structured.get('cap')}h），节奏可控。"
            return (f"未来 7 天有 {n} 天超载（每日上限约 {structured.get('cap')}h）。"
                    f"{structured.get('conclusion', '')} 详细削峰方案见下方卡片。")
        if intent == "goal":
            miss = structured.get("missing_modules", [])
            if not miss:
                return "目标模块已全部覆盖，保持节奏，进入刷题强化阶段就行。"
            rate = structured.get("coverage_rate") or 0
            return (f"你的目标共需 {len(structured.get('required_modules', []))} 个模块，"
                    f"目前覆盖 {round(rate * 100)}%，还缺：{'、'.join(miss)}。"
                    f"建议尽快把这些模块排进计划，详细拆解见卡片。")
        if intent == "resource":
            plan = structured.get("plan", [])
            if not plan:
                return "目前没识别到明确的弱知识点。你可以先在错题本给错题打上知识点标签，我再帮你聚合资源。"
            p = plan[0]
            names = "、".join(r["name"] for r in p["resources"])
            return f"针对「{p['topic']}」，建议按这个顺序补：{names}。完整清单见卡片。"
        if intent == "emotion":
            cause = structured.get("primary_cause", "")
            sug = structured.get("suggestion", "")
            return f"抱抱，先别自责。我判断主要是「{cause}」。{sug} 今天哪怕只完成最小的一块，也是在往前走。"
        return "我在。可以跟我说你的学习状态、某个学不懂的知识点，或者直接说「帮我看看最近状态」。"


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
