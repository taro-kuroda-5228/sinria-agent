from __future__ import annotations

import json

import pytest

from agent.company_context.policy import WorkspaceIdentity
from agent.company_context.state import LocalSyncState
from agent.company_context.transport import CompanyOsTransport, FakeHttp, TransportOffline


def make_transport(tmp_path, fake):
    return CompanyOsTransport(
        "https://company-os.invalid",
        identity=WorkspaceIdentity("workspace-a", "member-a", "instance-a"),
        bridge_token="test-only-token",
        state=LocalSyncState(tmp_path / "transport.json"),
        http=fake,
    )


def test_constructor_rejects_non_https_company_os_url(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        CompanyOsTransport(
            "http://company-os.invalid",
            identity=WorkspaceIdentity("workspace-a", "member-a", "instance-a"),
            bridge_token="test-only-credential",
            state=LocalSyncState(tmp_path / "state.json"),
            http=FakeHttp(),
        )


def test_metadata_transport_full_flow_and_readback(tmp_path):
    fake = FakeHttp()
    transport = make_transport(tmp_path, fake)
    created = transport.create(
        task_kind="knowledge_review",
        title="Review candidate",
        instruction="Review sanitized candidate metadata",
        agent_os_id="company-context",
        idempotency_key="task-key-a",
    )
    task_id = created["taskId"]
    claim = transport.claim(task_id, idempotency_key="claim-key-a", revision=1)
    attempt = claim["claim"]["attempt"]
    transport.renew(claim["claim"]["claimId"], idempotency_key="renew-key-a", revision=1)
    result = transport.result(
        task_id=task_id,
        agent_os_id="company-context",
        task_kind="knowledge_review",
        status="waiting_review",
        summary="Candidate ready for review",
        result_refs=["candidate:candidate-a"],
        idempotency_key="result-key-a",
        revision=1,
        claim_attempt=attempt,
    )
    assert result["ok"] is True
    assert transport.readback(task_id=task_id, idempotency_key="read-key-a")["ok"] is True
    assert transport.approval("review-a", approve=True, idempotency_key="approval-key-a", revision=1)["ok"] is True

    persisted = json.loads((tmp_path / "transport.json").read_text())
    serialized = json.dumps(persisted)
    assert "test-only-token" not in serialized
    assert "Review sanitized candidate metadata" not in serialized
    assert all(call[2]["workspaceId"] == "workspace-a" for call in fake.calls)
    assert all(call[2]["bridgeMemberId"] == "member-a" for call in fake.calls)
    assert all(call[2]["bridgeInstanceId"] == "instance-a" for call in fake.calls)


def test_lost_response_is_retryable_and_idempotent(tmp_path):
    fake = FakeHttp()
    transport = make_transport(tmp_path, fake)
    fake.fail_next = "lost_response"
    with pytest.raises(TransportOffline):
        transport.create(
            task_kind="knowledge_review",
            title="Review candidate",
            instruction="Sanitized metadata only",
            agent_os_id="company-context",
            idempotency_key="task-key-a",
        )
    retried = transport.create(
        task_kind="knowledge_review",
        title="Review candidate",
        instruction="Sanitized metadata only",
        agent_os_id="company-context",
        idempotency_key="task-key-a",
    )
    assert retried["taskId"] == "task-1"
    assert len(fake.tasks) == 1


def test_revoked_task_is_visible_on_readback(tmp_path):
    fake = FakeHttp()
    transport = make_transport(tmp_path, fake)
    task_id = transport.create(
        task_kind="knowledge_review",
        title="Review candidate",
        instruction="Sanitized metadata only",
        agent_os_id="company-context",
        idempotency_key="task-key-a",
    )["taskId"]
    fake.revoke(task_id)
    readback = transport.readback(task_id=task_id, idempotency_key="read-key-a")
    assert readback["tasks"][0]["status"] == "revoked"
