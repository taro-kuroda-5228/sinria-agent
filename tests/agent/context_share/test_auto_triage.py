"""Tests for review-queue dedup, auto-triage compaction, and backlog metrics.

Design: docs/plans/2026-07-03-context-share-backlog-auto-triage.md
"""

import json
from pathlib import Path

from agent.context_share.auto_triage import classify_auto_approval, run_auto_triage
from agent.context_share.evidence import ContextEvidence
from agent.context_share.extraction import EvidenceCandidate
from agent.context_share.loop_metrics import compute_loop_status
from agent.context_share.outcome_gap import record_practical_outcome_and_candidates
from agent.context_share.review_queue import (
    append_candidate_deduplicated,
    approve_candidate,
    dedup_key,
    load_review_candidates,
    write_review_candidates,
)


def _candidate(
    cid: str,
    *,
    summary: str = "Practical-completion gap detected: apply the durable-fix loop before claiming done.",
    sample: str = "goal_kind=practical_action; actual_kind=incomplete_or_blocked; cause_kind=execution_incomplete",
    scope: str = "project",
    sensitivity: str = "internal",
    source_kind: str = "workflow_outcome",
    applies_to: list[str] | None = None,
    approval_state: str = "proposed",
    occurrence_count: int = 1,
    session: str = "session-safe-a",
    valid_from: str = "2026-07-01T00:00:00Z",
    extraction_reason: str = "goal_actual_gap_practical_completion_loop",
) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=cid,
        evidence=ContextEvidence(
            evidence_id=cid.replace("ctx-candidate-", "ctx-ev-"),
            source_session_id=session,
            source_kind=source_kind,
            scope=scope,
            summary=summary,
            sanitized_sample=sample,
            sensitivity=sensitivity,
            applies_to=applies_to or ["self_improvement", "practical_completion"],
            valid_from=valid_from,
            confidence=0.88,
            human_approved=approval_state == "approved",
        ),
        approval_state=approval_state,
        occurrence_count=occurrence_count,
        extraction_reason=extraction_reason,
    )


def test_dedup_key_ignores_session_and_time_but_keeps_class():
    same_class_a = _candidate("ctx-candidate-aaa", session="session-safe-a", valid_from="2026-07-01T00:00:00Z")
    same_class_b = _candidate("ctx-candidate-bbb", session="session-safe-b", valid_from="2026-07-02T09:30:00Z")
    other_class = _candidate(
        "ctx-candidate-ccc",
        sample="goal_kind=practical_action; actual_kind=failed_or_interrupted; cause_kind=interrupted_or_failed",
    )

    assert dedup_key(same_class_a) == dedup_key(same_class_b)
    assert dedup_key(same_class_a) != dedup_key(other_class)


def test_append_candidate_deduplicated_merges_same_class(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    first = _candidate("ctx-candidate-aaa", valid_from="2026-07-01T00:00:00Z")
    second = _candidate("ctx-candidate-bbb", session="session-safe-b", valid_from="2026-07-02T09:30:00Z")

    append_candidate_deduplicated(first, path=queue_path)
    representative = append_candidate_deduplicated(second, path=queue_path)

    rows = load_review_candidates(path=queue_path)
    assert len(rows) == 1
    assert rows[0].candidate_id == "ctx-candidate-aaa"
    assert rows[0].occurrence_count == 2
    assert rows[0].last_seen_at == "2026-07-02T09:30:00Z"
    assert representative.candidate_id == "ctx-candidate-aaa"


def test_append_candidate_deduplicated_appends_new_class(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    append_candidate_deduplicated(_candidate("ctx-candidate-aaa"), path=queue_path)
    append_candidate_deduplicated(
        _candidate(
            "ctx-candidate-ccc",
            sample="goal_kind=practical_action; actual_kind=failed_or_interrupted; cause_kind=interrupted_or_failed",
        ),
        path=queue_path,
    )

    rows = load_review_candidates(path=queue_path)
    assert len(rows) == 2
    assert {row.occurrence_count for row in rows} == {1}


def test_repeated_same_class_outcome_gap_does_not_grow_queue(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "review_queue.jsonl"

    for session in ("session-safe-a", "session-safe-b", "session-safe-c"):
        record = record_practical_outcome_and_candidates(
            session_id=session,
            user_message="Sales Agent OSを直して実務で動くようにして",
            final_response="完了しました。設定しました。",
            completed=True,
            interrupted=False,
            outcome_path=outcome_path,
            review_queue_path=queue_path,
        )
        assert record.gap_detected is True

    rows = load_review_candidates(path=queue_path)
    assert len(rows) == 1
    assert rows[0].occurrence_count == 3


def test_legacy_queue_rows_load_with_default_dedup_fields(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    legacy = _candidate("ctx-candidate-aaa").to_dict()
    for key in ("occurrence_count", "last_seen_at", "merged_into", "approved_by"):
        legacy.pop(key, None)
    queue_path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")

    rows = load_review_candidates(path=queue_path)
    assert len(rows) == 1
    assert rows[0].occurrence_count == 1
    assert rows[0].last_seen_at is None
    assert rows[0].merged_into is None
    assert rows[0].approved_by is None


def test_approve_candidate_records_reviewer(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    write_review_candidates([_candidate("ctx-candidate-aaa")], path=queue_path)

    approve_candidate(
        "ctx-candidate-aaa",
        queue_path=queue_path,
        evidence_path=evidence_path,
        reviewer="human",
    )

    rows = load_review_candidates(path=queue_path)
    assert rows[0].approval_state == "approved"
    assert rows[0].approved_by == "human"


def test_run_auto_triage_dry_run_reports_without_writing(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    write_review_candidates(
        [
            _candidate("ctx-candidate-aaa", valid_from="2026-07-01T00:00:00Z"),
            _candidate("ctx-candidate-bbb", session="session-safe-b", valid_from="2026-07-02T00:00:00Z"),
            _candidate("ctx-candidate-org", scope="org", applies_to=["team_mode", "self_improvement"]),
        ],
        path=queue_path,
    )
    before = queue_path.read_bytes()

    report = run_auto_triage(queue_path=queue_path, min_occurrences=2)

    assert queue_path.read_bytes() == before
    assert report["dry_run"] is True
    assert report["pending_before"] == 3
    assert report["merged_count"] == 1
    assert report["distinct_pending_classes"] == 2
    eligible_ids = [entry["candidate_id"] for entry in report["auto_approve_eligible"]]
    assert eligible_ids == ["ctx-candidate-aaa"]
    review_ids = [entry["candidate_id"] for entry in report["human_review_required"]]
    assert "ctx-candidate-org" in review_ids
    assert report["auto_approved"] == []
    assert report["external_action_performed"] is False
    assert report["raw_private_context_exported"] is False


def test_run_auto_triage_apply_compacts_preserving_audit_trail(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    write_review_candidates(
        [
            _candidate("ctx-candidate-aaa", valid_from="2026-07-01T00:00:00Z"),
            _candidate("ctx-candidate-bbb", session="session-safe-b", valid_from="2026-07-02T00:00:00Z"),
            _candidate("ctx-candidate-org", scope="org", applies_to=["team_mode", "self_improvement"]),
        ],
        path=queue_path,
    )

    report = run_auto_triage(queue_path=queue_path, apply=True, min_occurrences=2)

    rows = {row.candidate_id: row for row in load_review_candidates(path=queue_path)}
    assert rows["ctx-candidate-aaa"].approval_state == "proposed"
    assert rows["ctx-candidate-aaa"].occurrence_count == 2
    assert rows["ctx-candidate-bbb"].approval_state == "merged"
    assert rows["ctx-candidate-bbb"].merged_into == "ctx-candidate-aaa"
    assert rows["ctx-candidate-org"].approval_state == "proposed"
    assert report["dry_run"] is False
    assert report["merged_count"] == 1
    assert report["auto_approved"] == []


def test_run_auto_triage_approve_low_risk_promotes_only_eligible(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    write_review_candidates(
        [
            _candidate("ctx-candidate-aaa", valid_from="2026-07-01T00:00:00Z"),
            _candidate("ctx-candidate-bbb", session="session-safe-b", valid_from="2026-07-02T00:00:00Z"),
            _candidate("ctx-candidate-ccc", session="session-safe-c", valid_from="2026-07-03T00:00:00Z"),
            _candidate("ctx-candidate-org", scope="org", applies_to=["team_mode", "self_improvement"]),
            _candidate(
                "ctx-candidate-prod",
                summary="Correct the production deploy target before mutation.",
                sample="deploy_target_correction",
            ),
        ],
        path=queue_path,
    )

    report = run_auto_triage(
        queue_path=queue_path,
        evidence_path=evidence_path,
        apply=True,
        approve_low_risk=True,
        min_occurrences=3,
    )

    rows = {row.candidate_id: row for row in load_review_candidates(path=queue_path)}
    assert rows["ctx-candidate-aaa"].approval_state == "approved"
    assert rows["ctx-candidate-aaa"].approved_by == "auto_triage_v1"
    assert rows["ctx-candidate-aaa"].evidence.human_approved is True
    assert rows["ctx-candidate-org"].approval_state == "proposed"
    assert rows["ctx-candidate-prod"].approval_state == "proposed"

    evidence_rows = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["evidence_id"] for row in evidence_rows] == ["ctx-ev-aaa"]
    assert [entry["candidate_id"] for entry in report["auto_approved"]] == ["ctx-candidate-aaa"]


def test_classify_auto_approval_is_fail_closed():
    eligible = _candidate("ctx-candidate-aaa", occurrence_count=3)
    assert classify_auto_approval(eligible, min_occurrences=3)[0] is True

    denied = [
        _candidate("ctx-candidate-sens", occurrence_count=3, sensitivity="confidential"),
        _candidate("ctx-candidate-org", occurrence_count=3, scope="org"),
        _candidate("ctx-candidate-kind", occurrence_count=3, source_kind="policy"),
        _candidate("ctx-candidate-apply", occurrence_count=3, applies_to=["sales", "outbound"]),
        _candidate(
            "ctx-candidate-prod",
            occurrence_count=3,
            summary="Correct the production deploy target before mutation.",
        ),
        _candidate("ctx-candidate-few", occurrence_count=2),
    ]
    for candidate in denied:
        allowed, reason = classify_auto_approval(candidate, min_occurrences=3)
        assert allowed is False, candidate.candidate_id
        assert reason


def test_loop_status_counts_merged_and_backlog(tmp_path: Path):
    queue_path = tmp_path / "review_queue.jsonl"
    outcome_path = tmp_path / "outcomes.jsonl"
    merged = EvidenceCandidate(
        candidate_id="ctx-candidate-bbb",
        evidence=_candidate("ctx-candidate-bbb").evidence,
        approval_state="merged",
        merged_into="ctx-candidate-aaa",
    )
    write_review_candidates(
        [
            _candidate("ctx-candidate-aaa", occurrence_count=2),
            merged,
            _candidate("ctx-candidate-ddd", approval_state="approved"),
        ],
        path=queue_path,
    )

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path, backlog_alert_threshold=1)
    assert status.pending_candidate_count == 1
    assert status.approved_candidate_count == 1
    assert status.merged_candidate_count == 1
    assert status.distinct_pending_classes == 1
    assert status.backlog_alert is True
    payload = status.to_dict()
    assert payload["merged_candidate_count"] == 1
    assert payload["distinct_pending_classes"] == 1
    assert payload["backlog_alert"] is True

    relaxed = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path, backlog_alert_threshold=5)
    assert relaxed.backlog_alert is False


def test_compaction_then_approval_flips_gap_group_fix_approved(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "review_queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"

    for session in ("session-safe-a", "session-safe-b"):
        record_practical_outcome_and_candidates(
            session_id=session,
            user_message="Sales Agent OSを直して実務で動くようにして",
            final_response="完了しました。設定しました。",
            completed=True,
            interrupted=False,
            outcome_path=outcome_path,
            review_queue_path=queue_path,
        )

    rows = load_review_candidates(path=queue_path)
    assert len(rows) == 1
    approve_candidate(rows[0].candidate_id, queue_path=queue_path, evidence_path=evidence_path)

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path)
    assert status.gap_count == 2
    assert len(status.recurrences) == 1
    assert status.recurrences[0].fix_approved is True
    assert status.recurrences[0].converging is True


def test_cli_auto_triage_defaults_to_dry_run(tmp_path: Path, capsys):
    from scripts.sinria_context_share_loop import main

    queue_path = tmp_path / "review_queue.jsonl"
    write_review_candidates(
        [
            _candidate("ctx-candidate-aaa", valid_from="2026-07-01T00:00:00Z"),
            _candidate("ctx-candidate-bbb", session="session-safe-b", valid_from="2026-07-02T00:00:00Z"),
        ],
        path=queue_path,
    )
    before = queue_path.read_bytes()

    rc = main(["auto-triage", "--queue-path", str(queue_path), "--min-occurrences", "2"])

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["merged_count"] == 1
    assert [entry["candidate_id"] for entry in report["auto_approve_eligible"]] == ["ctx-candidate-aaa"]
    assert queue_path.read_bytes() == before
