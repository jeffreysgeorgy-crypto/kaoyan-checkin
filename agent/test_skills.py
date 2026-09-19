# -*- coding: utf-8 -*-
"""
test_skills.py —— skills.py 确定性规则引擎的 pytest 单元测试。

覆盖 8 个 Skill 的关键分支与边界：
    diagnose   健康/medium/high 分级、完成率触发、拖延抬级、多维归因
    replan     split(fails=3) / hold(fails=4) / downgrade(fails≥5) /
               escalation(replan_count≥2) / remind / 补欠防重 / 错题闭环
    allocate   权重归一化、保底时长、触发条件
    interpret_feedback  关键词归因、LLM JSON 解析（围栏/旧字段兼容）
    proactive_scan      超载判定、削峰、总超载降级
    reschedule_for_calendar_change  version 守卫、冲突平移、课表 diff
    decompose_goal      模块识别、缺口、覆盖率、三阶段
    aggregate_resources 目录命中、兜底、去重、空输入

运行：cd agent && py -m pytest test_skills.py -v
"""

import copy

import pytest

import skills


# --------------------------------------------------------------------------
# 测试夹具：构造最小可用的 memory / plan 结构
# --------------------------------------------------------------------------

def make_subject(fails=0, rate=1.0, mastery=0.9, reflections=None,
                 weak=None, proc_hours=0.0, proc_count=0,
                 deadline="2027-01-15", focus_minutes=0):
    return {
        "mastery_score": {"value": mastery},
        "consecutive_failures": fails,
        "recent_7d_completion_rate": rate,
        "deadline": deadline,
        "procrastination_cost": {"hours": proc_hours, "count": proc_count},
        "focus_minutes": focus_minutes,
        "weak_topics": weak or [],
        "recent_reflections": [{"date": "2026-09-10", "text": t} for t in (reflections or [])],
    }


def make_lm(subjects):
    return {"subjects": subjects}


def make_task(subject="数据结构", hours=1.5, slot="14:00-15:30", flag=None, task_id=None):
    t = {
        "task_id": task_id or {"数学": "math_1", "数据结构": "ds_1",
                               "计算机组成原理": "co_1", "英语": "eng_1"}[subject],
        "subject": subject,
        "content": "单链表的插入与删除",
        "planned_hours": hours,
        "scheduled_slots": [slot] if slot else [],
        "status": "undone",
        "priority": "high",
    }
    if flag:
        t["flag"] = flag
    return t


def make_plan(tasks, date="2026-09-18"):
    return {"date": date, "generated_at": date, "tasks": tasks}


def alert_of(diag, subject="数据结构"):
    return next(a for a in diag["alert_subjects"] if a["subject"] == subject)


@pytest.fixture
def rules():
    return copy.deepcopy(skills.DEFAULT_RULES)


# --------------------------------------------------------------------------
# 基础函数：掌握度公式 / 严重度 / 时段校准
# --------------------------------------------------------------------------

class TestCoreHelpers:
    def test_compute_mastery_formula(self):
        # 0.5*0.4 + (1-0)*0.4 + 4/5*0.2 = 0.2 + 0.4 + 0.16 = 0.76
        assert skills.compute_mastery(
            {"completion_rate": 0.5, "error_rate": 0, "avg_efficiency": 4}) == 0.76

    def test_severity_levels(self, rules):
        trig = rules["trigger_replan"]
        sev = lambda f, r=0.9: skills._severity(f, r, trig["consecutive_failures_threshold"],
                                                 trig["completion_rate_threshold"])
        assert sev(0) == "low"
        assert sev(2) == "medium"
        assert sev(3) == "high"
        assert sev(4) == "high"
        assert sev(5) == "high"
        assert sev(1, 0.3) == "medium"  # 完成率 < 0.5 也触发 medium

    def test_calibrate_slots_keeps_start(self):
        t = make_task(hours=1.0, slot="20:30-22:00")
        skills.calibrate_slots(t)
        assert t["scheduled_slots"] == ["20:30-21:30"]

    def test_calibrate_slots_accepts_unicode_dash(self):
        t = make_task(hours=0.5, slot="21:00–21:50")
        skills.calibrate_slots(t)
        assert t["scheduled_slots"] == ["21:00-21:30"]

    def test_resolve_slot_conflicts_pushes_later(self):
        plan = make_plan([
            make_task("数学", hours=1.0, slot="19:00-20:00", task_id="math_1"),
            make_task("数据结构", hours=1.0, slot="19:30-20:30", task_id="ds_1"),
        ])
        skills._resolve_slot_conflicts(plan)
        assert plan["tasks"][1]["scheduled_slots"] == ["20:00-21:00"]  # 顺延且时长不变


# --------------------------------------------------------------------------
# Skill 1：diagnose
# --------------------------------------------------------------------------

class TestDiagnose:
    def test_healthy_when_no_alert(self, rules):
        lm = make_lm({"数据结构": make_subject(fails=0, rate=1.0)})
        diag = skills.diagnose(lm, rules)
        assert diag["overall_status"] == "healthy"
        assert diag["alert_subjects"] == []
        assert len(diag["healthy_subjects"]) == 1

    def test_two_days_is_medium(self, rules):
        lm = make_lm({"数据结构": make_subject(fails=2, rate=0.6)})
        diag = skills.diagnose(lm, rules)
        assert alert_of(diag)["severity"] == "medium"

    def test_three_days_is_high_with_reasons(self, rules):
        lm = make_lm({"数据结构": make_subject(fails=3, rate=0.0)})
        diag = skills.diagnose(lm, rules)
        a = alert_of(diag)
        assert a["severity"] == "high"
        assert any("连续 3 天未完成" in r for r in a["reasons"])
        assert any("完成率 0%" in r for r in a["reasons"])

    def test_procrastination_lifts_low_to_medium(self, rules):
        # fails=0、rate=1.0 本应 low，但拖延 3h（≥2h 阈值）抬到 medium
        lm = make_lm({"数据结构": make_subject(fails=0, rate=1.0, proc_hours=3.0, proc_count=5)})
        diag = skills.diagnose(lm, rules)
        assert alert_of(diag)["severity"] == "medium"
        assert any("拖延代价" in r for r in alert_of(diag)["reasons"])

    def test_multi_dimension_attribution(self, rules):
        lm = make_lm({"数据结构": make_subject(
            fails=3, rate=0.0,
            reflections=["单链表插入删除还是卡，感觉是方法不对", "写到一半就困了，累"])})
        a = alert_of(skills.diagnose(lm, rules))
        assert a["primary_cause"] == "方法不当"
        assert a["emotion"] == "疲惫"
        assert "链表" in a["weak_topics"]
        # 不搬运反思原文，只给提炼结论
        assert "单链表" not in a["evidence_summary"]

    def test_emotional_attribution(self, rules):
        lm = make_lm({"数据结构": make_subject(
            fails=3, rate=0.0, reflections=["和男朋友吵架了，心情难过，焦虑"])})
        a = alert_of(skills.diagnose(lm, rules))
        assert a["primary_cause"] == "情绪干扰"
        assert a["emotion"] in ("低落", "焦虑")


# --------------------------------------------------------------------------
# Skill 2：replan —— 分级干预链
# --------------------------------------------------------------------------

class TestReplanChain:
    def _diag(self, fails, rate=0.0, reflections=None, rules=None):
        lm = make_lm({"数据结构": make_subject(fails=fails, rate=rate, reflections=reflections)})
        return skills.diagnose(lm, rules), lm

    def test_day3_split_reduces_hours(self, rules):
        diag, lm = self._diag(3, rules=rules)
        plan = make_plan([make_task(hours=1.5)])
        new_plan, adj = skills.replan(plan, diag, lm, replan_count=0, rules=rules)
        assert len(adj) == 1
        a = adj[0]
        assert a["level"] == "split"
        assert new_plan["tasks"][0]["planned_hours"] == 1.0   # 1.5-0.5，且 ≥ 一半
        assert new_plan["tasks"][0]["flag"] == "split"
        assert a["before"] != a["after"]
        # 时长变了，时段同步校准（14:00 开始，1.0h → 15:00 结束）
        assert new_plan["tasks"][0]["scheduled_slots"] == ["14:00-15:00"]

    def test_day4_hold_leaves_task_untouched(self, rules):
        diag, lm = self._diag(4, reflections=["卡，方法不对"])
        plan = make_plan([make_task(hours=1.0, flag="split")])
        new_plan, adj = skills.replan(plan, diag, lm, replan_count=1, rules=rules)
        assert len(adj) == 1
        a = adj[0]
        assert a["level"] == "hold"
        assert a["before"] == a["after"]
        assert new_plan["tasks"][0]["planned_hours"] == 1.0
        assert new_plan["tasks"][0]["flag"] == "split"  # 不覆盖既有 flag

    def test_day5_downgrade(self, rules):
        diag, lm = self._diag(5)
        plan = make_plan([make_task(hours=1.0, flag="split")])
        new_plan, adj = skills.replan(plan, diag, lm, replan_count=1, rules=rules)
        a = adj[0]
        assert a["level"] == "downgrade"
        assert new_plan["tasks"][0]["planned_hours"] == 0.5
        assert new_plan["tasks"][0]["flag"] == "downgraded"

    def test_escalation_after_two_replans(self, rules):
        diag, lm = self._diag(3)
        plan = make_plan([make_task(hours=1.5)])
        _, adj = skills.replan(plan, diag, lm, replan_count=2, rules=rules)
        a = adj[0]
        assert a["level"] == "escalation"
        assert "换路径" in a["reason" ] and "推荐" in a["reason"]
        assert a["evidence"]["replan_count"] == 2

    def test_medium_is_remind_only(self, rules):
        lm = make_lm({"数据结构": make_subject(fails=2, rate=0.6)})
        diag = skills.diagnose(lm, rules)
        plan = make_plan([make_task(hours=1.5)])
        new_plan, adj = skills.replan(plan, diag, lm, rules=rules)
        assert adj[0]["level"] == "remind"
        assert new_plan["tasks"][0]["planned_hours"] == 1.5  # 内容时长不变
        assert new_plan["tasks"][0]["flag"] == "red"

    def test_catchup_not_duplicated(self, rules):
        # 不预警（fails=0）但拖延 3h → 追加一条补欠任务
        lm = make_lm({"数据结构": make_subject(fails=0, rate=1.0, proc_hours=3.0, proc_count=6)})
        diag = skills.diagnose(lm, rules)
        plan = make_plan([make_task(hours=1.0)])
        new_plan, adj = skills.replan(plan, diag, lm, rules=rules)
        catch = [a for a in adj if a["level"] == "catch_up"]
        assert len(catch) == 1
        assert any(t.get("flag") == "catch_up" for t in new_plan["tasks"])

        # 第二次重规划：已存在补欠任务，不重复追加
        _, adj2 = skills.replan(new_plan, diag, lm, rules=rules)
        assert len([a for a in adj2 if a["level"] == "catch_up"]) == 0

    def test_backlog_creates_error_review_once(self, rules):
        lm = make_lm({"数据结构": make_subject(fails=0, rate=1.0)})
        diag = skills.diagnose(lm, rules)
        plan = make_plan([make_task(hours=1.0)])
        new_plan, adj = skills.replan(plan, diag, lm, rules=rules, backlog=4)
        assert any(a["level"] == "error_review" for a in adj)
        assert any(t.get("flag") == "error_review" for t in new_plan["tasks"])
        # 幂等：再来一次不重复
        _, adj2 = skills.replan(new_plan, diag, lm, rules=rules, backlog=4)
        assert not any(a["level"] == "error_review" for a in adj2)

    def test_evidence_never_contains_raw_reflection(self, rules):
        diag, lm = self._diag(3, reflections=["单链表插入删除还是卡，感觉是方法不对"])
        plan = make_plan([make_task(hours=1.5)])
        _, adj = skills.replan(plan, diag, lm, rules=rules)
        ev = adj[0]["evidence"]
        assert ev["primary_cause"] == "方法不当"
        assert "单链表" not in ev["conclusion"]


# --------------------------------------------------------------------------
# Skill 3：allocate
# --------------------------------------------------------------------------

class TestAllocate:
    def _four_subjects(self, fails=None):
        fails = fails or {}
        return {
            "数学": make_subject(fails=fails.get("数学", 0), deadline="2027-01-15"),
            "数据结构": make_subject(fails=fails.get("数据结构", 0), deadline="2026-11-30"),
            "计算机组成原理": make_subject(fails=fails.get("计算机组成原理", 0), deadline="2026-11-30"),
            "英语": make_subject(fails=fails.get("英语", 0), deadline="2026-12-20"),
        }

    def test_weights_normalized_and_floor(self, rules):
        lm = make_lm(self._four_subjects())
        plan = make_plan([make_task(s, hours=1.0) for s in lm["subjects"]])
        result = skills.allocate(lm, plan, 6, rules=rules, today="2026-09-18")
        assert abs(sum(a["weight"] for a in result["allocations"]) - 1.0) < 1e-6
        assert all(a["allocated_hours"] >= result["min_floor"] for a in result["allocations"])

    def test_trigger_when_overloaded(self, rules):
        lm = make_lm(self._four_subjects())
        plan = make_plan([make_task(s, hours=3.0) for s in lm["subjects"]])  # 12h > 6h
        result = skills.allocate(lm, plan, 6, rules=rules)
        assert result["triggered"] is True
        assert "总计划时长" in result["trigger_reason"]

    def test_trigger_when_two_subjects_alert(self, rules):
        lm = make_lm(self._four_subjects(fails={"数学": 3, "计算机组成原理": 3}))
        plan = make_plan([make_task(s, hours=1.0) for s in lm["subjects"]])
        result = skills.allocate(lm, plan, 6, rules=rules)
        assert result["triggered"] is True
        assert "2 科同时预警" in result["trigger_reason"]

    def test_not_triggered_when_healthy(self, rules):
        lm = make_lm(self._four_subjects())
        plan = make_plan([make_task(s, hours=1.0) for s in lm["subjects"]])
        result = skills.allocate(lm, plan, 6, rules=rules)
        assert result["triggered"] is False


# --------------------------------------------------------------------------
# Skill 4：interpret_feedback
# --------------------------------------------------------------------------

class TestInterpretFeedback:
    def test_keyword_fallback(self):
        r = skills.interpret_feedback("单链表插入删除还是卡，方法不对，很困")
        assert r["source"] == "keyword"
        assert r["primary_cause"] == "方法不当"
        assert r["emotion"] == "疲惫"
        assert "链表" in r["weak_topics"]

    def test_llm_json_with_code_fence(self):
        llm = '```json\n{"primary_cause": "情绪干扰", "secondary_cause": "", ' \
              '"weak_topics": [], "emotion": "焦虑", "evidence_summary": "情绪问题"}\n```'
        r = skills.interpret_feedback("随便写点什么", llm)
        assert r["source"] == "llm"
        assert r["primary_cause"] == "情绪干扰"
        assert r["emotion"] == "焦虑"

    def test_llm_legacy_fields_compatible(self):
        llm = '{"attribution": "时间投入不足", "mood": "低落"}'
        r = skills.interpret_feedback("x", llm)
        assert r["source"] == "llm"
        assert r["primary_cause"] == "时间投入不足"
        assert r["emotion"] == "低落"

    def test_bad_llm_text_falls_back_to_keyword(self):
        r = skills.interpret_feedback("图好难，焦虑", "这不是JSON")
        assert r["source"] == "keyword"


# --------------------------------------------------------------------------
# Skill 5：proactive_scan
# --------------------------------------------------------------------------

class TestProactiveScan:
    def test_overload_detection(self, rules):
        days = [
            {"date": "2026-09-18", "weekday": "周五", "planned_hours": 4.0},
            {"date": "2026-09-19", "weekday": "周六", "planned_hours": 9.5},
        ]
        r = skills.proactive_scan(days, daily_available_hours=6, rules=rules)
        assert r["cap"] == 7.2
        assert r["overload_count"] == 1
        assert r["daily"][1]["overload"] is True

    def test_peak_shaving_moves_hours(self, rules):
        days = [
            {"date": f"2026-09-{18+i:02d}", "weekday": f"周{i+1}", "planned_hours": h}
            for i, h in enumerate([9.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0])
        ]
        r = skills.proactive_scan(days, daily_available_hours=6, rules=rules)
        assert r["moves"], "超载日的任务应平移到空闲日"
        assert any(m["from"] == "2026-09-18" for m in r["moves"])

    def test_total_overload_triggers_degrade(self, rules):
        days = [
            {"date": f"2026-09-{18+i:02d}", "weekday": f"周{i+1}", "planned_hours": 9.0}
            for i in range(7)
        ]
        r = skills.proactive_scan(days, daily_available_hours=6, rules=rules)
        assert r["degraded"], "削峰后仍超载应降级 low priority 任务"
        assert "降级" in r["conclusion"]

    def test_backlog_note(self, rules):
        days = [{"date": "2026-09-18", "weekday": "周五", "planned_hours": 4.0}]
        r = skills.proactive_scan(days, backlog=12, daily_available_hours=6, rules=rules)
        assert "12 题" in r["backlog_note"]


# --------------------------------------------------------------------------
# Skill 6：reschedule_for_calendar_change
# --------------------------------------------------------------------------

def _schedule(version, entries=None):
    return {"version": version, "entries": entries or []}


class TestReschedule:
    def test_same_version_not_triggered(self, rules):
        s = _schedule(1)
        r = skills.reschedule_for_calendar_change(
            s, s, {"tasks": []}, make_lm({}), rules=rules)
        assert r["triggered"] is False

    def test_new_conflict_moves_task_same_day(self, rules):
        old = _schedule(1, [])
        new = _schedule(2, [{
            "day_of_week": "周三", "time": "14:30-16:10",
            "course": "操作系统实验", "weeks": "6-16"}])
        task = {"task_id": "ds_1", "subject": "数据结构", "content": "链表专项",
                "planned_hours": 1.7, "day_of_week": "周三", "time": "14:30-16:10",
                "weeks": "6-16", "priority": "high"}
        r = skills.reschedule_for_calendar_change(
            old, new, {"tasks": [task]}, make_lm({"数据结构": make_subject()}),
            daily_available_hours=6, rules=rules)
        assert r["triggered"] is True
        assert len(r["affected_tasks"]) == 1
        assert len(r["moves"]) == 1
        assert r["moves"][0]["from_slot"] == "周三 14:30-16:10"
        # 就近平移：仍是周三，新时段不与实验课冲突
        assert r["moves"][0]["to_slot"].startswith("周三")
        assert "14:30-16:10" not in r["moves"][0]["to_slot"]

    def test_schedule_diff_add_cancel_change(self):
        old_entries = [
            {"day_of_week": "周一", "time": "08:00-09:40", "course": "A", "weeks": "1-16"},
            {"day_of_week": "周二", "time": "10:10-11:50", "course": "B", "weeks": "1-8"},
        ]
        new_entries = [
            {"day_of_week": "周一", "time": "08:00-09:40", "course": "A2", "weeks": "1-16"},  # 调课
            {"day_of_week": "周三", "time": "14:30-16:10", "course": "C", "weeks": "6-16"},  # 新增
            # B 被取消
        ]
        changes = skills._diff_schedule(old_entries, new_entries)
        types = {(c["type"], c.get("course")) for c in changes}
        assert ("新增", "C") in types
        assert ("取消", "B") in types
        assert any(c["type"] == "调课" for c in changes)


# --------------------------------------------------------------------------
# Skill 7：decompose_goal
# --------------------------------------------------------------------------

class TestDecomposeGoal:
    def test_kaoyan_math1_408_seven_modules(self, rules):
        profile = {"goal": "2027 年考研（数学一 + 408 计算机专业基础综合）",
                   "target_date": "2027-12-18"}
        # 当前计划只覆盖高数；英语是考研公共课，不算「目标外」
        plan = make_plan([make_task("数学", hours=2.0), make_task("英语", hours=0.8)])
        r = skills.decompose_goal(profile, plan, make_lm({}), rules=rules, today="2026-09-18")
        assert len(r["required_modules"]) == 7
        assert r["covered_modules"] == ["高等数学"]
        assert len(r["missing_modules"]) == 6
        assert r["coverage_rate"] == round(1 / 7, 4)
        assert r["extra_subjects"] == []            # 英语归入公共课，不再是目标外
        assert r["remaining_days"] == 456
        assert r["current_stage"] == "基础阶段"
        assert [s["name"] for s in r["stages"]] == ["基础阶段", "强化阶段", "冲刺阶段"]
        # 公共课：英语已排入计划，政治尚未
        pcs = {c["name"]: c for c in r["public_courses"]}
        assert pcs["英语"]["covered"] is True and pcs["英语"]["sources"] == ["计划任务"]
        assert pcs["政治"]["covered"] is False

    def test_schedule_courses_count_as_coverage(self, rules):
        """课表里的计组课 + 政治课应计入覆盖（用户本学期在上课，不算缺口）。"""
        profile = {"goal": "2027 年考研（数学一 + 408 计算机专业基础综合）",
                   "target_date": "2027-12-18"}
        plan = make_plan([make_task("数学", hours=2.0)])
        schedule = {"version": 1, "entries": [
            {"day_of_week": "周三", "time": "10:10-11:50",
             "course": "计算机组成原理", "weeks": "1-16"},
            {"day_of_week": "周二", "time": "14:30-16:10",
             "course": "习近平新时代中国特色社会主义思想概论", "weeks": "2-17"},
        ]}
        r = skills.decompose_goal(profile, plan, make_lm({}), rules=rules,
                                  today="2026-09-18", schedule=schedule)
        assert "计算机组成原理" in r["covered_modules"]
        assert r["covered_sources"]["计算机组成原理"] == "课表：计算机组成原理"
        assert r["coverage_rate"] == round(2 / 7, 4)
        pcs = {c["name"]: c for c in r["public_courses"]}
        assert pcs["政治"]["covered"] is True          # 政治课在课表上
        assert pcs["政治"]["sources"][0].startswith("课表")
        assert pcs["英语"]["covered"] is False

    def test_math2_only_two_modules(self, rules):
        profile = {"goal": "考研数学二", "target_date": "2027-12-18"}
        r = skills.decompose_goal(profile, make_plan([]), make_lm({}), rules=rules,
                                  today="2026-09-18")
        assert r["required_modules"] == ["高等数学", "线性代数"]
        assert r["coverage_rate"] == 0

    def test_full_coverage(self, rules):
        profile = {"goal": "408", "target_date": "2027-12-18"}
        plan = make_plan([
            make_task("数据结构", hours=1.0),
            make_task("计算机组成原理", hours=1.0),
            {"task_id": "os_1", "subject": "操作系统", "content": "x", "planned_hours": 1.0},
            {"task_id": "net_1", "subject": "计算机网络", "content": "x", "planned_hours": 1.0},
        ])
        r = skills.decompose_goal(profile, plan, make_lm({}), rules=rules, today="2026-09-18")
        assert r["missing_modules"] == []
        assert r["coverage_rate"] == 1.0


# --------------------------------------------------------------------------
# Skill 8：aggregate_resources
# --------------------------------------------------------------------------

class TestAggregateResources:
    def test_catalog_hit(self):
        r = skills.aggregate_resources(["链表"])
        assert r["plan"][0]["topic"] == "链表"
        names = " ".join(x["name"] for x in r["plan"][0]["resources"])
        assert "王道" in names
        types = [x["type"] for x in r["plan"][0]["resources"]]
        assert types == ["视频", "课后题", "错题本"]

    def test_unknown_topic_uses_default(self):
        r = skills.aggregate_resources(["某个没收录的知识点"])
        assert len(r["plan"][0]["resources"]) == 3
        assert r["plan"][0]["resources"][0] == {"type": "视频", "name": "对应科目章节视频"}

    def test_dedup_preserves_order(self):
        r = skills.aggregate_resources(["链表", "指针", "链表"])
        assert [p["topic"] for p in r["plan"]] == ["链表", "指针"]

    def test_empty_topics(self):
        r = skills.aggregate_resources([])
        assert r["plan"] == []
        assert "暂无" in r["summary"]
