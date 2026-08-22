from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from .bridge import validate_metadata_only
from .policy import KillSwitch, ScopePolicy, WorkspaceIdentity


@dataclass(frozen=True)
class WorkspaceSource:
    provider: str
    source_kind: str
    visibility: str
    provider_resource_id: str


class TeamSourceClient:
    """Registers opaque Workspace locators; raw provider IDs remain local."""

    def __init__(
        self,
        *,
        identity: WorkspaceIdentity,
        policy: ScopePolicy,
        kill_switch: KillSwitch,
        transport: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        self.identity = identity
        self.policy = policy
        self.kill_switch = kill_switch
        self.transport = transport

    def register(self, source: WorkspaceSource, *, dry_run: bool = False) -> dict[str, Any]:
        self.kill_switch.require_enabled()
        self.policy.authorize(self.identity, "source.register")
        if source.provider not in {"google_drive", "gmail"}:
            raise ValueError("unsupported provider")
        if source.provider == "gmail" and source.visibility != "member_private_signal":
            raise ValueError("Gmail sources must remain member-private")
        fingerprint = hashlib.sha256(source.provider_resource_id.encode()).hexdigest()
        payload = {
            "operation": "register",
            "workspaceId": self.identity.workspace_id,
            "memberId": self.identity.member_id,
            "instanceId": self.identity.instance_id,
            "provider": source.provider,
            "sourceKind": source.source_kind,
            "visibility": source.visibility,
            "resourceFingerprint": f"sha256:{fingerprint}",
            "rawContextStored": False,
            "rawLocatorStored": False,
        }
        validate_metadata_only(payload)
        if dry_run:
            return {"ok": True, "dryRun": True, "payload": payload}
        return self.transport(payload)
