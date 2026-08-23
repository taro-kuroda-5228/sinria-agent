"""Durable repair-ticket store and state machine (self-repair loop Phase 2).

Tickets survive process death and context compression the same way TodoStore
does: one JSON file per ticket under ``SINRIA_HOME/repair/tickets/``. Every
state transition is validated against the design §4.3 machine and appended to
``SINRIA_HOME/repair/transitions.jsonl`` (audit trail); outcome events land on
``SINRIA_HOME/repair/repair_outcomes.jsonl`` — the Phase 4 promotion
ledger starts accumulating now even though promotion itself is out of scope.

Everything stored here is sanitized metadata only (enforced in
``__post_init__`` / ``assert_sanitized_metadata``): no raw tracebacks, no raw
diffs, no command output bodies.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_sinria_home

from agent.privacy.sanitization import (
    assert_safe_identifier,
    assert_sanitized_metadata,
    assert_sanitized_text,
)
from agent.defect_capture import _safe_digest
from .storage import append_private_text, ensure_private_dir, write_private_text

REPAIR_OUTCOMES_RELATIVE_PATH = Path("repair") / "repair_outcomes.jsonl"

TICKET_STATUSES = (
    "queued",
    "reproducing",
    "patching",
    "verifying",
    "review_ready",
    "pr_open",
    "merged",
    "rejected",
    "rolled_back",
    "failed",
    "escalated",
)
ACTIVE_STATUSES = frozenset({"queued", "reproducing", "patching", "verifying", "review_ready", "pr_open"})
RISK_CLASSES = ("auto_eligible", "human_only", "escalate_only")
CANDIDATE_KINDS = ("defect", "refactor")

# Design §4.3: queued → reproducing → patching → verifying → pr_open →
# merged/rejected/rolled_back; every working state may fail; queued may be
# escalated to human. Terminal states have no exits.
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"reproducing", "escalated", "failed"}),
    "reproducing": frozenset({"patching", "failed"}),
    "patching": frozenset({"verifying", "failed"}),
    "verifying": frozenset({"review_ready", "pr_open", "failed"}),
    "review_ready": frozenset({"pr_open", "rejected"}),
    "pr_open": frozenset({"merged", "rejected", "rolled_back"}),
    "merged": frozenset(),
    "rejected": frozenset(),
    "rolled_back": frozenset(),
    "failed": frozenset(),
    "escalated": frozenset(),
}

_NOTE_MAX_CHARS = 300


def _iso(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RepairTicket:
    ticket_id: str
    fingerprint: str
    repo: str
    exc_class: str
    code_location: str
    severity: str
    risk_class: str
    status: str
    created_at: str
    updated_at: str
    attempt: int
    occurrence_count: int
    redacted_message: str = ""
    edit_approved_at: str = ""
    edit_approved_by: str = ""
    candidate_kind: str = "defect"
    signal_kind: str = ""
    metric_name: str = ""
    baseline_metric: float = 0.0
    target_metric: float = 0.0
    notes: tuple[str, ...] = ()
    branch: str = ""
    repro_test_path: str = ""
    pr_url: str = ""

    def __post_init__(self) -> None:
        assert_safe_identifier(self.ticket_id, field="ticket_id")
        assert_safe_identifier(self.fingerprint, field="fingerprint")
        assert_sanitized_text(self.repo, field="repo")
        assert_sanitized_text(self.exc_class, field="exc_class")
        assert_sanitized_text(self.code_location, field="code_location")
        assert_sanitized_text(self.redacted_message, field="redacted_message")
        if self.status not in TICKET_STATUSES:
            raise ValueError(f"unknown ticket status {self.status!r}")
        if self.risk_class not in RISK_CLASSES:
            raise ValueError(f"unknown risk class {self.risk_class!r}")
        if self.candidate_kind not in CANDIDATE_KINDS:
            raise ValueError(f"unknown candidate kind {self.candidate_kind!r}")
        assert_sanitized_text(self.signal_kind, field="signal_kind")
        assert_sanitized_text(self.metric_name, field="metric_name")
        if not math.isfinite(self.baseline_metric) or not math.isfinite(self.target_metric):
            raise ValueError("ticket metrics must be finite")
        if self.candidate_kind == "refactor":
            if not self.signal_kind or not self.metric_name:
                raise ValueError("refactor tickets require an objective signal")
            if self.baseline_metric <= self.target_metric:
                raise ValueError("refactor target must improve the baseline")
        for note in self.notes:
            assert_sanitized_text(note, field="notes")
        assert_sanitized_text(self.branch, field="branch")
        assert_sanitized_text(self.repro_test_path, field="repro_test_path")
        assert_sanitized_text(self.pr_url, field="pr_url")
        if self.edit_approved_at:
            try:
                parsed_approval = datetime.fromisoformat(self.edit_approved_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("edit_approved_at must be an ISO timestamp") from exc
            if parsed_approval.tzinfo is None:
                raise ValueError("edit_approved_at must include a timezone")
        if self.edit_approved_by:
            assert_safe_identifier(self.edit_approved_by, field="edit_approved_by")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data


def new_ticket(
    *,
    fingerprint: str,
    repo: str,
    exc_class: str,
    code_location: str,
    severity: str,
    risk_class: str,
    occurrence_count: int,
    redacted_message: str = "",
    attempt: int = 1,
    candidate_kind: str = "defect",
    signal_kind: str = "",
    metric_name: str = "",
    baseline_metric: float = 0.0,
    target_metric: float = 0.0,
    now: datetime | None = None,
) -> RepairTicket:
    ts = _iso(now)
    return RepairTicket(
        ticket_id=f"ticket-{_safe_digest(fingerprint + chr(10) + ts + chr(10) + str(attempt))}",
        fingerprint=fingerprint,
        repo=repo,
        exc_class=exc_class,
        code_location=code_location,
        severity=severity,
        risk_class=risk_class,
        status="queued",
        created_at=ts,
        updated_at=ts,
        attempt=attempt,
        occurrence_count=occurrence_count,
        redacted_message=redacted_message,
        candidate_kind=candidate_kind,
        signal_kind=signal_kind,
        metric_name=metric_name,
        baseline_metric=float(baseline_metric),
        target_metric=float(target_metric),
    )


def tickets_dir(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "repair" / "tickets"


def transitions_log_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "repair" / "transitions.jsonl"


def repair_outcomes_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / REPAIR_OUTCOMES_RELATIVE_PATH


def save_ticket(ticket: RepairTicket, *, home: Path | None = None) -> Path:
    directory = tickets_dir(home)
    repair_root = directory.parent
    ensure_private_dir(directory, root=repair_root)
    target = directory / f"{ticket.ticket_id}.json"
    write_private_text(
        target,
        json.dumps(ticket.to_dict(), ensure_ascii=False, sort_keys=True, indent=2),
        root=repair_root,
    )
    return target


def _ticket_from_dict(data: dict[str, Any]) -> RepairTicket:
    approved_at = str(data.get("edit_approved_at", ""))
    approved_by = str(data.get("edit_approved_by", ""))
    status = str(data.get("status", "failed"))
    return RepairTicket(
        ticket_id=str(data["ticket_id"]),
        fingerprint=str(data["fingerprint"]),
        repo=str(data.get("repo", "unknown")),
        exc_class=str(data.get("exc_class", "unknown")),
        code_location=str(data.get("code_location", "unknown:0")),
        severity=str(data.get("severity", "medium")),
        risk_class=str(data.get("risk_class", "human_only")),
        status=status,
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        attempt=int(data.get("attempt", 1)),
        occurrence_count=int(data.get("occurrence_count", 0)),
        redacted_message=str(data.get("redacted_message", "")),
        edit_approved_at=approved_at,
        edit_approved_by=approved_by,
        candidate_kind=str(data.get("candidate_kind", "defect")),
        signal_kind=str(data.get("signal_kind", "")),
        metric_name=str(data.get("metric_name", "")),
        baseline_metric=float(data.get("baseline_metric", 0.0)),
        target_metric=float(data.get("target_metric", 0.0)),
        notes=tuple(str(note) for note in data.get("notes", ())),
        branch=str(data.get("branch", "")),
        repro_test_path=str(data.get("repro_test_path", "")),
        pr_url=str(data.get("pr_url", "")),
    )


def load_ticket(ticket_id: str, *, home: Path | None = None) -> RepairTicket | None:
    target = tickets_dir(home) / f"{ticket_id}.json"
    try:
        return _ticket_from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        return None


def load_tickets(*, home: Path | None = None) -> list[RepairTicket]:
    directory = tickets_dir(home)
    if not directory.exists():
        return []
    tickets: list[RepairTicket] = []
    for path in directory.glob("*.json"):
        try:
            tickets.append(_ticket_from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    tickets.sort(key=lambda ticket: (ticket.created_at, ticket.ticket_id))
    return tickets


def transition(
    ticket: RepairTicket,
    new_status: str,
    *,
    note: str = "",
    now: datetime | None = None,
    home: Path | None = None,
    updates: dict[str, Any] | None = None,
) -> RepairTicket:
    """Validated state transition: persists the ticket and appends the audit log.

    ``updates`` may carry additional sanitized field changes that belong to the
    same transition (branch, repro_test_path, pr_url) — they are applied through
    the dataclass constructor so ``__post_init__`` re-validates everything.
    """
    allowed = _VALID_TRANSITIONS.get(ticket.status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"illegal ticket transition {ticket.status!r} -> {new_status!r} "
            f"(allowed: {sorted(allowed) or 'none — terminal state'})"
        )
    clean_note = " ".join(str(note or "").split())[:_NOTE_MAX_CHARS]
    assert_sanitized_text(clean_note, field="note")
    ts = _iso(now)
    fields: dict[str, Any] = dict(updates or {})
    fields["status"] = new_status
    fields["updated_at"] = ts
    if clean_note:
        fields["notes"] = (*ticket.notes, clean_note)
    moved = replace(ticket, **fields)
    save_ticket(moved, home=home)
    log_path = transitions_log_path(home)
    append_private_text(
        log_path,
        json.dumps(
            {
                "ticket_id": moved.ticket_id,
                "fingerprint": moved.fingerprint,
                "repo": moved.repo,
                "from_status": ticket.status,
                "to_status": new_status,
                "note": clean_note,
                "timestamp": ts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        root=log_path.parent,
    )
    return moved


def approve_ticket(
    ticket_id: str,
    *,
    approved_by: str,
    home: Path | None = None,
    now: datetime | None = None,
) -> RepairTicket:
    """Record explicit human approval before an executor may edit code."""
    actor = str(approved_by or "").strip().lower()
    assert_safe_identifier(actor, field="approved_by")
    ticket = load_ticket(ticket_id, home=home)
    if ticket is None:
        raise ValueError("repair ticket not found")
    if ticket.status != "queued":
        raise ValueError(f"ticket is not queued for approval: {ticket.status}")
    if ticket.edit_approved_at and ticket.edit_approved_by:
        return ticket
    ts = _iso(now)
    approved = replace(
        ticket,
        edit_approved_at=ts,
        edit_approved_by=actor,
        updated_at=ts,
        notes=tuple(ticket.notes) + ("explicit edit approval recorded",),
    )
    save_ticket(approved, home=home)
    record_outcome(
        {
            "event": "edit_approval",
            "ticket_id": approved.ticket_id,
            "approved_by": actor,
            "timestamp": ts,
        },
        home=home,
    )
    return approved


_TIMESTAMP_KEYS = frozenset({"timestamp", "created_at", "updated_at", "first_seen", "last_seen"})


def record_outcome(event: dict[str, Any], *, home: Path | None = None, path: Path | None = None) -> Path:
    """Append a sanitized outcome event to the Phase 4 promotion ledger."""
    # ISO dates trip the shared guard's phone-number heuristic (digit runs with
    # dashes), so timestamp fields are exempt — same convention as DefectRecord.
    assert_sanitized_metadata(
        {key: value for key, value in event.items() if key not in _TIMESTAMP_KEYS},
        field="repair_outcome",
    )
    target = path or repair_outcomes_path(home)
    append_private_text(target, json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return target
