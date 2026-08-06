"""Gateway-side clarify primitive (blocking event-based queue).

The ``clarify`` tool needs to ask the user a question and block the agent
thread until they respond.  In CLI mode this is trivial — ``input()`` is
synchronous.  In gateway mode the agent runs on a worker thread while the
event loop handles the user's reply, so we need a thread-safe primitive
that:

  * stores a pending clarify request (with a generated ``clarify_id``),
  * blocks the agent thread on an ``Event``,
  * resolves the wait when the gateway's button-callback or text-intercept
    fires ``resolve_gateway_clarify(clarify_id, response)``,
  * supports timeouts so a user who never responds does NOT hang the agent
    thread forever (which would also pin the gateway's running-agent guard).

State is module-level (same shape as ``tools.approval``) so platform
adapters can call ``resolve_gateway_clarify`` without holding a back-
reference to the ``GatewayRunner`` instance.

Two delivery paths from the adapter:

  1. **Button UI** — adapters override ``send_clarify`` to render inline
     buttons (e.g. Telegram ``InlineKeyboardMarkup``).  The button
     callback resolves with the chosen string.  A final "Other (type
     answer)" button enters text-capture mode for free-form responses.

  2. **Text fallback** — adapters without rich UI render a numbered list.
     The user replies with a number ("2") or with free text; the gateway's
     ``_handle_message`` intercepts the reply and resolves directly.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DELIVERY_FAILED_RESPONSE = "[clarify prompt could not be delivered]"


# =========================================================================
# Module-level state
# =========================================================================

@dataclass
class _ClarifyEntry:
    """One pending clarify request inside a gateway session."""
    clarify_id: str
    session_key: str
    question: str
    choices: Optional[List[str]]
    requester_user_id: Optional[str] = None
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[str] = None
    awaiting_text: bool = False  # set when user picked "Other" or clarify is open-ended

    def signature(self) -> Dict[str, object]:
        return {
            "clarify_id": self.clarify_id,
            "session_key": self.session_key,
            "question": self.question,
            "choices": list(self.choices) if self.choices else None,
        }


_lock = threading.RLock()
# clarify_id → _ClarifyEntry  (primary lookup for button callbacks)
_entries: Dict[str, _ClarifyEntry] = {}
# session_key → list[clarify_id]  (FIFO; for text-fallback intercept and session cleanup)
_session_index: Dict[str, List[str]] = {}


# =========================================================================
# Public API — agent-thread side
# =========================================================================

def register(
    clarify_id: str,
    session_key: str,
    question: str,
    choices: Optional[List[str]],
    requester_user_id: Optional[str] = None,
) -> _ClarifyEntry:
    """Register a pending clarify request and return the entry.

    The caller (gateway clarify_callback) will then send the prompt to the
    user and block on ``wait_for_response(clarify_id, timeout)``.
    """
    entry = _ClarifyEntry(
        clarify_id=clarify_id,
        session_key=session_key,
        question=question,
        choices=list(choices) if choices else None,
        requester_user_id=requester_user_id,
        # Open-ended (no choices) → next message IS the response, no buttons needed.
        awaiting_text=not bool(choices),
    )
    with _lock:
        _entries[clarify_id] = entry
        _session_index.setdefault(session_key, []).append(clarify_id)
    return entry


def accept_delivery_or_watch_failure(
    delivery_future: Future[Any],
    *,
    clarify_id: str,
    observation_timeout: float = 15.0,
) -> bool:
    """Preserve a slow clarify interaction and report only definite failure."""
    def _resolve_late_failure(done: Future[Any]) -> None:
        try:
            result = done.result()
            delivered = bool(getattr(result, "success", False))
            error = getattr(result, "error", None)
        except Exception as exc:
            delivered = False
            error = exc
        if delivered:
            logger.info("Clarify delivery completed after observation timeout (clarify_id=%s)", clarify_id)
            return
        logger.warning("Clarify delivery failed after observation timeout (clarify_id=%s): %s", clarify_id, error or "unknown delivery error")
        resolve_gateway_clarify(clarify_id, DELIVERY_FAILED_RESPONSE)

    try:
        result = delivery_future.result(timeout=observation_timeout)
    except FutureTimeoutError:
        delivery_future.add_done_callback(_resolve_late_failure)
        return True
    except Exception as exc:
        logger.warning("Clarify delivery failed before prompt presentation (clarify_id=%s): %s", clarify_id, exc)
        return False
    delivered = bool(getattr(result, "success", False))
    if not delivered:
        logger.warning("Clarify delivery rejected before prompt presentation (clarify_id=%s): %s", clarify_id, getattr(result, "error", None) or "unknown delivery error")
    return delivered


def wait_for_response(clarify_id: str, timeout: float) -> Optional[str]:
    """Block on the entry's event until resolved or timeout fires.

    Polls in 1-second slices so the agent's inactivity heartbeat keeps
    firing — without this, ``Event.wait(timeout=600)`` blocks the thread
    for 10 minutes with zero activity touches and the gateway's inactivity
    watchdog kills the agent while the user is still typing.

    Returns the resolved response string, or ``None`` on timeout.
    """
    with _lock:
        entry = _entries.get(clarify_id)
    if entry is None:
        return None

    try:
        from tools.environments.base import touch_activity_if_due
    except Exception:  # pragma: no cover - optional
        touch_activity_if_due = None

    deadline = time.monotonic() + max(timeout, 0.0)
    activity_state = {"last_touch": time.monotonic(), "start": time.monotonic()}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if entry.event.wait(timeout=min(1.0, remaining)):
            break
        if touch_activity_if_due is not None:
            touch_activity_if_due(activity_state, "waiting for user clarify response")

    with _lock:
        # Remove from indices regardless of resolution outcome.
        _entries.pop(clarify_id, None)
        ids = _session_index.get(entry.session_key)
        if ids and clarify_id in ids:
            ids.remove(clarify_id)
            if not ids:
                _session_index.pop(entry.session_key, None)

    return entry.response


# =========================================================================
# Public API — gateway / adapter side
# =========================================================================

def resolve_gateway_clarify(
    clarify_id: str,
    response: str,
    requester_user_id: Optional[str] = None,
) -> bool:
    """Unblock the agent thread waiting on ``clarify_id``.

    Returns True if an entry was found, owned by the requester, and resolved.
    Legacy entries without an owner remain compatible with existing callers.
    """
    with _lock:
        entry = _entries.get(clarify_id)
        if entry is None or entry.event.is_set():
            return False
        if entry.requester_user_id is not None and (
            not entry.requester_user_id or entry.requester_user_id != requester_user_id
        ):
            return False
        entry.response = str(response) if response is not None else ""
        entry.awaiting_text = False
        entry.event.set()
    return True


def get_pending_for_session(
    session_key: str,
    requester_user_id: Optional[str] = None,
) -> Optional[_ClarifyEntry]:
    """Return the oldest pending clarify owned by this user, or None.

    Choice prompts accept direct typed replies too. Requiring the user to
    click ``Other`` first can strand the agent thread for the full timeout
    when a natural-language approval arrives through a messaging gateway.
    In shared sessions, an entry bound to a requester cannot be resolved by
    another participant's natural-language message.
    """
    with _lock:
        ids = _session_index.get(session_key) or []
        for cid in ids:
            entry = _entries.get(cid)
            if entry is None or entry.event.is_set():
                continue
            if entry.requester_user_id is not None and (
                not entry.requester_user_id or entry.requester_user_id != requester_user_id
            ):
                continue
            return entry
        return None


def mark_awaiting_text(
    clarify_id: str,
    requester_user_id: Optional[str] = None,
) -> bool:
    """Switch an entry into free-text capture mode after "Other" is picked."""
    with _lock:
        entry = _entries.get(clarify_id)
        if entry is None:
            return False
        if entry.requester_user_id is not None and (
            not entry.requester_user_id or entry.requester_user_id != requester_user_id
        ):
            return False
        entry.awaiting_text = True
        return True


def has_pending(session_key: str) -> bool:
    """Return True when this session has at least one pending clarify entry."""
    with _lock:
        ids = _session_index.get(session_key) or []
        return any(_entries.get(cid) is not None for cid in ids)


def clear_session(session_key: str) -> int:
    """Resolve and drop every pending clarify for a session.

    Used by session-boundary cleanup (e.g. ``/new``, gateway shutdown,
    cached-agent eviction) so blocked agent threads don't hang past the
    end of their session.  Returns the number of entries cancelled.
    """
    with _lock:
        ids = list(_session_index.pop(session_key, []) or [])
        entries = [_entries.pop(cid, None) for cid in ids]
    cancelled = 0
    for entry in entries:
        if entry is None:
            continue
        # Empty string sentinel — agent code can distinguish from a real
        # response by inspecting the wait_for_response return value
        # alongside its own timeout deadline.  Most callers just treat any
        # falsy result as "user did not respond".
        entry.response = ""
        entry.event.set()
        cancelled += 1
    return cancelled


# =========================================================================
# Config
# =========================================================================

def get_clarify_timeout() -> int:
    """Read the clarify response timeout (seconds) from config.

    Defaults to 600 (10 minutes) — long enough for the user to type a
    thoughtful response, short enough that an abandoned prompt eventually
    unblocks the agent thread instead of pinning the running-agent guard
    forever.

    Reads ``agent.clarify_timeout`` from config.yaml.
    """
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        agent_cfg = cfg.get("agent", {}) or {}
        return int(agent_cfg.get("clarify_timeout", 600))
    except Exception:
        return 600


# =========================================================================
# Per-session notify hook (gateway → adapter bridge)
# =========================================================================
# Mirrors tools.approval's _gateway_notify_cbs: the gateway registers a
# per-session callback that sends the clarify prompt to the user.  The
# callback bridges sync→async (runs on the agent thread; schedules the
# adapter ``send_clarify`` call on the event loop).

_notify_cbs: Dict[str, Callable[[_ClarifyEntry], None]] = {}


def register_notify(session_key: str, cb: Callable[[_ClarifyEntry], None]) -> None:
    """Register a per-session notify callback used by ``clarify_callback``."""
    with _lock:
        _notify_cbs[session_key] = cb


def unregister_notify(session_key: str) -> None:
    """Drop the per-session notify callback and cancel any pending clarify entries."""
    with _lock:
        _notify_cbs.pop(session_key, None)
    # Cancel any pending entries so blocked threads unwind when the run
    # ends (interrupt, completion, gateway shutdown).
    clear_session(session_key)


def get_notify(session_key: str) -> Optional[Callable[[_ClarifyEntry], None]]:
    with _lock:
        return _notify_cbs.get(session_key)
