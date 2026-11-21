"""
MCP Task Client — remote version of task-manager that communicates via HTTP.

Use this on remote machines to connect to the Web Manager running elsewhere.

Setup on remote machine:
  1. pip install "mcp[cli]"
  2. Copy this file to the remote machine
  3. claude mcp add --transport stdio -s user task-manager -- \
       python3 /path/to/mcp_task_client.py --host 192.168.x.x --port 5100 --token <token>
     (the token is data/access_token on the manager's machine; or set JARVIS_TOKEN)
  4. Set project_id in the project's CLAUDE.md

All tool calls are proxied to the Web Manager HTTP API.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from mcp.server.fastmcp import FastMCP

# Parse --host and --port from command line
MANAGER_HOST = "localhost"
MANAGER_PORT = 5100
MANAGER_TOKEN = os.environ.get("JARVIS_TOKEN", "")
args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--host" and i + 1 < len(args):
        MANAGER_HOST = args[i + 1]
        i += 2
    elif args[i] == "--token" and i + 1 < len(args):
        MANAGER_TOKEN = args[i + 1]
        i += 2
    elif args[i] == "--port" and i + 1 < len(args):
        MANAGER_PORT = int(args[i + 1])
        i += 2
    else:
        i += 1

BASE_URL = f"http://{MANAGER_HOST}:{MANAGER_PORT}"

mcp = FastMCP("TaskManager-Remote",
              instructions=f"Task manager (remote), connected to {BASE_URL}")


def _api(method: str, path: str, data: dict = None) -> dict:
    """Call the Web Manager HTTP API."""
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}
    if MANAGER_TOKEN:
        headers["Authorization"] = f"Bearer {MANAGER_TOKEN}"
    try:
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method=method)
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": f"Connection failed: {e}"}


@mcp.tool()
def list_projects() -> str:
    """List all projects with their task counts."""
    return json.dumps(_api("GET", "/api/projects"), ensure_ascii=False, indent=2)


@mcp.tool()
def list_tasks(project_id: int = 0, status: str = "") -> str:
    """List a project's tasks, optionally by status."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    result = _api("GET", f"/api/projects/{project_id}/tasks")
    tasks = result.get("tasks", [])
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return json.dumps({"project_id": project_id, "tasks": [
        {"id": t["id"], "description": t.get("description", "")[:100],
         "status": t.get("status"), "plan_mode": t.get("plan_mode", False),
         "model": t.get("model", "")}
        for t in tasks
    ]}, ensure_ascii=False, indent=2)


@mcp.tool()
def get_task(task_id: int, project_id: int = 0) -> str:
    """Get a task's details."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    tasks = _api("GET", f"/api/projects/{project_id}/tasks").get("tasks", [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return json.dumps({"error": "Task not found"})
    # Get logs
    logs = _api("GET", f"/api/projects/{project_id}/tasks/{task_id}/logs?limit=10")
    result = {k: v for k, v in task.items() if k != "logs"}
    result["recent_logs"] = logs.get("logs", [])[-5:]
    result["log_count"] = logs.get("total", 0)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def get_task_logs(task_id: int, project_id: int = 0, limit: int = 50) -> str:
    """Get a task's run log."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    result = _api("GET", f"/api/projects/{project_id}/tasks/{task_id}/logs?limit={limit}")
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def claim_next(project_id: int = 0, model: str = "") -> str:
    """Claim the next pending task."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    result = _api("POST", f"/api/projects/{project_id}/claim-next")
    task = result.get("task")
    if not task:
        return json.dumps({"task": None, "message": "No pending tasks"})
    # Model filter (server-side claim-next doesn't filter by model for HTTP API)
    task_model = task.get("model", "")
    if model and task_model and task_model != model:
        # Unclaim: set back to pending
        _api("POST", f"/api/projects/{project_id}/tasks/{task['id']}/reopen")
        return json.dumps({"task": None, "message": f"Task #{task['id']} is for {task_model}; skipped"})
    return json.dumps({
        "project_id": project_id,
        "task": {
            "id": task["id"],
            "description": task.get("description", ""),
            "status": task.get("status"),
            "plan_mode": task.get("plan_mode", False),
            "model": task.get("model", ""),
            "images": task.get("images", []),
        },
        "instructions": (
            f"Work on this task. Report key progress with log_progress(task_id={task['id']}, message='...')."
            + (f"\n\nThis is a Plan Mode task: plan only, do not implement. When done, call "
               f"submit_plan(task_id={task['id']}, plan_content='...')."
               if task.get("plan_mode") else
               f"\nWhen done, call complete_task(task_id={task['id']}, summary='...').")
        )
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def complete_task(task_id: int, summary: str, project_id: int = 0) -> str:
    """Complete a task with a summary of the work."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    result = _api("POST", f"/api/projects/{project_id}/tasks/{task_id}/complete",
                  {"summary": summary})
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def fail_task(task_id: int, error: str, project_id: int = 0) -> str:
    """Mark a task as failed."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    # Use update via reopen + fail pattern
    _api("POST", f"/api/projects/{project_id}/tasks/{task_id}/retry")
    result = _api("POST", f"/api/projects/{project_id}/tasks/{task_id}/complete",
                  {"summary": f"FAILED: {error}"})
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def add_task(description: str, plan_mode: bool = False,
             predecessor_id: int = 0, project_id: int = 0,
             model: str = "") -> str:
    """Add a task."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    result = _api("POST", f"/api/projects/{project_id}/tasks", {
        "description": description,
        "plan_mode": plan_mode,
        "predecessor_id": predecessor_id if predecessor_id else None,
        "model": model,
    })
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def log_progress(task_id: int, message: str, project_id: int = 0) -> str:
    """Report progress on a task (shown live in the web manager)."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    result = _api("POST", f"/api/projects/{project_id}/tasks/{task_id}/log",
                  {"message": message, "type": "message"})
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def submit_plan(task_id: int, plan_content: str, project_id: int = 0) -> str:
    """Submit the plan of a Plan Mode task."""
    if not project_id:
        return json.dumps({"error": "Pass project_id"})
    # Store plan content via the revise endpoint (sets status back to review)
    # Actually we need a dedicated endpoint, for now use complete with plan marker
    result = _api("POST", f"/api/projects/{project_id}/tasks/{task_id}/log",
                  {"message": plan_content, "type": "message"})
    # TODO: Need a dedicated server endpoint for remote plan submission
    return json.dumps({"success": True, "note": "Plan logged. Please review in Web UI."})


if __name__ == "__main__":
    # Strip our custom args before passing to MCP
    filtered_argv = [sys.argv[0]]
    mcp.run(transport="stdio")
