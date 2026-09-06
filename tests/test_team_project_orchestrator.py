import json
import tomllib
from pathlib import Path

import pytest

from sinria_team_projects import (
    JsonProjectStore,
    ProjectSpec,
    StaleProjectState,
    TaskResult,
    TaskSpec,
    TeamProjectOrchestrator,
    UnsafeProjectMetadata,
    Worker,
    validate_task_graph,
)


def test_team_project_runtime_is_included_in_the_installed_package():
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    assert "sinria_team_projects" in pyproject["tool"]["setuptools"]["py-modules"]


def test_project_store_rejects_a_stale_revision(tmp_path):
    store = JsonProjectStore(tmp_path / "projects.json")
    orchestrator = TeamProjectOrchestrator(store, workers=[], planner=lambda _: [])
    orchestrator.create_project(ProjectSpec("project-cas", "Plan", ["planned"]))
    first = store.load("project-cas")
    stale = store.load("project-cas")
    first["revision"] += 1
    store.save("project-cas", first, expected_revision=1)
    stale["revision"] += 1

    with pytest.raises(StaleProjectState):
        store.save("project-cas", stale, expected_revision=1)


def test_project_metadata_rejects_raw_or_secret_shaped_fields(tmp_path):
    store = JsonProjectStore(tmp_path / "projects.json")
    orchestrator = TeamProjectOrchestrator(store, workers=[], planner=lambda _: [])

    with pytest.raises(UnsafeProjectMetadata):
        orchestrator.create_project(
            ProjectSpec(
                project_id="project-",
                goal="Prepare an internal brief",
                acceptance_criteria=["brief-reviewed"],
                metadata={"rawContext": "do not persist this"},
            )
        )

    with pytest.raises(UnsafeProjectMetadata):
        TaskResult(
            summary="completed",
            evidence=["https://external.example/result"],
            criteria_evidence={"brief-reviewed": "run://task/1"},
        ).validate()

    with pytest.raises(UnsafeProjectMetadata):
        orchestrator.create_project(
            ProjectSpec(
                project_id="project-phi",
                goal="Review a clinical record with MRN: 12345678",
                acceptance_criteria=["reviewed"],
            )
        )


def test_task_graph_rejects_cycles_and_unknown_dependencies():
    with pytest.raises(ValueError, match="unknown dependency"):
        validate_task_graph(
            [TaskSpec("a", "A", "research", depends_on=["missing"])]
        )

    with pytest.raises(ValueError, match="cycle"):
        validate_task_graph(
            [
                TaskSpec("a", "A", "research", depends_on=["b"]),
                TaskSpec("b", "B", "writing", depends_on=["a"]),
            ]
        )


def test_missing_capable_worker_waits_instead_of_faking_completion(tmp_path):
    store = JsonProjectStore(tmp_path / "projects.json")
    orchestrator = TeamProjectOrchestrator(
        store,
        workers=[Worker("member-a", "inst-a", {"research"}, fresh=True)],
        planner=lambda _: [TaskSpec("build", "Build artifact", "engineering")],
        executors={},
    )
    orchestrator.create_project(ProjectSpec("project-worker", "Build", ["built"]))

    state = orchestrator.run_until_blocked("project-worker")

    assert state["status"] == "waiting_worker"
    assert state["tasks"]["build"]["status"] == "waiting_worker"
    assert state["tasks"]["build"]["assignedMemberId"] is None


def test_internal_company_knowledge_write_runs_without_human_approval(tmp_path):
    calls = []

    def execute(_worker, task, _attempt, _key):
        calls.append((task.operation, task.scope, task.input_refs))
        return TaskResult(
            "stored internally",
            ["company-knowledge://operating-knowledge/brief-1"],
            {"knowledge-stored": "company-knowledge://operating-knowledge/brief-1"},
        )

    orchestrator = TeamProjectOrchestrator(
        JsonProjectStore(tmp_path / "projects.json"),
        workers=[Worker("member-a", "inst-a", {"operations"})],
        planner=lambda _: [
            TaskSpec(
                "store",
                "Store confidential context internally",
                "operations",
                operation="write",
                scope="company_knowledge",
                input_refs=[
                    "local://approved-context/brief-1",
                    "vault://service-credential/company-knowledge",
                ],
                acceptance_criteria=["knowledge-stored"],
            )
        ],
        executors={"operations": execute},
    )
    orchestrator.create_project(
        ProjectSpec("project-internal-write", "Store internal context", ["knowledge-stored"])
    )

    completed = orchestrator.run_until_blocked("project-internal-write")

    assert completed["status"] == "completed"
    assert completed["tasks"]["store"]["approval"] is None
    assert calls == [
        (
            "write",
            "company_knowledge",
            [
                "local://approved-context/brief-1",
                "vault://service-credential/company-knowledge",
            ],
        )
    ]


@pytest.mark.parametrize(
    ("operation", "scope", "reversible"),
    [
        ("send", "external", True),
        ("delete", "local", False),
        ("billing", "external", True),
        ("auth", "local", True),
        ("permission", "company_knowledge", True),
        ("production", "production", True),
    ],
)
def test_only_egress_irreversible_and_privileged_operations_wait_for_approval(
    tmp_path, operation, scope, reversible
):
    orchestrator = TeamProjectOrchestrator(
        JsonProjectStore(tmp_path / "projects.json"),
        workers=[Worker("member-a", "inst-a", {"operations"})],
        planner=lambda _: [
            TaskSpec(
                "action",
                "Perform bounded action",
                "operations",
                operation=operation,
                scope=scope,
                reversible=reversible,
            )
        ],
        executors={},
    )
    orchestrator.create_project(ProjectSpec("project-gated", "Act", ["acted"]))

    blocked = orchestrator.run_until_blocked("project-gated")

    assert blocked["status"] == "waiting_approval"
    assert blocked["tasks"]["action"]["status"] == "waiting_approval"


def test_reversible_local_delete_is_autonomous(tmp_path):
    orchestrator = TeamProjectOrchestrator(
        JsonProjectStore(tmp_path / "projects.json"),
        workers=[Worker("member-a", "inst-a", {"operations"})],
        planner=lambda _: [
            TaskSpec(
                "archive",
                "Move scratch artifact to recoverable trash",
                "operations",
                operation="delete",
                scope="local",
                reversible=True,
                acceptance_criteria=["archived"],
            )
        ],
        executors={
            "operations": lambda *_: TaskResult(
                "archived",
                ["local://trash/artifact-1"],
                {"archived": "local://trash/artifact-1"},
            )
        },
    )
    orchestrator.create_project(ProjectSpec("project-archive", "Archive", ["archived"]))

    completed = orchestrator.run_until_blocked("project-archive")

    assert completed["status"] == "completed"
    assert completed["tasks"]["archive"]["approval"] is None


def test_external_public_read_is_autonomous_when_no_private_payload_is_sent(tmp_path):
    orchestrator = TeamProjectOrchestrator(
        JsonProjectStore(tmp_path / "projects.json"),
        workers=[Worker("member-a", "inst-a", {"research"})],
        planner=lambda _: [
            TaskSpec(
                "research",
                "Read a public source",
                "research",
                operation="read",
                scope="external",
                acceptance_criteria=["grounded"],
            )
        ],
        executors={
            "research": lambda *_: TaskResult(
                "grounded",
                ["artifact://public-source/1"],
                {"grounded": "artifact://public-source/1"},
            )
        },
    )
    orchestrator.create_project(ProjectSpec("project-public-read", "Research", ["grounded"]))

    completed = orchestrator.run_until_blocked("project-public-read")

    assert completed["status"] == "completed"


def test_project_runs_across_workers_revises_gates_and_recovers_after_restart(tmp_path):
    path = tmp_path / "projects.json"
    calls = []

    def planner(_):
        return [
            TaskSpec(
                "research",
                "Collect approved internal facts",
                "research",
                acceptance_criteria=["facts-grounded"],
            ),
            TaskSpec(
                "draft",
                "Draft the brief",
                "writing",
                depends_on=["research"],
                operation="draft",
                acceptance_criteria=["brief-drafted"],
            ),
            TaskSpec(
                "record",
                "Record the approved brief",
                "operations",
                depends_on=["draft"],
                operation="write",
                scope="external",
                acceptance_criteria=["brief-recorded"],
            ),
        ]

    def execute(worker, task, attempt, idempotency_key):
        calls.append((worker.member_id, task.task_id, attempt, idempotency_key))
        criterion = task.acceptance_criteria[0]
        return TaskResult(
            summary=f"{task.task_id} completed",
            evidence=[f"run://{task.task_id}/{attempt}"],
            criteria_evidence={criterion: f"run://{task.task_id}/{attempt}"},
            external_action_performed=task.operation == "write",
        )

    def review(task, result, attempt):
        if task.task_id == "draft" and attempt == 1:
            return "revision_requested"
        return "accepted"

    workers = [
        Worker("member-kikuchi", "inst-kikuchi", {"research"}, fresh=True),
        Worker("member-taro", "inst-taro", {"writing", "operations"}, fresh=True),
    ]
    first = TeamProjectOrchestrator(
        JsonProjectStore(path),
        workers=workers,
        planner=planner,
        executors={"research": execute, "writing": execute, "operations": execute},
        reviewer=review,
        max_revisions=2,
    )
    first.create_project(
        ProjectSpec(
            "project-001",
            "Produce an evidence-backed internal brief",
            ["facts-grounded", "brief-drafted", "brief-recorded"],
        )
    )

    blocked = first.run_until_blocked("project-001")
    assert blocked["status"] == "waiting_approval"
    assert blocked["tasks"]["research"]["status"] == "accepted"
    assert blocked["tasks"]["research"]["assignedMemberId"] == "member-kikuchi"
    assert blocked["tasks"]["draft"]["status"] == "accepted"
    assert blocked["tasks"]["draft"]["attempts"] == 2
    assert blocked["tasks"]["record"]["status"] == "waiting_approval"
    assert not any(task_id == "record" for _, task_id, _, _ in calls)

    restarted = TeamProjectOrchestrator(
        JsonProjectStore(path),
        workers=workers,
        planner=planner,
        executors={"research": execute, "writing": execute, "operations": execute},
        reviewer=review,
        max_revisions=2,
    )
    with pytest.raises(PermissionError):
        restarted.approve_task("project-001", "record", actor="member-taro")
    restarted.approve_task(
        "project-001",
        "record",
        actor="member-taro",
        human_confirmed=True,
    )
    completed = restarted.run_until_blocked("project-001")

    assert completed["status"] == "completed"
    assert completed["tasks"]["record"]["status"] == "accepted"
    assert completed["tasks"]["record"]["approval"]["actor"] == "member-taro"
    assert set(completed["criteriaEvidence"]) == {
        "facts-grounded",
        "brief-drafted",
        "brief-recorded",
    }
    assert sum(1 for _, task_id, _, _ in calls if task_id == "research") == 1
    assert sum(1 for _, task_id, _, _ in calls if task_id == "draft") == 2
    assert sum(1 for _, task_id, _, _ in calls if task_id == "record") == 1
    with pytest.raises(ValueError, match="not waiting for approval"):
        restarted.approve_task(
            "project-001",
            "record",
            actor="member-taro",
            human_confirmed=True,
        )
    keys = [key for *_, key in calls]
    assert len(keys) == len(set(keys))

    persisted = json.loads(path.read_text())
    serialized = json.dumps(persisted).lower()
    assert "rawcontext" not in serialized
    assert "rawprompt" not in serialized
    assert "patientdata" not in serialized
    assert "credentials" not in serialized


def test_recovery_reuses_the_running_attempt_idempotency_key(tmp_path):
    path = tmp_path / "projects.json"
    store = JsonProjectStore(path)
    calls = []
    orchestrator = TeamProjectOrchestrator(
        store,
        workers=[Worker("member-a", "inst-a", {"research"})],
        planner=lambda _: [
            TaskSpec("research", "Research", "research", acceptance_criteria=["grounded"])
        ],
        executors={
            "research": lambda worker, task, attempt, key: (
                calls.append((attempt, key))
                or TaskResult("done", ["run://research/1"], {"grounded": "run://research/1"})
            )
        },
    )
    orchestrator.create_project(ProjectSpec("project-recovery", "Research", ["grounded"]))
    state = store.load("project-recovery")
    state["tasks"]["research"].update(
        {
            "status": "running",
            "attempts": 1,
            "idempotencyKey": orchestrator._idempotency_key("project-recovery", "research", 1),
            "assignedMemberId": "member-a",
            "assignedInstanceId": "inst-a",
        }
    )
    store.save("project-recovery", state)

    completed = orchestrator.run_until_blocked("project-recovery")

    assert completed["status"] == "completed"
    assert completed["tasks"]["research"]["attempts"] == 1
    assert calls == [
        (1, orchestrator._idempotency_key("project-recovery", "research", 1))
    ]


def test_executor_failure_retries_with_sanitized_state(tmp_path):
    calls = []

    def execute(_worker, _task, attempt, key):
        calls.append((attempt, key))
        if attempt == 1:
            raise RuntimeError("credential at /private/location")
        return TaskResult(
            "done",
            ["run://retry/2"],
            {"grounded": "run://retry/2"},
        )

    orchestrator = TeamProjectOrchestrator(
        JsonProjectStore(tmp_path / "projects.json"),
        workers=[Worker("member-a", "inst-a", {"research"})],
        planner=lambda _: [
            TaskSpec(
                "research",
                "Research",
                "research",
                acceptance_criteria=["grounded"],
            )
        ],
        executors={"research": execute},
        max_execution_attempts=2,
    )
    orchestrator.create_project(
        ProjectSpec("project-retry", "Research", ["grounded"])
    )

    completed = orchestrator.run_until_blocked("project-retry")

    assert completed["status"] == "completed"
    assert completed["tasks"]["research"]["attempts"] == 2
    assert completed["tasks"]["research"]["lastError"] is None
    assert [attempt for attempt, _ in calls] == [1, 2]
    assert calls[0][1] != calls[1][1]
    assert "credential" not in json.dumps(completed).lower()
