# Skill 设计说明（对应答题点 ②）

Agent 的「决策层」有 **8 个可解释的 Skill**，全部是确定性规则，不依赖 LLM。
LLM（DeepSeek V4）只负责在 openJiuwen 的 ReAct 循环里按需调度它们并撰写决策日志。

> 8 个 Skill：diagnose（诊断）/ replan（重规划）/ allocate（资源再分配）/
> interpret_feedback（反馈理解）/ proactive_scan（负荷扫描）/ reschedule_for_calendar_change（课表重排）/
> decompose_goal（目标拆解）/ aggregate_resources（资源聚合）。

---

## Skill 1：diagnose（学习诊断）

| 项 | 内容 |
| --- | --- |
| 输入 | `learning_memory`（各科目掌握度 / 连续失败天数 / 近7天完成率 / 弱知识点 / 反思原文） |
| 输出 | 诊断报告 `diagnosis`：`overall_status` + `alert_subjects[]`（severity / reasons / primary_cause / secondary_cause / weak_topics / emotion / evidence_summary）+ `healthy_subjects[]` |
| 调用条件 | ① 生成新计划前；② 某科目连续 3 天未完成、触发重规划前 |

**严重度判定**（`skills._severity`）：

| 条件 | 严重度 |
| --- | --- |
| 连续失败 ≥ 5 天 | `high` |
| 连续失败 ≥ 3 天（阈值） | `high` |
| 连续失败 ≥ 2 天，或近 7 天完成率 < 50% | `medium` |
| 其它 | `low` |

**归因**（`skills._attribute_reflections`）：对反思原文做关键词匹配，按 8 个维度
（方法不当 / 情绪干扰 / 精力不足 / 目标不清晰 / 时间投入不足 / 任务过载 / 基础薄弱 / 环境干扰）
命中最多者为主因、次多者为次因，输出可解释的 `primary_cause / secondary_cause / weak_topics / emotion / evidence_summary`。

---

## Skill 2：replan（动态重规划）

| 项 | 内容 |
| --- | --- |
| 输入 | `current_plan` + `diagnosis` + `learning_memory` + `replan_count`（已重规划次数）+ `user_profile`（可选，个性化调度）+ `backlog`（错题本未掌握数） |
| 输出 | `(new_plan, adjustments)`；`adjustments` 每条含 `task_id / subject / level / before / after / reason / evidence` |
| 调用条件 | 诊断出预警科目后，对计划做针对性调整 |

**个性化调度**（画像字段真正参与决策，缺省时行为不变）：

| 画像字段 | 作用 |
| --- | --- |
| `user_profile.preferred_start_time` | 新任务（补欠）的起始时段，落在用户偏好开始时间 |
| `learning_memory[科目].focus_minutes` | 拆分任务的子步骤粒度：首块「看视频/背单词」封顶到一个专注时长 |
| `learning_memory[科目].procrastination_cost` | 拖延代价超阈值触发补欠，且补欠安排在偏好时段「第一时间啃硬骨头」 |

**分级干预**：

| 触发条件 | 干预动作 | level |
| --- | --- | --- |
| 已重规划 ≥ 2 次 | 换任务类型（概念梳理+错题回顾）+ 给出三个方向（推荐 A） | `escalation` |
| 严重度 high 且连续失败 ≥ 5 天 | 降级内容难度 + 缩短时长 | `downgrade` |
| 严重度 high 且连续失败 = 3 天 | 减少时长 + 拆分任务 | `split` |
| 严重度 high 且连续失败 = 4 天 | 维持第 3 天拆分方案（观察窗口，不连续加码），显式留痕 | `hold` |
| 严重度 medium | 仅标红提醒，不改内容 | `remind` |
| 严重度 low | 不干预 | — |
| 错题本 `backlog` > 0 | 新增「错题回顾」任务（重做 N 道未掌握错题，15min/题、封顶 1h），形成错题本 → 计划闭环 | `error_review` |

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

## Skill 7：decompose_goal（目标拆解）

| 项 | 内容 |
| --- | --- |
| 输入 | `user_profile`（goal / target_date）+ `current_plan` + `learning_memory`（+ `rules`） |
| 输出 | `required_modules`（需覆盖模块）/ `covered_modules`（已覆盖）/ `missing_modules`（缺失）/ `coverage_rate` / `stages[]`（阶段里程碑）/ `current_stage` / `recommendation` |
| 调用条件 | 生成新计划前，检查「考研大目标」是否被完整拆解（命题背景「目标不清」） |

**关键逻辑**（`skills.decompose_goal`）：从目标关键词（数学一 / 数学二 / 408）识别需覆盖的知识模块，
对照当前计划的科目，指出「目标模块 vs 已覆盖模块」的缺口（如缺少线代、概率论、操作系统、计算机网络），
并按下剩余天数拆「基础 / 强化 / 冲刺」三阶段里程碑。

---

## Skill 8：aggregate_resources（资源聚合）

| 项 | 内容 |
| --- | --- |
| 输入 | 弱知识点列表 `weak_topics`（来自 diagnose 或 memory） |
| 输出 | 每个弱知识点对应的资源清单（视频 / 课后题 / 错题本 / 单词本）+ `summary` |
| 调用条件 | 诊断出弱知识点后，把分散资料收拢到一条补齐路径（命题背景「资源分散」） |

**关键逻辑**（`skills.aggregate_resources`）：按 `RESOURCE_CATALOG` 把弱知识点映射到具体资源
（视频 / 课后题 / 错题本标签 / 单词本），未命中目录时走兜底资源，把散落的资料按弱项收拢。

---

## 设计取舍

1. **规则引擎 + LLM 编排分离**：调整逻辑是确定性的，可复现、可审计、可单测；
   LLM 只做「何时调 Skill + 如何解释」，把可解释性交给结构化的 `reason/evidence`。
2. **调整明细自带证据**：每一条调整都带 `before/after/reason/evidence`，
   其中 `evidence` 引用连续失败天数、完成率与提炼后的归因结论（主因/次因/弱知识点/情绪），避免「模型瞎编理由」。
3. **兜底升级**：用 `replan_count` 记录连续重规划次数，避免「同一招反复用」，
   两次后强制换策略并给出三个方向（推荐 A）——这是对「动态调整」的闭环收口。
4. **8 个 Skill 覆盖全生命周期**：诊断（怎么变差）→ 重规划（怎么改）→ 分配（多科怎么分）→
   反馈理解（为什么差）→ 负荷扫描（未来会不会超）→ 课表重排（外部变动怎么接）→
   目标拆解（目标拆全了没）→ 资源聚合（该用什么资源补）。
