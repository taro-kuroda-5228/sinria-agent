"""Sanitized failure identities and state-aware warning deduplication.

The module intentionally accepts only category-level metadata. Raw exception text,
prompts, credentials, and payloads must stay at the local call site and are never
stored in an envelope or deduplication key.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Literal, Mapping

Retryability = Literal["never", "immediate", "backoff", "after_auth", "human_review"]
_RETRYABILITY = {"never", "immediate", "backoff", "after_auth", "human_review"}
_IDENTIFIER_RE = re.compile(r"[^a-z0-9_.-]+")
_REDACTED_RE = re.compile(r"\[redacted\]", re.IGNORECASE)


def sanitize_identifier(value: object, *, fallback: str = "unknown", limit: int = 96) -> str:
    """Return a bounded metadata token, never free-form text."""
    text = _REDACTED_RE.sub("secret", str(value or "").strip().lower())
    text = _IDENTIFIER_RE.sub("_", text).strip("_.-")
    return (text[:limit] or fallback)


def failure_fingerprint(
    *,
    provider: object,
    failure_class: object,
    dimensions: Mapping[str, object] | None = None,
) -> str:
    """Build a stable, non-reversible fingerprint from sanitized categories."""
    parts = [
        sanitize_identifier(provider),
        sanitize_identifier(failure_class),
    ]
    for key, value in sorted((dimensions or {}).items()):
        parts.append(f"{sanitize_identifier(key)}={sanitize_identifier(value)}")
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class FailureEnvelope:
    provider: str
    failure_class: str
    fingerprint: str
    run_id: str
    root_event_id: str
    retryability: Retryability

    @classmethod
    def create(
        cls,
        *,
        provider: object,
        failure_class: object,
        run_id: object,
        root_event_id: object,
        retryability: Retryability = "human_review",
        dimensions: Mapping[str, object] | None = None,
    ) -> "FailureEnvelope":
        retry = retryability if retryability in _RETRYABILITY else "human_review"
        safe_provider = sanitize_identifier(provider)
        safe_class = sanitize_identifier(failure_class)
        return cls(
            provider=safe_provider,
            failure_class=safe_class,
            fingerprint=failure_fingerprint(
                provider=safe_provider,
                failure_class=safe_class,
                dimensions=dimensions,
            ),
            run_id=sanitize_identifier(run_id),
            root_event_id=sanitize_identifier(root_event_id),
            retryability=retry,  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class WarningEmission:
    emit: bool
    fingerprint: str
    transition: str | None
    suppressed_count: int = 0


@dataclass
class _WarningState:
    state: str
    emitted_at: float
    suppressed_count: int = 0


class WarningDeduplicator:
    """Emit first occurrence, state transitions, and periodic summaries only."""

    def __init__(
        self,
        *,
        window_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_seconds = max(0.0, float(window_seconds))
        self._clock = clock
        self._states: dict[str, _WarningState] = {}
        self._lock = threading.Lock()

    def observe(
        self,
        *,
        provider: object,
        failure_class: object,
        state: object,
        dimensions: Mapping[str, object] | None = None,
    ) -> WarningEmission:
        safe_state = sanitize_identifier(state)
        fingerprint = failure_fingerprint(
            provider=provider,
            failure_class=failure_class,
            dimensions=dimensions,
        )
        now = self._clock()
        with self._lock:
            previous = self._states.get(fingerprint)
            if previous is None:
                self._states[fingerprint] = _WarningState(safe_state, now)
                return WarningEmission(True, fingerprint, None)

            if previous.state != safe_state:
                suppressed = previous.suppressed_count
                transition = f"{previous.state}->{safe_state}"
                self._states[fingerprint] = _WarningState(safe_state, now)
                return WarningEmission(True, fingerprint, transition, suppressed)

            if now - previous.emitted_at >= self.window_seconds:
                suppressed = previous.suppressed_count
                self._states[fingerprint] = _WarningState(safe_state, now)
                return WarningEmission(True, fingerprint, None, suppressed)

            previous.suppressed_count += 1
            return WarningEmission(False, fingerprint, None)
