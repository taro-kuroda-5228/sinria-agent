#!/usr/bin/env python3
"""Offline end-to-end smoke for distributed Sinria team project transport."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sinria_peer_collaboration import PeerCollaborationRunner
from sinria_team_project_transport import CompanyOsTeamProjectAdapter
from sinria_team_projects import JsonProjectStore, ProjectSpec, TaskSpec, TeamProjectOrchestrator, Worker


class Identity:
    def __init__(self, member_id: str, instance_id: str):
        self.member_id = member_id
        self.instance_id = instance_id


class InMemoryCompanyOs:
    def __init__(self):
        self.events = []
        self.runs = []
        self.claim_lease_seconds = []
        self.sequence = 0
        self.processing = False

    def _event(self, identity, fields):
        self.sequence += 1
        event = {
            "eventId": f"event-{self.sequence}",
            "workspaceId": "workspace-1",
            "spaceId": fields["spaceId"],
            "conversationId": fields["conversationId"],
            "kind": fields["kind"],
            "authorKind": "sinria",
            "authorMemberId": identity.member_id,
            "authorInstanceId": identity.instance_id,
            "sanitizedPreview": fields["sanitizedPreview"],
            "consultationMetadata": fields.get("consultationMetadata"),
            "bodyRef": fields.get("bodyRef"),
            "seq": self.sequence,
        }
        self.events.append(event)
        return event

    def append_conversation_event(self, identity, **fields):
        existing = next(
            (
                event
                for event in self.events
                if event.get("idempotencyKey") == fields["idempotencyKey"]
            ),
            None,
        )
        if existing:
            return {"event": existing}
        event = self._event(identity, fields)
        event["idempotencyKey"] = fields["idempotencyKey"]
        return {"event": event}

    def list_conversation_events(self, _identity, **fields):
        return {
            "events": [
                event
                for event in self.events
                if event["conversationId"] == fields["conversationId"]
            ]
        }

    def create_conversation_run(self, identity, **fields):
        existing = next(
            (run for run in self.runs if run["idempotencyKey"] == fields["idempotencyKey"]),
            None,
        )
        if existing:
            return {"run": existing}
        run = {
            "runId": f"run-{len(self.runs) + 1}",
            "workspaceId": "workspace-1",
            "spaceId": fields["spaceId"],
            "conversationId": fields["conversationId"],
            "triggeredByEventId": fields["triggeredByEventId"],
            "sourceMemberId": identity.member_id,
            "targetMemberId": fields["targetMemberId"],
            "targetInstanceId": fields.get("targetInstanceId"),
            "status": "queued",
            "revision": 0,
            "humanRelayCount": 0,
            "idempotencyKey": fields["idempotencyKey"],
        }
        self.runs.append(run)
        if run["targetMemberId"] == "member-kikuchi" and not self.processing:
            self.processing = True
            try:
                self._run_kikuchi_once()
            finally:
                self.processing = False
        return {"run": run}

    def list_conversation_runs(self, identity, **fields):
        return {
            "runs": [
                dict(run)
                for run in self.runs
                if run["targetMemberId"] == fields.get("targetMemberId", identity.member_id)
                and (
                    run["targetInstanceId"] is None
                    or run["targetInstanceId"] == fields.get("targetInstanceId", identity.instance_id)
                )
            ]
        }

    def claim_conversation_run(self, identity, **fields):
        run = next(run for run in self.runs if run["runId"] == fields["runId"])
        run["status"] = "running"
        run["claimedByMemberId"] = identity.member_id
        run["claimedByInstanceId"] = identity.instance_id
        self.claim_lease_seconds.append(fields["leaseSeconds"])
        return {"run": dict(run)}

    def complete_conversation_run(self, _identity, **fields):
        run = next(run for run in self.runs if run["runId"] == fields["runId"])
        run["status"] = "completed"
        run["sanitizedStatusNote"] = fields["sanitizedStatusNote"]
        return {"run": dict(run)}

    def fail_conversation_run(self, _identity, **fields):
        run = next(run for run in self.runs if run["runId"] == fields["runId"])
        run["status"] = "failed_recoverable"
        return {"run": dict(run)}

    def sweep_conversation_runs(self, _identity, **_fields):
        return {"swept": 0, "runs": []}

    def _run_kikuchi_once(self):
        path = ROOT / "scripts" / "peer-consultation-executor.py"
        spec = importlib.util.spec_from_file_location("distributed_smoke_executor", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def capability_handler(metadata):
            criterion = metadata["acceptanceCriteria"][0]
            evidence = "company-knowledge://projects/project-transport/research"
            return {
                "summary": "Remote research completed",
                "evidence": [evidence],
                "criteriaEvidence": {criterion: evidence},
                "verdict": "accepted",
                "externalActionPerformed": False,
            }

        def execute(_run, event):
            return module.execute({"event": event}, team_executor=capability_handler)

        identity = Identity("member-kikuchi", "inst-kikuchi")
        runner = PeerCollaborationRunner(
            self,
            identity,
            target_member_id=identity.member_id,
            target_instance_id=identity.instance_id,
            executor=execute,
            validator=lambda *_: "accepted",
        )
        result = runner.run_once()
        assert result and result["status"] == "completed"


def main() -> int:
    transport = InMemoryCompanyOs()
    taro = Identity("member-taro", "inst-taro")
    adapter = CompanyOsTeamProjectAdapter(
        transport,
        taro,
        space_id="space-1",
        conversation_id="conversation-1",
        poll_interval=0,
        max_wait=1,
    )
    kikuchi_adapter = CompanyOsTeamProjectAdapter(
        transport,
        Identity("member-kikuchi", "inst-kikuchi"),
        space_id="space-1",
        conversation_id="conversation-1",
    )
    kikuchi_adapter.publish_heartbeat(
        Worker("member-kikuchi", "inst-kikuchi", {"research"})
    )
    workers = adapter.discover_workers()

    with tempfile.TemporaryDirectory(prefix="sinria-distributed-team-") as directory:
        orchestrator = TeamProjectOrchestrator(
            JsonProjectStore(Path(directory) / "projects.json"),
            workers=workers,
            planner=lambda _: [
                TaskSpec(
                    "research",
                    "Collect approved internal facts",
                    "research",
                    scope="company_knowledge",
                    input_refs=["company-knowledge://briefs/source-1"],
                    acceptance_criteria=["facts-grounded"],
                )
            ],
            executors={"research": adapter.executor_for("project-transport")},
        )
        orchestrator.create_project(
            ProjectSpec("project-transport", "Produce a grounded internal brief", ["facts-grounded"])
        )
        state = orchestrator.run_until_blocked("project-transport")

    task = state["tasks"]["research"]
    payload = {
        "status": state["status"],
        "assignedMemberId": task["assignedMemberId"],
        "assignedInstanceId": task["assignedInstanceId"],
        "requestRuns": sum(1 for run in transport.runs if run["targetMemberId"] == "member-kikuchi"),
        "leaseSeconds": max(transport.claim_lease_seconds),
        "rawContextStored": False,
        "externalActionPerformed": task["result"]["external_action_performed"],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
