"""Durable, loss-resistant local evidence storage for Sinria Correction Loop v2.

The evidence ledger is append-only from an operator perspective: malformed or
legacy bytes are never removed implicitly. Readers salvage valid rows and emit a
sanitized warning; writers use a local cross-process lock and atomic replacement
so concurrent approvals cannot lose or duplicate evidence.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from hermes_constants import get_sinria_home

from .evidence import ContextEvidence, SensitiveContextError

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

EVIDENCE_RELATIVE_PATH = Path("corrections") / "evidence.jsonl"
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class EvidenceLoadWarning(UserWarning):
    """A durable evidence file contained rows that could not be loaded."""


def evidence_store_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / EVIDENCE_RELATIVE_PATH


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve(strict=False))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _evidence_lock(target: Path):
    """Serialize an evidence transaction across threads and local processes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    with _thread_lock_for(lock_path):
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


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
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
            handle.write(payload)
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


def _iter_raw_rows(payload: bytes):
    for line_no, raw_line in enumerate(payload.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(b"#"):
            continue
        yield line_no, raw_line


def load_evidence_jsonl(path: Path) -> list[ContextEvidence]:
    if not path.exists():
        return []
    evidence: list[ContextEvidence] = []
    malformed_line_numbers: list[int] = []
    for line_no, raw_line in _iter_raw_rows(path.read_bytes()):
        try:
            data = json.loads(raw_line.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("evidence row must be a JSON object")
            evidence.append(ContextEvidence(**data))
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, SensitiveContextError):
            malformed_line_numbers.append(line_no)
    if malformed_line_numbers:
        warnings.warn(
            f"Skipped {len(malformed_line_numbers)} malformed row(s) in Correction Loop evidence "
            f"at {path}; original bytes were preserved. Lines: "
            + ",".join(str(number) for number in malformed_line_numbers),
            EvidenceLoadWarning,
            stacklevel=2,
        )
    return evidence


def load_durable_evidence(*, home: Path | None = None, path: Path | None = None) -> list[ContextEvidence]:
    return load_evidence_jsonl(path or evidence_store_path(home))


def _existing_evidence_ids(payload: bytes) -> set[str]:
    evidence_ids: set[str] = set()
    for _line_no, raw_line in _iter_raw_rows(payload):
        try:
            data = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("evidence_id"), str):
            evidence_ids.add(data["evidence_id"])
    return evidence_ids


def append_evidence_jsonl(items: Iterable[ContextEvidence], *, path: Path | None = None) -> Path:
    """Atomically append unique evidence IDs while preserving all existing bytes."""
    target = path or evidence_store_path()
    pending = list(items)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _evidence_lock(target):
        original = target.read_bytes() if target.exists() else b""
        seen = _existing_evidence_ids(original)
        additions: list[bytes] = []
        for item in pending:
            if item.evidence_id in seen:
                continue
            additions.append(
                (json.dumps(item.__dict__, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            )
            seen.add(item.evidence_id)
        if not additions:
            if not target.exists():
                _atomic_write_bytes(target, original)
            return target
        separator = b"\n" if original and not original.endswith((b"\n", b"\r")) else b""
        _atomic_write_bytes(target, original + separator + b"".join(additions))
    return target
