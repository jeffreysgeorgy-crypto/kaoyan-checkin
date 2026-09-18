# -*- coding: utf-8 -*-
"""
gen_solution_doc.py —— 根据《解决方案》框架生成 Word 版（.docx）。

用法：  py gen_solution_doc.py
输出：  解决方案_基于openJiuwen的个人学习规划Agent.docx
"""
import os
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "解决方案_基于openJiuwen的个人学习规划Agent.docx")

BODY_FONT = "宋体"
HEAD_FONT = "黑体"


def _set_run(run, font=BODY_FONT, bold=False, size=12, color=None):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), "Times New Roman")
    rFonts.set(qn("w:hAnsi"), "Times New Roman")
    rFonts.set(qn("w:eastAsia"), font)


def heading(doc, text, level=1):
    sizes = {0: 22, 1: 16, 2: 14, 3: 12, 4: 11}
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14 if level <= 1 else 10)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(text)
    _set_run(run, font=HEAD_FONT, bold=True, size=sizes.get(level, 12))
    return p


def para(doc, text, indent=True, bold=False, size=12, align=None, space_after=6):
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    p.paragraph_format.space_after = Pt(space_after)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    _set_run(run, font=BODY_FONT, bold=bold, size=size)
    return p


def bullet(doc, text, size=12):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    run = p.add_run(text)
    _set_run(run, font=BODY_FONT, size=size)
    return p


def kv(doc, label, value, bold_value=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    r1 = p.add_run(label)
    _set_run(r1, font=BODY_FONT, bold=True, size=12)
    r2 = p.add_run(value)
    _set_run(r2, font=BODY_FONT, bold=bold_value, size=12)
    return p


def table(doc, headers, rows, widths=None, font_size=10.5):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(str(h))
        _set_run(run, font=HEAD_FONT, bold=True, size=font_size)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cell = cells[i]
            cell.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(str(val))
            _set_run(run, font=BODY_FONT, size=font_size)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = w
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def caption(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(text)
    _set_run(run, font=HEAD_FONT, bold=False, size=10.5)
    return p


def page_break(doc):
    doc.add_page_break()


doc = Document()

# 默认 Normal 样式
normal = doc.styles["Normal"]
normal.font.name = "Times New Roman"
normal.font.size = Pt(12)
normal._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

# ============================================================
# 封面
# ============================================================
for _ in range(4):
    doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("基于 openJiuwen 的个人学习规划 Agent")
_set_run(r, font=HEAD_FONT, bold=True, size=26)
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("—— 面向考研备考场景的动态学习路径规划解决方案")
_set_run(r, font=HEAD_FONT, bold=True, size=18)
doc.add_paragraph()
p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("封  面")
_set_run(r, font=HEAD_FONT, bold=True, size=18)
for _ in range(4):
    doc.add_paragraph()

kv(doc, "项目名称：", "P2025-XXX — 基于 openJiuwen 的个人学习规划 Agent")
kv(doc, "团队名称：", "【待填写：你的团队名】")
kv(doc, "所选命题编号与名称：", "【待填写：大赛组委会公布的命题编号及完整名称】")
kv(doc, "所属学校：", "【待填写：学校全称】")
kv(doc, "团队成员：", "【待填写：姓名、院校、专业、年级、角色与分工】")
kv(doc, "指导教师：", "【待填写：姓名、职称、研究方向】")
kv(doc, "申报日期：", "2026 年 X 月 X 日")

page_break(doc)

# ============================================================
# 图目录
# ============================================================
heading(doc, "图目录", 1)
for line in ["图 2-1  学习规划痛点分析鱼骨图",
             "图 3-1  系统总体架构图",
             "图 3-2  六个 Skill 的协作关系图",
             "图 3-3  动态重规划决策流程图",
             "图 5-1  项目实施里程碑甘特图"]:
    para(doc, line, indent=False)
page_break(doc)

# ============================================================
# 表目录
# ============================================================
heading(doc, "表目录", 1)
for line in ["表 2-1  现有学习规划工具对比表",
             "表 3-1  六个 Skill 的输入输出与调用条件",
             "表 3-2  赛题 6 个答题点覆盖对照表",
             "表 4-1  团队分工与能力匹配表",
             "表 5-1  项目实施进度安排表"]:
    para(doc, line, indent=False)
page_break(doc)

# ============================================================
# 佐证材料目录
# ============================================================
heading(doc, "佐证材料目录", 1)
for line in ["附件 1：demo.py 完整运行输出（PDF 截图）",
             "附件 2：memory.json 数据结构说明",
             "附件 3：代码仓库链接（GitHub/Gitee）",
             "附件 4：demo 演示录屏（5 分钟）",
             "附件 5：6 个答题点覆盖对照表",
             "附件 6：HTML 打卡系统演示截图"]:
    para(doc, line, indent=False)
page_break(doc)

# ============================================================
# 第一章 执行摘要
# ============================================================
heading(doc, "第一章  执行摘要", 1)
para(doc, "（全文不超过 2000 字）", indent=False, size=10.5)

heading(doc, "1.1  命题理解与解题思路", 2)
heading(doc, "1.1.1  所选命题概述", 3)
para(doc, "本方案针对 openJiuwen 命题「基于 openJiuwen 的个人学习规划 Agent」进行设计。命题核心诉求是：构建一个能够记住用户学习状态、动态规划学习路径、根据反馈调整计划的个人学习规划 Agent，解决传统静态课表「目标不清、计划难坚持、资源分散、进度无法动态调整」的痛点。")

heading(doc, "1.1.2  命题理解深度", 3)
para(doc, "我们理解，这道命题的真正难点不在于「生成一份学习计划」，而在于：")
bullet(doc, "记住状态：用户不是每天都状态一致，系统必须记住历史行为、错题、反思。")
bullet(doc, "动态调整：当用户连续失败、临时有事、课表变动时，计划要能自动重排。")
bullet(doc, "可解释性：每一步调整都要有依据，否则用户不会信任 Agent。")
bullet(doc, "个性化：同样的目标，不同用户的掌握度、可用时间、学习偏好不同，计划不能一刀切。")

heading(doc, "1.1.3  解题总体思路", 3)
para(doc, "我们采用「确定性规则 + LLM 理解 + openJiuwen 调度」三层架构：")
bullet(doc, "确定性规则层（skills.py）：用 6 个可解释的 Skill 完成诊断、重规划、资源分配、冲突处理。")
bullet(doc, "LLM 理解层：用大语言模型理解用户的自然语言反思，把碎碎念转化为结构化信号。")
bullet(doc, "openJiuwen 调度层：用 ReActAgent 编排 6 个 Skill，决定何时调用、如何组合。")
para(doc, "核心哲学是：规则保证可解释与稳定，LLM 保证能听懂人话，openJiuwen 保证两者协同工作。")

heading(doc, "1.2  解决方案概述", 2)
heading(doc, "1.2.1  核心方案内容", 3)
para(doc, "本方案实现了 6 个 Skill：")
bullet(doc, "diagnose（学习诊断）：检测科目是否连续失败、完成率是否过低、拖延是否超限。")
bullet(doc, "replan（动态重规划）：根据诊断结果调整任务时长、拆分任务、更换学习路径。")
bullet(doc, "allocate（资源再分配）：当多科冲突时，按权重重新分配每日可用时间。")
bullet(doc, "interpret_feedback（反馈理解）：把用户反思原文抽取为归因、弱知识点、情绪、建议。")
bullet(doc, "proactive_scan（事前冲突处理）：预测未来 7 天负荷，提前削峰填谷。")
bullet(doc, "reschedule_for_calendar_change（课表变动重排）：检测课表变化，自动重排受影响任务。")

heading(doc, "1.2.2  方案先进性", 3)
bullet(doc, "可解释：每一步调整都带 reason 和 evidence，引用具体历史记录。")
bullet(doc, "容错：LLM 不可用时自动降级为关键词匹配，保证核心流程不中断。")
bullet(doc, "数据自洽：所有引用的反思、打卡记录都可追溯到 memory.json。")
bullet(doc, "动态调整：不是简单的「if-else 规则」，而是多级兜底：减时长 → 换方式 → 换路径。")

heading(doc, "1.3  匹配度与可行性", 2)
heading(doc, "1.3.1  方案与命题匹配度", 3)
table(doc,
      ["赛题要求", "本方案对应"],
      [["用户画像与学习记忆", "memory.json 存储画像、打卡记录、掌握度、反思"],
       ["至少 2 个 Skill", "实现 6 个 Skill"],
       ["动态调整案例", "demo 中连续 3 天未完成，触发诊断 + 重规划"],
       ["计划生成前后对比", "demo 打印 [调整前] vs [调整后]，附理由"],
       ["openJiuwen 作用", "ReActAgent 调度 6 个 Skill，管理 ReAct 循环"],
       ["个性化与可解释性", "所有调整带 reason/evidence，引用反思原文"]])

heading(doc, "1.3.2  方案可行性", 3)
bullet(doc, "技术可行性：已在 CLI 环境完整跑通，demo.py 可重复运行。")
bullet(doc, "经济可行性：无需额外服务器，本地 Python 环境即可运行。")
bullet(doc, "时间可行性：已完整实现核心逻辑，答辩前只需打磨话术与文档。")

heading(doc, "1.4  团队简介", 2)
para(doc, "本团队由 3 名同校同学组成，均来自计算机 / 人工智能相关专业，形成「技术实现 + 教育场景 + 表达展示」的能力互补：")
bullet(doc, "负责人【姓名】（大三，人工智能方向）：负责整体架构设计与核心代码开发（memory.json / skills.py / agent.py / demo.py），并承担现场答辩。")
bullet(doc, "技术成员【姓名】（大三，软件工程方向）：负责 HTML 打卡系统前端开发与数据采集对接。")
bullet(doc, "教育/文档成员【姓名】（大三）：本人即 2027 考研备考者，负责教育场景梳理、需求定义与文档撰写，能站在真实用户视角打磨产品。")

heading(doc, "1.5  预期成效", 2)
bullet(doc, "教育成效：帮助本科生/考研党把「静态课表」升级为「动态学习路径」。")
bullet(doc, "技术成效：验证 openJiuwen 在长周期学习规划场景的可行性。")
bullet(doc, "社会成效：降低学习计划制定门槛，让更多学生能坚持长期目标。")

page_break(doc)

# ============================================================
# 第二章 命题分析与产业认知
# ============================================================
heading(doc, "第二章  命题分析与产业认知", 1)

heading(doc, "2.1  产业认知", 2)
heading(doc, "2.1.1  产业规模与增长速度", 3)
para(doc, "中国在线教育市场保持稳步扩容，AI 正从「辅助工具」升级为「教学全链路赋能者」，是当前增长最快的细分方向。据公开研究报告：")
bullet(doc, "整体规模：艾瑞咨询口径下，2025 年中国在线教育市场规模突破 6800 亿元，同比增长 17.3%，用户规模突破 4.5 亿；网经社口径下 2025 年数字教育市场规模约 5190 亿元（两机构统计口径不同，数值存在差异）。")
bullet(doc, "AI+教育：艾瑞咨询测算 2025 年 GenAI+教育产品服务总规模约 3442 亿元，预计 2028 年达 8910 亿元，年复合增长率约 37%；AI 智能教育赛道增速超 30%，成为行业新的增长极。")
bullet(doc, "渗透率：超过 60% 的头部平台已部署作文批改、思维链推导、虚拟教师对话等生成式 AI 功能，AI 自适应学习系统在头部平台渗透率达 34%。")
para(doc, "（数据来源：艾瑞咨询、网经社、中研普华等公开报告，统计口径与发布时间不同，引用时以原始报告为准。）")

heading(doc, "2.1.2  竞争格局", 3)
para(doc, "现有学习工具可分为三类，与本方案对比如下：")
caption(doc, "表 2-1  现有学习规划工具对比表")
table(doc,
      ["工具类型", "代表产品", "能记录", "能动态调整", "能记忆长期状态"],
      [["传统课表工具", "课程格子等", "✓", "✗", "✗"],
       ["打卡工具", "番茄 ToDo 等", "✓（专注时长）", "✗", "✗"],
       ["AI 对话工具", "豆包 / ChatGPT 等", "✓（对话）", "✗（不自动改计划）", "✗"],
       ["本方案", "学习规划 Agent", "✓", "✓", "✓"]])

heading(doc, "2.1.3  产业趋势与政策", 3)
bullet(doc, "趋势：从「工具型」向「Agent 型」演进。")
bullet(doc, "政策：《教育信息化 2.0 行动计划》鼓励 AI 与教育深度融合。")

heading(doc, "2.1.4  产业定位", 3)
para(doc, "本方案定位为「面向长周期学习目标的动态规划 Agent」，处于「学习工具」与「AI Agent」交叉地带。")

heading(doc, "2.2  命题企业分析", 2)
para(doc, "openJiuwen 是华为支持的开源 AI 智能体（Agent）平台，由华为 2012 实验室、华为云、计算、终端等团队联合打造，提供企业级 AI Agent 的开发、运行与优化能力，采用 Studio / Core / Ops 分层架构，代码以 Apache 2.0 / MIT 等开源协议运营，并提供 GitHub、AtomGit 等代码仓。")
para(doc, "在算力侧，openJiuwen 与昇腾深度协同，推出「算力亲和」全链路优化技术，通过 Agent Hint 状态契约实现对 KV Cache 的驱逐、卸载、预取等主动调度，实测 Agent 推理首 Token 时延降低 57% 以上、推理存储占用峰值下降 25%；平台还孵化了 JiuwenSwarm（蜂群多智能体框架）、SwarmFlow（多智能体工作流编排）、WorkSwarm（蜂群办公智能体）等组件。")
para(doc, "命题意图可理解为：以 openJiuwen 为底座，验证「长周期、有记忆、可解释」的个人 Agent 能否在真实学习场景中落地。本方案正是基于 openJiuwen 0.1.18 的 AgentCard + ReActAgentConfig + ReActAgent 新 API 与 @tool 工具装饰器实现，将 6 个 Skill 包装为工具，由 ReActAgent 完成「思考 → 调用 Skill → 观察 → 输出决策日志」的编排闭环。")

heading(doc, "2.3  命题需求深度剖析", 2)
heading(doc, "2.3.1  命题表面需求", 3)
para(doc, "赛题明确要求：")
bullet(doc, "用户画像与学习记忆")
bullet(doc, "至少 2 个 Skill")
bullet(doc, "动态调整案例")
bullet(doc, "计划生成前后对比")
bullet(doc, "openJiuwen 作用")
bullet(doc, "个性化与可解释性")

heading(doc, "2.3.2  命题深层痛点", 3)
para(doc, "命题背后的真实痛点不是「生成计划」，而是：")
bullet(doc, "用户记不住自己：传统工具没有记忆。")
bullet(doc, "系统听不懂反馈：用户说「太难了」，系统不知道怎么办。")
bullet(doc, "计划不会适应变化：课表变了、临时有事，计划就崩了。")
bullet(doc, "调整不可解释：用户不知道「为什么这么改」。")

heading(doc, "2.3.3  需求优先级排序", 3)
table(doc,
      ["优先级", "需求", "理由"],
      [["P0", "学习记忆", "没有记忆就没有个性化"],
       ["P0", "动态调整", "命题核心"],
       ["P0", "可解释性", "用户信任的前提"],
       ["P1", "LLM 理解反馈", "提升体验的关键"],
       ["P1", "openJiuwen 调度", "框架要求"]])

heading(doc, "2.4  资源需求分析", 2)
heading(doc, "2.4.1  解题所需资源清单", 3)
bullet(doc, "技术资源：Python 环境、openJiuwen 框架、LLM API（DeepSeek 或同类）")
bullet(doc, "数据资源：用户打卡记录（demo 中为模拟数据）")
bullet(doc, "人力资源：一人或小队")

heading(doc, "2.4.2  资源获取途径", 3)
bullet(doc, "openJiuwen：大赛官方提供")
bullet(doc, "LLM API：DeepSeek 开源 API 或同类")
bullet(doc, "数据：由 HTML 打卡系统真实采集")

page_break(doc)

# ============================================================
# 第三章 解决方案
# ============================================================
heading(doc, "第三章  解决方案", 1)

heading(doc, "3.1  解题理念与创新思路", 2)
heading(doc, "3.1.1  解题理念", 3)
para(doc, "「让系统记住用户，让调整有据可查。」")
para(doc, "传统学习工具是「表格 + 打卡」，本方案是「记忆 + 决策 + 解释」的 Agent。")

heading(doc, "3.1.2  创新思路", 3)
bullet(doc, "三层解耦：规则层（稳定）、LLM 层（理解）、调度层（协同）。")
bullet(doc, "多级兜底：调整不是一次到位，而是分 3 级：减时长 → 换方式 → 换路径。")
bullet(doc, "证据驱动：每一次调整都引用历史记录，不拍脑袋。")

heading(doc, "3.1.3  创新先进性", 3)
table(doc,
      ["维度", "传统工具", "本方案"],
      [["记忆", "无", "memory.json 存储长期状态"],
       ["调整", "手动", "自动诊断 + 重规划"],
       ["解释", "无", "reason + evidence"],
       ["容错", "无", "LLM 降级路径"]])

heading(doc, "3.2  技术方案", 2)
heading(doc, "3.2.1  技术路线图", 3)
for line in ["用户打卡 → memory.json 更新",
             "    ↓",
             "openJiuwen ReActAgent 检测触发条件",
             "    ↓",
             "调用 Skill（diagnose / replan / allocate / ...）",
             "    ↓",
             "生成 new_plan + reason + evidence",
             "    ↓",
             "输出决策日志"]:
    para(doc, line, indent=False)

heading(doc, "3.2.2  核心技术/方法", 3)
para(doc, "六个 Skill 的输入、输出与调用条件：")
caption(doc, "表 3-1  六个 Skill 的输入输出与调用条件")
table(doc,
      ["Skill", "输入", "输出", "调用条件"],
      [["diagnose", "learning_memory", "预警科目 + 严重度 + 归因", "连续失败 ≥3 天或完成率 <50%"],
       ["replan", "current_plan + diagnosis", "新计划 + 调整理由", "diagnose 返回 high"],
       ["allocate", "各科权重 + daily_available_hours", "各科分配时长", "总计划 > 可用时长 或 ≥2 科预警"],
       ["interpret_feedback", "反思原文", "归因 + 弱知识点 + 情绪 + 建议", "每次新反思写入时"],
       ["proactive_scan", "未来 7 天课表 + 计划负荷", "削峰填谷方案", "每日生成计划前"],
       ["reschedule_for_calendar_change", "旧课表 + 新课表", "重排后的任务", "课表 version 变化时"]])

heading(doc, "3.2.3  技术创新点", 3)
bullet(doc, "补欠拆分为独立任务：不把补欠加在主任务上，避免「一边减负一边加量」的矛盾。")
bullet(doc, "多级兜底策略：Day 3 拆任务 → Day 5 降难度 → Day 6 换路径。")
bullet(doc, "容错降级：LLM 超时自动切关键词匹配，保证 demo 可复现。")
bullet(doc, "数据自洽性检查：所有引用原文都存在于 memory.json。")
bullet(doc, "时长与时段自动校准：calibrate_slots() 在 planned_hours 变化时自动重算 scheduled_slots，保证计划前后数字一致。")

heading(doc, "3.3  实施方案", 2)
heading(doc, "3.3.1  实施路线图", 3)
para(doc, "已完成阶段：", bold=True)
bullet(doc, "memory.json 数据结构设计")
bullet(doc, "skills.py 六个 Skill 实现")
bullet(doc, "agent.py openJiuwen 调度")
bullet(doc, "demo.py 逐日演示脚本")
bullet(doc, "HTML 打卡系统（用户界面）")
para(doc, "答辩阶段：", bold=True)
bullet(doc, "现场运行 demo.py")
bullet(doc, "展示 6 个答题点对照")
bullet(doc, "回答评委提问")

heading(doc, "3.3.2  demo.py 逐日演示说明", 3)
para(doc, "Day 1-2：生成初始计划，观察数据。")
para(doc, "Day 3：连续 3 天未完成，触发 diagnose + replan，展示拆分 + 补欠 + 证据链。")
para(doc, "Day 4：观察，不干预。")
para(doc, "Day 5：第 2 次重规划，降级难度，展示预期效果。")
para(doc, "Day 6：第 3 次重规划，提供三个方向（推荐 A），展示「不直接劝退」的成熟决策。")
para(doc, "升级演示：", bold=True)
bullet(doc, "升级① allocate：多科冲突 → 资源再分配")
bullet(doc, "升级② interpret_feedback：自然语言反思 → 结构化信号")
bullet(doc, "升级⑤ proactive_scan：未来 7 天负荷可视化 + 削峰填谷")
bullet(doc, "升级⑦ reschedule：课表变动 → 自动重排")

heading(doc, "3.3.3  资源配置方案", 3)
bullet(doc, "技术资源：本地 Python 环境 + openJiuwen 框架")
bullet(doc, "时间资源：答辩前完成文档 + 录屏")
bullet(doc, "人力资源：一人开发 + 一人答辩（可兼任）")

heading(doc, "3.4  需求匹配度分析", 2)
heading(doc, "3.4.1  方案与命题对照", 3)
para(doc, "赛题 6 个答题点，本方案全覆盖：")
caption(doc, "表 3-2  赛题 6 个答题点覆盖对照表")
table(doc,
      ["答题点", "本方案实现"],
      [["① 用户画像与学习记忆", "memory.json 存储画像/打卡/掌握度/反思；demo 的 update_memory_after_checkin 回写"],
       ["② 至少 2 个 Skill", "实现 6 个 Skill（diagnose/replan/allocate/interpret_feedback/proactive_scan/reschedule）"],
       ["③ 动态调整案例", "demo Day 3：连续 3 天未完成触发诊断+重规划"],
       ["④ 计划生成前后对比", "demo 打印 [调整前] vs [调整后]，附 reason/evidence"],
       ["⑤ openJiuwen 作用", "agent.py 用 ReActAgent 包装 6 个 Skill 为 @tool"],
       ["⑥ 个性化与可解释性", "所有调整引用反思原文，diagnose 输出 primary_cause"]])

heading(doc, "3.4.2  匹配度评估", 3)
para(doc, "覆盖度：6/6 = 100%。")
para(doc, "满足程度：不仅满足，还超出（实现 6 个 Skill，远超「至少 2 个」要求）。")

heading(doc, "3.4.3  可行性论证", 3)
bullet(doc, "技术可行：已在 CLI 跑通。")
bullet(doc, "数据可行：HTML 系统可真实采集。")
bullet(doc, "时间可行：答辩前可完成文档与演示。")

heading(doc, "3.5  创新成效", 2)
heading(doc, "3.5.1  预期创新成果", 3)
bullet(doc, "一套可复用的「学习规划 Agent」技术框架。")
bullet(doc, "六个可迁移的 Skill 设计模式。")
bullet(doc, "一份真实的 demo 演示脚本。")

heading(doc, "3.5.2  对 openJiuwen 生态的贡献", 3)
para(doc, "本方案验证了 openJiuwen 在「长周期学习规划」场景的可行性，可作为示例项目供其他开发者参考。")

heading(doc, "3.6  预期效益", 2)
heading(doc, "3.6.1  教育效益", 3)
para(doc, "帮助学生把「静态课表」升级为「动态学习路径」，降低长期目标坚持难度。")

heading(doc, "3.6.2  社会效益", 3)
para(doc, "推动 AI Agent 在教育场景的落地，提升学习效率。")

page_break(doc)

# ============================================================
# 第四章 团队协作
# ============================================================
heading(doc, "第四章  团队协作", 1)

heading(doc, "4.1  团队介绍", 2)
heading(doc, "4.1.1  核心成员简介", 3)
para(doc, "负责人【姓名】（大三，人工智能专业）：负责整体架构设计与核心代码开发（memory.json / skills.py / agent.py / demo.py），具备独立完成 Agent 工程落地的能力，并承担现场答辩。")
para(doc, "技术成员【姓名】（大三，软件工程专业）：负责 HTML 打卡系统前端开发、localStorage 数据持久化与前端—Agent 数据对接。")
para(doc, "教育/文档成员【姓名】（大三）：本人即 2027 考研备考者（数学一 + 408），负责教育场景梳理、需求定义、文档撰写与 Q&A 准备，能站在真实用户视角打磨产品。")
heading(doc, "4.1.2  团队组成过程", 3)
para(doc, "团队成员为同校同届同学，因共同参与学科竞赛结缘，各自具备代码、前端、教育场景的不同积累。看到本命题「基于 openJiuwen 的个人学习规划 Agent」后，发现命题痛点与团队中考研备考者的真实需求高度契合——成员本人正苦于「静态课表无法动态调整」，于是决定以「考研学习规划」为切入口组队参赛，把亲身痛点转化为可演示、可解释的 Agent 方案。")
heading(doc, "4.1.3  能力互补与专业结构", 3)
para(doc, "团队在「技术 + 教育 + 表达」三方面形成互补：")
bullet(doc, "技术能力：负责人与前端成员可完成从规则引擎、Agent 编排到 HTML 前端的全栈实现；")
bullet(doc, "教育理解：成员本人是考研备考者，对「计划难坚持、方法没内化、课表冲突」有切身体会，能保证方案贴合真实场景；")
bullet(doc, "表达展示：文档与答辩由专人负责，能把技术细节翻译成评委易懂的价值陈述。")

heading(doc, "4.2  团队组织架构", 2)
heading(doc, "4.2.1  组织架构图", 3)
para(doc, "团队采用「负责人 + 技术组 + 答辩组」的扁平架构：")
para(doc, "负责人【姓名】 → 技术组（负责人 + 前端成员：skills.py / agent.py / demo.py / HTML 前端） + 答辩组（负责人 + 文档成员：文档 / 录屏 / 彩排 / Q&A）。", indent=False)
heading(doc, "4.2.2  人员配置与分工", 3)
caption(doc, "表 4-1  团队分工与能力匹配表")
table(doc,
      ["成员", "角色", "分工"],
      [["【姓名】", "负责人", "整体架构 + 核心代码（skills/agent/demo）+ 答辩"],
       ["【姓名】", "技术", "HTML 打卡前端 + 数据对接"],
       ["【姓名】", "文档/教育", "场景梳理 + 解决方案文档 + 演示脚本 + Q&A"]])

heading(doc, "4.3  团队与项目的关系", 2)
heading(doc, "4.3.1  团队投入情况", 3)
para(doc, "团队利用课余时间与周末集中开发，累计投入约 3–4 周：其中 memory.json 数据结构与 6 个 Skill 实现约 1.5 周，openJiuwen 调度层与 demo 逐日演示约 1 周，HTML 前端与数据对接约 1 周，文档、录屏与答辩彩排约 0.5–1 周。代码与数据均纳入版本管理，可审计、可复现。")
heading(doc, "4.3.2  项目真实性", 3)
para(doc, "demo 可运行、代码可审计、输出可复现。")

heading(doc, "4.4  团队与企业持续合作", 2)
heading(doc, "4.4.1  合作基础", 3)
para(doc, "项目全程基于 openJiuwen 官方 SDK（0.1.18）开发，采用其 AgentCard + ReActAgentConfig + ReActAgent 新 API 与 @tool 装饰器完成 6 个 Skill 的工具化注册与 ReAct 编排，并在昇腾社区 / openJiuwen 官方文档指导下完成 Agent 搭建。")
heading(doc, "4.4.2  持续合作可能性", 3)
para(doc, "若项目继续演进，可在三个方向上对接 openJiuwen 生态：")
bullet(doc, "能力侧：复用 JiuwenSwarm 多智能体框架，把「诊断 / 规划 / 答疑」拆成多 Agent 协作；")
bullet(doc, "算力侧：对接昇腾「算力亲和」能力，降低 Agent 推理时延，支撑真实场景的高频调用；")
bullet(doc, "生态侧：把本方案的 6 个 Skill 沉淀为可复用的 openJiuwen 工具/模板，供其他教育类 Agent 开发者参考。")

heading(doc, "4.5  外部资源", 2)
para(doc, "指导教师【姓名】（【职称】，研究方向【研究方向】）：在方案设计与答辩策略上提供指导。项目同时受益于 openJiuwen 官方文档、昇腾社区技术文章等开源资源。")

page_break(doc)

# ============================================================
# 第五章 实施计划与里程碑
# ============================================================
heading(doc, "第五章  实施计划与里程碑", 1)

heading(doc, "5.1  总体实施规划", 2)
heading(doc, "5.1.1  实施阶段划分", 3)
caption(doc, "表 5-1  项目实施进度安排表")
table(doc,
      ["阶段", "时间", "目标"],
      [["阶段 1", "已完成", "核心逻辑开发（memory/skills/agent/demo）"],
       ["阶段 2", "答辩前", "文档撰写 + 录屏 + 彩排"],
       ["阶段 3", "答辩后", "接入真实数据 + 多用户支持"]])
heading(doc, "5.1.2  各阶段目标", 3)
bullet(doc, "阶段 1：6 个 Skill 全部跑通，demo 输出稳定。")
bullet(doc, "阶段 2：解决方案文档完成，答辩稿练熟。")
bullet(doc, "阶段 3：HTML 系统与 CLI Agent 打通。")

heading(doc, "5.2  关键里程碑", 2)
heading(doc, "5.2.1  里程碑节点", 3)
for m in ["M1：memory.json 数据结构完成",
          "M2：skills.py 六个 Skill 完成",
          "M3：demo.py 逐日演示跑通",
          "M4：答辩稿完成",
          "M5：现场答辩"]:
    bullet(doc, m)

heading(doc, "5.2.2  里程碑交付物", 3)
table(doc,
      ["里程碑", "交付物", "验收标准"],
      [["M1", "memory.json", "数据完整、可追溯"],
       ["M2", "skills.py", "每个 Skill 有输入/输出/调用条件"],
       ["M3", "demo.py", "一条命令跑完 6 个答题点"],
       ["M4", "解决方案文档", "完整、可提交"],
       ["M5", "答辩", "现场通过"]])

heading(doc, "5.3  难点与重点", 2)
heading(doc, "5.3.1  关键难点识别", 3)
bullet(doc, "可解释性：如何让每一步调整都有依据。")
bullet(doc, "容错设计：LLM 不可用时如何不崩。")
bullet(doc, "多级兜底：调整无效时如何升级策略。")
heading(doc, "5.3.2  重点突破方向", 3)
bullet(doc, "建立 reason + evidence 标准。")
bullet(doc, "设计降级路径。")
bullet(doc, "实现多级兜底。")

heading(doc, "5.4  资源配置计划", 2)
bullet(doc, "人力：2–3 人（1 负责人兼答辩 + 1 前端 + 1 文档/教育），分工见 4.2.2。")
bullet(doc, "时间：核心开发已完成，答辩前预留 1 周用于文档完善、录屏制作与彩排。")
bullet(doc, "工具：Python 3.13 + openJiuwen 0.1.18 + DeepSeek V4 API + HTML（localStorage）打卡前端 + Git 版本管理。")
bullet(doc, "数据：memory.json（画像/计划/记忆/规则）+ HTML 端打卡流水，demo 阶段为模拟数据，可无缝替换为真实数据。")

page_break(doc)

# ============================================================
# 第六章 风险分析与应对
# ============================================================
heading(doc, "第六章  风险分析与应对", 1)

heading(doc, "6.1  风险识别", 2)
heading(doc, "6.1.1  技术风险", 3)
bullet(doc, "LLM API 不稳定 → 已设计降级路径")
bullet(doc, "demo 现场崩溃 → 已准备录屏")
heading(doc, "6.1.2  实施风险", 3)
bullet(doc, "答辩时间不足 → 已压缩演示到 5 分钟")
bullet(doc, "评委追问细节 → 已准备 Q&A 文档")
heading(doc, "6.1.3  合作风险", 3)
bullet(doc, "如无团队，此项可简化")
bullet(doc, "如需与 openJiuwen 对接，需提前沟通")
heading(doc, "6.1.4  资源风险", 3)
para(doc, "无（本地运行）")

heading(doc, "6.2  风险评估", 2)
table(doc,
      ["风险", "概率", "影响", "应对"],
      [["LLM 超时", "中", "中", "降级关键词匹配"],
       ["现场电脑崩", "低", "高", "录屏备份"],
       ["评委追问", "高", "中", "Q&A 准备"]])

heading(doc, "6.3  风险应对措施", 2)
para(doc, "按上表逐条说明应对策略：")
bullet(doc, "LLM 超时：interpret_feedback 与决策日志均内置「LLM 不可用自动走关键词匹配 / 确定性规则」降级路径，任何环境下都能跑出完整流程。")
bullet(doc, "现场电脑崩：提前准备 demo 录屏与 PDF 输出截图，作为离线备份。")
bullet(doc, "评委追问：已准备 docs/Q&A.md 标准问答，覆盖阈值选择、openJiuwen 作用、兜底链、产品化路径、输出稳定性等高频问题。")

page_break(doc)

# ============================================================
# 第七章 附件（佐证材料）
# ============================================================
heading(doc, "第七章  附件（佐证材料）", 1)

heading(doc, "7.1  demo.py 运行输出", 2)
para(doc, "【待附：PDF 截图，包含用户画像、Day 1-6、升级 ①⑤⑦、自检报告。】")

heading(doc, "7.2  memory.json 数据结构说明", 2)
para(doc, "【待附：完整 JSON 结构，说明各字段含义。】（可参考 docs/memory_schema.md）")

heading(doc, "7.3  代码仓库链接", 2)
para(doc, "项目源码（agent 规则引擎、openJiuwen 调度层、demo 演示脚本、HTML 打卡前端）已纳入 Git 版本管理。")
para(doc, "代码仓库：https://github.com/【用户名】/【仓库名】（请替换为实际 GitHub/Gitee 链接）")

heading(doc, "7.4  demo 演示录屏", 2)
para(doc, "5 分钟演示录屏覆盖：用户画像 → Day 1–6 逐日推进 → 升级①②⑤⑦ → 系统自检报告 → 6 个答题点对照。")
para(doc, "录屏链接：【待填写：视频链接 / 网盘链接】")

heading(doc, "7.5  6 个答题点对照表", 2)
para(doc, "【待附：完整对照表，逐条对应赛题要求。】")

heading(doc, "7.6  HTML 打卡系统演示截图", 2)
para(doc, "【待附：HTML 系统截图，说明前端如何与 CLI Agent 打通。】")

doc.save(OUT)
print("已生成：", OUT)
