"""Regression coverage for consented real-profile browser routing.

The browser process and agent-browser CLI remain external boundaries here; the
routing/session bookkeeping under test is production code, not a replacement
implementation supplied by the fixture.
"""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

import tools.browser_tool as browser_tool


@pytest.fixture
def clean_browser_state(monkeypatch):
    monkeypatch.setattr(browser_tool, "_active_sessions", {})
    monkeypatch.setattr(browser_tool, "_last_active_session_key", {})
    monkeypatch.setattr(browser_tool, "_session_last_activity", {})
    monkeypatch.setattr(browser_tool, "_cached_cloud_provider", None)
    monkeypatch.setattr(browser_tool, "_cloud_provider_resolved", False)
    monkeypatch.setattr(browser_tool, "_start_browser_cleanup_thread", lambda: None)
    monkeypatch.setattr(browser_tool, "_update_session_activity", lambda task_id: None)
    monkeypatch.setattr(browser_tool, "_ensure_cdp_supervisor", lambda task_id: None)


def test_consented_navigation_and_following_commands_share_persistent_cdp(
    monkeypatch, clean_browser_state
):
    """navigate plus later commands stay on one task session and never cloud-route."""
    task_id = "task-real-profile"
    cdp_url = "http://127.0.0.1:43111"
    monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
    monkeypatch.setattr(browser_tool, "_use_real_profile", lambda: True)
    monkeypatch.setattr(browser_tool, "_real_profile_cdp", lambda: (cdp_url, None))
    cloud = Mock(name="cloud-provider")
    monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: cloud)
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: False)
    monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: True)
    monkeypatch.setattr(browser_tool, "_run_browser_command", lambda key, command, args=None, **kwargs: {
        "success": True,
        "data": {"title": "Synthetic page", "url": "https://example.test", "snapshot": "button Submit", "refs": {"@e1": "Submit"}},
    })

    navigate = json.loads(browser_tool.browser_navigate("https://example.test", task_id=task_id))
    snapshot = json.loads(browser_tool.browser_snapshot(task_id=task_id))
    click = json.loads(browser_tool.browser_click("@e1", task_id=task_id))

    assert navigate["success"] is True
    assert snapshot == {"success": True, "snapshot": "button Submit", "element_count": 1}
    assert click == {"success": True, "clicked": "@e1"}
    assert browser_tool._active_sessions[task_id]["cdp_url"] == cdp_url
    assert browser_tool._last_active_session_key[task_id] == task_id
    cloud.create_session.assert_not_called()


def test_disabled_real_profile_keeps_existing_cloud_path(monkeypatch, clean_browser_state):
    provider = Mock()
    provider.create_session.return_value = {
        "session_name": "cloud-task",
        "cdp_url": "https://cloud.example/cdp",
        "features": {"cloud": True},
    }
    real_profile = Mock(side_effect=AssertionError("real profile must be skipped"))
    monkeypatch.setattr(browser_tool, "_use_real_profile", lambda: False)
    monkeypatch.setattr(browser_tool, "_real_profile_cdp", real_profile)
    monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
    monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: provider)

    session = browser_tool._get_session_info("task-disabled")

    assert session["session_name"] == "cloud-task"
    provider.create_session.assert_called_once_with("task-disabled")
    real_profile.assert_not_called()


def test_real_profile_failure_fails_closed_without_cloud_fallback(monkeypatch, clean_browser_state):
    monkeypatch.setattr(browser_tool, "_get_cdp_override", lambda: None)
    monkeypatch.setattr(browser_tool, "_use_real_profile", lambda: True)
    monkeypatch.setattr(browser_tool, "_real_profile_cdp", lambda: (None, "profile snapshot failed"))
    cloud = Mock(side_effect=AssertionError("cloud fallback is forbidden"))
    monkeypatch.setattr(browser_tool, "_get_cloud_provider", cloud)

    with pytest.raises(RuntimeError, match="profile snapshot failed"):
        browser_tool._get_session_info("task-profile-failed")

    cloud.assert_not_called()


def test_real_profile_helper_reuses_live_cached_cdp(monkeypatch):
    browser_tool._real_profile_cdp_cache.clear()
    browser_tool._real_profile_cdp_cache["cdp"] = "http://127.0.0.1:43112"
    monkeypatch.setattr(browser_tool, "_use_real_profile", lambda: True)
    monkeypatch.setattr(browser_tool, "_cdp_http_ready", lambda cdp: cdp.endswith("43112"))
    detect = Mock(side_effect=AssertionError("reuse must not detect or relaunch"))
    monkeypatch.setattr("hermes_cli.browser_connect.detect_default_chromium", detect)

    cdp, error = browser_tool._real_profile_cdp()

    assert (cdp, error) == ("http://127.0.0.1:43112", None)
    detect.assert_not_called()


def test_real_profile_helper_discards_stale_cache_and_fails_closed(monkeypatch):
    browser_tool._real_profile_cdp_cache.clear()
    browser_tool._real_profile_cdp_cache["cdp"] = "http://127.0.0.1:43113"
    monkeypatch.setattr(browser_tool, "_use_real_profile", lambda: True)
    monkeypatch.setattr(browser_tool, "_cdp_http_ready", lambda cdp: False)
    monkeypatch.setattr("hermes_cli.browser_connect.detect_default_chromium", lambda: "chrome")
    monkeypatch.setattr("hermes_cli.browser_connect.real_profile_copy_dir", lambda browser: "/tmp/hermes-profile")
    monkeypatch.setattr(browser_tool, "_agent_browser_get_cdp", lambda session: None)
    close = Mock()
    monkeypatch.setattr(browser_tool, "_agent_browser_close_session", close)
    monkeypatch.setattr("hermes_cli.browser_connect.snapshot_real_profile", lambda browser: (None, "snapshot unavailable"))

    cdp, error = browser_tool._real_profile_cdp()

    assert cdp is None
    assert "snapshot unavailable" in error
    assert "cdp" not in browser_tool._real_profile_cdp_cache
    close.assert_called_once_with(browser_tool._REAL_PROFILE_SESSION)


def test_synthetic_snapshot_fixture_uses_last_active_real_profile_session(monkeypatch):
    browser_tool._last_active_session_key.clear()
    browser_tool._last_active_session_key["task-synthetic"] = "task-synthetic"
    calls = []

    def fake_command(task_id, command, args=None, **kwargs):
        calls.append((task_id, command, args))
        return {"success": True, "data": {"snapshot": "heading Synthetic fixture", "refs": {"@e7": "fixture"}}}

    monkeypatch.setattr(browser_tool, "_run_browser_command", fake_command)

    result = json.loads(browser_tool.browser_snapshot(task_id="task-synthetic"))

    assert result == {
        "success": True,
        "snapshot": "heading Synthetic fixture",
        "element_count": 1,
    }
    assert calls == [("task-synthetic", "snapshot", ["-c"])]
