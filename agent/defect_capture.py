"""Structured code-defect telemetry for the Sinria codebase self-repair loop (Phase 1).

Runtime exceptions currently vanish into plain-text ``logs/errors.log`` where
the self-improvement loop cannot see them. This module records sanitized,
structured DefectRecord events into ``SINRIA_HOME/context_share/code_defects.jsonl``
— the same metadata-only surface as outcome_gap / routing_signals — so recurring
code defects become measurable (and, in later phases, repairable).

Confidentiality: records hold exception class names, redacted messages, and
repo-relative code locations only. Raw tracebacks, tool payloads, and
conversation text never enter the JSONL. Message sanitization is fail-closed:
when redaction cannot produce a clean message the message is dropped entirely
(the record itself survives — the fingerprint is the load-bearing datum).

Design doc: docs/plans/2026-07-06-codebase-self-repair-loop-design.md (§4.1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hermes_constants import get_sinria_home

from agent.context_share.safety import (
    assert_safe_identifier,
    assert_sanitized_text,
    contains_sensitive_text,
)

CODE_DEFECTS_RELATIVE_PATH = Path("context_share") / "code_defects.jsonl"

DefectKind = Literal["unhandled_exception", "tool_error_result"]
Severity = Literal["high", "medium", "low"]

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Transient-looking exception classes: still recorded (the observation report
# measures the noise ratio) but flagged so triage can discount them.
_TRANSIENT_EXC_NAMES = frozenset({
    "TimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "BrokenPipeError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteDisconnected",
})

_MESSAGE_MAX_CHARS = 300


def code_defects_path(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / CODE_DEFECTS_RELATIVE_PATH


def _safe_digest(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    # Digit-free digests: the shared safety guard treats long numeric runs as
    # potential identifiers/phone numbers (same convention as outcome_gap).
    return digest.translate(str.maketrans("0123456789", "abcdefghij"))


def build_fingerprint(repo: str, code_file: str, func_name: str, exc_class: str) -> str:
    """Stable recurrence key for a defect.

    Line numbers are deliberately excluded: they shift across versions while
    the (file, function, exception) triple keeps identifying the same defect.
    """
    return f"fp-{_safe_digest(chr(10).join([repo, code_file, func_name, exc_class]))}"


def sanitize_defect_message(message: str | None) -> str:
    """Redact then verify; fail-closed to empty string when still sensitive."""
    if not message:
        return ""
    try:
        from agent.redact import redact_sensitive_text

        cleaned = redact_sensitive_text(str(message), force=True)
    except Exception:
        return ""
    cleaned = " ".join(str(cleaned).split())[:_MESSAGE_MAX_CHARS]
    if contains_sensitive_text(cleaned):
        return ""
    return cleaned


@dataclass(frozen=True)
class DefectRecord:
    defect_id: str
    fingerprint: str
    timestamp: str
    repo: str
    defect_kind: DefectKind
    exc_class: str
    redacted_message: str
    code_location: str
    logger_name: str
    session_kind: str
    severity: Severity
    transient_likely: bool

    def __post_init__(self) -> None:
        assert_safe_identifier(self.defect_id, field="defect_id")
        assert_safe_identifier(self.fingerprint, field="fingerprint")
        assert_sanitized_text(self.repo, field="repo")
        assert_sanitized_text(self.exc_class, field="exc_class")
        assert_sanitized_text(self.redacted_message, field="redacted_message")
        assert_sanitized_text(self.code_location, field="code_location")
        assert_sanitized_text(self.logger_name, field="logger_name")
        assert_sanitized_text(self.session_kind, field="session_kind")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DefectSummary:
    fingerprint: str
    repo: str
    exc_class: str
    code_location: str
    severity: str
    occurrence_count: int
    first_seen: str
    last_seen: str
    transient_likely: bool


def append_defect_record(record: DefectRecord, *, path: Path | None = None) -> Path:
    target = path or code_defects_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return target


def load_defect_summaries(*, path: Path | None = None) -> list[DefectSummary]:
    """Aggregate append-only events into per-fingerprint summaries.

    The design schema's occurrence_count/first_seen/last_seen live at this
    aggregate level; the JSONL itself stays append-only like every other
    context_share store (no upsert-in-place).
    """
    target = path or code_defects_path()
    if not target.exists():
        return []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except ValueError:
            continue
        buckets.setdefault(str(data.get("fingerprint", "")), []).append(data)
    summaries: list[DefectSummary] = []
    for fingerprint, events in buckets.items():
        if not fingerprint:
            continue
        ordered = sorted(events, key=lambda event: str(event.get("timestamp", "")))
        last = ordered[-1]
        summaries.append(
            DefectSummary(
                fingerprint=fingerprint,
                repo=str(last.get("repo", "unknown")),
                exc_class=str(last.get("exc_class", "unknown")),
                code_location=str(last.get("code_location", "unknown")),
                severity=str(last.get("severity", "medium")),
                occurrence_count=len(ordered),
                first_seen=str(ordered[0].get("timestamp", "")),
                last_seen=str(last.get("timestamp", "")),
                transient_likely=bool(last.get("transient_likely", False)),
            )
        )
    summaries.sort(key=lambda summary: summary.occurrence_count, reverse=True)
    return summaries


def _innermost_repo_frame(tb) -> tuple[str, str, int] | None:
    """Walk to the deepest traceback frame inside this repo.

    The innermost in-repo frame is the best fix location: outer frames are
    dispatch plumbing, frames below it live in third-party code we do not
    repair. Returns (repo-relative file, function name, line) or None when
    no frame is in-repo (pure third-party failure).
    """
    chosen: tuple[str, str, int] | None = None
    while tb is not None:
        code = tb.tb_frame.f_code
        filename = code.co_filename
        # Pseudo-filenames from exec'd/compiled strings (<string>, <stdin>,
        # <frozen ...>) are not repo files and must not become fix locations.
        if not filename.startswith("<") and "site-packages" not in filename:
            try:
                rel = Path(filename).resolve().relative_to(_REPO_ROOT)
            except (ValueError, OSError):
                rel = None
            if rel is not None:
                chosen = (str(rel), code.co_name, tb.tb_lineno)
        tb = tb.tb_next
    return chosen


def record_exception_defect(
    exc_type,
    exc_value,
    tb,
    *,
    logger_name: str = "",
    levelno: int = logging.ERROR,
    session_kind: str = "unknown",
    path: Path | None = None,
    now: datetime | None = None,
) -> DefectRecord | None:
    """Build and append a sanitized DefectRecord from exc_info.

    Returns None when there is nothing recordable (no traceback / no
    exception type).
    """
    if exc_type is None or tb is None:
        return None
    frame = _innermost_repo_frame(tb)
    if frame is not None:
        code_file, func_name, lineno = frame
        repo = "sinria"
        code_location = f"{code_file}:{lineno}"
    else:
        repo = "external"
        code_file, func_name = "unknown", "unknown"
        code_location = "unknown:0"
    exc_class = getattr(exc_type, "__name__", str(exc_type))[:80]
    ts = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    fingerprint = build_fingerprint(repo, code_file, func_name, exc_class)
    record = DefectRecord(
        defect_id=f"defect-{_safe_digest(fingerprint + chr(10) + ts)}",
        fingerprint=fingerprint,
        timestamp=ts,
        repo=repo,
        defect_kind="unhandled_exception",
        exc_class=exc_class,
        redacted_message=sanitize_defect_message(str(exc_value) if exc_value is not None else ""),
        code_location=code_location,
        logger_name=(logger_name or "unknown")[:120],
        session_kind=(session_kind or "unknown")[:40],
        severity="high" if levelno >= logging.CRITICAL else "medium",
        transient_likely=exc_class in _TRANSIENT_EXC_NAMES,
    )
    append_defect_record(record, path=path)
    return record


_capture_tls = threading.local()


class DefectCaptureHandler(logging.Handler):
    """Captures ERROR+ log records that carry exc_info as DefectRecords.

    Contract: emit() never raises and never recurses. Attached once by
    hermes_logging.setup_logging next to the errors.log handler, so every
    mode (CLI / gateway / cron) feeds the same telemetry with zero hot-path
    changes.
    """

    def __init__(self, level: int = logging.ERROR, path: Path | None = None, session_kind: str = "unknown"):
        super().__init__(level=level)
        self._path = path
        self._session_kind = session_kind

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_capture_tls, "active", False):
            return
        exc_info = record.exc_info
        if not isinstance(exc_info, tuple) or len(exc_info) != 3 or exc_info[0] is None:
            return
        _capture_tls.active = True
        try:
            record_exception_defect(
                exc_info[0],
                exc_info[1],
                exc_info[2],
                logger_name=record.name,
                levelno=record.levelno,
                session_kind=self._session_kind,
                path=self._path,
            )
        except Exception:
            pass
        finally:
            _capture_tls.active = False


def repair_telemetry_enabled(config: dict | None = None) -> bool:
    """``repair.telemetry`` flag. Default True: records are metadata-only,
    the same confidentiality class as outcome_gap/routing_signals. Explicit
    falsy values disable capture. This flag is deliberately separate from the
    ``repair.enabled`` kill switch so observation survives an orchestrator
    shutdown (design §4.3)."""
    if config is None:
        try:
            from hermes_cli.config import load_config

            loaded = load_config()
            config = loaded if isinstance(loaded, dict) else {}
        except Exception:
            config = {}
    if not isinstance(config, dict):
        return True
    section = config.get("repair")
    if not isinstance(section, dict):
        return True
    value = section.get("telemetry", True)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off"}
