from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    member_id: str
    instance_id: str

    def __post_init__(self) -> None:
        if not all(value and value.strip() for value in (self.workspace_id, self.member_id, self.instance_id)):
            raise ValueError("workspace, member, and instance identity are required")


@dataclass(frozen=True)
class ScopePolicy:
    workspace_id: str
    allowed_actions: frozenset[str]

    def authorize(self, identity: WorkspaceIdentity, action: str) -> None:
        if identity.workspace_id != self.workspace_id:
            raise PermissionError("workspace identity mismatch")
        if action not in self.allowed_actions:
            raise PermissionError(f"action not allowed: {action}")


@dataclass(frozen=True)
class KillSwitch:
    enabled: bool = True
    reason: str = ""

    def require_enabled(self) -> None:
        if not self.enabled:
            raise PermissionError(f"organizational context sync disabled: {self.reason or 'kill switch'}")
