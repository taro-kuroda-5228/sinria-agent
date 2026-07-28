"""Review-gated code self-improvement orchestration tool."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from agent.context_share.code_improvement import CodeImprovementStore
from hermes_constants import get_sinria_home
from tools.approval import request_gateway_approval
from tools.registry import registry, tool_error


def _store() -> CodeImprovementStore:
    home = get_sinria_home()
    config_path = home / "config.yaml"
    config: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            config = loaded
    section = config.get("self_improvement") or {}
    repositories = (
        section.get("allowed_repositories") or []
        if isinstance(section, dict)
        else []
    )
    return CodeImprovementStore(home=home, allowed_repositories=repositories)


def _public(proposal) -> dict[str, Any]:
    payload = asdict(proposal)
    # Repository/worktree paths and raw diff paths stay local.  The model only
    # needs stable refs and review state; path lookup is a separate local action.
    for key in ("repository", "worktree_path", "review_diff_path"):
        if payload.get(key):
            payload[key] = "[LOCAL_PATH]"
    return payload


def self_improvement_tool(args: dict[str, Any], **_: Any) -> Any:
    action = str(args.get("action") or "list")
    store = _store()
    try:
        if action == "list":
            return {"proposals": [_public(row) for row in store.list_proposals()]}
        if action == "status":
            return {"proposal": _public(store.get(str(args.get("proposal_id") or "")))}
        if action == "propose":
            proposal = store.propose(
                repository=str(args.get("repository") or ""),
                goal=str(args.get("goal") or ""),
                gap=str(args.get("gap") or ""),
                cause=str(args.get("cause") or ""),
                durable_fix=str(args.get("durable_fix") or ""),
                acceptance_criteria=list(args.get("acceptance_criteria") or []),
                evidence_ids=list(args.get("evidence_ids") or []),
            )
            return {"proposal": _public(proposal), "next_action": "request_execution_approval"}
        if action == "request_execution_approval":
            proposal_id = str(args.get("proposal_id") or "")
            proposal = store.get(proposal_id)
            decision = request_gateway_approval(
                f"Prepare isolated worktree for {proposal.proposal_id}",
                "Allow Sinria to create an isolated worktree and let a local worker edit it. No commit, merge, push, deploy, or external send is permitted.",
                pattern_key=f"self_improvement:execute:{proposal.proposal_id}",
                allow_session=False,
                allow_permanent=False,
                metadata={"proposal_id": proposal.proposal_id, "gate": "execution"},
            )
            if not decision.get("approved"):
                return decision
            approved = store.approve_execution(proposal_id, approved_by="human:gateway")
            return {"approved": True, "proposal": _public(approved), "next_action": "prepare_worktree"}
        if action == "prepare_worktree":
            proposal = store.prepare_worktree(str(args.get("proposal_id") or ""))
            # This is intentionally returned only to the local agent runtime.
            return {"proposal": asdict(proposal), "next_action": "run_local_worker_and_tests"}
        if action == "collect_review":
            proposal = store.collect_review(
                str(args.get("proposal_id") or ""),
                verification=list(args.get("verification") or []),
            )
            return {"proposal": _public(proposal), "next_action": "request_application_approval"}
        if action == "request_application_approval":
            proposal_id = str(args.get("proposal_id") or "")
            proposal = store.get(proposal_id)
            decision = request_gateway_approval(
                f"Approve reviewed change for {proposal.proposal_id} (diff {proposal.review_diff_sha256 or 'missing'})",
                "Approve this verified local diff for a separate commit/merge step. This approval does not itself commit, merge, push, deploy, or send anything.",
                pattern_key=f"self_improvement:apply:{proposal.proposal_id}:{proposal.review_diff_sha256 or 'missing'}",
                allow_session=False,
                allow_permanent=False,
                metadata={
                    "proposal_id": proposal.proposal_id,
                    "gate": "application",
                    "base_sha": proposal.base_sha,
                    "review_diff_sha256": proposal.review_diff_sha256,
                },
            )
            if not decision.get("approved"):
                return decision
            approved = store.approve_application(proposal_id, approved_by="human:gateway")
            return {"approved": True, "proposal": _public(approved), "next_action": "ask_for_explicit_commit_or_merge_approval"}
        return tool_error(f"unknown action: {action}")
    except (OSError, ValueError) as exc:
        return tool_error(str(exc))


SELF_IMPROVEMENT_SCHEMA = {
    "name": "self_improvement",
    "description": (
        "Create and manage review-gated code/config improvement proposals. "
        "Use for repeated practical failures or durable code_or_config fixes. "
        "Execution and application are separate human approval gates; this tool never commits, merges, pushes, deploys, or sends raw diffs externally."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "propose", "list", "status", "request_execution_approval",
                    "prepare_worktree", "collect_review", "request_application_approval",
                ],
            },
            "proposal_id": {"type": "string"},
            "repository": {"type": "string"},
            "goal": {"type": "string"},
            "gap": {"type": "string"},
            "cause": {"type": "string"},
            "durable_fix": {"type": "string"},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "verification": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["action"],
    },
}


registry.register(
    name="self_improvement",
    toolset="code_execution",
    schema=SELF_IMPROVEMENT_SCHEMA,
    handler=self_improvement_tool,
    emoji="🛠️",
)
