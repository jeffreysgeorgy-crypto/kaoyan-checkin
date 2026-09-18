# -*- coding: utf-8 -*-
"""
regenerate_plan.py —— 根据真实课表 + 分配策略 + 排程规则，重新生成 data.js 的
DAILY_PLAN_DATA 时间段（只改 *Time 字段与 studyDuration / schoolNote，保留内容与竞赛块）。

用法：在 D:\\XX 目录下运行  py agent/regenerate_plan.py
    - 直接改写 data.js（改写前自动备份为 data.js.bak）
    - 加 --dry 只打印抽样结果，不写盘
"""
import argparse
import json
import math
import re
from datetime import date

DATA_JS = "data.js"

SEMESTER_START = date(2026, 9, 7)
NATIONAL_HOLIDAY = (date(2026, 10, 1), date(2026, 10, 7))

DAY_START = 7 * 60 + 30   # 07:30
DAY_END = 22 * 60         # 22:00
EN_DASH = "\u2013"        # –
CN_SEMI = "\uFF1B"        # ；

# 真实课表（用户提供，周二下午课；周四保留 p2 数字图像处理实验、周五无 p2）：weekday -> [(start, end, weeks)]
def _weeks(spec):
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out

SCHEDULE = {
    1: [("14:30", "16:10", _weeks("1-16")), ("16:20", "18:00", _weeks("2-17")), ("19:00", "21:00", _weeks("9-16"))],  # 周一
    2: [("14:30", "16:10", _weeks("2-17"))],                                                                          # 周二（下午习概）
    3: [("10:10", "11:50", _weeks("1-16")), ("14:30", "16:10", _weeks("1-16")), ("16:20", "18:00", _weeks("2-16"))],  # 周三
    4: [("08:00", "09:40", _weeks("1-16")), ("10:10", "11:50", _weeks("13-16")), ("14:30", "16:10", _weeks("5-16")), ("16:20", "18:00", _weeks("3,5,7,9,11,13,15-16"))],  # 周四
    5: [("14:30", "16:10", _weeks("8-15"))],                                                                          # 周五
    6: [],                                                                                                            # 周六
    7: [],                                                                                                            # 周日
}

# 每周分配策略（小时），按 weekday(1=周一..7=周日)
def allocation(weekday):
    if weekday == 3:                      # 周三课最多
        return {"math": 2.0, "major": 2.0, "english": 1.0}
    if weekday >= 6:                      # 周末
        return {"math": 3.5, "major": 3.0, "english": 1.0}
    return {"math": 3.0, "major": 2.5, "english": 1.0}   # 周一/二/四/五

# 排程规则：每科偏好窗口（分钟）
PREFERRED = {
    "english": [(7 * 60 + 30, 8 * 60 + 30), (12 * 60 + 30, 13 * 60 + 30)],
    "math":    [(8 * 60 + 30, 11 * 60 + 30), (19 * 60, 21 * 60)],
    "major":   [(14 * 60, 18 * 60), (18 * 60, 22 * 60)],
}

# 课表 schoolNote 用课程名（PERIODS 标准时间；与前端冲突检测一致）
PERIOD_TIME = {1: ("08:00", "09:40"), 2: ("10:10", "11:50"), 3: ("14:30", "16:10"),
               4: ("16:20", "18:00"), 5: ("19:00", "21:00")}
COURSE_NOTE = {
    1: {3: "数据分析与可视化", 4: "单片机及接口技术", 5: "自然语言处理技术"},
    2: {3: "习近平新时代中国特色社会主义思想概论"},
    3: {2: "计算机组成原理", 3: "数字图像处理", 4: "自然语言处理技术"},
    4: {1: "优化方法", 2: "数字图像处理", 3: "实验课", 4: "实验课"},
    5: {3: "单片机及接口技术"},
}


def to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(m):
    m = round(m / 5) * 5
    return f"{m // 60:02d}:{m % 60:02d}"


def merge(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals)
    out = [list(intervals[0])]
    for s, e in intervals[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def parse_time_blocks(tstr):
    """'08:30–10:00；10:15–11:45' 或 '—' -> [(s,e)]（分钟）"""
    if not tstr or tstr in ("—", ""):
        return []
    blocks = []
    for seg in re.split(r"[；;]", tstr):
        m = re.search(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", seg)
        if m:
            blocks.append((to_min(m.group(1)), to_min(m.group(2))))
    return blocks


def total_hours(tstr):
    return sum((e - s) for s, e in parse_time_blocks(tstr)) / 60


def free_intervals(busy):
    """从 busy（已占用区间）算出 07:30–22:00 里的空闲区间。"""
    busy = merge(busy)
    free = []
    cur = DAY_START
    for s, e in busy:
        if s > cur:
            free.append((cur, min(s, DAY_END)))
        cur = max(cur, e)
    if cur < DAY_END:
        free.append((cur, DAY_END))
    return free


def place(free, hours, preferred):
    """在 free 里放 hours 小时，拆 ≤2h 块，块间尽量留 10min 休息。

    排序：① 能整块放下的区间优先（避免把整科拆碎）；② 命中更早的偏好窗口优先；
    ③ 区间越大越优先。放不下时按此顺序贪心填满。
    """
    work = int(round(hours * 60))

    def window_idx(iv):
        s, e = iv
        for i, (ps, pe) in enumerate(preferred):
            if max(0, min(e, pe) - max(s, ps)) > 0:
                return i
        return len(preferred)

    def fits(iv):
        return (iv[1] - iv[0]) >= work

    ordered = sorted(free, key=lambda iv: (0 if fits(iv) else 1, window_idx(iv), -(iv[1] - iv[0])))
    blocks = []
    remaining = work
    for s, e in ordered:
        if remaining <= 0:
            break
        cur = s
        while remaining > 0 and cur < e:
            avail = e - cur
            take = min(avail, remaining, 120)  # 单块 ≤ 2h
            if take <= 0:
                break
            blocks.append((cur, cur + take))
            remaining -= take
            # 剩余量尚可观才留 10min 休息，否则紧接放置，避免产生碎块
            cur = cur + take + (10 if remaining >= 20 else 0)
    blocks.sort()
    return blocks


def fmt_time(blocks):
    if not blocks:
        return "—"
    return CN_SEMI.join(f"{to_hhmm(s)}{EN_DASH}{to_hhmm(e)}" for s, e in blocks)


def is_holiday(d):
    return NATIONAL_HOLIDAY[0] <= d <= NATIONAL_HOLIDAY[1]


def class_blocks(weekday, week, holiday):
    if holiday or weekday > 5:
        return []
    return [(to_min(s), to_min(e)) for s, e, weeks in SCHEDULE.get(weekday, []) if week in weeks]


def build_school_note(weekday, week, holiday):
    if holiday:
        return "国庆假期"
    if weekday > 5:
        return "无固定课"
    notes = []
    for s, e, weeks in SCHEDULE.get(weekday, []):
        if week not in weeks:
            continue
        name = None
        start_min = to_min(s)
        for period, (ps, _) in PERIOD_TIME.items():
            if to_min(ps) == start_min and period in COURSE_NOTE.get(weekday, {}):
                name = COURSE_NOTE[weekday][period]
                break
        name = name or "课程"
        notes.append(f"{s}{EN_DASH}{e} {name}")
    return "；".join(notes) if notes else "无固定课"


def split_major(major):
    """专业课拆两份（0.5h 粒度），ds 取多的一份。"""
    ds_h = math.ceil((major / 2) / 0.5) * 0.5
    ds_h = min(ds_h, major)
    cs_h = round(major - ds_h, 2)
    return ds_h, cs_h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    with open(DATA_JS, encoding="utf-8") as f:
        txt = f.read()

    m = re.search(r"const DAILY_PLAN_DATA = (\[.*?\]);", txt, re.S)
    if not m:
        print("未找到 DAILY_PLAN_DATA")
        return
    data = json.loads(m.group(1))

    changed = 0
    for d in data:
        y, mo, dd = d["date"].split("-")
        day = date(int(y), int(mo), int(dd))
        wd = day.isoweekday()
        wk = d["week"]
        holiday = is_holiday(day)

        alloc = allocation(wd)
        busy = class_blocks(wd, wk, holiday) + parse_time_blocks(d.get("otherTime") or "—")

        # 顺序放置：英语 → 数学 → 专业课(ds) → 专业课(cs)，每步都把已放块加入 busy
        if (d.get("englishContent") or "—") not in ("—", ""):
            blocks = place(free_intervals(busy), alloc["english"], PREFERRED["english"])
            d["englishTime"] = fmt_time(blocks)
            busy += blocks
        else:
            d["englishTime"] = "—"

        math_blocks = place(free_intervals(busy), alloc["math"], PREFERRED["math"])
        d["mathTime"] = fmt_time(math_blocks)
        busy += math_blocks

        has_ds = (d.get("dsContent") or "—") not in ("—", "")
        has_cs = (d.get("csContent") or "—") not in ("—", "")
        if has_ds and has_cs:
            ds_h, cs_h = split_major(alloc["major"])
            ds_blocks = place(free_intervals(busy), ds_h, PREFERRED["major"])
            d["dsTime"] = fmt_time(ds_blocks)
            busy += ds_blocks
            d["csTime"] = fmt_time(place(free_intervals(busy), cs_h, PREFERRED["major"]))
        elif has_ds:
            d["dsTime"] = fmt_time(place(free_intervals(busy), alloc["major"], PREFERRED["major"]))
            d["csTime"] = "—"
        elif has_cs:
            d["dsTime"] = "—"
            d["csTime"] = fmt_time(place(free_intervals(busy), alloc["major"], PREFERRED["major"]))
        else:
            d["dsTime"] = "—"
            d["csTime"] = "—"

        d["schoolNote"] = build_school_note(wd, wk, holiday)
        total = (
            total_hours(d["mathTime"])
            + total_hours(d["dsTime"])
            + total_hours(d["csTime"])
            + total_hours(d["englishTime"])
            + total_hours(d.get("otherTime") or "—")
        )
        d["studyDuration"] = f"{total:.2f}h"
        changed += 1

    if args.dry:
        for d in data[:20]:
            print(f"{d['date']} {d['weekday']} w{d['week']} | 数[{d['mathTime']}] ds[{d['dsTime']}] cs[{d['csTime']}] 英[{d['englishTime']}] | 竞[{d['otherTime']}] | {d['studyDuration']} | {d['schoolNote'][:34]}")
        return

    new_arr = json.dumps(data, ensure_ascii=False, indent=4)
    new_txt = txt[:m.start(1)] + new_arr + txt[m.end(1):]
    with open(DATA_JS + ".bak", "w", encoding="utf-8") as f:
        f.write(txt)
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write(new_txt)
    print(f"✅ 已重排 {changed} 天，备份 → data.js.bak")
    print("   提示：本脚本只改 DAILY_PLAN_DATA 时间段，不动顶部 SCHEDULE。")


if __name__ == "__main__":
    main()
