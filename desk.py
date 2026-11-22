"""The desk display (/desk and the iOS app ios/JarvisDesk): a big lamp for how things stand
and six keys, one per session, for an old phone left on the desk that asks every few seconds.

Built from the overview's cards, so states and their order are the home page's:
error / limit, your turn, running, done, quiet; free keys go to the sessions
used most recently (idle). Like a status keypad: colour says the state at a glance.
"""

DESK_KEYS = 6
_NEEDS_YOU = ("error", "limit", "wait")


def _item(c: dict) -> dict:
    st = c["state"]
    title = c.get("title") or ""
    # a session started outside any project folder lands in "other"; its title says more than that
    name = title if c.get("project") == "other" and title else c.get("project_name", "")
    return {
        "sid": c.get("sid", ""),
        "title": title,
        "project": name,
        "agent": c.get("agent", ""),
        "state": st,
        "bg": c.get("bg_wait", 0),
        "step": c.get("step", "") if st in ("run", "quiet") else "",
        "err": c.get("err", "") if st in ("error", "limit") else "",
        "at": c.get("last_at", 0),
    }


def desk_view(ov: dict, keys: int = DESK_KEYS) -> dict:
    # sessions you can do something about: not the scripted runs of some other program
    live = [_item(c) for c in ov.get("now", []) if not c.get("scripted")]
    counts = {"wait": 0, "run": 0, "bg": 0, "done": 0, "error": 0}
    for it in live:
        st = it["state"]
        if st == "run":
            counts["bg" if it["bg"] else "run"] += 1
        elif st == "quiet":                         # still running, just not saying anything
            counts["run"] += 1
        elif st in ("error", "limit"):
            counts["error"] += 1
        elif st in counts:
            counts[st] += 1
    items = live[:keys]
    seen = {it["sid"] for it in items}
    for c in ov.get("all", []):                     # newest first
        if len(items) >= keys:
            break
        if c["state"] in ("idle", "done", "quiet") and c.get("sid") and c["sid"] not in seen \
                and not c.get("scripted"):
            it = _item(c)
            it["state"] = "idle" if c["state"] == "idle" else it["state"]
            items.append(it)
            seen.add(c["sid"])
    return {"at": ov.get("generated_at", 0), "counts": counts, "items": items,
            "more": max(0, sum(1 for it in live if it["state"] in _NEEDS_YOU) -
                        sum(1 for it in items if it["state"] in _NEEDS_YOU))}


# ── what is left of each agent's quota, for the line under the lamp ─────────

# Antigravity has several quota groups; the desk shows the one its default model uses
_USAGE = (("claude", "Claude", None), ("codex", "Codex", None), ("antigravity", "Gemini", "Gemini"))


def desk_usage(snap: dict, now_ms: int) -> list:
    """[{name, left (0-100), resets_at, at}] from the usage snapshot: per agent, its tightest
    window. A window whose reset time has passed counts as fresh; unknown agents are left out."""
    out = []
    for key, name, group in _USAGE:
        u = (snap or {}).get(key) or {}
        tight = None
        for w in (u.get("windows") or {}).values():
            if group and w.get("group") != group:
                continue
            reset = w.get("resets_at") or 0
            used = 0.0 if reset and reset <= now_ms else (
                100.0 if w.get("status") == "rejected" else float(w.get("used") or 0))
            if tight is None or used > tight[0]:
                tight = (used, reset if used else 0)
        if tight is None:
            continue
        out.append({"name": name, "left": max(0, round(100 - tight[0])),
                    "resets_at": tight[1], "at": u.get("at", 0)})
    return out
