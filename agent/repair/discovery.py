"""Local-only discovery for repair candidates from logs and workflow artifacts.

The scanner persists counters, stable categories, cursors, and code pointers only.
It never copies log lines, workflow payloads, tracebacks, prompts, or identifiers
into repair telemetry or reports.
"""
from __future__ import annotations

import json
import os
import re
import stat

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.defect_capture import (
    code_defects_path,
    record_external_defect,
    record_turn_workflow_gap_defect,
    turn_signal_tickets_enabled,
)
from .storage import ensure_private_dir, open_private, write_private_text

DISCOVERY_STATE_RELATIVE_PATH = Path("repair") / "discovery_state.json"
_MAX_SCAN_BYTES = 2 * 1024 * 1024
_MAX_LINE_TAIL_BYTES = 64 * 1024
_DEFAULT_LOGS = {
    "agent": Path("logs") / "agent.log",
    "errors": Path("logs") / "errors.log",
    "gateway": Path("logs") / "gateway.log",
    "gateway_error": Path("logs") / "gateway.error.log",
}
_LOG_CODE_LOCATIONS = {
    "agent": "run_agent.py:logging",
    "errors": "run_agent.py:logging",
    "gateway": "gateway/run.py:logging",
    "gateway_error": "gateway/run.py:logging",
}
_ARTIFACTS = {
    "review_queue": (Path("corrections") / "review_queue.jsonl", "agent/correction_loop/review_queue.py:queue"),
    "outcome_gap": (Path("corrections") / "outcome_gap.jsonl", "agent/correction_loop/outcome_gap.py:ledger"),
    "loop_health": (Path("corrections") / "loop_health.jsonl", "agent/correction_loop/loop_health.py:ledger"),
    "maintenance_signals": (
        Path("repair") / "maintenance_signals.jsonl",
        "agent/repair/maintenance.py:ledger",
    ),
}
_EMAIL_RE = re.compile(rb"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_SECRET_RE = re.compile(
    rb"(?:api[_-]?key|access[_-]?token|authorization|password|secret)\s*[:=]",
    re.IGNORECASE,
)
_EXCEPTION_RE = re.compile(rb"\b([A-Z][A-Za-z0-9_]{2,60}(?:Error|Exception))\b")


def discovery_state_path(home: Path) -> Path:
    return home / DISCOVERY_STATE_RELATIVE_PATH


@contextmanager
def _discovery_lock(state_path: Path):
    """Serialize cursor read/scan/write across processes and worktrees."""
    lock_path = state_path.with_name(state_path.name + ".lock")
    ensure_private_dir(lock_path.parent)
    with open_private(lock_path, "a+b") as handle:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _enabled(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    repair = config.get("repair")
    if not isinstance(repair, dict):
        return False
    section = repair.get("discovery")
    if not isinstance(section, dict):
        return False
    value = section.get("enabled", False)
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _target_repo(config: dict[str, Any] | None) -> str:
    repair = dict((config or {}).get("repair") or {})
    discovery = dict(repair.get("discovery") or {})
    return str(discovery.get("repo") or "sinria")


def _max_events(config: dict[str, Any] | None) -> int:
    try:
        section = (config or {}).get("repair", {}).get("discovery", {})
        return max(1, min(10, int(section.get("max_events_per_signal", 3))))
    except (AttributeError, TypeError, ValueError):
        return 3


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"logs": {}, "workflow": {}}
    if not isinstance(value, dict):
        return {"logs": {}, "workflow": {}}
    logs = value.get("logs") if isinstance(value.get("logs"), dict) else {}
    workflow = value.get("workflow") if isinstance(value.get("workflow"), dict) else {}
    return {"logs": logs, "workflow": workflow}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    write_private_text(
        path,
        json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n",
        root=path.parent,
    )


def _read_increment(path: Path, cursor: dict[str, Any]) -> tuple[bytes, dict[str, int]]:
    try:
        metadata = path.stat()
    except OSError:
        return b"", {"inode": 0, "offset": 0}
    prior_inode = int(cursor.get("inode", 0) or 0)
    prior_offset = int(cursor.get("offset", 0) or 0)
    start = prior_offset if prior_inode == metadata.st_ino and metadata.st_size >= prior_offset else 0
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read(_MAX_SCAN_BYTES)
        if data and start + len(data) < metadata.st_size and not data.endswith((b"\n", b"\r")):
            data += handle.readline(_MAX_LINE_TAIL_BYTES)
    return data, {"inode": int(metadata.st_ino), "offset": start + len(data)}


def _contains_sensitive_material(line: bytes) -> bool:
    lower = line.lower()
    legacy_payload = b"inbound message:" in lower and any(
        token in lower for token in (b" msg=", b" preview=", b" content=", b" message=")
    )
    structured_payload = (
        any(token in lower for token in (b"user=", b"chat=", b"session_key="))
        and any(token in lower for token in (b"msg=", b"preview=", b"content="))
    )
    return legacy_payload or structured_payload or bool(_EMAIL_RE.search(line)) or bool(_SECRET_RE.search(line))


def _scan_log_lines(data: bytes) -> dict[str, int]:
    findings: dict[str, int] = {}
    for line in data.splitlines():
        if _contains_sensitive_material(line):
            findings["sensitive_log_material"] = findings.get("sensitive_log_material", 0) + 1
        if b" ERROR " in line or b" CRITICAL " in line:
            match = _EXCEPTION_RE.search(line)
            kind = "runtime_log_error" if match is None else "runtime_exception"
            findings[kind] = findings.get(kind, 0) + 1
    return findings


def _emit_log_finding(
    *,
    source: str,
    signal_kind: str,
    count: int,
    limit: int,
    repo: str,
    defects_path: Path,
    now: datetime,
    dry_run: bool,
) -> int:
    if dry_run:
        return 0
    exc_class = {
        "sensitive_log_material": "SensitiveLogMaterial",
        "runtime_exception": "RuntimeLogException",
        "runtime_log_error": "RuntimeLogError",
    }[signal_kind]
    severity = "high" if signal_kind == "sensitive_log_material" else "medium"
    # Generic ERROR/exception log lines do not carry a trustworthy code pointer
    # or command context, so automatic patch generation would target a logging
    # facade rather than the failing component. Keep them observable but route
    # them through the contract-less issue-proposal lane. Sensitive-log findings
    # remain actionable against the configured Sinria repository.
    effective_repo = repo if signal_kind == "sensitive_log_material" else "sinria-runtime-observations"
    emitted = min(count, limit)
    for _ in range(emitted):
        record_external_defect(
            repo=effective_repo,
            exc_class=exc_class,
            code_location=_LOG_CODE_LOCATIONS[source],
            severity=severity,
            session_kind="local_log_discovery",
            path=defects_path,
            now=now,
        )
    return emitted


def _artifact_has_integrity_gap(path: Path) -> bool:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        data = path.read_bytes()
    except OSError:
        return False
    if mode & 0o022 or b"\x00" in data:
        return True
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            return True
        if not isinstance(value, dict):
            return True
    return False


def _run_discovery_unlocked(
    *,
    config: dict[str, Any] | None,
    home: Path,
    dry_run: bool = False,
    now: datetime | None = None,
    defects_path: Path | None = None,
) -> dict[str, Any]:
    """Discover sanitized repair candidates without persisting source payloads."""
    report: dict[str, Any] = {
        "enabled": _enabled(config),
        "dry_run": dry_run,
        "findings": [],
        "workflow_events": 0,
        "artifact_findings": 0,
    }
    if not report["enabled"]:
        return report

    current = now or datetime.now(timezone.utc)
    target_repo = _target_repo(config)
    target = defects_path or code_defects_path(home)
    state_path = discovery_state_path(home)
    state = _load_state(state_path)
    next_state: dict[str, Any] = {"logs": dict(state["logs"]), "workflow": dict(state["workflow"])}
    limit = _max_events(config)

    for source, relative_path in _DEFAULT_LOGS.items():
        data, cursor = _read_increment(home / relative_path, state["logs"].get(source, {}))
        if data:
            for signal_kind, occurrences in sorted(_scan_log_lines(data).items()):
                emitted = _emit_log_finding(
                    source=source,
                    signal_kind=signal_kind,
                    count=occurrences,
                    limit=limit,
                    repo=target_repo,
                    defects_path=target,
                    now=current,
                    dry_run=dry_run,
                )
                report["findings"].append(
                    {
                        "source": source,
                        "signal_kind": signal_kind,
                        "occurrences": occurrences,
                        "emitted": emitted,
                    }
                )
        next_state["logs"][source] = cursor

    for artifact, (relative_path, code_location) in _ARTIFACTS.items():
        path = home / relative_path
        if not path.exists() or not _artifact_has_integrity_gap(path):
            continue
        report["artifact_findings"] += 1
        if not dry_run:
            record_external_defect(
                repo=target_repo,
                exc_class="JsonlIntegrityError",
                code_location=code_location,
                severity="high",
                session_kind=f"artifact_{artifact}",
                path=target,
                now=current,
            )

    outcome_path = home / "corrections" / "outcome_gap.jsonl"
    data, workflow_cursor = _read_increment(outcome_path, state["workflow"])
    ticket_eligible = turn_signal_tickets_enabled(config)
    for raw in data.splitlines():
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("gap_detected") is not True:
            continue
        signature = str(row.get("failure_signature", ""))
        if not signature.startswith("exit="):
            continue
        report["workflow_events"] += 1
        if not dry_run:
            record_turn_workflow_gap_defect(
                failure_signature=signature,
                ticket_eligible=ticket_eligible,
                repo=target_repo,
                path=target,
                now=current,
            )
    next_state["workflow"] = workflow_cursor

    if not dry_run:
        _save_state(state_path, next_state)
    return report


def run_discovery(
    *,
    config: dict[str, Any] | None,
    home: Path,
    dry_run: bool = False,
    now: datetime | None = None,
    defects_path: Path | None = None,
) -> dict[str, Any]:
    """Run one serialized local discovery pass."""
    if not _enabled(config):
        return _run_discovery_unlocked(
            config=config,
            home=home,
            dry_run=dry_run,
            now=now,
            defects_path=defects_path,
        )
    with _discovery_lock(discovery_state_path(home)):
        return _run_discovery_unlocked(
            config=config,
            home=home,
            dry_run=dry_run,
            now=now,
            defects_path=defects_path,
        )
