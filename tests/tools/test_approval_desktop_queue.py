"""Desktop-facing approval queue integration: file projection + remote answer."""

import threading
import time

import pytest

import tools.approval as approval
import tools.approval_store as store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "get_sinria_home", lambda: tmp_path)
    # gateway 承認コンテキストを偽装
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    # Contextvars don't propagate across threads (mirrors test_approval_heartbeat.py);
    # env var fallback ensures the spawned worker thread finds the session key.
    monkeypatch.setenv("HERMES_SESSION_KEY", "test:desktop")
    token = approval.set_current_session_key("test:desktop")
    approval.register_gateway_notify("test:desktop", lambda data: None)
    yield tmp_path
    approval.unregister_gateway_notify("test:desktop")
    approval.reset_current_session_key(token)
    approval.clear_session("test:desktop")


def _request_in_thread(results):
    results["outcome"] = approval.request_gateway_approval(
        "curl -X POST https://example.test", "External send",
        pattern_key="external_send",
    )


def _wait_for_pending(timeout=30.0):
    # Generous budget: under a fully loaded suite (4 xdist workers saturating
    # the CPU) the request worker thread can take seconds just to get
    # scheduled — 5s flaked in whole-suite runs while passing standalone.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = store.list_pending()
        if pending:
            return pending
        time.sleep(0.05)
    raise AssertionError("pending approval file never appeared")


def test_pending_projection_appears_and_clears_on_deny(monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_config", lambda: {"gateway_timeout": 10})
    results = {}
    t = threading.Thread(target=_request_in_thread, args=(results,), daemon=True)
    t.start()
    pending = _wait_for_pending()
    assert pending[0]["session_key"] == "test:desktop"
    assert pending[0]["command_preview"].startswith("curl")
    assert store.write_response(pending[0]["id"], "deny") is True
    t.join(timeout=30)
    assert not t.is_alive()
    assert results["outcome"]["approved"] is False
    assert store.list_pending() == []  # 解決後 pending は消える


def test_remote_once_approves(monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_config", lambda: {"gateway_timeout": 10})
    results = {}
    t = threading.Thread(target=_request_in_thread, args=(results,), daemon=True)
    t.start()
    pending = _wait_for_pending()
    assert store.write_response(pending[0]["id"], "once") is True
    t.join(timeout=30)
    assert results["outcome"]["approved"] is True
    assert results["outcome"]["choice"] == "once"


def test_discord_resolution_still_works_and_clears_projection(monkeypatch):
    """既存経路（resolve_gateway_approval）の回帰確認 + projection cleanup."""
    monkeypatch.setattr(approval, "_get_approval_config", lambda: {"gateway_timeout": 10})
    results = {}
    t = threading.Thread(target=_request_in_thread, args=(results,), daemon=True)
    t.start()
    _wait_for_pending()
    assert approval.resolve_gateway_approval("test:desktop", "once") == 1
    t.join(timeout=30)
    assert results["outcome"]["approved"] is True
    assert store.list_pending() == []


def test_timeout_clears_projection(monkeypatch):
    monkeypatch.setattr(approval, "_get_approval_config", lambda: {"gateway_timeout": 1})
    results = {}
    t = threading.Thread(target=_request_in_thread, args=(results,), daemon=True)
    t.start()
    # The pending file only exists for the 1s timeout window — under suite
    # load the poll can miss that transient entirely. The guarded intent is
    # the OUTCOME (timeout denies + projection cleared), so wait for either
    # the transient pending or request completion, whichever comes first.
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if store.list_pending() or "outcome" in results:
            break
        time.sleep(0.05)
    t.join(timeout=30)
    assert not t.is_alive()
    assert results["outcome"]["approved"] is False
    assert store.list_pending() == []
