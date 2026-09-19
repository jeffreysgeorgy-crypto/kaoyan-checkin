# memory.json 结构说明（学习记忆 Schema）

本文档说明「个人学习规划 Agent」的记忆如何存储与更新（对应答题点 ①）。

## 一、总体结构

```jsonc
{
  "user_profile":   { /* 用户画像 */ },
  "current_plan":   { /* 当前学习计划 */ },
  "schedule":       { /* 本学期课表（目标拆解的模块覆盖来源之一） */ },
  "learning_memory":{ /* 学习记忆（各科目画像） */ },
  "rules":          { /* 重规划触发规则 */ },
  "recent_records": [ /* 近 7 天打卡流水 */ ],
  "replanning_log": [ /* 历史重规划记录 */ ]
}
```

## 二、字段逐项说明

### 1. user_profile —— 用户画像

| 字段 | 说明 |
| --- | --- |
| `name` | 用户姓名 |
| `goal` | 长期目标（2027 考研：数一 + 408） |
| `target_date` | 目标日期 |
| `daily_available_hours` | 每日可投入学习时长（个性化规划的约束） |
| `preferred_start_time` | 偏好开始时间 |

### 2. current_plan —— 当前计划

| 字段 | 说明 |
| --- | --- |
| `generated_at` | 计划生成日期 |
| `tasks[]` | 任务列表，每条含 `task_id / subject / content / planned_hours / scheduled_slots / status / priority / depends_on`，重规划时可能新增 `flag`（red / split / downgraded / escalated） |

### 3. learning_memory.subjects —— 学习记忆（核心）

每个科目一份，含：

| 字段 | 说明 |
| --- | --- |
| `mastery_score` | 掌握度对象，见下方「掌握度公式」 |
| `consecutive_failures` | 连续未完成天数（驱动重规划触发） |
| `recent_7d_completion_rate` | 近 7 天完成率 |
| `weak_topics` | 弱知识点（用于诊断定位） |
| `recent_reflections` | 反思原文列表（`{date, text, source}`，供归因引用） |

`recent_reflections` 字段存储用户最近 5–7 条反思原文，由用户在打卡时填写，或从其他学习 App 导入，
是 `diagnose` 和 `interpret_feedback` 的主要输入来源。每条记录带 `source` 标注来源：
`initial_memory`（初始预置）或 `runtime_injected`（运行时注入）。

#### 掌握度公式（可解释性关键）

```text
mastery_score.value = 完成率*0.4 + (1-错题率)*0.4 + 平均效率评分/5*0.2
```

`raw_data` 保存三项原始数据（`completion_rate / error_rate / avg_efficiency`），
`formula` 记录公式文本，`last_calculated` 记录最近一次计算时间——
这样掌握度不是黑盒数字，随时可回放计算过程。

### 4. rules —— 触发规则

```jsonc
{
  "trigger_replan": {
    "consecutive_failures_threshold": 3,   // 连续失败 ≥3 天触发
    "completion_rate_threshold": 0.5,       // 近 7 天完成率 <50% 触发
    "lookback_days": 7
  },
  "task_downgrade": { /* 分级干预动作 */ },
  "escalation":     { "replan_count_threshold": 2, "action": "..." },
  "replan":         { "downgrade_hours": 0.5, "split_reduction": 0.5, "fallback_hours": 0.5, "catchup_max_hours": 1.0 },
  "allocate":       { "high_mastery": 0.8, "high_rate": 0.8, "low_mastery": 0.6, "downgrade_delta": 0.3 },
  "proactive_scan": { "overload_factor": 1.2 }
}
```

#### 关于 rules 的可配置边界

memory.json 的 `rules` 字段包含**业务可调阈值**（触发天数、完成率、拖延上限、分配权重等），
用户可按自己情况调整。公式内部的系数（掌握度公式、紧迫度分档、严重度分档）属于**算法定义**，
内置在代码中，保证公式的一致性。

### 5. recent_records —— 打卡流水

每日打卡追加一条 `{date, task_id, subject, status}`，仅保留近 7 天。
它是「记忆」的事实来源：`consecutive_failures`、`recent_7d_completion_rate`、
`mastery_score` 都由它推导而来。

### 6. replanning_log —— 重规划历史

每次重规划追加一条 `{date, trigger, adjustments}`，长度即为「已重规划次数」，
用于兜底升级判断（连续重规划 ≥2 次 → 换任务类型 + 给出三个方向，推荐 A 补基础）。

## 三、记忆如何更新

`demo.py` 的 `update_memory_after_checkin()` 是唯一的「记忆写入口」，流程：

```text
每日打卡结果
   → 追加到 recent_records
   → 按科目聚合近 7 天 status
   → 计算 recent_7d_completion_rate（done/total）
   → 从最新往回数连续 undone，得 consecutive_failures
   → 用完成率重算 mastery_score（代入公式）
   → 反思原文追加到 recent_reflections
```

整个闭环体现了「记忆随打卡动态更新」，而不是一次性静态配置。
