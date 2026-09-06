"""Company OS adapter for metadata-only distributed Sinria team tasks."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from typing import Any, Callable, Mapping

from sinria_team_projects import (
    INPUT_REFERENCE_SCHEMES,
    TaskResult,
    TaskSpec,
    Worker,
    requires_approval,
    validate_safe_metadata,
)


_SCHEMA = "team-project.v1"
_TYPES = {"worker_heartbeat", "task_request", "task_response"}
_COMMON = {
    "schemaVersion",
    "type",
    "rawContextStored",
    "externalActionPerformed",
}
_HEARTBEAT_KEYS = _COMMON | {
    "memberId",
    "instanceId",
    "capabilities",
    "observedAt",
}
_REQUEST_KEYS = _COMMON | {
    "dispatchId",
    "projectId",
    "taskId",
    "capability",
    "summary",
    "operation",
    "scope",
    "reversible",
    "inputRefs",
    "acceptanceCriteria",
    "attempt",
    "approvalRef",
}
_RESPONSE_KEYS = _COMMON | {
    "dispatchId",
    "projectId",
    "taskId",
    "status",
    "summary",
    "evidence",
    "criteriaEvidence",
    "verdict",
}
_RESULT_REF_SCHEMES = ("run://", "local://", "artifact://", "company-knowledge://")


class RemoteWorkerUnavailable(RuntimeError):
    """Raised when no fresh remote worker advertises a capability."""


class RemoteTaskTimeout(RuntimeError):
    """Raised when a dispatched task has no response within a bounded wait."""


def _required_text(value: Mapping[str, Any], key: str, *, limit: int = 500) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text.strip() or len(text) > limit or "\n" in text:
        raise ValueError(f"invalid team project {key}")
    return text


def _refs(value: Any, schemes: tuple[str, ...], *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > 20:
        raise ValueError("invalid team project references")
    for ref in value:
        if not isinstance(ref, str) or len(ref) > 300 or not ref.startswith(schemes):
            raise ValueError("invalid team project reference")
    return value


def validate_team_project_metadata(value: Any) -> dict[str, Any] | None:
    """Validate the strict metadata contract carried in conversation events."""

    if value is None:
        return None
    if not isinstance(value, Mapping) or value.get("schemaVersion") != _SCHEMA:
        raise ValueError("unsupported team project metadata")
    kind = value.get("type")
    allowed = (
        _HEARTBEAT_KEYS
        if kind == "worker_heartbeat"
        else _REQUEST_KEYS
        if kind == "task_request"
        else _RESPONSE_KEYS
        if kind == "task_response"
        else set()
    )
    if kind not in _TYPES or set(value) != allowed:
        raise ValueError("unsupported team project metadata")
    if value.get("rawContextStored") is not False:
        raise ValueError("unsafe team project metadata boundary")
    if not isinstance(value.get("externalActionPerformed"), bool):
        raise ValueError("invalid team project action provenance")
    validate_safe_metadata(value)

    if kind == "worker_heartbeat":
        _required_text(value, "memberId", limit=120)
        _required_text(value, "instanceId", limit=120)
        capabilities = value.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or len(capabilities) > 30
            or any(not isinstance(item, str) or not item.strip() or len(item) > 80 for item in capabilities)
        ):
            raise ValueError("invalid team project capabilities")
        if not isinstance(value.get("observedAt"), (int, float)):
            raise ValueError("invalid team project heartbeat time")
        return dict(value)

    for key in ("dispatchId", "projectId", "taskId"):
        _required_text(value, key, limit=120)

    if kind == "task_request":
        _required_text(value, "capability", limit=80)
        _required_text(value, "summary")
        if not isinstance(value.get("attempt"), int) or value["attempt"] < 1:
            raise ValueError("invalid team project attempt")
        spec = TaskSpec(
            task_id=value["taskId"],
            summary=value["summary"],
            capability=value["capability"],
            operation=value["operation"],
            scope=value["scope"],
            reversible=value["reversible"],
            input_refs=_refs(value["inputRefs"], INPUT_REFERENCE_SCHEMES),
            acceptance_criteria=value["acceptanceCriteria"],
        ).validate()
        if asdict(spec)["acceptance_criteria"] != value["acceptanceCriteria"]:
            raise ValueError("invalid team project acceptance criteria")
        approval_ref = value["approvalRef"]
        if approval_ref is not None and (
            not isinstance(approval_ref, str)
            or not approval_ref.startswith("company-os://review/")
            or len(approval_ref) > 240
        ):
            raise ValueError("invalid team project approval reference")
        if requires_approval(spec) and approval_ref is None:
            raise PermissionError("remote gated task requires an approval proof")
        if value["externalActionPerformed"] is not False:
            raise ValueError("task request cannot report an external action")
        return dict(value)

    _required_text(value, "summary")
    if value.get("status") != "completed" or value.get("verdict") not in {
        "accepted",
        "revision_requested",
        "decision_required",
    }:
        raise ValueError("invalid team project response state")
    evidence = _refs(value.get("evidence"), _RESULT_REF_SCHEMES, allow_empty=False)
    criteria = value.get("criteriaEvidence")
    if not isinstance(criteria, Mapping) or not criteria:
        raise ValueError("invalid team project criteria evidence")
    _refs(list(criteria.values()), _RESULT_REF_SCHEMES, allow_empty=False)
    TaskResult(
        summary=value["summary"],
        evidence=evidence,
        criteria_evidence=dict(criteria),
        external_action_performed=value["externalActionPerformed"],
    ).validate()
    return dict(value)


class CompanyOsTeamProjectAdapter:
    """Discover remote workers and execute tasks over Company OS run leases."""

    def __init__(
        self,
        transport: Any,
        identity: Any,
        *,
        space_id: str,
        conversation_id: str,
        now: Callable[[], float] = time.time,
        heartbeat_ttl: float = 90,
        poll_interval: float = 2,
        max_wait: float = 180,
    ) -> None:
        if heartbeat_ttl <= 0 or poll_interval < 0 or max_wait < 0:
            raise ValueError("invalid distributed team timing")
        self.transport = transport
        self.identity = identity
        self.space_id = space_id
        self.conversation_id = conversation_id
        self.now = now
        self.heartbeat_ttl = heartbeat_ttl
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    @staticmethod
    def dispatch_id(project_id: str, task_id: str, attempt: int, key: str) -> str:
        digest = hashlib.sha256(
            f"sinria-team-dispatch:{project_id}:{task_id}:{attempt}:{key}".encode()
        ).hexdigest()[:24]
        return f"dispatch-{digest}"

    def _events(self) -> list[Mapping[str, Any]]:
        result = self.transport.list_conversation_events(
            self.identity, conversationId=self.conversation_id
        )
        rows = result.get("events")
        if not isinstance(rows, list):
            raise ValueError("Company OS event list is invalid")
        return [row for row in rows if isinstance(row, Mapping)]

    def publish_heartbeat(self, worker: Worker) -> dict[str, Any]:
        worker.validate()
        if (
            worker.member_id != self.identity.member_id
            or worker.instance_id != self.identity.instance_id
        ):
            raise PermissionError("worker heartbeat identity does not match transport identity")
        observed_at = int(self.now())
        metadata = validate_team_project_metadata(
            {
                "schemaVersion": _SCHEMA,
                "type": "worker_heartbeat",
                "memberId": worker.member_id,
                "instanceId": worker.instance_id,
                "capabilities": sorted(worker.capabilities),
                "observedAt": observed_at,
                "rawContextStored": False,
                "externalActionPerformed": False,
            }
        )
        result = self.transport.append_conversation_event(
            self.identity,
            spaceId=self.space_id,
            conversationId=self.conversation_id,
            kind="system_note",
            sanitizedPreview="Sinria worker heartbeat.",
            consultationMetadata=metadata,
            bodyRef=None,
            idempotencyKey=f"team-heartbeat:{worker.instance_id}:{observed_at}",
        )
        return result.get("event", result)

    def discover_workers(self) -> list[Worker]:
        latest: dict[str, dict[str, Any]] = {}
        for event in self._events():
            metadata = event.get("consultationMetadata")
            if not isinstance(metadata, Mapping) or metadata.get("schemaVersion") != _SCHEMA:
                continue
            try:
                parsed = validate_team_project_metadata(metadata)
            except ValueError:
                continue
            if not parsed or parsed["type"] != "worker_heartbeat":
                continue
            if (
                event.get("authorKind") != "sinria"
                or event.get("authorMemberId") != parsed["memberId"]
                or event.get("authorInstanceId") != parsed["instanceId"]
            ):
                continue
            current = latest.get(parsed["instanceId"])
            if current is None or parsed["observedAt"] > current["observedAt"]:
                latest[parsed["instanceId"]] = parsed
        cutoff = self.now() - self.heartbeat_ttl
        workers = [
            Worker(item["memberId"], item["instanceId"], set(item["capabilities"]), fresh=True)
            for item in latest.values()
            if item["observedAt"] >= cutoff
        ]
        return sorted(workers, key=lambda worker: (worker.member_id, worker.instance_id))

    def select_worker(self, capability: str) -> Worker:
        candidates = [worker for worker in self.discover_workers() if capability in worker.capabilities]
        if not candidates:
            raise RemoteWorkerUnavailable("no fresh remote worker for capability")
        return candidates[0]

    def _response(self, dispatch_id: str, worker: Worker) -> dict[str, Any] | None:
        for event in reversed(self._events()):
            metadata = event.get("consultationMetadata")
            if not isinstance(metadata, Mapping) or metadata.get("schemaVersion") != _SCHEMA:
                continue
            try:
                parsed = validate_team_project_metadata(metadata)
            except ValueError:
                continue
            if (
                parsed
                and parsed["type"] == "task_response"
                and parsed["dispatchId"] == dispatch_id
                and event.get("kind") == "assistant_message"
                and event.get("authorKind") == "sinria"
                and event.get("authorMemberId") == worker.member_id
                and event.get("authorInstanceId") == worker.instance_id
            ):
                return parsed
        return None

    def executor_for(self, project_id: str, *, approval_refs: Mapping[str, str] | None = None):
        approvals = dict(approval_refs or {})

        def execute(worker: Worker, task: TaskSpec, attempt: int, key: str) -> TaskResult:
            task.validate()
            worker.validate()
            approval_ref = approvals.get(task.task_id)
            if requires_approval(task) and approval_ref is None:
                raise PermissionError("remote gated task requires an approval proof")
            dispatch_id = self.dispatch_id(project_id, task.task_id, attempt, key)
            existing = self._response(dispatch_id, worker)
            if existing is None:
                metadata = validate_team_project_metadata(
                    {
                        "schemaVersion": _SCHEMA,
                        "type": "task_request",
                        "dispatchId": dispatch_id,
                        "projectId": project_id,
                        "taskId": task.task_id,
                        "capability": task.capability,
                        "summary": task.summary,
                        "operation": task.operation,
                        "scope": task.scope,
                        "reversible": task.reversible,
                        "inputRefs": list(task.input_refs),
                        "acceptanceCriteria": list(task.acceptance_criteria),
                        "attempt": attempt,
                        "approvalRef": approval_ref,
                        "rawContextStored": False,
                        "externalActionPerformed": False,
                    }
                )
                event_result = self.transport.append_conversation_event(
                    self.identity,
                    spaceId=self.space_id,
                    conversationId=self.conversation_id,
                    kind="user_message",
                    sanitizedPreview=f"Team project task {project_id}/{task.task_id} queued.",
                    consultationMetadata=metadata,
                    bodyRef=None,
                    idempotencyKey=f"{dispatch_id}:event",
                )
                event = event_result.get("event", event_result)
                self.transport.create_conversation_run(
                    self.identity,
                    spaceId=self.space_id,
                    conversationId=self.conversation_id,
                    triggeredByEventId=event["eventId"],
                    targetMemberId=worker.member_id,
                    targetInstanceId=worker.instance_id,
                    idempotencyKey=f"{dispatch_id}:run",
                )

            deadline = self.now() + self.max_wait
            response = existing
            while response is None:
                response = self._response(dispatch_id, worker)
                if response is not None or self.now() >= deadline or self.poll_interval <= 0:
                    break
                time.sleep(self.poll_interval)
            if response is None:
                raise RemoteTaskTimeout("remote team task response timed out")
            if response["externalActionPerformed"] and approval_ref is None:
                raise PermissionError("remote external action lacks an approval proof")
            return TaskResult(
                summary=response["summary"],
                evidence=list(response["evidence"]),
                criteria_evidence=dict(response["criteriaEvidence"]),
                external_action_performed=response["externalActionPerformed"],
            ).validate()

        return execute
