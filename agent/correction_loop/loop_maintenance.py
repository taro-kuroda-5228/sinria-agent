"""Built-in throttled promotion for the Sinria self-improvement loop (P1).

The per-turn choke point (`outcome_gap.record_practical_outcome_and_candidates`)
is the INPUT of the self-improvement loop: it records sanitized outcomes and
queues review-gated candidates. Nothing promoted those candidates into durable,
resolver-visible evidence unless a human ran `scripts/sinria_correction_loop_loop.py`
by hand — so the loop stayed open and durable memory never grew.

This module is the missing OUTPUT side, designed to run **by default on every
Sinria install** with no cron/launchd setup: it piggybacks on the same per-turn
choke point but is throttled to run the fail-closed auto-triage promotion at
most once per interval. Most turns only read a small marker file (≈ free); once
per interval it promotes recurring low-risk classes into `evidence.jsonl`.

Confidentiality: promotion delegates entirely to the fail-closed
`auto_triage.classify_auto_approval` policy. Freeform user corrections are never
auto-promoted unless the install explicitly opts in via
``allow_correction_auto_promote`` (default False = org/multi-tenant safe).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from hermes_constants import get_sinria_home

from .auto_triage import DEFAULT_MIN_OCCURRENCES, run_auto_triage

MAINTENANCE_MARKER_RELATIVE_PATH = Path("corrections") / "last_promotion.json"
DEFAULT_MIN_INTERVAL_HOURS = 24.0


def maintenance_marker_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / MAINTENANCE_MARKER_RELATIVE_PATH


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_last_run(marker_path: Path) -> datetime | None:
    """Return the last promotion timestamp, or None if never run / unreadable."""
    try:
        if not marker_path.exists():
            return None
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse_ts(data.get("last_run_at"))


def promotion_due(*, now: datetime, last_run: datetime | None, min_interval_hours: float) -> bool:
    """True when enough time has elapsed since the last promotion."""
    if last_run is None:
        return True
    elapsed_hours = (now - last_run).total_seconds() / 3600.0
    return elapsed_hours >= min_interval_hours


def run_due_promotion(
    *,
    now: datetime | None = None,
    home: Path | None = None,
    queue_path: Path | None = None,
    evidence_path: Path | None = None,
    marker_path: Path | None = None,
    min_interval_hours: float = DEFAULT_MIN_INTERVAL_HOURS,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    allow_correction_auto_promote: bool = False,
) -> dict | None:
    """Promote recurring low-risk classes if the throttle interval has elapsed.

    Returns the auto-triage report dict when a promotion ran, or None when it
    was throttled (recently run). All fail-closed policy is delegated to
    ``run_auto_triage``; this function only adds the once-per-interval gate and
    the marker bookkeeping so it is safe to call on every turn.
    """
    current = now or datetime.now(timezone.utc)
    marker = marker_path or maintenance_marker_path(home)
    if not promotion_due(now=current, last_run=read_last_run(marker), min_interval_hours=min_interval_hours):
        return None
    report = run_auto_triage(
        queue_path=queue_path,
        evidence_path=evidence_path,
        apply=True,
        approve_low_risk=True,
        min_occurrences=min_occurrences,
        allow_correction_auto_promote=allow_correction_auto_promote,
        now=current,
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "last_run_at": current.isoformat(),
                "auto_approved": len(report.get("auto_approved", [])),
                "pending_after": report.get("pending_after"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    # Loop-health trendline: one counters-only snapshot per promotion run, on
    # the same built-in cadence (no extra cron). Best-effort — the trendline
    # must never break promotion or a turn.
    try:
        from .loop_health import append_loop_health_snapshot, build_loop_health_snapshot

        base = marker.parent
        append_loop_health_snapshot(
            build_loop_health_snapshot(
                now=current,
                outcome_path=base / "outcome_gap.jsonl",
                queue_path=queue_path or (base / "review_queue.jsonl"),
                evidence_path=evidence_path or (base / "evidence.jsonl"),
                defects_path=base.parent / "repair" / "code_defects.jsonl",
                repair_outcomes_path=base.parent / "repair" / "repair_outcomes.jsonl",
                routing_signals_path=base / "routing_signals.jsonl",
                verify_nudges_path=base / "verify_nudges.jsonl",
                issue_proposals_path=base.parent / "repair" / "issue_proposals.jsonl",
                promotion_report=report,
            ),
            path=base / "loop_health.jsonl",
        )
    except Exception:
        pass
    return report


def correction_autopromote_enabled(config: dict | None) -> bool:
    """Return the install-type opt-in flag (default False = org/multi-tenant safe).

    Reads ``correction_loop.auto_promote_recurring_corrections`` from a loaded
    config dict. Fail-closed: anything other than an explicit truthy value keeps
    freeform user corrections human-review gated.
    """
    if not isinstance(config, dict):
        return False
    section = config.get("correction_loop")
    if not isinstance(section, dict):
        return False
    value = section.get("auto_promote_recurring_corrections", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _load_config_best_effort() -> dict:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def run_builtin_promotion_if_due(
    *,
    review_queue_path: Path | None = None,
    now: datetime | None = None,
    config: dict | None = None,
    min_interval_hours: float = DEFAULT_MIN_INTERVAL_HOURS,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
) -> dict | None:
    """Choke-point entry point for the built-in, throttled loop-close.

    Resolves the correction opt-in flag from config (best-effort), then runs the
    throttled promotion. Paths are co-located with ``review_queue_path`` so the
    call is hermetic: in production the choke point passes no queue path and home
    defaults apply; in tests a tmp queue path keeps evidence/marker in tmp and
    never touches the real Sinria home.
    """
    if review_queue_path is not None:
        base = Path(review_queue_path).parent
        queue_path: Path | None = Path(review_queue_path)
        evidence_path: Path | None = base / "evidence.jsonl"
        marker_path: Path | None = base / "last_promotion.json"
    else:
        queue_path = evidence_path = marker_path = None
    resolved_config = config if config is not None else _load_config_best_effort()
    return run_due_promotion(
        now=now,
        queue_path=queue_path,
        evidence_path=evidence_path,
        marker_path=marker_path,
        min_interval_hours=min_interval_hours,
        min_occurrences=min_occurrences,
        allow_correction_auto_promote=correction_autopromote_enabled(resolved_config),
    )
