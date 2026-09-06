"""Local-first, metadata-only orchestration for Sinria team projects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


AUTO_OPERATIONS = {"read", "draft"}
GATED_OPERATIONS = {
    "write",
    "send",
    "delete",
    "billing",
    "auth",
    "permission",
    "production",
    "clinical_patient_data",
}
VERDICTS = {"accepted", "revision_requested", "decision_required"}
EVIDENCE_SCHEMES = ("run://", "local://", "artifact://")
FORBIDDEN_KEYS = {
    "body",
    "rawbody",
    "rawcontext",
    "rawprompt",
    "prompt",
    "credentials",
    "credential",
    "password",
    "token",
    "secret",
    "phi",
    "patientdata",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bmrn\s*[:#]", re.IGNORECASE),
    re.compile(r"\bpatient\s*(?:id|name)\s*[:#]", re.IGNORECASE),
)


class UnsafeProjectMetadata(ValueError):
    """Raised when control-plane state contains raw or sensitive material."""


class StaleProjectState(RuntimeError):
    """Raised when a writer attempts to overwrite a newer project revision."""


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def validate_safe_metadata(value: Any) -> None:
    """Reject raw-body/secret-shaped control-plane fields recursively."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalized_key(key) in FORBIDDEN_KEYS:
                raise UnsafeProjectMetadata(f"forbidden metadata field: {key}")
            validate_safe_metadata(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            validate_safe_metadata(child)
    elif isinstance(value, str):
        lowered = value.lower()
        if "password=" in lowered or "token=" in lowered or "secret=" in lowered:
            raise UnsafeProjectMetadata("secret-shaped metadata value")
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
            raise UnsafeProjectMetadata("patient-identifier-shaped metadata value")


def _validate_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class ProjectSpec:
    project_id: str
    goal: str
    acceptance_criteria: list[str]
    sensitivity: str = "internal"
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "ProjectSpec":
        _validate_identifier(self.project_id, "project_id")
        if not self.goal.strip() or not self.acceptance_criteria:
            raise ValueError("project goal and acceptance criteria are required")
        if len(set(self.acceptance_criteria)) != len(self.acceptance_criteria):
            raise ValueError("project acceptance criteria must be unique")
        validate_safe_metadata(asdict(self))
        return self


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    summary: str
    capability: str
    depends_on: list[str] = field(default_factory=list)
    operation: str = "read"
    acceptance_criteria: list[str] = field(default_factory=list)

    def validate(self) -> "TaskSpec":
        _validate_identifier(self.task_id, "task_id")
        if not self.summary.strip() or not self.capability.strip():
            raise ValueError("task summary and capability are required")
        if self.operation not in AUTO_OPERATIONS | GATED_OPERATIONS:
            raise ValueError("unsupported task operation")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("duplicate dependency")
        validate_safe_metadata(asdict(self))
        return self


@dataclass(frozen=True)
class Worker:
    member_id: str
    instance_id: str
    capabilities: set[str]
    fresh: bool = True

    def validate(self) -> "Worker":
        _validate_identifier(self.member_id, "member_id")
        _validate_identifier(self.instance_id, "instance_id")
        if not self.capabilities:
            raise ValueError("worker capabilities are required")
        validate_safe_metadata(asdict(self))
        return self


@dataclass(frozen=True)
class TaskResult:
    summary: str
    evidence: list[str]
    criteria_evidence: dict[str, str]
    external_action_performed: bool = False

    def validate(self) -> "TaskResult":
        if not self.summary.strip() or not self.evidence:
            raise ValueError("result summary and evidence are required")
        for ref in [*self.evidence, *self.criteria_evidence.values()]:
            if not isinstance(ref, str) or not ref.startswith(EVIDENCE_SCHEMES):
                raise UnsafeProjectMetadata("evidence must use a local metadata reference")
        validate_safe_metadata(asdict(self))
        return self


def validate_task_graph(tasks: Iterable[TaskSpec]) -> list[TaskSpec]:
    result = [task.validate() for task in tasks]
    ids = [task.task_id for task in result]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task id")
    known = set(ids)
    for task in result:
        missing = set(task.depends_on) - known
        if missing:
            raise ValueError(f"unknown dependency: {sorted(missing)[0]}")

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {task.task_id: task for task in result}

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError("task graph contains a cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id].depends_on:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in ids:
        visit(task_id)
    return result


class JsonProjectStore:
    """Small atomic durable store containing metadata only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schemaVersion": "sinria.team-projects.v1", "projects": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        validate_safe_metadata(data)
        if data.get("schemaVersion") != "sinria.team-projects.v1":
            raise ValueError("unsupported project store schema")
        return data

    def load(self, project_id: str) -> dict[str, Any]:
        data = self._read_all()
        try:
            return json.loads(json.dumps(data["projects"][project_id]))
        except KeyError as exc:
            raise KeyError(f"unknown project: {project_id}") from exc

    def save(
        self,
        project_id: str,
        state: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> None:
        validate_safe_metadata(state)
        data = self._read_all()
        current = data["projects"].get(project_id)
        actual_revision = 0 if current is None else current.get("revision")
        if expected_revision is not None and actual_revision != expected_revision:
            raise StaleProjectState(
                f"stale project revision: expected {expected_revision}, found {actual_revision}"
            )
        data["projects"][project_id] = state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


Executor = Callable[[Worker, TaskSpec, int, str], TaskResult]
Planner = Callable[[ProjectSpec], Iterable[TaskSpec]]
Reviewer = Callable[[TaskSpec, TaskResult, int], str]


class TeamProjectOrchestrator:
    """Drive a bounded project until completion or a recoverable gate."""

    def __init__(
        self,
        store: JsonProjectStore,
        *,
        workers: Iterable[Worker],
        planner: Planner,
        executors: Mapping[str, Executor] | None = None,
        reviewer: Reviewer | None = None,
        max_revisions: int = 2,
        max_execution_attempts: int = 3,
    ):
        if max_revisions < 0 or max_execution_attempts < 1:
            raise ValueError("invalid attempt limits")
        self.store = store
        self.workers = [worker.validate() for worker in workers]
        self.planner = planner
        self.executors = dict(executors or {})
        self.reviewer = reviewer or (lambda task, result, attempt: "accepted")
        self.max_revisions = max_revisions
        self.max_execution_attempts = max_execution_attempts

    def _persist(self, state: dict[str, Any]) -> None:
        previous_revision = state["revision"]
        state["revision"] = previous_revision + 1
        self.store.save(
            state["projectId"],
            state,
            expected_revision=previous_revision,
        )

    def create_project(self, spec: ProjectSpec) -> dict[str, Any]:
        spec.validate()
        tasks = validate_task_graph(self.planner(spec))
        task_state = {
            task.task_id: {
                "spec": asdict(task),
                "status": "pending",
                "attempts": 0,
                "assignedMemberId": None,
                "assignedInstanceId": None,
                "idempotencyKey": None,
                "result": None,
                "lastError": None,
                "approval": None,
            }
            for task in tasks
        }
        state = {
            "schemaVersion": "sinria.team-project.v1",
            "projectId": spec.project_id,
            "goal": spec.goal,
            "acceptanceCriteria": list(spec.acceptance_criteria),
            "criteriaEvidence": {},
            "sensitivity": spec.sensitivity,
            "metadata": spec.metadata,
            "status": "planned" if tasks else "decision_required",
            "revision": 1,
            "tasks": task_state,
        }
        self.store.save(spec.project_id, state, expected_revision=0)
        return state

    def approve_task(
        self,
        project_id: str,
        task_id: str,
        *,
        actor: str,
        human_confirmed: bool = False,
    ) -> dict[str, Any]:
        if human_confirmed is not True:
            raise PermissionError("explicit human confirmation is required")
        _validate_identifier(actor, "approval actor")
        state = self.store.load(project_id)
        task = state["tasks"].get(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        spec = TaskSpec(**task["spec"])
        if spec.operation not in GATED_OPERATIONS:
            raise ValueError("task does not require approval")
        if task["status"] != "waiting_approval":
            raise ValueError("task is not waiting for approval")
        task["approval"] = {"actor": actor, "operation": spec.operation}
        task["status"] = "pending"
        self._persist(state)
        return state

    @staticmethod
    def _idempotency_key(project_id: str, task_id: str, attempt: int) -> str:
        raw = f"sinria-team-project:{project_id}:{task_id}:{attempt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _select_worker(self, capability: str, state: dict[str, Any]) -> Worker | None:
        candidates = [
            worker for worker in self.workers
            if worker.fresh and capability in worker.capabilities
        ]
        if not candidates:
            return None
        counts = {
            worker.instance_id: sum(
                1
                for task in state["tasks"].values()
                if task.get("assignedInstanceId") == worker.instance_id
            )
            for worker in candidates
        }
        return min(candidates, key=lambda worker: (counts[worker.instance_id], worker.member_id, worker.instance_id))

    def _recover(self, state: dict[str, Any]) -> bool:
        changed = False
        for task in state["tasks"].values():
            if task["status"] == "running":
                task["status"] = "pending"
                task["resumeAttempt"] = True
                changed = True
        return changed

    def run_until_blocked(self, project_id: str) -> dict[str, Any]:
        state = self.store.load(project_id)
        if self._recover(state):
            self._persist(state)

        while True:
            progressed = False
            for task_id, entry in state["tasks"].items():
                if entry["status"] in {"accepted", "decision_required"}:
                    continue
                spec = TaskSpec(**entry["spec"])
                dependency_states = [state["tasks"][dep]["status"] for dep in spec.depends_on]
                if any(status == "decision_required" for status in dependency_states):
                    entry["status"] = "decision_required"
                    progressed = True
                    continue
                if any(status != "accepted" for status in dependency_states):
                    if entry["status"] not in {"waiting_approval", "waiting_worker"}:
                        entry["status"] = "pending"
                    continue
                if spec.operation in GATED_OPERATIONS and entry["approval"] is None:
                    if entry["status"] != "waiting_approval":
                        entry["status"] = "waiting_approval"
                        progressed = True
                    continue
                worker = self._select_worker(spec.capability, state)
                executor = self.executors.get(spec.capability)
                if worker is None or executor is None:
                    if entry["status"] != "waiting_worker":
                        entry["status"] = "waiting_worker"
                        progressed = True
                    continue

                resume_attempt = bool(entry.pop("resumeAttempt", False))
                attempt = entry["attempts"] if resume_attempt else entry["attempts"] + 1
                key = entry.get("idempotencyKey") if resume_attempt else None
                if not key:
                    key = self._idempotency_key(project_id, task_id, attempt)
                entry.update(
                    {
                        "status": "running",
                        "attempts": attempt,
                        "assignedMemberId": worker.member_id,
                        "assignedInstanceId": worker.instance_id,
                        "idempotencyKey": key,
                    }
                )
                state["status"] = "running"
                self._persist(state)

                try:
                    result = executor(worker, spec, attempt, key).validate()
                except Exception:
                    entry["lastError"] = "peer execution failed"
                    entry["status"] = (
                        "pending"
                        if attempt < self.max_execution_attempts
                        else "decision_required"
                    )
                    self._persist(state)
                    progressed = True
                    continue
                entry["lastError"] = None
                if result.external_action_performed and entry["approval"] is None:
                    raise UnsafeProjectMetadata("external action occurred without approval")
                verdict = self.reviewer(spec, result, attempt)
                if verdict not in VERDICTS:
                    raise ValueError("invalid reviewer verdict")
                entry["result"] = asdict(result)
                if verdict == "accepted":
                    entry["status"] = "accepted"
                    state["criteriaEvidence"].update(result.criteria_evidence)
                elif verdict == "revision_requested" and attempt <= self.max_revisions:
                    entry["status"] = "pending"
                else:
                    entry["status"] = "decision_required"
                self._persist(state)
                progressed = True

            statuses = [task["status"] for task in state["tasks"].values()]
            missing_criteria = set(state["acceptanceCriteria"]) - set(state["criteriaEvidence"])
            if statuses and all(status == "accepted" for status in statuses):
                state["status"] = "completed" if not missing_criteria else "decision_required"
                self._persist(state)
                return state
            if any(status == "decision_required" for status in statuses):
                state["status"] = "decision_required"
            elif any(status == "waiting_approval" for status in statuses):
                state["status"] = "waiting_approval"
            elif any(status == "waiting_worker" for status in statuses):
                state["status"] = "waiting_worker"
            else:
                state["status"] = "running"
            self._persist(state)
            if not progressed or state["status"] in {"waiting_approval", "waiting_worker", "decision_required"}:
                return state
