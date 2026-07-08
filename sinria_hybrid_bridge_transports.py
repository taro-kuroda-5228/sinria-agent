"""Outbound transport primitives for Sinria Hybrid Agent Bridge.

The initial implementation uses an in-memory store so the claim/result/review
protocol is testable without cloud credentials.  Real Supabase/Vercel/NATS
adapters should implement the same methods and keep the same outbound-only
security boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

from sinria_hybrid_bridge import (
    BridgeDataSensitivity,
    BridgeSideEffect,
    BridgeTaskEnvelope,
    BridgeTaskStatus,
    plan_task,
)
from sinria_hybrid_bridge_governance import ReviewRequest


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SAFE_METADATA_KEYS = frozenset(
    {
        "citation_ids",
        "risk_level",
        "stopped_at",
        "source_app",
        "workflow",
        "intent",
        "sanitized_summary",
    }
)


def _coerce_status(value: Any) -> BridgeTaskStatus:
    if isinstance(value, BridgeTaskStatus):
        return value
    return BridgeTaskStatus(str(value or BridgeTaskStatus.PENDING.value))


def _coerce_side_effect(value: Any) -> BridgeSideEffect:
    if isinstance(value, BridgeSideEffect):
        return value
    return BridgeSideEffect(str(value or BridgeSideEffect.DRAFT.value))


def _coerce_sensitivity(value: Any) -> BridgeDataSensitivity:
    if isinstance(value, BridgeDataSensitivity):
        return value
    return BridgeDataSensitivity(str(value or BridgeDataSensitivity.INTERNAL.value))


_PATIENT_ID_RE = re.compile(r"\bMRN-?\d+\b", re.IGNORECASE)
_CARD_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_POSTAL_CODE_RE = re.compile(r"\b\d{3}-\d{4}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+81[- ]?)?0?\d{1,3}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_JAPANESE_DEMO_NAME_RE = re.compile(r"山田[一-龥ぁ-んァ-ン]{1,4}")


def _redact_cloud_metadata_value(value: Any) -> Any:
    """Redact safe-key metadata values before they enter the local runner."""

    if isinstance(value, str):
        text = _PATIENT_ID_RE.sub("[REDACTED_ID]", value)
        text = _CARD_NUMBER_RE.sub("[REDACTED_CARD]", text)
        text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
        text = _POSTAL_CODE_RE.sub("[REDACTED_POSTAL]", text)
        return _JAPANESE_DEMO_NAME_RE.sub("[REDACTED_NAME]", text)
    if isinstance(value, list):
        return [_redact_cloud_metadata_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_cloud_metadata_value(item) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _redact_cloud_metadata_value(item) for key, item in value.items()}
    return value


def _sanitized_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    return {
        str(key): _redact_cloud_metadata_value(value)
        for key, value in metadata.items()
        if str(key) in _SAFE_METADATA_KEYS
    }


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce PostgREST JSON/string booleans without using truthiness."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "f", "0", "no", "n", "off", ""}:
            return False
    return default


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return _coerce_bool(value)


def bridge_task_from_postgrest_row(row: Mapping[str, Any]) -> BridgeTaskEnvelope:
    """Map a Supabase/PostgREST ``agent_tasks`` row into the local bridge envelope.

    The cloud row is intentionally treated as sanitized metadata only.  Known
    policy gate columns are preserved as tri-state overrides for ``plan_task``;
    free-form/raw payload keys in metadata are dropped so PHI/PII/secrets do not
    enter the on-prem runner contract through this adapter.
    """

    return BridgeTaskEnvelope(
        task_id=str(row["id"]),
        app_id=str(row["app_id"]),
        tenant_id=str(row["tenant_id"]),
        requested_by=str(row["requested_by"]),
        task_text_summary=str(_redact_cloud_metadata_value(row["task_text"])),
        side_effect=_coerce_side_effect(row.get("side_effect")),
        sensitivity=_coerce_sensitivity(row.get("sensitivity")),
        status=_coerce_status(row.get("status")),
        clinical_context=_coerce_bool(row.get("clinical_context", False)),
        external_egress=_coerce_bool(row.get("external_egress", False)),
        allowed_to_run_on_prem=_coerce_optional_bool(row.get("allowed_to_run_on_prem")),
        autonomous_execution_allowed=_coerce_optional_bool(row.get("autonomous_execution_allowed")),
        review_required=_coerce_optional_bool(row.get("review_required")),
        required_review_role=row.get("required_review_role"),
        metadata=_sanitized_metadata(row.get("metadata")),
    )


def postgrest_patch_for_status(
    *,
    status: BridgeTaskStatus | str,
    error_summary: str | None = None,
) -> dict[str, Any]:
    """Build a minimal status patch for a cloud ``agent_tasks`` row.

    The patch is intentionally small: it carries cloud-visible lifecycle/error
    metadata only and never echoes task text, results, or raw request payloads.
    """

    status_value = status.value if isinstance(status, BridgeTaskStatus) else str(status)
    patch: dict[str, Any] = {"status": status_value}
    if error_summary is not None:
        patch["error_summary"] = error_summary
    return patch


@dataclass(frozen=True)
class ClaimedTask:
    task: BridgeTaskEnvelope
    run_id: str
    attempt: int
    sinria_instance_id: str
    idempotency_key: str


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    task_id: str
    sinria_instance_id: str
    attempt: int
    status: str
    idempotency_key: str
    started_at: str
    completed_at: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class AgentResultRecord:
    result_id: str
    run_id: str
    task_id: str
    result_text: str
    requires_review: bool
    created_at: str


@dataclass
class InMemoryCloudEventStore:
    """Minimal cloud event layer simulator for tests and dry-run adapters."""

    tasks: dict[str, BridgeTaskEnvelope] = field(default_factory=dict)
    runs: list[AgentRunRecord] = field(default_factory=list)
    results: list[AgentResultRecord] = field(default_factory=list)
    review_requests: list[ReviewRequest] = field(default_factory=list)

    def add_task(self, task: BridgeTaskEnvelope) -> None:
        self.tasks[task.task_id] = task

    def request_cancel(self, task_id: str) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = BridgeTaskEnvelope(
            **{**task.__dict__, "status": BridgeTaskStatus.CANCEL_REQUESTED}
        )

    def claim_next_pending(self, *, sinria_instance_id: str) -> ClaimedTask | None:
        for task in self.tasks.values():
            if task.status != BridgeTaskStatus.PENDING:
                continue
            attempt = 1 + sum(1 for run in self.runs if run.task_id == task.task_id)
            run_id = f"run_{task.task_id}_{attempt}"
            key = f"{task.task_id}:{attempt}:{sinria_instance_id}"
            claimed_task = BridgeTaskEnvelope(**{**task.__dict__, "status": BridgeTaskStatus.CLAIMED})
            self.tasks[task.task_id] = claimed_task
            self.runs.append(
                AgentRunRecord(
                    run_id=run_id,
                    task_id=task.task_id,
                    sinria_instance_id=sinria_instance_id,
                    attempt=attempt,
                    status=BridgeTaskStatus.CLAIMED.value,
                    idempotency_key=key,
                    started_at=_utc_now_iso(),
                )
            )
            return ClaimedTask(claimed_task, run_id, attempt, sinria_instance_id, key)
        return None

    def next_cancel_requested(self, *, sinria_instance_id: str) -> ClaimedTask | None:
        for task in self.tasks.values():
            if task.status != BridgeTaskStatus.CANCEL_REQUESTED:
                continue
            attempt = 1 + sum(1 for run in self.runs if run.task_id == task.task_id)
            run_id = f"run_{task.task_id}_{attempt}"
            key = f"{task.task_id}:{attempt}:{sinria_instance_id}"
            return ClaimedTask(task, run_id, attempt, sinria_instance_id, key)
        return None

    def mark_task_status(self, task_id: str, status: BridgeTaskStatus) -> None:
        task = self.tasks[task_id]
        self.tasks[task_id] = BridgeTaskEnvelope(**{**task.__dict__, "status": status})

    def update_run_status(self, run_id: str, status: BridgeTaskStatus, error_summary: str | None = None) -> None:
        self.runs = [
            AgentRunRecord(
                run_id=run.run_id,
                task_id=run.task_id,
                sinria_instance_id=run.sinria_instance_id,
                attempt=run.attempt,
                status=status.value if run.run_id == run_id else run.status,
                idempotency_key=run.idempotency_key,
                started_at=run.started_at,
                completed_at=_utc_now_iso() if run.run_id == run_id and status in {BridgeTaskStatus.COMPLETED, BridgeTaskStatus.FAILED_RECOVERABLE, BridgeTaskStatus.CANCELLED} else run.completed_at,
                error_summary=error_summary if run.run_id == run_id else run.error_summary,
            )
            for run in self.runs
        ]

    def post_result(self, claimed: ClaimedTask, result_text: str, *, requires_review: bool = False) -> None:
        self.results.append(
            AgentResultRecord(
                result_id=f"result_{claimed.run_id}",
                run_id=claimed.run_id,
                task_id=claimed.task.task_id,
                result_text=result_text,
                requires_review=requires_review,
                created_at=_utc_now_iso(),
            )
        )
        self.mark_task_status(claimed.task.task_id, BridgeTaskStatus.COMPLETED)
        self.update_run_status(claimed.run_id, BridgeTaskStatus.COMPLETED)

    def create_review_request(self, claimed: ClaimedTask, required_role: str, reason: str) -> None:
        self.review_requests.append(
            ReviewRequest(
                review_id=f"review_{claimed.run_id}",
                task_id=claimed.task.task_id,
                required_role=required_role,
                action_summary=claimed.task.task_text_summary,
                reason=reason,
            )
        )
        self.mark_task_status(claimed.task.task_id, BridgeTaskStatus.WAITING_REVIEW)
        self.update_run_status(claimed.run_id, BridgeTaskStatus.WAITING_REVIEW)


@dataclass(frozen=True)
class PollingBridgeRunner:
    """One-iteration bridge runner used by cron/daemon adapters."""

    store: InMemoryCloudEventStore
    sinria_instance_id: str

    def run_once(self, processor: Callable[[BridgeTaskEnvelope], str]) -> str:
        cancel = self.store.next_cancel_requested(sinria_instance_id=self.sinria_instance_id)
        if cancel is not None:
            self.store.mark_task_status(cancel.task.task_id, BridgeTaskStatus.CANCELLED)
            return BridgeTaskStatus.CANCELLED.value

        claimed = self.store.claim_next_pending(sinria_instance_id=self.sinria_instance_id)
        if claimed is None:
            return "idle"

        decision = plan_task(claimed.task)
        if not decision.allowed_to_run_on_prem or decision.next_status == BridgeTaskStatus.FAILED_RECOVERABLE:
            self.store.mark_task_status(claimed.task.task_id, BridgeTaskStatus.FAILED_RECOVERABLE)
            self.store.update_run_status(
                claimed.run_id,
                BridgeTaskStatus.FAILED_RECOVERABLE,
                error_summary=decision.reason,
            )
            return BridgeTaskStatus.FAILED_RECOVERABLE.value
        if decision.review_required:
            self.store.create_review_request(
                claimed,
                required_role=decision.required_review_role or "admin",
                reason=decision.reason,
            )
            return BridgeTaskStatus.WAITING_REVIEW.value

        self.store.mark_task_status(claimed.task.task_id, BridgeTaskStatus.RUNNING)
        try:
            result_text = processor(claimed.task)
        except Exception as exc:
            self.store.mark_task_status(claimed.task.task_id, BridgeTaskStatus.FAILED_RECOVERABLE)
            self.store.update_run_status(
                claimed.run_id,
                BridgeTaskStatus.FAILED_RECOVERABLE,
                error_summary=str(_redact_cloud_metadata_value(str(exc))),
            )
            return BridgeTaskStatus.FAILED_RECOVERABLE.value
        self.store.post_result(claimed, result_text)
        return BridgeTaskStatus.COMPLETED.value
