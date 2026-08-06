"""Data models used by the Core Autonomy Kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CapabilityGrant:
    """Capability issued to an account for a specific scope and limits."""

    grant_id: str
    account: str
    scope: str
    expires_at: Optional[Any] = None
    limits: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.grant_id:
            raise ValueError("grant_id is required")
        if not self.account:
            raise ValueError("account is required")
        if not self.scope:
            raise ValueError("scope is required")


@dataclass(frozen=True)
class ActionRequest:
    """A single autonomy action invocation request."""

    request_id: str
    account: str
    scope: str
    action: str
    body: str
    constraints: Dict[str, Any] = field(default_factory=dict)
    capability_grants: List[CapabilityGrant] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id is required")
        if not self.account:
            raise ValueError("account is required")
        if not self.scope:
            raise ValueError("scope is required")
        if not self.action:
            raise ValueError("action is required")


@dataclass(frozen=True)
class Decision:
    """Policy decision for an action request."""

    outcome: str
    reason: str = ""
    grant_id: Optional[str] = None

    def is_allow(self) -> bool:
        return self.outcome == "allow"


@dataclass
class ActionReceipt:
    """Recorded result of request execution evaluation/attempt."""

    request_id: str
    decision: Decision
    executed: bool = False
    idempotent: bool = False
    readback: str = "not_read"
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def with_defaults(
        self,
        *,
        executed: Optional[bool] = None,
        idempotent: Optional[bool] = None,
        readback: Optional[str] = None,
        result: Any = None,
        error: Optional[str] = None,
        decision: Optional[Decision] = None,
    ) -> "ActionReceipt":
        return ActionReceipt(
            request_id=self.request_id,
            decision=decision if decision is not None else self.decision,
            executed=self.executed if executed is None else executed,
            idempotent=self.idempotent if idempotent is None else idempotent,
            readback=self.readback if readback is None else readback,
            result=self.result if result is None else result,
            error=self.error if error is None else error,
            created_at=self.created_at,
        )
