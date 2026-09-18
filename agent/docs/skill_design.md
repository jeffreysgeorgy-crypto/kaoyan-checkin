# Skill 设计说明（对应答题点 ②）

Agent 的「决策层」有 **6 个可解释的 Skill**，全部是确定性规则，不依赖 LLM。
LLM（DeepSeek V4）只负责在 openJiuwen 的 ReAct 循环里按需调度它们并撰写决策日志。

> 6 个 Skill：diagnose（诊断）/ replan（重规划）/ allocate（资源再分配）/
> interpret_feedback（反馈理解）/ proactive_scan（负荷扫描）/ reschedule_for_calendar_change（课表重排）。

---

## Skill 1：diagnose（学习诊断）

| 项 | 内容 |
| --- | --- |
| 输入 | `learning_memory`（各科目掌握度 / 连续失败天数 / 近7天完成率 / 弱知识点 / 反思原文） |
| 输出 | 诊断报告 `diagnosis`：`overall_status` + `alert_subjects[]`（severity / reasons / primary_cause / reflection_quotes）+ `healthy_subjects[]` |
| 调用条件 | ① 生成新计划前；② 某科目连续 3 天未完成、触发重规划前 |

**严重度判定**（`skills._severity`）：

| 条件 | 严重度 |
| --- | --- |
| 连续失败 ≥ 5 天 | `high` |
| 连续失败 ≥ 3 天（阈值） | `high` |
| 连续失败 ≥ 2 天，或近 7 天完成率 < 50% | `medium` |
| 其它 | `low` |

**归因**（`skills._primary_cause`）：对反思原文做关键词匹配，输出可解释主因——
「方法不当」「时间投入不足」「目标与能力不匹配」等。

---

## Skill 2：replan（动态重规划）

| 项 | 内容 |
| --- | --- |
| 输入 | `current_plan` + `diagnosis` + `learning_memory` + `replan_count`（已重规划次数） |
| 输出 | `(new_plan, adjustments)`；`adjustments` 每条含 `task_id / subject / level / before / after / reason / evidence` |
| 调用条件 | 诊断出预警科目后，对计划做针对性调整 |

**分级干预**：

| 触发条件 | 干预动作 | level |
| --- | --- | --- |
| 已重规划 ≥ 2 次 | 换任务类型（概念梳理+错题回顾）+ 给出三个方向（推荐 A） | `escalation` |
| 严重度 high 且连续失败 ≥ 5 天 | 降级内容难度 + 缩短时长 | `downgrade` |
| 严重度 high 且连续失败 = 3 天 | 减少时长 + 拆分任务 | `split` |
| 严重度 medium | 仅标红提醒，不改内容 | `remind` |
| 严重度 low | 不干预 | — |

---

## Skill 3：allocate（多科目资源再分配）

| 项 | 内容 |
| --- | --- |
| 输入 | `learning_memory` + `current_plan` + `daily_available_hours`（每日可用时长，来自画像） |
| 输出 | 每科分配时长 + 可解释分配理由 |
| 调用条件 | 多科目冲突时：`Σ planned_hours > available`（总负荷超可用时长）或 `≥2 科预警` |

**分配公式**（`skills.allocate`）：

```text
每科分配 = max(weight × 每日可用时长, 最低保底 0.5h)
weight   = 科目优先级(subject_weights) × 阶段紧迫度(deadline) × 连续失败严重度
```

理由随分配一起输出，说明每科权重的来源（优先级 / 紧迫度 / 严重度），可解释。

---

## Skill 4：interpret_feedback（反馈理解）

| 项 | 内容 |
| --- | --- |
| 输入 | 用户反思 / 碎碎念原文（`text`），可选 LLM 返回的 JSON（`llm_text`） |
| 输出 | 结构化信号：`primary_cause / secondary_cause / weak_topics / emotion / evidence_summary / source / evidence` |
| 调用条件 | 用户提交反思原文时（反思页「💬 分析反思」） |

**降级策略**：优先 LLM 抽取（温度 0）；满足任一降级条件（未配 key / 超时 > 5s / 连续失败 3 次 / 解析失败）自动走 `skills.interpret_feedback` 关键词匹配，返回结果带 `degraded` + `degrade_reason` 标注。

---

## Skill 5：proactive_scan（未来 7 天负荷扫描）

| 项 | 内容 |
| --- | --- |
| 输入 | 未来 7 天 `days[]`（`{date, weekday, planned_hours}`）+ `backlog`（错题本未掌握数）+ `daily_available_hours` |
| 输出 | 逐日负荷评估 + 削峰填谷方案 + 降级决策 |
| 调用条件 | 计划生成后、需要事前负荷预测时 |

**关键逻辑**（`skills.proactive_scan`）：负载上限 = 每日可用时长 × 1.2（`overload_factor`）；超载日标红并给出削峰方案，把高峰任务平移至低峰日。

---

## Skill 6：reschedule_for_calendar_change（课表变动重排）

| 项 | 内容 |
| --- | --- |
| 输入 | 旧课表 + 新课表 + 当前计划 + 学习记忆（+ 每日可用时长） |
| 输出 | `affected_tasks`（受影响任务）/ `rearranged_plan`（重排后计划）/ `explanation`（说明） |
| 调用条件 | 检测到课表 `version` 变化时自动触发（version 相同则返回 `triggered=false`，不重排） |

**关键逻辑**（`skills.reschedule_for_calendar_change`）：对比新旧课表、检测冲突、就近平移 / 跨天平移重排未来受影响任务，必要时触发 `allocate` 全局再分配。

---

## 设计取舍

1. **规则引擎 + LLM 编排分离**：调整逻辑是确定性的，可复现、可审计、可单测；
   LLM 只做「何时调 Skill + 如何解释」，把可解释性交给结构化的 `reason/evidence`。
2. **调整明细自带证据**：每一条调整都带 `before/after/reason/evidence`，
   其中 `evidence` 直接引用历史记录与反思原文，避免「模型瞎编理由」。
3. **兜底升级**：用 `replan_count` 记录连续重规划次数，避免「同一招反复用」，
   两次后强制换策略并给出三个方向（推荐 A）——这是对「动态调整」的闭环收口。
4. **6 个 Skill 覆盖全生命周期**：诊断（怎么变差）→ 重规划（怎么改）→ 分配（多科怎么分）→
   反馈理解（为什么差）→ 负荷扫描（未来会不会超）→ 课表重排（外部变动怎么接）。
