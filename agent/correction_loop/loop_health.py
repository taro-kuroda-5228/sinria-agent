"""Loop-health trendline for the Sinria self-improvement loop.

``loop_metrics`` answers "is the loop healthy right now?"; nothing recorded
history, so "the more Sinria is used, the stronger it gets" could never be
demonstrated over time. This module appends one counters-only snapshot per
built-in promotion run (same 24h throttle, no extra cron) to
``SINRIA_HOME/corrections/loop_health.jsonl``.

Confidentiality: snapshots hold integer counters and sanitized signature
category tokens only — never raw conversation text, summaries, or samples.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sinria_constants import get_sinria_home

LOOP_HEALTH_RELATIVE_PATH = Path("corrections") / "loop_health.jsonl"
DEFAULT_WINDOW_DAYS = 7
TOP_SIGNATURES_LIMIT = 5
_FAILURE_SIGNATURE_GAP_MARKER = "|sinria_failure_signature="


def _failure_signature(row: dict[str, Any]) -> str:
    signature = row.get("failure_signature")
    if isinstance(signature, str) and _FAILURE_SIGNATURE_RE.fullmatch(signature):
        return signature
    gap_summary = row.get("gap_summary")
    if not isinstance(gap_summary, str):
        return ""
    _base, marker, embedded = gap_summary.rpartition(_FAILURE_SIGNATURE_GAP_MARKER)
    if marker and _FAILURE_SIGNATURE_RE.fullmatch(embedded):
        return embedded
    return ""


_FAILURE_SIGNATURE_RE = re.compile(
    r"^(?:tool=[a-z_]{1,40}:cls=[A-Za-z_]{1,40}|exit=[a-z_]{1,40})$"
)
_CATEGORY_TOKEN_RE = re.compile(r"^[a-z_]{1,40}$")
_TIMESTAMP_RE = re.compile(r"^[0-9T:+.\-Z]{1,40}$")
_COUNTER_FIELDS = frozenset({
    "window_days",
    "outcomes_total",
    "outcomes_window",
    "gaps_window",
    "unique_root_events_window",
    "signature_classes_window",
    "queue_proposed",
    "queue_approved",
    "queue_merged",
    "evidence_total",
    "defects_window",
    "tool_error_defects_window",
    "issue_proposals_total",
    "routing_signals_total",
    "verify_nudges_total",
    "promotion_auto_approved",
    "promotion_pending_after",
})


def loop_health_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / LOOP_HEALTH_RELATIVE_PATH


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_rows(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _count_lines(path: Path | None) -> int:
    return len(_load_rows(path))


def sanitize_loop_health_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return only counters and allowlisted category tokens safe for storage/output.

    JSONL files are local metadata, but may be partially written, hand-edited,
    or produced by an older build. Never trust arbitrary keys or category
    strings when replaying the trendline through the CLI.
    """
    if not isinstance(snapshot, dict):
        return {}
    safe: dict[str, Any] = {}
    timestamp = snapshot.get("timestamp")
    if isinstance(timestamp, str) and _TIMESTAMP_RE.fullmatch(timestamp):
        safe["timestamp"] = timestamp
    for field in _COUNTER_FIELDS:
        value = snapshot.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[field] = value

    if "top_signatures_window" in snapshot:
        top_signatures: list[dict[str, Any]] = []
        raw_top = snapshot.get("top_signatures_window")
        if isinstance(raw_top, list):
            for item in raw_top[:TOP_SIGNATURES_LIMIT]:
                if not isinstance(item, dict):
                    continue
                signature = item.get("signature")
                count = item.get("count")
                if (
                    isinstance(signature, str)
                    and _FAILURE_SIGNATURE_RE.fullmatch(signature)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                ):
                    top_signatures.append({"signature": signature, "count": count})
        safe["top_signatures_window"] = top_signatures

    if "repair_outcomes_window" in snapshot:
        repair_outcomes: dict[str, int] = {}
        raw_repair = snapshot.get("repair_outcomes_window")
        if isinstance(raw_repair, dict):
            for event, count in raw_repair.items():
                if (
                    isinstance(event, str)
                    and _CATEGORY_TOKEN_RE.fullmatch(event)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                ):
                    repair_outcomes[event] = count
        safe["repair_outcomes_window"] = dict(sorted(repair_outcomes.items()))
    return safe


def build_loop_health_snapshot(
    *,
    now: datetime | None = None,
    home: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    outcome_path: Path | None = None,
    queue_path: Path | None = None,
    evidence_path: Path | None = None,
    defects_path: Path | None = None,
    repair_outcomes_path: Path | None = None,
    routing_signals_path: Path | None = None,
    verify_nudges_path: Path | None = None,
    issue_proposals_path: Path | None = None,
    promotion_report: dict | None = None,
) -> dict[str, Any]:
    """Compute a counters-only health snapshot from the local metadata stores.

    Read-only and defensive: missing or corrupt files count as empty rather
    than raising, so the choke-point wiring can never break a turn.
    """
    base_home = home or get_sinria_home()
    share = base_home / "correction_loop"
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=window_days)

    outcomes = _load_rows(outcome_path or (share / "outcome_gap.jsonl"))
    outcomes_window = []
    for row in outcomes:
        ts = _parse_ts(row.get("timestamp"))
        if ts is not None and ts >= cutoff:
            outcomes_window.append(row)
    gaps_window = [row for row in outcomes_window if row.get("gap_detected")]
    root_events: set[str] = set()
    unique_failures: set[tuple[str, str]] = set()
    for index, row in enumerate(gaps_window):
        root_event = row.get("source_turn_ref")
        if not isinstance(root_event, str) or not root_event:
            record_id = row.get("record_id")
            root_event = (
                record_id
                if isinstance(record_id, str) and record_id
                else f"legacy-row-{index}"
            )
        root_events.add(root_event)
        signature = _failure_signature(row)
        if signature:
            unique_failures.add((root_event, signature))
    signature_counts: Counter[str] = Counter(
        signature for _root_event, signature in unique_failures
    )

    queue_rows = _load_rows(queue_path or (share / "review_queue.jsonl"))
    queue_states: Counter[str] = Counter(str(row.get("approval_state", "unknown")) for row in queue_rows)

    defects = _load_rows(defects_path or (share / "code_defects.jsonl"))
    defects_window = []
    for row in defects:
        ts = _parse_ts(row.get("timestamp"))
        if ts is not None and ts >= cutoff:
            defects_window.append(row)

    repair_rows = _load_rows(repair_outcomes_path or (share / "repair_outcomes.jsonl"))
    repair_window: Counter[str] = Counter(
        event
        for row in repair_rows
        if (ts := _parse_ts(row.get("timestamp"))) is not None
        and ts >= cutoff
        and isinstance((event := row.get("event")), str)
        and _CATEGORY_TOKEN_RE.fullmatch(event)
    )

    snapshot: dict[str, Any] = {
        "timestamp": current.isoformat().replace("+00:00", "Z"),
        "window_days": window_days,
        "outcomes_total": len(outcomes),
        "outcomes_window": len(outcomes_window),
        "gaps_window": len(gaps_window),
        "unique_root_events_window": len(root_events),
        "signature_classes_window": len(signature_counts),
        "top_signatures_window": [
            {"signature": signature, "count": count}
            for signature, count in signature_counts.most_common(TOP_SIGNATURES_LIMIT)
        ],
        "queue_proposed": queue_states.get("proposed", 0),
        "queue_approved": queue_states.get("approved", 0),
        "queue_merged": queue_states.get("merged", 0),
        "evidence_total": _count_lines(evidence_path or (share / "evidence.jsonl")),
        "defects_window": len(defects_window),
        "tool_error_defects_window": sum(
            1 for row in defects_window if row.get("defect_kind") == "tool_error_result"
        ),
        "repair_outcomes_window": dict(sorted(repair_window.items())),
        "issue_proposals_total": _count_lines(
            issue_proposals_path or (base_home / "repair" / "issue_proposals.jsonl")
        ),
        "routing_signals_total": _count_lines(routing_signals_path or (share / "routing_signals.jsonl")),
        "verify_nudges_total": _count_lines(verify_nudges_path or (share / "verify_nudges.jsonl")),
    }
    if promotion_report is not None:
        snapshot["promotion_auto_approved"] = len(promotion_report.get("auto_approved", []))
        snapshot["promotion_pending_after"] = promotion_report.get("pending_after")
    return sanitize_loop_health_snapshot(snapshot)


def append_loop_health_snapshot(snapshot: dict[str, Any], *, path: Path | None = None) -> Path:
    target = path or loop_health_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_snapshot = sanitize_loop_health_snapshot(snapshot)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_snapshot, ensure_ascii=False, sort_keys=True) + "\n")
    return target
