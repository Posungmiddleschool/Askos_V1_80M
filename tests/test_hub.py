"""Lock logic for several Modal accounts sharing one HF namespace (common/hub.py), on an in-memory store.

python3 tests/test_hub.py      (or: python3 -m pytest -q tests)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from common import hub  # noqa: E402
from common.config import load_infra  # noqa: E402

H = 3600


def test_two_accounts_cannot_take_the_same_key():
    s = hub.MemStore()
    hub.acquire(s, "download/code", "acctA:alice", 30, now=0)
    try:
        hub.acquire(s, "download/code", "acctB:bob", 30, now=H)
        raise AssertionError("second account got the lock")
    except hub.LockHeld as e:
        assert e.lock["owner"] == "acctA:alice"
    hub.acquire(s, "download/math", "acctB:bob", 30, now=H)       # a different key is free
    assert set(hub.locks(s)) == {"download/code", "download/math"}


def test_concurrent_commit_is_detected_and_retried():
    s = hub.MemStore()
    # bob commits his lock between alice's read and alice's commit: alice must re-read and then lose
    s.before_commit = lambda: hub.acquire(s, "download/code", "acctB:bob", 30, now=0)
    try:
        hub.acquire(s, "download/code", "acctA:alice", 30, now=0)
        raise AssertionError("both accounts got the lock")
    except hub.LockHeld as e:
        assert e.lock["owner"] == "acctB:bob"


def test_done_blocks_until_forced_and_failed_allows_retry():
    s = hub.MemStore()
    hub.acquire(s, "download/code", "acctA:alice", 30, now=0)
    assert hub.release(s, "download/code", "acctA:alice", "failed", {"why": "429"}, now=10)
    lk = hub.acquire(s, "download/code", "acctB:bob", 30, now=20)        # failed -> anyone may retry
    assert lk["attempt"] == 2 and lk["history"][-1]["status"] == "failed"
    hub.release(s, "download/code", "acctB:bob", "done", {"gb": 11.8}, now=30)
    try:
        hub.acquire(s, "download/code", "acctA:alice", 30, now=40)
        raise AssertionError("done lock was taken again")
    except hub.AlreadyDone:
        pass
    assert hub.acquire(s, "download/code", "acctA:alice", 30, force=True, now=50)["owner"] == "acctA:alice"


def test_stale_lock_can_be_taken_over_and_old_owner_notices():
    s = hub.MemStore()
    hub.acquire(s, "download/code", "acctA:alice", 30, now=0)
    assert hub.heartbeat(s, "download/code", "acctA:alice", {"bytes": 5, "target": 10}, now=H)
    try:
        hub.acquire(s, "download/code", "acctB:bob", 30, now=20 * H)       # 19 h since heartbeat: alive
        raise AssertionError("took a live lock")
    except hub.LockHeld:
        pass
    lk = hub.acquire(s, "download/code", "acctB:bob", 30, now=32 * H)      # 31 h: stale
    assert lk["took_over_stale"]
    assert not hub.heartbeat(s, "download/code", "acctA:alice", now=33 * H)   # alice learns she lost it
    assert not hub.release(s, "download/code", "acctA:alice", "done", now=33 * H)


def test_same_owner_resumes_its_own_running_lock():
    s = hub.MemStore()
    hub.acquire(s, "download/code", "acctA:alice", 30, now=0)
    assert hub.acquire(s, "download/code", "acctA:alice", 30, now=H)["attempt"] == 2   # container died, rerun


def test_lock_table_and_config():
    s = hub.MemStore()
    hub.acquire(s, "download/code", "acctA:alice", 30, now=0)
    hub.heartbeat(s, "download/code", "acctA:alice", {"bytes": 2e9, "target": 4e9}, now=10)
    t = hub.lock_table(hub.locks(s), now=20, stale_hours=30)
    assert "download/code" in t and "running" in t and "2.00/4.00 GB" in t
    h = load_infra()["hub"]
    assert h["state_repo"] == "askos-v1-80m-state" and h["data_repo"] == "askos-v1-80m-data" and h["lock_stale_hours"] > 0


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
