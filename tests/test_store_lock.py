"""The project store's file lock is shared by the server's threads."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import FileLock  # noqa: E402
from transcript import agy_kind, agy_trailing_glitch  # noqa: E402


def test_many_threads_share_one_lock(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    errors, inside, peak = [], [0], [0]

    def work():
        try:
            for _ in range(200):
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                    inside[0] -= 1
        except Exception as e:          # the old lock: "I/O operation on closed file"
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and peak[0] == 1


def test_lock_is_reentrant(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    with lock:
        with lock:
            pass
    with lock:                           # released properly: can take it again
        pass


def test_agy_empty_call_after_the_answer_is_not_a_failure():
    res = {"status": "ERROR", "error": "Your previous response contained an improperly formatted "
           "function call: Malformed function call: Failed to parse function call: Function call is empty"}
    assert agy_trailing_glitch(res, "text")
    assert not agy_trailing_glitch(res, "tool")          # broke mid-work: still a failure
    assert not agy_trailing_glitch({"status": "ERROR", "error": "quota"}, "text")
    done = {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "DONE"}}
    tool = {"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE"}}
    assert agy_kind(done) == "text" and agy_kind(tool) == "tool" and agy_kind({"event": "init"}) == ""
