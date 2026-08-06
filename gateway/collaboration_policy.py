"""Role/capability policy for Sinria shared-session operations."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Iterable, Mapping

from gateway.collaboration import WorkItem
from gateway.collaboration_store import CollaborationStore


class Capability(str, Enum):
    OBSERVE = "observe"
    CONTRIBUTE = "contribute"
    OPERATE = "operate"
    REVIEW = "review"
    ADMIN = "admin"


def _capability(value: object) -> Capability | None:
    try:
        return Capability(str(value).strip().lower())
    except ValueError:
        return None


def capabilities_for_actor(
    actor_id: str,
    role_ids: Iterable[str],
    role_capabilities: Mapping[str, Iterable[str]] | None = None,
    user_capabilities: Mapping[str, Iterable[str]] | None = None,
) -> set[Capability]:
    """Resolve stable platform IDs to internal capabilities.

    Human-readable role names are intentionally not accepted because names are
    mutable and unsafe as authorization identifiers.
    """
    result: set[Capability] = set()
    for role_id in {str(value) for value in role_ids}:
        for value in (role_capabilities or {}).get(role_id, ()):
            capability = _capability(value)
            if capability is not None:
                result.add(capability)
    for value in (user_capabilities or {}).get(str(actor_id), ()):
        capability = _capability(value)
        if capability is not None:
            result.add(capability)
    return result


def approval_binding(
    item: WorkItem,
    *,
    requester_actor_id: str,
    command: str,
    required_capability: Capability = Capability.REVIEW,
    require_distinct_approver: bool = True,
    allowed_role_ids: Iterable[str] = (),
) -> dict:
    """Build a raw-content-free binding for a dangerous operation."""
    return {
        "work_item_id": item.id,
        "work_item_version": item.version,
        "requester_actor_id": str(requester_actor_id),
        "required_capability": required_capability.value,
        "payload_sha256": hashlib.sha256(
            str(command).encode("utf-8", "replace")
        ).hexdigest(),
        "require_distinct_approver": bool(require_distinct_approver),
        "allowed_role_ids": sorted({str(value) for value in allowed_role_ids}),
    }


def authorize_approval(
    data: dict,
    actor_id: str,
    actor_role_ids: Iterable[str],
    actor_capabilities: set[Capability],
    store: CollaborationStore,
) -> bool:
    """Validate a collaboration-bound approval against current durable state."""
    metadata = data.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("work_item_id"):
        return True  # Legacy, non-collaboration approvals retain existing policy.

    requester = str(metadata.get("requester_actor_id", ""))
    if metadata.get("require_distinct_approver", True) and str(actor_id) == requester:
        return False

    required = _capability(metadata.get("required_capability", Capability.REVIEW.value))
    if Capability.ADMIN not in actor_capabilities:
        if required is None or required not in actor_capabilities:
            return False
        allowed_roles = {str(value) for value in metadata.get("allowed_role_ids", ())}
        if allowed_roles and not ({str(value) for value in actor_role_ids} & allowed_roles):
            return False

    item = store.get_work_item(str(metadata.get("work_item_id")))
    if item is None or item.version != int(metadata.get("work_item_version", -1)):
        return False

    expected_digest = str(metadata.get("payload_sha256", ""))
    actual_digest = hashlib.sha256(
        str(data.get("command", "")).encode("utf-8", "replace")
    ).hexdigest()
    return bool(expected_digest) and expected_digest == actual_digest
