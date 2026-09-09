"""Real process boundaries: memory is never an authorization authority."""
import json
import os
import select
import subprocess
import sys

import pytest

from tools import approval, approval_store as store

COMMAND = "execute_code <<'PY'\nprint(42)\nPY"
SESSION = "approval-replay-test"


def child(source):
    result = subprocess.run(
        [sys.executable, "-c", source], env=os.environ.copy(),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("resolution", ["remote", "owner"])
def test_other_process_consumption_invalidates_stale_owner(resolution):
    approval._defer_gateway_approval(SESSION, {"command": COMMAND})
    approval_id = approval.peek_gateway_approval_id(SESSION)
    assert approval_id
    if resolution == "remote":
        assert store.write_response(approval_id, "once")
    else:
        assert approval.resolve_gateway_approval(SESSION, "once") == 1
    consumed = child(
        "import json; from tools import approval; "
        f"print(json.dumps(approval._consume_deferred_gateway_approval({SESSION!r}, {COMMAND!r})))"
    )
    assert consumed == "once"
    assert approval.resolve_gateway_approval(
        SESSION, "once", expected_approval_id=approval_id
    ) == 0
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND) is None
    assert approval.peek_gateway_approval_id(SESSION) is None


def test_deferred_retry_cannot_steal_live_waiters_remote_response():
    source = f'''
import json
from tools import approval
approval._get_approval_config = lambda: {{"gateway_timeout": 5}}
approval._fire_approval_hook = lambda *a, **kw: None
approval._is_gateway_approval_context = lambda: True
approval.set_current_session_key({SESSION!r})
def notify(data):
    print(approval.peek_gateway_approval_id({SESSION!r}), flush=True)
    input()
approval.register_gateway_notify({SESSION!r}, notify)
result = approval.request_gateway_approval(
    {COMMAND!r}, "test script", pattern_key="script", allow_session=False,
    metadata={{"tool": "execute_code"}},
)
print(json.dumps(result), flush=True)
'''
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", source], env=os.environ.copy(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout
        assert select.select([proc.stdout], [], [], 20)[0], "owner never published"
        approval_id = proc.stdout.readline().strip()
        assert approval_id
        assert store.write_response(approval_id, "once")
        stolen = approval._consume_deferred_gateway_approval(SESSION, COMMAND)
        stdout, stderr = proc.communicate("continue\n", timeout=20)
        assert proc.returncode == 0, stderr
        assert stolen is None
        assert json.loads(stdout)["approved"] is True
        assert not store.write_response(approval_id, "once")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_consumed_id_cannot_be_republished_or_answered():
    store.record_pending("retired", SESSION, {"command": COMMAND})
    assert store.write_response("retired", "once")
    assert store.poll_response("retired") == "once"
    assert not store.write_response("retired", "once")
    store.record_pending("retired", SESSION, {"command": COMMAND})
    assert not store.write_response("retired", "once")
    assert store.poll_response("retired") is None


def test_first_response_is_final():
    store.record_pending("decision", SESSION, {"command": COMMAND})
    assert store.write_response("decision", "deny")
    assert not store.write_response("decision", "once")
    assert store.poll_response("decision") == "deny"


def test_clear_retires_grant_even_if_projection_cleanup_fails(monkeypatch):
    from pathlib import Path
    store.record_pending("cleanup", SESSION, {"command": COMMAND})
    assert store.write_response("cleanup", "once")
    monkeypatch.setattr(Path, "unlink", lambda *a, **kw: (_ for _ in ()).throw(OSError("test")))
    store.clear_pending("cleanup")
    assert store.poll_response("cleanup") is None


def test_competing_processes_claim_only_once():
    approval._defer_gateway_approval(SESSION, {"command": COMMAND})
    approval_id = approval.peek_gateway_approval_id(SESSION)
    assert approval_id
    assert store.write_response(approval_id, "once")
    source = (
        "import json; from tools import approval; input(); "
        f"print(json.dumps(approval._consume_deferred_gateway_approval({SESSION!r}, {COMMAND!r})))"
    )
    processes = [subprocess.Popen(
        [sys.executable, "-c", source], env=os.environ.copy(), text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) for _ in range(4)]
    try:
        for proc in processes:
            assert proc.stdin
            proc.stdin.write("go\n")
            proc.stdin.flush()
        results = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=20)
            assert proc.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert results.count("once") == 1
        assert results.count(None) == 3
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def test_real_timeout_guard_handoff_survives_process_exit():
    setup = '''
import json
from tools import approval
approval._get_approval_config = lambda: {"gateway_timeout": 0}
approval._get_approval_mode = lambda: "manual"
approval._fire_approval_hook = lambda *a, **kw: None
approval._is_gateway_approval_context = lambda: True
approval.set_current_session_key("approval-replay-test")
'''
    first = child(setup + '''
result = approval.check_execute_code_guard("print(42)", "local")
print(json.dumps({"approved": result["approved"], "id": approval.peek_gateway_approval_id("approval-replay-test")}))
''')
    assert first["approved"] is False
    assert first["id"]
    assert store.write_response(first["id"], "once")
    retried = child(setup + '''
first = approval.check_execute_code_guard("print(42)", "local")
second = approval.check_execute_code_guard("print(42)", "local")
print(json.dumps([first["approved"], second["approved"]]))
''')
    assert retried == [True, False]
    assert not store.write_response(first["id"], "once")


def test_clear_session_retires_deferred_authority():
    approval._defer_gateway_approval(SESSION, {"command": COMMAND})
    approval_id = approval.peek_gateway_approval_id(SESSION)
    assert store.write_response(approval_id, "once")
    approval.clear_session(SESSION)
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND) is None
    assert not store.write_response(approval_id, "once")


def test_legacy_json_cannot_create_authority(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(store, "get_sinria_home", lambda: tmp_path)
    pending = tmp_path / "approvals" / "pending"
    pending.mkdir(parents=True)
    (pending / "legacy.json").write_text(json.dumps({"id": "legacy"}))
    assert not store.write_response("legacy", "once")
    assert store.poll_response("legacy") is None


@pytest.mark.parametrize("remote", [False, True])
def test_deferred_rechecks_authorization_and_exact_binding(remote):
    approval._defer_gateway_approval(SESSION, {"command": COMMAND})
    if remote:
        with approval._lock:
            approval._deferred_gateway_approvals.clear()
    with pytest.raises(approval.ApprovalAuthorizationError):
        approval.resolve_gateway_approval(SESSION, "once", authorize=lambda *a: False)
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND) is None
    assert approval.resolve_gateway_approval(SESSION, "once", authorize=lambda *a: True) == 1
    assert approval._consume_deferred_gateway_approval("other-session", COMMAND) is None
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND + "\n") is None
    assert approval._consume_deferred_gateway_approval(SESSION, COMMAND) == "once"



@pytest.mark.parametrize("choice", ["session", "always", "unexpected"])
def test_script_active_response_cannot_expand_one_shot_scope(monkeypatch, choice):
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: True)
    monkeypatch.setattr(approval, "_get_approval_config", lambda: {"gateway_timeout": 1})
    token = approval.set_current_session_key(SESSION)
    def notify(data):
        approval.resolve_gateway_approval(SESSION, choice)
    approval.register_gateway_notify(SESSION, notify)
    try:
        result = approval.request_gateway_approval(
            COMMAND, "script", pattern_key="execute_code", allow_session=False,
            allow_permanent=False, metadata={"tool": "execute_code"},
        )
        assert result["approved"] is False
    finally:
        approval.unregister_gateway_notify(SESSION)
        approval.clear_session(SESSION)
        approval.reset_current_session_key(token)
