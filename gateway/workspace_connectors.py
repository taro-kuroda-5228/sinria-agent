"""First-party Sinria Workspace bindings for Discord and Slack transports.

External platforms are connectors only: a binding maps their chat/thread IDs to
one native Workspace conversation.  Message bodies are never copied into this
registry; only a caller-supplied sanitized preview is journaled for dedupe.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from gateway.session import SessionSource, build_workspace_session_key
from gateway.workspace_journal import WorkspaceJournal

_ALLOWED_PLATFORMS = frozenset({"discord", "slack"})
_ALLOWED_BOUNDARIES = frozenset({"private", "internal", "partner", "clinical"})


@dataclass(frozen=True)
class WorkspaceConnectorBinding:
    binding_id: str
    platform: str
    external_chat_id: str
    external_thread_id: str
    workspace_id: str
    space_id: str
    conversation_id: str
    boundary: str

    @property
    def session_key(self) -> str:
        return build_workspace_session_key(
            self.workspace_id,
            self.space_id,
            self.conversation_id,
        )


@dataclass(frozen=True)
class WorkspaceConnectorMapping:
    accepted: bool
    reason: str
    binding: Optional[WorkspaceConnectorBinding] = None
    task_id: Optional[str] = None
    task_revision: Optional[int] = None


class WorkspaceConnectorRegistry:
    """Durable connector binding registry backed by the local Workspace DB."""

    def __init__(self, journal: Optional[WorkspaceJournal] = None):
        self.journal = journal or WorkspaceJournal()

    @staticmethod
    def _platform_name(source_or_platform) -> str:
        value = getattr(source_or_platform, "value", source_or_platform)
        return str(value).strip().lower()

    @staticmethod
    def _message_ref(
        *,
        platform: str,
        external_chat_id: str,
        external_thread_id: str,
        event_id: str,
    ) -> str:
        """Build a namespaced, metadata-only connector message reference."""
        return (
            f"connector:{platform}:{external_chat_id}:"
            f"{external_thread_id}:{event_id}"
        )

    def register_binding(
        self,
        *,
        binding_id: str,
        platform: str,
        external_chat_id: str,
        external_thread_id: str = "",
        workspace_id: str,
        space_id: str,
        conversation_id: str,
        boundary: str,
    ) -> WorkspaceConnectorBinding:
        platform = self._platform_name(platform)
        boundary = str(boundary).strip().lower()
        if platform not in _ALLOWED_PLATFORMS:
            raise ValueError("Workspace connectors support only discord and slack")
        if boundary not in _ALLOWED_BOUNDARIES:
            raise ValueError("invalid Workspace connector boundary")
        binding = WorkspaceConnectorBinding(
            binding_id=str(binding_id).strip(),
            platform=platform,
            external_chat_id=str(external_chat_id).strip(),
            external_thread_id=str(external_thread_id or "").strip(),
            workspace_id=str(workspace_id).strip(),
            space_id=str(space_id).strip(),
            conversation_id=str(conversation_id).strip(),
            boundary=boundary,
        )
        if not all(
            (
                binding.binding_id,
                binding.external_chat_id,
                binding.workspace_id,
                binding.space_id,
                binding.conversation_id,
            )
        ):
            raise ValueError("connector binding identifiers must be non-empty")
        now = time.time()
        with self.journal._connect() as conn:
            conn.execute(
                "insert into connector_bindings"
                " (binding_id, platform, external_chat_id, external_thread_id,"
                " workspace_id, space_id, conversation_id, boundary, enabled, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)"
                " on conflict(platform, external_chat_id, external_thread_id) do update set"
                " binding_id=excluded.binding_id, workspace_id=excluded.workspace_id,"
                " space_id=excluded.space_id, conversation_id=excluded.conversation_id,"
                " boundary=excluded.boundary, enabled=1, updated_at=excluded.updated_at",
                (
                    binding.binding_id,
                    binding.platform,
                    binding.external_chat_id,
                    binding.external_thread_id,
                    binding.workspace_id,
                    binding.space_id,
                    binding.conversation_id,
                    binding.boundary,
                    now,
                    now,
                ),
            )
        return binding

    def get_binding(
        self,
        platform: str,
        external_chat_id: str,
        external_thread_id: str = "",
    ) -> Optional[WorkspaceConnectorBinding]:
        platform = self._platform_name(platform)
        chat_id = str(external_chat_id).strip()
        thread_id = str(external_thread_id or "").strip()
        with self.journal._connect() as conn:
            row = conn.execute(
                "select binding_id, platform, external_chat_id, external_thread_id,"
                " workspace_id, space_id, conversation_id, boundary"
                " from connector_bindings where platform = ? and external_chat_id = ?"
                " and external_thread_id = ? and enabled = 1",
                (platform, chat_id, thread_id),
            ).fetchone()
            if row is None and thread_id:
                row = conn.execute(
                    "select binding_id, platform, external_chat_id, external_thread_id,"
                    " workspace_id, space_id, conversation_id, boundary"
                    " from connector_bindings where platform = ? and external_chat_id = ?"
                    " and external_thread_id = '' and enabled = 1",
                    (platform, chat_id),
                ).fetchone()
        return WorkspaceConnectorBinding(**dict(row)) if row is not None else None

    def map_inbound(
        self,
        source: SessionSource,
        *,
        event_id: str,
        reply_to_event_id: str = "",
        explicit_task_id: str = "",
        sanitized_preview: str = "",
        is_self_origin: bool = False,
        internal: bool = False,
    ) -> WorkspaceConnectorMapping:
        """Resolve a connector binding and guard against webhook replay.

        ``internal=True`` marks a gateway-synthesized event (startup
        auto-resume probe, background-process completion notice).  Those still
        need their binding — it is what keeps the lane owned by its Workspace
        conversation — but they must skip the inbox journal.  The journal
        dedupes *external* deliveries by their platform event id, and a
        synthesized event has none: it reuses the origin's last known
        ``message_id`` (already journaled, so it reads as a duplicate) or
        carries nothing at all.  Judging it there silently drops the lane.
        Duplicate suppression for internal events belongs to whoever
        synthesizes them.
        """
        if is_self_origin or bool(getattr(source, "is_bot", False)):
            return WorkspaceConnectorMapping(False, "self_origin")
        platform = self._platform_name(source.platform)
        if platform not in _ALLOWED_PLATFORMS:
            return WorkspaceConnectorMapping(False, "unsupported_platform")
        external_chat_id = str(getattr(source, "parent_chat_id", None) or source.chat_id)
        thread_id = str(source.thread_id or "")
        binding = self.get_binding(platform, external_chat_id, thread_id)
        if binding is None:
            return WorkspaceConnectorMapping(False, "unbound")
        if internal:
            source.workspace_session_key = binding.session_key
            source.workspace_boundary = binding.boundary
            return WorkspaceConnectorMapping(True, "internal", binding)
        event_id = str(event_id).strip()
        if not event_id:
            return WorkspaceConnectorMapping(False, "missing_event_id", binding)
        source_message_ref = self._message_ref(
            platform=platform,
            external_chat_id=external_chat_id,
            external_thread_id=thread_id,
            event_id=event_id,
        )
        reply_to_message_ref = ""
        if reply_to_event_id:
            reply_to_message_ref = self._message_ref(
                platform=platform,
                external_chat_id=external_chat_id,
                external_thread_id=thread_id,
                event_id=str(reply_to_event_id),
            )
        inbound = self.journal.record_inbound(
            channel_key=binding.session_key,
            idempotency_key=f"connector:{platform}:{event_id}",
            kind="connector_message",
            sanitized_preview=str(sanitized_preview)[:240],
        )
        if not inbound.accepted:
            task_id = self.journal.find_task_by_message(source_message_ref)
            if task_id:
                source.workspace_task_id = task_id
                source.workspace_task_revision = 1
            return WorkspaceConnectorMapping(
                False, "duplicate", binding, task_id, 1 if task_id else None,
            )
        task_binding, _ = self.journal.resolve_or_create_task_binding(
            channel_key=binding.session_key,
            source_message_ref=source_message_ref,
            reply_to_message_ref=reply_to_message_ref or None,
            explicit_task_id=explicit_task_id or None,
        )
        task_id = str(task_binding["task_id"])
        source.workspace_session_key = binding.session_key
        source.workspace_boundary = binding.boundary
        source.workspace_task_id = task_id
        source.workspace_task_revision = 1
        return WorkspaceConnectorMapping(True, "mapped", binding, task_id, 1)


__all__ = [
    "WorkspaceConnectorBinding",
    "WorkspaceConnectorMapping",
    "WorkspaceConnectorRegistry",
]
