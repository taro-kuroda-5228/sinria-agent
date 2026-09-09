"""Timeout recovery must retain exact one-shot authorization, never auto-approve."""
import uuid
import pytest
from tools import approval
from gateway.session_context import set_session_vars, clear_session_vars

@pytest.fixture
def session(monkeypatch):
    key = 'agent:main:discord:group:' + uuid.uuid4().hex
    tokens = set_session_vars(platform='discord', chat_id=key, user_id='owner', session_key=key)
    monkeypatch.setattr(approval, '_get_approval_mode', lambda: 'manual')
    monkeypatch.setattr(approval, '_wait_for_gateway_entry', lambda entry, timeout: False)
    monkeypatch.setattr(approval, 'is_current_session_yolo_enabled', lambda: False)
    monkeypatch.delenv('HERMES_YOLO_MODE', raising=False)
    monkeypatch.delenv('HERMES_CRON_SESSION', raising=False)
    yield key
    clear_session_vars(tokens)
    with approval._lock:
        entry = approval._deferred_gateway_approvals.pop(key, None)
    if entry:
        approval.approval_store.clear_pending(entry['approval_id'])


def test_timeout_can_be_resolved_then_consumed_once(session):
    first = approval.check_execute_code_guard('print(42)', 'local')
    assert not first['approved']
    pending_id = approval.peek_gateway_approval_id(session)
    assert pending_id
    assert approval.resolve_gateway_approval(session, 'once') == 1
    assert approval.check_execute_code_guard('print(42)', 'local')['approved']
    assert not approval.check_execute_code_guard('print(42)', 'local')['approved']


def test_pending_retry_keeps_same_id(session):
    approval.check_execute_code_guard('print(42)', 'local')
    first = approval.peek_gateway_approval_id(session)
    assert not approval.check_execute_code_guard('print(42)', 'local')['approved']
    assert approval.peek_gateway_approval_id(session) == first


def test_expired_approval_cannot_authorize(session, monkeypatch):
    approval.check_execute_code_guard('print(42)', 'local')
    assert approval.resolve_gateway_approval(session, 'once') == 1
    with approval._lock:
        approval._deferred_gateway_approvals[session]['expires_at'] = 0
    assert not approval.check_execute_code_guard('print(42)', 'local')['approved']


def test_handoff_race_does_not_leave_replayable_approval(session, monkeypatch):
    original = approval._defer_gateway_approval
    def racing(*args, **kwargs):
        original(*args, **kwargs)
        assert approval.resolve_gateway_approval(session, 'once') == 1
    monkeypatch.setattr(approval, '_defer_gateway_approval', racing)
    assert approval.check_execute_code_guard('print(42)', 'local')['approved']
    assert approval.peek_gateway_approval_id(session) is None


def test_stale_id_cannot_approve_replacement(session):
    approval.check_execute_code_guard('print(42)', 'local')
    old = approval.peek_gateway_approval_id(session)
    approval.check_execute_code_guard('print(43)', 'local')
    assert approval.resolve_gateway_approval(session, 'once', expected_approval_id=old) == 0
    assert not approval.check_execute_code_guard('print(43)', 'local')['approved']


def test_file_response_consumption_removes_memory_copy(session):
    approval.check_execute_code_guard('print(42)', 'local')
    old = approval.peek_gateway_approval_id(session)
    assert approval.approval_store.write_response(old, 'once')
    assert approval.check_execute_code_guard('print(42)', 'local')['approved']
    assert approval.peek_gateway_approval_id(session) is None
    assert approval.resolve_gateway_approval(session, 'once', expected_approval_id=old) == 0


def test_deny_survives_timeout(session):
    assert not approval.check_execute_code_guard('print(42)', 'local')['approved']
    assert approval.resolve_gateway_approval(session, 'deny') == 1
    assert not approval.check_execute_code_guard('print(42)', 'local')['approved']


def test_changed_script_does_not_consume_approval(session):
    assert not approval.check_execute_code_guard('print(42)', 'local')['approved']
    assert approval.resolve_gateway_approval(session, 'once') == 1
    assert approval._consume_deferred_gateway_approval(session, "execute_code <<'PY'\nprint(43)\nPY") is None
    assert approval.check_execute_code_guard('print(42)', 'local')['approved']
