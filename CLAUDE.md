# Jarvis - CLAUDE.md

## What this is
A local web manager for coding agents (Claude Code, Codex, Antigravity): a read-only view of
every agent session on this machine, plus a task queue whose tasks run headless in parallel,
each on its own git branch. Inspired by "提高 Agentic Coding 吞吐量的 10 个阶段"
("10 stages to higher agentic coding throughput").

## How a task runs
1. **Create**: from the task box, the overview's Todo or MCP `add_task`; stored in `data/projects.json`
   (`store.py`, file lock `data/dev-task.lock`). "Park in Todo" waits until it is handed out.
2. **Claim**: the worker manager in `server.py` polls and starts it, up to the project's max parallel tasks.
3. **Worktree** (git projects): `gitflow.py` runs it in `<project>/.claude/worktrees/task-<line>` on branch
   `task-<line>`, cut from the latest main; the project folder itself is never touched. Folders without git
   run tasks in place, one at a time.
4. **Run**: `worker.py` (Claude) / `agents.py` (Codex, Antigravity), headless, streaming events to the page.
5. **Commit** (git projects): what the agent left uncommitted is committed when it ends.
6. **Merge, on request**: the panel's "Merge to main" runs the project's test command, checks for conflicts
   with `git merge-tree`, merges, then removes the worktree and branch. Nothing is merged automatically.
7. **Follow-ups** resume the same session in the same worktree.

## Architecture notes
- Backend: Python + FastAPI + uvicorn. Frontend: vanilla HTML/CSS/JS (mobile-first). Storage: JSON files
  with file locks. Real time: WebSocket.
- Overview: `sessions.py` indexes this machine's Claude session logs and the desktop app's session list,
  read-only (cached in `data/session_index.json`, incremental scan every 10 s, about 0.01 s each) →
  `overview.py` (grouped by project) → `/api/overview`, `/api/projects/{id}/live` →
  `static/overview.js` / `overview.css`
- Session panel: `transcript.py` reads a log into rounds; images are only recorded by their position in
  the log `{o: line offset, i, j}` and fetched by `GET /api/sessions/{sid}/image` through `image_at()`, so the
  conversation data never carries image bodies. `session_send.py` continues a session from the page →
  `/api/sessions/{sid}`, `/send`, `/cancel` → `static/session.js` / `session.css`. Tasks claimed over MCP are
  tied to a session by the `complete_task` / `log_progress(task_id)` calls in its log (`overview.link_mcp_sessions`)
- Board: `board.project_board()` merges tasks and sessions into one card per line of work, in five columns
  by who has to act next: Todo / Queued / Running / Your turn / Done. Every session in the project whose log
  still exists, that was asked something at least once and is not headless (`codex_exec` / `sdk-cli`) gets a
  card (no age limit; the index counts all rounds in `turns`; main logs older than 14 days are indexed too,
  only old subagent logs are skipped). `hidden.py` records sessions put away
  (`POST /api/sessions/{sid}/hide`; new activity brings them back). Sent with `/api/projects/{id}/live` as
  `board`; the home page's Running / Your turn counts come from it. A reply sent from the session panel is
  recorded as a task with `source: "session"` (in_progress from the start, written back by
  `_finish_panel_task` when it ends)
- Session state: `sessions.session_state()` tells "your turn" (`asks_you()`: an unanswered
  AskUserQuestion / ExitPlanMode, or the desktop app's closing `wrap_up` with an ask / blocked; terminal and
  Codex sessions without that box count when the last reply ends with a question mark) from "done" (the turn
  ended without a question); both turn idle after 3 hours. A finished turn that still has background work
  without an answer (`bg`: background Bash / subagents / Monitor, crossed off by `<task-notification>`,
  `TaskStop`, or `TaskOutput` reading a finished result; 6 hours at most) counts as running, with the step
  "Waiting on N background tasks". The home page's "Now" order: error / limit → your turn → running → done
  → quiet; the board's "Your turn" column only holds `wait`, `done` goes to Done. The home page's
  "Now | All" switch (localStorage `ov.mode`): All = `GET /api/sessions?offset&limit&agent&project`, a paged
  list of every non-headless session that was asked something (headless ones that worked on a Jarvis task
  count too), cards for running and the like on top and rows for the rest; `/api/overview` only gives `all_count`
- Archive: the panel's archive button → `POST /api/sessions/{sid}/archive` (`hidden.archived_sessions`,
  `data/archived_sessions.json`); `SessionIndex.sessions()` marks it `archived` (new activity, or an
  instruction sent from the panel, brings it back) and it leaves Now and the board. All leaves archived
  sessions out by default; "Archived · N ›" at its end opens them (`/api/sessions?archived=1`)
- Branch status: `gitflow.branch_status(path)` → `GET /api/sessions/{sid}/git` (a worker task's worktree, or
  the folder other sessions started in). The panel header shows "branch ↑ahead ↓behind ●uncommitted"; opened,
  it lists ahead / behind, an uncommitted summary (files, +/−), commits (the branch's own; on main the last 10,
  each with its changed files via `/git/commit?sha=`) and "Merge to main"
- Interrupt and stop: while Jarvis runs a session (a panel reply or a worker task), sending interrupts the
  current turn and continues with the new instruction (`_stop_session`, then resend; for a worker the current
  task is cancelled and the follow-up starts at once); `queue: true` queues instead.
  `POST /api/sessions/{sid}/cancel` stops either; `send.ours` in the details says whether it can be stopped /
  interrupted. Sessions running in the desktop app or a terminal can only be queued
- Session effort: the model chip in the panel header picks an effort, stored in `data/session_prefs.json`
  (`session_prefs.py`) and applied by `transcript.session_settings()` as `!level` to every later turn Jarvis
  sends (not raised to "at least high"; for Antigravity it becomes the same model's name for that level)
- Questions a session leaves: `transcript.claude_prompt()` records the desktop app's closing box (`wrap_up` ask /
  reply buttons), AskUserQuestion and ExitPlanMode as the round's `prompt`; the panel shows it in a yellow box,
  with buttons on the last round while nothing runs; a click sends the button text as the reply
- Context warning: the index keeps each session's `ctx` (Claude: the last input read; Codex:
  `last_token_usage` / window; agy: cumulative input). `sessions.context_level()` grades it (Claude 300k / 600k,
  Codex 60% / 80% of the window, agy 1M / 2M); home cards, the board and the panel header show a yellow / red
  "Context N" only past a level. In the panel it is clickable: it opens "+ New session" with a hand-over note
  filled in (`openHandoff`)
- Headless runs: `sessions.headless()`, true when the first entrypoint is `sdk-cli` / `codex_exec` / an
  `agy -p` Jarvis started (first line `started` in `agy_logs`), or `claude-desktop` while missing from the
  desktop app's list (`claude -p` run in the desktop app's shell inherits that entrypoint). Such runs go idle
  when they finish and get no board card; the first entrypoint is kept in `entry0`, so `sdk-cli` appearing
  after a panel reply doesn't change it
- Usage: `usage.py`. Claude: the stream-json `rate_limit_event` (recorded during worker and panel runs;
  `POST /api/usage/refresh` sends a tiny Haiku request that is not saved). Codex: `rate_limits` in the last
  few days' rollout logs. Stored in `data/usage.json`, sent as `usage` with `/api/overview`. Antigravity:
  `agy -p /usage --output-format json --log-file /dev/null` (no tokens), refreshed in the background every
  5 minutes. The Antigravity session index reads both `~/.gemini/antigravity/` (IDE) and
  `~/.gemini/antigravity-cli/` (the `agy` CLI); the latter's titles come from the first instruction in
  `cache/conversation_metadata.json`
- Antigravity runs: `agy -p … --output-format stream-json --model <model>`; the `init` event gives the
  conversation ID. Default model `gemini-4-hydrogen-high` (the menu's options are high variants too; an
  old low / medium model carried by a follow-up is raised to the same model's high). Argon, agy's own default,
  is slow with a quota of its own, so it only runs when a new task picks it (task field `agy_model`).
  `usage.tracker.agy_models()` moves groups whose quota is used up (per /usage) to the back; fallback chain:
  chosen model → Hydrogen High → `claude-sonnet-5-5-high`. A quota error during a run (`agy_quota_error`)
  reruns the same conversation on the next model. The model used is recorded on each round's prompt line in
  the agy log (`model`) and reused by panel replies. When a follow-up's conversation has read more than 1M
  input tokens (agy's `result` `input_tokens` is cumulative; `AGY_FRESH_TOKENS`), it starts a new conversation
  with a summary of recent tasks in front of the instruction (`_agy_carry_note`). `transcript.AgyLog` keeps
  the events (plus each round's instruction) in `data/agy_logs/<conversation id>.jsonl`, which the panel
  shows (agy's own steps are protobuf and unreadable); worker and panel runs both write it
- Branches: `gitflow.py`. Worker tasks run in their own worktree `<project>/.claude/worktrees/task-<line>` on
  branch `task-<line>`, cut from the latest main; follow-ups keep their predecessor's line (same folder, so
  `claude --resume` finds the session; `carry_claude_session` copies the old log over). The project folder's
  own branch and uncommitted changes are left alone. Nothing merges by itself: a task with commits gets
  `merge_state: pending` (board "To merge"; the panel's "Merge to main" → `POST /api/projects/{pid}/tasks/{tid}/merge`
  runs the project's `test_command` in the worktree, checks conflicts with `merge-tree`, then merges with
  commit-tree + update-ref when main isn't checked out, in place when it is checked out in a clean folder, and
  refuses when that folder has uncommitted changes), after which the worktree and branch are removed; a task
  without commits is cleaned up at once. `worktree_setup` is the project's setup command for new worktrees;
  root `.env*` files are copied in. The panel's "+" opens a fresh session in the current project (Claude /
  Codex / Antigravity, default model and effort)
- Agents: a task's `agent` is claude / codex / antigravity / me. Claude's effort has a floor of high
  (`transcript.claude_effort()`: new tasks get `--effort high`, follow-ups and panel replies below high are
  raised to high). `worker.py` dispatches on it; the Codex / Antigravity calls live in `agents.py`. Sent tasks
  run right away (there is no manual mode; Todo is only used with "Park in Todo", handed out by
  `POST /tasks/{id}/assign`). Codex runs in the workspace-write sandbox with network allowed
  (`-c sandbox_workspace_write.network_access=true`). The project setting "Skip all permission checks" only
  covers Codex / Antigravity (off = sandbox); **Claude is not controlled by it**: worker and panel runs use
  `bypassPermissions` with stdio auto-approval (`WorkerProcess._handle_control_request_sync`); the settings
  page says so
- Access control: `access.AccessMiddleware` (pure ASGI, guards HTTP and WebSocket). This machine (loopback peer
  and a Host of localhost / 127.0.0.1) needs no token; other devices need it (`$JARVIS_TOKEN`, else
  `data/access_token`, created on first start): cookie `jarvis_token`, `Authorization: Bearer`,
  `X-Jarvis-Token`, or `?token=` once (a page load swaps it for the cookie and 303s it away). Requests with an
  Origin from another origin, or a cross-site `Sec-Fetch-Site`, get 403 (except following a link to a page).
  `start.sh` listens on 127.0.0.1 unless `JARVIS_HOST=0.0.0.0`; the header's phone button on this machine
  (`/api/access`, this machine only) lists LAN / Tailscale links with the token. Set `JARVIS_LOCAL_NO_TOKEN=0`
  behind a reverse proxy on this machine
- MCP progress: on start the server writes its port to `data/server_port`; `mcp_task_server.py` uses it (or
  `$JARVIS_URL`) to push `log_progress` / `submit_plan` to the page, bypassing HTTP proxies, and only writes
  the JSONL directly when that fails
- Configurable: Antigravity models (default / fallback / choices / families kept at high) are built in as
  `usage._AGY_BUILTIN`, overridden by `data/models.json`, and sent to the task box menu by `GET /api/models`.
  The task section added to a new project's CLAUDE.md (`config.TASK_LIFECYCLE_TEMPLATES`) comes in Chinese and
  English, picked by the UI language when the project is created (request field `lang`; API callers without
  it get English), with `JARVIS_PORT` replaced by the real port
- The front end's only external script, `marked`, is pinned with SRI (`lib/marked.umd.js`; jsDelivr's
  on-the-fly .min files are unfit for SRI). To upgrade, change the version and recompute the integrity
- Dependencies: `mcp[cli]` is pinned below 2 (2.x renamed `FastMCP` to `MCPServer`)
- UI languages: `state.lang` (the 中 / EN button in the header, localStorage `lang`). New UI text always
  comes in both languages: app.js uses `t(key)` (`i18n.zh/en`) or `tz(中文, English)`, overview.js `tr()`
  (`L.zh/en`), session.js `[zh, en]` pairs. Chinese static text in index.html is collected at start by
  `captureStaticTexts()` and switched through the `STATIC_EN` table, so **Chinese text added to index.html
  needs its English in `STATIC_EN`**. Chinese errors from the server are translated by `localizeServer()` in
  `api()` / the WebSocket handler with the `SERVER_EN` regex table (only the `error` / `note` / `failed` / `err`
  fields), so **a new user-facing server error needs an entry in `SERVER_EN`**. Tool steps (读 / 改 / 搜…) get
  their verb swapped by `stepText()`. The token login page (`access.LOGIN_PAGE`) follows the device's `lang`
  or the browser language. Prompts to agents (worker / agents / MCP tool descriptions / the image-only
  placeholder `config.IMAGES_ONLY_PROMPT`) are always English (`tests/test_prompts_english.py` checks);
  tasks created from a plan start with `[From Plan #N, subtask M]`, and the page still recognizes the old
  `[来自 Plan #N 子任务 M]`
- Public docs: `README.md` (English) / `README.zh-CN.md` (Chinese) and the MIT `LICENSE`; when startup,
  environment variables or security behaviour change, update both READMEs. Their screenshots come from a
  demo instance with made-up data, never from a real one
- Tests: `python3 -m pytest tests -q`

## Conventions
- Don't make the same mistake twice.
- Context, not control: describe what is needed, don't micromanage.
- When a test or a merge fails, fix the cause and try again; don't give up and mark the task failed.
- Instructions that belong to one machine (the task queue's project_id, reply language, the dev log) go in
  `CLAUDE.local.md`, which is not in git and which Claude Code reads by itself; `gitflow.setup_worktree`
  symlinks it into worker worktrees
