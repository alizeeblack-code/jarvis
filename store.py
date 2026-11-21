"""ProjectStore — manages projects and tasks in projects.json"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import (AGENTS, ASSIGNEES, BASE_PORT, DEFAULT_PROJECT_MAX_WORKERS,
                    LOCK_FILE, MAX_WORKERS, PROJECTS_FILE, RESERVED_DEV_PORTS,
                    TASK_LIFECYCLE_TEMPLATES, normalize_agent, task_agent)


class FileLock:
    """Cross-process lock on a file, safe to share between threads: a thread lock
    serialises this process, and each thread keeps its own descriptor (a shared
    `self.fd` let one thread close the file another had just opened). Re-entrant."""

    def __init__(self, path: Path):
        import threading
        self.path = path
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def acquire(self):
        import fcntl
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            try:
                fd = open(self.path, "w")
                fcntl.flock(fd, fcntl.LOCK_EX)
            except BaseException:
                self._thread_lock.release()
                raise
            self._local.fd = fd
        self._local.depth = depth + 1

    def release(self):
        import fcntl
        depth = getattr(self._local, "depth", 0)
        if depth <= 0:
            return
        self._local.depth = depth - 1
        if depth == 1:
            fd, self._local.fd = self._local.fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                fd.close()
        self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


class ProjectStore:
    """Manages projects, each with its own task list"""

    def __init__(self):
        self.lock = FileLock(LOCK_FILE)
        with self.lock:
            if not PROJECTS_FILE.exists():
                self._write({"next_id": 1, "projects": []})
            else:
                data = self._read()
                if self._ensure_dev_ports(data):
                    self._write(data)

    def _read(self) -> dict:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict):
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── Project CRUD ──

    def create_project(self, name: str, path: str, inject: bool = True,
                       reserved_ports: Optional[set] = None, lang: str = "en",
                       port: int = 5100) -> dict:
        """inject=False registers an existing folder without writing anything
        into it (no CLAUDE.md task section, no PROGRESS.md)."""
        abs_path = os.path.normpath(os.path.expanduser(path))
        if inject:
            os.makedirs(abs_path, exist_ok=True)
            self._ensure_progress_md(abs_path)

        with self.lock:
            data = self._read()
            changed = self._ensure_dev_ports(data, reserved_ports=reserved_ports)
            # Prevent duplicate paths
            for p in data["projects"]:
                if os.path.normpath(p["path"]) == abs_path:
                    if changed:
                        self._write(data)
                    return self._project_summary(p)
            project_id = data["next_id"]
            dev_port = self._allocate_dev_port(data["projects"], reserved_ports=reserved_ports)
            project = {
                "id": project_id,
                "name": name,
                "path": abs_path,
                "created_at": datetime.now().isoformat(),
                "next_task_id": 1,
                "tasks": [],
                "settings": {
                    "skip_permissions": False,  # False = bidirectional stdin protocol
                    "auto_commit": True,
                    "new_task_mode": "auto",   # tasks you send run right away (Todo only when chosen)
                    "dev_port": dev_port,
                },
            }
            data["projects"].append(project)
            data["next_id"] = project_id + 1
            self._write(data)

        # Inject CLAUDE.md with project_id (after lock released)
        if inject:
            self._ensure_claude_md(abs_path, name, project_id, lang=lang, port=port)
        return self._project_summary(project)

    @staticmethod
    def _ensure_claude_md(project_path: str, project_name: str,
                          project_id: int = 0, lang: str = "en", port: int = 5100):
        import re
        claude_md = os.path.join(project_path, "CLAUDE.md")
        lifecycle_marker = "## Task Lifecycle (Auto-managed by Claude Code Manager)"
        pid_marker = "**Task Manager project_id:"

        existing = ""
        if os.path.exists(claude_md):
            with open(claude_md, "r", encoding="utf-8") as f:
                existing = f.read()

        changed = False

        # 1. Ensure project_id line exists (plain text, visible to Claude)
        if project_id > 0:
            pid_line = f"{pid_marker} {project_id}**"
            if pid_marker in existing:
                # Update existing
                new = re.sub(
                    rf'\*\*Task Manager project_id:\s*\d+\*\*',
                    pid_line, existing)
                if new != existing:
                    existing = new
                    changed = True
            else:
                # Append
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                existing += f"\n{pid_line}\n"
                changed = True

        # 2. Ensure lifecycle template exists
        if lifecycle_marker not in existing:
            template = TASK_LIFECYCLE_TEMPLATES.get(lang) or TASK_LIFECYCLE_TEMPLATES["en"]
            template = template.replace("JARVIS_PORT", str(port))
            if project_id > 0:
                template = template.replace("PROJECT_ID", str(project_id))
            if existing and not existing.endswith("\n"):
                existing += "\n"
            existing += template
            changed = True

        # Also clean up old HTML comment markers
        if "<!-- task-manager-project-id:" in existing:
            existing = re.sub(
                r'<!-- task-manager-project-id:\d+ -->\n?', '', existing)
            changed = True

        if changed:
            with open(claude_md, "w", encoding="utf-8") as f:
                f.write(existing)

    @staticmethod
    def read_claude_md(project_path: str) -> str:
        claude_md = os.path.join(project_path, "CLAUDE.md")
        if os.path.exists(claude_md):
            with open(claude_md, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    @staticmethod
    def write_claude_md(project_path: str, content: str):
        claude_md = os.path.join(project_path, "CLAUDE.md")
        with open(claude_md, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def _ensure_progress_md(project_path: str):
        progress_md = os.path.join(project_path, "PROGRESS.md")
        if os.path.exists(progress_md):
            return
        with open(progress_md, "w", encoding="utf-8") as f:
            f.write("""# Progress & Lessons Learned

Every time you encounter a problem or complete an important change, record it here:
- What problem was encountered
- How it was resolved
- How to avoid it in the future
- **Must include git commit ID**

**Do not make the same mistake twice!**

---

""")

    @staticmethod
    def read_progress_md(project_path: str) -> str:
        progress_md = os.path.join(project_path, "PROGRESS.md")
        if os.path.exists(progress_md):
            with open(progress_md, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    @staticmethod
    def write_progress_md(project_path: str, content: str):
        progress_md = os.path.join(project_path, "PROGRESS.md")
        with open(progress_md, "w", encoding="utf-8") as f:
            f.write(content)

    def snapshot_projects(self) -> list:
        """Raw projects with their tasks (read-only copy)."""
        with self.lock:
            return self._read()["projects"]

    def get_all_projects(self) -> list:
        with self.lock:
            data = self._read()
            if self._ensure_dev_ports(data):
                self._write(data)
            summaries = [self._project_summary(p) for p in data["projects"]]
        summaries.sort(key=lambda s: s["last_activity_at"], reverse=True)
        return summaries

    def get_project(self, project_id: int) -> Optional[dict]:
        with self.lock:
            data = self._read()
            for p in data["projects"]:
                if p["id"] == project_id:
                    return p
            return None

    @staticmethod
    def _normalize_dev_port(val) -> Optional[int]:
        if val is None or val == "" or isinstance(val, bool):
            return None
        try:
            port = int(val)
        except (TypeError, ValueError):
            return None
        return port if 1 <= port <= 65535 else None

    @classmethod
    def _allocate_dev_port(cls, projects: list,
                           exclude_project_id: Optional[int] = None,
                           reserved_ports: Optional[set] = None) -> int:
        used = set(RESERVED_DEV_PORTS)
        if reserved_ports:
            used.update(p for p in reserved_ports if isinstance(p, int) and 1 <= p <= 65535)
        for p in projects:
            if exclude_project_id is not None and p.get("id") == exclude_project_id:
                continue
            port = cls._normalize_dev_port((p.get("settings") or {}).get("dev_port"))
            if port is not None:
                used.add(port)
        port = BASE_PORT
        while port <= 65535:
            if port not in used:
                return port
            port += 1
        raise ValueError("没有可用的项目端口")

    def _ensure_dev_ports(self, data: dict, reserved_ports: Optional[set] = None) -> bool:
        """Ensure every project has a valid, non-conflicting dev_port."""
        changed = False
        seen = set()
        projects = data.get("projects", [])
        for p in projects:
            settings = p.setdefault("settings", {})
            port = self._normalize_dev_port(settings.get("dev_port"))
            if port is not None and port not in seen:
                seen.add(port)
                if settings.get("dev_port") != port:
                    settings["dev_port"] = port
                    changed = True
            else:
                settings["dev_port"] = None
                new_port = self._allocate_dev_port(
                    projects,
                    exclude_project_id=p.get("id"),
                    reserved_ports=(reserved_ports or set()) | seen,
                )
                settings["dev_port"] = new_port
                seen.add(new_port)
                changed = True
        return changed

    @classmethod
    def _parse_explicit_dev_port(cls, val) -> int:
        if isinstance(val, bool):
            raise ValueError("端口号必须为 1 到 65535 之间的整数")
        try:
            port = int(val)
        except (TypeError, ValueError):
            raise ValueError("端口号必须为 1 到 65535 之间的整数")
        if port < 1 or port > 65535:
            raise ValueError("端口号必须为 1 到 65535 之间的整数")
        return port

    def update_project(self, project_id: int, reserved_ports: Optional[set] = None,
                       **kwargs) -> Optional[dict]:
        with self.lock:
            data = self._read()
            changed = self._ensure_dev_ports(data, reserved_ports=reserved_ports)
            for i, p in enumerate(data["projects"]):
                if p["id"] == project_id:
                    for k, v in kwargs.items():
                        if k in ("name", "path"):
                            data["projects"][i][k] = v
                        elif k == "settings" and isinstance(v, dict):
                            existing = data["projects"][i].get("settings", {})
                            updated = dict(v)
                            if "dev_port" in updated:
                                raw_port = updated["dev_port"]
                                if raw_port is None or (isinstance(raw_port, str) and not raw_port.strip()):
                                    updated["dev_port"] = self._allocate_dev_port(
                                        data["projects"],
                                        exclude_project_id=project_id,
                                        reserved_ports=reserved_ports,
                                    )
                                else:
                                    port = self._parse_explicit_dev_port(raw_port)
                                    if reserved_ports and port in reserved_ports:
                                        raise ValueError(f"端口 {port} 已被 Jarvis 服务占用，请使用其他端口")
                                    for other in data["projects"]:
                                        if other["id"] == project_id:
                                            continue
                                        other_port = self._normalize_dev_port(
                                            (other.get("settings") or {}).get("dev_port")
                                        )
                                        if other_port == port:
                                            raise ValueError(
                                                f"端口 {port} 已被项目「{other['name']}」占用，请换一个端口"
                                            )
                                    updated["dev_port"] = port
                            existing.update(updated)
                            data["projects"][i]["settings"] = existing
                    self._write(data)
                    return self._project_summary(data["projects"][i])
            if changed:
                self._write(data)
            return None

    def delete_project(self, project_id: int) -> bool:
        with self.lock:
            data = self._read()
            original_len = len(data["projects"])
            data["projects"] = [p for p in data["projects"]
                                if p["id"] != project_id]
            if len(data["projects"]) < original_len:
                self._write(data)
                return True
            return False

    @staticmethod
    def _last_activity(project: dict) -> str:
        """Return the most recent timestamp across all tasks, or created_at."""
        latest = project.get("created_at", "")
        for t in project.get("tasks", []):
            for key in ("completed_at", "started_at", "created_at"):
                ts = t.get(key)
                if ts and ts > latest:
                    latest = ts
        return latest

    def _project_summary(self, project: dict) -> dict:
        tasks = project.get("tasks", [])
        path_exists = os.path.isdir(project["path"])
        is_git = os.path.exists(os.path.join(project["path"], ".git"))
        settings = project.get("settings", {})
        return {
            "id": project["id"],
            "name": project["name"],
            "path": project["path"],
            "path_exists": path_exists,
            "is_git": is_git,
            "created_at": project["created_at"],
            "last_activity_at": self._last_activity(project),
            "settings": {
                "skip_permissions": settings.get("skip_permissions", False),
                "auto_commit": settings.get("auto_commit", True),
                "new_task_mode": settings.get("new_task_mode", "auto"),
                "test_command": settings.get("test_command", ""),
                "worktree_setup": settings.get("worktree_setup", ""),
                "max_parallel": self.project_max_parallel(project),
                "dev_port": self._normalize_dev_port(settings.get("dev_port")),
            },
            "task_stats": {
                "total": len(tasks),
                "todo": sum(1 for t in tasks if t["status"] == "todo"),
                "pending": sum(1 for t in tasks if t["status"] == "pending"),
                "in_progress": sum(1 for t in tasks
                                   if t["status"] == "in_progress"),
                "review": sum(1 for t in tasks if t["status"] == "review"),
                "done": sum(1 for t in tasks if t["status"] == "done"),
                "failed": sum(1 for t in tasks if t["status"] == "failed"),
                "cancelled": sum(1 for t in tasks
                                 if t["status"] == "cancelled"),
            },
        }

    # ── Task CRUD (scoped to project) ──

    def add_task(self, project_id: int, description: str,
                 plan_mode: bool = False,
                 predecessor_id: Optional[int] = None,
                 images: Optional[list] = None,
                 model: str = "",
                 agent: str = "",
                 extra: Optional[dict] = None) -> Optional[dict]:
        """agent: claude/codex/antigravity → queued now; "me" → personal todo;
        "" → the project's new_task_mode decides (default: run right away).
        extra: fields set in the same write (e.g. a reply sent from the session
        panel is born in_progress, so no worker can claim it in between)."""
        agent = normalize_agent(agent or model)
        force_todo = agent == "todo"          # "park it in Todo, decide later"
        if agent not in ASSIGNEES:
            agent = ""
        with self.lock:
            data = self._read()
            for i, p in enumerate(data["projects"]):
                if p["id"] != project_id:
                    continue
                if agent in AGENTS:
                    status = "pending"
                elif agent == "me" or force_todo:
                    status = "todo"
                else:
                    mode = p.get("settings", {}).get("new_task_mode", "auto")
                    status = "pending" if mode == "auto" else "todo"
                task_id = p["next_task_id"]
                task = {
                    "id": task_id,
                    "project_id": project_id,
                    "description": description,
                    "status": status,
                    "agent": agent,
                    "plan_mode": plan_mode,
                    "predecessor_id": predecessor_id,
                    "worker_id": None,
                    "branch": None,
                    "created_at": datetime.now().isoformat(),
                    "started_at": None,
                    "completed_at": None,
                    "logs": [],  # Kept for compat; actual logs in JSONL files
                    "plan_content": None,
                    "plan_questions": None,
                    "error": None,
                    "commit_ids": [],
                    "session_id": None,
                    "images": images or [],
                    "model": agent if agent in AGENTS else "",  # legacy mirror of agent
                }
                task.update(extra or {})
                data["projects"][i]["tasks"].append(task)
                data["projects"][i]["next_task_id"] = task_id + 1
                self._write(data)
                return task
            return None

    def get_tasks(self, project_id: int) -> list:
        with self.lock:
            data = self._read()
            for p in data["projects"]:
                if p["id"] == project_id:
                    return p.get("tasks", [])
            return []

    def get_task(self, project_id: int, task_id: int) -> Optional[dict]:
        with self.lock:
            data = self._read()
            for p in data["projects"]:
                if p["id"] == project_id:
                    for t in p.get("tasks", []):
                        if t["id"] == task_id:
                            return t
            return None

    def update_task(self, project_id: int, task_id: int,
                    **kwargs) -> Optional[dict]:
        with self.lock:
            data = self._read()
            for pi, p in enumerate(data["projects"]):
                if p["id"] != project_id:
                    continue
                for ti, t in enumerate(p.get("tasks", [])):
                    if t["id"] == task_id:
                        data["projects"][pi]["tasks"][ti].update(kwargs)
                        self._write(data)
                        return data["projects"][pi]["tasks"][ti]
            return None

    def claim_next_task(self, project_id: int,
                        worker_id: str = "manual",
                        model: str = "") -> Optional[dict]:
        """Claim next pending task for a specific project.
        model: executor claiming it ("claude"/"codex"/"antigravity"; legacy
        "gemini" = antigravity); "" = any. Tasks assigned to someone else are skipped."""
        model = normalize_agent(model)
        with self.lock:
            data = self._read()
            for pi, p in enumerate(data["projects"]):
                if p["id"] != project_id:
                    continue
                tasks = p.get("tasks", [])
                for ti, t in enumerate(tasks):
                    if t["status"] != "pending":
                        continue
                    task_model = task_agent(t)
                    if model and task_model and task_model != model:
                        continue
                    if t.get("predecessor_id"):
                        pred = next((x for x in tasks
                                     if x["id"] == t["predecessor_id"]), None)
                        if pred and pred["status"] not in ("done", "failed", "cancelled", "archived"):
                            continue
                    tasks[ti]["status"] = "in_progress"
                    tasks[ti]["worker_id"] = worker_id
                    tasks[ti]["started_at"] = datetime.now().isoformat()
                    tasks[ti]["agent"] = task_model or model
                    data["projects"][pi]["tasks"] = tasks
                    self._write(data)
                    return tasks[ti]
            return None

    def assign_task(self, project_id: int, task_id: int, agent: str) -> Optional[dict]:
        """Hand a task to an executor (queues it) or to "me" (stays in Todo)."""
        agent = normalize_agent(agent)
        if agent not in ASSIGNEES:
            return None
        task = self.get_task(project_id, task_id)
        if not task or task["status"] not in ("todo", "pending", "failed", "cancelled"):
            return None
        fields = {"agent": agent, "model": agent if agent in AGENTS else ""}
        if agent in AGENTS:
            fields.update(status="pending", error=None, worker_id=None,
                          started_at=None, completed_at=None)
        else:
            fields.update(status="todo")
        return self.update_task(project_id, task_id, **fields)

    @staticmethod
    def project_max_parallel(project: dict) -> int:
        """Git repos isolate tasks in separate worktrees and default to DEFAULT_PROJECT_MAX_WORKERS;
        non-git folders have no worktree isolation and stay serial (1)."""
        path = (project or {}).get("path") or ""
        if not path or not os.path.exists(os.path.join(path, ".git")):
            return 1
        try:
            raw = (project.get("settings") or {}).get("max_parallel")
            val = int(raw) if raw not in (None, "") else DEFAULT_PROJECT_MAX_WORKERS
        except (TypeError, ValueError):
            val = DEFAULT_PROJECT_MAX_WORKERS
        return max(1, min(MAX_WORKERS, val))

    @staticmethod
    def _effective_line(tasks_by_id: dict, task: dict) -> int:
        cur = task
        seen = set()
        while cur and cur.get("id") not in seen:
            seen.add(cur.get("id"))
            if cur.get("line"):
                return cur["line"]
            pid = cur.get("predecessor_id")
            cur = tasks_by_id.get(pid) if pid else None
        return task.get("line") or task["id"]

    def claim_any_task(self, worker_id: str) -> Optional[dict]:
        """Claim next runnable pending task across all projects.
        Git projects run different worktree lines in parallel (up to project_max_parallel);
        tasks on the same line/session and non-git projects stay serial."""
        with self.lock:
            data = self._read()
            candidates = []
            for pi, p in enumerate(data["projects"]):
                tasks = p.get("tasks", [])
                tasks_by_id = {t["id"]: t for t in tasks}
                cap = self.project_max_parallel(p)
                in_prog = [t for t in tasks if t["status"] == "in_progress"]
                worker_running = sum(1 for t in in_prog if t.get("source") != "session")
                if cap <= 1:
                    if in_prog:
                        continue
                elif worker_running >= cap:
                    continue
                busy_lines = {self._effective_line(tasks_by_id, t) for t in in_prog}
                busy_sids = {t.get("session_id") for t in in_prog if t.get("session_id")}
                for ti, t in enumerate(tasks):
                    if t["status"] != "pending":
                        continue
                    pred = None
                    if t.get("predecessor_id"):
                        pred = tasks_by_id.get(t["predecessor_id"])
                        if pred and pred["status"] not in ("done", "failed", "cancelled", "archived"):
                            continue
                    t_line = self._effective_line(tasks_by_id, t)
                    if t_line in busy_lines:
                        continue
                    sid = t.get("session_id") or (pred.get("session_id") if pred else "")
                    if sid and sid in busy_sids:
                        continue
                    candidates.append((worker_running, pi, ti, t_line))
                    break
            if not candidates:
                return None
            _, pi, ti, t_line = min(candidates, key=lambda x: (x[0], x[1]))
            tasks = data["projects"][pi]["tasks"]
            t = tasks[ti]
            t["status"] = "in_progress"
            t["worker_id"] = worker_id
            t["started_at"] = datetime.now().isoformat()
            t["branch"] = f"task-{t_line}"
            t["agent"] = task_agent(t) or "claude"
            self._write(data)
            return t

    # ── JSONL log storage ───────────────────────────────────────────────

    def _log_path(self, project_id: int, task_id: int) -> Path:
        from config import TASK_LOGS_DIR
        d = TASK_LOGS_DIR / str(project_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"task_{task_id}.jsonl"

    def append_log(self, project_id: int, task_id: int,
                   message: str = "", *,
                   type: str = "info", content: str = "",
                   meta: dict = None):
        """Append a structured log entry to JSONL file (O(1) append)."""
        entry = {"time": datetime.now().isoformat()}
        if message and not content:
            entry.update(_parse_legacy_log(message))
        else:
            entry["type"] = type
            entry["content"] = content[:2000]
            if meta:
                entry["meta"] = meta

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._log_path(project_id, task_id), "a") as f:
            f.write(line)

    def get_logs(self, project_id: int, task_id: int,
                 limit: int = 0, offset: int = 0) -> list:
        """Read logs from JSONL file. limit=0 means all."""
        path = self._log_path(project_id, task_id)
        if not path.exists():
            return []
        logs = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    logs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if offset:
            logs = logs[offset:]
        if limit:
            logs = logs[-limit:]
        return logs

    def get_log_count(self, project_id: int, task_id: int) -> int:
        path = self._log_path(project_id, task_id)
        if not path.exists():
            return 0
        with open(path) as f:
            return sum(1 for line in f if line.strip())

    def get_last_log_time(self, project_id: int, task_id: int) -> str:
        """Get timestamp of the last log entry (for stale detection)."""
        path = self._log_path(project_id, task_id)
        if not path.exists():
            return ""
        last_line = ""
        with open(path) as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return ""
        try:
            return json.loads(last_line).get("time", "")
        except json.JSONDecodeError:
            return ""

    def clear_logs(self, project_id: int, task_id: int):
        """Delete log file (used on task retry/reset)."""
        path = self._log_path(project_id, task_id)
        if path.exists():
            path.unlink()

    def delete_task(self, project_id: int, task_id: int) -> bool:
        with self.lock:
            data = self._read()
            for pi, p in enumerate(data["projects"]):
                if p["id"] != project_id:
                    continue
                tasks = p.get("tasks", [])
                original_len = len(tasks)
                tasks = [t for t in tasks if t["id"] != task_id]
                if len(tasks) < original_len:
                    data["projects"][pi]["tasks"] = tasks
                    self._write(data)
                    return True
            return False

    def get_task_stats(self, project_id: int) -> dict:
        tasks = self.get_tasks(project_id)
        return {
            "total": len(tasks),
            "todo": sum(1 for t in tasks if t["status"] == "todo"),
            "pending": sum(1 for t in tasks if t["status"] == "pending"),
            "in_progress": sum(1 for t in tasks
                               if t["status"] == "in_progress"),
            "review": sum(1 for t in tasks if t["status"] == "review"),
            "done": sum(1 for t in tasks if t["status"] in ("done", "archived")),
            "failed": sum(1 for t in tasks if t["status"] == "failed"),
            "cancelled": sum(1 for t in tasks if t["status"] == "cancelled"),
            "archived": sum(1 for t in tasks if t["status"] == "archived"),
        }

    def get_project_path(self, project_id: int) -> Optional[str]:
        with self.lock:
            data = self._read()
            for p in data["projects"]:
                if p["id"] == project_id:
                    return p["path"]
            return None

    def get_analytics(self, project_id: int, days: int = 0) -> Optional[dict]:
        """Aggregate analytics for a project's tasks.
        If days > 0, only include tasks created/completed within that window.
        """
        with self.lock:
            data = self._read()
            project = None
            for p in data["projects"]:
                if p["id"] == project_id:
                    project = p
                    break
            if project is None:
                return None

        tasks = project.get("tasks", [])

        # Filter by time window
        if days > 0:
            cutoff = datetime.now() - timedelta(days=days)
            filtered = []
            for t in tasks:
                ts = t.get("created_at") or t.get("started_at")
                if ts:
                    try:
                        if datetime.fromisoformat(ts) >= cutoff:
                            filtered.append(t)
                            continue
                    except (ValueError, TypeError):
                        pass
                # Also include if completed within window
                ca = t.get("completed_at")
                if ca:
                    try:
                        if datetime.fromisoformat(ca) >= cutoff:
                            filtered.append(t)
                            continue
                    except (ValueError, TypeError):
                        pass
            tasks = filtered

        # ── Status counts ──
        status_counts = {}
        for t in tasks:
            s = t["status"]
            status_counts[s] = status_counts.get(s, 0) + 1

        total = len(tasks)
        done = status_counts.get("done", 0)
        failed = status_counts.get("failed", 0)
        finished = done + failed
        success_rate = round(done / finished, 4) if finished else None

        # ── Duration stats (seconds) ──
        durations = []
        for t in tasks:
            if t.get("started_at") and t.get("completed_at"):
                try:
                    s = datetime.fromisoformat(t["started_at"])
                    e = datetime.fromisoformat(t["completed_at"])
                    durations.append((e - s).total_seconds())
                except (ValueError, TypeError):
                    pass

        avg_duration = round(sum(durations) / len(durations), 1) if durations else None
        min_duration = round(min(durations), 1) if durations else None
        max_duration = round(max(durations), 1) if durations else None

        # ── Cost from [DIAG] log entries (read from JSONL) ──
        total_cost = 0.0
        for t in tasks:
            for log in self.get_logs(project_id, t["id"]):
                if log.get("type") == "diag":
                    try:
                        diag = json.loads(log["content"])
                        total_cost += diag.get("total_cost_usd", 0)
                    except (json.JSONDecodeError, TypeError):
                        pass

        # ── Daily trend (by completed_at date) ──
        daily: dict[str, dict] = {}
        for t in tasks:
            ca = t.get("completed_at")
            if not ca:
                continue
            try:
                day = datetime.fromisoformat(ca).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if day not in daily:
                daily[day] = {"date": day, "done": 0, "failed": 0}
            if t["status"] == "done":
                daily[day]["done"] += 1
            elif t["status"] == "failed":
                daily[day]["failed"] += 1

        trend = sorted(daily.values(), key=lambda d: d["date"])

        # ── Per executor ──
        by_agent: dict[str, dict] = {}
        for t in tasks:
            a = task_agent(t) or "unknown"
            row = by_agent.setdefault(a, {"done": 0, "failed": 0, "durations": []})
            if t["status"] in ("done", "archived"):
                row["done"] += 1
            elif t["status"] == "failed":
                row["failed"] += 1
            if t.get("started_at") and t.get("completed_at"):
                try:
                    row["durations"].append((datetime.fromisoformat(t["completed_at"])
                                             - datetime.fromisoformat(t["started_at"])).total_seconds())
                except (ValueError, TypeError):
                    pass
        for row in by_agent.values():
            d = row.pop("durations")
            row["avg_seconds"] = round(sum(d) / len(d), 1) if d else None
            fin = row["done"] + row["failed"]
            row["success_rate"] = round(row["done"] / fin, 4) if fin else None

        return {
            "total_tasks": total,
            "status_counts": status_counts,
            "success_rate": success_rate,
            "duration": {
                "avg_seconds": avg_duration,
                "min_seconds": min_duration,
                "max_seconds": max_duration,
                "sample_size": len(durations),
            },
            "total_cost_usd": round(total_cost, 4),
            "daily_trend": trend,
            "by_agent": by_agent,
        }

    def get_project_settings(self, project_id: int) -> dict:
        with self.lock:
            data = self._read()
            for p in data["projects"]:
                if p["id"] == project_id:
                    return p.get("settings", {})
            return {}


# ── Module-level helpers (outside class) ─────────────────────────────────

def _parse_legacy_log(msg_raw: str) -> dict:
    """Convert old-style '[prefix] ...' string logs to structured format."""
    msg = msg_raw[:2000]
    if msg.startswith("[init] "):
        return {"type": "init", "content": msg[7:]}
    if msg.startswith("[tool_use] "):
        rest = msg[11:]
        tool_name = rest.split(":")[0].split("(")[0].strip()
        return {"type": "tool_use", "content": rest,
                "meta": {"tool_name": tool_name, "status": "created"}}
    if msg.startswith("[tool_ok] "):
        rest = msg[10:]
        tool_name = rest.split(":")[0].strip()
        return {"type": "tool_ok", "content": rest,
                "meta": {"tool_name": tool_name, "status": "success"}}
    if msg.startswith("[tool_error] "):
        return {"type": "tool_error", "content": msg[13:],
                "meta": {"status": "failed"}}
    if msg.startswith("[git] "):
        return {"type": "git", "content": msg[6:]}
    if msg.startswith("[result] "):
        return {"type": "result", "content": msg[9:]}
    if msg.startswith("[stderr] "):
        return {"type": "error", "content": msg[9:]}
    if msg.startswith("[rate_limit] "):
        return {"type": "rate_limit", "content": msg[13:]}
    if msg.startswith("[resume] "):
        return {"type": "info", "content": msg[9:]}
    if msg.startswith("[DIAG] "):
        return {"type": "diag", "content": msg[7:]}
    return {"type": "message", "content": msg}
