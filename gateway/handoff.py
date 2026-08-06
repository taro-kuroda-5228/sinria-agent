"""Secure, one-time claims for G2 gateway session handoffs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sinria_constants import get_sinria_home

_CODE_RE = re.compile(r"[A-Z0-9]")
_REQUIRED = {"version", "source", "sessionId", "ownerDeviceIdHash", "createdAt", "expiresAt"}


class HandoffError(Exception):
    """Expected, sanitized handoff failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Handoff:
    session_id: str
    owner_device_id_hash: str


def normalize_code(code: str) -> str:
    """Normalize a displayed code without retaining its raw representation."""
    if not isinstance(code, str):
        return ""
    return "".join(_CODE_RE.findall(code.upper()))


def _handoff_dir() -> Path:
    return get_sinria_home() / "handoffs" / "even-g2"


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def claim_handoff(code: str, *, owner_device_id: str | None = None, now_ms: int | None = None) -> Handoff:
    """Atomically consume a valid G2 handoff record.

    No raw code, session key, or record body is logged or persisted by this
    function.  A successful claim renames the record before returning.
    """
    normalized = normalize_code(code)
    if not normalized:
        raise HandoffError("invalid")
    directory = _handoff_dir()
    path = directory / f"{hashlib.sha256(normalized.encode('ascii')).hexdigest()}.json"
    claimed = path.with_name(path.name[:-5] + ".claimed.json")
    try:
        if _mode(directory) != 0o700 or _mode(path) != 0o600:
            raise HandoffError("permissions")
        with path.open("r", encoding="utf-8") as stream:
            record: Any = json.load(stream)
    except HandoffError:
        raise
    except FileNotFoundError:
        raise HandoffError("notfound") from None
    except (OSError, ValueError, TypeError):
        raise HandoffError("malformed") from None

    if not isinstance(record, dict) or set(record) != _REQUIRED:
        raise HandoffError("malformed")
    if record.get("version") != 1 or record.get("source") != "even-g2":
        raise HandoffError("malformed")
    session_id = record.get("sessionId")
    owner_hash = record.get("ownerDeviceIdHash")
    created = record.get("createdAt")
    expires = record.get("expiresAt")
    if (not isinstance(session_id, str) or not session_id or
            not isinstance(owner_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", owner_hash) or
            not isinstance(created, int) or isinstance(created, bool) or
            not isinstance(expires, int) or isinstance(expires, bool)):
        raise HandoffError("malformed")
    if owner_device_id is not None and hashlib.sha256(owner_device_id.encode("utf-8")).hexdigest() != owner_hash:
        raise HandoffError("owner")
    if (now_ms if now_ms is not None else int(time.time() * 1000)) >= expires:
        raise HandoffError("expired")

    try:
        os.replace(path, claimed)
    except FileNotFoundError:
        raise HandoffError("used") from None
    except OSError:
        raise HandoffError("used") from None
    return Handoff(session_id=session_id, owner_device_id_hash=owner_hash)
