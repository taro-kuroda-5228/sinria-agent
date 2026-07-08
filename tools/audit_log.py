"""Persistent JSONL audit sink for approval/security events.

Sinria's healthcare/confidential mission requires durable evidence of what
the approval layer saw and decided. Events are appended one JSON object per
line to ``{HERMES_HOME}/audit/audit-YYYYMMDD.jsonl``.

Invariants:
  * Never raises — approval flow is safety-critical, audit is observability.
  * Never records full command text: a bounded preview plus a SHA-256 digest
    is enough to correlate and prove what ran without durably re-storing
    secrets that may be embedded in command arguments.
  * Disabled with ``HERMES_AUDIT_LOG=false`` (on by default).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_PREVIEW_LEN = 160
_lock = threading.Lock()


def audit_enabled() -> bool:
    # Deliberately "on unless explicitly disabled" (not utils.is_truthy_value):
    # a typo'd value must not silently turn the audit trail off — only a
    # recognized negative does.
    return os.getenv("HERMES_AUDIT_LOG", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _audit_dir() -> Path:
    return get_hermes_home() / "audit"


def _sanitize(kwargs: dict) -> dict:
    """Flatten hook kwargs into a JSONL-safe record.

    ``command`` is replaced by a bounded preview + SHA-256 digest so the audit
    trail proves what ran without persisting embedded secrets verbatim.
    """
    out: dict = {}
    for key, value in kwargs.items():
        if key == "command":
            text = str(value or "")
            out["command_sha256"] = hashlib.sha256(
                text.encode("utf-8", "replace")
            ).hexdigest()
            preview = text[:_PREVIEW_LEN]
            out["command_preview"] = preview + ("…" if len(text) > _PREVIEW_LEN else "")
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v)[:200] for v in value]
        else:
            out[key] = str(value)[:200]
    return out


def record_audit_event(event: str, **kwargs) -> None:
    """Append one audit event. Best-effort: swallows every failure."""
    if not audit_enabled():
        return
    try:
        now = datetime.now(timezone.utc)
        record = {"ts": now.isoformat(), "event": str(event), **_sanitize(kwargs)}
        directory = _audit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"audit-{now.strftime('%Y%m%d')}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:
        logger.debug("audit sink write failed: %s", exc)
