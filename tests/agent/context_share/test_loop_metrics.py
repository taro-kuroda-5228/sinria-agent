"""Tests for the self-improvement loop effect measurement (stage 7 KPI).

The loop is: Goal → Actual → Gap → Cause → Durable-fix candidates → review-gated
promotion → behavior change. These tests cover the missing closing stage:
measuring whether the same gap recurs after an approved durable fix, i.e. the
"same correction recurrence → zero convergence" KPI.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.context_share.loop_metrics import (
    GapRecurrence,
    LoopStatus,
    candidate_id_for_record,
    compute_loop_status,
)
from agent.context_share.outcome_gap import record_practical_outcome_and_candidates
from agent.context_share.review_queue import approve_candidate, load_review_candidates


def _record_gap(outcome_path: Path, queue_path: Path, *, session: str, when: datetime):
    """Record a practical-action turn that claims completion without verification."""
    import agent.context_share.outcome_gap as og

    record = og.assess_practical_outcome(
        session_id=session,
        user_message="Sales Agent OSを直して実務で動くようにして",
        final_response="完了しました。設定しました。",
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=1,
        now=when,
    )
    og.append_outcome_record(record, path=outcome_path)
    if record.gap_detected:
        og.append_candidate_deduplicated(og._candidate_for_record(record), path=queue_path)
    return record


def _record_verified(outcome_path: Path, queue_path: Path, *, session: str, when: datetime):
    import agent.context_share.outcome_gap as og

    record = og.assess_practical_outcome(
        session_id=session,
        user_message="Sales Agent OSを直して実務で動くようにして",
        final_response="実装し、pytest通過とbrowser動作確認まで検証しました。完了です。",
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=3,
        now=when,
    )
    og.append_outcome_record(record, path=outcome_path)
    return record


def test_candidate_id_for_record_matches_outcome_gap_linkage(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    record = record_practical_outcome_and_candidates(
        session_id="session-link-1",
        user_message="実装して",
        final_response="完了しました。",
        completed=True,
        interrupted=False,
        tool_turn_count=1,
        outcome_path=outcome_path,
        review_queue_path=queue_path,
    )
    candidates = load_review_candidates(path=queue_path)
    assert len(candidates) == 1
    assert candidate_id_for_record(record.record_id) == candidates[0].candidate_id


def test_status_counts_gaps_and_pending_candidates(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    _record_gap(outcome_path, queue_path, session="session-a", when=t1)
    _record_verified(outcome_path, queue_path, session="session-b", when=datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc))

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path)
    assert isinstance(status, LoopStatus)
    assert status.total_outcomes == 2
    assert status.gap_count == 1
    assert status.pending_candidate_count == 1
    assert status.approved_candidate_count == 0
    assert len(status.recurrences) == 1
    rec = status.recurrences[0]
    assert isinstance(rec, GapRecurrence)
    assert rec.gap_summary == "practical_action:claimed_without_visible_verification:verification_gap"
    assert rec.occurrences == 1
    assert rec.fix_approved is False
    assert rec.recurrence_after_fix == 0
    assert rec.converging is False  # no approved fix yet


def test_approved_fix_with_no_later_recurrence_is_converging(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    record = _record_gap(outcome_path, queue_path, session="session-a", when=t1)
    approve_candidate(candidate_id_for_record(record.record_id), queue_path=queue_path, evidence_path=evidence_path)
    # later verified completion of the same kind of request — no gap
    _record_verified(outcome_path, queue_path, session="session-c", when=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc))

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path)
    assert status.approved_candidate_count == 1
    rec = status.recurrences[0]
    assert rec.fix_approved is True
    assert rec.recurrence_after_fix == 0
    assert rec.converging is True


def test_same_gap_after_approved_fix_counts_recurrence_and_blocks_convergence(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    record = _record_gap(outcome_path, queue_path, session="session-a", when=t1)
    approve_candidate(
        candidate_id_for_record(record.record_id),
        queue_path=queue_path,
        evidence_path=evidence_path,
        approved_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
    )
    # the same gap recurs AFTER the fix was approved
    _record_gap(outcome_path, queue_path, session="session-d", when=datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc))

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path)
    rec = status.recurrences[0]
    assert rec.occurrences == 2
    assert rec.fix_approved is True
    assert rec.recurrence_after_fix == 1
    assert rec.converging is False


def test_approved_recurring_gap_reports_prompt_injection_effect_gap(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    first = _record_gap(
        outcome_path,
        queue_path,
        session="session-injection-1",
        when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    approve_candidate(
        candidate_id_for_record(first.record_id),
        queue_path=queue_path,
        evidence_path=evidence_path,
        approved_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
    )
    _record_gap(
        outcome_path,
        queue_path,
        session="session-injection-2",
        when=datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc),
    )

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path, evidence_path=evidence_path)

    rec = status.recurrences[0]
    assert rec.approved_constraint_injected is True
    assert rec.behavior_change_verified is False
    assert rec.effect_gap == "constraint_injected_but_behavior_recurred"
    assert status.to_dict()["approved_recurrence_alert"] is True
    assert status.to_dict()["prompt_injection_gap_count"] == 0


def test_approved_gap_reports_missing_prompt_injection_when_evidence_store_lacks_fix(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    first = _record_gap(
        outcome_path,
        queue_path,
        session="session-missing-injection-1",
        when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    approve_candidate(
        candidate_id_for_record(first.record_id),
        queue_path=queue_path,
        evidence_path=tmp_path / "unused-evidence.jsonl",
        approved_at=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
    )

    status = compute_loop_status(
        outcome_path=outcome_path,
        queue_path=queue_path,
        evidence_path=tmp_path / "missing-evidence.jsonl",
    )

    rec = status.recurrences[0]
    assert rec.approved_constraint_injected is False
    assert rec.behavior_change_verified is False
    assert rec.effect_gap == "approved_constraint_not_in_prompt"
    assert status.to_dict()["prompt_injection_gap_count"] == 1


def test_historical_recurrences_before_human_approval_do_not_block_convergence(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    first = _record_gap(
        outcome_path,
        queue_path,
        session="session-before-approval-1",
        when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
    )
    _record_gap(
        outcome_path,
        queue_path,
        session="session-before-approval-2",
        when=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
    )

    approve_candidate(
        candidate_id_for_record(first.record_id),
        queue_path=queue_path,
        evidence_path=evidence_path,
        approved_at=datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc),
    )

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path)
    rec = status.recurrences[0]
    assert rec.occurrences == 2
    assert rec.fix_approved is True
    assert rec.fix_approved_at == "2026-06-03T10:00:00+00:00"
    assert rec.recurrence_after_fix == 0
    assert rec.converging is True


def test_status_handles_missing_files(tmp_path: Path):
    status = compute_loop_status(outcome_path=tmp_path / "missing.jsonl", queue_path=tmp_path / "missing-q.jsonl")
    assert status.total_outcomes == 0
    assert status.gap_count == 0
    assert status.recurrences == []


def test_status_to_dict_is_json_safe_and_sanitized(tmp_path: Path):
    import json

    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    _record_gap(outcome_path, queue_path, session="session-a", when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))
    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path)
    serialized = json.dumps(status.to_dict(), ensure_ascii=False)
    assert "Sales Agent OS" not in serialized  # raw user content must never appear
    assert "gap_summary" in serialized


# --- CLI tests -------------------------------------------------------------


def test_cli_status_outputs_json(tmp_path: Path, capsys):
    from scripts.sinria_context_share_loop import main

    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    _record_gap(outcome_path, queue_path, session="session-a", when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))

    rc = main(["status", "--outcome-path", str(outcome_path), "--queue-path", str(queue_path)])
    assert rc == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["total_outcomes"] == 1
    assert report["gap_count"] == 1
    assert report["external_action_performed"] is False


def test_cli_list_shows_pending_candidates(tmp_path: Path, capsys):
    from scripts.sinria_context_share_loop import main

    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    _record_gap(outcome_path, queue_path, session="session-a", when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))

    rc = main(["list", "--queue-path", str(queue_path)])
    assert rc == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["pending_count"] == 1
    assert report["candidates"][0]["approval_state"] == "proposed"


def test_cli_approve_promotes_candidate_to_durable_evidence(tmp_path: Path, capsys):
    from scripts.sinria_context_share_loop import main

    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    record = _record_gap(outcome_path, queue_path, session="session-a", when=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))
    cid = candidate_id_for_record(record.record_id)

    rc = main([
        "approve", cid,
        "--queue-path", str(queue_path),
        "--evidence-path", str(evidence_path),
    ])
    assert rc == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["approved_candidate_id"] == cid
    assert evidence_path.exists()
    from agent.context_share.storage import load_evidence_jsonl

    promoted = load_evidence_jsonl(evidence_path)
    assert len(promoted) == 1
    assert promoted[0].human_approved is True


def test_cli_approve_unknown_candidate_fails_recoverably(tmp_path: Path, capsys):
    from scripts.sinria_context_share_loop import main

    queue_path = tmp_path / "queue.jsonl"
    queue_path.write_text("", encoding="utf-8")
    rc = main(["approve", "ctx-candidate-doesnotexist", "--queue-path", str(queue_path)])
    assert rc == 1
    import json

    report = json.loads(capsys.readouterr().out)
    assert "error" in report
