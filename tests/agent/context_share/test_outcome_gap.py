from pathlib import Path

from agent.context_share.outcome_gap import (
    apply_practical_completion_guard,
    assess_practical_outcome,
    load_outcome_records,
    record_practical_outcome_and_candidates,
)
from agent.context_share.review_queue import load_review_candidates


def test_practical_completion_guard_marks_unverified_completion_claim_before_user_delivery():
    guarded = apply_practical_completion_guard(
        user_message="Sinriaの自己改善ループを完璧に実装して",
        final_response="完了しました。実装しました。",
        completed=True,
        interrupted=False,
        tool_turn_count=2,
    )

    assert "Sinria verification gate" in guarded
    assert "未検証" in guarded
    assert "完了しました。実装しました。" in guarded

    record = assess_practical_outcome(
        session_id="session-safe-guard",
        user_message="Sinriaの自己改善ループを完璧に実装して",
        final_response=guarded,
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=2,
    )
    assert record.actual_kind == "incomplete_or_blocked"
    assert record.cause_kind == "execution_incomplete"


def test_practical_completion_guard_leaves_verified_completion_unchanged():
    response = "実装しました。pytestを実行し、27 passedを確認しました。"

    assert apply_practical_completion_guard(
        user_message="Sinriaの自己改善ループを完璧に実装して",
        final_response=response,
        completed=True,
        interrupted=False,
        tool_turn_count=2,
    ) == response


def test_practical_action_claim_without_verification_becomes_gap_candidate(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "review_queue.jsonl"

    record = record_practical_outcome_and_candidates(
        session_id="session-safe-1",
        user_message="Sales Agent OSを直して実務で動くようにして",
        final_response="完了しました。設定しました。",
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=1,
        outcome_path=outcome_path,
        review_queue_path=queue_path,
    )

    assert record.gap_detected is True
    assert record.actual_kind == "claimed_without_visible_verification"
    assert record.cause_kind == "verification_gap"
    assert record.gap_summary == "practical_action:claimed_without_visible_verification:verification_gap"
    assert {"skill", "test", "runbook"} <= set(record.durable_fix_kinds)

    records = load_outcome_records(path=outcome_path)
    assert len(records) == 1
    assert records[0].raw_context_stored is False
    assert records[0].external_action_performed is False
    assert records[0].source_turn_ref.startswith("turn:")
    # Raw hex digests can contain long digit runs that look like phone/ID data
    # to the safety guard. Outcome refs must use the digit-free safe digest.
    assert not any(ch.isdigit() for ch in records[0].source_turn_ref)

    candidates = load_review_candidates(path=queue_path)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.extraction_reason == "goal_actual_gap_practical_completion_loop"
    assert candidate.raw_context_stored is False
    assert candidate.external_action_performed is False
    assert candidate.evidence.source_kind == "workflow_outcome"
    assert "Goal→Actual→Gap→Cause→Durable Fix" in candidate.evidence.summary
    assert "Sales Agent OS" not in candidate.evidence.summary


def test_verified_real_workflow_completion_is_recorded_without_candidate(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "review_queue.jsonl"

    record = record_practical_outcome_and_candidates(
        session_id="session-safe-2",
        user_message="SINRIA_SALES_LLM_MODELをgpt-5.5に設定して",
        final_response="設定しました。smoke testを実行し、ok: true を確認しました。",
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=2,
        outcome_path=outcome_path,
        review_queue_path=queue_path,
    )

    assert record.gap_detected is False
    assert record.actual_kind == "verified_practical_completion"
    assert load_review_candidates(path=queue_path) == []


def test_question_is_not_treated_as_practical_gap():
    record = assess_practical_outcome(
        session_id="session-safe-3",
        user_message="この設計どうかな？",
        final_response="良いと思います。理由は...",
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=0,
    )

    assert record.goal_kind == "question"
    assert record.gap_detected is False


def test_practical_action_that_only_gets_explanation_is_gap():
    record = assess_practical_outcome(
        session_id="session-safe-3b",
        user_message="自己改善ループを実際に動くところまで構築して",
        final_response="良い方向です。Goal→Actual→Gapで改善できます。",
        completed=True,
        interrupted=False,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=0,
    )

    assert record.goal_kind == "practical_action"
    assert record.actual_kind == "incomplete_or_blocked"
    assert record.gap_detected is True
    assert {"test", "runbook", "code_or_config"} <= set(record.durable_fix_kinds)


def test_interrupted_practical_action_becomes_gap_candidate(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "review_queue.jsonl"

    record = record_practical_outcome_and_candidates(
        session_id="session-safe-4",
        user_message="自己改善ループを実際に動くところまで構築して",
        final_response=None,
        completed=False,
        interrupted=True,
        model="gpt-5.5",
        provider="openai-codex",
        platform="discord",
        tool_turn_count=3,
        outcome_path=outcome_path,
        review_queue_path=queue_path,
    )

    assert record.goal_kind == "practical_action"
    assert record.actual_kind == "failed_or_interrupted"
    assert record.gap_detected is True
    assert {"test", "runbook", "code_or_config"} <= set(record.durable_fix_kinds)

    records = load_outcome_records(path=outcome_path)
    assert len(records) == 1
    assert records[0].raw_context_stored is False

    candidates = load_review_candidates(path=queue_path)
    assert len(candidates) == 1
    assert candidates[0].evidence.source_kind == "workflow_outcome"
    assert "failed_or_interrupted" in candidates[0].evidence.sanitized_sample
