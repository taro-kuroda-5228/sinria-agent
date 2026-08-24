"""Tests for the resume_pending session continuity path.

Covers the behaviour introduced to fix the ``Gateway shutting down ...
task will be interrupted`` follow-up bug (spec: PR #11852, builds on
PRs #9850, #9934, #7536):

1. When a gateway restart drain times out and agents are force-interrupted,
   the affected sessions are flagged ``resume_pending=True`` — not
   ``suspended`` — so the next user message on the same session_key
   auto-resumes from the existing transcript instead of getting routed
   through ``suspend_recently_active()`` and converted into a fresh
   session.

2. ``suspended=True`` (from ``/stop`` or stuck-loop escalation) still
   wins over ``resume_pending`` — the forced-wipe path is preserved.

3. The restart-resume system note injected into the next user message is
   a superset of the existing tool-tail auto-continue note (from
   PR #9934), using session-entry metadata rather than just transcript
   shape so it fires even when the interrupted transcript does NOT end
   with a ``tool`` role.

4. The existing ``.restart_failure_counts`` stuck-loop counter from
   PR #7536 remains the single source of escalation — no parallel
   counter is added on ``SessionEntry``.
"""

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, SendResult
from gateway.run import (
    _auto_continue_freshness_window,
    _coerce_gateway_timestamp,
    _is_fresh_gateway_interruption,
    _last_transcript_timestamp,
    _should_clear_resume_pending_after_turn,
)
from gateway.session import SessionEntry, SessionSource, SessionStore
from tests.gateway.restart_test_helpers import (
    make_restart_runner,
    make_restart_source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_resume_pending_is_cleared_only_after_successful_turn():
    """Interrupted/failed drain results must keep the restart recovery marker.

    Regression for dogfood failure: during gateway restart the interrupted run
    returned an empty final response and was normalized into a user-facing
    fallback, but the gateway cleared ``resume_pending`` before startup could
    auto-resume it.
    """
    assert _should_clear_resume_pending_after_turn({"final_response": "done"}) is True
    assert _should_clear_resume_pending_after_turn({"completed": True}) is True
    assert _should_clear_resume_pending_after_turn({"interrupted": True}) is False
    assert _should_clear_resume_pending_after_turn({"completed": False}) is False
    assert _should_clear_resume_pending_after_turn({"failed": True}) is False
    assert _should_clear_resume_pending_after_turn({"partial": True}) is False
    assert _should_clear_resume_pending_after_turn({"error": "boom"}) is False


def _make_source(platform=Platform.TELEGRAM, chat_id="123", user_id="u1"):
    return SessionSource(platform=platform, chat_id=chat_id, user_id=user_id)


def _make_store(tmp_path):
    return SessionStore(sessions_dir=tmp_path, config=GatewayConfig())


def _build_agent_history(history: list) -> list:
    """Mirror gateway/run.py's ``history → agent_history`` conversion.

    This is the transformation that strips ``timestamp`` off tool/tool_call
    rows before the agent sees them.  Tests that check the freshness gate
    must go through this conversion so they exercise the *real* data the
    note-injection code sees.
    """
    agent_history: list = []
    for msg in history:
        role = msg.get("role")
        if not role or role in {"session_meta", "system"}:
            continue
        has_tool_calls = "tool_calls" in msg
        has_tool_call_id = "tool_call_id" in msg
        is_tool_message = role == "tool"
        if has_tool_calls or has_tool_call_id or is_tool_message:
            agent_history.append({k: v for k, v in msg.items() if k != "timestamp"})
        else:
            content = msg.get("content")
            if content:
                agent_history.append({"role": role, "content": content})
    return agent_history


def _simulate_note_injection(
    history: list,
    user_message: str,
    resume_entry: SessionEntry | None,
    *,
    agent_history: list | None = None,
    window_secs: float | None = None,
) -> str:
    """Mirror the note-injection logic in gateway/run.py _run_agent().

    The freshness signal reads ``history[-1].timestamp`` (the raw transcript
    row), NOT ``agent_history[-1].timestamp`` (which has been stripped).
    Tests pass the raw ``history`` — ``agent_history`` is derived from it
    via the real conversion if not supplied explicitly.
    """
    if agent_history is None:
        agent_history = _build_agent_history(history)

    window = (
        float(window_secs)
        if window_secs is not None
        else _auto_continue_freshness_window()
    )
    interruption_is_fresh = _is_fresh_gateway_interruption(
        _last_transcript_timestamp(history),
        window_secs=window,
    )

    message = user_message
    is_resume_pending = bool(
        resume_entry is not None
        and getattr(resume_entry, "resume_pending", False)
        and interruption_is_fresh
    )
    has_fresh_tool_tail = bool(
        agent_history
        and agent_history[-1].get("role") == "tool"
        and interruption_is_fresh
    )

    if is_resume_pending:
        reason = getattr(resume_entry, "resume_reason", None) or "restart_timeout"
        reason_phrase = (
            "a gateway restart"
            if reason == "restart_timeout"
            else "a gateway shutdown"
            if reason == "shutdown_timeout"
            else "a gateway interruption"
        )
        message = (
            f"[System note: Your previous turn in this session was interrupted "
            f"by {reason_phrase}. The conversation history below is intact. "
            f"If it contains unfinished tool result(s), process them first and "
            f"summarize what was accomplished, then address the user's new "
            f"message below.]\n\n"
            + message
        )
    elif has_fresh_tool_tail:
        message = (
            "[System note: Your previous turn was interrupted before you could "
            "process the last tool result(s). The conversation history contains "
            "tool outputs you haven't responded to yet. Please finish processing "
            "those results and summarize what was accomplished, then address the "
            "user's new message below.]\n\n"
            + message
        )
    return message


# ---------------------------------------------------------------------------
# SessionEntry field + serialization
# ---------------------------------------------------------------------------


class TestSessionEntryResumeFields:
    def test_defaults(self):
        now = datetime.now()
        entry = SessionEntry(
            session_key="agent:main:telegram:dm:1",
            session_id="sid",
            created_at=now,
            updated_at=now,
        )
        assert entry.resume_pending is False
        assert entry.resume_reason is None
        assert entry.last_resume_marked_at is None

    def test_roundtrip_with_resume_fields(self):
        now = datetime(2026, 4, 18, 12, 0, 0)
        entry = SessionEntry(
            session_key="agent:main:telegram:dm:1",
            session_id="sid",
            created_at=now,
            updated_at=now,
            resume_pending=True,
            resume_reason="restart_timeout",
            last_resume_marked_at=now,
        )
        restored = SessionEntry.from_dict(entry.to_dict())
        assert restored.resume_pending is True
        assert restored.resume_reason == "restart_timeout"
        assert restored.last_resume_marked_at == now

    def test_from_dict_legacy_without_resume_fields(self):
        """Old sessions.json without the new fields deserialize cleanly."""
        now = datetime.now()
        legacy = {
            "session_key": "agent:main:telegram:dm:1",
            "session_id": "sid",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "chat_type": "dm",
        }
        restored = SessionEntry.from_dict(legacy)
        assert restored.resume_pending is False
        assert restored.resume_reason is None
        assert restored.last_resume_marked_at is None

    def test_malformed_timestamp_is_tolerated(self):
        now = datetime.now()
        data = {
            "session_key": "k",
            "session_id": "sid",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "resume_pending": True,
            "resume_reason": "restart_timeout",
            "last_resume_marked_at": "not-a-timestamp",
        }
        restored = SessionEntry.from_dict(data)
        # resume_pending still honoured, only the broken timestamp drops
        assert restored.resume_pending is True
        assert restored.resume_reason == "restart_timeout"
        assert restored.last_resume_marked_at is None


# ---------------------------------------------------------------------------
# SessionStore.mark_resume_pending / clear_resume_pending
# ---------------------------------------------------------------------------


class TestMarkResumePending:
    def test_marks_existing_session(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)

        assert store.mark_resume_pending(entry.session_key) is True
        refreshed = store._entries[entry.session_key]
        assert refreshed.resume_pending is True
        assert refreshed.resume_reason == "restart_timeout"
        assert refreshed.last_resume_marked_at is not None

    def test_custom_reason_persists(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)

        store.mark_resume_pending(entry.session_key, reason="shutdown_timeout")
        assert store._entries[entry.session_key].resume_reason == "shutdown_timeout"

    def test_returns_false_for_unknown_key(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.mark_resume_pending("no-such-key") is False

    def test_does_not_override_suspended(self, tmp_path):
        """suspended wins — mark_resume_pending is a no-op on a suspended entry."""
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.suspend_session(entry.session_key)

        assert store.mark_resume_pending(entry.session_key) is False
        e = store._entries[entry.session_key]
        assert e.suspended is True
        assert e.resume_pending is False

    def test_survives_roundtrip_through_json(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.mark_resume_pending(entry.session_key, reason="restart_timeout")

        # Reload from disk
        store2 = _make_store(tmp_path)
        store2._ensure_loaded()
        reloaded = store2._entries[entry.session_key]
        assert reloaded.resume_pending is True
        assert reloaded.resume_reason == "restart_timeout"


class TestClearResumePending:
    def test_clears_flag(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.mark_resume_pending(entry.session_key)

        assert store.clear_resume_pending(entry.session_key) is True
        e = store._entries[entry.session_key]
        assert e.resume_pending is False
        assert e.resume_reason is None
        assert e.last_resume_marked_at is None

    def test_returns_false_when_not_pending(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        # Not marked
        assert store.clear_resume_pending(entry.session_key) is False

    def test_returns_false_for_unknown_key(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.clear_resume_pending("no-such-key") is False


# ---------------------------------------------------------------------------
# SessionStore.get_or_create_session resume_pending behaviour
# ---------------------------------------------------------------------------


class TestGetOrCreateResumePending:
    def test_resume_pending_preserves_session_id(self, tmp_path):
        """This is THE core behavioural fix — resume_pending ≠ new session."""
        store = _make_store(tmp_path)
        source = _make_source()
        first = store.get_or_create_session(source)
        original_sid = first.session_id
        store.mark_resume_pending(first.session_key)

        second = store.get_or_create_session(source)
        assert second.session_id == original_sid
        assert second.was_auto_reset is False
        assert second.auto_reset_reason is None
        # Flag is NOT cleared on read — only on successful turn completion.
        assert second.resume_pending is True

    def test_suspended_still_creates_new_session(self, tmp_path):
        """Regression guard — suspended must still force a clean slate."""
        store = _make_store(tmp_path)
        source = _make_source()
        first = store.get_or_create_session(source)
        original_sid = first.session_id
        store.suspend_session(first.session_key)

        second = store.get_or_create_session(source)
        assert second.session_id != original_sid
        assert second.was_auto_reset is True
        assert second.auto_reset_reason == "suspended"

    def test_suspended_overrides_resume_pending(self, tmp_path):
        """Terminal escalation: a session that somehow has BOTH flags must
        behave like ``suspended`` — forced wipe + auto_reset_reason."""
        store = _make_store(tmp_path)
        source = _make_source()
        first = store.get_or_create_session(source)
        original_sid = first.session_id

        # Force the pathological state directly (normally mark_resume_pending
        # refuses to run when suspended=True, but a stuck-loop escalation
        # can set suspended=True AFTER resume_pending is set).
        with store._lock:
            e = store._entries[first.session_key]
            e.resume_pending = True
            e.resume_reason = "restart_timeout"
            e.suspended = True
            store._save()

        second = store.get_or_create_session(source)
        assert second.session_id != original_sid
        assert second.was_auto_reset is True
        assert second.auto_reset_reason == "suspended"


# ---------------------------------------------------------------------------
# SessionStore.suspend_recently_active skip behaviour
# ---------------------------------------------------------------------------


class TestSuspendRecentlyActiveSkipsResumePending:
    def test_resume_pending_entries_not_suspended(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.mark_resume_pending(entry.session_key)

        count = store.suspend_recently_active()
        assert count == 0
        e = store._entries[entry.session_key]
        assert e.suspended is False
        assert e.resume_pending is True

    def test_non_resume_pending_gets_resume_pending(self, tmp_path):
        """Non-resume sessions are now marked resume_pending (not suspended)."""
        store = _make_store(tmp_path)
        source_a = _make_source(chat_id="a")
        source_b = _make_source(chat_id="b")
        entry_a = store.get_or_create_session(source_a)
        entry_b = store.get_or_create_session(source_b)
        store.mark_resume_pending(entry_a.session_key)

        count = store.suspend_recently_active()
        # entry_a is already resume_pending → skipped. entry_b gets marked.
        assert count == 1
        assert store._entries[entry_a.session_key].suspended is False
        assert store._entries[entry_b.session_key].resume_pending is True
        assert store._entries[entry_b.session_key].suspended is False


# ---------------------------------------------------------------------------
# Restart-resume system-note injection
# ---------------------------------------------------------------------------


class TestResumePendingSystemNote:
    def _pending_entry(self, reason="restart_timeout") -> SessionEntry:
        now = datetime.now()
        return SessionEntry(
            session_key="agent:main:telegram:dm:1",
            session_id="sid",
            created_at=now,
            updated_at=now,
            resume_pending=True,
            resume_reason=reason,
            last_resume_marked_at=now,
        )

    def test_resume_pending_restart_note_mentions_restart(self):
        entry = self._pending_entry(reason="restart_timeout")
        result = _simulate_note_injection(
            history=[
                {"role": "assistant", "content": "in progress", "timestamp": time.time()},
            ],
            user_message="what happened?",
            resume_entry=entry,
        )
        assert "[System note:" in result
        assert "gateway restart" in result
        assert "what happened?" in result

    def test_resume_pending_shutdown_note_mentions_shutdown(self):
        entry = self._pending_entry(reason="shutdown_timeout")
        result = _simulate_note_injection(
            history=[
                {"role": "assistant", "content": "in progress", "timestamp": time.time()},
            ],
            user_message="ping",
            resume_entry=entry,
        )
        assert "gateway shutdown" in result

    def test_resume_pending_fires_without_tool_tail(self):
        """Key improvement over PR #9934: the restart-resume note fires
        even when the transcript's last role is NOT ``tool``."""
        entry = self._pending_entry()
        history = [
            {"role": "user", "content": "run a long thing", "timestamp": time.time() - 10},
            {"role": "assistant", "content": "ok, starting...", "timestamp": time.time()},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=entry)
        assert "[System note:" in result
        assert "gateway restart" in result

    def test_resume_pending_subsumes_tool_tail_note(self):
        """When BOTH conditions are true, the restart-resume note wins —
        no duplicate notes."""
        entry = self._pending_entry()
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 1},
            {"role": "tool", "tool_call_id": "c1", "content": "result",
             "timestamp": time.time()},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=entry)
        assert result.count("[System note:") == 1
        assert "gateway restart" in result
        # Old tool-tail wording absent
        assert "haven't responded to yet" not in result

    def test_no_resume_pending_preserves_tool_tail_note(self):
        """Regression: the old PR #9934 tool-tail behaviour is unchanged."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 1},
            {"role": "tool", "tool_call_id": "c1", "content": "result",
             "timestamp": time.time()},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=None)
        assert "[System note:" in result
        assert "tool result" in result

    def test_stale_resume_pending_does_not_inject_restart_note(self):
        """Old restart markers must not revive an unrelated stale task.

        The transcript's last row is from an hour ago — well outside the
        default 1h freshness window (fixture uses window=1800 to exercise
        the stale path without tying the test to the production default).
        """
        entry = self._pending_entry()
        entry.last_resume_marked_at = datetime.now() - timedelta(hours=1)

        history = [
            {"role": "assistant", "content": "old in progress",
             "timestamp": time.time() - 3600},
        ]
        result = _simulate_note_injection(
            history=history,
            user_message="start a new task",
            resume_entry=entry,
            window_secs=1800,
        )
        assert result == "start a new task"

    def test_fresh_tool_tail_preserves_auto_continue_note(self):
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 1},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "result",
                "timestamp": time.time(),
            },
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=None)
        assert "[System note:" in result
        assert "tool result" in result

    def test_stale_tool_tail_does_not_inject_auto_continue_note(self):
        """The core bug fix: stale tool-tail must not revive a dead task.

        Uses window_secs=1800 (30 min) to verify the gate fires at 1h —
        keeps the test stable regardless of the production default.
        """
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 3601},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "stale result",
                "timestamp": time.time() - 3600,
            },
        ]
        result = _simulate_note_injection(
            history,
            "start a new task",
            resume_entry=None,
            window_secs=1800,
        )
        assert result == "start a new task"

    def test_stale_tool_tail_with_production_data_shape(self):
        """Regression guard for #16802: exercise the REAL production path
        where ``agent_history`` has been stripped of timestamps.

        The original PR #16802 fix read ``agent_history[-1].get("timestamp")``
        — which is always ``None`` at runtime because the gateway strips
        ``timestamp`` off tool/tool_call rows in ``history → agent_history``.
        This test builds a stale history, runs it through the real
        ``_build_agent_history`` conversion, then asserts:

          1. The stripped ``agent_history`` carries NO timestamp (protects
             against someone "fixing" the original PR by re-adding the
             stripped field — which would break the API contract).
          2. The freshness gate still correctly classifies the transcript
             as stale because the signal is read from ``history`` BEFORE
             the strip.
          3. No auto-continue note is injected.
        """
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 7201},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "stale result",
                "timestamp": time.time() - 7200,  # 2 hours old
            },
        ]
        agent_history = _build_agent_history(history)

        # Invariant 1: strip contract preserved
        assert agent_history[-1]["role"] == "tool"
        assert "timestamp" not in agent_history[-1], (
            "agent_history tool rows must NOT carry a timestamp — the "
            "freshness gate must read from raw history, not agent_history"
        )

        # Invariant 2+3: stale classification, no note injection
        result = _simulate_note_injection(
            history,
            "start a new task",
            resume_entry=None,
            agent_history=agent_history,
        )
        assert result == "start a new task"

    def test_freshness_gate_disabled_via_zero_window(self):
        """window_secs=0 restores pre-fix behaviour (always inject)."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ], "timestamp": time.time() - 86400},
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "day-old result",
                "timestamp": time.time() - 86400,  # 24 hours old
            },
        ]
        result = _simulate_note_injection(
            history, "ping", resume_entry=None, window_secs=0,
        )
        assert "[System note:" in result
        assert "tool result" in result

    def test_legacy_history_without_timestamps_still_injects(self):
        """Transcripts predating timestamp persistence must keep the old
        behaviour — freshness unknown → treat as fresh."""
        history = [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=None)
        assert "[System note:" in result
        assert "tool result" in result

    def test_no_note_when_nothing_to_resume(self):
        history = [
            {"role": "user", "content": "hello", "timestamp": time.time() - 2},
            {"role": "assistant", "content": "hi", "timestamp": time.time() - 1},
        ]
        result = _simulate_note_injection(history, "ping", resume_entry=None)
        assert result == "ping"


# ---------------------------------------------------------------------------
# Freshness helpers
# ---------------------------------------------------------------------------


class TestFreshnessHelpers:
    def test_coerce_datetime(self):
        now = datetime.now()
        assert _coerce_gateway_timestamp(now) == pytest.approx(now.timestamp(), abs=1e-3)

    def test_coerce_epoch_seconds(self):
        assert _coerce_gateway_timestamp(1_700_000_000) == 1_700_000_000.0
        assert _coerce_gateway_timestamp(1_700_000_000.5) == 1_700_000_000.5

    def test_coerce_epoch_milliseconds(self):
        # Values > 10^10 treated as ms
        assert _coerce_gateway_timestamp(1_700_000_000_000) == 1_700_000_000.0

    def test_coerce_iso_string(self):
        iso = "2026-04-18T12:00:00+00:00"
        expected = datetime.fromisoformat(iso).timestamp()
        assert _coerce_gateway_timestamp(iso) == pytest.approx(expected, abs=1e-3)

    def test_coerce_iso_string_with_z_suffix(self):
        iso_z = "2026-04-18T12:00:00Z"
        expected = datetime.fromisoformat("2026-04-18T12:00:00+00:00").timestamp()
        assert _coerce_gateway_timestamp(iso_z) == pytest.approx(expected, abs=1e-3)

    def test_coerce_numeric_string(self):
        assert _coerce_gateway_timestamp("1700000000") == 1_700_000_000.0

    def test_coerce_rejects_garbage(self):
        assert _coerce_gateway_timestamp(None) is None
        assert _coerce_gateway_timestamp("") is None
        assert _coerce_gateway_timestamp("not-a-timestamp") is None
        assert _coerce_gateway_timestamp(True) is None  # bool rejected
        assert _coerce_gateway_timestamp(False) is None
        assert _coerce_gateway_timestamp([1, 2, 3]) is None

    def test_is_fresh_unknown_is_fresh(self):
        """Legacy-compat: unknown timestamp → fresh."""
        assert _is_fresh_gateway_interruption(None) is True
        assert _is_fresh_gateway_interruption("not-a-timestamp") is True

    def test_is_fresh_window_bounds(self):
        now = 1_700_000_000.0
        # 1h window, 30min old → fresh
        assert _is_fresh_gateway_interruption(
            now - 1800, now=now, window_secs=3600,
        ) is True
        # 1h window, 2h old → stale
        assert _is_fresh_gateway_interruption(
            now - 7200, now=now, window_secs=3600,
        ) is False
        # 1h window, exactly at boundary → fresh (<=)
        assert _is_fresh_gateway_interruption(
            now - 3600, now=now, window_secs=3600,
        ) is True

    def test_is_fresh_zero_window_always_fresh(self):
        """Opt-out: window_secs=0 disables the gate entirely."""
        assert _is_fresh_gateway_interruption(
            0.0, now=1_700_000_000.0, window_secs=0,
        ) is True
        assert _is_fresh_gateway_interruption(
            -1.0, now=1_700_000_000.0, window_secs=-5,
        ) is True

    def test_last_transcript_timestamp_skips_meta(self):
        history = [
            {"role": "user", "content": "hi", "timestamp": 100.0},
            {"role": "assistant", "content": "hey", "timestamp": 200.0},
            {"role": "session_meta", "content": "tools:{}", "timestamp": 999.0},
            {"role": "system", "content": "ignore", "timestamp": 999.0},
        ]
        assert _last_transcript_timestamp(history) == 200.0

    def test_last_transcript_timestamp_empty(self):
        assert _last_transcript_timestamp([]) is None
        assert _last_transcript_timestamp(None) is None

    def test_last_transcript_timestamp_row_without_timestamp(self):
        """Legacy transcript row (no timestamp) returns None → caller
        treats as fresh."""
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
        ]
        assert _last_transcript_timestamp(history) is None

    def test_auto_continue_freshness_window_reads_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUTO_CONTINUE_FRESHNESS", "7200")
        assert _auto_continue_freshness_window() == 7200.0

    def test_auto_continue_freshness_window_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_AUTO_CONTINUE_FRESHNESS", raising=False)
        # Default is 1 hour
        assert _auto_continue_freshness_window() == 3600.0

    def test_auto_continue_freshness_window_malformed_falls_back(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUTO_CONTINUE_FRESHNESS", "not-a-number")
        assert _auto_continue_freshness_window() == 3600.0

    def test_auto_continue_freshness_window_empty_falls_back(self, monkeypatch):
        monkeypatch.setenv("HERMES_AUTO_CONTINUE_FRESHNESS", "")
        assert _auto_continue_freshness_window() == 3600.0


# ---------------------------------------------------------------------------
# Drain-timeout path marks sessions resume_pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_timeout_marks_resume_pending():
    """End-to-end: a drain timeout during gateway stop should flag every
    active session as resume_pending BEFORE the interrupt fires, so the
    next startup's suspend_recently_active() does not destroy them."""
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    runner._restart_drain_timeout = 0.05

    running_agent = MagicMock()
    session_key_one = "agent:main:telegram:dm:A"
    session_key_two = "agent:main:telegram:dm:B"
    runner._running_agents = {
        session_key_one: running_agent,
        session_key_two: MagicMock(),
    }

    # Plug a mock session_store that records marks.
    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    # Both active sessions were marked with the shutdown_timeout reason.
    calls = session_store.mark_resume_pending.call_args_list
    marked = {args[0][0] for args in calls}
    assert marked == {session_key_one, session_key_two}
    for args in calls:
        assert args[0][1] == "shutdown_timeout"


@pytest.mark.asyncio
async def test_drain_timeout_uses_restart_reason_when_restarting():
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    runner._restart_drain_timeout = 0.05
    runner._restart_requested = True

    running_agent = MagicMock()
    runner._running_agents = {"agent:main:telegram:dm:A": running_agent}

    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop(restart=True, detached_restart=False, service_restart=True)

    calls = session_store.mark_resume_pending.call_args_list
    assert calls, "expected at least one mark_resume_pending call"
    for args in calls:
        assert args[0][1] == "restart_timeout"


@pytest.mark.asyncio
async def test_clean_drain_does_not_mark_resume_pending():
    """If the drain completes within timeout (no force-interrupt), no
    sessions should be flagged — the normal shutdown path is unchanged."""
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()

    running_agent = MagicMock()
    runner._running_agents = {"agent:main:telegram:dm:A": running_agent}

    # Finish the agent before the (generous) drain deadline
    async def finish_agent():
        await asyncio.sleep(0.05)
        runner._running_agents.clear()

    asyncio.create_task(finish_agent())

    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    session_store.mark_resume_pending.assert_not_called()
    running_agent.interrupt.assert_not_called()


@pytest.mark.asyncio
async def test_drain_timeout_only_marks_still_running_sessions():
    """A session that finished gracefully during the drain window must
    NOT be marked ``resume_pending`` — it completed cleanly and its
    next turn should be a normal fresh turn, not one prefixed with the
    restart-interruption system note.

    Regression guard for using ``self._running_agents`` at timeout
    rather than the ``active_agents`` drain-start snapshot.
    """
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    # Long enough for the finisher to exit, short enough to still time out
    # with the stuck session still present.
    runner._restart_drain_timeout = 0.3

    session_key_finisher = "agent:main:telegram:dm:A"
    session_key_stuck = "agent:main:telegram:dm:B"
    runner._running_agents = {
        session_key_finisher: MagicMock(),
        session_key_stuck: MagicMock(),
    }

    async def finish_one():
        await asyncio.sleep(0.05)
        runner._running_agents.pop(session_key_finisher, None)

    asyncio.create_task(finish_one())

    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    calls = session_store.mark_resume_pending.call_args_list
    marked = {args[0][0] for args in calls}
    # Only the session still running at timeout is marked; the finisher is not.
    assert marked == {session_key_stuck}


@pytest.mark.asyncio
async def test_external_shutdown_premarks_running_sessions_before_drain_timeout():
    """External SIGTERM may be SIGKILLed by launchd before the drain timeout.

    Mark non-restart shutdowns before waiting so startup can auto-resume if the
    service manager kills the process mid-turn.  A session that finishes during
    the drain should still be able to clear the marker through the normal
    successful-turn path; this test only verifies the durable marker is written
    early enough.
    """
    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    runner._restart_requested = False
    setattr(runner, "_signal_initiated_shutdown", True)
    runner._restart_drain_timeout = 0.2

    session_key = "agent:main:telegram:dm:early"
    runner._running_agents = {session_key: MagicMock()}
    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    async def finish_during_drain():
        await asyncio.sleep(0.02)
        runner._running_agents.pop(session_key, None)

    asyncio.create_task(finish_during_drain())

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    session_store.mark_resume_pending.assert_any_call(session_key, "shutdown_timeout")


@pytest.mark.asyncio
async def test_drain_timeout_skips_pending_sentinel_sessions():
    """Pending sentinels — sessions whose AIAgent construction hasn't
    produced a real agent yet — are skipped by
    ``_interrupt_running_agents()``.  The resume_pending marking must
    mirror that: no agent started means no turn was interrupted.
    """
    from gateway.run import _AGENT_PENDING_SENTINEL

    runner, adapter = make_restart_runner()
    adapter.disconnect = AsyncMock()
    runner._restart_drain_timeout = 0.05

    session_key_real = "agent:main:telegram:dm:A"
    session_key_sentinel = "agent:main:telegram:dm:B"
    runner._running_agents = {
        session_key_real: MagicMock(),
        session_key_sentinel: _AGENT_PENDING_SENTINEL,
    }

    session_store = MagicMock()
    session_store.mark_resume_pending = MagicMock(return_value=True)
    runner.session_store = session_store

    with patch("gateway.status.remove_pid_file"), patch(
        "gateway.status.write_runtime_status"
    ):
        await runner.stop()

    calls = session_store.mark_resume_pending.call_args_list
    marked = {args[0][0] for args in calls}
    assert marked == {session_key_real}


# ---------------------------------------------------------------------------
# Gateway startup auto-resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_auto_resume_schedules_fresh_pending_sessions():
    """Fresh resume_pending sessions should continue automatically after startup.

    This closes the UX gap where restart recovery only happened if the user sent
    another message after the gateway came back.
    """
    runner, adapter = make_restart_runner()
    source = make_restart_source(
        chat_id="resume-chat", chat_type="group", thread_id="topic-1"
    )
    pending_entry = SessionEntry(
        session_key="agent:main:telegram:group:resume-chat:topic-1",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="group",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=datetime.now(),
    )
    runner.session_store._entries = {pending_entry.session_key: pending_entry}
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert isinstance(event, MessageEvent)
    assert event.internal is True
    assert event.message_type == MessageType.TEXT
    assert event.source == source
    # Text is empty — the existing _is_resume_pending branch in
    # _handle_message_with_agent owns the system-note injection so we don't
    # double it up.
    assert event.text == ""


@pytest.mark.asyncio
async def test_startup_auto_resume_accepts_timezone_aware_marker():
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="resume-aware")
    now = datetime.now(timezone.utc)
    entry = SessionEntry(
        session_key="agent:main:telegram:dm:resume-aware",
        session_id="sid-aware",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=now,
    )
    runner.session_store._entries = {entry.session_key: entry}
    adapter.handle_message = AsyncMock()

    assert runner._schedule_resume_pending_sessions() == 1
    await asyncio.sleep(0)
    adapter.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_auto_resume_schedules_all_sibling_sessions_for_same_user():
    """Every fresh interrupted channel resumes without another user message.

    Each internal event must retain its own SessionSource so concurrent Discord
    lanes cannot be cross-routed even though all of them are resumed together.
    """
    runner, adapter = make_restart_runner()
    runner.adapters = {Platform.DISCORD: adapter}
    # chat_type must match what the stored key encodes: the persisted key is
    # derived from the persisted origin, so a fixture where they disagree is
    # not a state the gateway can produce.
    older_source = replace(
        _make_source(platform=Platform.DISCORD, chat_id="main-chat"), chat_type="group"
    )
    newer_source = replace(
        _make_source(platform=Platform.DISCORD, chat_id="sidework-chat"),
        chat_type="group",
    )
    older_marker = datetime.now() - timedelta(seconds=30)
    newer_marker = datetime.now()
    older_entry = SessionEntry(
        session_key="agent:main:discord:group:main-chat:u1",
        session_id="sid-main",
        created_at=older_marker,
        updated_at=older_marker,
        origin=older_source,
        platform=Platform.DISCORD,
        chat_type="group",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=older_marker,
    )
    newer_entry = SessionEntry(
        session_key="agent:main:discord:group:sidework-chat:u1",
        session_id="sid-side",
        created_at=newer_marker,
        updated_at=newer_marker,
        origin=newer_source,
        platform=Platform.DISCORD,
        chat_type="group",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=newer_marker,
    )
    runner.session_store._entries = {
        older_entry.session_key: older_entry,
        newer_entry.session_key: newer_entry,
    }
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 2
    assert adapter.handle_message.await_count == 2
    events = [call.args[0] for call in adapter.handle_message.await_args_list]
    assert {event.source.chat_id for event in events} == {"main-chat", "sidework-chat"}
    assert all(event.internal is True and event.text == "" for event in events)
    assert getattr(adapter, "sent_calls") == []


@pytest.mark.asyncio
async def test_startup_auto_resume_deduplicates_inflight_session():
    """Repeated readiness checks must not start the same lane twice."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="resume-chat")
    pending_entry = SessionEntry(
        session_key="agent:main:telegram:dm:resume-chat",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=datetime.now(),
    )
    runner.session_store._entries = {pending_entry.session_key: pending_entry}
    release = asyncio.Event()

    async def block_until_released(_event):
        await release.wait()

    adapter.handle_message = AsyncMock(side_effect=block_until_released)

    assert runner._schedule_resume_pending_sessions() == 1
    assert runner._schedule_resume_pending_sessions() == 0
    await asyncio.sleep(0)
    assert adapter.handle_message.await_count == 1

    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_startup_auto_resume_bounds_parallel_dispatch(monkeypatch):
    """All lanes are scheduled, but provider-facing work is concurrency-bounded."""
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "2")
    runner, adapter = make_restart_runner()
    now = datetime.now()
    entries = []
    for index in range(6):
        lane_platform = Platform.TELEGRAM if index < 3 else Platform.DISCORD
        source = SessionSource(
            platform=lane_platform,
            chat_id=f"resume-{index}",
            chat_type="dm",
            user_id="u1",
        )
        entries.append(
            SessionEntry(
                session_key=f"agent:main:{lane_platform.value}:dm:resume-{index}",
                session_id=f"sid-{index}",
                created_at=now,
                updated_at=now,
                origin=source,
                platform=lane_platform,
                chat_type="dm",
                resume_pending=True,
                resume_reason="restart_timeout",
                last_resume_marked_at=now,
            )
        )
    runner.session_store._entries = {entry.session_key: entry for entry in entries}

    release = asyncio.Event()
    active = 0
    max_active = 0
    started = []

    adapter._session_tasks = {}

    async def process_lane(event):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.append(event.source.chat_id)
        await release.wait()
        active -= 1

    async def enqueue_like_real_adapter(event):
        session_key = (
            f"agent:main:{event.source.platform.value}:dm:{event.source.chat_id}"
        )
        adapter._session_tasks[session_key] = asyncio.create_task(process_lane(event))

    adapter.handle_message = AsyncMock(side_effect=enqueue_like_real_adapter)
    runner.adapters[Platform.DISCORD] = adapter

    assert runner._schedule_resume_pending_sessions(platform=Platform.TELEGRAM) == 3
    assert runner._schedule_resume_pending_sessions(platform=Platform.DISCORD) == 3
    tasks = list(runner._background_tasks)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(started) == 2
    assert max_active == 2

    release.set()
    await asyncio.gather(*tasks)
    assert set(started) == {f"resume-{index}" for index in range(6)}
    assert max_active == 2


@pytest.mark.asyncio
async def test_startup_auto_resume_dedupes_until_real_adapter_task_finishes():
    """Production adapters enqueue work; dedupe must outlive handle_message()."""
    runner, adapter = make_restart_runner()
    now = datetime.now()
    source = make_restart_source(chat_id="resume-production-contract")
    session_key = "agent:main:telegram:dm:resume-production-contract"
    entry = SessionEntry(
        session_key=session_key,
        session_id="sid-production-contract",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=now,
    )
    runner.session_store._entries = {session_key: entry}

    first_release = asyncio.Event()
    successor_release = asyncio.Event()
    adapter._session_tasks = {}

    async def successor_lane():
        await successor_release.wait()

    async def process_lane():
        await first_release.wait()
        adapter._session_tasks[session_key] = asyncio.create_task(successor_lane())

    async def enqueue_like_real_adapter(_event):
        adapter._session_tasks[session_key] = asyncio.create_task(process_lane())

    adapter.handle_message = AsyncMock(side_effect=enqueue_like_real_adapter)

    assert runner._schedule_resume_pending_sessions() == 1
    wrapper_task = next(iter(runner._background_tasks))
    await asyncio.sleep(0)
    assert adapter.handle_message.await_count == 1
    assert not wrapper_task.done()

    assert runner._schedule_resume_pending_sessions() == 0
    assert adapter.handle_message.await_count == 1

    first_release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not wrapper_task.done()
    assert runner._schedule_resume_pending_sessions() == 0

    successor_release.set()
    await wrapper_task


@pytest.mark.asyncio
async def test_startup_auto_resume_schedule_failure_does_not_block_other_lanes():
    """A failure creating one lane task must not suppress later lanes."""
    runner, adapter = make_restart_runner()
    now = datetime.now()
    entries = []
    for index in range(2):
        source = make_restart_source(chat_id=f"resume-{index}")
        entries.append(
            SessionEntry(
                session_key=f"agent:main:telegram:dm:resume-{index}",
                session_id=f"sid-{index}",
                created_at=now,
                updated_at=now,
                origin=source,
                platform=Platform.TELEGRAM,
                chat_type="dm",
                resume_pending=True,
                resume_reason="restart_timeout",
                last_resume_marked_at=now,
            )
        )
    runner.session_store._entries = {entry.session_key: entry for entry in entries}
    adapter.handle_message = AsyncMock()

    real_create_task = asyncio.create_task
    attempts = 0

    def create_task_with_first_failure(coro):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic scheduling failure")
        return real_create_task(coro)

    with patch("gateway.run.asyncio.create_task", side_effect=create_task_with_first_failure):
        scheduled = runner._schedule_resume_pending_sessions()

    await asyncio.sleep(0)

    assert scheduled == 1
    assert adapter.handle_message.await_count == 1
    assert adapter.handle_message.await_args_list[0].args[0].source.chat_id == "resume-1"


@pytest.mark.asyncio
async def test_startup_auto_resume_includes_crash_recovery():
    """Crash-recovered sessions (reason=restart_interrupted) are also auto-resumed.

    suspend_recently_active() marks in-flight sessions with resume_reason
    "restart_interrupted" when the previous gateway exit was not clean
    (crash/SIGKILL/OOM).  These should get the same magic continuation as
    drain-timeout interruptions.
    """
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="crash-chat")
    pending_entry = SessionEntry(
        session_key="agent:main:telegram:dm:crash-chat",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_interrupted",
        last_resume_marked_at=datetime.now(),
    )
    runner.session_store._entries = {pending_entry.session_key: pending_entry}
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 1
    adapter.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_stale_entries():
    """Entries older than the freshness window must not be auto-resumed."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="stale-chat")
    stale_marker = datetime.now() - timedelta(
        seconds=_auto_continue_freshness_window() + 60
    )
    stale_entry = SessionEntry(
        session_key="agent:main:telegram:dm:stale-chat",
        session_id="sid",
        created_at=stale_marker,
        updated_at=stale_marker,
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=stale_marker,
    )
    runner.session_store._entries = {stale_entry.session_key: stale_entry}
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()

    assert scheduled == 0
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_suspended_and_originless():
    """suspended entries and entries with no origin are excluded."""
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="ok")
    suspended_entry = SessionEntry(
        session_key="agent:main:telegram:dm:suspended",
        session_id="sid-s",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        suspended=True,
        last_resume_marked_at=datetime.now(),
    )
    originless = SessionEntry(
        session_key="agent:main:telegram:dm:originless",
        session_id="sid-o",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=None,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=datetime.now(),
    )
    runner.session_store._entries = {
        suspended_entry.session_key: suspended_entry,
        originless.session_key: originless,
    }
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()

    assert scheduled == 0
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_disallowed_reasons():
    """Reasons outside the auto-resume set (e.g. a future custom reason) are skipped.

    These sessions still auto-resume on the next real user message via the
    existing _is_resume_pending branch — we just don't synthesize a turn
    for them at startup.
    """
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="other")
    other_entry = SessionEntry(
        session_key="agent:main:telegram:dm:other",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="manual_resume_request",
        last_resume_marked_at=datetime.now(),
    )
    runner.session_store._entries = {other_entry.session_key: other_entry}
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()

    assert scheduled == 0
    adapter.handle_message.assert_not_called()


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_when_adapter_unavailable():
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="resume-chat")
    pending_entry = SessionEntry(
        session_key="agent:main:telegram:dm:resume-chat",
        session_id="sid",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=source,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=datetime.now(),
    )
    runner.session_store._entries = {pending_entry.session_key: pending_entry}
    runner.adapters = {}
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()

    assert scheduled == 0
    adapter.handle_message.assert_not_called()


# ---------------------------------------------------------------------------
# Shutdown banner wording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_banner_uses_try_to_resume_wording():
    """The notification sent before drain should hedge the resume promise
    — the session-continuity fix is best-effort (stuck-loop counter can
    still escalate to suspended)."""
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner._running_agents["agent:main:telegram:dm:999"] = MagicMock()

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    msg = adapter.sent[0]
    assert "restarting" in msg
    assert "try to resume" in msg


@pytest.mark.asyncio
async def test_restart_notifies_home_channel_even_without_active_sessions():
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == [
        "⚠️ Gateway restarting — Your current task will be interrupted. "
        "Send any message after restart and I'll try to resume where you left off."
    ]


@pytest.mark.asyncio
async def test_restart_home_channel_notification_dedupes_active_chat():
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner._running_agents["agent:main:telegram:dm:999"] = MagicMock()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="999",
        name="Ops Home",
    )

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1


@pytest.mark.asyncio
async def test_restart_home_channel_notification_not_fanned_out_when_active_thread_exists():
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    session_key = "agent:main:telegram:group:999"
    runner.session_store._entries[session_key] = MagicMock(
        origin=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="999",
            chat_type="group",
            user_id="u1",
            thread_id="topic-7",
        )
    )
    runner._running_agents[session_key] = MagicMock()
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="999",
        name="Ops Home",
    )

    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    assert adapter.sent_calls[0][2] == {"thread_id": "topic-7"}


@pytest.mark.asyncio
async def test_restart_home_channel_notification_ignores_false_send_result():
    runner, adapter = make_restart_runner()
    runner._restart_requested = True
    runner.config.platforms[Platform.TELEGRAM].home_channel = HomeChannel(
        platform=Platform.TELEGRAM,
        chat_id="home-42",
        name="Ops Home",
    )
    adapter.send = AsyncMock(return_value=SendResult(success=False, error="network down"))

    await runner._notify_active_sessions_of_shutdown()

    adapter.send.assert_called_once()


# ---------------------------------------------------------------------------
# Stuck-loop escalation integration
# ---------------------------------------------------------------------------


class TestStuckLoopEscalation:
    """The existing .restart_failure_counts counter (PR #7536) remains the
    single source of terminal escalation — no parallel counter on
    SessionEntry was added.  After the configured threshold, the startup
    path flips suspended=True which overrides resume_pending."""

    def test_escalation_via_stuck_loop_counter_overrides_resume_pending(
        self, tmp_path, monkeypatch
    ):
        """Simulate a session that keeps getting restart-interrupted and
        hits the stuck-loop threshold: next startup should force it to
        fresh-session despite resume_pending being set."""
        import json

        from gateway.run import GatewayRunner

        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.mark_resume_pending(entry.session_key, reason="restart_timeout")

        # Simulate counter already at threshold (3 consecutive interrupted
        # restarts).  _suspend_stuck_loop_sessions will flip suspended=True.
        counts_file = tmp_path / ".restart_failure_counts"
        counts_file.write_text(json.dumps({entry.session_key: 3}))

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        runner = object.__new__(GatewayRunner)
        runner.session_store = store

        suspended_count = GatewayRunner._suspend_stuck_loop_sessions(runner)
        assert suspended_count == 1
        assert store._entries[entry.session_key].suspended is True
        # resume_pending is still set on the entry, but suspended wins in
        # get_or_create_session so the next message still gets a new sid.
        second = store.get_or_create_session(source)
        assert second.session_id != entry.session_id
        assert second.auto_reset_reason == "suspended"

    def test_successful_turn_flow_clears_both_counter_and_resume_pending(
        self, tmp_path, monkeypatch
    ):
        """The gateway's post-turn cleanup should clear both signals so a
        future restart-interrupt starts with a fresh counter."""
        import json

        from gateway.run import GatewayRunner

        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        store.mark_resume_pending(entry.session_key, reason="restart_timeout")

        counts_file = tmp_path / ".restart_failure_counts"
        counts_file.write_text(json.dumps({entry.session_key: 2}))

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        runner = object.__new__(GatewayRunner)
        runner.session_store = store

        GatewayRunner._clear_restart_failure_count(runner, entry.session_key)
        store.clear_resume_pending(entry.session_key)

        assert store._entries[entry.session_key].resume_pending is False
        assert not counts_file.exists()

    def test_increment_restart_failure_counts_uses_atomic_json_write(
        self, tmp_path, monkeypatch
    ):
        from gateway.run import GatewayRunner

        source = _make_source()
        session_key = _make_store(tmp_path).get_or_create_session(source).session_key

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        calls = []

        def _fake_atomic_json_write(path, payload, **kwargs):
            calls.append((path, payload, kwargs))

        monkeypatch.setattr("gateway.run.atomic_json_write", _fake_atomic_json_write)

        runner = object.__new__(GatewayRunner)
        runner._increment_restart_failure_counts({session_key})

        assert calls == [
            (
                tmp_path / ".restart_failure_counts",
                {session_key: 1},
                {"indent": None},
            )
        ]

    def test_clear_restart_failure_count_uses_atomic_json_write_when_entries_remain(
        self, tmp_path, monkeypatch
    ):
        import json

        from gateway.run import GatewayRunner

        source = _make_source()
        session_key = _make_store(tmp_path).get_or_create_session(source).session_key
        other_key = "agent:main:telegram:dm:other"
        counts_file = tmp_path / ".restart_failure_counts"
        counts_file.write_text(
            json.dumps({session_key: 2, other_key: 1}),
            encoding="utf-8",
        )

        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        calls = []

        def _fake_atomic_json_write(path, payload, **kwargs):
            calls.append((path, payload, kwargs))

        monkeypatch.setattr("gateway.run.atomic_json_write", _fake_atomic_json_write)

        runner = object.__new__(GatewayRunner)
        runner._clear_restart_failure_count(session_key)

        assert calls == [
            (
                tmp_path / ".restart_failure_counts",
                {other_key: 1},
                {"indent": None},
            )
        ]


# ---------------------------------------------------------------------------
# Startup auto-resume concurrency hardening
#
# The semaphore introduced for bounded dispatch means a lane can sit queued
# for a long time between scheduling and dispatch.  Everything checked at
# schedule time (marker set, lane idle, gateway running) can change while the
# wrapper waits — these tests pin the dispatch-time revalidation contract.
# ---------------------------------------------------------------------------


def _make_pending_entry(source, session_key):
    now = datetime.now()
    return SessionEntry(
        session_key=session_key,
        session_id=f"sid-{session_key.rsplit(':', 1)[-1]}",
        created_at=now,
        updated_at=now,
        origin=source,
        platform=source.platform,
        chat_type=source.chat_type or "dm",
        resume_pending=True,
        resume_reason="restart_timeout",
        last_resume_marked_at=now,
    )


def _two_queued_lanes(runner):
    """Two fresh pending lanes; lane-a's dispatch blocks until released."""
    key_a = "agent:main:telegram:dm:lane-a"
    key_b = "agent:main:telegram:dm:lane-b"
    runner.session_store._entries = {
        key_a: _make_pending_entry(make_restart_source(chat_id="lane-a"), key_a),
        key_b: _make_pending_entry(make_restart_source(chat_id="lane-b"), key_b),
    }
    release = asyncio.Event()

    async def hold_first_lane(event):
        if event.source.chat_id == "lane-a":
            await release.wait()

    return key_a, key_b, release, hold_first_lane


def _dispatched_chat_ids(adapter):
    return {call.args[0].source.chat_id for call in adapter.handle_message.await_args_list}


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_lane_cleared_while_queued(monkeypatch):
    """A queued wrapper must revalidate the marker after the semaphore wait.

    While lane B waits for a dispatch slot, its resume obligation can be
    fulfilled by a normal user turn (which clears resume_pending).  Dispatching
    the stale empty internal event afterwards would run a bare LLM turn and
    post an unprompted reply into the channel.
    """
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "1")
    runner, adapter = make_restart_runner()
    key_a, key_b, release, hold_first_lane = _two_queued_lanes(runner)
    adapter.handle_message = AsyncMock(side_effect=hold_first_lane)

    assert runner._schedule_resume_pending_sessions() == 2
    tasks = list(runner._background_tasks)
    for _ in range(3):
        await asyncio.sleep(0)
    assert adapter.handle_message.await_count == 1

    # Lane B's obligation is fulfilled elsewhere while its wrapper is queued.
    runner.session_store._entries[key_b].resume_pending = False
    release.set()
    await asyncio.gather(*tasks)

    assert _dispatched_chat_ids(adapter) == {"lane-a"}


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_lane_with_running_agent():
    """Never synthesize a resume turn into a lane the runner already owns.

    The live turn is resume-aware by itself (the _is_resume_pending branch
    fires on any message while the marker is set), so dispatching here can
    only interrupt the user's in-flight work.  The marker must survive.
    """
    runner, adapter = make_restart_runner()
    key = "agent:main:telegram:dm:busy-runner"
    runner.session_store._entries = {
        key: _make_pending_entry(make_restart_source(chat_id="busy-runner"), key)
    }
    runner._running_agents[key] = MagicMock()
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    adapter.handle_message.assert_not_called()
    assert runner.session_store._entries[key].resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_lane_already_active_in_adapter():
    """Dispatching into an adapter-owned lane must not happen at all.

    Without the guard the empty internal event goes down the busy path: it
    replaces the user's queued follow-up, interrupts the live turn, and posts
    a busy ack the user never asked for.  Uses the REAL handle_message so a
    regression reproduces the full blast radius.
    """
    runner, adapter = make_restart_runner()
    runner._busy_ack_ts = {}
    source = make_restart_source(chat_id="busy-adapter")
    key = "agent:main:telegram:dm:busy-adapter"
    runner.session_store._entries = {key: _make_pending_entry(source, key)}

    hold = asyncio.Event()
    live_task = asyncio.create_task(hold.wait())
    adapter._active_sessions[key] = asyncio.Event()
    adapter._session_tasks[key] = live_task
    queued_user_event = MessageEvent(
        text="user follow-up", message_type=MessageType.TEXT, source=source
    )
    adapter._pending_messages[key] = queued_user_event
    running_agent = MagicMock()
    runner._running_agents[key] = running_agent

    try:
        scheduled = runner._schedule_resume_pending_sessions()
        for _ in range(3):
            await asyncio.sleep(0)

        assert scheduled == 0
        assert adapter._pending_messages[key] is queued_user_event
        running_agent.interrupt.assert_not_called()
        assert adapter.sent_calls == []
        assert runner.session_store._entries[key].resume_pending is True
    finally:
        hold.set()
        live_task.cancel()
        for task in list(runner._background_tasks):
            task.cancel()
        await asyncio.gather(*runner._background_tasks, live_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_startup_auto_resume_rechecks_busy_lane_after_semaphore_wait(monkeypatch):
    """The busy-lane guard must run again after the semaphore wait.

    A user can start a turn on lane B while B's wrapper is still queued behind
    the concurrency cap; dispatching then would interrupt that live turn.
    """
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "1")
    runner, adapter = make_restart_runner()
    key_a, key_b, release, hold_first_lane = _two_queued_lanes(runner)
    adapter.handle_message = AsyncMock(side_effect=hold_first_lane)

    assert runner._schedule_resume_pending_sessions() == 2
    tasks = list(runner._background_tasks)
    for _ in range(3):
        await asyncio.sleep(0)
    assert adapter.handle_message.await_count == 1

    # A real user message claims lane B while its wrapper is queued.
    adapter._active_sessions[key_b] = asyncio.Event()
    release.set()
    await asyncio.gather(*tasks)

    assert _dispatched_chat_ids(adapter) == {"lane-a"}
    assert runner.session_store._entries[key_b].resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_releases_slot_after_resume_obligation_ends(monkeypatch):
    """The slot must not be pinned by unrelated follow-up user turns.

    Once the lane's resume obligation is over (marker cleared by the resume
    turn), a fresh task installed by new user traffic must not keep holding
    the semaphore — that starves every still-pending lane behind it.
    """
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "1")
    runner, adapter = make_restart_runner()
    key_a = "agent:main:telegram:dm:lane-a"
    key_b = "agent:main:telegram:dm:lane-b"
    runner.session_store._entries = {
        key_a: _make_pending_entry(make_restart_source(chat_id="lane-a"), key_a),
        key_b: _make_pending_entry(make_restart_source(chat_id="lane-b"), key_b),
    }

    unrelated_hold = asyncio.Event()
    b_dispatched = asyncio.Event()

    async def resume_turn_for_lane_a():
        # The resume turn completes successfully: the normal turn path clears
        # the marker...
        runner.session_store._entries[key_a].resume_pending = False
        # ...and unrelated new user traffic takes over the lane's task slot.
        adapter._session_tasks[key_a] = asyncio.create_task(unrelated_hold.wait())

    async def enqueue_like_real_adapter(event):
        if event.source.chat_id == "lane-a":
            adapter._session_tasks[key_a] = asyncio.create_task(resume_turn_for_lane_a())
        else:
            b_dispatched.set()

    adapter.handle_message = AsyncMock(side_effect=enqueue_like_real_adapter)

    assert runner._schedule_resume_pending_sessions() == 2
    tasks = list(runner._background_tasks)
    try:
        await asyncio.wait_for(b_dispatched.wait(), timeout=1.0)
    finally:
        unrelated_hold.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        leftover = adapter._session_tasks.get(key_a)
        if leftover is not None and not leftover.done():
            leftover.cancel()
            await asyncio.gather(leftover, return_exceptions=True)


@pytest.mark.asyncio
async def test_startup_auto_resume_aborts_queued_wrapper_when_draining(monkeypatch):
    """A queued wrapper must not dispatch into a gateway that is shutting down.

    During a resume storm a re-restart flips _draining while wrappers are
    still waiting for a slot; dispatching then creates dangling adapter tasks
    and can post drain-refusal text into lanes where nobody typed anything.
    """
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "1")
    runner, adapter = make_restart_runner()
    key_a, key_b, release, hold_first_lane = _two_queued_lanes(runner)
    adapter.handle_message = AsyncMock(side_effect=hold_first_lane)

    assert runner._schedule_resume_pending_sessions() == 2
    tasks = list(runner._background_tasks)
    for _ in range(3):
        await asyncio.sleep(0)
    assert adapter.handle_message.await_count == 1

    runner._draining = True
    release.set()
    await asyncio.gather(*tasks)

    assert _dispatched_chat_ids(adapter) == {"lane-a"}
    assert runner.session_store._entries[key_b].resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_wrapper_cancellation_wins_over_cancelled_lane_task():
    """Cancelling the wrapper while it awaits an already-cancelled lane task
    must cancel the wrapper itself.

    Shutdown cancels _background_tasks fire-and-forget; if the wrapper
    swallows its own cancellation because the lane task happened to be
    cancelled in the same tick, it keeps running untracked through teardown.
    """
    runner, adapter = make_restart_runner()
    key = "agent:main:telegram:dm:cancel-lane"
    runner.session_store._entries = {
        key: _make_pending_entry(make_restart_source(chat_id="cancel-lane"), key)
    }

    hold = asyncio.Event()

    async def enqueue(event):
        adapter._session_tasks[key] = asyncio.create_task(hold.wait())

    adapter.handle_message = AsyncMock(side_effect=enqueue)

    assert runner._schedule_resume_pending_sessions() == 1
    wrapper_task = next(iter(runner._background_tasks))
    for _ in range(3):
        await asyncio.sleep(0)  # wrapper reaches the shielded await

    processing = adapter._session_tasks[key]
    processing.cancel()
    await asyncio.sleep(0)  # lane task settles as cancelled
    wrapper_task.cancel()
    await asyncio.gather(wrapper_task, return_exceptions=True)

    assert wrapper_task.cancelled()


@pytest.mark.asyncio
async def test_startup_auto_resume_preserves_same_parent_thread_isolation():
    """Two Discord threads under the SAME parent channel resume independently
    with their exact origins — replies must route back into each thread.

    Sibling-thread session keys differ only in the thread_id segment (with
    thread_sessions_per_user disabled the user id is deliberately not
    appended), so thread_id is the only thing keeping these lanes apart.
    """
    runner, adapter = make_restart_runner()
    runner.adapters = {Platform.DISCORD: adapter}
    now = datetime.now()
    entries = {}
    for thread_id in ("T1", "T2"):
        source = SessionSource(
            platform=Platform.DISCORD,
            chat_id="parent-channel",
            chat_type="group",
            user_id="u1",
            thread_id=thread_id,
            parent_chat_id="parent-channel",
        )
        key = f"agent:main:discord:group:parent-channel:{thread_id}"
        entries[key] = SessionEntry(
            session_key=key,
            session_id=f"sid-{thread_id}",
            created_at=now,
            updated_at=now,
            origin=source,
            platform=Platform.DISCORD,
            chat_type="group",
            resume_pending=True,
            resume_reason="restart_timeout",
            last_resume_marked_at=now,
        )
    runner.session_store._entries = entries
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 2
    events = [call.args[0] for call in adapter.handle_message.await_args_list]
    assert {event.source.thread_id for event in events} == {"T1", "T2"}
    assert {event.source.chat_id for event in events} == {"parent-channel"}
    assert {event.source.parent_chat_id for event in events} == {"parent-channel"}
    assert all(event.internal is True and event.text == "" for event in events)
    assert adapter.sent_calls == []


class TestEmptyInternalResumeSinkGuard:
    """Building blocks of the defensive sink guard: classify synthesized
    auto-resume probe events, and consume markers that transcript freshness
    has already proven dead (so reconnect passes stop re-dispatching them)."""

    def _probe(self, **overrides):
        from gateway.run import _INTERNAL_KIND_AUTO_RESUME

        kwargs = dict(
            text="",
            message_type=MessageType.TEXT,
            source=_make_source(),
            internal=True,
            internal_kind=_INTERNAL_KIND_AUTO_RESUME,
        )
        kwargs.update(overrides)
        return MessageEvent(**kwargs)

    def test_empty_internal_event_is_probe(self):
        from gateway.run import _is_empty_internal_resume_event

        assert _is_empty_internal_resume_event(self._probe()) is True

    def test_untagged_empty_internal_event_is_not_a_probe(self):
        """Another synthesizer's empty internal event must still run.

        Rendered webhook prompts (``msgraph_webhook._render_prompt``) and other
        internal injections can legitimately come out empty.  Classifying those
        as auto-resume probes would make the sink guard swallow them with no
        reply and no error — a silent drop of a real notification.
        """
        from gateway.run import _is_empty_internal_resume_event

        assert _is_empty_internal_resume_event(self._probe(internal_kind=None)) is False
        assert (
            _is_empty_internal_resume_event(self._probe(internal_kind="watch_notice"))
            is False
        )

    @pytest.mark.asyncio
    async def test_the_scheduler_actually_produces_a_recognized_probe(self):
        """Close the loop: the event the scheduler emits must arm the guard.

        The tag and the classifier are useless if they disagree, and nothing
        else in the pipeline would notice — the turn would simply run bare.
        """
        from gateway.run import _is_empty_internal_resume_event

        runner, adapter = make_restart_runner()
        source = make_restart_source(chat_id="probe-shape")
        key = runner._session_key_for_source(source)
        runner.session_store._entries = {key: _make_pending_entry(source, key)}
        adapter.handle_message = AsyncMock()

        assert runner._schedule_resume_pending_sessions() == 1
        await asyncio.sleep(0)

        event = adapter.handle_message.await_args.args[0]
        assert _is_empty_internal_resume_event(event) is True

    def test_whitespace_only_internal_event_is_probe(self):
        from gateway.run import _is_empty_internal_resume_event

        assert _is_empty_internal_resume_event(self._probe(text=" \n\t")) is True

    def test_user_event_is_not_probe(self):
        from gateway.run import _is_empty_internal_resume_event

        assert _is_empty_internal_resume_event(self._probe(internal=False)) is False

    def test_internal_event_with_text_is_not_probe(self):
        from gateway.run import _is_empty_internal_resume_event

        assert _is_empty_internal_resume_event(self._probe(text="do the thing")) is False

    def test_internal_event_with_media_is_not_probe(self):
        from gateway.run import _is_empty_internal_resume_event

        assert (
            _is_empty_internal_resume_event(self._probe(media_urls=["/tmp/x.png"]))
            is False
        )

    def test_sink_guard_truth_table(self):
        """The full decision the guard makes before an unprompted turn.

        This condition lives ~1500 lines into ``_run_agent``, which cannot be
        driven without a live model, so the predicate is pinned here instead.
        """
        from gateway.run import _auto_resume_probe_has_no_work

        def _decide(probe, resume_note, tool_tail):
            return _auto_resume_probe_has_no_work(
                is_auto_resume_probe=probe,
                resume_note_will_fire=resume_note,
                tool_tail_note_will_fire=tool_tail,
            )

        # A real user turn is never suppressed, whatever the notes say.
        for resume_note in (True, False):
            for tool_tail in (True, False):
                assert _decide(False, resume_note, tool_tail) is False

        # A probe with recovery work to do runs.
        assert _decide(True, True, False) is False
        assert _decide(True, False, True) is False, (
            "an unanswered tool tail is recovery work even without the marker"
        )
        assert _decide(True, True, True) is False

        # A probe with nothing to say must not reach the model.
        assert _decide(True, False, False) is True

    def test_noop_suppression_is_process_scoped_and_leaves_the_marker(self, tmp_path):
        """A no-op probe must not retire the durable recovery marker.

        ``resume_pending`` has a second job beyond the recovery note: it
        short-circuits the reset policy in ``get_or_create_session``, keeping
        an interrupted transcript alive across the 4am / idle boundary.  A
        transient staleness verdict taken at gateway startup must not spend
        it — only a completed turn may, per
        ``_should_clear_resume_pending_after_turn``.
        """
        from gateway.run import _suppress_further_resume_probes

        runner, _ = make_restart_runner()
        runner._auto_resume_noop_lanes = set()
        store = _make_store(tmp_path)
        entry = store.get_or_create_session(_make_source())
        store.mark_resume_pending(entry.session_key)

        assert _suppress_further_resume_probes(runner, entry.session_key) is True
        assert runner._auto_resume_noop_lanes == {entry.session_key}
        assert store._entries[entry.session_key].resume_pending is True
        # The transcript is still protected from the reset policy.
        assert store.get_or_create_session(_make_source()).session_id == entry.session_id

    def test_noop_suppression_tolerates_a_missing_key_or_runner_state(self):
        from gateway.run import _suppress_further_resume_probes

        runner, _ = make_restart_runner()
        runner._auto_resume_noop_lanes = set()
        assert _suppress_further_resume_probes(runner, None) is False
        assert _suppress_further_resume_probes(object(), "some-key") is False

    @pytest.mark.asyncio
    async def test_a_noop_lane_is_not_re_probed_by_a_reconnect_pass(self):
        """The suppression has to reach the scheduler, or nothing changed.

        Adapter reconnects re-run the pass; without this the same dead lane
        would be re-loaded and re-dispatched on every reconnect until its
        marker aged out of the freshness window.
        """
        from gateway.run import _suppress_further_resume_probes

        runner, adapter = make_restart_runner()
        runner._auto_resume_noop_lanes = set()
        source = make_restart_source(chat_id="noop-lane")
        key = runner._session_key_for_source(source)
        runner.session_store._entries = {key: _make_pending_entry(source, key)}
        adapter.handle_message = AsyncMock()

        assert runner._schedule_resume_pending_sessions() == 1
        await asyncio.sleep(0)

        _suppress_further_resume_probes(runner, key)
        assert runner._schedule_resume_pending_sessions() == 0
        await asyncio.sleep(0)
        assert adapter.handle_message.await_count == 1


# ---------------------------------------------------------------------------
# Routing-key drift
#
# Auto-resume bookkeeping (per-lane task map, live-turn guard, marker re-read,
# dedupe chase) is keyed by the PERSISTED ``entry.session_key``.  The turn it
# dispatches runs under the key the pipeline recomputes from the persisted
# origin.  When those two disagree the lane resumes into a different session,
# never clears its own marker, and is invisible to every dedupe guard — so it
# is re-dispatched by each later pass.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_lane_whose_origin_no_longer_maps_to_its_key():
    """A lane stored under a key its origin no longer produces must be skipped.

    Reachable by flipping ``group_sessions_per_user`` /
    ``thread_sessions_per_user`` in config.yaml and restarting, or by binding
    an interrupted channel to a Workspace conversation: the persisted key
    stops matching the key the dispatch pipeline recomputes from the origin.
    """
    runner, adapter = make_restart_runner()
    source = make_restart_source(chat_id="drifted-chat")
    routed_key = runner._session_key_for_source(source)
    stored_key = routed_key + ":legacy-suffix"
    assert stored_key != routed_key

    runner.session_store._entries = {
        stored_key: _make_pending_entry(source, stored_key)
    }
    adapter.handle_message = AsyncMock()

    scheduled = runner._schedule_resume_pending_sessions()
    await asyncio.sleep(0)

    assert scheduled == 0
    adapter.handle_message.assert_not_called()
    # The marker survives: the user's next real message routes to the same new
    # key the turn would have used, and recovers there.
    assert runner.session_store._entries[stored_key].resume_pending is True


@pytest.mark.asyncio
async def test_startup_auto_resume_skips_lane_whose_key_drifts_while_queued(monkeypatch):
    """Key drift discovered after the semaphore wait must also abort dispatch.

    Same hazard as above, in the window the concurrency cap opens: the lane
    passed enumeration, then the derivation changed before it got a slot.
    """
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "1")
    runner, adapter = make_restart_runner()
    key_a, key_b, release, hold_first_lane = _two_queued_lanes(runner)
    adapter.handle_message = AsyncMock(side_effect=hold_first_lane)

    assert runner._schedule_resume_pending_sessions() == 2
    tasks = list(runner._background_tasks)
    for _ in range(3):
        await asyncio.sleep(0)
    assert adapter.handle_message.await_count == 1

    # Lane B's origin stops mapping to its stored key while B is queued.
    entry_b = runner.session_store._entries[key_b]
    entry_b.origin = replace(entry_b.origin, chat_id="renamed-lane-b")
    release.set()
    await asyncio.gather(*tasks)

    assert _dispatched_chat_ids(adapter) == {"lane-a"}
    assert runner.session_store._entries[key_b].resume_pending is True


def test_auto_resume_skip_reason_accepts_a_lane_whose_key_still_matches():
    """The drift guard must not reject the ordinary case.

    Every eligible lane goes through this predicate, so a false positive here
    would disable startup auto-resume entirely.
    """
    from gateway.run import _auto_resume_skip_reason

    runner, _ = make_restart_runner()
    source = make_restart_source(chat_id="steady-chat")
    key = runner._session_key_for_source(source)
    entry = _make_pending_entry(source, key)

    assert (
        _auto_resume_skip_reason(
            entry,
            window=_auto_continue_freshness_window(),
            resolve_session_key=runner._session_key_for_source,
        )
        is None
    )


def test_auto_resume_skip_reason_tolerates_a_failing_key_resolver():
    """A resolver that raises must not veto an otherwise eligible lane.

    The resolver reaches into the session store; a transient store error must
    degrade to "no drift detected" rather than silently disabling recovery.
    """
    from gateway.run import _auto_resume_skip_reason

    runner, _ = make_restart_runner()
    source = make_restart_source(chat_id="resolver-boom")
    key = runner._session_key_for_source(source)

    def _boom(_source):
        raise RuntimeError("session store unavailable")

    assert (
        _auto_resume_skip_reason(
            _make_pending_entry(source, key),
            window=_auto_continue_freshness_window(),
            resolve_session_key=_boom,
        )
        is None
    )


@pytest.mark.asyncio
async def test_startup_auto_resume_lane_failure_does_not_block_the_queue(monkeypatch):
    """One lane raising inside dispatch must not strand the lanes behind it.

    With the concurrency cap at 1, a wrapper that dies without releasing its
    slot would deadlock every remaining lane for the rest of the process.
    """
    monkeypatch.setenv("SINRIA_AUTO_RESUME_MAX_CONCURRENT", "1")
    runner, adapter = make_restart_runner()
    key_a, key_b, _release, _hold = _two_queued_lanes(runner)

    async def explode_on_first_lane(event):
        if event.source.chat_id == "lane-a":
            raise RuntimeError("adapter blew up")

    adapter.handle_message = AsyncMock(side_effect=explode_on_first_lane)

    assert runner._schedule_resume_pending_sessions() == 2
    tasks = list(runner._background_tasks)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert any(isinstance(r, RuntimeError) for r in results)
    assert _dispatched_chat_ids(adapter) == {"lane-a", "lane-b"}
    # The failed lane keeps its marker for the next pass; nothing was sent.
    assert runner.session_store._entries[key_a].resume_pending is True
    assert adapter.sent_calls == []
