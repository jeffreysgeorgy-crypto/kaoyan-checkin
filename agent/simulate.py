# -*- coding: utf-8 -*-
"""
simulate.py —— 虚拟学生 A/B 回放实验：用数据证明「动态重规划优于静态计划」。

【与真实数据完全隔离】
    · 不读取、不写入 memory.json / new_plan.json / schedule_last.json；
    · 不连接 Supabase、不启动服务；
    · 所有「学生」都是按可解释概率模型随机生成的虚拟人；
    · 唯一输出：simulation_report.json（实验结果）+ 控制台报告。

【实验设计】
    配对对照：同一批虚拟学生、同一串随机数，各跑两遍——
      A 组（static）：初始计划 30 天不变；
      B 组（dynamic）：每日打卡写回模拟记忆，命中阈值时真实调用
                      skills.diagnose + skills.replan（回放线上规则引擎）。
    因为是配对设计，两组的差异只来自「是否动态调整」。

【虚拟学生行为模型（每个假设都可解释、可答辩）】
    单科当日完成概率 p：
      p = clip( base_ability                      # 个体差异（Beta 分布采样，跨学生不同）
              - subject_difficulty                # 科目难度偏移（数据结构偏难）
              - 0.25 × max(task_hours - 1.5, 0)   # 长任务启动门槛：超过 1.5h 线性降概率
              - 0.06 × min(consecutive_fails, 5)  # 信心衰减：连续失败让人更难开始
              + difficulty_relief, 0.03, 0.97)    # 动态组降级/拆分后难度下降 → 概率回升
    动态组的调整直接来自 skills.replan 的真实输出：
      split（1.5h→1.0h 拆分）长任务惩罚消失；downgrade（0.5h 概念梳理）再给难度补偿；
      escalation 换任务类型后给小幅补偿。

运行：
    py simulate.py                  # 默认 100 名学生 × 30 天，seed=42
    py simulate.py --students 200 --days 30 --seed 7
"""

import argparse
import copy
import json
import os
import random
from datetime import date, timedelta

import skills

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_PATH = os.path.join(HERE, "simulation_report.json")

SUBJECTS = ["数学", "数据结构", "计算机组成原理", "英语"]
# 初始每日计划：合计 6h（对齐画像 daily_available_hours=6）
INITIAL_HOURS = {"数学": 2.0, "数据结构": 2.0, "计算机组成原理": 1.2, "英语": 0.8}
# 科目难度偏移：数据结构对这批 AI 专业本科生偏难（与 demo 叙事一致）
SUBJECT_DIFFICULTY = {"数学": 0.02, "数据结构": 0.16, "计算机组成原理": 0.0, "英语": 0.05}
DEADLINES = {"数学": "2027-01-15", "数据结构": "2026-11-30",
             "计算机组成原理": "2026-11-30", "英语": "2026-12-20"}


# --------------------------------------------------------------------------
# 虚拟学生生成
# --------------------------------------------------------------------------

def generate_student(rng):
    """生成一个虚拟学生：各科基础能力 ~ Beta(5,3)（均值约 0.625，有个体差异）。"""
    return {s: min(0.92, max(0.25, rng.betavariate(5, 3) - SUBJECT_DIFFICULTY[s] + 0.05))
            for s in SUBJECTS}


def completion_prob(ability, task_hours, consecutive_fails, relief):
    """单科当日完成概率（见模块 docstring 的行为模型）。"""
    p = (ability
         - 0.25 * max(task_hours - 1.5, 0.0)   # 长任务启动门槛
         - 0.06 * min(consecutive_fails, 5)    # 连续失败的信心衰减
         + relief)                              # 动态调整带来的难度补偿
    return min(0.97, max(0.03, p))


# --------------------------------------------------------------------------
# 模拟记忆：结构与线上 memory.json 同构（但完全在内存里，绝不落盘到真实文件）
# --------------------------------------------------------------------------

def fresh_sim_memory(today_iso):
    subjects = {}
    for s in SUBJECTS:
        subjects[s] = {
            "mastery_score": {"value": 0.5, "raw_data": {"completion_rate": 0.5, "error_rate": 0.5, "avg_efficiency": 4}},
            "consecutive_failures": 0,
            "recent_7d_completion_rate": 0.5,
            "deadline": DEADLINES[s],
            "procrastination_cost": {"hours": 0, "count": 0},
            "focus_minutes": 0,
            "weak_topics": ["链表"] if s == "数据结构" else [],
            "recent_reflections": [],
        }
    return {
        "user_profile": {"name": "virtual", "goal": "考研", "target_date": "2027-12-18",
                         "daily_available_hours": 6},
        "current_plan": {"date": today_iso, "tasks": []},
        "learning_memory": {"subjects": subjects, "overall": {"last_updated": today_iso}},
        "rules": copy.deepcopy(skills.DEFAULT_RULES),
        "recent_records": [],
        "replanning_log": [],
    }


def build_tasks(task_hours):
    """每科一条任务（task_id 与线上命名一致，便于 replan 定位）。"""
    ids = {"数学": "math_1", "数据结构": "ds_1", "计算机组成原理": "co_1", "英语": "eng_1"}
    return [{
        "task_id": ids[s], "subject": s,
        "content": {"数学": "高数章节讲解 + 严选题", "数据结构": "王道数据结构 + 课后题",
                    "计算机组成原理": "计组章节 + 习题", "英语": "单词 + 长难句"}[s],
        "planned_hours": h, "scheduled_slots": [], "status": "undone",
        "priority": "high" if s in ("数学", "数据结构") else "medium",
    } for s, h in task_hours.items()]


def update_sim_memory(memory, day_iso, statuses):
    """把当日打卡写回模拟记忆：recent_records → 完成率 / 连续失败 / 掌握度（与 demo 同构）。"""
    records = memory["recent_records"]
    for s, st in statuses.items():
        records.append({"date": day_iso, "task_id": f"{s}_1", "subject": s, "status": st})
    distinct = sorted({r["date"] for r in records})
    keep = set(distinct[-7:])
    memory["recent_records"] = [r for r in records if r["date"] in keep]

    by_subject = {}
    for r in memory["recent_records"]:
        by_subject.setdefault(r["subject"], []).append(r["status"])

    for name, info in memory["learning_memory"]["subjects"].items():
        statuses_list = by_subject.get(name, [])
        total = len(statuses_list)
        rate = statuses_list.count("done") / total if total else 0.0
        info["recent_7d_completion_rate"] = round(rate, 4)
        fails = 0
        for st in reversed(statuses_list):
            if st == "undone":
                fails += 1
            else:
                break
        info["consecutive_failures"] = fails
        raw = info["mastery_score"]["raw_data"]
        raw["completion_rate"] = info["recent_7d_completion_rate"]
        info["mastery_score"]["value"] = skills.compute_mastery(raw)
        info["mastery_score"]["last_calculated"] = day_iso
    memory["learning_memory"]["overall"]["last_updated"] = day_iso


# --------------------------------------------------------------------------
# 单组回放
# --------------------------------------------------------------------------

def run_one_group(student, seed_offset, days, dynamic):
    """一个虚拟学生在一组（static/dynamic）中的 30 天回放。返回逐日指标。"""
    rng = random.Random(seed_offset)  # 两组同种子 → 每日随机数一致（配对）
    start = date(2026, 9, 18)
    memory = fresh_sim_memory(start.isoformat())
    task_hours = dict(INITIAL_HOURS)
    # relief：动态组因 replan 降级/换型获得的「难度补偿」，按科记录
    relief = {s: 0.0 for s in SUBJECTS}

    daily_rows = []
    triggers = 0
    cooldown_until = -1  # 实质性调整（split/downgrade/escalation）后的 2 天观察窗口
    for d in range(days):
        day_iso = (start + timedelta(days=d)).isoformat()
        statuses = {}
        for s in SUBJECTS:
            info = memory["learning_memory"]["subjects"][s]
            p = completion_prob(student[s], task_hours[s], info["consecutive_failures"], relief[s])
            statuses[s] = "done" if rng.random() < p else "undone"

        update_sim_memory(memory, day_iso, statuses)

        adjusted = False
        if dynamic:
            # 触发节奏与真实使用一致（不是每天重规划，避免「计划天天变」）：
            #   ① 任一科目 high 预警（连续失败 ≥3）且不在观察冷却期 → 立即 replan；
            #   ② 每 7 天一次周期性体检（体检日无视冷却，但无预警时 adjustments 为空）。
            # 每次实质调整后留 2 天观察窗口（对齐规则里 hold 分支的产品语义）。
            diag = skills.diagnose(memory["learning_memory"], memory["rules"])
            high_alert = any(a["severity"] == "high" for a in diag["alert_subjects"])
            checkup_day = (d % 7 == 6)
            if (high_alert and d >= cooldown_until) or checkup_day:
                replan_count = len(memory["replanning_log"])
                current_plan = {"date": day_iso, "tasks": build_tasks(task_hours)}
                new_plan, adjustments = skills.replan(
                    current_plan, diag, memory["learning_memory"],
                    replan_count, rules=memory["rules"])
                if adjustments:
                    memory["replanning_log"].append({"date": day_iso, "adjustments": adjustments})
                    triggers += 1
                    adjusted = True
                # 把规则引擎的真实调整作用到后续模拟：时长 + 难度补偿
                for t in new_plan["tasks"]:
                    task_hours[t["subject"]] = float(t["planned_hours"])
                levels = {a.get("level") for a in adjustments}
                for a in adjustments:
                    lvl = a.get("level")
                    if lvl == "downgrade":
                        relief[a["subject"]] = max(relief[a["subject"]], 0.15)
                    elif lvl == "escalation":
                        relief[a["subject"]] = max(relief[a["subject"]], 0.10)
                    # split：长任务变短，惩罚自然消失；hold/remind：不加补偿
                # split/downgrade/escalation 改变了任务形态 → 进入 2 天观察冷却
                if levels & {"split", "downgrade", "escalation"}:
                    cooldown_until = d + 3

        done = sum(1 for st in statuses.values() if st == "done")
        daily_rows.append({"day": d + 1, "done": done, "total": len(SUBJECTS),
                           "rate": done / len(SUBJECTS), "adjusted": adjusted})
    return {"daily": daily_rows, "triggers": triggers, "memory": memory}


# --------------------------------------------------------------------------
# 实验与报告
# --------------------------------------------------------------------------

def subject_final(memory):
    return {s: {
        "completion_rate": memory["learning_memory"]["subjects"][s]["recent_7d_completion_rate"],
        "mastery": memory["learning_memory"]["subjects"][s]["mastery_score"]["value"],
    } for s in SUBJECTS}


def run_experiment(students_n, days, seed):
    rng = random.Random(seed)
    static_rows, dynamic_rows = [], []
    static_final, dynamic_final = [], []
    trigger_counts = []
    paired_wins = 0
    cumulative_static, cumulative_dynamic = [0] * days, [0] * days

    for i in range(students_n):
        student = generate_student(rng)
        # 配对：两组用相同的偏移种子（学生能力也相同）
        static = run_one_group(student, seed * 100003 + i, days, dynamic=False)
        dynamic = run_one_group(student, seed * 100003 + i, days, dynamic=True)

        s_rates = [r["rate"] for r in static["daily"]]
        d_rates = [r["rate"] for r in dynamic["daily"]]
        static_rows.append(s_rates)
        dynamic_rows.append(d_rates)
        for d in range(days):
            cumulative_static[d] += s_rates[d]
            cumulative_dynamic[d] += d_rates[d]

        static_final.append(subject_final(static["memory"]))
        dynamic_final.append(subject_final(dynamic["memory"]))
        trigger_counts.append(dynamic["triggers"])
        if sum(d_rates) > sum(s_rates):
            paired_wins += 1

    n = students_n
    mean = lambda seq: sum(seq) / len(seq)
    static_daily_mean = [cumulative_static[d] / n for d in range(days)]
    dynamic_daily_mean = [cumulative_dynamic[d] / n for d in range(days)]

    def aggregate(final_rows, key):
        return {s: round(mean([row[s][key] for row in final_rows]), 4) for s in SUBJECTS}

    result = {
        "config": {"students": students_n, "days": days, "seed": seed,
                   "design": "paired A/B：同一批虚拟学生、同一随机种子；A=静态计划，B=动态重规划（真实调用 skills.diagnose/replan）"},
        "overall_completion_rate": {
            "static": round(mean([mean(r) for r in static_rows]), 4),
            "dynamic": round(mean([mean(r) for r in dynamic_rows]), 4),
        },
        "final7d_completion_rate_by_subject": {
            "static": aggregate(static_final, "completion_rate"),
            "dynamic": aggregate(dynamic_final, "completion_rate"),
        },
        "final_mastery_by_subject": {
            "static": aggregate(static_final, "mastery"),
            "dynamic": aggregate(dynamic_final, "mastery"),
        },
        "paired_dynamic_better_ratio": round(paired_wins / n, 4),
        "avg_replan_triggers_per_student": round(mean(trigger_counts), 2),
        "daily_mean_completion": {
            "static": [round(x, 4) for x in static_daily_mean],
            "dynamic": [round(x, 4) for x in dynamic_daily_mean],
        },
    }
    a = result["overall_completion_rate"]["static"]
    b = result["overall_completion_rate"]["dynamic"]
    result["lift_percentage_points"] = round((b - a) * 100, 2)
    result["relative_improvement_pct"] = round((b - a) / a * 100, 2)
    return result


def ascii_chart(static_daily, dynamic_daily, width=60, height=14):
    """画两线 ASCII 对比图（30 天滚动平均完成率），不依赖任何绘图库。"""
    def moving_avg(seq, w=3):
        out = []
        for i in range(len(seq)):
            lo = max(0, i - w + 1)
            out.append(sum(seq[lo:i + 1]) / (i - lo + 1))
        return out
    s, d = moving_avg(static_daily), moving_avg(dynamic_daily)
    n = len(s)
    grid = [[" "] * width for _ in range(height)]
    def plot(seq, ch):
        for x in range(width):
            i = min(n - 1, int(round(x * (n - 1) / (width - 1))))
            y = int(round((1 - seq[i]) * (height - 1)))
            grid[y][x] = ch
    plot(s, "·")   # 静态
    plot(d, "*")   # 动态
    lines = ["".join(row).rstrip() for row in grid]
    return "\n".join("      │" + ln for ln in lines) + "\n      └" + "─" * width


def print_report(r):
    print("=" * 72)
    print(f"虚拟学生 A/B 回放实验（{r['config']['students']} 人 × {r['config']['days']} 天，seed={r['config']['seed']}，配对设计）")
    print("=" * 72)
    print("\n【30 天平均每日完成率】")
    print(f"  A 静态计划：{r['overall_completion_rate']['static']:.1%}")
    print(f"  B 动态重规划：{r['overall_completion_rate']['dynamic']:.1%}")
    print(f"  提升：+{r['lift_percentage_points']} 个百分点（相对提升 {r['relative_improvement_pct']}%）")
    print(f"\n【配对胜率】{r['paired_dynamic_better_ratio']:.0%} 的虚拟学生在动态组完成率更高")
    print(f"【平均重规划次数】{r['avg_replan_triggers_per_student']} 次/人")

    print("\n【期末近 7 天各科完成率】")
    print(f"  {'科目':<8}{'静态':>8}{'动态':>8}{'变化':>8}")
    for subj in SUBJECTS:
        a = r["final7d_completion_rate_by_subject"]["static"][subj]
        b = r["final7d_completion_rate_by_subject"]["dynamic"][subj]
        print(f"  {subj:<8}{a:>8.1%}{b:>8.1%}{b-a:>+8.1%}")

    print("\n【期末各科掌握度（按同一公式）】")
    print(f"  {'科目':<8}{'静态':>8}{'动态':>8}{'变化':>8}")
    for subj in SUBJECTS:
        a = r["final_mastery_by_subject"]["static"][subj]
        b = r["final_mastery_by_subject"]["dynamic"][subj]
        print(f"  {subj:<8}{a:>8.2f}{b:>8.2f}{b-a:>+8.2f}")

    print("\n【30 天完成率趋势】（· 静态组，* 动态组，3 日移动平均）")
    print(ascii_chart(r["daily_mean_completion"]["static"], r["daily_mean_completion"]["dynamic"]))
    print("  说明：动态组在连续失败触发 split/downgrade 后，任务时长与难度下降，完成率率先回升。")
    print(f"\n结果已写入 {os.path.basename(REPORT_PATH)}（独立文件，与真实 memory.json 完全隔离）。")


def main():
    parser = argparse.ArgumentParser(description="虚拟学生 A/B 回放实验（不触碰真实数据）")
    parser.add_argument("--students", type=int, default=100, help="虚拟学生数（默认 100）")
    parser.add_argument("--days", type=int, default=30, help="模拟天数（默认 30）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42，保证可复现）")
    args = parser.parse_args()

    result = run_experiment(args.students, args.days, args.seed)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print_report(result)


if __name__ == "__main__":
    main()
