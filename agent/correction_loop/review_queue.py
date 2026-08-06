"""Review queue for Correction Loop evidence candidates.

All queue mutations are serialized across threads and processes, written through
an fsynced temporary file, and committed with ``os.replace``. Malformed rows are
removed from the live queue and preserved in a local mode-0600 quarantine file
without logging their contents.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from hermes_constants import get_sinria_home

from .evidence import ContextEvidence
from .extraction import EvidenceCandidate
from .storage import append_evidence_jsonl

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

REVIEW_QUEUE_RELATIVE_PATH = Path("corrections") / "review_queue.jsonl"
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_T = TypeVar("_T")


def review_queue_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / REVIEW_QUEUE_RELATIVE_PATH


def review_queue_quarantine_path(queue_path: Path) -> Path:
    """Return the local forensic quarantine path for malformed queue rows."""
    return queue_path.with_name(f"{queue_path.stem}.quarantine.jsonl")


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve(strict=False))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _queue_lock(target: Path):
    """Serialize a queue transaction across threads and local processes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_LOCK"), 1)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                os.lseek(descriptor, 0, os.SEEK_SET)
                getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
            os.close(descriptor)


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
            "sensitivity": data.get("risk_level")
            if data.get("risk_level") in {"public", "internal", "confidential", "clinical", "secret_ref"}
            else "internal",
            "applies_to": ["correction_loop", "self_improvement", "company_os"],
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
        rejected_by=data.get("rejected_by"),
        rejected_at=data.get("rejected_at"),
    )


def _serialize_candidates(candidates: Iterable[EvidenceCandidate]) -> bytes:
    lines = [json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True) for candidate in candidates]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _fsync_parent_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_candidates(target: Path, candidates: Iterable[EvidenceCandidate]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(_serialize_candidates(candidates))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        os.chmod(target, 0o600)
        _fsync_parent_directory(target.parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def _append_quarantine(target: Path, corrupt_rows: list[tuple[int, bytes, Exception]]) -> None:
    if not corrupt_rows:
        return
    quarantine = review_queue_quarantine_path(target)
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(
        (
            json.dumps(
                {
                    "quarantined_at": datetime.now(timezone.utc).isoformat(),
                    "line_number": line_number,
                    "error_type": type(error).__name__,
                    "raw_base64": base64.b64encode(raw).decode("ascii"),
                },
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        for line_number, raw, error in corrupt_rows
    )
    descriptor = os.open(quarantine, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.chmod(quarantine, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_review_candidates_unlocked(target: Path) -> list[EvidenceCandidate]:
    if not target.exists():
        return []
    candidates: list[EvidenceCandidate] = []
    corrupt_rows: list[tuple[int, bytes, Exception]] = []
    for line_number, raw_line in enumerate(target.read_bytes().splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            data = json.loads(raw_line.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("review candidate row must be a JSON object")
            candidates.append(_candidate_from_dict(data))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            corrupt_rows.append((line_number, raw_line, error))
    if corrupt_rows:
        _append_quarantine(target, corrupt_rows)
        _atomic_write_candidates(target, candidates)
    return candidates


def load_review_candidates(*, path: Path | None = None) -> list[EvidenceCandidate]:
    target = path or review_queue_path()
    with _queue_lock(target):
        return _load_review_candidates_unlocked(target)


def write_review_candidates(
    candidates: Iterable[EvidenceCandidate],
    *,
    path: Path | None = None,
    append: bool = False,
) -> Path:
    target = path or review_queue_path()
    candidate_rows = list(candidates)
    with _queue_lock(target):
        existing = _load_review_candidates_unlocked(target) if append else []
        merged = {candidate.candidate_id: candidate for candidate in existing}
        for candidate in candidate_rows:
            merged[candidate.candidate_id] = candidate
        _atomic_write_candidates(target, merged.values())
    return target


def mutate_review_candidates(
    mutator: Callable[[list[EvidenceCandidate]], tuple[list[EvidenceCandidate], _T]],
    *,
    path: Path | None = None,
    before_commit: Callable[[_T], None] | None = None,
) -> _T:
    """Apply a serialized queue mutation with an optional durable pre-commit.

    ``before_commit`` runs while the queue lock is held and before the approved
    state becomes visible. It is used to persist resolver-consumed evidence
    first, preventing an approved queue row from pointing at missing evidence.
    The durable append must be idempotent because a later queue write may fail.
    """
    target = path or review_queue_path()
    with _queue_lock(target):
        existing = _load_review_candidates_unlocked(target)
        rewritten, result = mutator(existing)
        if before_commit is not None:
            before_commit(result)
        _atomic_write_candidates(target, rewritten)
        return result


def dedup_key(candidate: EvidenceCandidate) -> tuple:
    """Identity of a correction class, independent of its source turn."""
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
    """Append a candidate, or atomically bump the existing row of its class."""

    def mutate(existing: list[EvidenceCandidate]) -> tuple[list[EvidenceCandidate], EvidenceCandidate]:
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
        return rewritten, representative

    return mutate_review_candidates(mutate, path=path)


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
    def mutate(existing: list[EvidenceCandidate]) -> tuple[list[EvidenceCandidate], EvidenceCandidate]:
        rewritten: list[EvidenceCandidate] = []
        approved: EvidenceCandidate | None = None
        for candidate in existing:
            if candidate.candidate_id == candidate_id:
                approved = replace(
                    candidate,
                    evidence=replace(candidate.evidence, human_approved=True),
                    approval_state="approved",
                    approved_at=_approval_timestamp(approved_at),
                    approved_by=reviewer,
                )
                rewritten.append(approved)
            else:
                rewritten.append(candidate)
        if approved is None:
            raise ValueError(f"Candidate not found: {candidate_id}")
        return rewritten, approved

    def persist_approved(approved: EvidenceCandidate) -> None:
        append_evidence_jsonl([approved.evidence], path=evidence_path)

    return mutate_review_candidates(
        mutate,
        path=queue_path,
        before_commit=persist_approved,
    )


def reject_candidate(
    candidate_id: str,
    *,
    queue_path: Path | None = None,
    reviewer: str = "human",
    rejected_at: datetime | str | None = None,
) -> EvidenceCandidate:
    """Mark a candidate rejected inside one locked queue transaction."""
    def mutate(existing: list[EvidenceCandidate]) -> tuple[list[EvidenceCandidate], EvidenceCandidate]:
        rewritten: list[EvidenceCandidate] = []
        rejected: EvidenceCandidate | None = None
        for candidate in existing:
            if candidate.candidate_id == candidate_id:
                rejected = replace(
                    candidate,
                    approval_state="rejected",
                    rejected_by=reviewer,
                    rejected_at=_approval_timestamp(rejected_at),
                )
                rewritten.append(rejected)
            else:
                rewritten.append(candidate)
        if rejected is None:
            raise KeyError(candidate_id)
        return rewritten, rejected

    return mutate_review_candidates(mutate, path=queue_path)
