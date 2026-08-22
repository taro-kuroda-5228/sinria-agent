"""Fail-closed A0-A4 provider-boundary action authorization."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from .contracts import ActionTier

@dataclass(frozen=True)
class ActionRequest:
    action: str; tier: ActionTier; workspace_id: str; resource_id: str | None = None
    range: str | None = None; count: int = 1; expires_at: datetime | None = None
    review_id: str | None = None; dry_run: bool = False

class ActionPolicy:
    def authorize(self, req: ActionRequest, *, now: datetime | None = None) -> bool:
        if not req.action or req.count < 1 or req.tier not in ActionTier: raise PermissionError("invalid action")
        now = now or datetime.now(timezone.utc)
        if req.expires_at and req.expires_at <= now: raise PermissionError("capability expired")
        if req.tier >= ActionTier.A2 and not req.resource_id: raise PermissionError("resource grant required")
        if req.tier >= ActionTier.A3 and not req.review_id: raise PermissionError("independent review required")
        if req.tier == ActionTier.A2 and req.count > 100: raise PermissionError("grant count exceeded")
        if req.tier == ActionTier.A4 and not req.review_id: raise PermissionError("A4 always requires review")
        if req.action not in {"read", "search", "classify", "draft", "candidate", "sheet_update", "drive_write", "send", "delete", "share", "deploy", "purge"}: raise PermissionError("action deny-by-default")
        return True
    def enforce_before_provider(self, req: ActionRequest, *, now=None) -> bool:
        self.authorize(req, now=now)
        if req.dry_run: return False
        return True
