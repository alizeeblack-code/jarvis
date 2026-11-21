"""
MCP Task Server — exposes task management tools to Claude Code.

Designed as a USER-LEVEL MCP server so all projects can use it.
Auto-detects project_id from cwd when not specified.

Setup (global, one-time):
  claude mcp add --transport stdio -s user task-manager -- python3 /path/to/jarvis/mcp_task_server.py

Progress is pushed to the running web manager at $JARVIS_URL, else the port it
recorded in data/server_port, else http://localhost:5100.

Tools provided:
  - list_projects: List all projects
  - list_tasks: List tasks (auto-detects project from cwd)
  - claim_next: Claim the next pending task
  - complete_task: Mark a task as done with summary
  - add_task: Create a new task
  - get_task: Get task details by ID
  - fail_task: Mark a task as failed with error message
"""

import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Find and load the project store
PROJ_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJ_DIR))
from store import ProjectStore

store = ProjectStore()


def _manager_url() -> str:
    url = os.environ.get("JARVIS_URL", "").rstrip("/")
    if url:
        return url
    try:
        port = int((PROJ_DIR / "data" / "server_port").read_text().strip())
    except (OSError, ValueError):
        port = 5100
    return f"http://localhost:{port}"


def _post_log(pid: int, task_id: int, payload: dict) -> None:
    """Hand a log line to the web manager so open pages see it at once."""
    import urllib.request
    req = urllib.request.Request(
        f"{_manager_url()}/api/projects/{pid}/tasks/{task_id}/log",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    # never through an HTTP proxy: it is this machine
    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3)
mcp = FastMCP("TaskManager",
              instructions="任务管理工具。用于领取、执行、完成项目任务。"
                           "大多数工具的 project_id 可省略，会自动根据当前工作目录匹配。")


def _resolve_project(project_id: int = 0) -> int:
    """Resolve project_id. Claude should pass it explicitly (from CLAUDE.md).
    Falls back to cwd matching if not specified."""
    if project_id > 0:
        return project_id
    # Fallback: try cwd match (works when MCP server cwd == project dir)
    cwd = os.getcwd()
    data = store._read()
    for p in data.get("projects", []):
        if os.path.normpath(p["path"]) == os.path.normpath(cwd):
            return p["id"]
    return 0


@mcp.tool()
def list_projects() -> str:
    """列出所有项目及其任务统计"""
    data = store._read()
    cwd = os.getcwd()
    projects = []
    for p in data.get("projects", []):
        tasks = p.get("tasks", [])
        stats = {}
        for t in tasks:
            s = t.get("status", "pending")
            stats[s] = stats.get(s, 0) + 1
        projects.append({
            "id": p["id"],
            "name": p["name"],
            "path": p["path"],
            "task_count": len(tasks),
            "stats": stats,
            "is_current": os.path.normpath(p["path"]) == os.path.normpath(cwd),
        })
    return json.dumps(projects, ensure_ascii=False, indent=2)


@mcp.tool()
def list_tasks(project_id: int = 0, status: str = "") -> str:
    """列出项目的任务列表。project_id 可省略（自动匹配当前目录）。可选按状态筛选"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    tasks = store.get_tasks(pid)
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    result = []
    for t in tasks:
        result.append({
            "id": t["id"],
            "description": t["description"][:100],
            "status": t.get("status", "pending"),
            "plan_mode": t.get("plan_mode", False),
            "predecessor_id": t.get("predecessor_id"),
            "created_at": t.get("created_at"),
        })
    return json.dumps({"project_id": pid, "tasks": result}, ensure_ascii=False, indent=2)


@mcp.tool()
def get_task(task_id: int, project_id: int = 0) -> str:
    """获取任务详情。project_id 可省略"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    task = store.get_task(pid, task_id)
    if not task:
        return json.dumps({"error": "Task not found"})
    result = {k: v for k, v in task.items() if k != "logs"}
    result["recent_logs"] = store.get_logs(pid, task_id, limit=5)
    result["log_count"] = store.get_log_count(pid, task_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def get_task_logs(task_id: int, project_id: int = 0, limit: int = 50) -> str:
    """获取任务的运行日志。默认返回最近 50 条。"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    logs = store.get_logs(pid, task_id, limit=limit)
    total = store.get_log_count(pid, task_id)
    return json.dumps({
        "task_id": task_id,
        "total": total,
        "showing": len(logs),
        "logs": logs,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def claim_next(project_id: int = 0, model: str = "") -> str:
    """领取下一个待执行的任务。model＝领取者：claude / codex / antigravity（旧值 gemini 等同 antigravity）/ ""(任意)。只领 pending；Todo 里的任务要先在网页上派出去"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    task = store.claim_next_task(pid, worker_id="mcp-worker", model=model)
    if not task:
        return json.dumps({"task": None, "message": "没有待执行的任务"})
    return json.dumps({
        "project_id": pid,
        "task": {
            "id": task["id"],
            "description": task["description"],
            "status": task["status"],
            "plan_mode": task.get("plan_mode", False),
            "images": task.get("images", []),
        },
        "instructions": (
            f"请执行此任务。执行过程中请用 log_progress(task_id={task['id']}, message='...') "
            f"汇报关键进展（分析结论、修改内容、commit ID 等）。"
            + (f"\n\n这是 Plan Mode 任务：请只做规划不实现。输出计划后调用 "
               f"submit_plan(task_id={task['id']}, plan_content='...计划全文...') 提交。"
               f"\n计划格式要求：## 概述 / ## 子任务（带依赖） / ## 问题与澄清 / ## 风险"
               if task.get("plan_mode") else
               f"\n完成后用 complete_task(task_id={task['id']}, summary='...') 标记完成。")
        )
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def complete_task(task_id: int, summary: str, project_id: int = 0) -> str:
    """完成任务并附带工作总结。project_id 可省略。summary 格式：问题/实现/改动文件/commit"""
    from datetime import datetime
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    task = store.get_task(pid, task_id)
    if not task:
        return json.dumps({"error": "Task not found"})
    store.update_task(pid, task_id,
                      status="done",
                      completed_at=datetime.now().isoformat(),
                      summary=summary)
    return json.dumps({"success": True, "task_id": task_id, "project_id": pid})


@mcp.tool()
def fail_task(task_id: int, error: str, project_id: int = 0) -> str:
    """标记任务失败。project_id 可省略"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    task = store.get_task(pid, task_id)
    if not task:
        return json.dumps({"error": "Task not found"})
    store.update_task(pid, task_id, status="failed", error=error)
    return json.dumps({"success": True, "task_id": task_id, "project_id": pid})


@mcp.tool()
def add_task(description: str, plan_mode: bool = False,
                   predecessor_id: int = 0, project_id: int = 0,
                   model: str = "") -> str:
    """添加新任务。model＝执行者：claude / codex / antigravity / me（我自己）/ ""（按项目设置，默认先进 Todo）。plan_mode=true 时只规划不实现"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    task = store.add_task(
        pid, description,
        plan_mode=plan_mode,
        predecessor_id=predecessor_id if predecessor_id else None,
        model=model)
    if not task:
        return json.dumps({"error": "Failed to add task"})
    return json.dumps({
        "success": True,
        "project_id": pid,
        "task": {
            "id": task["id"],
            "description": task["description"][:100],
            "status": task["status"],
        }
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def log_progress(task_id: int, message: str, project_id: int = 0) -> str:
    """汇报任务进展。在执行任务的关键步骤时调用，让 Web Manager 能实时看到进度。
    例如：读完代码后汇报分析结论、修改文件后汇报改了什么、提交代码后汇报 commit。"""
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    # Push via HTTP API so WebSocket broadcasts to web UI in real-time
    try:
        _post_log(pid, task_id, {"message": message, "type": "message"})
    except Exception:
        # Fallback: write directly to JSONL if server is down
        store.append_log(pid, task_id, type="message", content=message)
    return json.dumps({"success": True})


@mcp.tool()
def submit_plan(task_id: int, plan_content: str, project_id: int = 0) -> str:
    """提交 Plan Mode 任务的计划内容，设置状态为待 Review。
    plan_content 应包含：概述、子任务列表（带依赖关系）、问题与澄清、风险。
    用户在 Web 界面审批后会自动创建子任务。"""
    from utils import extract_plan_json, extract_questions, extract_subtasks
    pid = _resolve_project(project_id)
    if not pid:
        return json.dumps({"error": "无法自动匹配项目，请指定 project_id"})
    task = store.get_task(pid, task_id)
    if not task:
        return json.dumps({"error": "Task not found"})

    # Try JSON extraction first, fall back to regex
    plan_data = extract_plan_json(plan_content)
    if plan_data:
        subtasks = plan_data.get("subtasks", [])
        questions = plan_data.get("questions", [])
    else:
        subtasks = extract_subtasks(plan_content)
        questions = extract_questions(plan_content)

    store.update_task(pid, task_id,
                      status="review",
                      plan_content=plan_content,
                      plan_questions=questions,
                      plan_subtasks=subtasks)

    # Notify Web UI via WebSocket
    try:
        _post_log(pid, task_id, {"message": f"Plan 已提交，等待审批（{len(subtasks)} 个子任务，{len(questions)} 个问题）",
                                 "type": "result"})
    except Exception:
        pass

    return json.dumps({
        "success": True,
        "task_id": task_id,
        "status": "review",
        "subtasks_count": len(subtasks),
        "questions_count": len(questions),
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
