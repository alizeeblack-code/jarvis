"""Shared configuration and constants"""

from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
LOCK_FILE = DATA_DIR / "dev-task.lock"
UPLOADS_DIR = DATA_DIR / "uploads"
TASK_LOGS_DIR = DATA_DIR / "task_logs"
LOGS_DIR = BASE_DIR / "logs"

MAX_WORKERS = 5
DEFAULT_PROJECT_MAX_WORKERS = 3

# Who a task can be handed to. "me" = a personal todo nobody runs automatically.
AGENTS = ("claude", "codex", "antigravity")
ASSIGNEES = AGENTS + ("me",)


def task_agent(task: dict) -> str:
    """Executor of a task, with fallbacks for records made before `agent` existed."""
    agent = task.get("agent") or ""
    if agent:
        return agent
    model = task.get("model") or ""
    if model == "gemini":
        return "antigravity"
    if model in AGENTS:
        return model
    if str(task.get("worker_id") or "").startswith("worker-"):
        return "claude"          # the built-in worker only ever ran Claude
    return ""


def normalize_agent(value: str) -> str:
    value = (value or "").strip().lower()
    return "antigravity" if value == "gemini" else value
BASE_PORT = 5200
RESERVED_DEV_PORTS = frozenset({5100, 5180, 7890})

# What a project's CLAUDE.md gets when it is added, in the UI's language at the time.
# PROJECT_ID / JARVIS_PORT are filled in; the heading is the marker that it is there.
TASK_LIFECYCLE_TEMPLATES = {
    "zh": '''
## Task Lifecycle (Auto-managed by Claude Code Manager)

**重要：使用中文回复用户。** 运行日志可以是英文，但面向用户的输出、总结、计划必须使用中文。

### 工作流程
当你收到任务时，请按照以下步骤执行：

1. **理解任务**: 仔细阅读任务描述，理解需求
2. **实现功能**: 在当前目录中编写代码
3. **提交代码**: `git add` 相关文件，`git commit` 提交改动
4. **运行测试**: 如果项目有测试，运行测试确保通过
5. **完成**: 退出

### 冲突处理
**Rebase 失败时的处理流程**:
1. 如果是 "unstaged changes" 错误，先 commit 或 stash 当前改动
2. 如果有 merge conflicts:
   - `git status` 查看冲突文件
   - 读取冲突文件内容，理解双方改动意图
   - 手动解决冲突（保留正确的代码）
   - `git add <resolved-files>`
   - `git rebase --continue`
3. 重复直到 rebase 完成

**测试失败时的处理流程**:
1. 运行测试
2. 如果失败，分析错误信息
3. 修复代码中的 bug
4. 重新运行测试，直到全部通过
5. 提交修复: `git commit -m "fix: ..."`

**不要放弃**: 遇到 rebase 或测试失败时，必须解决问题后才能继续，不能直接标记任务失败。

### 经验教训沉淀
每次遇到问题或完成重要改动后，要在 PROGRESS.md 中记录：
- 遇到了什么问题
- 如何解决的
- 以后如何避免
- **必须附上 git commit ID**

**同样的问题不要犯两次！**

### 大文件读取
当读取文件遇到 token 超限错误时，使用 `offset` 和 `limit` 参数分段读取：
```
Read(file_path="large_file.py", offset=1, limit=500)
Read(file_path="large_file.py", offset=500, limit=500)
```
或使用 Grep/Glob 先定位再精确读取。

### 任务队列（MCP 工具）
本项目通过 MCP task-manager 管理任务。可用工具：
- `claim_next` — 领取下一个待执行任务
- `complete_task(task_id, summary)` — 完成任务
- `fail_task(task_id, error)` — 标记失败
- `log_progress(task_id, message)` — 汇报进度（Web Manager 可实时看到）

**执行任务时，请在关键步骤调用 `log_progress` 汇报进度。**

自动循环: `/loop 3m 用 claim_next 检查任务，有任务就执行并 complete_task（附带 summary），没有就跳过`

备用 curl 方式（MCP 不可用时）:
领取: `curl -s --noproxy localhost -X POST http://localhost:JARVIS_PORT/api/projects/PROJECT_ID/claim-next`
完成: `curl -s --noproxy localhost -X POST http://localhost:JARVIS_PORT/api/projects/PROJECT_ID/tasks/TASK_ID/complete -H "Content-Type: application/json" -d '{"summary": "..."}'`
''',
    "en": '''
## Task Lifecycle (Auto-managed by Claude Code Manager)

### Workflow
When you get a task:

1. **Understand it**: read the task description carefully
2. **Implement it**: write the code in the current directory
3. **Commit**: `git add` the files you changed, then `git commit`
4. **Test**: if the project has tests, run them and make sure they pass
5. **Done**: exit

### Conflicts
**When a rebase fails**:
1. "unstaged changes": commit or stash your changes first
2. Merge conflicts:
   - `git status` to see the conflicting files
   - read them and understand what each side intended
   - resolve by hand, keeping the correct code
   - `git add <resolved-files>`
   - `git rebase --continue`
3. Repeat until the rebase is done

**When tests fail**:
1. Run the tests
2. Read the failure
3. Fix the bug in the code
4. Run the tests again until they all pass
5. Commit the fix: `git commit -m "fix: ..."`

**Don't give up**: fix rebase and test failures before moving on; never just mark the task failed.

### Lessons learned
After a problem or an important change, record in PROGRESS.md:
- what went wrong
- how it was fixed
- how to avoid it next time
- **the git commit ID**

**Don't make the same mistake twice.**

### Large files
If reading a file runs over the token limit, read it in parts with `offset` and `limit`:
```
Read(file_path="large_file.py", offset=1, limit=500)
Read(file_path="large_file.py", offset=500, limit=500)
```
or locate the part you need with Grep/Glob first.

### Task queue (MCP tools)
Tasks for this project come from the task-manager MCP server:
- `claim_next` — claim the next pending task
- `complete_task(task_id, summary)` — finish a task
- `fail_task(task_id, error)` — mark it failed
- `log_progress(task_id, message)` — report progress (shown live in the web manager)

**Call `log_progress` at the key steps of a task.**

Loop: `/loop 3m use claim_next to check for a task; if there is one, do it and complete_task with a summary, otherwise skip`

Without MCP, use curl:
Claim: `curl -s --noproxy localhost -X POST http://localhost:JARVIS_PORT/api/projects/PROJECT_ID/claim-next`
Complete: `curl -s --noproxy localhost -X POST http://localhost:JARVIS_PORT/api/projects/PROJECT_ID/tasks/TASK_ID/complete -H "Content-Type: application/json" -d '{"summary": "..."}'`
''',
}
