from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.company_context import (
    CompanyOsKnowledgeClient,
    KillSwitch,
    ReceiptLedger,
    ScopePolicy,
    TeamSourceClient,
    TransportOutcomeUnknown,
    WorkspaceIdentity,
    WorkspaceSource,
    apply_remote_reviews,
    apply_review_readback,
    candidate_payloads,
    sync_review_queue,
    validate_metadata_only,
)
from agent.correction_loop.evidence import ContextEvidence
from agent.correction_loop.extraction import EvidenceCandidate


def _identity() -> WorkspaceIdentity:
    return WorkspaceIdentity("medical-horizon", "member-taro", "instance-mac")


def _candidate() -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id="ctx-candidate-safe",
        evidence=ContextEvidence(
            evidence_id="ctx-ev-safe",
            source_session_id="session-local-pointer",
            source_kind="user_correction",
            scope="org",
            summary="Review-gated metadata-only organizational learning is required.",
            sanitized_sample="organizational learning boundary",
            sensitivity="internal",
            applies_to=["company_os", "correction_loop"],
            valid_from="2026-08-18T00:00:00Z",
            confidence=0.92,
            human_approved=False,
        ),
    )


def test_policy_is_identity_bound_and_kill_switch_is_fail_closed():
    policy = ScopePolicy("medical-horizon", frozenset({"knowledge.propose"}))
    policy.authorize(_identity(), "knowledge.propose")
    with pytest.raises(PermissionError):
        policy.authorize(WorkspaceIdentity("other", "member-taro", "instance-mac"), "knowledge.propose")
    with pytest.raises(PermissionError):
        policy.authorize(_identity(), "knowledge.promote")
    with pytest.raises(PermissionError):
        KillSwitch(enabled=False, reason="incident").require_enabled()


def test_metadata_boundary_rejects_raw_or_sensitive_fields():
    validate_metadata_only({"rawContextStored": False, "sanitizedSummary": "safe metadata"})
    for payload in (
        {"rawContextStored": True},
        {"body": "raw message"},
        {"subject": "private subject"},
        {"recipient": "person@example.com"},
        {"accessToken": "secret"},
    ):
        with pytest.raises(ValueError):
            validate_metadata_only(payload)


def test_candidate_mapping_is_deterministic_and_metadata_only():
    first = candidate_payloads(_candidate(), _identity())
    second = candidate_payloads(_candidate(), _identity())
    assert first == second
    observation, candidate = first
    assert observation["rawSourceStored"] is False
    assert candidate["rawEvidenceStored"] is False
    assert candidate["humanApprovalRequired"] is True
    assert candidate["idempotencyKey"].startswith("sinria-org-")
    encoded = json.dumps(first)
    assert "session-local-pointer" not in encoded
    assert _candidate().evidence.summary not in encoded
    assert _candidate().evidence.sanitized_sample not in encoded


def test_client_dry_run_has_no_transport_side_effect(tmp_path: Path):
    calls = []
    client = CompanyOsKnowledgeClient(
        identity=_identity(),
        policy=ScopePolicy("medical-horizon", frozenset({"knowledge.propose"})),
        kill_switch=KillSwitch(),
        ledger=ReceiptLedger(tmp_path / "receipts.json"),
        transport=lambda *_: calls.append(True),
    )
    result = client.propose(_candidate(), dry_run=True)
    assert result.status == "dry_run"
    assert calls == []


def test_success_receipt_is_idempotent(tmp_path: Path):
    calls = []

    def transport(payload):
        calls.append(payload)
        return {"ok": True, "asset": {"assetId": "ka-1"}}

    client = CompanyOsKnowledgeClient(
        identity=_identity(),
        policy=ScopePolicy("medical-horizon", frozenset({"knowledge.propose"})),
        kill_switch=KillSwitch(),
        ledger=ReceiptLedger(tmp_path / "receipts.json"),
        transport=transport,
    )
    assert client.propose(_candidate()).status == "confirmed"
    assert client.propose(_candidate()).status == "confirmed"
    assert len(calls) == 2  # observation + candidate only once


def test_success_without_remote_asset_id_is_not_confirmed(tmp_path: Path):
    responses = iter([
        {"ok": True, "observation": {"observationId": "obs-1"}},
        {"ok": True, "candidate": {}},
    ])
    ledger = ReceiptLedger(tmp_path / "receipts.json")
    client = CompanyOsKnowledgeClient(
        identity=_identity(),
        policy=ScopePolicy("medical-horizon", frozenset({"knowledge.propose"})),
        kill_switch=KillSwitch(),
        ledger=ledger,
        transport=lambda _payload: next(responses),
    )

    with pytest.raises(RuntimeError, match="missing assetId"):
        client.propose(_candidate())
    key = str(candidate_payloads(_candidate(), _identity())[1]["idempotencyKey"])
    assert ledger.get(key) is None


def test_unknown_outcome_blocks_automatic_retry(tmp_path: Path):
    def transport(_payload):
        raise TimeoutError("outcome unknown")

    client = CompanyOsKnowledgeClient(
        identity=_identity(),
        policy=ScopePolicy("medical-horizon", frozenset({"knowledge.propose"})),
        kill_switch=KillSwitch(),
        ledger=ReceiptLedger(tmp_path / "receipts.json"),
        transport=transport,
    )
    with pytest.raises(TransportOutcomeUnknown):
        client.propose(_candidate())
    with pytest.raises(TransportOutcomeUnknown, match="retry blocked"):
        client.propose(_candidate())


def test_review_readback_updates_local_queue_only_after_human_decision(tmp_path: Path):
    from agent.correction_loop.review_queue import write_review_candidates, load_review_candidates

    queue = tmp_path / "review.jsonl"
    write_review_candidates([_candidate()], path=queue)
    apply_review_readback(
        candidate_id=_candidate().candidate_id,
        remote_asset={
            "status": "validated",
            "reviewedByMemberId": "human-reviewer",
            "reviewedAt": "2026-08-18T01:00:00Z",
            "externalActionPerformed": False,
            "rawEvidenceStored": False,
        },
        queue_path=queue,
    )
    [updated] = load_review_candidates(path=queue)
    assert updated.approval_state == "approved"
    assert updated.approved_at == "2026-08-18T01:00:00Z"


def test_review_readback_rejects_automatic_or_unsafe_promotion(tmp_path: Path):
    from agent.correction_loop.review_queue import write_review_candidates

    queue = tmp_path / "review.jsonl"
    write_review_candidates([_candidate()], path=queue)
    with pytest.raises(ValueError, match="human reviewer"):
        apply_review_readback(
            candidate_id=_candidate().candidate_id,
            remote_asset={"status": "validated", "reviewedByMemberId": None, "externalActionPerformed": False},
            queue_path=queue,
        )
    with pytest.raises(ValueError, match="external action"):
        apply_review_readback(
            candidate_id=_candidate().candidate_id,
            remote_asset={"status": "validated", "reviewedByMemberId": "human", "externalActionPerformed": True},
            queue_path=queue,
        )


def test_end_to_end_queue_sync_and_review_readback(tmp_path: Path):
    from agent.correction_loop.review_queue import load_review_candidates, write_review_candidates

    queue = tmp_path / "review.jsonl"
    ledger = ReceiptLedger(tmp_path / "receipts.json")
    write_review_candidates([_candidate()], path=queue)
    remote_assets = []

    def transport(payload):
        if payload["kind"] == "observation":
            return {"ok": True, "observation": {"observationId": "obs-1"}}
        candidate = {**payload, "assetId": "ka-1", "status": "candidate"}
        remote_assets.append(candidate)
        # Match the canonical Company OS POST /api/knowledge-assets response.
        return {"ok": True, "candidate": candidate}

    client = CompanyOsKnowledgeClient(
        identity=_identity(),
        policy=ScopePolicy("medical-horizon", frozenset({"knowledge.propose"})),
        kill_switch=KillSwitch(),
        ledger=ledger,
        transport=transport,
    )
    results = sync_review_queue(queue, client)
    assert [result.status for result in results] == ["confirmed"]
    remote_assets[0].update({
        "status": "validated",
        "reviewedByMemberId": "member-reviewer",
        "reviewedAt": "2026-08-18T02:00:00Z",
        "externalActionPerformed": False,
        "rawEvidenceStored": False,
    })
    assert apply_remote_reviews(queue, ledger, remote_assets) == [_candidate().candidate_id]
    [approved] = load_review_candidates(path=queue)
    assert approved.approval_state == "approved"
    assert approved.evidence.human_approved is True


def test_team_source_registration_hashes_provider_id_locally():
    calls = []
    client = TeamSourceClient(
        identity=_identity(),
        policy=ScopePolicy("medical-horizon", frozenset({"source.register"})),
        kill_switch=KillSwitch(),
        transport=lambda payload: calls.append(payload) or {"ok": True, "source": {"sourceId": "src-safe"}},
    )
    result = client.register(WorkspaceSource("google_drive", "drive_folder", "team_source", "raw-drive-id"))
    assert result["ok"] is True
    assert "raw-drive-id" not in json.dumps(calls)
    assert calls[0]["resourceFingerprint"].startswith("sha256:")
    with pytest.raises(ValueError, match="member-private"):
        client.register(WorkspaceSource("gmail", "mail_thread", "team_source", "raw-thread-id"))
