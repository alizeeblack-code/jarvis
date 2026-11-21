# Jarvis

[English](README.md)

在本机跑的网页控制台，用来并行调度多个编程 agent，并且在电脑浏览器或手机上随时看到本机所有 agent 会话的状态。支持的 agent 有
[Claude Code](https://docs.anthropic.com/en/docs/claude-code)、OpenAI Codex CLI 和 Google Antigravity（`agy`）。

整个程序就是一个 Python 进程（FastAPI），前端是原生 HTML/JS。没有数据库，也不需要构建，状态都存在 `data/` 下的 JSON 文件里。

## 能做什么

- **本机所有会话的总览**
  - 读取 Claude Code（`~/.claude/projects`）、Codex（`~/.codex/sessions`）和 Antigravity 写下的日志，按项目归类。
  - 显示哪些在跑、哪些在等你、哪些刚做完，以及每个会话离上下文上限还有多远。
  - 只读，不会改动这些日志。
- **在网页上续写任意会话**：看对话（含图片）、回复、插话打断、排队下一条、调 effort、归档。
- **按项目的任务队列**
  - 写一条任务、选执行者（Claude / Codex / Antigravity），发出去就会无人值守地运行。
  - 每个任务在独立的 git worktree 里跑，分支是 `task-<n>`，所以可以同时跑好几个，也不会动你当前的工作目录。
  - 续接任务沿用同一个会话。
- **看板**：一条工作线一张卡，按「现在该谁动」分成 Todo / 排队 / 进行中 / 等你 / 已完成五列。
- **在界面里合并**：任务分支做完后，先跑项目的测试命令，再用 `git merge-tree` 确认没有冲突，然后合并到 `main`。
- **用量**：Claude、Codex、Antigravity 的剩余额度一眼可见。
- **MCP 任务工具**：通过 `mcp_task_server.py`，agent 可以自己领任务、汇报进度、标记完成（`claim_next`、`log_progress`、`complete_task` 等）。

## 环境要求

- Python 3.11+
- `git`
- `PATH` 里至少有一个已登录的 agent 命令行：
  - `claude`（Claude Code）：大部分功能依赖它
  - `codex`（OpenAI Codex CLI）：可选
  - `agy`（Google Antigravity）：可选
- 主要在 macOS 上开发和使用。Linux 大体可用但没有测过；Claude 桌面版的会话清单只在 macOS 上有。

## 快速开始

```bash
git clone https://github.com/alizeeblack-code/jarvis.git
cd jarvis
pip3 install -r requirements.txt
./start.sh            # http://localhost:5100
```

- 换端口：`./start.sh 8080`
- `static/` 下文件改动时自动重载：`./start.sh --dev`

启动后点「+ 新建项目」，选一个 git 仓库。添加项目时，Jarvis 会在该仓库的 `CLAUDE.md` 末尾追加一段任务说明，并新建 `PROGRESS.md`，这样 agent 就知道怎么回报进度。任务说明用添加时的界面语言写（先用页头的「中 / EN」切换）。

### 让 agent 能用任务工具（可选）

```bash
claude mcp add --transport stdio -s user task-manager -- python3 /path/to/jarvis/mcp_task_server.py
```

MCP 服务会自己找到正在运行的 Jarvis：Jarvis 启动时会把端口记在 `data/server_port`。要指定别的地址，设置 `JARVIS_URL`。

## 安全

**对本机以外开放之前，请先读这一节。**

### agent 的权限很大

任务是无人值守运行的，中途没法停下来等你批准：

- **Claude**：始终自动批准所有工具调用，不受项目设置影响。
- **Codex 和 Antigravity**：项目设置「完全跳过权限」关闭时在沙箱里运行，只能写工作区，可以联网；打开后不受限制。

只添加你信任的仓库。能访问 Jarvis，就等于拿到了你这个用户账号的 shell。

### 默认只能在本机访问

- Jarvis 默认只监听 `127.0.0.1`，本机发出的请求不需要令牌。
- 浏览器里的跨站请求一律拒绝，所以你打开的网页没法借你的浏览器操控 `localhost` 上的 Jarvis。
- 没有 TLS。需要加密时，走 VPN 或套一层 HTTPS 反向代理。

### 从手机或其他电脑访问

1. 用 `JARVIS_HOST=0.0.0.0 ./start.sh` 启动，最好只在 Tailscale 这类 VPN 里可达。
2. 其他设备都要访问令牌。令牌首次运行时生成在 `data/access_token`，也可以用 `JARVIS_TOKEN` 指定。
3. 在本机打开 Jarvis，点页头的手机图标，里面有带令牌的现成链接。在另一台设备上打开一次，之后靠 cookie 保持登录。
4. API 客户端可以发 `Authorization: Bearer <令牌>`。

如果在同一台机器上给 Jarvis 套了反向代理，要设置 `JARVIS_LOCAL_NO_TOKEN=0`，否则所有经代理进来的请求都会被当成本机请求。

## 配置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `JARVIS_HOST` | `127.0.0.1` | `start.sh` 监听的地址 |
| `JARVIS_TOKEN` | `data/access_token` 的内容 | 其他设备用的访问令牌 |
| `JARVIS_LOCAL_NO_TOKEN` | `1` | 本机套反向代理时设为 `0` |
| `JARVIS_URL` | `http://localhost:<记录的端口>` | `mcp_task_server.py` 把进度发到哪里 |

每个项目的设置在项目设置弹窗里，包括：测试命令、新工作区准备命令、最大并行任务数、dev 端口、自动提交、跳过权限。

### Antigravity 模型

Antigravity 任务可选的模型内置在 `usage.py` 里。要改的话，新建 `data/models.json`；没写的项沿用内置值：

```json
{
  "antigravity": {
    "default": {"model": "gemini-4-hydrogen-high", "label": "Hydrogen"},
    "fallback": "claude-sonnet-5-5-high",
    "choices": {
      "argon": {"model": "gemini-4-argon-high", "label": "Argon"}
    },
    "keep_high": ["claude-opus-5-5"]
  }
}
```

- `default`：任务没选模型时用它。
- `fallback`：所选模型额度用完时接着试它。
- `choices`：任务输入框里的其他选项。
- `keep_high`：这些模型系列运行时不低于 `-high` 档。上面列出的模型所属系列已自动算在内。

改完重启 Jarvis 生效。Claude 和 Codex 用的是各自命令行里设置的模型。

## 目录结构

| 路径 | 内容 |
|---|---|
| `server.py` | FastAPI 应用：REST API、WebSocket、worker 调度 |
| `access.py` | 访问控制（令牌、跨站检查） |
| `worker.py`、`agents.py` | 运行 Claude / Codex / Antigravity 任务 |
| `gitflow.py` | 工作区、分支、合并 |
| `sessions.py`、`overview.py`、`board.py` | 会话索引、总览、看板 |
| `transcript.py`、`session_send.py` | 读会话、从网页续写会话 |
| `store.py` | 项目与任务（`data/projects.json`） |
| `usage.py` | 额度统计 |
| `mcp_task_server.py` | 给本机 agent 用的 MCP 任务工具 |
| `mcp_task_client.py` | 同一套工具，给另一台机器上的 agent 通过 HTTP 用（`--host`、`--port`、`--token`） |
| `static/` | 前端 |
| `data/`、`logs/` | 运行时数据（不进 git） |

## 测试

```bash
python3 -m pytest tests -q
```

## 许可证

[MIT](LICENSE)
