# -*- coding: utf-8 -*-
"""
skills.py —— 8 个可解释 Skill（技能）：诊断 diagnose / 重规划 replan / 资源再分配 allocate /
反馈理解 interpret_feedback / 负荷扫描 proactive_scan / 课表重排 reschedule_for_calendar_change /
目标拆解 decompose_goal / 资源聚合 aggregate_resources。

设计原则：Skill 是「可解释的确定性规则引擎」，不依赖 LLM，输入/输出都是纯数据，
便于审计、复现与单元测试。openJiuwen 的 ReActAgent 在 agent.py 里把它们包装成
工具，在 ReAct 循环中按需调用并生成决策日志。

Skill 1：diagnose(learning_memory, rules=None) -> diagnosis
    输入：学习记忆（各科目掌握度 / 连续未完成天数 / 近 7 天完成率 / 弱知识点 / 反思原文）
    输出：诊断报告（整体状态 + 预警科目[严重度/原因/主因/反思引用] + 健康科目）
    调用条件：① 生成新计划前；② 某科目连续 3 天未完成触发重规划前。

Skill 2：replan(current_plan, diagnosis, learning_memory, replan_count=0, rules=None, user_profile=None)
    输入：当前计划 + 诊断结果 + 学习记忆 + 已重规划次数 + 用户画像（可选，用于个性化调度）
    输出：(新计划, 调整明细列表)，调整明细含 task_id / before / after / reason / evidence
    调用条件：诊断出预警科目后，对计划做针对性调整。
    个性化：user_profile.preferred_start_time 决定新任务起始时段；subject.focus_minutes 决定拆分粒度。

Skill 3：allocate(learning_memory, current_plan, daily_available_hours)
    输入：学习记忆 + 当前计划 + 每日可用时长
    输出：每科分配时长 + 可解释分配理由（weight = 优先级 × 阶段紧迫度 × 连续失败严重度）
    调用条件：总负荷超可用时长，或 ≥2 科预警。

Skill 4：interpret_feedback(text, llm_text=None)
    输入：用户反思 / 碎碎念原文（可选 LLM 返回 JSON）
    输出：结构化信号（主因 / 次因 / 弱知识点 / 情绪 / 提炼结论 / 来源）
    调用条件：用户提交反思原文时（优先 LLM 抽取，失败降级关键词匹配）。

Skill 5：proactive_scan(days, backlog=0, daily_available_hours)
    输入：未来 7 天计划负荷 + 积压错题数 + 每日可用时长
    输出：逐日负荷评估 + 削峰填谷方案 + 降级决策（负载上限 = 可用时长 × 1.2）
    调用条件：计划生成后，需要事前负荷预测时。

Skill 6：reschedule_for_calendar_change(old_schedule, new_schedule, current_plan, learning_memory)
    输入：旧课表 + 新课表 + 当前计划 + 学习记忆
    输出：受影响任务 + 重排后计划 + 说明
    调用条件：课表 version 变化时自动触发（version 相同则不重排）。

Skill 7：decompose_goal(user_profile, current_plan, learning_memory, rules=None)
    输入：用户画像（goal / target_date）+ 当前计划 + 学习记忆
    输出：需覆盖模块 / 已覆盖模块 / 缺失模块 / 阶段里程碑 / 当前阶段 / 可解释建议
    调用条件：生成计划前，检查「大目标」是否被完整拆解（命题背景「目标不清」）。

Skill 8：aggregate_resources(weak_topics, resource_catalog=None)
    输入：弱知识点列表（来自 diagnose 或 memory）
    输出：每个弱知识点对应的资源清单（视频/课后题/错题本/单词本）+ 可解释总结
    调用条件：诊断出弱知识点后，把分散资源收拢到补齐路径（命题背景「资源分散」）。
"""

import copy
import math
import re
from datetime import date, datetime, timedelta

# 默认规则（与 memory.json 里的 rules 保持一致；调用方也可传入覆盖）
DEFAULT_RULES = {
    "trigger_replan": {
        "consecutive_failures_threshold": 3,
        "completion_rate_threshold": 0.5,
        "lookback_days": 7,
    },
    "escalation": {"replan_count_threshold": 2},
    # 升级 ①：全局资源再分配权重（优先级）与最低保底时长
    "subject_weights": {"数学": 0.45, "数据结构": 0.30, "计算机组成原理": 0.10, "英语": 0.15},
    "min_floor": 0.5,
    # 升级 ③：拖延代价预警阈值（小时）
    "procrastination": {"hours_threshold": 2.0},
    # 策略参数（已从代码迁入 rules，可配置；与 memory.json 保持一致）
    "replan": {
        "downgrade_hours": 0.5,       # 降级时长
        "split_reduction": 0.5,       # 拆分减少量
        "fallback_hours": 0.5,        # 兜底时长
        "catchup_max_hours": 1.0,     # 补欠上限
    },
    "allocate": {
        "high_mastery": 0.8,          # 高掌握度阈值（稳定科目上调）
        "high_rate": 0.8,             # 高完成率阈值
        "low_mastery": 0.6,           # 低掌握度阈值（触发降级）
        "downgrade_delta": 0.3,       # 低掌握度降级减少量
    },
    "proactive_scan": {
        "overload_factor": 1.2,       # 超载系数（每日可用 × 该系数为负载上限）
    },
    # Skill 7：目标拆解（命题背景「目标不清」——把大目标拆成阶段里程碑）
    "goal": {
        "stages": [
            {"name": "基础阶段", "fraction": 0.4, "focus": "覆盖全部知识模块第一轮，打牢基础"},
            {"name": "强化阶段", "fraction": 0.4, "focus": "分模块刷题，攻克弱知识点"},
            {"name": "冲刺阶段", "fraction": 0.2, "focus": "真题 + 模拟 + 查漏补缺"},
        ],
    },
}

MASTERY_FORMULA = "完成率*0.4 + (1-错题率)*0.4 + 平均效率评分/5*0.2"

# 多维度归因维度（顺序无关，命中关键词越多排名越靠前）。LLM 模式下由 prompt 引导产出同样维度。
ATTRIBUTION_DIMENSIONS = [
    ("方法不当", ["方法", "不会", "卡", "懵", "写不出", "看不懂", "做不出来", "看得懂",
                  "没掌握", "不会写", "反应不过来", "搞不清", "不会做"]),
    ("情绪干扰", ["男朋友", "女朋友", "吵架", "分手", "冷战", "感情", "心情", "难过",
                  "不开心", "委屈", "失落", "孤独", "焦虑", "崩溃", "低落", "冷淡", "敷衍", "烦"]),
    ("精力不足", ["困", "累", "疲惫", "头昏", "头晕", "没精神", "熬夜", "睡眠",
                  "睡不好", "疲劳", "状态不好"]),
    ("目标不清晰", ["迷茫", "没方向", "学了干什么", "学完要干什么", "目的", "没计划",
                    "无从下手", "不知道学"]),
    ("任务过载", ["任务太多", "做不完", "太多", "超负荷", "量太大", "完不成", "赶不上", "任务多"]),
    ("时间投入不足", ["没时间", "来不及", "太忙", "没空", "时间不够", "没怎么学"]),
    ("基础薄弱", ["基础", "跟不上", "公式记不住", "概念不清", "不会做", "看不懂课本"]),
    ("环境干扰", ["宿舍", "室友", "手机", "分心", "打扰", "静不下心", "环境", "太吵", "很吵", "噪音"]),
]

# 情绪关键词（按顺序优先匹配）
EMOTION_KEYWORDS = [
    ("低落", ["难过", "不开心", "失落", "委屈", "心情不好", "低落", "孤独", "冷淡", "敷衍", "烦", "难受"]),
    ("焦虑", ["焦虑", "崩溃", "担心", "害怕", "压力", "紧张", "慌"]),
    ("疲惫", ["困", "累", "疲惫", "没精神", "头昏", "头晕", "熬夜", "睡不好", "疲劳"]),
    ("积极", ["开心", "成就感", "收获", "顺利", "状态不错", "有进步"]),
]

# 弱知识点关键词（反思里提到的具体知识点）
WEAK_TOPIC_KEYWORDS = {
    "链表": "链表", "指针": "指针", "二叉树": "二叉树", "树": "树", "图": "图",
    "存储": "存储器", "Cache": "Cache", "cache": "Cache",
    "极限": "极限", "导数": "导数", "积分": "积分", "中值定理": "中值定理", "泰勒": "泰勒",
    "单词": "单词", "长难句": "长难句",
}

# Skill 7（目标拆解）：目标关键词 → 需覆盖的知识模块（命题背景「目标不清」）
GOAL_REQUIRED_MODULES = {
    "数学一": ["高等数学", "线性代数", "概率论与数理统计"],
    "数学二": ["高等数学", "线性代数"],
    "408": ["数据结构", "计算机组成原理", "操作系统", "计算机网络"],
}

# 当前系统「科目」→ 对应知识模块（用于覆盖检查；「数学」科目当前只规划了高数）
SUBJECT_TO_MODULE = {
    "数学": "高等数学",
    "数据结构": "数据结构",
    "计算机组成原理": "计算机组成原理",
    "操作系统": "操作系统",
    "计算机网络": "计算机网络",
    "英语": "英语",
}

# Skill 8（资源聚合）：弱知识点 → 该用的资源（视频/课后题/错题本/单词本），把分散资料收拢
RESOURCE_CATALOG = {
    "链表": [("视频", "王道数据结构 · 链表章节"), ("课后题", "王道课后题 · 链表专题"), ("错题本", "错题本『链表』标签")],
    "指针": [("视频", "C 语言指针专题"), ("课后题", "指针专项练习"), ("错题本", "错题本『指针』标签")],
    "二叉树": [("视频", "王道数据结构 · 树与二叉树"), ("课后题", "王道课后题 · 树专题"), ("错题本", "错题本『二叉树』标签")],
    "树": [("视频", "王道数据结构 · 树与二叉树"), ("课后题", "王道课后题 · 树专题"), ("错题本", "错题本『树』标签")],
    "图": [("视频", "王道数据结构 · 图"), ("课后题", "王道课后题 · 图专题"), ("错题本", "错题本『图』标签")],
    "存储器": [("视频", "王道计组 · 存储系统"), ("课后题", "王道课后题 · 存储专题"), ("错题本", "错题本『存储』标签")],
    "Cache": [("视频", "王道计组 · Cache 章节"), ("课后题", "王道课后题 · Cache 专题"), ("错题本", "错题本『Cache』标签")],
    "极限": [("视频", "高数 · 极限与连续"), ("课后题", "高数课后题 · 极限专题"), ("错题本", "错题本『极限』标签")],
    "导数": [("视频", "高数 · 导数与微分"), ("课后题", "高数课后题 · 导数专题"), ("错题本", "错题本『导数』标签")],
    "积分": [("视频", "高数 · 不定/定积分"), ("课后题", "高数课后题 · 积分专题"), ("错题本", "错题本『积分』标签")],
    "中值定理": [("视频", "高数 · 中值定理"), ("课后题", "高数课后题 · 中值定理专题"), ("错题本", "错题本『中值定理』标签")],
    "泰勒": [("视频", "高数 · 泰勒公式"), ("课后题", "高数课后题 · 泰勒专题"), ("错题本", "错题本『泰勒』标签")],
    "单词": [("单词本", "艾宾浩斯单词本 · 复习队列"), ("错题本", "错题本『单词』标签")],
    "长难句": [("视频", "英语 · 长难句精讲"), ("课后题", "长难句每日一句"), ("错题本", "错题本『长难句』标签")],
}

# 未命中目录时的兜底资源（仍指向系统自己的三类资源）
DEFAULT_RESOURCES = [("视频", "对应科目章节视频"), ("课后题", "对应科目课后题"), ("错题本", "错题本对应标签")]


def compute_mastery(raw_data):
    """按公式计算掌握度：完成率*0.4 + (1-错题率)*0.4 + 平均效率/5*0.2。"""
    cr = raw_data.get("completion_rate", 0)
    er = raw_data.get("error_rate", 0)
    ef = raw_data.get("avg_efficiency", 0)
    return round(cr * 0.4 + (1 - er) * 0.4 + ef / 5 * 0.2, 4)


def _severity(failures, completion_rate, failures_threshold, completion_rate_threshold):
    """把连续失败天数与完成率映射为预警严重度。"""
    if failures >= 5:
        return "high"
    if failures >= failures_threshold:  # 默认 3
        return "high"
    if failures >= 2 or completion_rate < completion_rate_threshold:  # 默认 0.5
        return "medium"
    return "low"


def _attribute_reflections(texts):
    """把多条反思原文做确定性多维度归因（LLM 不可用时的兜底）。

    返回 {primary_cause, secondary_cause, weak_topics, emotion, evidence_summary}。
    不再只给「时间投入不足」单一口径：按 ATTRIBUTION_DIMENSIONS 命中关键词，
    命中最多者为主因、次多者为次因；情绪单独按 EMOTION_KEYWORDS 匹配。
    """
    joined = " ".join(texts or [])
    hits = []
    for name, kws in ATTRIBUTION_DIMENSIONS:
        cnt = sum(1 for k in kws if k in joined)
        if cnt > 0:
            hits.append((cnt, name))
    hits.sort(key=lambda x: -x[0])
    primary = hits[0][1] if hits else "状态波动（未识别单一主因）"
    secondary = hits[1][1] if len(hits) > 1 else ""

    emotion = "平静"
    for name, kws in EMOTION_KEYWORDS:
        if any(k in joined for k in kws):
            emotion = name
            break

    weak = []
    for kw, topic in WEAK_TOPIC_KEYWORDS.items():
        if kw in joined and topic not in weak:
            weak.append(topic)

    parts = [f"主因：{primary}"]
    if secondary:
        parts.append(f"次因：{secondary}")
    if weak:
        parts.append(f"弱知识点：{'、'.join(weak)}")
    parts.append(f"情绪：{emotion}")
    return {
        "primary_cause": primary,
        "secondary_cause": secondary,
        "weak_topics": weak,
        "emotion": emotion,
        "evidence_summary": "；".join(parts),
    }


def diagnose(learning_memory, rules=None):
    """Skill 1：学习诊断。"""
    rules = rules or DEFAULT_RULES
    trig = rules["trigger_replan"]
    subjects = learning_memory.get("subjects", {})

    alert_subjects = []
    healthy_subjects = []
    proc_threshold = rules.get("procrastination", {}).get("hours_threshold", 2.0)

    for name, info in subjects.items():
        failures = info.get("consecutive_failures", 0)
        rate = info.get("recent_7d_completion_rate", 0)
        sev = _severity(
            failures,
            rate,
            trig["consecutive_failures_threshold"],
            trig["completion_rate_threshold"],
        )
        score = info.get("mastery_score", {}).get("value", 0)
        proc = info.get("procrastination_cost", {})
        proc_hours = proc.get("hours", 0)

        # 升级 ③：拖延代价作为第 3 个预警信号，可把 low 抬升为 medium
        if sev == "low" and proc_hours >= proc_threshold:
            sev = "medium"

        if sev in ("high", "medium"):
            quotes = [r.get("text", "") for r in info.get("recent_reflections", [])]
            attr = _attribute_reflections(quotes)
            # 弱知识点合并：记忆里记录的 + 反思里提到的，去重
            weak_topics = list(info.get("weak_topics") or [])
            for w in attr.get("weak_topics", []):
                if w not in weak_topics:
                    weak_topics.append(w)
            reasons = []
            if failures >= trig["consecutive_failures_threshold"]:
                reasons.append(
                    f"连续 {failures} 天未完成，达到触发阈值 {trig['consecutive_failures_threshold']} 天"
                )
            if rate < trig["completion_rate_threshold"]:
                reasons.append(
                    f"近 {trig['lookback_days']} 天完成率 {rate:.0%}，低于阈值 {trig['completion_rate_threshold']:.0%}"
                )
            if proc_hours >= proc_threshold:
                reasons.append(
                    f"本周拖延代价 {proc_hours}h（{proc.get('count', 0)} 次），超过阈值 {proc_threshold}h，需安排补欠"
                )
            if weak_topics:
                reasons.append(f"弱知识点：{'、'.join(weak_topics)}")

            alert_subjects.append({
                "subject": name,
                "severity": sev,
                "consecutive_failures": failures,
                "recent_7d_completion_rate": rate,
                "mastery_score": score,
                "procrastination_cost": proc_hours,
                "reasons": reasons,
                "primary_cause": attr["primary_cause"],
                "secondary_cause": attr["secondary_cause"],
                "weak_topics": weak_topics,
                "emotion": attr["emotion"],
                "evidence_summary": attr["evidence_summary"],
                "evidence_source": f"近 {len(quotes)} 条反思 + 连续 {failures} 天失败",
            })
        else:
            healthy_subjects.append({
                "subject": name,
                "mastery_score": score,
                "consecutive_failures": failures,
            })

    overall = "warning" if alert_subjects else "healthy"
    evidence = []
    for a in alert_subjects:
        evidence.append(
            f"{a['subject']}：连续失败 {a['consecutive_failures']} 天，"
            f"近 {trig['lookback_days']} 天完成率 {a['recent_7d_completion_rate']:.0%}"
        )
    for a in alert_subjects:
        # 依据只引用提炼后的结论，不搬运反思原文（原文留在 memory.json 的 recent_reflections 供审计）
        line = (
            f"{a['subject']} 归因：主因 {a['primary_cause']}"
            + (f"；次因 {a['secondary_cause']}" if a.get("secondary_cause") else "")
            + (f"；弱知识点 {'、'.join(a['weak_topics'])}" if a.get("weak_topics") else "")
            + f"；情绪 {a['emotion']}"
        )
        evidence.append(line)
    if not alert_subjects:
        evidence.append("各科连续失败天数与完成率均在阈值内，未触发预警")
    return {
        "overall_status": overall,
        "alert_subjects": alert_subjects,
        "healthy_subjects": healthy_subjects,
        "trigger_condition": (
            f"任一科目连续未完成天数 >= {trig['consecutive_failures_threshold']}，"
            f"或近 {trig['lookback_days']} 天完成率 < {trig['completion_rate_threshold']}"
        ),
        "evidence": evidence,
    }


def _find_task(plan, subject):
    """在计划中定位某科目的任务（取第一条）。"""
    for t in plan.get("tasks", []):
        if t.get("subject") == subject:
            return t
    return None


def _has_catchup(plan, subject):
    """该科目是否已存在独立补欠任务（flag == 'catch_up'），避免重复补欠。"""
    return any(
        t.get("subject") == subject and t.get("flag") == "catch_up"
        for t in plan.get("tasks", [])
    )


def _describe_task(task):
    """任务的「调整前/后」可读描述。"""
    return f"{task['subject']}：{task['content']}，共 {task['planned_hours']}h"


def _hhmm_to_min(t):
    """把 "HH:MM" 转成分钟数（自包含，供 calibrate_slots 使用，避免依赖下方定义）。"""
    h, m = t.strip().split(":")
    return int(h) * 60 + int(m)


def _min_to_hhmm(m):
    """把分钟数转成 "HH:MM"。"""
    return f"{m // 60:02d}:{m % 60:02d}"


def _slot_from_start(start_time, hours):
    """从「开始时刻 + 时长」构造 "HH:MM-HH:MM" 时段；开始时刻非法时返回空串。

    用于把用户画像里的 preferred_start_time 落到新任务的 scheduled_slots 上。
    """
    try:
        start_min = _hhmm_to_min(str(start_time).strip())
    except (ValueError, AttributeError):
        return ""
    end_min = start_min + max(int(round(hours * 60)), 10)
    return f"{_min_to_hhmm(start_min)}-{_min_to_hhmm(end_min)}"


def calibrate_slots(task):
    """时长（planned_hours）变化时，自动重算 scheduled_slots，保证「时长与时段一致」。

    保持原开始时间不变，结束时间 = 开始时间 + planned_hours。
    例：1.5h @20:30-22:00 缩短为 1.0h → @20:30-21:30。
    无时段、时段非法或 planned_hours 缺失时原样返回，避免误改。
    时段分隔符同时兼容 ASCII 连字符（-）、短横线（–）与破折号（—）。
    """
    slots = task.get("scheduled_slots") or []
    if not slots:
        return task
    m = re.match(r"(\d{1,2}:\d{2})\s*[-–—]\s*\d{1,2}:\d{2}", slots[0])
    if not m:
        return task
    try:
        start_min = _hhmm_to_min(m.group(1))
        hours = float(task.get("planned_hours"))
    except (ValueError, TypeError):
        return task
    end_min = start_min + int(round(hours * 60))
    task["scheduled_slots"] = [f"{m.group(1)}-{_min_to_hhmm(end_min)}"]
    return task


def _resolve_slot_conflicts(new_plan):
    """调整后同一天内任务时段互相冲突时，做小范围顺延。

    按开始时间排序，发现与上一任务重叠时，把后一个任务顺延到上一任务结束之后，
    时长不变（至少保留 10 分钟）。时段非法（无法解析起止）的任务跳过，避免误改。
    """
    ordered = []
    for t in new_plan.get("tasks", []) or []:
        slots = t.get("scheduled_slots") or []
        if not slots:
            continue
        m = re.match(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", slots[0])
        if not m:
            continue
        try:
            s = _hhmm_to_min(m.group(1))
            e = _hhmm_to_min(m.group(2))
        except (ValueError, TypeError):
            continue
        ordered.append({"task": t, "start": s, "end": e})

    ordered.sort(key=lambda x: x["start"])
    prev_end = None
    for item in ordered:
        s, e = item["start"], item["end"]
        task = item["task"]
        if prev_end is not None and s < prev_end:
            dur = max(e - s, 10)  # 顺延时保持原时长，至少 10 分钟
            new_s = prev_end
            task["scheduled_slots"] = [f"{_min_to_hhmm(new_s)}-{_min_to_hhmm(new_s + dur)}"]
            e = new_s + dur
        prev_end = e
    return new_plan


def _split_content(content, subject, hours, focus_minutes=0):
    """按科目把任务拆成「具体子步骤」，返回新任务名（不拼接旧名、不套通用模板）。

    - 数学        → 看讲解视频 + 做基础题
    - 数据结构/计组 → 看王道视频 + 做课后题
    - 英语        → 背单词 + 长难句（按原任务子项拆，背单词:长难句 ≈ 2:1）
    - 其他        → 保留原描述（不做通用拆分）

    focus_minutes > 0 时，首个子步骤不超过一个「专注时长」（贴合用户实际专注耐力），
    让拆出来的第一块刚好是一次能坚持的专注单元，进一步降低启动门槛。
    """
    total_min = max(int(round(hours * 60)), 1)  # 与 planned_hours 一致，杜绝拆出 0min 子项
    focus = int(focus_minutes or 0)
    if subject == "英语":
        first = int(round(total_min * 2 / 3))
        label1, label2 = "背单词", "长难句"
    elif subject in ("数据结构", "计算机组成原理"):
        first = total_min // 2
        label1, label2 = "看王道视频", "做课后题"
    elif subject == "数学":
        first = total_min // 2
        label1, label2 = "看讲解视频", "做基础题"
    else:
        return content
    if focus > 0:
        first = min(first, focus)
    return f"{label1} {first}min + {label2} {total_min - first}min"


def _downgrade_content(content):
    """降低内容难度。"""
    return f"{content} → 降级为『知识点概念梳理 + 错题回顾』"


def _evidence(alert, conclusion=None, replan_count=None):
    """结构化「调整依据」：连续失败天数 / 近7天完成率 / 多维度归因（主因/次因/弱知识点/情绪/依据来源）。

    不再搬运反思原文全文；原文仅留在 memory.json 的 recent_reflections 里供审计。
    """
    ev = {
        "consecutive_failures": alert["consecutive_failures"],
        "completion_rate": alert["recent_7d_completion_rate"],
        "primary_cause": alert.get("primary_cause", ""),
        "secondary_cause": alert.get("secondary_cause", ""),
        "weak_topics": alert.get("weak_topics", []),
        "emotion": alert.get("emotion", ""),
        "evidence_source": alert.get("evidence_source", ""),
        "conclusion": conclusion or alert.get("primary_cause", ""),
    }
    if replan_count is not None:
        ev["replan_count"] = replan_count
    return ev


def replan(current_plan, diagnosis, learning_memory, replan_count=0, rules=None, user_profile=None, backlog=0):
    """Skill 2：动态重规划。返回 (new_plan, adjustments)。

    user_profile 为可选个性化画像：preferred_start_time 决定新任务（补欠）的起始时段，
    learning_memory 里每科的 focus_minutes 决定拆分粒度。缺省时行为与旧版一致。

    backlog 为错题本未掌握题目数（>0 时把「错题回顾」排进计划，形成错题本 → 计划闭环）。
    """
    rules = rules or DEFAULT_RULES
    replan_cfg = rules.get("replan", DEFAULT_RULES["replan"])
    profile = user_profile or {}
    preferred_start = profile.get("preferred_start_time") or "19:00"
    new_plan = copy.deepcopy(current_plan)
    adjustments = []

    for alert in diagnosis.get("alert_subjects", []):
        subject = alert["subject"]
        task = _find_task(new_plan, subject)
        if task is None:
            continue

        before = _describe_task(task)
        sev = alert["severity"]
        fails = alert["consecutive_failures"]

        # 兜底：已连续重规划多次仍未改善 → 换任务类型 + 给出三个可选方向（推荐 A）
        if replan_count >= 2:
            task["content"] = "暂停原任务：改为『知识点概念梳理 + 错题回顾』"
            task["planned_hours"] = replan_cfg.get("fallback_hours", 0.5)
            task["flag"] = "escalated"
            calibrate_slots(task)  # 时长变化后校准时段
            adjustments.append({
                "task_id": task["task_id"],
                "subject": subject,
                "level": "escalation",
                "before": before,
                "after": _describe_task(task),
                "reason": (
                    f"已连续重规划 {replan_count} 次仍未改善，触发兜底：先换任务类型"
                    "（从『刷题推进』转为『概念梳理 + 错题回顾』），再给出三个方向供用户选择——"
                    "A 换路径：补上指针/链表基础再回到原计划（推荐）；"
                    "B 降目标：降低近期目标难度，把进度顺延到寒假；"
                    "C 审视目标：结合时间投入重新审视考研目标本身。"
                    "推荐 A：用最小代价补齐短板，不牺牲整体进度。"
                ),
                "evidence": _evidence(
                    alert,
                    conclusion=f"连续重规划 {replan_count} 次仍未改善，触发兜底（推荐方向 A）",
                    replan_count=replan_count,
                ),
            })
        # 严重度高且连续失败 ≥5 天 → 降级内容难度
        elif sev == "high" and fails >= 5:
            task["content"] = _downgrade_content(task["content"])
            task["planned_hours"] = replan_cfg.get("downgrade_hours", 0.5)
            task["flag"] = "downgraded"
            calibrate_slots(task)  # 时长变化后校准时段
            adjustments.append({
                "task_id": task["task_id"],
                "subject": subject,
                "level": "downgrade",
                "before": before,
                "after": _describe_task(task),
                "reason": (
                    "连续失败 ≥5 天且严重度 high，判定当前难度超过现有能力，"
                    "降低内容难度并缩短时长。"
                ),
                "evidence": _evidence(alert),
            })
        # 严重度高且连续失败恰好 3 天 → 减少时长 + 拆分任务
        elif sev == "high" and fails == 3:
            original_hours = float(task.get("planned_hours") or 0)
            reduced = round(original_hours - replan_cfg.get("split_reduction", 0.5), 2)
            half = round(original_hours * 0.5, 2)
            # 拆分后总时长不得为 0：缩减后若 ≤ 原时长×0.5，强制取原时长×0.5
            new_hours = max(reduced, half)
            if new_hours <= 0:
                new_hours = rules.get("min_floor", 0.5)  # 兜底，绝不 0h
            task["planned_hours"] = new_hours
            focus = int((learning_memory.get("subjects", {}).get(subject, {}) or {}).get("focus_minutes") or 0)
            task["content"] = _split_content(task["content"], subject, new_hours, focus_minutes=focus)
            task["flag"] = "split"
            calibrate_slots(task)  # 时长变化后按新 planned_hours 重算时段
            adjustments.append({
                "task_id": task["task_id"],
                "subject": subject,
                "level": "split",
                "before": before,
                "after": _describe_task(task),
                "reason": (
                    "连续失败 3 天达到阈值，采取『减少时长 + 拆分任务』："
                    "把大块任务拆成可完成的子步骤，降低启动门槛。"
                ),
                "evidence": _evidence(alert),
            })
        # 中等预警 → 仅标红提醒，不改变计划内容
        elif sev == "medium":
            task["flag"] = "red"
            adjustments.append({
                "task_id": task["task_id"],
                "subject": subject,
                "level": "remind",
                "before": before,
                "after": before,
                "reason": "中等预警：仅标红提醒，不改变计划内容。",
                "evidence": _evidence(alert),
            })
        # 严重度 low → 不干预

    # 升级 ③：拖延代价——为拖延最重的科目安排「补欠时段」（独立于预警科目）
    proc_threshold = rules.get("procrastination", {}).get("hours_threshold", 2.0)
    costed = sorted(
        [
            (name, info.get("procrastination_cost", {}).get("hours", 0),
             info.get("procrastination_cost", {}).get("count", 0))
            for name, info in learning_memory.get("subjects", {}).items()
        ],
        key=lambda x: -x[1],
    )
    if costed and costed[0][1] >= proc_threshold:
        name, hours, count = costed[0]
        task = _find_task(new_plan, name)
        # 补欠作为「独立任务」追加，而不是把主任务继续加码：主任务 1.0h + 补欠 1.0h = 2.0h，
        # 各自再拆成 ≤30min 的子步骤，降低启动门槛、避免一天任务越滚越大。
        if task is not None and not _has_catchup(new_plan, name):
            info = learning_memory["subjects"][name]
            catch_up = min(round(hours, 1), replan_cfg.get("catchup_max_hours", 1.0))
            main_before = _describe_task(task)
            catchup_task = {
                "task_id": f"{task['task_id']}_catchup",
                "subject": name,
                "content": f"补欠专项：①错题回顾 30min ②指针基础专项 30min（共 {catch_up}h）",
                "planned_hours": catch_up,
                # 个性化：把最拖延科目的补欠安排在用户偏好开始时段，第一时间啃硬骨头
                "scheduled_slots": [slot] if (slot := _slot_from_start(preferred_start, catch_up)) else [],
                "status": "undone",
                "priority": "medium",
                "flag": "catch_up",
                "depends_on": [task["task_id"]],
            }
            new_plan.setdefault("tasks", []).append(catchup_task)
            adjustments.append({
                "task_id": catchup_task["task_id"],
                "subject": name,
                "level": "catch_up",
                "before": "（无独立补欠任务）",
                "after": _describe_task(catchup_task),
                "reason": (
                    f"本周拖延代价 {hours}h（{count} 次），超过阈值 {proc_threshold}h，"
                    f"为 {name} 新增 {catch_up}h 补欠任务，与主任务合计 {round(task['planned_hours'] + catch_up, 1)}h，"
                    "但拆成 4 个 ≤30min 子步骤，降低启动门槛。"
                ),
                "evidence": _evidence(
                    {
                        "consecutive_failures": info.get("consecutive_failures", 0),
                        "recent_7d_completion_rate": info.get("recent_7d_completion_rate", 0),
                        **_attribute_reflections(
                            [r.get("text", "") for r in info.get("recent_reflections", [])]
                        ),
                        "evidence_source": (
                            f"近 {len(info.get('recent_reflections', []))} 条反思"
                            f" + 拖延代价 {hours}h"
                        ),
                    },
                    conclusion=f"拖延代价 {hours}h，安排 {catch_up}h 独立补欠任务",
                ),
            })

    # 升级 ⑪：错题本联动——未掌握错题驱动「错题回顾」任务，
    # 把 proactive_scan 的 backlog_note（「建议纳入补欠池」）真正落到计划里。
    if backlog > 0 and not any(t.get("flag") == "error_review" for t in new_plan.get("tasks", [])):
        review_hours = round(min(backlog * 0.25, replan_cfg.get("catchup_max_hours", 1.0)), 1)
        review_hours = max(review_hours, 0.3)  # 至少排 0.3h，避免「0 题 0h」的空任务
        review_task = {
            "task_id": "error_review_1",
            "subject": "综合",
            "content": f"错题回顾：重做 {backlog} 道未掌握错题（共 {review_hours}h）",
            "planned_hours": review_hours,
            "scheduled_slots": [slot] if (slot := _slot_from_start(preferred_start, review_hours)) else [],
            "status": "undone",
            "priority": "high",
            "flag": "error_review",
            "depends_on": [],
        }
        new_plan.setdefault("tasks", []).append(review_task)
        adjustments.append({
            "task_id": review_task["task_id"],
            "subject": "综合",
            "level": "error_review",
            "before": "（无错题回顾任务）",
            "after": _describe_task(review_task),
            "reason": (
                f"错题本有 {backlog} 道未掌握错题，此前仅提示「建议纳入补欠池」而未实际排进计划。"
                f"本次把错题回顾真正排进计划：重做 {backlog} 题，共 {review_hours}h，"
                "安排在偏好开始时段第一时间啃硬骨头，形成「错题本 → 计划」闭环。"
            ),
            "evidence": {
                "evidence_source": f"错题本未掌握 {backlog} 题",
            },
        })

    # 调整可能导致同一天任务时段重叠，统一做一次小范围顺延（顺延到上一任务之后）
    new_plan = _resolve_slot_conflicts(new_plan)
    return new_plan, adjustments


# ============================================================================
# 升级 ①：allocate —— 全局资源再分配（多科目冲突）
# ============================================================================

def _days_left(deadline, today=None):
    """距 deadline 剩余天数；deadline 为空或格式非法时返回 None（紧迫度取默认 1.0）。"""
    if not deadline:
        return None
    try:
        d = datetime.strptime(deadline, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    try:
        t = datetime.strptime(today, "%Y-%m-%d").date() if today else date.today()
    except (TypeError, ValueError):
        t = date.today()
    return (d - t).days


def _urgency_factor(days_left):
    """阶段紧迫度：距 deadline 越近越紧迫（权重越大）。"""
    if days_left is None:
        return 1.0
    if days_left <= 0:
        return 1.5
    if days_left <= 30:
        return 1.4
    if days_left <= 90:
        return 1.2
    if days_left <= 180:
        return 1.0
    return 0.8


def _severity_factor(failures):
    """连续失败严重度：失败越多，越需要投入时间补救。"""
    return 1.0 + min(failures, 5) * 0.15


def _ceil_half(x):
    """向上取整到 0.5h 粒度。"""
    return math.ceil(x * 2) / 2


def _should_allocate(learning_memory, current_plan, daily_available_hours, rules=None):
    """判断是否触发全局再分配：Σ planned_hours > available 或 ≥2 科 alert。"""
    rules = rules or DEFAULT_RULES
    planned = sum(float(t.get("planned_hours", 0)) for t in current_plan.get("tasks", []))
    diag = diagnose(learning_memory, rules)
    n_alert = len(diag["alert_subjects"])
    reasons = []
    if planned > daily_available_hours:
        reasons.append(f"总计划时长 {planned:.1f}h > 每日可用 {daily_available_hours}h")
    if n_alert >= 2:
        reasons.append(f"{n_alert} 科同时预警")
    return (bool(reasons), "；".join(reasons) or "无需再分配")


def _apply_allocations(current_plan, allocations, rules):
    """把 allocate 的每科分配时长写回计划：更新 planned_hours 并重算 scheduled_slots。

    只改主任务（跳过 catch_up 等附加任务），保证「调整后任务的具体时间与内容」一致，
    供前端写回 new_plan 并同步到今日/任务清单。
    """
    new_plan = copy.deepcopy(current_plan)
    hours_by_subject = {a["subject"]: a["allocated_hours"] for a in allocations}
    for t in new_plan.get("tasks", []):
        if t.get("flag") == "catch_up":
            continue
        subj = t.get("subject")
        if subj in hours_by_subject:
            t["planned_hours"] = round(hours_by_subject[subj], 2)
            calibrate_slots(t)
    return new_plan


def allocate(learning_memory, current_plan, daily_available_hours, rules=None, today=None):
    """Skill 3：全局资源再分配（多科目冲突）。

    输入：学习记忆 + 当前计划 + 每日可用时长（+ 规则 + 今天日期）
    输出：每科分配时长 + 可解释分配理由。
    公式：每科分配 = max(weight × total_hours, min_floor)
          weight = 优先级(subject_weights) × 阶段紧迫度(deadline) × 连续失败严重度
    触发：Σ planned_hours > available 或 ≥2 科 alert。
    """
    rules = rules or DEFAULT_RULES
    weights_cfg = rules.get("subject_weights", DEFAULT_RULES["subject_weights"])
    min_floor = rules.get("min_floor", 0.5)
    alloc_cfg = rules.get("allocate", DEFAULT_RULES["allocate"])
    high_mastery = alloc_cfg.get("high_mastery", 0.8)
    high_rate = alloc_cfg.get("high_rate", 0.8)
    low_mastery = alloc_cfg.get("low_mastery", 0.6)
    downgrade_delta = alloc_cfg.get("downgrade_delta", 0.3)
    subjects = learning_memory.get("subjects", {})
    total = float(daily_available_hours or 6.0)
    if today is None:
        today = (current_plan.get("date") or current_plan.get("generated_at")
                 or date.today().isoformat())

    # 1) 原始权重 = 优先级 × 紧迫度 × 严重度
    raw = {}
    for name, info in subjects.items():
        base = weights_cfg.get(name, 0.1)
        urgency = _urgency_factor(_days_left(info.get("deadline"), today))
        severity = _severity_factor(info.get("consecutive_failures", 0))
        raw[name] = base * urgency * severity

    # 2) 归一化（保证 Σ weight = 1，总时长守恒）
    s = sum(raw.values()) or 1.0
    weights = {k: v / s for k, v in raw.items()}

    # 3) 基础分配 + 保底 + 可解释调整
    allocations = []
    for name, info in subjects.items():
        w = weights[name]
        base_h = round(w * total, 2)
        allocated = base_h
        reason = f"权重{w:.2f} × {total:.0f}h = {base_h:.2f}h"
        score = info.get("mastery_score", {}).get("value", 0)
        rate = info.get("recent_7d_completion_rate", 0)
        priority = weights_cfg.get(name, 0.1)

        if base_h < min_floor:
            allocated = min_floor
            reason += f"，低于保底 {min_floor}h，上调至 {allocated}h"
        elif score < low_mastery and priority >= 0.4:
            allocated = _ceil_half(base_h)
            reason += f"，因掌握度 {score} 低，上调至 {allocated}h"
        elif score < low_mastery:
            allocated = max(min_floor, round(base_h - downgrade_delta, 1))
            reason += f"，但掌握度 {score} 触发降级，降至 {allocated}h"
        elif score >= high_mastery and rate >= high_rate:
            allocated = max(allocated, _ceil_half(base_h))
            reason += (
                f"，因掌握度 {score}（高）+ 完成率 {rate:.0%}（良好），上调至 {allocated}h"
                "（稳定科目，维持投入）"
            )

        allocations.append({
            "subject": name,
            "weight": round(w, 4),
            "base_hours": base_h,
            "allocated_hours": round(allocated, 2),
            "min_floor": min_floor,
            "reason": reason,
        })

    triggered, trigger_reason = _should_allocate(learning_memory, current_plan, total, rules)
    formula = "每科分配 = max(weight × total_hours, min_floor)；weight = 优先级 × 紧迫度 × 严重度"
    mastery_summary = "；".join(
        f"{name} 掌握度 {info.get('mastery_score', {}).get('value', 0):.2f}"
        for name, info in subjects.items()
    )
    evidence = [
        f"权重公式：{formula}",
        f"各科掌握度：{mastery_summary}",
        f"可用时长：每日 {total:g}h",
    ]
    new_plan = _apply_allocations(current_plan, allocations, rules)
    return {
        "triggered": triggered,
        "trigger_reason": trigger_reason,
        "total_hours": total,
        "min_floor": min_floor,
        "formula": formula,
        "allocations": allocations,
        "new_plan": new_plan,
        "evidence": evidence,
    }


# ============================================================================
# 升级 ②：interpret_feedback —— 用户自然语言反馈理解
# ============================================================================

def _keyword_feedback(text):
    """确定性关键词抽取（LLM 降级时的兜底）—— 与 _attribute_reflections 同一套维度。"""
    return _attribute_reflections([text or ""])


def _parse_llm_feedback(llm_text):
    """解析 LLM 返回的反馈 JSON（容忍 markdown 代码块围栏）。

    新格式：{primary_cause, secondary_cause, weak_topics, emotion, evidence_summary}
    兼容旧字段：attribution→primary_cause，mood→emotion。
    """
    import json as _json
    txt = (llm_text or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:].lstrip()
    try:
        obj = _json.loads(txt)
    except Exception:
        start, end = txt.find("{"), txt.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            obj = _json.loads(txt[start:end + 1])
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    primary = obj.get("primary_cause") or obj.get("attribution") or ""
    secondary = obj.get("secondary_cause") or ""
    emotion = obj.get("emotion") or obj.get("mood") or ""
    weak = obj.get("weak_topics") or []
    if isinstance(weak, str):
        weak = [w.strip() for w in weak.split("、") if w.strip()]
    return {
        "primary_cause": primary,
        "secondary_cause": secondary,
        "weak_topics": weak,
        "emotion": emotion,
        "evidence_summary": obj.get("evidence_summary") or "",
    }


def interpret_feedback(text, llm_text=None):
    """Skill 4：理解用户自然语言反馈 → 结构化信号。

    返回 {primary_cause, secondary_cause, weak_topics, emotion, evidence_summary, source, evidence}。
    llm_text：LLM 返回的 JSON 字符串（可选）。为空或解析失败时走关键词匹配降级。
    """
    def _evidence_for(result, method):
        lines = []
        if result.get("primary_cause"):
            lines.append(f"主因：{result['primary_cause']}")
        if result.get("secondary_cause"):
            lines.append(f"次因：{result['secondary_cause']}")
        if result.get("weak_topics"):
            lines.append(f"弱知识点：{'、'.join(result['weak_topics'])}")
        if result.get("emotion"):
            lines.append(f"情绪状态：{result['emotion']}")
        lines.append(f"依据来源：{method}")
        return lines

    if llm_text:
        parsed = _parse_llm_feedback(llm_text)
        if parsed and parsed.get("primary_cause"):
            result = {**parsed, "source": "llm"}
            result["evidence"] = _evidence_for(result, "LLM 结构化抽取")
            return result
    result = {**_keyword_feedback(text), "source": "keyword"}
    result["evidence"] = _evidence_for(result, "关键词规则（离线）")
    return result


# ============================================================================
# 升级 ⑤：proactive_scan —— 事前冲突处理（预测 + 削峰填谷）
# ============================================================================

def proactive_scan(days, backlog=0, daily_available_hours=6.0, rules=None):
    """Skill 5：事前冲突处理。预测未来 7 天冲突 + 削峰填谷 + 降级决策。

    输入：
      days: [{date, weekday, planned_hours}]，未来 7 天（planned_hours 为计划学习负荷）
      backlog: 错题本未掌握题目数（用于加码判断）
      daily_available_hours: 每日可用学习时长
    输出：预测冲突 + 削峰填谷方案 + 降级决策（带负载上限 = available × 1.2）。
    """
    rules = rules or DEFAULT_RULES
    overload_factor = rules.get("proactive_scan", DEFAULT_RULES["proactive_scan"]).get("overload_factor", 1.2)
    cap = daily_available_hours * overload_factor  # 负载上限

    # 1) 逐日负荷评估
    daily = []
    for d in days:
        planned = float(d.get("planned_hours", 0))
        daily.append({
            "date": d.get("date", ""),
            "weekday": d.get("weekday", ""),
            "planned_hours": planned,
            "available": daily_available_hours,
            "cap": round(cap, 1),
            "overload": planned > cap,
        })

    # 2) 削峰填谷：超载日超出 available 的部分，贪心平移到空闲日
    moves = []
    excess_days = [[d["date"], d["planned_hours"] - daily_available_hours]
                   for d in daily if d["planned_hours"] > daily_available_hours]
    slack_days = [[d["date"], daily_available_hours - d["planned_hours"]]
                  for d in daily if d["planned_hours"] < daily_available_hours]
    for src, amount in excess_days:
        remaining = amount
        for i, (dst, slack) in enumerate(slack_days):
            if remaining <= 0:
                break
            if slack <= 0:
                continue
            move = min(remaining, slack)
            moves.append({"from": src, "to": dst, "hours": round(move, 1)})
            remaining -= move
            slack_days[i][1] -= move

    # 3) 削峰后仍超载 → 降级 low priority 任务
    total_load = sum(d["planned_hours"] for d in daily)
    total_cap = cap * len(daily)
    degraded = []
    conclusion = ""
    if total_load > total_cap:
        over = round(total_load - total_cap, 1)
        cut = round(min(over, 1.5), 1)
        degraded = [{"subject": "竞赛/AI（low priority）", "hours": cut}]
        conclusion = f"削峰后仍超载 {over}h，系统决定降级 {len(degraded)} 个 low priority 任务（砍 {cut}h）"
    else:
        conclusion = "削峰填谷后，未来 7 天负荷全部进入负载上限内"

    overload_count = sum(1 for d in daily if d["overload"])
    load_summary = "，".join(f"{d['date'][5:]} {d['planned_hours']}h" for d in daily)
    remaining = round(max(0.0, total_load - total_cap), 1)
    evidence = [
        f"未来 7 天负荷：[{load_summary}]",
        f"超载天数：{overload_count}",
        f"削峰后剩余：{remaining}h",
    ]
    return {
        "cap": round(cap, 1),
        "overload_count": overload_count,
        "daily": daily,
        "moves": moves,
        "degraded": degraded,
        "conclusion": conclusion,
        "backlog_note": f"错题本未掌握 {backlog} 题，建议纳入补欠池" if backlog else "",
        "evidence": evidence,
    }


# ============================================================================
# 升级 ⑦：reschedule_for_calendar_change —— 课表变动 → 冲突检测 + 重排
# ============================================================================

WEEKDAY_INDEX = {"周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7}

# 候选自修时段（重排时从中挑“离原时段最近”的空闲时段）
STUDY_WINDOWS = [
    "07:30-08:00",
    "08:00-09:40",
    "10:10-11:50",
    "14:30-16:10",
    "16:20-18:00",
    "19:00-20:30",
    "20:30-22:00",
]

# 可排学习任务的教学日（周一~周五）
STUDY_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五"]


def _weekday_distance(a, b):
    """两个星期的环向距离（周一~周五循环）：同天=0，相邻=1。"""
    ia = WEEKDAY_INDEX.get(a, 0)
    ib = WEEKDAY_INDEX.get(b, 0)
    if not ia or not ib:
        return 99
    d = abs(ia - ib)
    return min(d, 5 - d)


def _parse_weeks(weeks_str):
    """解析周次字符串（"6-16" / "3,5,7" / "17"）为 int 集合；空返回空集。"""
    result = set()
    if weeks_str is None:
        return result
    s = str(weeks_str).replace("，", ",")
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            try:
                result.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                result.add(int(part))
            except ValueError:
                pass
    return result


def _weeks_overlap(a, b):
    """两组周次是否有交集；任一为空视为“全年/不限”，与任意周次相交。"""
    if not a or not b:
        return True
    return bool(a & b)


def _time_to_min(t):
    h, m = t.strip().split(":")
    return int(h) * 60 + int(m)


def _time_overlap(a, b):
    """a/b 为 "HH:MM-HH:MM"，判断时间区间是否有重叠（端点相接视为不重叠）。"""
    a1, a2 = a.split("-")
    b1, b2 = b.split("-")
    return not (_time_to_min(a2) <= _time_to_min(b1) or _time_to_min(b2) <= _time_to_min(a1))


def _task_time(task):
    return task.get("time") or (task.get("scheduled_slots") or [None])[0]


def _task_day(task):
    return task.get("day_of_week") or task.get("weekday") or ""


def _task_weeks(task):
    return _parse_weeks(task.get("weeks"))


def _entry_conflicts_task(entry, task):
    """单个课表条目是否与某学习任务在 星期/时间/周次 上冲突。"""
    et = entry.get("time", "")
    tt = _task_time(task)
    if not et or not tt:
        return False
    return (
        entry.get("day_of_week") == _task_day(task)
        and _time_overlap(et, tt)
        and _weeks_overlap(_parse_weeks(entry.get("weeks")), _task_weeks(task))
    )


def _task_conflicts_entries(task, entries):
    return any(_entry_conflicts_task(e, task) for e in entries)


def _diff_schedule(old_entries, new_entries):
    """对比新旧课表，识别 新增/调课/取消（主键 = day_of_week + time）。"""
    changes = []
    old_by_key = {(e.get("day_of_week"), e.get("time")): e for e in old_entries}
    new_by_key = {(e.get("day_of_week"), e.get("time")): e for e in new_entries}
    key_sort = lambda k: (WEEKDAY_INDEX.get(k[0], 9), k[1])

    for k in sorted(set(new_by_key) - set(old_by_key), key=key_sort):
        e = new_by_key[k]
        changes.append({"type": "新增", "course": e.get("course", ""), "day_of_week": k[0],
                        "time": k[1], "weeks": e.get("weeks", "")})
    for k in sorted(set(old_by_key) - set(new_by_key), key=key_sort):
        e = old_by_key[k]
        changes.append({"type": "取消", "course": e.get("course", ""), "day_of_week": k[0],
                        "time": k[1], "weeks": e.get("weeks", "")})
    for k in sorted(set(old_by_key) & set(new_by_key), key=key_sort):
        o, n = old_by_key[k], new_by_key[k]
        if o.get("course") != n.get("course") or o.get("weeks") != n.get("weeks"):
            changes.append({"type": "调课", "course": f"{o.get('course', '')} → {n.get('course', '')}",
                            "day_of_week": k[0], "time": k[1],
                            "weeks_old": o.get("weeks", ""), "weeks_new": n.get("weeks", "")})
    return changes


def _subject_priority_map(learning_memory, rules):
    """复用 allocate 的优先级打分：weight = 优先级 × 紧迫度 × 严重度（归一化）。"""
    weights_cfg = rules.get("subject_weights", DEFAULT_RULES["subject_weights"])
    subjects = learning_memory.get("subjects", {})
    raw = {}
    for name, info in subjects.items():
        base = weights_cfg.get(name, 0.1)
        urgency = _urgency_factor(_days_left(info.get("deadline")))
        severity = _severity_factor(info.get("consecutive_failures", 0))
        raw[name] = base * urgency * severity
    s = sum(raw.values()) or 1.0
    return {k: v / s for k, v in raw.items()}


def _find_free_windows(new_entries, day_of_week, weeks):
    """在某星期、某周次范围内，找出与新课表不冲突的候选自修时段（按 STUDY_WINDOWS 原始顺序）。"""
    free = []
    for w in STUDY_WINDOWS:
        blocked = any(
            e.get("day_of_week") == day_of_week
            and e.get("time")
            and _time_overlap(e.get("time", ""), w)
            and _weeks_overlap(_parse_weeks(e.get("weeks")), weeks)
            for e in new_entries
        )
        if not blocked:
            free.append(w)
    return free


def _find_free_slots_cross_day(new_entries, day_of_week, weeks, near_time=None):
    """跨天查找空闲自修时段：先同一天（按时间就近），再相邻星期（按星期就近）。

    返回 [(星期, 时段), ...]，按 (星期环向距离, 距原时段的时间差) 升序排列。
    """
    near_min = _time_to_min(near_time.split("-")[0]) if near_time else None
    cands = []
    for wd in STUDY_WEEKDAYS:
        for win in _find_free_windows(new_entries, wd, weeks):
            day_dist = _weekday_distance(day_of_week, wd)
            time_dist = abs(_time_to_min(win.split("-")[0]) - near_min) if near_min is not None else 0
            cands.append((day_dist, time_dist, wd, win))
    cands.sort(key=lambda x: (x[0], x[1]))
    return [(wd, win) for _, _, wd, win in cands]


def reschedule_for_calendar_change(old_schedule, new_schedule, current_plan,
                                   learning_memory, daily_available_hours=6.0,
                                   rules=None, today=None):
    """Skill 6：课表变动 → 冲突检测 + 未来受影响日期重排 + 必要时 allocate。

    输入：旧课表 + 新课表 + 当前计划 + 学习记忆（+ 每日可用时长 + 规则）
    输出：affected_tasks / rearranged_plan / explanation
    调用条件：检测到课表 version 变化时自动触发（version 相同则返回 triggered=False）。
    """
    rules = rules or DEFAULT_RULES
    overload_factor = rules.get("proactive_scan", DEFAULT_RULES["proactive_scan"]).get("overload_factor", 1.2)
    old_entries = (old_schedule or {}).get("entries", []) if isinstance(old_schedule, dict) else (old_schedule or [])
    new_entries = (new_schedule or {}).get("entries", []) if isinstance(new_schedule, dict) else (new_schedule or [])
    old_ver = old_schedule.get("version") if isinstance(old_schedule, dict) else None
    new_ver = new_schedule.get("version") if isinstance(new_schedule, dict) else None

    if old_ver is not None and new_ver is not None and old_ver == new_ver:
        return {
            "triggered": False,
            "reason": f"课表 version 未变化（均为 {new_ver}），无需重排",
            "changes": [], "affected_tasks": [], "moves": [], "rearranged_plan": current_plan,
            "overload_days": [], "cap": round(daily_available_hours * overload_factor, 1),
            "allocate_triggered": False, "allocations": [], "explanation": [],
        }

    changes = _diff_schedule(old_entries, new_entries)
    tasks = list((current_plan or {}).get("tasks", []))

    # 受影响任务：与新课表冲突、但旧课表不冲突（即“课表变动新挤占”的任务）
    affected = [t for t in tasks
                if _task_conflicts_entries(t, new_entries) and not _task_conflicts_entries(t, old_entries)]

    # 重排：按优先级打分从高到低，就近平移到同一天的空闲时段
    prio = _subject_priority_map(learning_memory, rules)
    affected.sort(key=lambda t: prio.get(t.get("subject"), 0), reverse=True)
    moves = []
    rearranged_tasks = [dict(t) for t in tasks]
    for t in affected:
        day = _task_day(t)
        weeks = _task_weeks(t)
        old_time = _task_time(t)
        slots = _find_free_slots_cross_day(new_entries, day, weeks, near_time=old_time)
        if any(wd == day and win == old_time for wd, win in slots):
            # 原时段在新课表下已不再冲突，保持不动
            new_day, new_time = day, old_time
        elif slots:
            # 就近平移：先同一天最近时段，当天已满则跨到最近的相邻星期
            new_day, new_time = slots[0]
        else:
            new_day, new_time = day, old_time
        blocker = next((e for e in new_entries if _entry_conflicts_task(e, t)), None)
        if new_time != old_time or new_day != day:
            if new_day == day:
                why = "就近平移至同天空闲时段"
            else:
                why = f"当天空闲时段不足，就近调至 {new_day} 空闲时段"
            moves.append({
                "task_id": t["task_id"], "subject": t["subject"],
                "from_slot": f"{day} {old_time}", "to_slot": f"{new_day} {new_time}",
                "reason": f"原时段与新课「{blocker.get('course', '?') if blocker else '?'}」冲突，{why}",
            })
        for rt in rearranged_tasks:
            if rt["task_id"] == t["task_id"]:
                rt["time"] = new_time
                rt["day_of_week"] = new_day
                if "weekday" in rt:
                    rt["weekday"] = new_day
                if "scheduled_slots" in rt:
                    rt["scheduled_slots"] = [new_time]
                break

    # 重排后按星期聚合负荷，超上限（每日可用 × overload_factor）则触发 allocate 全局再分配
    load = {}
    for rt in rearranged_tasks:
        day = _task_day(rt)
        if not day:
            continue
        load[day] = load.get(day, 0) + float(rt.get("planned_hours", 0))
    cap = daily_available_hours * overload_factor
    overload_days = sorted([d for d, h in load.items() if h > cap])
    allocate_triggered = bool(overload_days)
    allocations = []
    if allocate_triggered:
        allocations = allocate(learning_memory, {"tasks": rearranged_tasks},
                               daily_available_hours, rules=rules, today=today)["allocations"]

    # 可解释说明（覆盖 4 个必答项：变动了什么 / 哪些受影响 / 怎么重排 / 为什么）
    explanation = []
    for c in changes:
        if c["type"] == "新增":
            explanation.append(f"课表新增「{c['course']}」{c['day_of_week']} {c['time']}（第 {c['weeks']} 周）")
        elif c["type"] == "取消":
            explanation.append(f"课表取消「{c['course']}」{c['day_of_week']} {c['time']}（原第 {c['weeks']} 周）")
        else:
            explanation.append(f"课表调课「{c['course']}」{c['day_of_week']} {c['time']}（第 {c['weeks_old']} 周 → 第 {c['weeks_new']} 周）")
    for m in moves:
        explanation.append(f"受影响任务 [{m['subject']}]{m['task_id']}：{m['from_slot']} → {m['to_slot']}（{m['reason']}）")
    if allocate_triggered:
        explanation.append(
            f"重排后 {len(overload_days)} 天负荷超上限 {round(cap, 1)}h（{'、'.join(overload_days)}），"
            f"触发 allocate 全局再分配")

    return {
        "triggered": bool(changes),
        "reason": f"课表 version {old_ver} → {new_ver}，检测到 {len(changes)} 处变动",
        "changes": changes,
        "affected_tasks": [
            {"task_id": t["task_id"], "subject": t["subject"], "content": t.get("content", ""),
             "day_of_week": _task_day(t), "time": _task_time(t)}
            for t in affected
        ],
        "moves": moves,
        "rearranged_plan": {"tasks": rearranged_tasks},
        "overload_days": overload_days,
        "cap": round(cap, 1),
        "allocate_triggered": allocate_triggered,
        "allocations": allocations,
        "explanation": explanation,
    }


def _build_goal_stages(target_date, today, rules):
    """按剩余天数拆成 基础/强化/冲刺 三阶段，返回 (stages, current_stage, remaining_days)。"""
    stage_cfg = (rules or {}).get("goal", {}).get("stages") or [
        {"name": "基础阶段", "fraction": 0.4, "focus": "覆盖全部知识模块第一轮，打牢基础"},
        {"name": "强化阶段", "fraction": 0.4, "focus": "分模块刷题，攻克弱知识点"},
        {"name": "冲刺阶段", "fraction": 0.2, "focus": "真题 + 模拟 + 查漏补缺"},
    ]
    try:
        td = datetime.strptime(target_date, "%Y-%m-%d").date()
    except Exception:
        return [], "目标日期未知", None
    try:
        t0 = datetime.strptime(today, "%Y-%m-%d").date()
    except Exception:
        t0 = date.today()
    remaining_days = (td - t0).days
    if remaining_days <= 0:
        return [], "已到目标日期", remaining_days

    stages = []
    cursor = t0
    current_stage = None
    for i, st in enumerate(stage_cfg):
        frac = float(st.get("fraction", 0.33))
        span = max(1, int(round(remaining_days * frac)))
        end = min(cursor + timedelta(days=span - 1), td)
        # 最后一阶段强制收尾到目标日期，避免阶段之间留缝
        if i == len(stage_cfg) - 1:
            end = td
        stages.append({
            "name": st["name"],
            "start": cursor.isoformat(),
            "end": end.isoformat(),
            "focus": st.get("focus", ""),
        })
        if current_stage is None and t0 <= end:
            current_stage = st["name"]
        cursor = end + timedelta(days=1)
        if cursor > td:
            break
    if current_stage is None and stages:
        current_stage = stages[-1]["name"]
    return stages, current_stage, remaining_days


def decompose_goal(user_profile, current_plan, learning_memory, rules=None, today=None):
    """Skill 7：目标拆解。把「考研大目标」拆成知识模块 + 阶段里程碑，检查当前计划覆盖缺口。

    对应命题背景「目标不清」：用户只有「2027 考研」一个抽象目标，不知道还差哪些模块、
    每个阶段该干什么。本 Skill 把目标拆成可检查的模块清单，并指出当前计划的缺口。

    输入：用户画像（goal / target_date）+ 当前计划 + 学习记忆 + 规则
    输出：需覆盖模块 / 已覆盖模块 / 缺失模块 / 阶段里程碑 / 当前阶段 / 可解释建议
    """
    rules = rules or DEFAULT_RULES
    goal = (user_profile or {}).get("goal", "")
    target_date = (user_profile or {}).get("target_date", "")
    today = today or date.today().isoformat()

    # 1) 从目标关键词识别需覆盖的知识模块
    required = []
    for kw, mods in GOAL_REQUIRED_MODULES.items():
        if kw in goal:
            for m in mods:
                if m not in required:
                    required.append(m)

    # 2) 当前计划里出现的科目模块（含目标之外的公共课，如英语）
    plan_modules = []
    for t in (current_plan or {}).get("tasks", []):
        m = SUBJECT_TO_MODULE.get(t.get("subject"))
        if m and m not in plan_modules:
            plan_modules.append(m)

    covered = [m for m in required if m in plan_modules]   # 所需模块中已被覆盖的
    missing = [m for m in required if m not in plan_modules]
    extra = [m for m in plan_modules if m not in required]  # 目标之外的科目（英语等），不参与覆盖率

    # 3) 阶段拆解
    stages, current_stage, remaining_days = _build_goal_stages(target_date, today, rules)

    # 4) 可解释建议
    if not required:
        recommendation = (
            f"未在目标「{goal}」中识别到已知考研科目关键词（数学一/数学二/408），"
            f"暂无法做模块覆盖检查。当前计划覆盖：{'、'.join(plan_modules) if plan_modules else '无'}。"
        )
    elif missing:
        recommendation = (
            f"目标需覆盖 {len(required)} 个模块，当前计划仅覆盖 {len(covered)} 个"
            f"（{'、'.join(covered) if covered else '无'}），缺少：{'、'.join(missing)}。"
            f"建议在计划中补入这些模块的任务，否则目标可能落空。"
        )
    else:
        recommendation = f"当前计划已覆盖目标全部 {len(required)} 个模块，覆盖完整。"

    return {
        "goal": goal,
        "target_date": target_date,
        "remaining_days": remaining_days,
        "required_modules": required,
        "covered_modules": covered,
        "missing_modules": missing,
        "extra_subjects": extra,
        "coverage_rate": round(len(covered) / len(required), 4) if required else None,
        "stages": stages,
        "current_stage": current_stage,
        "recommendation": recommendation,
    }


def aggregate_resources(weak_topics, resource_catalog=None):
    """Skill 8：资源聚合。把分散资源（视频/课后题/错题本/单词本）按弱知识点收拢成一条清单。

    对应命题背景「资源分散」：视频、讲义、课后题、错题本、单词本散落各处，学生不知道
    该用什么补弱项。本 Skill 把资源按弱知识点聚合，输出「该弱知识点 → 该用的资源」。

    输入：弱知识点列表（来自 diagnose 或 memory）
    输出：每个弱知识点对应的资源清单 + 一句可解释总结
    """
    catalog = resource_catalog or RESOURCE_CATALOG
    topics = list(dict.fromkeys(weak_topics or []))  # 去重保序
    plan = []
    for topic in topics:
        res = catalog.get(topic, DEFAULT_RESOURCES)
        plan.append({"topic": topic, "resources": [{"type": t, "name": n} for t, n in res]})
    if plan:
        summary = (
            "为弱知识点「" + "」「".join(topics) + "」各聚合了视频/课后题/错题本等资源，"
            "把分散资料收拢到一条可执行的补齐路径上。"
        )
    else:
        summary = "暂无弱知识点，无需聚合资源。"
    return {"weak_topics": topics, "plan": plan, "summary": summary}
