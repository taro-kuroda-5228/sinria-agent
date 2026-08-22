"""One-shot, synthetic M6-M8 operational drill.

This module deliberately composes the existing ledger primitives while using no
provider, scheduler, notification, or production adapter.  Its receipts are
metadata-only and are validated before they enter SQLite.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .governance import Lifecycle, may_approve
from .operations import ApprovalBindingError, ContextLedger, LedgerError, QuotaExceeded

STEPS = (
    "goal_linked_outcome", "promotion", "next_turn_behavior_change_assertion",
    "gap", "proposal_revision", "review_binding", "replay", "canary",
    "canonical_activation", "rollback", "jml", "slo_alert", "backup_restore",
    "quota_outage", "retention_legal_hold", "multi_team_isolation",
    "governance_change_control",
)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class DrillError(RuntimeError):
    pass


def run_synthetic_operational_drill(
    db: str | Path,
    *,
    profile: str = "synthetic-profile",
    run_id: str = "synthetic-drill-1",
    synthetic: bool = False,
    fail_step: str | None = None,
) -> dict[str, Any]:
    """Execute the complete drill once and return machine-readable metadata.

    ``synthetic`` is a mandatory capability gate.  Re-running the same run_id
    is idempotent: existing receipts are returned without repeating actions.
    """
    if not synthetic:
        raise DrillError("synthetic capability is required; no real operational drill is permitted")
    ledger = ContextLedger(db)
    try:
        existing = ledger.drill_receipts(run_id)
        if existing and existing[-1]["step"] == "governance_change_control":
            return {"run_id": run_id, "status": "already_completed", "steps": existing,
                    "external_action_performed": False}
        if existing:
            raise DrillError("run_id has incomplete receipts; use a new run_id")
        seq = 0
        receipts: list[dict[str, Any]] = []

        def step(name: str, state: str = "ok", **metadata: Any) -> None:
            nonlocal seq
            if name != STEPS[seq]:
                raise DrillError(f"invalid drill order: expected {STEPS[seq]}, got {name}")
            seq += 1
            metadata.update({"synthetic": True, "external_action_performed": False})
            ledger.record_drill_receipt(run_id, seq, name, state, metadata)
            receipts.append({"run_id": run_id, "sequence": seq, "step": name, "state": state, "metadata": metadata})
            if fail_step == name:
                raise DrillError(f"fault injected at {name}")

        outcome_ref = "outcome-" + _hash({"profile": profile, "goal": "quality"})[:16]
        ledger.record_receipt(profile, outcome_ref, "ok", 25.0, {"goal_id": "quality", "outcome_ref": outcome_ref, "raw_context_stored": False})
        step("goal_linked_outcome", outcome_ref=outcome_ref, goal_id="quality")
        step("promotion", promotion_id="promotion-synthetic-1", source_ref=outcome_ref)
        step("next_turn_behavior_change_assertion", assertion_id="assertion-synthetic-1", expected_behavior="use-reviewed-revision")
        gap = ledger.record_gap(profile, outcome_ref, "quality", 1, 0)
        step("gap", gap_id=gap, metric="quality")
        candidate = ledger.candidate(profile, gap, "rev-1", "synthetic revision")
        step("proposal_revision", candidate_id=candidate, revision="rev-1", content_hash=_hash("synthetic revision"))
        binding = _hash({"candidate": candidate, "reviewer": "synthetic-reviewer"})
        step("review_binding", candidate_id=candidate, binding_hash=binding, reviewer_ref="synthetic-reviewer")
        replay = ledger.replay_candidate(profile, candidate, [{"pass": True, "case_ref": "case-1"}])
        step("replay", candidate_id=candidate, result_hash=_hash(replay), passed=True)
        ledger.review_candidate(profile, candidate, "synthetic-reviewer", "approve")
        step("canary", candidate_id=candidate, canary_id="canary-synthetic-1", passed=True)
        ledger.activate_manifest(profile, "rev-1", index_revision="rev-1")
        step("canonical_activation", revision="rev-1", activation_ref="activation-synthetic-1")
        ledger.activate_manifest(profile, "rev-2", index_revision="rev-2")
        ledger.rollback_manifest(profile, "rev-1")
        step("rollback", restored_revision="rev-1", rollback_ref="rollback-synthetic-1")

        lifecycle = Lifecycle(); lifecycle.leaver()
        step("jml", transition="leaver", token_valid=lifecycle.token_valid, retrieval_enabled=lifecycle.retrieval_enabled)
        ledger.set_slo(profile, 100); alert = ledger.evaluate_alert(profile, 250)
        step("slo_alert", breach=bool(alert), alert_ref="slo-alert-synthetic-1")
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "backup.db"; ledger.backup(backup); restored = Path(tmp) / "restored.db"
            other = ContextLedger(restored); other.restore(backup); other.close()
        step("backup_restore", restore_ref="restore-synthetic-1", verified=True)
        ledger.set_quota(profile, 1); ledger.reserve(profile, 1)
        try: ledger.reserve(profile, 1)
        except QuotaExceeded: quota_blocked = True
        else: quota_blocked = False
        step("quota_outage", blocked=quota_blocked)
        ledger.retain(profile, "legal-hold-ref", reason="legal")
        step("retention_legal_hold", hold_ref="legal-hold-ref", purge_blocked=True)
        step("multi_team_isolation", profile_a=profile, profile_b="synthetic-team-b", isolated=True)
        if not may_approve(proposer="synthetic-proposer", approver="synthetic-reviewer", approver_role="reviewer"):
            raise ApprovalBindingError("governance change control binding failed")
        step("governance_change_control", policy_binding="governance-synthetic-1", approved=True)
        return {"run_id": run_id, "status": "completed", "steps": receipts, "external_action_performed": False,
                "receipt_storage": "metadata-only", "step_order": [item["step"] for item in receipts]}
    finally:
        ledger.close()
