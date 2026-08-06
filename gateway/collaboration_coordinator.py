"""Platform-neutral coordination rules for shared Sinria sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gateway.collaboration import (
    Handoff,
    InvalidState,
    Participant,
    PermissionDenied,
    Proposal,
    WorkItem,
    WorkItemStatus,
)
from gateway.collaboration_store import CollaborationStore


class InputDisposition(str, Enum):
    PROCEED = "proceed"
    PROPOSAL = "proposal"


@dataclass(frozen=True)
class InputDecision:
    disposition: InputDisposition
    work_item: WorkItem
    proposal: Optional[Proposal] = None


@dataclass(frozen=True)
class ProposalResolution:
    work_item: WorkItem
    proposal: Proposal
    adopted_content: Optional[str]


@dataclass(frozen=True)
class CollaborationStatus:
    work_item: Optional[WorkItem]
    owner_actor_id: Optional[str]
    participants: tuple[Participant, ...]
    proposals: tuple[Proposal, ...]
    pending_handoff: Optional[Handoff]

    @property
    def pending_proposals(self) -> int:
        """Backward-compatible count for existing status consumers."""
        return len(self.proposals)


class CollaborationCoordinator:
    """Enforces ownership and explicit human-to-human handoff semantics."""

    def __init__(self, store: CollaborationStore):
        self.store = store

    def observe_input(
        self,
        session_key: str,
        actor_id: str,
        platform: str,
        content: str,
        now: Optional[float] = None,
    ) -> InputDecision:
        self.store.touch_participant(session_key, actor_id, platform, now=now)
        item = self.store.get_or_create_active_work_item(
            session_key, actor_id, platform, now=now
        )
        if item.owner_actor_id in {None, actor_id}:
            if item.owner_actor_id is None:
                item = self.store.claim_work_item(
                    item.id, item.version, actor_id=actor_id, now=now
                )
            return InputDecision(InputDisposition.PROCEED, item)
        proposal = self.store.create_proposal(
            item.id,
            expected_version=item.version,
            actor_id=actor_id,
            content=content,
            now=now,
        )
        return InputDecision(InputDisposition.PROPOSAL, item, proposal)

    def status(
        self,
        session_key: str,
        now: Optional[float] = None,
        active_ttl: float = 300.0,
    ) -> CollaborationStatus:
        item = self.store.get_active_work_item(session_key)
        participants = tuple(
            self.store.list_participants(
                session_key, now=now, active_ttl=active_ttl
            )
        )
        if item is None:
            return CollaborationStatus(None, None, participants, (), None)
        proposals = tuple(self.store.list_proposals(item.id, status="pending"))
        return CollaborationStatus(
            item,
            item.owner_actor_id,
            participants,
            proposals,
            self.store.get_pending_handoff(item.id),
        )

    def format_status(self, session_key: str, now: Optional[float] = None) -> str:
        status = self.status(session_key, now=now)
        active = ",".join(p.actor_id for p in status.participants) or "none"
        if status.work_item is None:
            return f"👥 Sinria Team · task=none · active={active} · action=/claim"
        handoff = (
            f"\nHandoff: →{status.pending_handoff.target_actor_id} "
            "(`/handoff accept` or `/handoff reject`)"
            if status.pending_handoff
            else ""
        )
        proposal_ids = ",".join(p.id[:8] for p in status.proposals) or "none"
        proposal_hint = (
            f"\nProposals: {proposal_ids} "
            "(`/proposal accept <id>` or `/proposal reject <id>`)"
            if status.proposals
            else "\nProposals: none"
        )
        return (
            "👥 Sinria Team"
            f"\nTask: {status.work_item.id[:8]} · state={status.work_item.status.value}"
            f" · v={status.work_item.version}"
            f"\nOwner: {status.owner_actor_id or 'unclaimed'}"
            f"\nActive: {active}"
            f"{proposal_hint}"
            f"{handoff}"
            "\nActions: `/team complete`, `/team cancel`, `/team release`, `/claim`"
        )

    def claim(
        self,
        session_key: str,
        actor_id: str,
        *,
        force: bool = False,
        now: Optional[float] = None,
    ) -> WorkItem:
        item = self._require_item(session_key)
        return self.store.claim_work_item(
            item.id, item.version, actor_id, force=force, now=now
        )

    def release(
        self, session_key: str, actor_id: str, now: Optional[float] = None
    ) -> WorkItem:
        item = self._require_item(session_key)
        return self.store.release_owner(item.id, item.version, actor_id, now=now)

    def offer_handoff(
        self,
        session_key: str,
        actor_id: str,
        target_actor_id: str,
        now: Optional[float] = None,
        expires_at: Optional[float] = None,
    ) -> Handoff:
        item = self._require_item(session_key)
        self.store.touch_participant(
            session_key, target_actor_id, item.platform, now=now
        )
        return self.store.offer_handoff(
            item.id,
            item.version,
            actor_id,
            target_actor_id,
            now=now,
            expires_at=expires_at,
        )

    def accept_handoff(
        self, session_key: str, actor_id: str, now: Optional[float] = None
    ) -> WorkItem:
        item = self._require_item(session_key)
        handoff = self.store.get_pending_handoff(item.id)
        if handoff is None:
            raise InvalidState("no handoff is pending")
        _, updated = self.store.accept_handoff(
            handoff.id, item.version, actor_id, now=now
        )
        return updated

    def reject_handoff(
        self, session_key: str, actor_id: str, now: Optional[float] = None
    ) -> Handoff:
        item = self._require_item(session_key)
        handoff = self.store.get_pending_handoff(item.id)
        if handoff is None:
            raise InvalidState("no handoff is pending")
        return self.store.reject_handoff(
            handoff.id, item.version, actor_id, now=now
        )

    def cancel_handoff(
        self, session_key: str, actor_id: str, now: Optional[float] = None
    ) -> Handoff:
        item = self._require_item(session_key)
        handoff = self.store.get_pending_handoff(item.id)
        if handoff is None:
            raise InvalidState("no handoff is pending")
        return self.store.cancel_handoff(
            handoff.id, item.version, actor_id, now=now
        )

    def resolve_proposal(
        self,
        session_key: str,
        actor_id: str,
        proposal_id: str,
        accept: bool,
        now: Optional[float] = None,
    ) -> ProposalResolution:
        item = self._require_item(session_key)
        proposal = self.store.resolve_proposal(
            proposal_id,
            item_id=item.id,
            expected_version=item.version,
            actor_id=actor_id,
            resolution="accepted" if accept else "rejected",
            now=now,
        )
        return ProposalResolution(
            work_item=item,
            proposal=proposal,
            adopted_content=proposal.content if accept else None,
        )

    def finish_work_item(
        self,
        session_key: str,
        actor_id: str,
        *,
        cancel: bool,
        now: Optional[float] = None,
    ) -> WorkItem:
        item = self._require_item(session_key)
        if item.owner_actor_id != actor_id:
            raise PermissionDenied("only the owner can finish the work item")
        status = WorkItemStatus.CANCELLED if cancel else WorkItemStatus.COMPLETED
        return self.store.transition_work_item(
            item.id, item.version, status, actor_id, now=now
        )

    def _require_item(self, session_key: str) -> WorkItem:
        item = self.store.get_active_work_item(session_key)
        if item is None:
            raise InvalidState("no active work item")
        return item
