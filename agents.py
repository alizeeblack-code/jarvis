"""Non-Claude executors for WorkerProcess: Codex CLI and Antigravity CLI (agy).

Both reuse the worker's git lifecycle (branch → run → auto-commit → merge);
only the "run the agent" step differs.
"""

import asyncio
import json
import os
import subprocess
import threading
import time

import gitflow
from transcript import (AgyLog, agy_kind, agy_last_input_tokens, agy_step, agy_trailing_glitch,
                        resume_args, session_settings)
from usage import agy_quota_error, tracker as usage_tracker

AGY_TIMEOUT_MIN = 60          # agy --print-timeout
AGY_HARD_LIMIT_MIN = 70       # our own kill switch (agy can hang before its timeout starts)
AGY_FRESH_TOKENS = 1_000_000  # a conversation that has read more than this in all starts over
CODEX_HARD_LIMIT_MIN = 180


class AgentRunnersMixin:
    """Expects WorkerProcess attributes: store, project_id, task_id, task,
    project_path, project_settings, cancelled, session_id, result_summary,
    _last_message, tool_calls, turn_count, error_count, _log helpers."""

    def _record_pid(self, pid: int):
        """The executor's process id on the task: it can outlive a server restart,
        and the restarted server waits for it before settling the task."""
        store = getattr(self, "store", None)
        try:
            if store is not None:
                store.update_task(self.project_id, self.task_id, agent_pid=pid)
        except Exception:                                # noqa: BLE001
            pass

    # ── shared: stream a subprocess line by line ──
    async def _stream(self, cmd, stdin_text=None, hard_limit_min=60, on_line=None):
        proc = subprocess.Popen(
            cmd, cwd=self.project_path, env=getattr(self, "agent_env", None),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._popen = proc
        self._record_pid(proc.pid)
        if stdin_text is not None:
            try:
                proc.stdin.write(stdin_text.encode())
                proc.stdin.close()
            except BrokenPipeError:
                pass

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def reader():
            try:
                for raw in proc.stdout:
                    loop.call_soon_threadsafe(queue.put_nowait, raw)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                pass            # the run already ended (killed) and its loop is closed

        threading.Thread(target=reader, daemon=True).start()
        deadline = time.monotonic() + hard_limit_min * 60
        while True:
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=5)
            except asyncio.TimeoutError:
                raw = ...
            if self.cancelled:
                proc.terminate()
                return proc
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError(f"{cmd[0]} 超过 {hard_limit_min} 分钟没结束，已终止")
            if raw is ...:
                continue
            if raw is None:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line.strip() and on_line:
                await on_line(line)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        return proc

    def _stderr_tail(self, proc) -> str:
        try:
            return proc.stderr.read().decode("utf-8", errors="replace").strip()[-500:]
        except Exception:
            return ""

    # ── Codex ──
    async def _run_codex(self, prompt):
        skip = self.project_settings.get("skip_permissions")
        cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", self.project_path]
        cmd += ["--dangerously-bypass-approvals-and-sandbox"] if skip else ["-s", "workspace-write", "-c", "sandbox_workspace_write.network_access=true"]
        cmd += gitflow.codex_optional_mcp(self.project_path)    # a down MCP server shouldn't stop the task
        for img in self.task.get("images") or []:
            if img.get("path"):
                cmd += ["-i", img["path"]]
        resume = self._resume_session_for("codex")
        if resume:
            model, effort = session_settings(resume, "codex")
            cmd += ["resume", *resume_args("codex", model, effort), resume]
            self._log_text(f"[resume] codex thread: {resume[:16]}... ({model or 'default model'} · {effort or 'default effort'})")
        cmd.append("-")                      # prompt from stdin
        if not skip and not self.task.get("plan_mode"):
            # workspace-write keeps .git read-only; the worker commits afterwards.
            prompt += ("\n\n[Note] You run in a sandbox where .git is read-only: change files only, do not "
                       "run git commit and do not report a failed commit; the task manager commits afterwards.")
        self._log_text(f"Running Codex in: {self.project_path}")
        await self._broadcast_status("running", f"Codex running in {self.project_path}")

        state = {"usage": {}, "fatal": "", "texts": []}

        async def on_line(line):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                self._log_text(line[:200])
                return
            await self._handle_codex_event(ev, state)

        try:
            proc = await self._stream(cmd, stdin_text=prompt, hard_limit_min=CODEX_HARD_LIMIT_MIN,
                                      on_line=on_line)
        except FileNotFoundError:
            raise RuntimeError("Codex CLI not found (codex)")
        if self.cancelled:
            return
        if self.task.get("plan_mode") and state["texts"]:
            self.store.update_task(self.project_id, self.task_id,
                                   plan_content="\n".join(state["texts"]))
        self.result_summary = self._last_message or self.result_summary
        self.codex_usage = state["usage"]
        if state["fatal"]:
            raise RuntimeError(f"Codex error: {state['fatal'][:300]}")
        if proc.returncode:
            err = self._stderr_tail(proc)
            if "active writer" in err:                   # thread open in an interactive codex
                from session_send import codex_busy_message
                raise RuntimeError(codex_busy_message(""))
            raise RuntimeError(f"Codex exit code {proc.returncode}: {err}")

    async def _handle_codex_event(self, ev, state):
        et = ev.get("type", "")
        log = self.store.append_log
        if et == "thread.started":
            self.session_id = ev.get("thread_id", "")
            self.model = "codex"
            if self.session_id:
                self.store.update_task(self.project_id, self.task_id, session_id=self.session_id)
            log(self.project_id, self.task_id, type="init", content="codex",
                meta={"model": "codex", "session_id": self.session_id})
            await self._broadcast_event("init", {"model": "codex"})
        elif et in ("item.started", "item.completed"):
            item = ev.get("item") or {}
            it = item.get("type", "")
            done = et == "item.completed"
            if it == "agent_message" and done:
                text = item.get("text", "")
                if text:
                    self.turn_count += 1
                    self._last_message = text
                    state["texts"].append(text)
                    log(self.project_id, self.task_id, type="message", content=text)
                    await self._broadcast_log(text[:500])
            elif it == "command_execution":
                cmd = str(item.get("command", ""))[:300]
                if not done:
                    self.tool_calls.append({"tool": "Bash", "tool_use_id": item.get("id", ""),
                                            "input_summary": cmd, "is_error": False})
                    log(self.project_id, self.task_id, type="tool_use", content=f"Bash: {cmd}",
                        meta={"tool_name": "Bash", "status": "created",
                              "tool_use_id": item.get("id", ""), "input_summary": cmd})
                    await self._broadcast_event("tool_use", {"tool": "Bash", "input": cmd})
                else:
                    failed = item.get("exit_code") not in (0, None)
                    out = str(item.get("aggregated_output", ""))[:300]
                    if failed:
                        self.error_count += 1
                        for tc in reversed(self.tool_calls):
                            if tc["tool_use_id"] == item.get("id"):
                                tc["is_error"] = True
                                break
                        log(self.project_id, self.task_id, type="tool_error",
                            content=f"exit {item.get('exit_code')}: {out}",
                            meta={"tool_name": "Bash", "status": "failed"})
                        await self._broadcast_event("tool_error", {"error": out})
                    else:
                        self.error_count = 0
                        if out:
                            log(self.project_id, self.task_id, type="tool_ok",
                                content=f"Bash: {out}", meta={"tool_name": "Bash", "status": "success"})
            elif it == "file_change" and done:
                paths = ", ".join(c.get("path", "") for c in item.get("changes", []))[:300]
                self.tool_calls.append({"tool": "Edit", "tool_use_id": item.get("id", ""),
                                        "input_summary": paths, "is_error": False})
                log(self.project_id, self.task_id, type="tool_use", content=f"Edit: {paths}",
                    meta={"tool_name": "Edit", "status": "created", "input_summary": paths})
                await self._broadcast_event("tool_use", {"tool": "Edit", "input": paths})
            elif it in ("mcp_tool_call", "web_search") and not done:
                name = item.get("tool") or item.get("query") or it
                log(self.project_id, self.task_id, type="tool_use", content=f"{it}: {name}"[:300],
                    meta={"tool_name": it, "status": "created"})
            elif it == "error" and done:
                # non-fatal notice (e.g. hook warnings); real failures come as turn.failed
                log(self.project_id, self.task_id, type="info",
                    content=f"[codex] {item.get('message', '')}"[:300])
        elif et == "turn.completed":
            u = ev.get("usage") or {}
            for k, v in u.items():
                if isinstance(v, (int, float)):
                    state["usage"][k] = state["usage"].get(k, 0) + v
            log(self.project_id, self.task_id, type="result",
                content=f"turn completed | out={u.get('output_tokens', 0)} in={u.get('input_tokens', 0)}",
                meta={"usage": u})
            await self._broadcast_event("result", {"usage": u})
        elif et == "turn.failed":
            state["fatal"] = (ev.get("error") or {}).get("message", "turn failed")
        elif et == "error":
            state["fatal"] = ev.get("message", "error")

    # ── Antigravity ──
    async def _run_antigravity(self, prompt):
        """Runs on the task's model (Hydrogen unless it asks for another); a turn
        that runs out of quota goes again, in the same conversation, on the next
        model whose quota is left."""
        resume = self._resume_session_for("antigravity")
        choice = self.task.get("agy_model") or (session_settings(resume, "antigravity")[0] if resume else "")
        used = agy_last_input_tokens(resume) if resume else 0
        if used > AGY_FRESH_TOKENS:
            # a long conversation is slow and costly to carry on, and the model starts
            # tripping over its own tool calls — carry a short note instead
            self._log_text(f"[antigravity] conversation {resume[:8]} has read {used / 1e6:.1f}M tokens; starting a new one")
            prompt = self._agy_carry_note(resume) + prompt
            resume = None
        models = usage_tracker.agy_models(choice)
        if resume:
            self._log_text(f"[resume] antigravity conversation: {resume[:16]}...")
        self._log_text(f"Running Antigravity in: {self.project_path}")
        await self._broadcast_status("running", f"Antigravity running in {self.project_path}")
        self.model = "antigravity"
        while True:
            model = models.pop(0)
            try:
                return await self._agy_once(prompt, resume or self.session_id or "", model)
            except RuntimeError as e:
                if self.cancelled or not models or not agy_quota_error(str(e)):
                    raise
                self._log_text(f"[antigravity] {model} is out of quota; switching to {models[0]}")

    def _agy_carry_note(self, old_sid: str) -> str:
        """What a fresh conversation needs to know about the long one it replaces."""
        tasks = [t for t in self.store.get_tasks(self.project_id)
                 if t.get("session_id") == old_sid and t["id"] != self.task_id][-8:]
        lines = []
        for t in tasks:
            ask = (t["description"].strip().splitlines() or [""])[0][:90]
            got = ((t.get("summary") or "").strip().splitlines() or [""])[0][:140]
            lines.append(f"- #{t['id']} {ask}" + (f" → {got}" if got else ""))
        return ("(This is a new conversation: the previous one grew too long. The working folder and branch "
                "are the same, with all code and commits; for details read the code, git log and PROGRESS.md. "
                "Recent tasks:)\n"
                + "\n".join(lines) + "\n\n") if lines else ""

    async def _agy_once(self, prompt, conversation, model):
        from session_send import _image_dirs
        skip = self.project_settings.get("skip_permissions")
        images = self.task.get("images") or []
        # Print mode cannot stop to ask for tool approval, so approvals are always
        # skipped; without the project's "skip permissions" the terminal is sandboxed.
        cmd = ["agy", "-p", prompt, "--output-format", "stream-json",
               "--print-timeout", f"{AGY_TIMEOUT_MIN}m", "--dangerously-skip-permissions",
               "--model", model]
        for d in _image_dirs(images):
            cmd += ["--add-dir", d]
        if not skip:
            cmd.append("--sandbox")
        if conversation:
            cmd += ["--conversation", conversation]
        self._log_text(f"[antigravity] model: {model}")
        agy_log = AgyLog(prompt, conversation, model=model, images=images)
        state = {"texts": {}, "result": None}

        async def on_line(line):
            ev = agy_log.feed(line)
            if ev is None:
                if line.strip():
                    self._log_text(line[:300])
                return
            state["last_kind"] = agy_kind(ev) or state.get("last_kind", "")
            await self._handle_agy_event(ev, state)

        try:
            proc = await self._stream(cmd, hard_limit_min=AGY_HARD_LIMIT_MIN, on_line=on_line)
        except FileNotFoundError:
            raise RuntimeError("Antigravity CLI not found (agy)")
        if self.cancelled:
            return
        res = state["result"] or {}
        text = (res.get("response") or self._last_message or "").strip()
        self._last_message = text
        self.result_summary = text[-4000:] if text else self.result_summary
        if self.task.get("plan_mode") and text:
            self.store.update_task(self.project_id, self.task_id, plan_content=text)
        glitch = res and res.get("status") not in ("SUCCESS", None) and text \
            and agy_trailing_glitch(res, state.get("last_kind", ""))
        if glitch:
            self._log_text("[antigravity] an empty tool call followed the finished answer; treating it as done")
        elif res and res.get("status") not in ("SUCCESS", None):
            why = res.get("error") or res.get("finished_reason") or res.get("status")
            raise RuntimeError(f"agy 没有完成：{str(why)[:300]}")
        if proc.returncode and not glitch:
            raise RuntimeError(f"agy exit code {proc.returncode}: {self._stderr_tail(proc)}")
        if not text:
            raise RuntimeError("agy 没有输出（可能需要先在终端里运行一次 agy 完成首次设置）")

    async def _handle_agy_event(self, ev, state):
        log = self.store.append_log
        kind = ev.get("event")
        if kind == "init" or (ev.get("conversation_id") and not self.session_id):
            cid = ev.get("conversation_id") or ""
            if cid and cid != self.session_id:
                self.session_id = cid
                self.store.update_task(self.project_id, self.task_id, session_id=cid)
                log(self.project_id, self.task_id, type="init", content="antigravity",
                    meta={"model": "antigravity", "session_id": cid})
                await self._broadcast_event("init", {"model": "antigravity"})
        if kind == "step_update":
            u = ev.get("step_update") or {}
            if not self.session_id and u.get("conversation_id"):
                await self._handle_agy_event({"event": "init", "conversation_id": u["conversation_id"]}, state)
            st, step = u.get("state"), u.get("step_type")
            key = u.get("step_index")
            if step == "tool":
                info = u.get("tool_info") or {}
                name = u.get("tool_name") or info.get("name") or "tool"
                summary = agy_step(name, info.get("parameters") or {})
                if st == "ACTIVE" and not any(tc["tool_use_id"] == f"agy{key}" for tc in self.tool_calls):
                    self.tool_calls.append({"tool": name, "tool_use_id": f"agy{key}",
                                            "input_summary": summary, "is_error": False})
                    log(self.project_id, self.task_id, type="tool_use", content=summary,
                        meta={"tool_name": name, "status": "created", "input_summary": summary})
                    await self._broadcast_event("tool_use", {"tool": name, "input": summary})
                elif st not in ("ACTIVE", "DONE", "PENDING", None):
                    self.error_count += 1
                    for tc in self.tool_calls:
                        if tc["tool_use_id"] == f"agy{key}":
                            tc["is_error"] = True
                    log(self.project_id, self.task_id, type="tool_error", content=f"{summary}: {st}",
                        meta={"tool_name": name, "status": "failed"})
                    await self._broadcast_event("tool_error", {"error": f"{name}: {st}"})
                elif st == "DONE":
                    self.error_count = 0
            elif step == "agent_response":
                if u.get("text_delta"):
                    state["texts"][key] = state["texts"].get(key, "") + u["text_delta"]
                if st == "DONE" and state["texts"].get(key, "").strip():
                    text = state["texts"][key].strip()
                    self.turn_count += 1
                    self._last_message = text
                    log(self.project_id, self.task_id, type="message", content=text[:4000])
                    await self._broadcast_log(text[:500])
        elif kind == "result":
            res = ev.get("result") or {}
            state["result"] = res
            u = res.get("usage") or {}
            log(self.project_id, self.task_id, type="result",
                content=f"{res.get('status', '')} | out={u.get('output_tokens', 0)} in={u.get('input_tokens', 0)}",
                meta={"usage": u, "duration": res.get("duration_seconds")})
            await self._broadcast_event("result", {"usage": u})

    # ── helpers ──
    def _log_text(self, text):
        self.store.append_log(self.project_id, self.task_id, text)

    def _resume_session_for(self, agent):
        """Session to continue: the predecessor's (same executor only), or this
        task's own when a plan is being revised."""
        from config import task_agent
        pred_id = self.task.get("predecessor_id")
        if pred_id:
            pred = self.store.get_task(self.project_id, pred_id)
            if pred and pred.get("session_id") and (task_agent(pred) or "claude") == agent:
                return pred["session_id"]
        if self.task.get("plan_feedback") and self.task.get("session_id"):
            return self.task["session_id"]
        return None
