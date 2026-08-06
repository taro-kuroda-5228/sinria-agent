"""Routing signals from verified outcomes — local-first escalation substrate.

Architecture-centric P1 (docs/plans/2026-07-06-architecture-centric-agent-os-p1.md,
Task C): local-first routing means "run the smallest capable model, escalate
on *verified* failure, not on vibes". This module records the evidence that
decision needs: whenever a small/medium-tier model ends a practical-action
turn with a verification or execution gap, a sanitized recommendation row
(model / provider / tier / cause metadata only — never conversation text)
is appended to ``repair/routing_signals.jsonl``.

This wave records recommendations only. Automatic mid-turn model switching
deliberately stays out until the signal data justifies its design.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_sinria_home

_ESCALATION_CAUSES = ("verification_gap", "execution_incomplete")
_ESCALATION_TIERS = ("small", "medium")


def routing_signals_path(home: Optional[Path] = None) -> Path:
    return (home or get_sinria_home()) / "corrections" / "routing_signals.jsonl"


def build_routing_signal(
    *,
    model: Optional[str],
    provider: Optional[str],
    tier: str,
    cause_kind: str,
    escalation_model: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Build a sanitized escalation recommendation, or None when not warranted.

    Escalation is recommended only when a small/medium-tier model produced a
    verification or execution gap — large-tier gaps are workflow problems,
    not capacity problems, and benign causes carry no routing information.
    """
    if cause_kind not in _ESCALATION_CAUSES or tier not in _ESCALATION_TIERS:
        return None
    signal: dict[str, Any] = {
        "model": model or "",
        "provider": provider or "",
        "tier": tier,
        "cause_kind": cause_kind,
        "recommendation": "escalate",
        "timestamp": timestamp
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if escalation_model:
        signal["escalation_model"] = escalation_model
    return signal


def append_routing_signal(
    signal: dict[str, Any], *, path: Optional[Path] = None
) -> Path:
    target = path or routing_signals_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(signal, ensure_ascii=False, sort_keys=True) + "\n")
    return target


__all__ = ["append_routing_signal", "build_routing_signal", "routing_signals_path"]
