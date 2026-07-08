"""Sinria Hybrid Agent Bridge planning primitives.

This module is intentionally local and deterministic.  It defines the contract
for cloud apps that share UI/state while an on-prem Sinria runtime remains the
agent brain and tool executor.  It performs no network calls and never handles
raw secrets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class BridgeTransport(str, Enum):
    """Outbound-only transport choices for the on-prem bridge."""

    POLLING = "polling"
    REALTIME = "realtime"
    QUEUE = "queue"
    TUNNEL = "secure_tunnel"


class BridgeTaskStatus(str, Enum):
    """Cloud-visible lifecycle for a bridge task."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    COMPLETED = "completed"
    FAILED_RECOVERABLE = "failed_recoverable"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class BridgeSideEffect(str, Enum):
    """Maximum side-effect requested by a cloud task."""

    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    SEND = "send"
    DELETE = "delete"


class BridgeDataSensitivity(str, Enum):
    """Maximum data class named by cloud-visible metadata."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PATIENT = "patient"


_REVIEW_SIDE_EFFECTS = {BridgeSideEffect.WRITE, BridgeSideEffect.SEND, BridgeSideEffect.DELETE}
_REVIEW_SENSITIVITIES = {BridgeDataSensitivity.CONFIDENTIAL, BridgeDataSensitivity.PATIENT}
_ALLOWED_APP_IDS = {"chatops_crm", "sierra_service", "consent_agent"}


@dataclass(frozen=True)
class CloudTableSpec:
    name: str
    purpose: str
    columns: tuple[str, ...]
    cloud_data_boundary: str


@dataclass(frozen=True)
class HybridBridgePhase:
    phase: int
    title: str
    goal: str
    deliverables: tuple[str, ...]
    definition_of_done: str


@dataclass(frozen=True)
class BridgeTaskEnvelope:
    task_id: str
    app_id: str
    tenant_id: str
    requested_by: str
    task_text_summary: str
    side_effect: BridgeSideEffect = BridgeSideEffect.DRAFT
    sensitivity: BridgeDataSensitivity = BridgeDataSensitivity.INTERNAL
    status: BridgeTaskStatus = BridgeTaskStatus.PENDING
    clinical_context: bool = False
    external_egress: bool = False
    allowed_to_run_on_prem: bool | None = None
    autonomous_execution_allowed: bool | None = None
    review_required: bool | None = None
    required_review_role: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BridgeTaskDecision:
    allowed_to_run_on_prem: bool
    autonomous_execution_allowed: bool
    review_required: bool
    required_review_role: str | None
    reason: str
    next_status: BridgeTaskStatus
    safety_note: str = (
        "Planning-only decision. Cloud app state remains minimal; on-prem Sinria "
        "keeps private context, credentials, local files, and raw sensitive data."
    )


def phase_plan() -> tuple[HybridBridgePhase, ...]:
    """Return the Phase 1-5 implementation roadmap for Hybrid Agent Bridge."""

    return (
        HybridBridgePhase(
            1,
            "ChatOps CRM MVP with outbound polling",
            "Cloud UI creates tasks; on-prem Sinria polls, claims, runs locally, and returns results.",
            (
                "Minimal cloud task/run/result/review/improvement schema",
                "Outbound-only bridge worker contract",
                "Local task risk planner",
                "Dry-run worker with no network calls",
            ),
            "Kikuchi can submit a task in cloud UI and on-prem Sinria can safely plan/claim/result it.",
        ),
        HybridBridgePhase(
            2,
            "Realtime Bridge",
            "Replace/augment polling with realtime subscriptions or queues while preserving outbound-only security.",
            (
                "Transport interface",
                "Progress events",
                "Cancellation contract",
                "Idempotent claim/ack protocol",
            ),
            "Task updates are near-realtime and duplicate events do not duplicate side effects.",
        ),
        HybridBridgePhase(
            3,
            "Tool/Approval Gate and CRM operations",
            "Let Sinria draft and execute CRM operations only through explicit policy/review gates.",
            (
                "CRM action manifest",
                "Human review queue",
                "Draft-by-default sends/writes",
                "Recoverable blocked-action reports",
            ),
            "Sinria can prepare CRM updates/messages but cannot write/send without approval.",
        ),
        HybridBridgePhase(
            4,
            "Self-improvement loop",
            "Convert corrections and repeated failures into skill, policy, template, and eval proposals.",
            (
                "Improvement candidate table",
                "Correction/failure grouping",
                "Nightly local improvement job",
                "Concise milestone reporting",
            ),
            "Repeated human edits or failures produce reviewable improvement proposals.",
        ),
        HybridBridgePhase(
            5,
            "On-prem k3s/Kubernetes packaging",
            "Package on-prem Sinria as a resilient local AgentOS runtime for offices/hospitals.",
            (
                "Bridge/worker/tool-executor/scheduler deployments",
                "Helm values and secret references",
                "NetworkPolicy and ServiceAccount separation",
                "Local health, backup, and runbook",
            ),
            "A local k3s Sinria runtime connects to cloud apps without exposing inbound ports.",
        ),
    )


def mvp_table_specs() -> tuple[CloudTableSpec, ...]:
    """Return minimal cloud-side table specs for the Phase 1 ChatOps CRM MVP."""

    return (
        CloudTableSpec(
            "chat_messages",
            "Human-visible ChatOps messages and links to agent tasks.",
            ("id", "app_id", "tenant_id", "user_id", "body", "task_id", "created_at"),
            "User-visible message text only; no private vault dumps, secrets, credentials, or raw PHI.",
        ),
        CloudTableSpec(
            "crm_contacts",
            "Legacy/shared CRM contact view safe enough for cloud collaboration.",
            ("id", "tenant_id", "name", "organization", "status", "owner_user_id", "updated_at"),
            "Shared CRM fields; sensitive notes stay on-prem unless explicitly approved.",
        ),
        CloudTableSpec(
            "companies",
            "OpenClaw CRM company/account metadata migrated into the Sinria cloud UI surface.",
            ("id", "tenant_id", "display_name", "segment", "status", "owner_user_id", "metadata", "updated_at"),
            "Sanitized organization metadata only; no raw confidential notes, secrets, or contact details.",
        ),
        CloudTableSpec(
            "contacts",
            "Sanitized people/contact handles associated with companies and leads.",
            ("id", "tenant_id", "company_id", "display_name", "role", "safe_contact_ref", "status", "metadata", "updated_at"),
            "Sanitized contact labels and opaque refs only; raw email/phone values stay on-prem or in approved systems.",
        ),
        CloudTableSpec(
            "leads",
            "Sales lead pipeline rows shown in the Vercel board.",
            ("id", "tenant_id", "company_id", "contact_id", "campaign_id", "stage", "priority", "next_action", "agent_task_id", "updated_at"),
            "Pipeline metadata and sanitized next actions; detailed research/context remains on-prem.",
        ),
        CloudTableSpec(
            "campaigns",
            "Campaign grouping for lead queues and outreach planning.",
            ("id", "tenant_id", "name", "channel", "goal", "status", "owner_user_id", "updated_at"),
            "Sanitized campaign metadata only; no raw recipient lists or secrets.",
        ),
        CloudTableSpec(
            "outreach_drafts",
            "Draft copy prepared for human review before any outreach action.",
            ("id", "tenant_id", "lead_id", "agent_task_id", "review_request_id", "channel", "draft_text", "status", "updated_at"),
            "Sanitized draft text for review; real send is never performed from this table without approval.",
        ),
        CloudTableSpec(
            "outreach_jobs",
            "Queued safe work items linked to Sinria bridge tasks and review requests.",
            ("id", "tenant_id", "lead_id", "agent_task_id", "review_request_id", "job_kind", "status", "requested_by", "created_at"),
            "Job metadata only; real_send and real_form_submit remain blocked or require human confirmation.",
        ),
        CloudTableSpec(
            "interactions",
            "Sanitized timeline of CRM interactions and approvals.",
            ("id", "tenant_id", "lead_id", "agent_task_id", "interaction_kind", "summary", "created_at"),
            "Sanitized summaries only; no raw message bodies, contact values, or confidential evidence.",
        ),
        CloudTableSpec(
            "agent_notes",
            "Agent-visible notes and handoff summaries for CRM operations.",
            ("id", "tenant_id", "lead_id", "agent_task_id", "note_kind", "sanitized_summary", "created_at"),
            "Sanitized lesson/status summaries only; private reasoning, raw logs, and secrets stay local.",
        ),
        CloudTableSpec(
            "audit_logs",
            "Cloud-visible audit trail for approvals, blocks, and safe bridge actions.",
            (
                "id",
                "tenant_id",
                "actor",
                "action",
                "agent_task_id",
                "review_request_id",
                "risk_level",
                "sanitized_summary",
                "external_action_performed",
                "created_at",
            ),
            "No raw secrets, raw PHI, raw contact values, private vault context, or completed external action payloads; sanitized metadata only.",
        ),
        CloudTableSpec(
            "agent_tasks",
            "Work requests created by cloud UI and claimed by on-prem Sinria.",
            ("id", "app_id", "tenant_id", "requested_by", "task_text", "side_effect", "sensitivity", "status", "risk_level", "allowed_to_run_on_prem", "autonomous_execution_allowed", "review_required", "required_review_role", "human_approval_required", "external_action_performed", "external_egress", "recoverable", "stopped_at", "citation_ids", "created_at"),
            "Task summary and classification only; detailed context is resolved on-prem.",
        ),
        CloudTableSpec(
            "agent_runs",
            "One or more on-prem Sinria attempts for each task.",
            ("id", "task_id", "sinria_instance_id", "attempt", "status", "started_at", "completed_at", "error_summary"),
            "Operational metadata; error summaries must be sanitized/recoverable.",
        ),
        CloudTableSpec(
            "agent_results",
            "Sanitized outputs returned to cloud UI.",
            ("id", "run_id", "result_text", "result_json", "requires_review", "created_at"),
            "Result visible to collaborators; raw local evidence remains on-prem by default.",
        ),
        CloudTableSpec(
            "review_requests",
            "Human approval queue for writes/sends/high-risk outputs.",
            ("id", "run_id", "requested_to", "status", "approved_by", "decision_comment", "created_at"),
            "Review metadata and sanitized draft/action plan only.",
        ),
        CloudTableSpec(
            "improvement_candidates",
            "Corrections/failures that may become skills, policies, templates, or evals.",
            (
                "id",
                "tenant_id",
                "source_run_id",
                "category",
                "summary",
                "human_approval_required",
                "external_action_performed",
                "status",
                "created_at",
            ),
            "Sanitized lesson summary only; detailed logs stay local unless explicitly exported, and candidates never record completed external actions.",
        ),
    )


def plan_task(task: BridgeTaskEnvelope) -> BridgeTaskDecision:
    """Plan whether an on-prem Sinria bridge may run a cloud-created task.

    The bridge may always *plan/claim* a well-formed task locally, but autonomous
    execution is withheld when side effects, sensitivity, clinical context, or
    external egress require human review.
    """

    review_reasons: list[str] = []
    if task.app_id not in _ALLOWED_APP_IDS:
        return BridgeTaskDecision(
            allowed_to_run_on_prem=False,
            autonomous_execution_allowed=False,
            review_required=True,
            required_review_role="admin",
            reason=f"unknown app_id={task.app_id!r}; register the cloud app before on-prem execution",
            next_status=BridgeTaskStatus.FAILED_RECOVERABLE,
        )
    if task.allowed_to_run_on_prem is False:
        return BridgeTaskDecision(
            allowed_to_run_on_prem=False,
            autonomous_execution_allowed=False,
            review_required=True,
            required_review_role=task.required_review_role or "admin",
            reason="cloud policy disallowed on-prem execution; administrator review required before Sinria can run this task",
            next_status=BridgeTaskStatus.FAILED_RECOVERABLE,
        )
    if task.review_required is True or task.autonomous_execution_allowed is False:
        return BridgeTaskDecision(
            allowed_to_run_on_prem=True,
            autonomous_execution_allowed=False,
            review_required=True,
            required_review_role=task.required_review_role or "admin",
            reason="cloud policy requires human review before autonomous on-prem execution",
            next_status=BridgeTaskStatus.WAITING_REVIEW,
        )
    if task.side_effect in _REVIEW_SIDE_EFFECTS:
        review_reasons.append(f"side_effect={task.side_effect.value} requires human approval")
    if task.sensitivity in _REVIEW_SENSITIVITIES:
        review_reasons.append(f"sensitivity={task.sensitivity.value} requires restricted handling")
    if task.clinical_context:
        review_reasons.append("clinical_context=True requires clinical/administrator review")
    if task.external_egress:
        review_reasons.append("external_egress=True requires explicit approval before send/write")

    if review_reasons:
        role = "physician" if task.clinical_context or task.sensitivity == BridgeDataSensitivity.PATIENT else "admin"
        return BridgeTaskDecision(
            allowed_to_run_on_prem=True,
            autonomous_execution_allowed=False,
            review_required=True,
            required_review_role=role,
            reason="; ".join(review_reasons),
            next_status=BridgeTaskStatus.WAITING_REVIEW,
        )

    return BridgeTaskDecision(
        allowed_to_run_on_prem=True,
        autonomous_execution_allowed=True,
        review_required=False,
        required_review_role=None,
        reason="low-risk internal/public read or draft task may run on-prem autonomously",
        next_status=BridgeTaskStatus.RUNNING,
    )


def worker_contract(transport: BridgeTransport = BridgeTransport.POLLING) -> dict[str, Any]:
    """Return the outbound-only worker contract for docs/tools/dry-run output."""

    return {
        "success": True,
        "transport": transport.value,
        "direction": "on_prem_to_cloud_outbound_only",
        "no_inbound_ports_required": True,
        "polls_or_subscribes_to": ["agent_tasks", "cancel_requested status", "review decisions"],
        "writes_back_to": ["agent_runs", "agent_results", "review_requests", "improvement_candidates"],
        "secret_handling": "Tokens are read from environment/secret store and must never be logged or written to cloud task rows.",
        "cloud_data_boundary": "Cloud stores minimal task/status/result/review metadata; private context and raw sensitive data remain on-prem.",
    }


def to_plain_dict(value: Any) -> Any:
    """Serialize bridge dataclasses/enums into JSON-compatible values."""

    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_plain_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, tuple | list):
        return [to_plain_dict(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): to_plain_dict(val) for key, val in value.items()}
    return value
