# 基于 openJiuwen 的个人学习规划 Agent

国际创新比赛参赛项目。一个能**记住用户学习状态**、**动态规划学习路径**、
**根据反馈调整计划**的个人学习规划 Agent（考研场景：数学一 + 408）。

- 框架：openJiuwen 0.1.18（Apache-2.0，昇腾开源 Agent 框架）新 API
- 模型：DeepSeek V4（`deepseek-v4-pro`，官方 API，openjiuwen 原生支持）
- 语言：Python 3.11≤v<3.14（本机 3.13.5，用 `py` 启动器）

## 快速开始

```bash
# 1. 配置 API Key（已有则跳过）
#    编辑 .env，把 API_KEY 换成你的 DeepSeek key

# 2. 一条命令演示所有答题点
py demo.py
```

> 没有配置 API Key 也能跑通：`demo.py` 会自动降级为「确定性规则输出」，
> 打印相同的数据结论，只是决策日志不由 LLM 撰写。

## 文件结构

```text
agent/
├── memory.json              # 用户画像 + 学习记忆 + 当前计划 + 规则（初始状态）
├── skills.py                # 6 个 Skill：diagnose / replan / allocate / interpret_feedback / proactive_scan / reschedule_for_calendar_change，确定性规则
├── agent.py                 # openJiuwen 决策层：AgentCard + ReActAgent + @tool 封装
├── server.py                # FastAPI 后端：打通 HTML 前端与 Agent（免手动搬文件）
├── requirements.txt         # FastAPI 后端依赖（fastapi / uvicorn / python-multipart / supabase）
├── demo.py                  # 逐日演示脚本（一条命令覆盖 6 个答题点）
├── presentation_script.md   # 答辩逐字稿 + 时间分配（5–8 分钟）
├── memory_after_demo.json   # demo 运行后生成的最终记忆快照
├── .env                     # 模型连接配置
└── docs/
    ├── memory_schema.md     # 记忆 Schema 与更新机制
    ├── skill_design.md      # 6 个 Skill 的输入/输出/调用条件
    ├── adjustment_cases.md  # 动态调整案例（连续 3 天未完成）
    └── Q&A.md               # 评委高频问题与标准答案
```

## 架构

```text
┌─────────────────────────────────────────────────────┐
│  openJiuwen 决策层（agent.py）                        │
│  ReActAgent：思考 → 调用工具 → 观察 → … → 决策日志     │
│     └─ 6 个 @tool Skill ←──── 规则引擎（skills.py）    │
└──────────────────────────┬──────────────────────────┘
                           │ 读写
┌──────────────────────────▼──────────────────────────┐
│  学习记忆（memory.json）                              │
│  画像 / 计划 / 各科掌握度 / 连续失败 / 完成率 / 反思     │
└─────────────────────────────────────────────────────┘
```

- **感知层 + 记忆层**：`memory.json` + `demo.py` 的打卡回写逻辑。
- **决策层**：openJiuwen `ReActAgent`，把 6 个 Skill 包装成工具自主编排。
- **规则层**：`skills.py` 的确定性规则，保证每一步调整可解释、可审计。

## HTML + Agent 联动演示（完整产品闭环）

把纯前端打卡系统（`../index.html`、`../app.js`、`../data.js`，localStorage 持久化）当
「感知层 + 记忆层」，CLI Agent 当「决策层」，两者通过 `memory.json` 解耦：

```text
┌─────────────────────────────┐  memory.json   ┌─────────────────────────────┐
│ HTML 打卡系统（感知 + 记忆）  │ ─────────────► │  CLI Agent（决策层）         │
│ · 打卡 / 错题 / 专注 / 单词   │                │  · 6 个 Skill 自主编排         │
│ · 「我的」页 6 个 Skill 入口   │ ◄───────────── │  · 输出 new_plan.json         │
└─────────────────────────────┘  new_plan.json  └─────────────────────────────┘
```

### ① HTML 导出：`📤 导出 Agent 数据`

在 HTML「我的」页点击导出，把 localStorage 里的打卡记录、错题本、专注记录、单词进度
汇总成 `memory.json`（含 `user_profile` / `current_plan` / `learning_memory.subjects` /
`recent_records` / `replanning_log`）。其中 `mastery_score` 按公式实时计算，不硬编码：

```
掌握度 = 完成率×0.4 + (1−错题率)×0.4 + 平均效率评分/5×0.2
```

### ② CLI 决策：`py agent.py`

```bash
py agent.py memory.json            # 确定性规则，无需 API，秒出结果
py agent.py memory.json --llm      # 加 openJiuwen + DeepSeek 生成 LLM 决策日志
```

读取 `memory.json` → 跑 `diagnose` + `replan` → 输出 `new_plan.json`：

```text
new_plan.json
├── user_profile        # 原样带回（画像不动）
├── current_plan        # 重规划后的新计划（供覆盖）
├── replanning_log      # 追加本次调整记录（调整前后对比 + 理由 + 依据）
├── diagnosis           # 诊断结果（预警科目 / 主因）
└── decision_log        # 中文决策日志（LLM 或确定性规则）
```

### ③ HTML 导入：`📥 导入 Agent 建议`

在「我的」页选择 `new_plan.json`：

- 读取 `current_plan`，**只覆盖未来日期**（写入 `agentPlan` 覆盖层），过去的打卡记录保留；
- 今日时间轴自动显示新任务；
- `replanning_log` 展示在「统计 → 调整日志」标签（含调整前后对比与依据）。

### 演示脚本（答辩用）

```text
1. 在 HTML 打卡系统里连续 3 天「数据结构」未完成，错题本记几条错题
2. 「我的」→ 📤 导出 Agent 数据 → 得到 memory.json
3. py agent.py memory.json --llm     # 生成 new_plan.json，打印调整前后对比
4. 「我的」→ 📥 导入 Agent 建议 → 选择 new_plan.json
5. 今日时间轴出现新任务；「统计 → 调整日志」出现调整依据
```

## 前后端联动演示（FastAPI，免手动搬文件）

上面的 CLI 流程需要手动下载 `memory.json`、命令行跑 `agent.py`、再手动导入 `new_plan.json`。
`server.py` 提供一个本地 FastAPI 后端，把这三步收成「前端点按钮 → 后端跑规则 → 前端自动写回」：

```text
┌─────────────────────────────┐   POST /api/export_memory   ┌─────────────────────────────┐
│ HTML 前端（「我的」页）        │ ──────────────────────────► │  FastAPI 后端（server.py）   │
│ · 📤 导出并发送 Agent         │                              │  · 收 memory.json 落盘        │
│ · 🔄 重新规划                 │ ◄────────────────────────── │  · skills.diagnose + replan  │
│ · 📷 上传错题照片             │    new_plan + reason + evidence│  · 生成 new_plan.json         │
└─────────────────────────────┘   POST /api/replan          └─────────────────────────────┘
```

### 依赖与启动

```bash
pip install -r requirements.txt     # fastapi + uvicorn[standard] + python-multipart
py server.py                        # 等价于 py -m uvicorn server:app --reload（127.0.0.1:8000）
```

健康检查：浏览器打开 <http://127.0.0.1:8000/> 应看到 `{"message":"Agent Server is running"}`。

### 接口一览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 健康检查 |
| POST | `/api/export_memory` | 接收前端 `buildAgentMemory()` 生成的 dict，保存 `memory.json` |
| POST | `/api/replan` | 读 `memory.json` → `diagnose` + `replan` → 返回 `new_plan` + `reason` + `evidence` |
| POST | `/api/reschedule` | 课表变动 → `reschedule_for_calendar_change` 冲突检测 + 重排，返回新计划 + 调整明细 |
| POST | `/api/parse_schedule` | 接收课表图片（base64）→ 硅基流动视觉模型 OCR → 返回可编辑课表条目 |
| POST | `/api/upload_mistake` | 接收错题照片（`UploadFile` + `Form` 标签）；Supabase 已配置则存 Storage 并返回公开 URL，否则存本地 `uploads/` |

`/api/replan` 只调用 `skills.py` 的确定性规则，**不 import openjiuwen、不调 LLM**，因此离线 / 无 key /
openjiuwen 缺失都能稳定返回完整的 `reason` + `evidence`，天然满足「LLM 不可用时降级为规则引擎」；
如需 LLM 决策日志，另行运行 `py agent.py memory.json --llm`。

### 演示步骤（答辩用）

```text
1. 在 HTML 打卡系统里连续 3 天「数据结构」未完成，错题本记几条错题
2. 「我的」→ 📤 导出并发送 Agent    # POST /api/export_memory，后端落盘 memory.json
3. 「我的」→ 🔄 重新规划            # POST /api/replan，返回 new_plan + reason + evidence
4. 今日时间轴自动出现新任务；「统计 → 调整日志」出现调整理由与依据
5. （可选）📷 上传错题照片 → 后端 uploads/（或 Supabase Storage）出现带时间戳的图片文件
```

## 手机访问完整步骤（内网穿透，电脑需开机）—— 当前实际部署方式

> **当前实际运行方式**：后端跑在本机（`uvicorn 8000`），cpolar 内网穿透让手机访问；数据存 Supabase（已配置，见 .env），未配置时降级本地文件。下方「云端部署」是规划中的 7×24 方案，**尚未实际启用**。

后端顺带托管前端（同源），所以只需**一个 cpolar 隧道**，手机打开一个地址即可，无需配置后端地址。

```text
1. 启动后端（绑定 0.0.0.0，前端后端同源）：
   cd agent && py -m uvicorn server:app --host 0.0.0.0 --port 8000

2. 开一个 cpolar 隧道（新开一个终端）：
   cpolar http 8000
   → 记下 cpolar 分配的 https 地址，例如 https://xxxx.cpolar.cn

3. 手机浏览器打开第 2 步的地址（直接就是前端首页），点「📤 导出并发送 Agent」验证。
```

一键脚本：双击 `start_mobile.bat` 自动完成上面 1、2 步并打印地址。

> 注意：
> - cpolar 免费版地址**每次重启隧道都会变**，每次用脚本重新打印即可。
> - 后端 CORS 已放开；前端后端同源，云端/隧道访问时 `BACKEND_URL` 自动走同源相对路径，**无需填「后端地址」**。
> - 电脑必须保持开机（后端 + cpolar 都跑在电脑上）。

## 云端部署（规划中 / 备选，未实际启用）

> **状态说明**：本节为 7×24 云端方案，**尚未实际启用**。当前实际运行的是上面的「本地 + cpolar」方式。
> Supabase 已配置（`SUPABASE_URL` / `SUPABASE_KEY` 已写入 .env），本地后端已在用它存数据；Render 云端托管仅作规划备选，`render.yaml` 已备好但未部署。

后端 + 前端都放 Render、数据放 Supabase，实现 7×24 可用。**前端由后端顺带托管**（同一个
onrender.com 域名），所以不需要 Vercel（`vercel.app` 在国内被墙）、也不需要配后端地址（同源）。
核心原因：Render 免费层是**临时文件系统**（重新部署会清空本地文件），所以 `memory.json`、
上次课表和错题图片都迁到 Supabase；未配置 Supabase 环境变量时，后端自动降级回本地文件，本地开发不受影响。

### 1. 代码推到 GitHub

```bash
# 在 D:\XX 根目录（.gitignore 已排除 .env / __pycache__ / uploads 等）
git add -A
git commit -m "feat: 云端部署（Supabase 存数据，Render 托管前后端）"
git push origin main
```

> `.env`（含 API Key）已被 .gitignore 排除，不会推上去；密钥通过下面的环境变量注入。

### 2. Supabase 建表 + 存储桶

登录 <https://supabase.com> → New project → 记下 **Project URL** 和 **service_role key**
（Project Settings → API 里；后端用 service_role key 可绕过 RLS，最省事）。打开 SQL Editor 执行：

```sql
-- memory：存学习记忆（memory.json）
create table if not exists memory (
  user_id text primary key,
  data jsonb not null,
  updated_at timestamptz default now()
);

-- schedule_last：记住上次课表，供课表重排 diff（Render 重启后仍能对比）
create table if not exists schedule_last (
  user_id text primary key,
  data jsonb not null,
  updated_at timestamptz default now()
);

-- 错题图片存储桶（public=true 才能直接公开访问）
insert into storage.buckets (id, name, public)
values ('mistakes', 'mistakes', true)
on conflict (id) do nothing;
```

### 3. Render 部署（前后端一体）

仓库根目录已带 `render.yaml`（Blueprint），后端会顺带托管前端：

**方式 A（推荐，省事）**：Render → New → **Blueprint** → 连接 GitHub 仓库，会自动读 `render.yaml`
部署（后端 + 前端一起）。部署后到该服务的 **Environment** 标签，手动加两个密钥：

   | 变量 | 值 |
   | --- | --- |
   | `SUPABASE_URL` | `https://lupsygzkoiwvheeqcmmj.supabase.co` |
   | `SUPABASE_KEY` | 你的 service_role key（`eyJ...`） |

   （`VISION_API_KEY` 可选，课表 OCR 用；加完点 Save 会自动重启生效。）

**方式 B（手动）**：Render → New → **Web Service** → 连仓库，
Build Command=`cd agent && pip install -r requirements.txt`、
Start Command=`cd agent && uvicorn server:app --host 0.0.0.0 --port $PORT`，
再在 Environment 加同样的 `SUPABASE_URL` / `SUPABASE_KEY`。

健康检查：访问 `https://<服务名>.onrender.com/api/health`，应看到 `{"message":"Agent Server is running"}`。

### 4. 手机访问

手机浏览器打开 `https://<服务名>.onrender.com/` 直接就是前端首页，**不用填后端地址**（同源）。
点「📤 导出并发送 Agent」，状态栏变绿即打通。之后电脑关机也能用：数据存 Supabase，换手机打开同地址即同步。

> 国内访问提示：`render.com`（官网首页）常被墙，但 `dashboard.render.com`（后台）和
> `onrender.com`（部署域名）通常可访问；打不开 `render.com` 时直接进 `dashboard.render.com`。
> 免费版首次请求有几十秒冷启动，属正常。

## 国内云服务器部署（无需绑卡，替代 Render）

Render 的免费 Web Service 强制绑 Visa/Mastercard，银联卡过不了。国内用户可用腾讯云 / 阿里云的
新用户免费试用云服务器代替：实名认证后开一台轻量服务器，把代码拉上去跑 uvicorn 即可，**代码零改动**
（后端已顺带托管前端，单进程单端口）。

### 1. 开服务器

腾讯云「轻量应用服务器」或阿里云「ECS」→ 新用户免费试用（需**实名认证**，一般不用绑卡）。
记下**公网 IP**，并在控制台的「防火墙 / 安全组」里**放行 TCP 8000 端口**。

### 2. 服务器上部署

SSH 登录服务器（或网页终端），粘贴执行：

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
cd ~
git clone https://github.com/jeffreysgeorgy-crypto/kaoyan-checkin.git
cd kaoyan-checkin/agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 写 .env（VISION 两行可选，课表 OCR 用）
cat > .env << 'EOF'
SUPABASE_URL=https://lupsygzkoiwvheeqcmmj.supabase.co
SUPABASE_KEY=你的service_role_key
VISION_API_BASE=https://api.siliconflow.cn/v1
VISION_API_KEY=你的siliconflow_key
VISION_MODEL=Qwen/Qwen2.5-VL-72B-Instruct
EOF

# 后台启动（断开 SSH 后继续跑）
nohup uvicorn server:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
sleep 3 && curl http://127.0.0.1:8000/api/health
```

看到 `{"message":"Agent Server is running"}` 即成功。

### 3. 手机访问

手机浏览器打开 `http://<公网IP>:8000/` 即前端首页，**不用填后端地址**（同源）。
数据存 Supabase，换手机打开同地址即同步。

> 注意：裸 IP + 非标准端口是 HTTP（非 HTTPS），所以 PWA 离线缓存（service worker）不生效，
> 但核心功能（打卡 / 错题 / 专注 / 单词 / Agent 联动）全部正常。要 HTTPS + 自定义域名需 ICP 备案。

## 6 个 Skill 完整演示流程

6 个 Skill 全部接入了 HTML 前端，每个配一个入口按钮；后端只跑确定性规则（不调 LLM），
因此每一步都稳定返回 `reason` + `evidence`，答辩可逐条解释。

| # | Skill | 前端入口 | 后端接口 | 结果卡片展示 |
| --- | --- | --- | --- | --- |
| 1 | 学习诊断 `diagnose` | 「我的」→ 🔍 学习诊断 | `POST /api/diagnose` | 预警/健康科目、主因、触发条件 |
| 2 | 重新规划 `replan` | 「我的」→ 🔄 重新规划 | `POST /api/replan` | 调整前后对比 + 理由 |
| 3 | 资源再分配 `allocate` | 「我的」→ ⚖️ 资源再分配 | `POST /api/allocate` | 各科小时条形图 + 公式 |
| 4 | 未来负荷扫描 `proactive_scan` | 「我的」→ 📊 未来 7 天负荷 | `POST /api/proactive_scan` | 7 天负荷柱、超载红、削峰方案 |
| 5 | 反馈解析 `interpret_feedback` | 「反思」→ 💬 分析反思 | `POST /api/interpret_feedback` | 归因 / 弱知识点 / 情绪 / 建议 |
| 6 | 课表变动重排 `reschedule_for_calendar_change` | 「课表」→ 🔄 检测冲突并重排 | `POST /api/reschedule` | 冲突明细 + 新计划 |

**统一体验**（5 条要求全部落地）：
1. **loading**：点按钮后文字变「⏳ 处理中…」并禁用，请求结束恢复；
2. **结果卡片**：结果渲染成 `agent-result-card` 卡片（不是 `alert` 弹窗）；
3. **reason + evidence**：卡片逐条展示 `reason`（理由）与 `evidence`（依据）；
4. **错误红色提示**：失败时 `#agentResults` / `#feedbackResult` 顶部插入红色错误条，并在「Agent 在线联动」状态栏标红；
5. **写回 localStorage**：结果存进 `state.agentResults`，刷新页面不丢（`init()` 里 `renderAgentResults()` / `renderFeedbackResult()` 恢复）。

### 完整演示脚本（答辩用，一次跑通 6 个 Skill）

```text
0. 启动后端：cd agent && py server.py            # 127.0.0.1:8000
1. 「我的」→ 📤 导出并发送 Agent                 # 前端把 localStorage 汇总成 memory.json 落盘后端
2. 「我的」→ 🔍 学习诊断                         # Skill①：展示预警科目 + 主因（reason/evidence）
3. 「我的」→ 🔄 重新规划                         # Skill②：调整前后对比卡片 + 今日时间轴更新
4. 「我的」→ ⚖️ 资源再分配                       # Skill③：4 科小时分配条形图 + 公式
5. 「我的」→ 📊 未来 7 天负荷                    # Skill④：超载日标红 + 削峰填谷方案
6. 「反思」页填三段反思 → 💬 分析反思            # Skill⑤：归因/弱知识点/情绪/建议
7. 「课表」页 → 📷 导入课表 → 🔄 检测冲突并重排  # Skill⑥：冲突检测 + 重排新计划
8. 刷新页面 → 第 2~5 步的结果卡片仍在（localStorage 持久化）
```

> 注意：`/api/diagnose` 和 `/api/replan` 依赖 `memory.json`，需先点「导出并发送 Agent」生成。其余 4 个 Skill 不强依赖。
> 另：Skill⑥ 依赖「课表」页已有课表数据（先 📷 导入或 📅 新增课程）。

## 6 个答题点对照

| 答题点 | 实现位置 |
| --- | --- |
| ① 用户画像与学习记忆如何存储与更新 | `memory.json` + `demo.py::update_memory_after_checkin` |
| ② 至少 2 个 Skill | `skills.py` 的 6 个 Skill（详见 `docs/skill_design.md`） |
| ③ 动态调整案例（连续 3 天未完成） | `demo.py` Day 3～6（详见 `docs/adjustment_cases.md`） |
| ④ 计划生成前后对比 | `demo.py` 的 `[调整前] vs [调整后]` 打印 |
| ⑤ openJiuwen 的作用 | `agent.py`：AgentCard + ReActAgentConfig + ReActAgent + @tool |
| ⑥ 个性化与可解释性 | 调整明细的 `reason` / `evidence` 引用历史与反思原文 |

## 关键技术点

- 使用 openJiuwen **新 API**（旧版 `WorkflowAgentConfig`/`single_agent.legacy` 已弃用）：
  `AgentCard` 描述 Agent 身份，`ReActAgentConfig.configure_model_client(...)` 配置模型，
  `@tool` 装饰器把函数包装成工具，`agent.ability_manager.add_ability(...)` 注册工具，
  `await agent.invoke({"query": ...})` 执行 ReAct 循环。
- 模型走 OpenAI 兼容接口：`provider=deepseek`、`api_base=https://api.deepseek.com`、
  `model_name=deepseek-v4-pro`（openjiuwen 原生支持 DeepSeek V4，自动处理 `reasoning_content` 回传）。
- 备用：`deepseek-v4-flash` 更快更省；如需切回硅基流动，配置见 `.env` 注释。
