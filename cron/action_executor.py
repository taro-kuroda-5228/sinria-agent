"""Durable execution and recovery for approved Sinria cron actions."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from cron.action_runtime import (
    CronAction,
    CronActionState,
    CronActionStore,
    InvalidTransition,
    StaleActionVersion,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionExecutionResult:
    success: bool
    output: str = ""
    error: str | None = None
    verification_evidence: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "verification_evidence": dict(self.verification_evidence),
        }

    @classmethod
    def from_payload(cls, value: dict[str, Any]) -> "ActionExecutionResult":
        return cls(
            success=bool(value.get("success")),
            output=str(value.get("output") or ""),
            error=str(value["error"]) if value.get("error") else None,
            verification_evidence=dict(value.get("verification_evidence") or {}),
        )


@dataclass(frozen=True)
class ActionVerificationResult:
    verified: bool
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {"verified": self.verified, "evidence": dict(self.evidence)}


ActionRunner = Callable[[CronAction], ActionExecutionResult]
ActionVerifier = Callable[[CronAction, ActionExecutionResult], ActionVerificationResult]


class CronActionExecutor:
    """Claim, execute, verify, and recover actions without duplicate execution."""

    def __init__(
        self,
        store: CronActionStore,
        *,
        runner: ActionRunner,
        verifier: ActionVerifier,
        owner_id: str | None = None,
        lease_ttl: float = 300,
    ) -> None:
        self.store = store
        self.runner = runner
        self.verifier = verifier
        self.owner_id = owner_id or f"cron-action-worker-{uuid.uuid4()}"
        self.lease_ttl = lease_ttl

    def execute(self, action_id: str, *, now: float | None = None) -> CronAction:
        action = self.store.get(action_id)
        if action.state in {
            CronActionState.COMPLETED,
            CronActionState.FAILED,
            CronActionState.REJECTED,
            CronActionState.EXPIRED,
            CronActionState.NEEDS_REVIEW,
        }:
            return action
        if action.state is CronActionState.VERIFYING:
            return self._verify(action, now=now)
        if action.state is not CronActionState.APPROVED:
            return action

        try:
            action = self.store.acquire_execution_lease(
                action_id,
                self.owner_id,
                ttl=self.lease_ttl,
                now=now,
            )
        except (InvalidTransition, StaleActionVersion):
            return self.store.get(action_id)

        try:
            execution = self.runner(action)
        except Exception as exc:
            logger.exception("Cron action %s execution failed", action_id)
            current = self.store.get(action_id)
            if current.state is CronActionState.EXECUTING:
                return self.store.release_execution_lease(
                    action_id,
                    self.owner_id,
                    current.lease_token,
                    outcome=CronActionState.FAILED,
                    now=now,
                )
            return current

        action = self.store.update_payload(
            action_id,
            expected_version=action.version,
            updates={"execution_result": execution.as_payload()},
            now=now,
        )
        if not execution.success:
            return self.store.release_execution_lease(
                action_id,
                self.owner_id,
                action.lease_token,
                outcome=CronActionState.FAILED,
                now=now,
            )

        action = self.store.release_execution_lease(
            action_id,
            self.owner_id,
            action.lease_token,
            outcome=CronActionState.VERIFYING,
            now=now,
        )
        return self._verify(action, execution=execution, now=now)

    def _verify(
        self,
        action: CronAction,
        *,
        execution: ActionExecutionResult | None = None,
        now: float | None = None,
    ) -> CronAction:
        if execution is None:
            stored = action.payload.get("execution_result")
            if not isinstance(stored, dict):
                return self.store.transition(
                    action.action_id,
                    CronActionState.NEEDS_REVIEW,
                    expected_version=action.version,
                    actor_id=self.owner_id,
                    now=now,
                )
            execution = ActionExecutionResult.from_payload(stored)

        try:
            verification = self.verifier(action, execution)
        except Exception as exc:
            logger.exception("Cron action %s verification failed", action.action_id)
            verification = ActionVerificationResult(
                verified=False,
                evidence={"reason": type(exc).__name__},
            )

        action = self.store.update_payload(
            action.action_id,
            expected_version=action.version,
            updates={"verification": verification.as_payload()},
            now=now,
        )
        target = (
            CronActionState.COMPLETED
            if verification.verified
            else CronActionState.NEEDS_REVIEW
        )
        return self.store.transition(
            action.action_id,
            target,
            expected_version=action.version,
            actor_id=self.owner_id,
            now=now,
        )

    def recover_pending(self, *, now: float | None = None) -> list[CronAction]:
        """Resume approved work and verification; never replay uncertain execution."""
        self.store.expire(now=now)
        pending = self.store.list_actions(
            states={CronActionState.APPROVED, CronActionState.VERIFYING}
        )
        return [self.execute(action.action_id, now=now) for action in pending]


def scheduler_resume_runner(action: CronAction) -> ActionExecutionResult:
    """Resume the exact source cron session through the scheduler's agent path."""
    from cron.jobs import load_jobs
    from cron.scheduler import run_job

    payload = action.payload
    job_id = str(payload.get("job_id") or "")
    session_id = str(payload.get("cron_session_id") or payload.get("run_id") or "")
    instruction = str(payload.get("resume_instruction") or "").strip()
    verification_marker = f"SINRIA_ACTION_VERIFIED:{action.action_id}"
    if not instruction:
        instruction = (
            "Continue the exact cron workflow that requested this human decision. "
            "Do not broaden the approved scope. Existing tool safety approvals still apply."
        )
    instruction = (
        f"{instruction}\n\nAfter acting, perform provider/system readback and include this exact "
        f"final line only when that readback succeeds: {verification_marker}"
    )
    job = next((candidate for candidate in load_jobs() if str(candidate.get("id")) == job_id), None)
    if job is None:
        snapshot = payload.get("job_snapshot")
        if isinstance(snapshot, dict) and str(snapshot.get("id") or "") == job_id:
            job = dict(snapshot)
    if job is None:
        return ActionExecutionResult(success=False, error=f"cron job not found: {job_id}")
    if not session_id:
        return ActionExecutionResult(success=False, error="source cron session is missing")

    success, _full_output, final_response, error = run_job(
        dict(job),
        resume_session_id=session_id,
        continuation_prompt=instruction,
        skip_delivery=True,
    )
    return ActionExecutionResult(
        success=success,
        output=final_response,
        error=error,
        verification_evidence={
            "source_session_id": session_id,
            "agent_completed": success,
            "verification_marker": verification_marker,
        },
    )


def scheduler_readback_verifier(
    action: CronAction,
    execution: ActionExecutionResult,
) -> ActionVerificationResult:
    """Require an explicit readback marker; otherwise route to human review."""
    marker = str(execution.verification_evidence.get("verification_marker") or "")
    final_line = next(
        (line.strip() for line in reversed(execution.output.splitlines()) if line.strip()),
        "",
    )
    verified = bool(marker) and execution.success and final_line == marker
    evidence = dict(execution.verification_evidence)
    evidence.update({"readback_marker": marker, "marker_present": final_line == marker})
    return ActionVerificationResult(verified=verified, evidence=evidence)
