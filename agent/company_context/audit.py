"""Hash-chained append-only audit and fail-closed release gates."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
@dataclass(frozen=True)
class AuditEvent: sequence: int; event_type: str; metadata: dict; previous_hash: str; digest: str
class ImmutableAudit:
    def __init__(self): self.events=[]
    def append(self, event_type, metadata):
        if not isinstance(metadata, dict) or any(x in str(metadata).lower() for x in ("token", "secret", "password", "raw_body")): raise ValueError("unsafe audit metadata")
        prev=self.events[-1].digest if self.events else "GENESIS"
        payload=json.dumps({"sequence":len(self.events)+1,"event_type":event_type,"metadata":metadata,"previous_hash":prev},sort_keys=True,separators=(",",":"))
        digest=hashlib.sha256(payload.encode()).hexdigest(); event=AuditEvent(len(self.events)+1,event_type,metadata,prev,digest); self.events.append(event); return event
    def verify(self):
        prev="GENESIS"
        for e in self.events:
            payload=json.dumps({"sequence":e.sequence,"event_type":e.event_type,"metadata":e.metadata,"previous_hash":e.previous_hash},sort_keys=True,separators=(",",":"))
            if e.previous_hash != prev or hashlib.sha256(payload.encode()).hexdigest()!=e.digest: return False
            prev=e.digest
        return True

def release_allowed(*, audit_ok: bool, required_gates: dict[str,bool], tier: int=0) -> bool:
    return bool(audit_ok and all(required_gates.values()) and (tier < 3 or audit_ok))
