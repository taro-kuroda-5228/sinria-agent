"""Safe automatic continuation decisions for incomplete gateway turns."""
from __future__ import annotations

from typing import Any

_CONTINUATION_PROMPT = (
    "Continue the same task in the same session from the durable conversation, todo, and "
    "filesystem state. Do not defer it merely because a tool failed: inspect the failure and "
    "use a safe alternative when available. Do not redo completed work. Inspect the actual "
    "state first, then finish the remaining steps and verification. Do not claim completion "
    "until the requested workflow is verified."
)

_RECEIPT_CONTINUATION_PROMPT = (
    "The previous turn produced a response but verification evidence show the requested "
    "workflow was not verified as done. Continue the same task in the same session from the "
    "durable conversation, todo, and filesystem state. Do not defer it merely because a tool "
    "failed: inspect the failure and use a safe alternative when available. Do not redo "
    "completed work. Run the missing action and verification, then capture a verification "
    "receipt before reporting completion."
)

_CARRYOVER_DETAILS = {
    "approval_required": {
        "stop_point": "required_action_not_authorized",
        "remaining_work": "retry_action_then_verify_workflow",
        "resume_condition": "explicit_approval_received",
    },
    "authorization_required": {
        "stop_point": "required_authorization_unavailable",
        "remaining_work": "authorize_then_retry_and_verify",
        "resume_condition": "required_authorization_available",
    },
    "external_dependency_unavailable": {
        "stop_point": "external_dependency_unavailable",
        "remaining_work": "retry_dependency_then_verify_workflow",
        "resume_condition": "external_dependency_recovered",
    },
    "safety_boundary": {
        "stop_point": "safety_policy_prohibits_action",
        "remaining_work": "human_decision_or_safe_alternative",
        "resume_condition": "approved_safe_path_available",
    },
}


def _receipt_reason(result: dict[str, Any]) -> dict[str, Any] | None:
    reason = result.get("completion_reason")
    return reason if isinstance(reason, dict) else None


def build_incomplete_task_receipt(
    result: dict[str, Any] | None, *, depth: int, max_depth: int
) -> dict[str, Any] | None:
    """Return a sanitized carryover receipt only for an actual fallback condition.

    Recoverable gaps cannot be carried over while same-session recovery budget
    remains. This prevents continuation from becoming an easy alternative to
    completing the initiating task.
    """
    if not isinstance(result, dict):
        return None
    if result.get("completed") is True or result.get("interrupted") is True:
        return None

    reason = _receipt_reason(result) or {}
    kind = str(reason.get("kind") or "")
    receipt_reason = str(reason.get("receipt_reason") or "")
    evidence_ids = [str(item) for item in (reason.get("evidence_ids") or []) if str(item)]

    if kind == "carryover" and receipt_reason in _CARRYOVER_DETAILS:
        return {
            "status": "incomplete_carryover",
            "reason": receipt_reason,
            **_CARRYOVER_DETAILS[receipt_reason],
            "evidence_ids": evidence_ids,
        }

    turn_exit_reason = str(result.get("turn_exit_reason") or "")
    recovery_needed = kind == "recoverable" or turn_exit_reason.startswith(
        "max_iterations_reached("
    )
    if recovery_needed and depth >= max_depth:
        return {
            "status": "incomplete_carryover",
            "reason": "same_session_recovery_exhausted",
            "stop_point": "bounded_recovery_limit_reached",
            "remaining_work": "inspect_state_retry_remaining_action_and_verify",
            "resume_condition": "new_execution_budget_available",
            "evidence_ids": evidence_ids,
        }
    return None


def apply_incomplete_task_receipt(
    result: dict[str, Any], *, depth: int, max_depth: int
) -> dict[str, Any] | None:
    """Attach the last-resort carryover state to durable and visible output."""
    receipt = build_incomplete_task_receipt(result, depth=depth, max_depth=max_depth)
    if receipt is None:
        return None

    result["incomplete_task"] = receipt
    marker = "未完了タスク（持ち越しfallback）"
    final_response = str(result.get("final_response") or "").rstrip()
    if marker not in final_response:
        report = (
            f"{marker}\n"
            f"理由: {receipt['reason']}\n"
            f"停止点: {receipt['stop_point']}\n"
            f"残作業: {receipt['remaining_work']}\n"
            f"再開条件: {receipt['resume_condition']}"
        )
        result["final_response"] = f"{final_response}\n\n{report}" if final_response else report
    return receipt


def build_budget_continuation(
    result: dict[str, Any] | None,
    *,
    depth: int,
    max_depth: int,
) -> str | None:
    """Return a follow-up prompt when the current gateway turn is incomplete.

    Two incomplete states are safe to auto-continue:
    1. the turn exhausted its iteration budget without completing; or
    2. receipt-backed practical completion says the workflow is recoverable in
       the same session (missing verification, a retryable block, or a failed
       tool that may have a safe alternative).

    Explicit carryover conditions (for example approval waiting) never busy
    retry. Interrupted, completed, and malformed results are also left alone.
    """
    if not isinstance(result, dict):
        return None
    if result.get("completed") is True or result.get("interrupted") is True:
        return None
    if depth >= max_depth:
        return None

    receipt_reason = _receipt_reason(result)
    if receipt_reason is not None and receipt_reason.get("kind") in {
        "terminal",
        "carryover",
    }:
        return None

    reason = str(result.get("turn_exit_reason") or "")
    if reason.startswith("max_iterations_reached("):
        return _CONTINUATION_PROMPT

    if receipt_reason is not None and receipt_reason.get("kind") == "recoverable":
        return _RECEIPT_CONTINUATION_PROMPT

    return None
