# Jarvis - CLAUDE.md

## 项目概述
这是一个 Claude Code Web Manager，用于管理多个并行 Claude Code 实例的任务调度系统。
灵感来自"提高 Agentic Coding 吞吐量的 10 个阶段"。

## 架构说明
支持多个 Claude Code 实例并行工作，每个实例通过 `claude -p` 非交互式执行任务。

```
并行开发工作流
┌─────────────────────────────────────────────┐
│  Worker 1    Worker 2    Worker 3    ...     │
│  port:5200   port:5201   port:5202          │
│     │           │           │               │
│   data/       data/       data/   (隔离数据)  │
└─────────────────────────────────────────────┘

共享文件 (symlink):
- dev-tasks.json (任务队列)
- dev-task.lock (文件锁)
```

## 任务生命周期
1. **领取任务**: 原子操作，从 `data/dev-tasks.json` 获取任务
2. **创建工作区**: 分配 worker，隔离环境
3. **实现功能**: Claude Code 在隔离环境中工作
4. **提交代码**: `git commit` 在任务分支
5. **Merge + 测试**: `git fetch origin && git merge origin/main` + `npm test`
6. **自动合并到 main**: `git rebase origin/main`，冲突处理
7. **标记完成**: 更新 `dev-tasks.json`
8. **清理**: 删除 worktree 和远程分支
9. **经验沉淀**: 在 PROGRESS.md 记录经验教训

## 冲突处理
**Rebase 失败时**:
1. 如果是 "unstaged changes" 错误，先 commit 或 stash
2. 如果有 merge conflicts:
   - `git status` 查看冲突文件
   - 读取冲突内容，理解双方改动意图
   - 手动解决（保留正确的代码）
   - `git add <resolved-files>` → `git rebase --continue`
3. 重复直到 rebase 完成

**测试失败时**:
1. 运行测试
2. 分析错误信息
3. 修复代码中的 bug
4. 重新运行测试直到全部通过
5. 提交修复

**不要放弃**: 遇到 rebase 或测试失败时，必须解决问题后才能继续。

## 技术栈
- Backend: Python + FastAPI + uvicorn
- Frontend: Vanilla HTML/CSS/JS (mobile-first)
- Process: claude -p --output-format stream-json --verbose
- Storage: JSON file with file-based locking
- Real-time: WebSocket
- 全局视图：`sessions.py`（只读索引本机 Claude 会话日志 + 桌面版会话清单，缓存 `data/session_index.json`，每 10 秒增量扫描，一次约 0.01 秒）→ `overview.py`（按项目汇总）→ `/api/overview`、`/api/projects/{id}/live` → `static/overview.js`/`overview.css`
- 会话面板：`transcript.py`（日志读成轮次；图片只记在日志里的位置 `{o: 行偏移, i, j}`，由 `GET /api/sessions/{sid}/image` 经 `image_at()` 按位置取出，对话数据里不带图片本体）+ `session_send.py`（从网页续写会话）→ `/api/sessions/{sid}`、`/send`、`/cancel` → `static/session.js`/`session.css`；MCP 领取的任务靠日志里的 `complete_task/log_progress(task_id)` 关联会话（`overview.link_mcp_sessions`）
- 看板：`board.py` 的 `project_board()` 把任务和会话合成「一条工作线一张卡」，按「现在该谁动」分 Todo / 排队 / 进行中 / 等你 / 已完成五列；项目里日志还在、问过至少一次、非无头运行（`codex_exec`/`sdk-cli`）的会话都成卡（不限天数；索引 `turns` 记总轮数，14 天前的主日志也收录、只跳过旧的子代理日志），`hidden.py` 记「收起」的会话（`POST /api/sessions/{sid}/hide`，有新动静自动回到看板）；随 `/api/projects/{id}/live` 的 `board` 下发，首页项目卡的「在跑 / 等你」计数也取自它。会话面板里直接回复会记成 `source: "session"` 的任务（创建即 in_progress，结束时由 `_finish_panel_task` 回写）
- 会话状态：`sessions.session_state()` 区分「等你」（`asks_you()`：未答的 AskUserQuestion / ExitPlanMode，或桌面版每轮收尾 `wrap_up` 带 ask / blocked；没有收尾框的终端、Codex 会话看最后一句是否以问号结尾）和「完成」（`done`，这一轮做完没留问题），都 3 小时后变空闲；一轮已结束但还有没回音的后台任务（`bg`：后台 Bash / 子代理 / Monitor，`<task-notification>`、`TaskStop`、`TaskOutput` 读完时划掉，6 小时为限）算「在跑」，步骤显示「等后台任务（N 个）」；首页「现在」排序 出错/额度 → 等你 → 在跑 → 完成 → 安静，看板「等你」列只放 wait，`done` 进「已完成」；首页「现在 | 全部」切换（记在 localStorage `ov.mode`）：全部 = `GET /api/sessions?offset&limit&agent&project` 分页列出所有问过的、非无头的会话（做过 Jarvis 任务的无头会话也算），在跑等卡片在上、其余为行，底部不再单列会话；`/api/overview` 只给 `all_count`
- 归档：面板右上角归档按钮 → `POST /api/sessions/{sid}/archive`（`hidden.archived_sessions`，`data/archived_sessions.json`），`SessionIndex.sessions()` 把它标成 `archived`（之后又有动静即自动回来，面板发指令也会取消归档），离开「现在」与看板；「全部」默认不含已归档，末尾「已归档 · N ›」进入（`/api/sessions?archived=1`）
- 分支状态：`gitflow.branch_status(path)` → `GET /api/sessions/{sid}/git`（worker 任务用其工作区、其他会话用启动目录），面板顶部「分支 ↑领先 ↓落后 ●未提交」标签，点开看领先 / 落后、未提交汇总（文件数 +/−）、提交列表（分支自己的提交；main 上为最近 10 个，每条可点开看改动文件，`/git/commit?sha=`）和「合并到 main」
- 插话与停止：Jarvis 正在跑某会话（面板续写 / worker 任务）时，发送默认打断当前这一轮再带新指令续接（`_stop_session` 后重发；worker 取消当前任务、续接任务立刻开始），`queue: true` 才排队；`POST /api/sessions/{sid}/cancel` 统一停止，详情 `send.ours` 表示可停 / 可插话；桌面版、终端里在跑的会话只能排队
- 会话 effort：面板顶部模型标签可点选 effort，存 `data/session_prefs.json`（`session_prefs.py`），由 `transcript.session_settings()` 以 `!档位` 形式套用到之后从 Jarvis 发出的所有轮次（不受「至少 high」限制；Antigravity 换成同模型对应档位的名字）
- 会话留下的提问：`transcript.claude_prompt()` 把桌面版收尾框（`wrap_up` 的 ask / 回复型按钮）、AskUserQuestion、ExitPlanMode 记成轮次的 `prompt`，面板黄框显示，最后一轮且没在跑时出按钮，点了就把按钮文字作为回复发出
- 上下文提示：索引记每个会话的 `ctx`（Claude 最近一次读入、Codex `last_token_usage` / 窗口、agy 累计输入），`sessions.context_level()` 分档（Claude 30 万 / 60 万、Codex 窗口 60% / 80%、agy 100 万 / 200 万），首页卡片、看板、面板顶部只在超档时出现黄 / 红「上下文 N」；面板里的可点，打开「＋新会话」并预填交接说明（`openHandoff`）
- 无头运行：`sessions.headless()`——最初的 entrypoint 是 `sdk-cli` / `codex_exec` / Jarvis 发起的 `agy -p`（`agy_logs` 首行 `started`），或 `claude-desktop` 却不在桌面版清单里（在桌面版的 shell 里跑 `claude -p` 会继承这个 entrypoint）。跑完直接算空闲、不成看板卡；最初的 entrypoint 记在 `entry0`，面板续写后日志里出现的 `sdk-cli` 不影响判断
- 用量：`usage.py`——Claude 取 stream-json 的 `rate_limit_event`（worker、面板续写时顺手记，`POST /api/usage/refresh` 用 Haiku 发一个不落盘的极小请求刷新），Codex 读最近几天 rollout 日志里的 `rate_limits`，存 `data/usage.json`，随 `/api/overview` 的 `usage` 下发；Antigravity 用 `agy -p /usage --output-format json --log-file /dev/null`（不花 token），5 分钟后台刷新一次。Antigravity 会话索引同时读 `~/.gemini/antigravity/`（IDE）和 `~/.gemini/antigravity-cli/`（`agy` 命令行），后者标题取 `cache/conversation_metadata.json` 的首句指令
- Antigravity 执行：`agy -p … --output-format stream-json --model <模型>`，`init` 事件直接给会话 ID；默认模型 `gemini-4-hydrogen-high`（下拉四个选项也都是 high 版；续接沿用的旧模型是 low / medium 的提到同名 high）（agy 自己默认的 Argon 慢、额度单列，只在新建任务时手动选「Argon」才用，任务字段 `agy_model`）；`usage.tracker.agy_models()` 按 /usage 把额度用完的组排到后面，回退链 选定模型 → Hydrogen High → `claude-sonnet-5-5-high`，运行中报额度错（`agy_quota_error`）就在同一会话换下一个模型重跑；用的模型记在 agy 日志每轮的 prompt 行（`model`），面板续写沿用；续接任务时对话累计输入（agy `result` 的 `input_tokens` 是累计值）超过 100 万 token（`AGY_FRESH_TOKENS`）就换新对话，指令前附最近任务摘要（`_agy_carry_note`）；`transcript.AgyLog` 把事件（加上每轮的指令）存到 `data/agy_logs/<会话ID>.jsonl`，会话面板据此显示对话（agy 自己的步骤是 protobuf 读不了）；worker 与面板续写都写这份日志
- 分支：`gitflow.py`——worker 任务在 `<项目>/.claude/worktrees/task-<line>` 的独立工作区里跑，分支 `task-<line>` 从最新 main 开；续接任务沿用前序的 line（同一目录，`claude --resume` 才找得到会话，旧会话日志由 `carry_claude_session` 复制过去）。项目目录本身的分支和未提交改动不动。做完不自动合并：有提交就 `merge_state: pending`（看板「待合并」，面板「合并到 main」→ `POST /api/projects/{pid}/tasks/{tid}/merge`：先在工作区跑项目设置里的 `test_command`，再用 `merge-tree` 查冲突；main 没被签出时用 commit-tree + update-ref 合并，签出在干净目录里时就地 merge，目录有未提交改动则拒绝），合并后删工作区和分支；没有提交就直接清理。`worktree_setup` 是新工作区的准备命令，根目录 `.env*` 会被复制过去。面板右上角「＋」在当前项目开一个全新会话（选 Claude / Codex / Antigravity，模型和 effort 用默认）
- 执行者：任务 `agent` = claude / codex / antigravity / me；Claude 的 effort 以 high 为下限（`transcript.claude_effort()`：新任务 `--effort high`，续接 / 面板续写低于 high 的提到 high）；`worker.py` 按它分派，Codex / Antigravity 的调用在 `agents.py`；发出的任务直接执行（没有手动模式；Todo 只在选「先放 Todo」时才用，`POST /tasks/{id}/assign` 派出）；Codex 在 workspace-write 沙箱里跑但允许联网（`-c sandbox_workspace_write.network_access=true`）；项目设置「完全跳过权限」只管 Codex / Antigravity（关 = 沙箱），**Claude 不受它控制**：worker 与面板续写都用 `bypassPermissions` + stdio 自动批准（`WorkerProcess._handle_control_request_sync`）；设置页如实写明
- 访问控制：`access.py` 的 `AccessMiddleware`（纯 ASGI，HTTP 与 WebSocket 都管）——本机（回环地址且 Host 是 localhost / 127.0.0.1）免令牌，其他设备要令牌（`$JARVIS_TOKEN`，否则 `data/access_token` 首次自动生成）：cookie `jarvis_token`、`Authorization: Bearer`、`X-Jarvis-Token` 或带一次 `?token=`（页面加载时换成 cookie 并 303 去掉）；带 Origin 且不同源、或 `Sec-Fetch-Site` 是跨站的请求一律 403（从别处点链接打开页面除外）。`start.sh` 默认只听 127.0.0.1，`JARVIS_HOST=0.0.0.0` 才对外；本机页头「手机访问」（`/api/access`，仅本机可读）列出带令牌的局域网 / Tailscale 地址。本机前面套反向代理时设 `JARVIS_LOCAL_NO_TOKEN=0`
- MCP 进度转发：服务启动时把端口写进 `data/server_port`，`mcp_task_server.py` 据此（或 `$JARVIS_URL`）把 `log_progress` / `submit_plan` 推给网页，不走 HTTP 代理；推不到才直接写 JSONL
- 可配置：Antigravity 模型（默认 / 回退 / 选项 / 不低于 high 的系列）内置在 `usage._AGY_BUILTIN`，`data/models.json` 覆盖，`GET /api/models` 下发任务框下拉；新项目注入的任务说明 `config.TASK_LIFECYCLE_TEMPLATES` 有中英两份，按建项目时的界面语言（请求体 `lang`，API 不带则英文）选，`JARVIS_PORT` 换成实际端口
- 前端唯一的外部脚本 `marked` 锁版本 + SRI（`lib/marked.umd.js`，jsDelivr 现场压缩的 .min 不适合 SRI）；升级时换版本号并重算 integrity
- 依赖：`mcp[cli]` 锁在 `<2`（2.x 把 `FastMCP` 改名为 `MCPServer`）
- 界面中英：`state.lang`（页头「中 / EN」，存 localStorage `lang`）。新文案一律中英成对：app.js 用 `t(key)`（`i18n.zh/en`）或 `tz(中文, English)`，overview.js 用 `tr()`（`L.zh/en`），session.js 用 `[中, 英]` 对；index.html 里的静态中文由 `captureStaticTexts()` 启动时收集、按 `STATIC_EN` 表切换——**index.html 加了中文文案要同时在 `STATIC_EN` 里加英文**；服务端返回的中文报错由 `api()` / WebSocket 经 `localizeServer()` 按 `SERVER_EN` 正则表翻成英文（只看 `error` / `note` / `failed` / `err` 字段）——**服务端新增给用户看的报错要在 `SERVER_EN` 里加一条**；工具步骤「读 / 改 / 搜…」由 `stepText()` 换动词；令牌登录页（`access.LOGIN_PAGE`）按设备上的 `lang` 或浏览器语言切换。给 agent 的提示词（worker / agents / MCP 工具说明 / 只有图片时的占位 `config.IMAGES_ONLY_PROMPT`）一律英文（`tests/test_prompts_english.py` 把关）；从 Plan 生成的任务描述以 `[From Plan #N, subtask M]` 开头，前端也认旧的 `[来自 Plan #N 子任务 M]`
- 对外文档：`README.md`（英文）/ `README.zh-CN.md`（中文），MIT `LICENSE`；改了启动方式、环境变量、安全行为要同步两份 README
- 测试：`python3 -m pytest tests -q`

## 开发约定
- 同样的问题不要犯两次！
- Context, not control — 描述清楚需求，不要微管理
- 本机专属的说明（任务队列的 project_id、回复语言、开发日志）放在不进 git 的 `CLAUDE.local.md`，Claude Code 会自动读取；worker 工作区里由 `gitflow.setup_worktree` 软链接回项目根目录那一份
