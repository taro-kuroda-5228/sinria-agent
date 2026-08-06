"""Persistence layer for Core Autonomy Kernel state and ledger."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Optional

from .models import ActionReceipt, ActionRequest, Decision
from sinria_constants import get_sinria_home


class AutonomyStore:
    """Simple JSON state + JSONL ledger store under ~/.sinria/autonomy."""

    def __init__(self, home: Optional[str] = None) -> None:
        root = Path(home or get_sinria_home())
        self.base_dir = root / "autonomy"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.base_dir / "state.json"
        self.ledger_path = self.base_dir / "ledger.jsonl"

    @property
    def _empty_state(self) -> Dict:
        return {"receipts": {}, "grant_usage": {}}

    def _read_state(self) -> Dict:
        if not self.state_path.exists():
            return self._empty_state
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return self._empty_state

        if not isinstance(data, dict):
            return self._empty_state

        receipts = data.get("receipts")
        grant_usage = data.get("grant_usage")
        if not isinstance(receipts, dict) or not isinstance(grant_usage, dict):
            return self._empty_state
        data["receipts"] = receipts
        data["grant_usage"] = grant_usage
        if not isinstance(data.get("kill_switches", []), list):
            data["kill_switches"] = []
        return data

    def _atomic_write_state(self, payload: Dict) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix="state.", suffix=".json", dir=str(self.base_dir))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp_name, str(self.state_path))

    def _load_receipt(self, request_id: str) -> Optional[ActionReceipt]:
        state = self._read_state()
        raw = state.get("receipts", {}).get(request_id)
        if not isinstance(raw, dict):
            return None

        decision_raw = raw.get("decision")
        if not isinstance(decision_raw, dict):
            return None

        decision = Decision(
            outcome=decision_raw.get("outcome", "block"),
            reason=decision_raw.get("reason", ""),
            grant_id=decision_raw.get("grant_id"),
        )

        return ActionReceipt(
            request_id=raw.get("request_id", request_id),
            decision=decision,
            executed=bool(raw.get("executed", False)),
            idempotent=bool(raw.get("idempotent", False)),
            readback=str(raw.get("readback", "not_read")),
            result=raw.get("result"),
            error=raw.get("error"),
            created_at=raw.get("created_at", ""),
        )

    def get_receipt(self, request_id: str) -> Optional[ActionReceipt]:
        return self._load_receipt(request_id)

    def _persist_receipt(self, receipt: ActionReceipt) -> None:
        state = self._read_state()
        receipts = state.setdefault("receipts", {})
        receipts[receipt.request_id] = asdict(receipt)
        self._atomic_write_state(state)

    def set_kill_switch(self, scope: str, enabled: bool = True) -> None:
        """Persist a global/account/campaign kill switch."""
        if not scope:
            raise ValueError("kill switch scope is required")
        state = self._read_state()
        switches = set(state.get("kill_switches", []))
        if enabled:
            switches.add(scope)
        else:
            switches.discard(scope)
        state["kill_switches"] = sorted(switches)
        self._atomic_write_state(state)

    def is_killed(self, request: ActionRequest) -> bool:
        switches = set(self._read_state().get("kill_switches", []))
        campaign_id = str(request.constraints.get("campaign_id", ""))
        candidates = {"global", f"account:{request.account}"}
        if campaign_id:
            candidates.add(f"campaign:{campaign_id}")
        return bool(switches & candidates)

    def _increment_usage(self, grant_id: str, action: str, scope: str) -> None:
        state = self._read_state()
        usage = state.setdefault("grant_usage", {}).setdefault(grant_id, {})
        if not isinstance(usage, dict):
            usage = {}
            state["grant_usage"][grant_id] = usage

        usage[str(action)] = int(usage.get(str(action), 0)) + 1
        usage[str(scope)] = int(usage.get(str(scope), 0)) + 1
        state["grant_usage"][grant_id] = usage
        self._atomic_write_state(state)

    def get_grant_usage(self) -> Dict[str, Dict[str, int]]:
        state = self._read_state()
        raw = state.get("grant_usage", {})
        usage = {}
        for grant_id, values in raw.items():
            if not isinstance(values, dict):
                continue
            usage[grant_id] = {k: int(v) for k, v in values.items() if isinstance(v, int) or str(v).isdigit()}
        return usage

    def record_receipt(self, receipt: ActionReceipt) -> None:
        self._persist_receipt(receipt)
        self._append_to_ledger(receipt)

    def record_execution(self, receipt: ActionReceipt, request: ActionRequest) -> None:
        self._persist_receipt(receipt)
        self._append_to_ledger(receipt, request=request)

    def consume_limit(self, grant_id: str, action: str, scope: str) -> None:
        self._increment_usage(grant_id, action, scope)

    def _append_to_ledger(self, receipt: ActionReceipt, request: Optional[ActionRequest] = None) -> None:
        ledger_entry = {
            "request_id": receipt.request_id,
            "outcome": receipt.decision.outcome,
            "reason": receipt.decision.reason,
            "grant_id": receipt.decision.grant_id,
            "executed": receipt.executed,
            "readback": receipt.readback,
            "error": receipt.error,
            "created_at": receipt.created_at,
        }

        if request is not None:
            ledger_entry.update(
                {
                    "account": request.account,
                    "scope": request.scope,
                    "action": request.action,
                }
            )

        lines = []
        if self.ledger_path.exists():
            with self.ledger_path.open("r", encoding="utf-8") as f:
                existing = f.read()
                if existing:
                    lines.append(existing)
        lines.append(json.dumps(ledger_entry, sort_keys=True))

        payload = "\n".join(line for line in lines if line)
        payload = payload + "\n"

        fd, tmp_name = tempfile.mkstemp(prefix="ledger.", suffix=".jsonl", dir=str(self.base_dir))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, str(self.ledger_path))
