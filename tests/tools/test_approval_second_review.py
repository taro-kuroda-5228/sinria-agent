"""Second independent review: shared identity, expiry, and invalidation."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from tools import approval, approval_store as store
from tests.tools.test_approval_replay_processes import child, COMMAND, SESSION

SETUP = '''
import json
from tools import approval
approval._get_approval_config = lambda: {"gateway_timeout": 0}
approval._get_approval_mode = lambda: "manual"
approval._fire_approval_hook = lambda *a, **kw: None
approval._is_gateway_approval_context = lambda: True
approval.set_current_session_key("approval-replay-test")
'''


def request(code):
    return child(SETUP + f'''
r = approval.check_execute_code_guard({code!r}, "local")
print(json.dumps([r["approved"], approval.peek_gateway_approval_id({SESSION!r})]))
''')


def test_process_retry_reuses_pending_id_and_supersedes_other_script():
    first = request("print(42)")
    assert request("print(42)") == first
    second = request("print(43)")
    assert second[1] != first[1]
    assert not store.write_response(first[1], "once")
    assert store.write_response(second[1], "once")
    assert request("print(43)")[0] is True
    assert request("print(42)")[0] is False


@pytest.mark.parametrize("resolved", [False, True])
def test_clear_session_retires_other_process_deferred(resolved):
    _, aid = request("print(42)")
    if resolved:
        assert store.write_response(aid, "once")
    approval.clear_session(SESSION)
    assert not store.write_response(aid, "once")
    assert child("import json; from tools import approval; " +
                 f"print(json.dumps(approval._consume_deferred_gateway_approval({SESSION!r}, {COMMAND!r})))") is None


@pytest.mark.parametrize("operation", ["write", "active", "poll", "handoff"])
def test_expiry_authoritative_without_listing(monkeypatch, operation):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    store.record_pending("expires", SESSION, {"command": COMMAND}, ttl_seconds=10)
    if operation == "poll":
        assert store.write_response("expires", "once")
    clock[0] = 1010.0
    result = {"write": lambda: store.write_response("expires", "once"),
              "active": lambda: store.resolve_active("expires", "once"),
              "poll": lambda: store.poll_response("expires"),
              "handoff": lambda: store.mark_deferred("expires")}[operation]()
    assert not result
    clock[0] = 1001.0
    assert not store.is_live("expires")


def test_expiry_between_discovery_and_claim(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    store.record_pending("paused", SESSION, {"command": COMMAND}, owner="deferred", ttl_seconds=10)
    assert store.write_response("paused", "once")
    original = store.list_pending
    def paused(**kwargs):
        rows = original(**kwargs)
        assert rows
        clock[0] = 1010.0
        return rows
    monkeypatch.setattr(store, "list_pending", paused)
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND) is None


def test_binding_cannot_be_rewritten_or_forged(tmp_path):
    store.record_pending("binding", SESSION, {"command": COMMAND}, owner="deferred")
    store.record_pending("binding", "other", {"command": "different"})
    row = store.list_pending()[0]
    assert row["session_key"] == SESSION
    assert row["command_sha256"] == hashlib.sha256(COMMAND.encode()).hexdigest()
    path = store._pending_dir() / "binding.json"
    row["session_key"] = "other"
    row["command_sha256"] = hashlib.sha256(b"different").hexdigest()
    path.write_text(json.dumps(row))
    assert store.write_response("binding", "once")
    assert approval._consume_deferred_gateway_approval("other", "different") is None


@pytest.mark.parametrize("bad", ["a/b", "a.b", "a b", "a_b" + "x" * 64, "", None])
def test_malformed_ids_never_alias(bad):
    store.record_pending("a_b", SESSION, {"command": COMMAND})
    assert not store.write_response(bad, "once")
    store.clear_pending(bad)
    store.record_pending(bad, SESSION, {"command": COMMAND})
    assert [r["id"] for r in store.list_pending()] == ["a_b"]
    assert store.is_live("a_b")


def test_concurrent_process_creation_reuses_one_identity():
    source = SETUP + f'''
input()
approval.check_execute_code_guard("print(42)", "local")
print(json.dumps(approval.peek_gateway_approval_id({SESSION!r})))
'''
    processes = [subprocess.Popen(
        [sys.executable, "-c", source], env=os.environ.copy(), text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) for _ in range(4)]
    try:
        for proc in processes:
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        ids = []
        for proc in processes:
            out, err = proc.communicate(timeout=20)
            assert proc.returncode == 0, err
            ids.append(json.loads(out))
        assert ids[0] and len(set(ids)) == 1
        assert len(store.list_pending()) == 1
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def test_resolved_supersession_invalidates_stale_owner():
    approval._defer_gateway_approval(SESSION, {"command": COMMAND})
    old = approval.peek_gateway_approval_id(SESSION)
    assert old is not None
    assert store.write_response(old, "once")
    assert request("print(43)")[1] != old
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND) is None
    assert approval.resolve_gateway_approval(SESSION, "once", expected_approval_id=old) == 0


def test_republication_and_reuse_do_not_extend_expiry(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    data = {"command": COMMAND, "metadata": {"tool": "execute_code"}}
    assert store.record_pending("original", SESSION, data, ttl_seconds=10) == "original"
    clock[0] = 1009.0
    assert store.record_pending("original", SESSION, data) == "original"
    assert store.record_pending("retry", SESSION, data) == "original"
    clock[0] = 1010.0
    assert not store.write_response("original", "once")


def test_claim_rechecks_binding_even_with_forged_discovery(monkeypatch):
    store.record_pending("exact", SESSION, {"command": COMMAND}, owner="deferred")
    assert store.write_response("exact", "once")
    monkeypatch.setattr(store, "list_pending", lambda **kw: [{
        "id": "exact", "session_key": "wrong", "command_sha256": hashlib.sha256(b"wrong").hexdigest(),
    }])
    assert approval._consume_deferred_gateway_approval("wrong", "wrong") is None
    assert store.poll_response("exact", owner="deferred", session_key=SESSION,
                               digest=hashlib.sha256(COMMAND.encode()).hexdigest()) == "once"


def test_legacy_sqlite_rows_retire_without_importing_projection():
    root = store._pending_dir().parent
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "state.sqlite3") as db:
        db.execute("CREATE TABLE approvals (id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, choice TEXT)")
        db.execute("INSERT INTO approvals VALUES ('legacy', 'deferred', 'resolved', 'once')")
    assert store.poll_response("legacy", owner="deferred") is None
    assert store.record_pending("legacy", SESSION, {"command": COMMAND}) is None
    assert not store.is_live("legacy")
    assert store.record_pending("fresh", SESSION, {"command": COMMAND}) == "fresh"
