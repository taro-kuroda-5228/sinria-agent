"""Owner-bound, approval-gated Gmail private-signal state machine."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, time
from typing import Any, Protocol

class GmailTransport(Protocol):
    def send(self, *, owner_id: str, message: dict[str,Any], idempotency_key: str) -> dict[str,Any]: ...
    def readback(self, *, owner_id: str, idempotency_key: str) -> dict[str,Any] | None: ...

@dataclass
class SignalState:
    key: str; owner_id: str; state: str = "draft"; message: dict[str,Any] | None = None; message_id: str|None = None

class GmailPrivateSignal:
    def __init__(self, owner_id: str, transport: GmailTransport, *, clock=time.time):
        if not owner_id: raise ValueError("owner required")
        self.owner_id, self.transport, self.clock = owner_id, transport, clock
        self.states: dict[str,SignalState] = {}
    @staticmethod
    def _digest(message): return hashlib.sha256(json.dumps(message,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    def draft(self, key: str, message: dict[str,Any]) -> SignalState:
        if not key or not isinstance(message,dict) or not message.get("subject") or not message.get("body"): raise ValueError("invalid private signal")
        state=self.states.setdefault(key,SignalState(key,self.owner_id)); state.message=dict(message); state.state="pending_approval"; return state
    def approve(self, key: str, approval: dict[str,Any]) -> SignalState:
        state=self.states.get(key)
        if not state or state.state != "pending_approval": raise PermissionError("approval not pending")
        if approval.get("owner_id") != self.owner_id or approval.get("idempotency_key") != key or approval.get("payload_hash") != self._digest(state.message): raise PermissionError("approval mismatch")
        if float(approval.get("expires_at",0)) < self.clock(): raise PermissionError("approval expired")
        state.state="approved"; return state
    def send(self, key: str) -> SignalState:
        state=self.states.get(key)
        if not state or state.owner_id != self.owner_id: raise PermissionError("unknown owner-bound signal")
        if state.state == "sent": return state
        if state.state != "approved": raise PermissionError("approval required")
        state.state="sending"
        try:
            result=self.transport.send(owner_id=self.owner_id,message=state.message or {},idempotency_key=key)
            if result.get("status") == "unknown": state.state="unknown"; return state
            read = self.transport.readback(owner_id=self.owner_id, idempotency_key=key)
            if not read or read.get("idempotency_key") != key:
                state.state="unknown"
            else:
                state.message_id=read.get("message_id") or result.get("message_id"); state.state="sent"
        except (TimeoutError,ConnectionError):
            read=self.transport.readback(owner_id=self.owner_id,idempotency_key=key)
            if read and read.get("idempotency_key") == key: state.message_id=read.get("message_id"); state.state="sent"
            else: state.state="unknown"
        return state
    def readback(self, key: str) -> SignalState:
        state=self.states.get(key)
        if not state: raise KeyError(key)
        result=self.transport.readback(owner_id=self.owner_id,idempotency_key=key)
        if result and result.get("idempotency_key") == key: state.message_id=result.get("message_id"); state.state="sent"
        return state
