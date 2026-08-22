"""M0 identity, scope, lifecycle and policy contracts (metadata only)."""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import Any
from .policy import WorkspaceIdentity

CANONICAL_WORKSPACE_ID = "medical-horizon"
WORKSPACE_ALIASES = {"medical-horizon": CANONICAL_WORKSPACE_ID, "medical_horizon": CANONICAL_WORKSPACE_ID, "workspace_medical_horizon": CANONICAL_WORKSPACE_ID}

def canonical_workspace_id(value: str) -> str:
    try: return WORKSPACE_ALIASES[value]
    except KeyError: raise ValueError("unknown workspace") from None

@dataclass(frozen=True)
class AuthContext:
    identity: WorkspaceIdentity
    auth_method: str
    scopes: frozenset[str]
    authenticated: bool = True
    def require(self, workspace_id: str, member_id: str | None = None, instance_id: str | None = None) -> None:
        if not self.authenticated:
            raise PermissionError("identity scope mismatch")
        try:
            requested = canonical_workspace_id(workspace_id)
        except ValueError as exc:
            raise PermissionError("identity scope mismatch") from exc
        if requested != self.identity.workspace_id: raise PermissionError("identity scope mismatch")
        if member_id is not None and member_id != self.identity.member_id: raise PermissionError("member scope mismatch")
        if instance_id is not None and instance_id != self.identity.instance_id: raise PermissionError("instance scope mismatch")

class ActionTier(IntEnum): A0=0; A1=1; A2=2; A3=3; A4=4

@dataclass(frozen=True)
class JMLState:
    status: str
    revoked: bool = False
    def revoke(self) -> "JMLState": return JMLState("leaver", True)

def metadata_only(value: Any) -> bool:
    if isinstance(value, dict):
        return all(not any(x in str(k).lower() for x in ("token", "secret", "password", "credential", "raw", "body", "phi")) and metadata_only(v) for k,v in value.items())
    if isinstance(value, list): return all(metadata_only(v) for v in value)
    return value is None or isinstance(value, (str, int, float, bool))
