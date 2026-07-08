"""Tool surface for Sinria Hybrid Agent Bridge planning.

This is deliberately planning-only.  It exposes the cloud/on-prem bridge
contract, MVP table specs, and task risk planner without making network calls.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from sinria_hybrid_bridge import (
    BridgeDataSensitivity,
    BridgeSideEffect,
    BridgeTaskEnvelope,
    BridgeTaskStatus,
    BridgeTransport,
    mvp_table_specs,
    phase_plan,
    plan_task,
    to_plain_dict,
    worker_contract,
)
from sinria_hybrid_bridge_governance import (
    ReviewDecision,
    ReviewRequest,
    apply_review_decision,
    propose_improvement_candidate,
)
from tools.registry import registry


def sinria_hybrid_bridge(
    mode: str,
    *,
    task_id: str | None = None,
    app_id: str = "chatops_crm",
    tenant_id: str = "medical_horizon",
    requested_by: str = "unknown",
    task_text_summary: str = "",
    side_effect: str = "draft",
    sensitivity: str = "internal",
    clinical_context: bool = False,
    external_egress: bool = False,
    metadata: Mapping[str, Any] | None = None,
    transport: str = "polling",
    review_id: str | None = None,
    required_role: str = "admin",
    approved: bool = False,
    decided_by: str = "unknown",
    role: str = "user",
    comment: str = "",
    signal: str = "human_correction",
    source_run_id: str = "run_preview",
) -> str:
    """Return sanitized Hybrid Agent Bridge planning output as JSON."""

    try:
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode == "phase_plan":
            return json.dumps(
                {
                    "success": True,
                    "phases": to_plain_dict(phase_plan()),
                    "safety_note": "Planning-only; no cloud service, on-prem runtime, or external network was contacted.",
                },
                ensure_ascii=False,
            )

        if normalized_mode == "mvp_schema":
            return json.dumps(
                {
                    "success": True,
                    "tables": to_plain_dict(mvp_table_specs()),
                    "safety_note": "Schema contract only; do not store secrets, raw PHI, or private vault dumps in cloud rows.",
                },
                ensure_ascii=False,
            )

        if normalized_mode == "worker_contract":
            return json.dumps(worker_contract(BridgeTransport(transport)), ensure_ascii=False)

        if normalized_mode == "review_decision":
            request = ReviewRequest(
                review_id=review_id or "review_preview",
                task_id=task_id or "task_preview",
                required_role=required_role,
                action_summary=task_text_summary or "sanitized action summary",
            )
            outcome = apply_review_decision(
                request,
                ReviewDecision(approved=bool(approved), decided_by=decided_by, role=role, comment=comment),
            )
            return json.dumps({"success": True, "request": to_plain_dict(request), "outcome": to_plain_dict(outcome)}, ensure_ascii=False)

        if normalized_mode == "propose_improvement":
            candidate = propose_improvement_candidate(
                tenant_id=tenant_id,
                source_run_id=source_run_id,
                signal=signal,
                summary=task_text_summary or "sanitized improvement signal",
            )
            return json.dumps({"success": True, "candidate": to_plain_dict(candidate)}, ensure_ascii=False)

        if normalized_mode == "plan_task":
            envelope = BridgeTaskEnvelope(
                task_id=task_id or "task_preview",
                app_id=app_id,
                tenant_id=tenant_id,
                requested_by=requested_by,
                task_text_summary=task_text_summary or "sanitized task summary",
                side_effect=BridgeSideEffect(side_effect),
                sensitivity=BridgeDataSensitivity(sensitivity),
                status=BridgeTaskStatus.PENDING,
                clinical_context=bool(clinical_context),
                external_egress=bool(external_egress),
                metadata=metadata or {},
            )
            decision = plan_task(envelope)
            return json.dumps(
                {
                    "success": True,
                    "task": to_plain_dict(envelope),
                    "decision": to_plain_dict(decision),
                    "safety_note": "Local planning only. Execute through review/tool gates before any write/send/delete.",
                },
                ensure_ascii=False,
            )

        raise ValueError("mode must be one of: phase_plan, mvp_schema, worker_contract, plan_task, review_decision, propose_improvement")
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


SINRIA_HYBRID_BRIDGE_SCHEMA = {
    "name": "sinria_hybrid_bridge",
    "description": (
        "Plan Sinria Hybrid Agent Bridge deployments where cloud apps provide shared UI/state "
        "and on-prem Sinria performs agent execution via outbound-only polling/realtime/queue/tunnel. "
        "Planning only; performs no network calls and returns no secrets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["phase_plan", "mvp_schema", "worker_contract", "plan_task", "review_decision", "propose_improvement"]},
            "task_id": {"type": "string"},
            "app_id": {"type": "string", "default": "chatops_crm"},
            "tenant_id": {"type": "string", "default": "medical_horizon"},
            "requested_by": {"type": "string"},
            "task_text_summary": {"type": "string", "description": "Sanitized task summary; do not include secrets or raw PHI."},
            "side_effect": {"type": "string", "enum": ["read", "draft", "write", "send", "delete"], "default": "draft"},
            "sensitivity": {"type": "string", "enum": ["public", "internal", "confidential", "patient"], "default": "internal"},
            "clinical_context": {"type": "boolean", "default": False},
            "external_egress": {"type": "boolean", "default": False},
            "metadata": {"type": "object", "description": "Sanitized metadata only."},
            "transport": {"type": "string", "enum": ["polling", "realtime", "queue", "secure_tunnel"], "default": "polling"},
            "review_id": {"type": "string"},
            "required_role": {"type": "string", "enum": ["user", "admin", "compliance", "physician"], "default": "admin"},
            "approved": {"type": "boolean", "default": False},
            "decided_by": {"type": "string"},
            "role": {"type": "string", "enum": ["user", "admin", "compliance", "physician"], "default": "user"},
            "comment": {"type": "string"},
            "signal": {"type": "string", "description": "Improvement signal such as human_correction, repeated_safe_block, connector_bug, or regression."},
            "source_run_id": {"type": "string"},
        },
        "required": ["mode"],
    },
}


registry.register(
    name="sinria_hybrid_bridge",
    toolset="sinria_integrations",
    schema=SINRIA_HYBRID_BRIDGE_SCHEMA,
    handler=lambda args, **kw: sinria_hybrid_bridge(
        mode=args.get("mode", ""),
        task_id=args.get("task_id"),
        app_id=args.get("app_id", "chatops_crm"),
        tenant_id=args.get("tenant_id", "medical_horizon"),
        requested_by=args.get("requested_by", "unknown"),
        task_text_summary=args.get("task_text_summary", ""),
        side_effect=args.get("side_effect", "draft"),
        sensitivity=args.get("sensitivity", "internal"),
        clinical_context=args.get("clinical_context", False),
        external_egress=args.get("external_egress", False),
        metadata=args.get("metadata"),
        transport=args.get("transport", "polling"),
        review_id=args.get("review_id"),
        required_role=args.get("required_role", "admin"),
        approved=args.get("approved", False),
        decided_by=args.get("decided_by", "unknown"),
        role=args.get("role", "user"),
        comment=args.get("comment", ""),
        signal=args.get("signal", "human_correction"),
        source_run_id=args.get("source_run_id", "run_preview"),
    ),
    check_fn=lambda: True,
    description="Local Sinria Hybrid Agent Bridge planner",
    emoji="🌉",
)
