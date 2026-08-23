"""Safe user-evidence bridge into the canonical repair intake.

The gateway/model already owns image understanding. This boundary accepts only
extracted text (never image bytes or a source path), records the existing
sanitized ``DefectRecord``, and creates a metadata-only receipt. A separate
explicit confirmation is required before ``run_intake`` may create a ticket.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hermes_constants import get_sinria_home

from agent.privacy.sanitization import assert_safe_identifier, assert_sanitized_metadata
from agent.defect_capture import build_fingerprint, code_defects_path, record_external_defect

from .intake import evidence_confirmations_path, run_intake

SourceKind = Literal["image", "paste"]

_EXCEPTION_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure))\b")
_PYTHON_LOCATION_RE = re.compile(r"File\s+[\"']([^\"']+)[\"']\s*,\s*line\s+(\d+)")
_ERROR_MARKER_RE = re.compile(r"(?i)\b(error|failed|failure|exception|traceback|crash(?:ed)?)\b")
_SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
_SAFE_EXCEPTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_SAFE_LOCATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}:[0-9]{1,7}$")
_BASE_RECEIPT_FIELDS = frozenset(
    {
        "receipt_id",
        "fingerprint",
        "repo",
        "exc_class",
        "code_location",
        "source_kind",
        "status",
        "created_at",
    }
)


def evidence_receipts_dir(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "repair" / "evidence_receipts"


def _timestamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 35:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_receipt(row: object) -> dict[str, Any]:
    """Fail closed on tampered receipts and any raw-evidence field."""
    if not isinstance(row, dict):
        raise ValueError("evidence receipt must be an object")
    allowed = set(_BASE_RECEIPT_FIELDS)
    if row.get("status") == "confirmed":
        allowed.add("confirmed_at")
    if set(row) != allowed:
        raise ValueError("evidence receipt has missing or forbidden fields")
    assert_safe_identifier(str(row["receipt_id"]), field="receipt_id")
    assert_safe_identifier(str(row["fingerprint"]), field="fingerprint")
    if not _SAFE_REPO_RE.fullmatch(str(row["repo"])):
        raise ValueError("invalid receipt repo")
    if not _SAFE_EXCEPTION_RE.fullmatch(str(row["exc_class"])):
        raise ValueError("invalid receipt exception class")
    if not _SAFE_LOCATION_RE.fullmatch(str(row["code_location"])):
        raise ValueError("invalid receipt code location")
    if row["source_kind"] not in {"image", "paste"}:
        raise ValueError("invalid receipt source kind")
    if row["status"] not in {"pending_confirmation", "confirmed"}:
        raise ValueError("invalid receipt status")
    if not _valid_timestamp(row["created_at"]):
        raise ValueError("invalid receipt creation timestamp")
    if row["status"] == "confirmed" and not _valid_timestamp(row["confirmed_at"]):
        raise ValueError("invalid receipt confirmation timestamp")
    # Timestamps are validated structurally above. Recursively scan all other
    # values for secret/PHI/PII markers before local persistence.
    assert_sanitized_metadata(
        {key: value for key, value in row.items() if not key.endswith("_at")},
        field="evidence_receipt",
    )
    return row


def _extract_signature(text: str) -> tuple[str, str] | None:
    classes = _EXCEPTION_RE.findall(text)
    if classes:
        exc_class = classes[-1].rsplit(".", 1)[-1][:80]
    elif _ERROR_MARKER_RE.search(text):
        exc_class = "UserReportedFailure"
    else:
        return None

    locations = _PYTHON_LOCATION_RE.findall(text)
    if locations:
        raw_path, line = locations[-1]
        # Basename only: absolute paths can disclose usernames/tenant dirs.
        filename = Path(raw_path.replace("\\", "/")).name
        safe_filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)[:120] or "reported"
        location = f"{safe_filename}:{line}"
    else:
        location = "user_evidence:0"
    return exc_class, location


def _load_receipts(home: Path | None) -> list[dict[str, Any]]:
    directory = evidence_receipts_dir(home)
    if not directory.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            rows.append(_validate_receipt(row))
        except ValueError:
            continue
    return rows


def _receipt_path(receipt_id: str, home: Path | None) -> Path:
    assert_safe_identifier(receipt_id, field="receipt_id")
    return evidence_receipts_dir(home) / f"{receipt_id}.json"


def _write_receipt(row: dict[str, Any], *, home: Path | None) -> Path:
    _validate_receipt(row)
    target = _receipt_path(str(row["receipt_id"]), home)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def capture_error_evidence(
    *,
    repo: str,
    source_kind: SourceKind,
    extracted_text: str,
    home: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Capture extracted screenshot/paste text without retaining raw evidence."""
    if source_kind not in {"image", "paste"}:
        raise ValueError("source_kind must be 'image' or 'paste'")
    if not _SAFE_REPO_RE.fullmatch(repo or ""):
        raise ValueError("repo must be a compact repository identifier")
    text = str(extracted_text or "").strip()
    if not text:
        reason = "image_text_not_extracted" if source_kind == "image" else "pasted_text_empty"
        return {"status": "needs_more_information", "reason": reason}
    signature = _extract_signature(text)
    if signature is None:
        return {"status": "needs_more_information", "reason": "actionable_error_not_found"}
    exc_class, code_location = signature

    location_key = code_location.rsplit(":", 1)[0]
    fingerprint = build_fingerprint(repo, location_key, "user_evidence", exc_class)
    for receipt in _load_receipts(home):
        if receipt.get("fingerprint") == fingerprint:
            duplicate_status = (
                "duplicate_pending"
                if receipt.get("status") == "pending_confirmation"
                else "duplicate_confirmed"
            )
            return {
                "status": duplicate_status,
                "receipt_id": receipt["receipt_id"],
                "fingerprint": fingerprint,
            }

    record = record_external_defect(
        repo=repo,
        exc_class=exc_class,
        message=text,
        code_location=code_location,
        func_name="user_evidence",
        session_kind="user_evidence",
        path=code_defects_path(home),
        now=now,
    )
    receipt_id = record.defect_id.replace("defect-", "evidence-", 1)
    row = {
        "receipt_id": receipt_id,
        "fingerprint": record.fingerprint,
        "repo": record.repo,
        "exc_class": record.exc_class,
        "code_location": record.code_location,
        "source_kind": source_kind,
        "status": "pending_confirmation",
        "created_at": record.timestamp,
    }
    _write_receipt(row, home=home)
    return {
        "status": "confirmation_required",
        "receipt_id": receipt_id,
        "fingerprint": record.fingerprint,
        "exc_class": record.exc_class,
        "code_location": record.code_location,
    }


def confirm_error_evidence(
    receipt_id: str,
    *,
    config: dict | None = None,
    home: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Confirm one sanitized receipt, then run canonical intake for only it."""
    path = _receipt_path(receipt_id, home)
    if not path.exists():
        return {"status": "not_found", "receipt_id": receipt_id}
    try:
        receipt = _validate_receipt(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {"status": "invalid_receipt", "receipt_id": receipt_id}
    if receipt["receipt_id"] != receipt_id:
        return {"status": "invalid_receipt", "receipt_id": receipt_id}
    if receipt.get("status") == "confirmed":
        intake = run_intake(
            config=config,
            home=home,
            defects_path=code_defects_path(home),
            now=now,
            only_fingerprints={str(receipt["fingerprint"])},
        )
        return {"status": "already_confirmed", "receipt_id": receipt_id, "intake": intake}
    if receipt.get("status") != "pending_confirmation":
        return {"status": "invalid_receipt", "receipt_id": receipt_id}

    ts = _timestamp(now)
    decision = {
        "receipt_id": receipt_id,
        "fingerprint": receipt["fingerprint"],
        "decision": "confirmed",
        "timestamp": ts,
    }
    ledger = evidence_confirmations_path(home)
    assert_safe_identifier(receipt_id, field="receipt_id")
    assert_safe_identifier(str(receipt["fingerprint"]), field="fingerprint")
    already_ledgered = False
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                existing = json.loads(line)
            except ValueError:
                continue
            if (
                existing.get("decision") == "confirmed"
                and existing.get("fingerprint") == receipt["fingerprint"]
            ):
                already_ledgered = True
                break
    if not already_ledgered:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")

    receipt["status"] = "confirmed"
    receipt["confirmed_at"] = ts
    _write_receipt(receipt, home=home)
    intake = run_intake(
        config=config,
        home=home,
        defects_path=code_defects_path(home),
        now=now,
        only_fingerprints={str(receipt["fingerprint"])},
    )
    return {"status": "confirmed", "receipt_id": receipt_id, "intake": intake}
