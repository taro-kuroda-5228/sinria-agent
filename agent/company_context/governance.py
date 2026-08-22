"""RACI/JML and release-safe governance primitives."""
from dataclasses import dataclass
@dataclass(frozen=True)
class RACI:
    responsible: str; accountable: str; consulted: tuple[str,...]=(); informed: tuple[str,...]=()
    def __post_init__(self):
        if not self.accountable or self.accountable in self.consulted: raise ValueError("unique accountable required")

def may_approve(*, proposer: str, approver: str, approver_role: str, required_role: str="reviewer") -> bool:
    return proposer != approver and approver_role == required_role

@dataclass
class Lifecycle:
    status: str = "active"; token_valid: bool = True; grant_valid: bool = True; retrieval_enabled: bool = True
    def leaver(self): self.status="leaver"; self.token_valid=self.grant_valid=self.retrieval_enabled=False
    def device_lost(self): self.status="quarantined"; self.token_valid=self.grant_valid=self.retrieval_enabled=False
