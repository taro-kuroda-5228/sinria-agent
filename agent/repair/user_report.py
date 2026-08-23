"""Confidentiality-first intake for user supplied error reports.

Raw pasted text and screenshots remain in a local 0700 report directory. Only a
bounded, redacted excerpt and deterministic signatures leave this boundary.
OCR is strictly local and fails closed when no local engine is available.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from typing import Callable, Any

from .storage import ensure_private_dir, write_private

_MAX_TEXT = 1_000_000
_MAX_IMAGE = 20 * 1024 * 1024
_MAX_EXCERPT = 2_000
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


class OCRUnavailableError(RuntimeError):
    pass


class InvalidUserReport(ValueError):
    pass


def redact_error_text(text: str) -> str:
    rules = (
        (r"(?i)\b(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+", "[REDACTED_CREDENTIAL]"),
        (r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]"),
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]"),
        (r"(?<![\w])/(?:Users|home)/[^\s:]+", "/[REDACTED_HOME]"),
        (r"\b(?:MRN|patient[_ -]?id)\s*[:=]\s*[A-Za-z0-9_-]+", "[REDACTED_PATIENT_ID]"),
        (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]"),
    )
    result = text
    for pattern, replacement in rules:
        result = re.sub(pattern, replacement, result)
    return result


def diagnose_error(text: str) -> dict[str, Any]:
    error = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failure))\b", text)
    status = re.search(r"(?i)\b(?:status|http)\s*[:= ]\s*([1-5]\d\d)\b", text)
    timeout = re.search(r"(?i)\b(?:timed?\s*out|timeout)(?:\s*[:= ]\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|seconds)?)?", text)
    location = re.search(r"(?P<file>[A-Za-z0-9_.-]+\.(?:py|js|ts|tsx|go|rs|rb|java)):(?P<line>\d+)", text)
    return {
        "error_class": error.group(1) if error else None,
        "status": int(status.group(1)) if status else None,
        "timeout": timeout.group(0)[:80] if timeout else None,
        "location": f"{location.group('file')}:{location.group('line')}" if location else None,
    }


def _default_local_ocr(path: Path) -> str:
    engine = shutil.which("tesseract")
    if engine:
        argv = [engine, str(path), "stdout"]
    elif sys.platform == "darwin" and shutil.which("swift"):
        helper = Path(__file__).parent / "assets" / "local_ocr.swift"
        if not helper.is_file():
            raise OCRUnavailableError("approved local OCR helper is unavailable")
        argv = [shutil.which("swift") or "/usr/bin/swift", str(helper), str(path)]
    else:
        raise OCRUnavailableError("no approved local OCR engine is available")
    result = subprocess.run(
        argv, capture_output=True, text=True,
        timeout=60, check=False, shell=False,
    )
    if result.returncode != 0:
        raise OCRUnavailableError("local OCR failed")
    return result.stdout


def intake_user_report(
    pasted_text: str | None = None, *, screenshot_path: str | Path | None = None,
    home: str | Path | None = None, report_id: str | None = None,
    ocr_runner: Callable[[Path], str] | None = None,
    defect_writer: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    if not pasted_text and screenshot_path is None:
        raise InvalidUserReport("pasted text or screenshot is required")
    rid = report_id or f"report-{secrets.token_hex(12)}"
    if not _SAFE_ID.fullmatch(rid):
        raise InvalidUserReport("invalid report id")
    root = Path(home or Path.home() / ".sinria").expanduser().resolve()
    repair_root = root / "repair"
    report_dir = repair_root / "user-reports" / rid
    ensure_private_dir(report_dir, root=repair_root)

    parts: list[str] = []
    if pasted_text:
        if len(pasted_text.encode("utf-8")) > _MAX_TEXT:
            raise InvalidUserReport("pasted text is too large")
        parts.append(pasted_text)
    if screenshot_path is not None:
        source = Path(screenshot_path).expanduser().resolve(strict=True)
        if not source.is_file() or source.stat().st_size > _MAX_IMAGE:
            raise InvalidUserReport("invalid screenshot")
        suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".img"
        target = report_dir / f"screenshot{suffix}"
        write_private(target, source.read_bytes(), root=repair_root)
        extracted = (ocr_runner or _default_local_ocr)(target)
        if extracted:
            parts.append(extracted)

    raw = "\n".join(parts)
    write_private(report_dir / "raw.txt", raw, root=repair_root)
    redacted = redact_error_text(raw)
    diagnosis = diagnose_error(redacted)
    safe = {
        "report_id": rid,
        "artifact_ref": str(report_dir.relative_to(root)),
        "diagnosis": diagnosis,
        "sanitized_excerpt": redacted[:_MAX_EXCERPT],
        "raw_confined_local": True,
    }
    write_private(
        report_dir / "sanitized.json",
        json.dumps(safe, ensure_ascii=False, indent=2),
        root=repair_root,
    )
    if defect_writer:
        defect_writer({"report_id": rid, "diagnosis": diagnosis, "sanitized_excerpt": safe["sanitized_excerpt"]})
    return safe
