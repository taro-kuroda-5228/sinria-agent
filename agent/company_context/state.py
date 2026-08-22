from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class Receipt:
    idempotency_key: str
    status: str
    remote_id: str | None = None
    retry_blocked: bool = False
    candidate_id: str | None = None


class ReceiptLedger:
    """Local-only atomic sync receipts; never stores payloads or raw context."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def get(self, key: str) -> Receipt | None:
        records = self._load()
        value = records.get(key)
        return Receipt(**value) if value else None

    def find_by_remote_id(self, remote_id: str) -> Receipt | None:
        for value in self._load().values():
            receipt = Receipt(**value)
            if receipt.remote_id == remote_id:
                return receipt
        return None

    def put(self, receipt: Receipt) -> None:
        records = self._load()
        records[receipt.idempotency_key] = asdict(receipt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".receipts-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(records, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid receipt ledger")
        return data


class LocalSyncState:
    """Atomic metadata-only transport receipts for retry/readback."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"receipts": {}}
        except (FileNotFoundError, json.JSONDecodeError):
            return {"receipts": {}}

    def receipt(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read().get("receipts", {}).get(key)

    def record(self, key: str, **data: Any) -> None:
        allowed = {
            "operation", "status", "next_attempt_at", "revision", "claim_attempt",
            "task_id", "claim_id", "review_id", "result_id",
        }
        clean = {name: value for name, value in data.items() if name in allowed and value is not None}
        self.write_receipt(key, clean)

    def write_receipt(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            state = self._read()
            state.setdefault("receipts", {})[key] = {"idempotency_key": key, **data}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".transport-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                self.path.chmod(0o600)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
