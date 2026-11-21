"""Usage per executor and Antigravity CLI sessions."""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import usage  # noqa: E402
from sessions import SessionIndex  # noqa: E402


def test_claude_event_windows_merge(tmp_path):
    t = usage.UsageTracker(path=tmp_path / "u.json", codex_home=tmp_path)
    t.record_claude({"status": "allowed", "rateLimitType": "seven_day", "resetsAt": 1000,
                     "unifiedWindows": {"seven_day": {"utilization": 0.34, "resetsAt": 1000,
                                                      "status": "allowed"}}})
    t.record_claude({"status": "allowed_warning", "rateLimitType": "five_hour", "resetsAt": 500})
    c = usage.UsageTracker(path=tmp_path / "u.json", codex_home=tmp_path).snapshot()["claude"]
    assert c["windows"]["seven_day"] == {"used": 34.0, "resets_at": 1_000_000, "status": "allowed"}
    assert c["windows"]["five_hour"]["used"] is None and c["status"] == "allowed_warning"


def test_codex_limits_from_newest_log(tmp_path):
    d = tmp_path / "sessions" / "2026" / "11" / "13"
    d.mkdir(parents=True)
    rl = {"primary": {"used_percent": 15.0, "window_minutes": 10080, "resets_at": 2000},
          "secondary": None, "credits": {"has_credits": True, "unlimited": False, "balance": "12.5"},
          "plan_type": "pro"}
    with open(d / "rollout-x.jsonl", "w") as f:
        f.write(json.dumps({"type": "event_msg", "timestamp": "2026-11-13T07:00:00Z",
                            "payload": {"type": "token_count", "rate_limits": rl}}) + "\n")
        f.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": None}}) + "\n")
    got = usage.latest_codex(tmp_path)
    assert got["windows"] == {"m10080": {"used": 15.0, "minutes": 10080, "resets_at": 2_000_000,
                                         "status": "allowed"}}
    assert got["credits"] == 12.5 and got["plan"] == "pro"


def _agy_dir(root, cid, title, preview_cache):
    root.mkdir(parents=True)
    con = sqlite3.connect(root / "conversation_summaries.db")
    con.execute("create table conversation_summaries (conversation_id text, title text, preview text, "
                "workspace_uris text, status text, last_modified_time text, step_count int)")
    con.execute("insert into conversation_summaries values (?,?,?,?,?,?,?)",
                (cid, title, "", '["file:///w/app"]', "CASCADE_RUN_STATUS_IDLE",
                 "2099-01-01 00:00:00+00:00", 3))
    con.commit()
    con.close()
    if preview_cache:
        (root / "cache").mkdir()
        (root / "cache" / "conversation_metadata.json").write_text(json.dumps(
            {"conversations": {cid: {"summary": {"Title": "", "Preview": preview_cache}}}}))


def test_agy_reads_ide_and_cli_dirs(tmp_path):
    _agy_dir(tmp_path / "ide", "a1", "IDE 会话", None)
    _agy_dir(tmp_path / "cli", "c1", "", "Reply with PONG")
    ix = SessionIndex(tmp_path / "i.json", agy_dirs=[tmp_path / "ide", tmp_path / "cli"])
    assert ix._scan_agy(0)
    assert {a["sid"]: a["title"] for a in ix.agy} == {"a1": "IDE 会话", "c1": "Reply with PONG"}
    assert not ix._scan_agy(0)          # unchanged files: no rescan


def test_agy_limits_from_usage_command():
    data = {"groups": [
        {"name": "Gemini Models", "buckets": [
            {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.97,
             "reset_time": "2026-11-14T15:54:28Z"},
            {"id": "gemini-5h", "window": "5h", "remaining_fraction": 0, "reset_time": "2026-11-13T16:43:20Z"}]},
        {"name": "Gemini 4 Argon", "buckets": [{"id": "argon", "window": "weekly", "remaining_fraction": 1}]}]}
    w = usage.agy_limits(data, 5)["windows"]
    assert w["gemini-weekly"]["used"] == 3.0 and w["gemini-weekly"]["group"] == "Gemini"
    assert w["gemini-5h"]["status"] == "rejected" and w["argon"]["group"] == "Argon"
