"""Deterministic task resolution across Sinria input channels.

Conversation bodies remain isolated.  Only explicit bindings and sanitized
resource metadata may connect two channels; semantic similarity alone never
silently merges work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .resource_scope import resource_scopes_overlap


@dataclass(frozen=True)
class InboundTaskEnvelope:
    workspace_id: str
    channel_key: str
    source_message_ref: str
    reply_to_message_ref: str | None = None
    explicit_task_id: str | None = None
    sanitized_intent_key: str | None = None
    write_resource_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskCandidate:
    task_id: str
    workspace_id: str
    revision: int
    channel_keys: tuple[str, ...] = ()
    source_message_refs: tuple[str, ...] = ()
    resource_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    decision: Literal["create", "join", "queue", "review_required", "reject_stale"]
    task_id: str | None
    reason_code: str
    conflicting_task_id: str | None = None
    sanitized_explanation: str = ""


class TaskCoordinator:
    """Resolve routing with source identity ahead of durable/context hints."""

    def resolve(
        self,
        envelope: InboundTaskEnvelope,
        candidates: Sequence[TaskCandidate],
    ) -> Resolution:
        same_workspace = [
            item for item in candidates if item.workspace_id == envelope.workspace_id
        ]

        if envelope.reply_to_message_ref:
            for item in same_workspace:
                if envelope.reply_to_message_ref in item.source_message_refs:
                    return Resolution(
                        "join", item.task_id, "reply_binding",
                        sanitized_explanation="Joined the task bound to the replied message.",
                    )

        if envelope.explicit_task_id:
            for item in same_workspace:
                if item.task_id == envelope.explicit_task_id:
                    return Resolution(
                        "join", item.task_id, "explicit_task_id",
                        sanitized_explanation="Joined the explicitly referenced task.",
                    )

        for item in same_workspace:
            if envelope.channel_key in item.channel_keys:
                return Resolution(
                    "join", item.task_id, "active_channel_binding",
                    sanitized_explanation="Joined the active task for this channel.",
                )

        for requested in envelope.write_resource_scopes:
            for item in same_workspace:
                if any(resource_scopes_overlap(requested, held) for held in item.resource_scopes):
                    return Resolution(
                        "queue", None, "resource_conflict",
                        conflicting_task_id=item.task_id,
                        sanitized_explanation="Waiting for a conflicting resource claim.",
                    )

        return Resolution(
            "create", None, "no_binding",
            sanitized_explanation="No explicit task binding was found.",
        )
