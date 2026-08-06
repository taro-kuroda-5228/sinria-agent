"""Sinria multiplayer collaboration domain types.

The types in this module contain no platform SDK dependencies.  Raw message
content belongs to the local collaboration store and is never copied into
sanitized audit events.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CollaborationError(RuntimeError):
    """Base class for collaboration state errors."""


class ConflictError(CollaborationError):
    """The caller acted on a stale work-item version."""


class PermissionDenied(CollaborationError):
    """The actor is not allowed to perform the requested transition."""


class InvalidState(CollaborationError):
    """The requested transition is invalid for the current state."""


class PresenceState(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"


class WorkItemStatus(str, Enum):
    ACTIVE = "active"
    WAITING_REVIEW = "waiting_review"
    WAITING_APPROVAL = "waiting_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            WorkItemStatus.COMPLETED,
            WorkItemStatus.CANCELLED,
        }


class HandoffStatus(str, Enum):
    OFFERED = "offered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Participant:
    session_key: str
    actor_id: str
    platform: str
    last_seen: float
    presence: PresenceState


@dataclass(frozen=True)
class WorkItem:
    id: str
    session_key: str
    owner_actor_id: Optional[str]
    requester_actor_id: str
    platform: str
    status: WorkItemStatus
    version: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class Proposal:
    id: str
    work_item_id: str
    actor_id: str
    content: str
    content_sha256: str
    status: str
    created_at: float
    resolved_at: Optional[float]
    resolved_by: Optional[str]


@dataclass(frozen=True)
class Handoff:
    id: str
    work_item_id: str
    from_actor_id: str
    target_actor_id: str
    work_item_version: int
    status: HandoffStatus
    created_at: float
    expires_at: Optional[float]
    resolved_at: Optional[float]


@dataclass(frozen=True)
class CollaborationAuditEvent:
    id: int
    work_item_id: str
    event_type: str
    actor_id: str
    work_item_version: int
    subject_id: Optional[str]
    payload_sha256: Optional[str]
    created_at: float
