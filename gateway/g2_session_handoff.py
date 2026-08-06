"""Single-use Even G2 to Gateway session handoff tokens.

The G2 relay writes metadata-only token records under the profile-aware Sinria
home. Gateway atomically claims one record, then resumes its SessionDB session
in only the requesting transport lane. Message bodies and raw codes are never
stored in this handoff directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from hermes_constants import get_sinria_home

_CODE_RE = re.compile(r"^[A-Z2-7]{8}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class G2SessionHandoff:
    session_id: str
    created_at_ms: int
    expires_at_ms: int


class G2SessionHandoffStore:
    """Claim metadata-only handoff tokens issued by the local G2 relay."""

    def __init__(
        self,
        storage_directory: Optional[Path] = None,
        *,
        now_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self._directory = storage_directory or (
            get_sinria_home() / "handoffs" / "even-g2"
        )
        self._now_ms = now_ms or (lambda: __import__("time").time_ns() // 1_000_000)

    @staticmethod
    def normalize_code(code: str) -> str:
        return re.sub(r"[-\s]", "", str(code)).upper()

    @staticmethod
    def _filename(code: str) -> str:
        return hashlib.sha256(code.encode("ascii")).hexdigest()

    def claim(self, code: str) -> Optional[G2SessionHandoff]:
        normalized = self.normalize_code(code)
        if not _CODE_RE.fullmatch(normalized):
            return None

        directory = self._directory
        try:
            directory_stat = directory.stat()
        except FileNotFoundError:
            return None
        if not directory.is_dir():
            return None
        if os.name == "posix":
            if stat_module.S_IMODE(directory_stat.st_mode) != 0o700:
                return None
            getuid = getattr(os, "getuid", None)
            if getuid is not None and directory_stat.st_uid != getuid():
                return None

        source = directory / self._filename(normalized)
        claimed = directory / f".{source.name}.claimed.{os.getpid()}"
        try:
            os.replace(source, claimed)
        except FileNotFoundError:
            return None

        try:
            file_stat = claimed.stat()
            if not claimed.is_file():
                return None
            if os.name == "posix":
                if stat_module.S_IMODE(file_stat.st_mode) != 0o600:
                    return None
                getuid = getattr(os, "getuid", None)
                if getuid is not None and file_stat.st_uid != getuid():
                    return None
            payload = json.loads(claimed.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            if payload.get("version") != 1 or payload.get("source") != "even-g2":
                return None
            session_id = payload.get("sessionId")
            owner_hash = payload.get("ownerDeviceIdHash")
            created_at = payload.get("createdAt")
            expires_at = payload.get("expiresAt")
            if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
                return None
            if not isinstance(owner_hash, str) or not _SHA256_RE.fullmatch(owner_hash):
                return None
            if not isinstance(created_at, (int, float)) or not isinstance(expires_at, (int, float)):
                return None
            created_at_ms = int(created_at)
            expires_at_ms = int(expires_at)
            if created_at_ms < 0 or expires_at_ms <= created_at_ms:
                return None
            if expires_at_ms <= self._now_ms():
                return None
            return G2SessionHandoff(
                session_id=session_id,
                created_at_ms=created_at_ms,
                expires_at_ms=expires_at_ms,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        finally:
            try:
                claimed.unlink()
            except FileNotFoundError:
                pass


__all__ = ["G2SessionHandoff", "G2SessionHandoffStore"]
