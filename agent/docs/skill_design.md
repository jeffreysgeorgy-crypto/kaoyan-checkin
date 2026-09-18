# Skill 设计说明（对应答题点 ②）

Agent 的「决策层」只有两个可解释的 Skill，全部是确定性规则，不依赖 LLM。
LLM（Qwen3-32B）只负责在 openJiuwen 的 ReAct 循环里按需调度它们并撰写决策日志。

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

## 设计取舍

1. **规则引擎 + LLM 编排分离**：调整逻辑是确定性的，可复现、可审计、可单测；
   LLM 只做「何时调 Skill + 如何解释」，把可解释性交给结构化的 `reason/evidence`。
2. **调整明细自带证据**：每一条调整都带 `before/after/reason/evidence`，
   其中 `evidence` 直接引用历史记录与反思原文，避免「模型瞎编理由」。
3. **兜底升级**：用 `replan_count` 记录连续重规划次数，避免「同一招反复用」，
   两次后强制换策略并给出三个方向（推荐 A）——这是对「动态调整」的闭环收口。
