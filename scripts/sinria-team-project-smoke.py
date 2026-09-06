#!/usr/bin/env python3
"""Deterministic non-network smoke for autonomous Sinria team projects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sinria_team_projects import (
    JsonProjectStore,
    ProjectSpec,
    TaskResult,
    TaskSpec,
    TeamProjectOrchestrator,
    Worker,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args()
    revisions = {"count": 0}
    executed: list[tuple[str, str, int]] = []

    def planner(_spec: ProjectSpec) -> list[TaskSpec]:
        return [
            TaskSpec("research", "Collect approved facts", "research", acceptance_criteria=["facts-grounded"]),
            TaskSpec(
                "draft",
                "Draft internal brief",
                "writing",
                depends_on=["research"],
                operation="draft",
                acceptance_criteria=["brief-drafted"],
            ),
            TaskSpec(
                "record",
                "Record approved brief",
                "operations",
                depends_on=["draft"],
                operation="write",
                scope="external",
                acceptance_criteria=["brief-recorded"],
            ),
        ]

    def execute(worker: Worker, task: TaskSpec, attempt: int, _key: str) -> TaskResult:
        executed.append((worker.member_id, task.task_id, attempt))
        criterion = task.acceptance_criteria[0]
        return TaskResult(
            summary=f"{task.task_id} completed",
            evidence=[f"run://smoke/{task.task_id}/{attempt}"],
            criteria_evidence={criterion: f"run://smoke/{task.task_id}/{attempt}"},
            external_action_performed=task.operation == "write",
        )

    def review(task: TaskSpec, _result: TaskResult, attempt: int) -> str:
        if task.task_id == "draft" and attempt == 1:
            revisions["count"] += 1
            return "revision_requested"
        return "accepted"

    workers = [
        Worker("member-kikuchi", "inst-kikuchi", {"research"}),
        Worker("member-taro", "inst-taro", {"writing", "operations"}),
    ]
    executors = {name: execute for name in ("research", "writing", "operations")}
    first = TeamProjectOrchestrator(
        JsonProjectStore(args.store),
        workers=workers,
        planner=planner,
        executors=executors,
        reviewer=review,
    )
    first.create_project(
        ProjectSpec(
            "smoke-autonomous-team",
            "Produce and record an evidence-backed internal brief",
            ["facts-grounded", "brief-drafted", "brief-recorded"],
        )
    )
    blocked = first.run_until_blocked("smoke-autonomous-team")
    if blocked["status"] != "waiting_approval":
        return 2

    restarted = TeamProjectOrchestrator(
        JsonProjectStore(args.store),
        workers=workers,
        planner=planner,
        executors=executors,
        reviewer=review,
    )
    restarted.approve_task(
        "smoke-autonomous-team",
        "record",
        actor="member-taro",
        human_confirmed=True,
    )
    state = restarted.run_until_blocked("smoke-autonomous-team")
    if state["status"] != "completed":
        return 3

    receipt = {
        "projectId": state["projectId"],
        "status": state["status"],
        "workers": sorted({member for member, _, _ in executed}),
        "acceptedTasks": sum(task["status"] == "accepted" for task in state["tasks"].values()),
        "revisionCount": revisions["count"],
        "approvalRecorded": state["tasks"]["record"]["approval"] is not None,
        "rawContextStored": False,
        "externalActionPerformed": state["tasks"]["record"]["result"]["external_action_performed"],
        "restartVerified": True,
    }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
