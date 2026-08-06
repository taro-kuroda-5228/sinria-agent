"""Process-wide circuit breaker for provider subscription usage caps.

A provider's hard weekly/account quota is shared by every gateway channel and
agent instance. Retrying it in each lane cannot succeed before the reset and
wastes latency plus fallback calls. This module stores only sanitized provider
labels and reset timestamps; no prompts, credentials, or user data.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

_DEFAULT_COOLDOWN_SECONDS = 60 * 60
_MIN_COOLDOWN_SECONDS = 60

_lock = threading.Lock()
_blocked_until: dict[str, float] = {}


def _provider_key(provider: str | None, model: str | None = None) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized:
        return normalized
    return f"unknown:{str(model or '').strip().lower()}"


def _coerce_reset_at(error_context: Mapping[str, object] | None, *, now: float) -> float:
    context = error_context if isinstance(error_context, Mapping) else {}
    for key in ("resets_at", "reset_at"):
        value = context.get(key)
        try:
            reset_at = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if reset_at >= now + _MIN_COOLDOWN_SECONDS:
            return reset_at

    for key in ("resets_in_seconds", "reset_in_seconds", "retry_after"):
        value = context.get(key)
        try:
            seconds = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if seconds >= _MIN_COOLDOWN_SECONDS:
            return now + seconds

    return now + _DEFAULT_COOLDOWN_SECONDS


def record_hard_usage_limit(
    provider: str | None,
    model: str | None,
    error_context: Mapping[str, object] | None,
    *,
    now: float | None = None,
) -> float:
    """Open the shared provider circuit until its advertised reset."""

    current = time.time() if now is None else float(now)
    reset_at = _coerce_reset_at(error_context, now=current)
    key = _provider_key(provider, model)
    with _lock:
        _blocked_until[key] = max(reset_at, _blocked_until.get(key, 0.0))
        return _blocked_until[key]


def get_hard_usage_limit(
    provider: str | None,
    model: str | None,
    *,
    now: float | None = None,
) -> float | None:
    """Return the active reset epoch, clearing expired entries."""

    current = time.time() if now is None else float(now)
    key = _provider_key(provider, model)
    with _lock:
        reset_at = _blocked_until.get(key)
        if reset_at is None:
            return None
        if reset_at <= current:
            _blocked_until.pop(key, None)
            return None
        return reset_at


def clear_hard_usage_limits() -> None:
    """Clear process state (tests and explicit runtime reset only)."""

    with _lock:
        _blocked_until.clear()
