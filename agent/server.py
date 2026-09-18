# -*- coding: utf-8 -*-
"""
server.py —— FastAPI 后端：打通 HTML 前端与 Python Agent。

启动（任选其一）：
    uvicorn server:app --reload          # 标准方式
    py -m uvicorn server:app --reload    # Windows 下用 py 启动器更稳
    py server.py                         # 直接跑（等价于 127.0.0.1:8000）

接口：
    GET  /                     健康检查
    POST /api/export_memory    接收前端 buildAgentMemory() 生成的 memory.json，保存到本地
    POST /api/diagnose         读 memory.json，跑 skills.diagnose（学习诊断 / 预警科目）
    POST /api/replan           读 memory.json，跑 skills.diagnose + skills.replan，生成 new_plan.json
    POST /api/allocate         读 memory.json，跑 skills.allocate（全局资源再分配）
    POST /api/interpret_feedback  理解反思原文 → 结构化信号，写入 memory.json
    POST /api/proactive_scan   未来 7 天负荷预测 + 削峰填谷 + 降级决策
    POST /api/reschedule       课表变动 → 冲突检测 + 重排受影响任务
    POST /api/parse_schedule   接收课表图片（base64），调硅基流动视觉模型 OCR，返回可编辑条目
    POST /api/upload_mistake   接收错题照片（multipart/form-data），保存到 uploads/ 目录

设计说明（关于 openJiuwen / LLM）：
    /api/replan、/api/reschedule 只调用 skills.py 的确定性规则，不 import openjiuwen、
    不调 LLM——因此任何环境（离线 / 无 API key / openjiuwen 缺失）都能稳定返回 reason + evidence，
    天然满足「LLM 不可用时降级为规则引擎」的要求。若需要 LLM 生成的决策日志，可另行运行
    `py agent.py memory.json --llm`（agent.py 已内置 try/except 降级）。

    例外：/api/parse_schedule 需要「看懂图片」，因此会调硅基流动的视觉大模型（Qwen2.5-VL）做 OCR；
    未配置 VISION_API_KEY 或调用失败时，直接返回错误，前端降级为手动录入，不会影响其它接口。
"""
import json
import os
import time
from datetime import datetime

import requests

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

import skills

HERE = os.path.dirname(os.path.abspath(__file__))          # 相对本文件，不写死绝对路径
MEMORY_PATH = os.path.join(HERE, "memory.json")
NEW_PLAN_PATH = os.path.join(HERE, "new_plan.json")
SCHEDULE_LAST_PATH = os.path.join(HERE, "schedule_last.json")   # 记住上次课表，供下次 diff 做 old_schedule
UPLOAD_DIR = os.path.join(HERE, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 前端静态文件目录：仓库根目录（本地= D:\XX，Render 部署= 仓库根）。后端顺带托管前端，实现同源访问。
FRONTEND_DIR = os.path.abspath(os.path.join(HERE, ".."))
_FRONTEND_FILES = {"index.html", "app.js", "style.css", "data.js", "sw.js", "manifest.json", "icon.svg"}

app = FastAPI(title="学习规划 Agent 后端")

# 开发阶段放开跨域；生产环境把 allow_origins 收敛为具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_memory() -> dict:
    """读取学习记忆（Supabase 优先，未配置则本地 memory.json）；不存在时抛 FileNotFoundError。"""
    if _db_enabled():
        data = _db_get("memory")
        if data is not None:
            return data
        raise FileNotFoundError("memory 记录不存在（Supabase）")
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_json_optional(path: str, default):
    """读取 JSON 文件；文件不存在或损坏时返回 default，不抛异常。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _db_enabled() -> bool:
    """Supabase 是否可用（配置了 URL + KEY 且客户端初始化成功）。"""
    return _supabase is not None


def _db_get(table: str):
    """从 Supabase 表按 user_id 读取 data 列；无记录返回 None。"""
    resp = _supabase.table(table).select("data").eq("user_id", USER_ID).execute()
    rows = resp.data or []
    if rows and isinstance(rows[0].get("data"), dict):
        return rows[0]["data"]
    return None


def _db_set(table: str, obj: dict) -> None:
    """把 data 写入（upsert）Supabase 表。"""
    _supabase.table(table).upsert({
        "user_id": USER_ID,
        "data": obj,
        "updated_at": datetime.now().isoformat(),
    }).execute()


def _load_memory_safe() -> dict:
    """读取学习记忆，容忍缺失：Supabase 优先，否则本地文件，再退 {}。"""
    if _db_enabled():
        return _db_get("memory") or {}
    return _load_json_optional(MEMORY_PATH, {})


def _save_memory(obj: dict) -> None:
    """保存学习记忆：Supabase 优先，未配置则本地 memory.json。"""
    if _db_enabled():
        _db_set("memory", obj)
    else:
        _save_json(MEMORY_PATH, obj)


def _load_schedule_last() -> dict:
    """读取上次课表（供课表重排 diff）：Supabase 优先，否则本地 schedule_last.json。"""
    if _db_enabled():
        return _db_get("schedule_last") or {}
    return _load_json_optional(SCHEDULE_LAST_PATH, {})


def _save_schedule_last(obj: dict) -> None:
    """保存上次课表：Supabase 优先，未配置则本地文件。"""
    if _db_enabled():
        _db_set("schedule_last", obj)
    else:
        _save_json(SCHEDULE_LAST_PATH, obj)


def _err(message: str) -> JSONResponse:
    return JSONResponse(status_code=500, content={"status": "error", "message": message})


def _log(msg: str) -> None:
    """带时间戳的中文业务日志，flush=True 让终端实时显示（答辩时可现场展示 Agent 工作过程）。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------- 课表图片 OCR（硅基流动视觉模型） ----------
try:                                    # 读取 .env（若未装 python-dotenv 则直接读环境变量）
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except Exception:
    pass

# ---------- Supabase（云端存储；未配置则自动降级为本地文件） ----------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "mistakes")
USER_ID = os.environ.get("SUPABASE_USER_ID", "default")

_supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        _log(f"[supabase] 已连接：{SUPABASE_URL}")
    except Exception as exc:
        _log(f"[supabase] 初始化失败，降级为本地存储：{exc}")
        _supabase = None

VISION_PROMPT = (
    "你是课表识别助手。请识别图片中的大学课程表，提取每一门课，只输出严格 JSON，格式：\n"
    '{"entries": [{"day_of_week": "周一", "time": "14:30-16:10", "course": "课程名", '
    '"location": "教室", "weeks": "1-16"}]}\n'
    "规则：\n"
    "1. day_of_week 只能是 周一/周二/周三/周四/周五/周六/周日 之一；\n"
    "2. time 用 24 小时制 HH:MM-HH:MM，只保留数字、冒号、短横线，不要空格；\n"
    "3. weeks 保留原图周次写法（如 1-16、3,5,7、9-16），识别不出就写 1-16；\n"
    "4. location 是教室号，识别不出留空字符串 \"\"；\n"
    "5. 一张图里若有多张课表，全部识别；表格外的水印/标题/备注不要当成课程。\n"
    "只输出 JSON，不要任何解释文字。"
)

# 与前端 data.js 的 PERIODS 保持一致（用于把识别出的时间映射到第几大节）
PERIODS_CFG = [
    (1, "08:00", "09:40"),
    (2, "10:10", "11:50"),
    (3, "14:30", "16:10"),
    (4, "16:20", "18:00"),
    (5, "19:00", "21:00"),
]
VALID_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _to_min(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def _period_for_time(time_str: str):
    """把 "HH:MM-HH:MM" 映射到第几大节；识别不出返回 None。"""
    if not time_str or "-" not in time_str:
        return None
    start = time_str.split("-")[0]
    sm = _to_min(start)
    for period, s, _ in PERIODS_CFG:
        if start == s:
            return period
    for period, s, e in PERIODS_CFG:                     # 起始时间落在某大节区间内 → 归入该大节
        if _to_min(s) <= sm < _to_min(e):
            return period
    if sm >= _to_min("19:00"):
        return 5
    if sm >= _to_min("16:20"):
        return 4
    if sm >= _to_min("14:30"):
        return 3
    if sm >= _to_min("10:10"):
        return 2
    return 1 if sm >= 0 else None


def _load_vision_env() -> dict:
    return {
        "api_base": os.getenv("VISION_API_BASE", "https://api.siliconflow.cn/v1"),
        "api_key": os.getenv("VISION_API_KEY", ""),
        "model": os.getenv("VISION_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct"),
    }


def _extract_json(text: str):
    """从模型输出里抠出 JSON 对象（容忍 markdown 围栏 / 前后废话）。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        text = text[s:e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_schedule_images(images):
    """调用硅基流动视觉模型识别课表，返回 (模型原文, 错误信息)。"""
    env = _load_vision_env()
    if not env["api_key"] or env["api_key"].startswith("sk-xxxx"):
        return None, "未配置硅基流动视觉模型 API key（.env 里 VISION_API_KEY 为空）"
    content = [{"type": "text", "text": VISION_PROMPT}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": img}})
    url = env["api_base"].rstrip("/") + "/chat/completions"
    resp = requests.post(
        url,
        headers={"Authorization": "Bearer " + env["api_key"], "Content-Type": "application/json"},
        json={"model": env["model"],
              "messages": [{"role": "user", "content": content}],
              "temperature": 0},
        timeout=90,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    content_out = msg.get("content")
    if isinstance(content_out, list):                      # 某些模型返回多段 content
        content_out = "".join(p.get("text", "") for p in content_out if isinstance(p, dict))
    return (content_out or "").strip(), None


@app.get("/")
def frontend_index():
    """根路径返回前端首页（云端同源部署：后端顺带托管前端，免配后端地址）。"""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/health")
def health():
    """健康检查（原 GET / 移到此处，便于命令行验证）。"""
    return {"message": "Agent Server is running"}


@app.post("/api/export_memory")
def export_memory(payload: dict):
    """接收前端传来的 memory.json（dict），保存到本地。

    前端在 buildAgentMemory() 里已把 localStorage 的打卡/错题/专注/反思
    汇总成标准 memory.json 格式；这里只负责校验并落盘，避免在 Python 里
    重复实现一遍映射逻辑。
    """
    try:
        if not isinstance(payload, dict) or "learning_memory" not in payload:
            _log("[export_memory] 错误：缺少 learning_memory 字段")
            return _err("数据格式不正确：缺少 learning_memory 字段")
        subjects = payload.get("learning_memory", {}).get("subjects", {})
        subject_count = len(subjects)
        task_count = len(payload.get("current_plan", {}).get("tasks", []))
        # 反思存在每个科目的 recent_reflections 里（buildAgentMemory 拷贝的是同一份），取最大值即真实反思数
        reflection_count = max((len(s.get("recent_reflections", [])) for s in subjects.values()), default=0)
        _log(f"[export_memory] 收到数据：科目数={subject_count}，任务数={task_count}，反思数={reflection_count}")
        _save_memory(payload)
        _log("[export_memory] 已保存学习记忆（Supabase / memory.json）")
        return {"status": "ok", "file": "memory.json"}
    except Exception as exc:
        _log(f"[export_memory] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/replan")
def replan():
    """读 memory.json → 跑 diagnose + replan → 生成 new_plan.json → 返回调整明细。

    返回字段：
        new_plan       重规划后的 current_plan（含 tasks，供前端写回）
        replanning_log 追加本次调整后的完整历史（前端「调整日志」直接展示）
        diagnosis      诊断结果（预警科目 / 主因）
        adjustments    本次调整明细（每条含 reason + evidence）
        reason         本次调整理由列表（便捷字段）
        evidence       本次调整依据列表（便捷字段）
    """
    try:
        memory = _load_memory()
        learning_memory = memory.get("learning_memory", {})
        rules = memory.get("rules") or None
        current_plan = memory.get("current_plan", {})
        replan_count = len(memory.get("replanning_log", []))

        subject_count = len(learning_memory.get("subjects", {}))
        task_count = len(current_plan.get("tasks", []))
        _log(f"[replan] 读取 memory.json：科目数={subject_count}，任务数={task_count}")

        diagnosis = skills.diagnose(learning_memory, rules)
        alert = diagnosis.get("alert_subjects", [])
        sev = "、".join(a.get("severity", "") for a in alert) if alert else "无预警"
        _log(f"[replan] diagnose 结果：预警科目={len(alert)}，严重度={sev}")

        new_plan, adjustments = skills.replan(
            current_plan, diagnosis, learning_memory, replan_count, rules=rules
        )
        catchup_count = sum(1 for a in adjustments if a.get("level") == "catch_up")
        _log(f"[replan] replan 结果：调整任务数={len(adjustments)}，新增补欠={catchup_count}")

        trigger = "无预警科目"
        if diagnosis.get("alert_subjects"):
            a = diagnosis["alert_subjects"][0]
            trigger = f"连续 {a['consecutive_failures']} 天未完成"
        replanning_log = list(memory.get("replanning_log", [])) + [{
            "date": current_plan.get("date") or current_plan.get("generated_at")
                    or datetime.now().date().isoformat(),
            "trigger": trigger,
            "adjustments": adjustments,
        }]

        # 落盘 new_plan.json（与 agent.py CLI 输出结构保持一致，便于审计/复用）
        _save_json(NEW_PLAN_PATH, {
            "user_profile": memory.get("user_profile", {}),
            "current_plan": new_plan,
            "replanning_log": replanning_log,
            "diagnosis": diagnosis,
        })
        _log("[replan] 已生成 new_plan.json，返回给前端")

        return {
            "status": "ok",
            "new_plan": new_plan,
            "replanning_log": replanning_log,
            "diagnosis": diagnosis,
            "adjustments": adjustments,
            "reason": [a.get("reason", "") for a in adjustments],
            "evidence": [a.get("evidence", {}) for a in adjustments],
        }
    except FileNotFoundError:
        _log("[replan] 错误：memory.json 不存在，请先点「导出并发送 Agent」")
        return _err("memory.json 不存在：请先在「我的」页点击「导出并发送 Agent」")
    except Exception as exc:
        _log(f"[replan] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/reschedule")
def reschedule(payload: dict):
    """读入新课表 + 当前计划 → 冲突检测 + 重排 → 返回新计划与调整说明。

    输入：{"schedule": {...}, "current_plan": {...}}
    说明：reschedule_for_calendar_change 需要 old_schedule + learning_memory，
          这里由后端自行补齐——old_schedule 取自上次持久化的 schedule_last.json，
          learning_memory 取自 memory.json（未导出时用空结构，保证不崩）。

    返回：
        new_plan     重排后的计划（{tasks: [...]}，task 带新的 time/day_of_week）
        adjustments  每条含 before/after/reason/evidence
        reason       调整理由列表（便捷字段）
        evidence     调整依据列表（便捷字段）
        explanation  Skill 生成的可解释说明（中文，逐条）
    """
    try:
        if not isinstance(payload, dict) or "schedule" not in payload:
            _log("[reschedule] 错误：缺少 schedule 字段")
            return _err("数据格式不正确：缺少 schedule 字段")
        schedule = payload.get("schedule") or {}
        current_plan = payload.get("current_plan") or {}

        # learning_memory 从 memory.json（或 Supabase）读（未导出则空，保证规则仍可跑）
        memory = _load_memory_safe()
        learning_memory = memory.get("learning_memory", {})
        rules = memory.get("rules") or None
        daily_hours = float(memory.get("user_profile", {}).get("daily_available_hours") or 6.0)

        # old_schedule 从上次落盘的副本读（首次为空 → 视为「从无到有」，version 必变）
        old_schedule = _load_schedule_last()
        old_ver = old_schedule.get("version") if isinstance(old_schedule, dict) else None
        new_ver = schedule.get("version") if isinstance(schedule, dict) else None
        _log(f"[reschedule] 收到课表 version {old_ver} → {new_ver}，任务数={len(current_plan.get('tasks', []))}")

        result = skills.reschedule_for_calendar_change(
            old_schedule, schedule, current_plan, learning_memory,
            daily_available_hours=daily_hours, rules=rules,
        )

        # 记住本次课表，作为下次的 old_schedule（下次变动才能正确 diff）
        _save_schedule_last(schedule)

        moves = result.get("moves", [])
        adjustments = [{
            "task_id": m.get("task_id"),
            "subject": m.get("subject"),
            "before": m.get("from_slot"),
            "after": m.get("to_slot"),
            "reason": m.get("reason"),
            "evidence": {"source": "课表冲突检测", "conflict": m.get("reason")},
        } for m in moves]

        _log(f"[reschedule] 重排完成：触发={result.get('triggered')}，"
             f"受影响任务={len(result.get('affected_tasks', []))}，"
             f"平移任务={len(moves)}，触发全局再分配={result.get('allocate_triggered')}")

        return {
            "status": "ok",
            "triggered": result.get("triggered"),
            "new_plan": result.get("rearranged_plan", {"tasks": []}),
            "adjustments": adjustments,
            "reason": [m.get("reason", "") for m in moves],
            "evidence": [{"conflict": m.get("reason", "")} for m in moves],
            "explanation": result.get("explanation", []),
        }
    except Exception as exc:
        _log(f"[reschedule] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/diagnose")
def diagnose_endpoint():
    """读 memory.json → 跑 skills.diagnose，返回学习诊断（预警科目 / 严重度 / 主因）。"""
    try:
        memory = _load_memory()
        learning_memory = memory.get("learning_memory", {})
        rules = memory.get("rules") or None
        _log(f"[diagnose] 读取 memory.json：科目数={len(learning_memory.get('subjects', {}))}")
        diagnosis = skills.diagnose(learning_memory, rules)
        _log(f"[diagnose] 结果：预警科目={len(diagnosis.get('alert_subjects', []))}")
        return {"status": "ok", "diagnosis": diagnosis}
    except FileNotFoundError:
        _log("[diagnose] 错误：memory.json 不存在")
        return _err("memory.json 不存在：请先在「我的」页点击「导出并发送 Agent」")
    except Exception as exc:
        _log(f"[diagnose] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/allocate")
def allocate_endpoint(payload: dict):
    """读 memory.json → 跑 skills.allocate（全局资源再分配），返回各科分配时长 + 理由。

    输入：{"daily_available_hours": 6}（可选，缺省用 memory.json 里的画像值，再缺省 6）
    """
    try:
        payload = payload if isinstance(payload, dict) else {}
        memory = _load_memory_safe()
        learning_memory = memory.get("learning_memory", {})
        current_plan = memory.get("current_plan", {})
        rules = memory.get("rules") or None
        daily_hours = float(payload.get("daily_available_hours")
                            or memory.get("user_profile", {}).get("daily_available_hours") or 6.0)
        _log(f"[allocate] 读取 memory.json：科目数={len(learning_memory.get('subjects', {}))}，每日可用 {daily_hours}h")
        result = skills.allocate(learning_memory, current_plan, daily_hours, rules=rules)
        _log(f"[allocate] 结果：分配 {len(result.get('allocations', []))} 科，触发={result.get('triggered')}")
        return {
            "status": "ok",
            "triggered": result.get("triggered"),
            "trigger_reason": result.get("trigger_reason"),
            "total_hours": result.get("total_hours"),
            "min_floor": result.get("min_floor"),
            "formula": result.get("formula"),
            "allocations": result.get("allocations", []),
            "reason": [a.get("reason", "") for a in result.get("allocations", [])],
            "evidence": result.get("evidence", []),
        }
    except Exception as exc:
        _log(f"[allocate] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/interpret_feedback")
def interpret_feedback_endpoint(payload: dict):
    """理解一段反思原文 → 结构化信号 {归因/弱知识点/情绪/建议}，并写入 memory.json 的 feedback_signals。

    输入：{"reflection": "单链表插入删除还是卡，感觉是方法不对。"}
    """
    try:
        reflection = (payload.get("reflection") or "").strip() if isinstance(payload, dict) else ""
        if not reflection:
            return _err("缺少 reflection 字段")
        _log(f"[interpret_feedback] 收到反思：{reflection[:30]}{'…' if len(reflection) > 30 else ''}")
        result = skills.interpret_feedback(reflection)

        # 写入学习记忆（仅当记忆已存在，避免误建空记录覆盖真实记忆）
        try:
            memory = _load_memory()
            memory.setdefault("feedback_signals", []).append({
                "date": datetime.now().date().isoformat(), **result,
            })
            _save_memory(memory)
            _log("[interpret_feedback] 信号已写入 feedback_signals（Supabase / memory.json）")
        except FileNotFoundError:
            _log("[interpret_feedback] 学习记忆不存在，信号未落盘（仅返回给前端）")

        _log(f"[interpret_feedback] 结果：归因={result.get('attribution')}，"
             f"弱知识点={result.get('weak_topics')}，情绪={result.get('mood')}")
        return {
            "status": "ok",
            "attribution": result.get("attribution"),
            "weak_topics": result.get("weak_topics"),
            "mood": result.get("mood"),
            "suggestion": result.get("suggestion"),
            "source": result.get("source"),
            "evidence": result.get("evidence", []),
        }
    except Exception as exc:
        _log(f"[interpret_feedback] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/proactive_scan")
def proactive_scan_endpoint(payload: dict):
    """未来 7 天负荷预测 + 削峰填谷 + 降级决策。

    输入：{"days": [{"date","weekday","planned_hours"}, ...], "backlog": 0}
         days 由前端计算（前端最清楚未来 7 天每天的计划负荷），后端只转发给 skills.proactive_scan。
    """
    try:
        if not isinstance(payload, dict):
            return _err("数据格式不正确")
        days = payload.get("days") or []
        backlog = int(payload.get("backlog") or 0)
        if not days:
            return _err("缺少 days（未来 7 天负荷列表）")
        memory = _load_memory_safe()
        daily_hours = float(memory.get("user_profile", {}).get("daily_available_hours") or 6.0)
        rules = memory.get("rules") or None
        _log(f"[proactive_scan] 扫描未来 {len(days)} 天，backlog={backlog}，每日可用 {daily_hours}h")
        result = skills.proactive_scan(days, backlog=backlog, daily_available_hours=daily_hours, rules=rules)
        overload_days = [d for d in result["daily"] if d["overload"]]
        _log(f"[proactive_scan] 结果：超载 {result.get('overload_count')} 天，"
             f"削峰 {len(result.get('moves', []))} 次，降级 {len(result.get('degraded', []))} 项")
        return {
            "status": "ok",
            "cap": result.get("cap"),
            "overload_days": overload_days,
            "daily": result.get("daily"),
            "peak_shaving": result.get("moves"),
            "downgrade": result.get("degraded"),
            "conclusion": result.get("conclusion"),
            "backlog_note": result.get("backlog_note"),
            "evidence": result.get("evidence", []),
        }
    except Exception as exc:
        _log(f"[proactive_scan] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/upload_mistake")
async def upload_mistake(file: UploadFile = File(...), tag: str = Form(None)):
    """接收错题本照片（multipart/form-data）。

    方案 A：仅改后端接口。Supabase 已配置时上传到 Storage 的 mistakes bucket 并返回公开 URL；
    未配置时降级保存到本地 uploads/ 目录。前端错题本仍以本地 base64 为主，本接口保持独立。

    参数：
        file  图片文件（UploadFile）
        tag   错题标签（Form，可选，缺省为「未分类」）
    返回：
        Supabase：{"status": "ok", "filename": ..., "tag": ..., "url": 公开 URL}
        本地：    {"status": "ok", "filename": ..., "tag": ...}
    """
    try:
        _log(f"[upload_mistake] 收到图片：filename={file.filename or '无文件名'}，tag={tag or '未分类'}")
        contents = await file.read()                                  # 1. 读取文件内容
        if not tag:                                                   # 2. 标签缺省
            tag = "未分类"
        safe_name = os.path.basename(file.filename or "photo.jpg")    # 去掉可能的路径分隔符
        dest_name = f"{int(time.time())}_{safe_name}"                 # 3+4. 时间戳防重名

        if _db_enabled():                                             # 5. Supabase 已配置 → 传 Storage
            content_type = file.content_type or "image/jpeg"
            bucket = _supabase.storage.from_(SUPABASE_BUCKET)
            bucket.upload(path=dest_name, file=contents, file_options={"content-type": content_type})
            public_url = bucket.get_public_url(dest_name)
            _log(f"[upload_mistake] 已上传到 Supabase Storage：{public_url}")
            return {"status": "ok", "filename": dest_name, "tag": tag, "url": public_url}

        dest_path = os.path.join(UPLOAD_DIR, dest_name)               # 6. 降级：存本地 uploads/
        os.makedirs(UPLOAD_DIR, exist_ok=True)                        # 目录不存在则自动创建
        with open(dest_path, "wb") as f:
            f.write(contents)
        _log(f"[upload_mistake] 已保存到 uploads/{dest_name}")
        return {"status": "ok", "filename": dest_name, "tag": tag}
    except Exception as exc:
        _log(f"[upload_mistake] 错误：{exc}")
        return _err(str(exc))


@app.post("/api/parse_schedule")
def parse_schedule(payload: dict):
    """接收课表图片（base64），调硅基流动视觉模型 OCR，返回可编辑的课表条目。

    输入：{"images": ["data:image/jpeg;base64,...", ...]}
    返回：
        status   ok/error
        entries  [{day_of_week, period, time, course, location, weeks}, ...]
        raw      模型原始输出（答辩可展示识别过程）
        失败时 status=error + message，前端据此降级为手动录入。
    """
    try:
        if not isinstance(payload, dict) or "images" not in payload:
            _log("[parse_schedule] 错误：缺少 images 字段")
            return _err("数据格式不正确：缺少 images 字段")
        images = [i for i in (payload.get("images") or []) if isinstance(i, str) and i.strip()]
        if not images:
            return _err("没有收到有效的课表图片")
        _log(f"[parse_schedule] 收到 {len(images)} 张课表图片，开始识别…")

        raw, err = _parse_schedule_images(images)
        if err:
            _log(f"[parse_schedule] 识别失败：{err}")
            return _err(err)
        data = _extract_json(raw)
        if not data or not isinstance(data.get("entries"), list):
            _log("[parse_schedule] 模型输出无法解析为 entries JSON")
            return _err("识别结果解析失败，请改用手动录入")

        entries = []
        for e in data["entries"]:
            if not isinstance(e, dict):
                continue
            course = str(e.get("course", "")).strip()
            if not course:
                continue
            day = str(e.get("day_of_week", "")).strip()
            if day not in VALID_WEEKDAYS:
                day = ""
            time_str = str(e.get("time", "")).strip().replace(" ", "").replace("–", "-").replace("—", "-")
            entries.append({
                "day_of_week": day,
                "period": _period_for_time(time_str),
                "time": time_str,
                "course": course,
                "location": str(e.get("location", "")).strip(),
                "weeks": str(e.get("weeks", "")).strip() or "1-16",
            })
        _log(f"[parse_schedule] 识别完成：提取 {len(entries)} 门课")
        return {"status": "ok", "entries": entries, "raw": raw}
    except Exception as exc:
        _log(f"[parse_schedule] 错误：{exc}")
        return _err(str(exc))


@app.get("/{filename}")
def frontend_static(filename: str):
    """按白名单返回前端静态资源；白名单外的路径返回 404，避免暴露 agent/ 后端源码。"""
    if filename in _FRONTEND_FILES:
        return FileResponse(os.path.join(FRONTEND_DIR, filename))
    return JSONResponse(status_code=404, content={"status": "error", "message": "not found"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000)
