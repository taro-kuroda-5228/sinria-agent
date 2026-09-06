import json

import pytest

from sinria_consultation import validate_consultation
from sinria_team_project_transport import (
    CompanyOsTeamProjectAdapter,
    RemoteTaskTimeout,
    RemoteWorkerUnavailable,
    validate_team_project_metadata,
)
from sinria_team_projects import TaskSpec, Worker


class Identity:
    member_id = "member-taro"
    instance_id = "inst-taro"


class FakeTransport:
    def __init__(self, events=None, *, auto_respond=False):
        self.events = list(events or [])
        self.auto_respond = auto_respond
        self.runs = []
        self.append_calls = []
        self.create_calls = []
        self._event_number = len(self.events)

    def list_conversation_events(self, _identity, **_fields):
        return {"events": list(self.events)}

    def append_conversation_event(self, identity, **fields):
        self.append_calls.append(fields)
        self._event_number += 1
        event = {
            "eventId": f"event-{self._event_number}",
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
            "seq": self._event_number,
        }
        self.events.append(event)
        return {"event": event}

    def create_conversation_run(self, _identity, **fields):
        self.create_calls.append(fields)
        existing = next(
            (run for run in self.runs if run["idempotencyKey"] == fields["idempotencyKey"]),
            None,
        )
        if existing:
            return {"run": existing}
        run = {
            "runId": f"run-{len(self.runs) + 1}",
            "idempotencyKey": fields["idempotencyKey"],
            **fields,
        }
        self.runs.append(run)
        if self.auto_respond:
            request = next(
                event for event in self.events
                if event["eventId"] == fields["triggeredByEventId"]
            )
            metadata = request["consultationMetadata"]
            self.events.append(response(
                metadata["dispatchId"],
                project_id=metadata["projectId"],
                task_id=metadata["taskId"],
                criterion=metadata["acceptanceCriteria"][0],
                external=metadata["scope"] == "external",
                author_member=fields["targetMemberId"],
                author_instance=fields["targetInstanceId"],
            ))
        return {"run": run}


def heartbeat(member, instance, capabilities, observed_at, seq):
    return {
        "eventId": f"heartbeat-{seq}",
        "workspaceId": "workspace-1",
        "spaceId": "space-1",
        "conversationId": "conversation-1",
        "kind": "system_note",
        "authorKind": "sinria",
        "authorMemberId": member,
        "authorInstanceId": instance,
        "sanitizedPreview": "Sinria worker heartbeat.",
        "consultationMetadata": {
            "schemaVersion": "team-project.v1",
            "type": "worker_heartbeat",
            "memberId": member,
            "instanceId": instance,
            "capabilities": capabilities,
            "observedAt": observed_at,
            "rawContextStored": False,
            "externalActionPerformed": False,
        },
        "bodyRef": None,
        "seq": seq,
    }


def response(
    dispatch_id,
    *,
    project_id="project-1",
    task_id="research",
    criterion="facts-grounded",
    external=False,
    author_member="member-kikuchi",
    author_instance="inst-kikuchi",
):
    evidence = f"company-knowledge://projects/{project_id}/{task_id}"
    return {
        "eventId": "response-1",
        "workspaceId": "workspace-1",
        "spaceId": "space-1",
        "conversationId": "conversation-1",
        "kind": "assistant_message",
        "authorKind": "sinria",
        "authorMemberId": author_member,
        "authorInstanceId": author_instance,
        "sanitizedPreview": "Team project task completed.",
        "consultationMetadata": {
            "schemaVersion": "team-project.v1",
            "type": "task_response",
            "dispatchId": dispatch_id,
            "projectId": project_id,
            "taskId": task_id,
            "status": "completed",
            "summary": "Research completed",
            "evidence": [evidence],
            "criteriaEvidence": {criterion: evidence},
            "verdict": "accepted",
            "rawContextStored": False,
            "externalActionPerformed": external,
        },
        "bodyRef": None,
        "seq": 20,
    }


def test_team_project_metadata_is_strict_and_accepted_by_collaboration_validator():
    request = {
        "schemaVersion": "team-project.v1",
        "type": "task_request",
        "dispatchId": "dispatch-1",
        "projectId": "project-1",
        "taskId": "research",
        "capability": "research",
        "summary": "Collect approved internal facts",
        "operation": "read",
        "scope": "company_knowledge",
        "reversible": False,
        "inputRefs": ["company-knowledge://briefs/source-1"],
        "acceptanceCriteria": ["facts-grounded"],
        "attempt": 1,
        "approvalRef": None,
        "rawContextStored": False,
        "externalActionPerformed": False,
    }

    assert validate_team_project_metadata(request) == request
    assert validate_consultation(request) == request

    unsafe = dict(request, rawContext="must not cross")
    with pytest.raises(ValueError, match="unsupported team project metadata"):
        validate_team_project_metadata(unsafe)


def test_worker_heartbeats_drive_capability_and_freshness_assignment():
    transport = FakeTransport(
        [
            heartbeat("member-old", "inst-old", ["research"], 800, 1),
            heartbeat("member-kikuchi", "inst-kikuchi", ["research"], 995, 2),
            heartbeat("member-taro", "inst-taro", ["writing"], 998, 3),
        ]
    )
    adapter = CompanyOsTeamProjectAdapter(
        transport,
        Identity(),
        space_id="space-1",
        conversation_id="conversation-1",
        now=lambda: 1000,
        heartbeat_ttl=60,
    )

    workers = adapter.discover_workers()

    assert workers == [
        Worker("member-kikuchi", "inst-kikuchi", {"research"}, fresh=True),
        Worker("member-taro", "inst-taro", {"writing"}, fresh=True),
    ]


def test_worker_discovery_rejects_heartbeat_identity_spoofing():
    spoofed = heartbeat("member-kikuchi", "inst-kikuchi", ["research"], 995, 1)
    spoofed["authorMemberId"] = "member-other"
    adapter = CompanyOsTeamProjectAdapter(
        FakeTransport([spoofed]),
        Identity(),
        space_id="space-1",
        conversation_id="conversation-1",
        now=lambda: 1000,
    )

    assert adapter.discover_workers() == []


def test_remote_execution_is_idempotent_and_returns_metadata_only_task_result():
    transport = FakeTransport(
        [heartbeat("member-kikuchi", "inst-kikuchi", ["research"], 995, 1)],
        auto_respond=True,
    )
    adapter = CompanyOsTeamProjectAdapter(
        transport,
        Identity(),
        space_id="space-1",
        conversation_id="conversation-1",
        now=lambda: 1000,
        heartbeat_ttl=60,
        poll_interval=0,
        max_wait=1,
    )
    task = TaskSpec(
        "research",
        "Collect approved internal facts",
        "research",
        scope="company_knowledge",
        input_refs=["company-knowledge://briefs/source-1"],
        acceptance_criteria=["facts-grounded"],
    )
    key = "a" * 64
    executor = adapter.executor_for("project-1")
    first = executor(adapter.discover_workers()[0], task, 1, key)
    second = executor(adapter.discover_workers()[0], task, 1, key)

    assert first == second
    assert first.summary == "Research completed"
    assert first.evidence == ["company-knowledge://projects/project-1/research"]
    assert first.external_action_performed is False
    assert len(transport.runs) == 1
    assert transport.create_calls[0]["targetMemberId"] == "member-kikuchi"
    serialized = json.dumps(transport.append_calls)
    for forbidden in ('"rawContext":', '"rawPrompt":', '"credentials":', '"patientData":'):
        assert forbidden not in serialized


def test_remote_execution_refuses_missing_fresh_worker_and_unapproved_egress():
    adapter = CompanyOsTeamProjectAdapter(
        FakeTransport(),
        Identity(),
        space_id="space-1",
        conversation_id="conversation-1",
        now=lambda: 1000,
        poll_interval=0,
        max_wait=0,
    )
    with pytest.raises(RemoteWorkerUnavailable):
        adapter.select_worker("research")

    gated = TaskSpec(
        "send",
        "Send externally",
        "operations",
        operation="send",
        scope="external",
    )
    with pytest.raises(PermissionError, match="approval proof"):
        adapter.executor_for("project-1")(
            Worker("member-a", "inst-a", {"operations"}), gated, 1, "b" * 64
        )


def test_remote_execution_rejects_a_response_from_the_wrong_worker():
    transport = FakeTransport(
        [heartbeat("member-kikuchi", "inst-kikuchi", ["research"], 995, 1)]
    )
    adapter = CompanyOsTeamProjectAdapter(
        transport,
        Identity(),
        space_id="space-1",
        conversation_id="conversation-1",
        now=lambda: 1000,
        poll_interval=0,
        max_wait=0,
    )
    task = TaskSpec(
        "research",
        "Collect approved internal facts",
        "research",
        acceptance_criteria=["facts-grounded"],
    )
    key = "d" * 64
    forged = response(adapter.dispatch_id("project-1", "research", 1, key))
    forged["authorMemberId"] = "member-other"
    forged["authorInstanceId"] = "inst-other"
    transport.events.append(forged)

    with pytest.raises(RemoteTaskTimeout, match="timed out"):
        adapter.executor_for("project-1")(
            adapter.select_worker("research"), task, 1, key
        )


def test_remote_egress_runs_only_with_a_typed_company_os_approval_reference():
    transport = FakeTransport(
        [heartbeat("member-a", "inst-a", ["operations"], 995, 1)],
        auto_respond=True,
    )
    adapter = CompanyOsTeamProjectAdapter(
        transport,
        Identity(),
        space_id="space-1",
        conversation_id="conversation-1",
        now=lambda: 1000,
        poll_interval=0,
        max_wait=1,
    )
    gated = TaskSpec(
        "send",
        "Send approved output externally",
        "operations",
        operation="send",
        scope="external",
        acceptance_criteria=["sent"],
    )

    result = adapter.executor_for(
        "project-1",
        approval_refs={"send": "company-os://review/review-1"},
    )(adapter.select_worker("operations"), gated, 1, "c" * 64)

    assert result.external_action_performed is True
    request = transport.append_calls[0]["consultationMetadata"]
    assert request["approvalRef"] == "company-os://review/review-1"
