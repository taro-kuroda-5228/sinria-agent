"""Review queue for Context Share evidence candidates."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hermes_constants import get_sinria_home

from .extraction import EvidenceCandidate
from .evidence import ContextEvidence
from .storage import append_evidence_jsonl

REVIEW_QUEUE_RELATIVE_PATH = Path("context_share") / "review_queue.jsonl"


def review_queue_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / REVIEW_QUEUE_RELATIVE_PATH


def _candidate_from_dict(data: dict) -> EvidenceCandidate:
    evidence_data = data.get("evidence") or {}
    candidate_id = data.get("candidate_id") or data.get("id")
    if not candidate_id:
        raise ValueError("review candidate is missing candidate_id")
    if not evidence_data:
        evidence_data = {
            "evidence_id": str(candidate_id).replace("ctx-candidate-", "ctx-ev-"),
            "source_session_id": data.get("source") or "legacy-review-queue",
            "source_kind": "repeated_failure",
            "scope": "org",
            "summary": data.get("summary") or "Legacy operational review candidate imported without raw context.",
            "sanitized_sample": data.get("kind") or "legacy_operational_candidate",
            "sensitivity": data.get("risk_level") if data.get("risk_level") in {"public", "internal", "confidential", "clinical", "secret_ref"} else "internal",
            "applies_to": ["context_share", "self_improvement", "company_os"],
            "valid_from": data.get("created_at") or "2026-06-14T00:00:00Z",
            "confidence": 0.72,
            "human_approved": False,
        }
    return EvidenceCandidate(
        candidate_id=candidate_id,
        evidence=ContextEvidence(**evidence_data),
        approval_state=data.get("approval_state", "proposed"),
        approved_at=data.get("approved_at"),
        raw_context_stored=bool(data.get("raw_context_stored", False)),
        external_action_performed=bool(data.get("external_action_performed", False)),
        extraction_reason=data.get("extraction_reason", "deterministic_prior_correction_rule"),
        occurrence_count=int(data.get("occurrence_count", 1)),
        last_seen_at=data.get("last_seen_at"),
        merged_into=data.get("merged_into"),
        approved_by=data.get("approved_by"),
    )


def dedup_key(candidate: EvidenceCandidate) -> tuple:
    """Identity of a correction *class*, independent of session/time of occurrence.

    Candidates whose ids differ only because they were minted from different
    turns (timestamp/session digests) collapse onto one reviewable row.
    """
    evidence = candidate.evidence
    return (
        candidate.extraction_reason,
        evidence.source_kind,
        evidence.scope,
        evidence.sensitivity,
        evidence.summary,
        evidence.sanitized_sample,
        tuple(sorted(evidence.applies_to)),
    )


def append_candidate_deduplicated(
    candidate: EvidenceCandidate,
    *,
    path: Path | None = None,
) -> EvidenceCandidate:
    """Append a candidate, or bump the existing row of the same class.

    Merged rows are skipped as representatives so a class keeps exactly one
    live (proposed/approved) row that accumulates ``occurrence_count``.
    """
    target = path or review_queue_path()
    existing = load_review_candidates(path=target)
    key = dedup_key(candidate)
    rewritten: list[EvidenceCandidate] = []
    representative: EvidenceCandidate | None = None
    for row in existing:
        if representative is None and row.approval_state != "merged" and dedup_key(row) == key:
            representative = replace(
                row,
                occurrence_count=row.occurrence_count + candidate.occurrence_count,
                last_seen_at=candidate.evidence.valid_from,
            )
            rewritten.append(representative)
        else:
            rewritten.append(row)
    if representative is None:
        representative = candidate
        rewritten.append(candidate)
    write_review_candidates(rewritten, path=target)
    return representative


def load_review_candidates(*, path: Path | None = None) -> list[EvidenceCandidate]:
    target = path or review_queue_path()
    if not target.exists():
        return []
    candidates: list[EvidenceCandidate] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        candidates.append(_candidate_from_dict(json.loads(stripped)))
    return candidates


def write_review_candidates(candidates: Iterable[EvidenceCandidate], *, path: Path | None = None, append: bool = False) -> Path:
    target = path or review_queue_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = load_review_candidates(path=target) if append else []
    merged: dict[str, EvidenceCandidate] = {candidate.candidate_id: candidate for candidate in existing}
    for candidate in candidates:
        merged[candidate.candidate_id] = candidate
    mode = "w"
    with target.open(mode, encoding="utf-8") as handle:
        for candidate in merged.values():
            handle.write(json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


def _approval_timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def approve_candidate(
    candidate_id: str,
    *,
    queue_path: Path | None = None,
    evidence_path: Path | None = None,
    reviewer: str = "human",
    approved_at: datetime | str | None = None,
) -> EvidenceCandidate:
    target = queue_path or review_queue_path()
    candidates = load_review_candidates(path=target)
    approved: EvidenceCandidate | None = None
    rewritten: list[EvidenceCandidate] = []
    for candidate in candidates:
        if candidate.candidate_id == candidate_id:
            evidence = replace(candidate.evidence, human_approved=True)
            approved = replace(
                candidate,
                evidence=evidence,
                approval_state="approved",
                approved_at=_approval_timestamp(approved_at),
                approved_by=reviewer,
            )
            rewritten.append(approved)
        else:
            rewritten.append(candidate)
    if approved is None:
        raise ValueError(f"candidate not found: {candidate_id}")
    write_review_candidates(rewritten, path=target)
    append_evidence_jsonl([approved.evidence], path=evidence_path)
    return approved
