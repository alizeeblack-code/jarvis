"""WorkerProcess — executes a single task (Claude / Codex / Antigravity) with git lifecycle"""

import asyncio
import json
import os
from datetime import datetime

from agents import AgentRunnersMixin
from config import task_agent
import gitflow
from transcript import claude_effort, find_log, resume_args, session_settings
from usage import tracker as usage_tracker
from utils import extract_questions, summarize_tool_input


class WorkerProcess(AgentRunnersMixin):
    """Manages a single worker instance; the executor comes from the task's `agent`."""

    def __init__(self, worker_id, task, project_path, store, broadcast_fn,
                 project_settings=None):
        self.worker_id = worker_id
        self.task = task
        self.task_id = task["id"]
        self.project_id = task["project_id"]
        self.project_path = project_path
        self.store = store
        self.broadcast = broadcast_fn
        self.project_settings = project_settings or {}
        self.process = None
        self.cancelled = False
        # Diagnostics
        self.tool_calls = []
        self.error_count = 0
        self.turn_count = 0
        self.total_cost = 0.0
        self.model = ""
        self.session_id = ""
        self.result_summary = ""
        self._last_message = ""
        self.agent = task_agent(task) or "claude"
        self.codex_usage = {}
        self._popen = None

    # ── Main lifecycle ──────────────────────────────────────────────────

    async def run(self):
        """Run the task in its line's own worktree (gitflow.py): the project folder
        is never switched or cleaned, and the branch waits for "合并到 main"."""
        self.repo_path = self.project_path
        self.agent_env = await asyncio.to_thread(gitflow.agent_env, self.repo_path)
        use_wt = gitflow.is_repo(self.repo_path) and not self.task.get("plan_mode")
        line = None

        try:
            await self._broadcast_status("started", "Worker started")

            if use_wt:
                line = self._line()
                path, created = await asyncio.to_thread(gitflow.ensure_worktree, self.repo_path, line)
                if created:
                    await asyncio.to_thread(gitflow.setup_worktree, self.repo_path, path,
                                            self.project_settings.get("worktree_setup", ""))
                self.store.update_task(self.project_id, self.task_id, line=line,
                                       branch=gitflow.branch_name(line), worktree=path)
                self.project_path = path
                self.store.append_log(self.project_id, self.task_id,
                                      f"[git] {'created' if created else 'reusing'} worktree {path} "
                                      f"on branch {gitflow.branch_name(line)}")
                await self._broadcast_event("git", {"action": "worktree", "branch": gitflow.branch_name(line)})

            prompt = self._build_prompt()
            if self.agent == "codex":
                await self._run_codex(prompt)
            elif self.agent == "antigravity":
                await self._run_antigravity(prompt)
            else:
                await self._run_claude(prompt)

            if self.cancelled:
                # stopped from the page (or interrupted by a new instruction): close it here,
                # keeping the session so a follow-up can resume it
                self.store.update_task(self.project_id, self.task_id, status="cancelled",
                                       completed_at=datetime.now().isoformat(),
                                       session_id=self.session_id or None,
                                       summary=self.result_summary or None)
                await self._broadcast_status("cancelled", "Task stopped")
                return

            if self.session_id:
                self.store.update_task(self.project_id, self.task_id,
                                       session_id=self.session_id)

            diag = self._build_diagnostics()
            self.store.append_log(self.project_id, self.task_id,
                                  f"[DIAG] {json.dumps(diag, ensure_ascii=False)}")

            if self.task.get("plan_mode"):
                task_data = self.store.get_task(self.project_id, self.task_id)
                plan_text = (task_data.get("plan_content", "")
                             if task_data else "")
                # Try JSON extraction first, fall back to regex
                from utils import (extract_plan_json, extract_questions,
                                   extract_subtasks)
                plan_data = extract_plan_json(plan_text)
                if plan_data:
                    subtasks = plan_data.get("subtasks", [])
                    questions = plan_data.get("questions", [])
                else:
                    subtasks = extract_subtasks(plan_text)
                    questions = extract_questions(plan_text)
                self.store.update_task(self.project_id, self.task_id,
                                       status="review",
                                       plan_questions=questions,
                                       plan_subtasks=subtasks,
                                       plan_feedback=None)
                await self._broadcast_status("review", "Plan ready for review")
            else:
                extra = {}
                if use_wt:
                    await asyncio.to_thread(gitflow.commit_all, self.project_path,
                                            f"{gitflow.branch_name(line)}: {self.task['description'][:60]}")
                    info = await asyncio.to_thread(gitflow.ahead, self.repo_path, line)
                    if info["commits"]:
                        extra = {"merge_state": "pending", "commit_ids": info["ids"]}
                        self.store.append_log(self.project_id, self.task_id,
                                              f"[git] {info['commits']} commit(s) on {info['branch']} "
                                              "waiting to be merged")
                    else:
                        # nothing to keep; a follow-up recreates the same folder from main
                        await asyncio.to_thread(gitflow.remove_line, self.repo_path, line)
                        extra = {"merge_state": "none"}
                self.store.update_task(
                    self.project_id, self.task_id, status="done",
                    completed_at=datetime.now().isoformat(),
                    summary=self.result_summary or None, **extra)
                await self._broadcast_status("done", "Task completed")

        except asyncio.CancelledError:
            self.cancelled = True
            self.store.update_task(self.project_id, self.task_id,
                                   status="cancelled",
                                   session_id=self.session_id or None)
            await self._broadcast_status("cancelled", "Task cancelled")
        except Exception as e:
            error_msg = str(e)[:500]
            diag = self._build_diagnostics()
            diag["fatal_error"] = error_msg
            self.store.update_task(self.project_id, self.task_id,
                                   status="failed", error=error_msg,
                                   session_id=self.session_id or None)
            self.store.append_log(self.project_id, self.task_id,
                                  f"[DIAG] {json.dumps(diag, ensure_ascii=False)}")
            await self._broadcast_status("failed", error_msg)

    def _line(self) -> int:
        """The line of work this task belongs to: a follow-up continues its
        predecessor's line (same branch and folder, so the session can resume)."""
        pred_id = self.task.get("predecessor_id")
        if pred_id:
            pred = self.store.get_task(self.project_id, pred_id)
            if pred and pred.get("line"):
                return pred["line"]
        return self.task.get("line") or self.task_id

    # ── Prompt & session ────────────────────────────────────────────────

    def _get_predecessor_session(self):
        """Predecessor's session id to resume — only when it ran on the same executor."""
        pred_id = self.task.get("predecessor_id")
        if not pred_id:
            return None
        pred_task = self.store.get_task(self.project_id, pred_id)
        if not pred_task or (task_agent(pred_task) or "claude") != self.agent:
            return None
        return pred_task.get("session_id")

    def _build_prompt(self):
        desc = self.task["description"]
        has_predecessor_session = bool(self._get_predecessor_session())
        images = self.task.get("images") or []
        image_section = ""
        if images:
            paths = "\n".join(f"- {img['path']}" for img in images)
            image_section = (
                f"\n\n[Attached Images]\n"
                f"The user attached image files. Use the Read tool to view them:\n"
                f"{paths}"
            )

        # When resuming a session, keep prompt short — context is already there
        if has_predecessor_session and not self.task.get("plan_mode"):
            pred_id = self.task.get("predecessor_id")
            return (
                f"继续工作（基于任务 #{pred_id} 的上下文）。\n\n"
                f"新任务: {desc}{image_section}"
            )

        if self.task.get("plan_mode"):
            plan_feedback = self.task.get("plan_feedback")
            if plan_feedback and self.task.get("session_id"):
                return (
                    f"用户对你之前的方案有以下反馈，请根据反馈修改计划：\n\n"
                    f"反馈: {plan_feedback}{image_section}\n\n"
                    f"请重新输出完整的修改后的 JSON 计划（格式与之前相同）。"
                )
            return (
                f"[Plan Mode] 分析任务并创建实施计划。不要动手实现，只做规划。\n\n"
                f"任务: {desc}{image_section}\n\n"
                f"请先用自然语言描述你的分析和方案，然后在最后输出一个 JSON 代码块，格式如下：\n\n"
                f"```json\n"
                f'{{\n'
                f'  "summary": "概述：做什么、为什么做",\n'
                f'  "subtasks": [\n'
                f'    {{\n'
                f'      "title": "子任务标题",\n'
                f'      "description": "详细描述，足够让另一个 Claude Code 独立执行",\n'
                f'      "files": ["file1.py", "file2.js"],\n'
                f'      "depends_on": []\n'
                f'    }},\n'
                f'    {{\n'
                f'      "title": "第二个子任务",\n'
                f'      "description": "描述",\n'
                f'      "files": [],\n'
                f'      "depends_on": [1]\n'
                f'    }}\n'
                f'  ],\n'
                f'  "questions": [\n'
                f'    {{\n'
                f'      "question": "需要确认的问题",\n'
                f'      "suggestion": "你的建议（可选）"\n'
                f'    }}\n'
                f'  ],\n'
                f'  "risks": ["风险1", "风险2"]\n'
                f'}}\n'
                f"```\n\n"
                f"要求:\n"
                f"- depends_on 是子任务序号数组（从 1 开始），空数组表示无依赖\n"
                f"- 子任务粒度适中（每个 1-3 个文件改动）\n"
                f"- 描述要详细，让另一个 Claude Code 能独立执行"
            )
        if not os.path.exists(os.path.join(self.project_path, "PROGRESS.md")):
            return desc + image_section      # folder registered without Jarvis docs
        return (desc + image_section +
                "\n\n[完成后] 如果遇到了问题或有值得记录的经验，"
                "请追加到 PROGRESS.md（包含问题、解决方式、commit ID）。")

    # ── Claude Code execution ───────────────────────────────────────────

    async def _run_claude(self, prompt):
        """Run Claude Code with bidirectional stdin/stdout protocol.

        Uses subprocess.Popen (not asyncio.create_subprocess_exec) to avoid
        uvloop's stdin pipe bugs (https://github.com/MagicStack/uvloop/issues/312).
        Stdout is read in a thread and fed to the async event loop via a queue.
        """
        import subprocess
        import threading
        import uuid as _uuid

        skip_perms = self.project_settings.get("skip_permissions")

        if skip_perms:
            cmd = ["claude", "-p", prompt,
                   "--output-format", "stream-json", "--verbose",
                   "--dangerously-skip-permissions"]
        else:
            cmd = ["claude",
                   "--output-format", "stream-json",
                   "--input-format", "stream-json",
                   "--verbose",
                   "--permission-prompt-tool", "stdio",
                   "--permission-mode", "bypassPermissions"]

        # Resume predecessor session if available (reuse context)
        predecessor_session = self._get_predecessor_session()
        # For plan revisions, resume the task's own session
        if (not predecessor_session
                and self.task.get("plan_feedback")
                and self.task.get("session_id")):
            predecessor_session = self.task["session_id"]
        if not predecessor_session:
            cmd.extend(["--effort", claude_effort()])
        if predecessor_session:
            # `--resume` looks in this folder's session store; bring the log over if it ran elsewhere
            await asyncio.to_thread(gitflow.carry_claude_session, predecessor_session,
                                    self.project_path, find_log)
            model, effort = session_settings(predecessor_session, "claude")
            keep = resume_args("claude", model, effort)
            cmd.extend([*keep, "--resume", predecessor_session])
            self.store.append_log(self.project_id, self.task_id,
                                  f"[resume] session: {predecessor_session[:16]}..."
                                  + f" (effort {claude_effort(effort)})")

        self.store.append_log(self.project_id, self.task_id,
                              f"Running in: {self.project_path}")
        await self._broadcast_status("running",
                                     f"Claude Code running in {self.project_path}")

        try:
            # Use subprocess.Popen to bypass uvloop's broken stdin pipes
            proc = subprocess.Popen(
                cmd,
                env=getattr(self, "agent_env", None),
                stdin=subprocess.PIPE if not skip_perms else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.project_path)
            self._popen = proc
            self._record_pid(proc.pid)

            # Bidirectional mode: send initialize + prompt via stdin
            if not skip_perms and proc.stdin:
                init_msg = json.dumps({
                    "type": "control_request",
                    "request_id": str(_uuid.uuid4()),
                    "request": {"subtype": "initialize"}
                }) + "\n"
                proc.stdin.write(init_msg.encode())
                proc.stdin.flush()

                import time
                time.sleep(1)

                user_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": prompt}
                }) + "\n"
                proc.stdin.write(user_msg.encode())
                proc.stdin.flush()

            # Read stdout in a background thread, push lines to async queue
            line_queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _reader():
                try:
                    for raw_line in proc.stdout:
                        loop.call_soon_threadsafe(
                            line_queue.put_nowait, raw_line)
                except Exception:
                    pass
                loop.call_soon_threadsafe(
                    line_queue.put_nowait, None)  # sentinel

            reader_thread = threading.Thread(
                target=_reader, daemon=True)
            reader_thread.start()

            plan_content = []
            while True:
                raw_line = await line_queue.get()
                if raw_line is None:
                    break  # EOF
                if self.cancelled:
                    proc.terminate()
                    return

                line_str = raw_line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    event = json.loads(line_str)
                except json.JSONDecodeError:
                    if line_str:
                        self.store.append_log(
                            self.project_id, self.task_id, line_str[:200])
                    continue

                # Handle control requests (permission prompts) via stdin
                if (event.get("type") == "control_request"
                        and proc.stdin):
                    self._handle_control_request_sync(proc.stdin, event)
                else:
                    await self._handle_event(event, plan_content)

            # Close stdin after stdout EOF to ensure Claude process exits.
            # Without this, proc.wait() hangs forever in bidirectional mode.
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            proc.wait(timeout=30)
            reader_thread.join(timeout=5)

            if self.task.get("plan_mode") and plan_content:
                self.store.update_task(
                    self.project_id, self.task_id,
                    plan_content="\n".join(plan_content))

            if proc.returncode and proc.returncode != 0:
                err_msg = proc.stderr.read().decode(
                    "utf-8", errors="replace")[:500]
                if err_msg:
                    self.store.append_log(self.project_id, self.task_id,
                                          f"[stderr] {err_msg}")
                    raise RuntimeError(
                        f"Exit code {proc.returncode}: {err_msg}")

        except FileNotFoundError:
            raise RuntimeError("Claude Code CLI not found")

    # ── Bidirectional protocol (sync, for Popen stdin) ───────────────────

    @staticmethod
    def _handle_control_request_sync(stdin, event):
        """Auto-approve permission requests via stdin (synchronous)."""
        request_id = event.get("request_id", "")
        request = event.get("request", {})
        subtype = request.get("subtype", "")

        if subtype == "can_use_tool":
            response = {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {
                        "behavior": "allow",
                        "updatedInput": request.get("input", {})
                    }
                }
            }
        elif subtype == "hook_callback":
            response = {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "permissionDecisionReason": "Auto-approved"
                        }
                    }
                }
            }
        else:
            response = {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": None
                }
            }

        data = json.dumps(response) + "\n"
        stdin.write(data.encode())
        stdin.flush()

    # ── Event parsing & diagnostics ─────────────────────────────────────

    async def _handle_event(self, event, plan_content):
        etype = event.get("type", "")
        _log = self.store.append_log  # shorthand

        if etype == "system" and event.get("subtype") == "init":
            self.session_id = event.get("session_id", "")
            self.model = event.get("model", "")
            tools = event.get("tools", [])
            # Store session_id immediately so it's saved even if worker dies
            if self.session_id:
                self.store.update_task(self.project_id, self.task_id,
                                       session_id=self.session_id)
            _log(self.project_id, self.task_id,
                 type="init",
                 content=f"model={self.model} tools={len(tools)}",
                 meta={"model": self.model, "tools_count": len(tools),
                       "session_id": self.session_id})
            await self._broadcast_event("init", {"model": self.model})

        elif etype == "assistant" and "message" in event:
            self.turn_count += 1
            msg = event.get("message", {})
            if not isinstance(msg, dict):
                return
            for block in msg.get("content", []):
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "")
                    if text:
                        plan_content.append(text)
                        self._last_message = text  # fallback for summary
                        _log(self.project_id, self.task_id,
                             type="message", content=text)
                        await self._broadcast_log(text[:500])
                elif btype == "tool_use":
                    name = block.get("name", "unknown")
                    tool_id = block.get("id", "")
                    summary = summarize_tool_input(
                        name, block.get("input", {}))
                    self.tool_calls.append({
                        "tool": name, "tool_use_id": tool_id,
                        "input_summary": summary, "is_error": False})
                    _log(self.project_id, self.task_id,
                         type="tool_use",
                         content=f"{name}: {summary}",
                         meta={"tool_name": name, "status": "created",
                               "tool_use_id": tool_id,
                               "input_summary": summary})
                    await self._broadcast_event(
                        "tool_use", {"tool": name, "input": summary})

        elif etype == "user":
            msg = event.get("message", {})
            if isinstance(msg, dict):
                for block in msg.get("content", []):
                    if block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        is_err = block.get("is_error", False)
                        content = block.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(
                                b.get("text", "") for b in content
                                if b.get("type") == "text")
                        tool_name = ""
                        for tc in reversed(self.tool_calls):
                            if tc.get("tool_use_id") == tid:
                                tc["is_error"] = is_err
                                tool_name = tc["tool"]
                                break
                        if is_err:
                            self.error_count += 1
                            _log(self.project_id, self.task_id,
                                 type="tool_error",
                                 content=str(content)[:500],
                                 meta={"tool_name": tool_name,
                                       "status": "failed",
                                       "tool_use_id": tid})
                            await self._broadcast_event(
                                "tool_error",
                                {"error": str(content)[:500]})
                        else:
                            self.error_count = 0
                            preview = str(content)[:300]
                            if preview:
                                _log(self.project_id, self.task_id,
                                     type="tool_ok",
                                     content=f"{tool_name}: {preview}",
                                     meta={"tool_name": tool_name,
                                           "status": "success",
                                           "tool_use_id": tid})

        elif etype == "result":
            self.total_cost = event.get("total_cost_usd", 0)
            result_text = event.get("result", "")
            turns = event.get("num_turns", 0)
            _log(self.project_id, self.task_id,
                 type="result",
                 content=f"{event.get('subtype','')} | turns={turns} cost=${self.total_cost:.4f}",
                 meta={"turns": turns, "cost": self.total_cost,
                       "subtype": event.get("subtype", "")})
            if result_text:
                self.result_summary = result_text
            elif not self.result_summary and hasattr(self, '_last_message'):
                # Fallback: use last assistant message as summary
                self.result_summary = self._last_message
            if event.get("is_error"):
                raise RuntimeError(f"Claude Code error: {result_text[:300]}")
            # Close stdin so Claude process exits (bidirectional mode keeps
            # the process alive waiting for more stdin input)
            if hasattr(self, '_popen') and self._popen and self._popen.stdin:
                try:
                    self._popen.stdin.close()
                except Exception:
                    pass
            await self._broadcast_event("result", {
                "turns": turns, "cost": self.total_cost})

        elif etype == "rate_limit_event":
            info = event.get("rate_limit_info", {})
            usage_tracker.record_claude(info)
            if info.get("status") != "allowed":
                _log(self.project_id, self.task_id,
                     type="rate_limit",
                     content=info.get("status", ""),
                     meta=info)

    def _build_diagnostics(self):
        tool_summary = {}
        for tc in self.tool_calls:
            name = tc["tool"]
            tool_summary.setdefault(name, {"count": 0, "errors": 0})
            tool_summary[name]["count"] += 1
            if tc.get("is_error"): tool_summary[name]["errors"] += 1

        problems = []
        for name, stats in tool_summary.items():
            if stats["errors"] > 2:
                problems.append(f"Tool '{name}' failed {stats['errors']}/{stats['count']} times")
        if self.error_count > 3:
            problems.append(f"High consecutive error count: {self.error_count}")
        if self.turn_count > 20:
            problems.append(f"Excessive turns ({self.turn_count}), possible loop")
        if len(self.tool_calls) >= 4:
            last4 = [f"{tc['tool']}:{tc.get('input_summary','')}" for tc in self.tool_calls[-4:]]
            if len(set(last4)) == 1:
                problems.append(f"Loop detected: {self.tool_calls[-1]['tool']}")

        return {"session_id": self.session_id, "model": self.model, "agent": self.agent,
                "usage": self.codex_usage or None,
                "turns": self.turn_count, "total_cost_usd": round(self.total_cost, 4),
                "tool_summary": tool_summary, "total_tool_calls": len(self.tool_calls),
                "problems": problems}

    # ── Instance methods ─────────────────────────────────────────────────

    async def cancel(self):
        self.cancelled = True
        if self._popen and self._popen.poll() is None:
            self._popen.terminate()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try: await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError: self.process.kill()

    async def _broadcast_status(self, status, message):
        await self.broadcast({"type": "task_update", "task_id": self.task_id,
                              "project_id": self.project_id,
                              "worker_id": self.worker_id,
                              "status": status, "message": message,
                              "timestamp": datetime.now().isoformat()})

    async def _broadcast_log(self, message):
        await self.broadcast({"type": "task_log", "task_id": self.task_id,
                              "project_id": self.project_id,
                              "message": message,
                              "timestamp": datetime.now().isoformat()})

    async def _broadcast_event(self, event_name, data):
        await self.broadcast({"type": "task_event", "task_id": self.task_id,
                              "project_id": self.project_id,
                              "event": event_name, "data": data,
                              "timestamp": datetime.now().isoformat()})
