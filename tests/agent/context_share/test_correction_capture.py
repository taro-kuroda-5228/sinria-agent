"""Open-world sanitized correction capture tests.

The capture path must turn real user corrections into review-gated evidence
candidates (so Sinria's long-term memory can grow beyond hardcoded rules)
while never storing secrets/PHI-like content and never auto-approving
freeform captured text.
"""

from datetime import datetime, timezone
from pathlib import Path

from agent.context_share.auto_triage import classify_auto_approval
from agent.context_share.correction_capture import (
    EXTRACTION_REASON,
    extract_correction_candidate,
    record_correction_candidate,
)
from agent.context_share.evidence import EvidenceLedger
from agent.context_share.intent_resolver import IntentResolver
from agent.context_share.outcome_gap import record_practical_outcome_and_candidates
from agent.context_share.review_queue import approve_candidate, load_review_candidates
from agent.context_share.storage import load_evidence_jsonl

_NOW = datetime(2026, 7, 4, 5, 0, tzinfo=timezone.utc)


def test_japanese_correction_marker_yields_review_gated_candidate():
    candidate = extract_correction_candidate(
        "議事録はMeeting InboxではなくDriveの議事録共有ドライブに保存して",
        session_id="session-corr-1",
        now=_NOW,
    )

    assert candidate is not None
    assert candidate.extraction_reason == EXTRACTION_REASON
    assert candidate.evidence.human_approved is False
    assert candidate.evidence.source_kind == "user_correction"
    assert "ではなく" in candidate.evidence.summary
    assert "user_correction_capture" in candidate.evidence.applies_to


def test_message_without_correction_marker_is_ignored():
    assert extract_correction_candidate("今日の天気を教えて", session_id="s", now=_NOW) is None
    assert extract_correction_candidate("MedSpotの実装を進めて", session_id="s", now=_NOW) is None


def test_sensitive_correction_is_dropped_not_stored():
    candidate = extract_correction_candidate(
        "今後は password: hunter2 を使って接続して",
        session_id="session-corr-2",
        now=_NOW,
    )

    assert candidate is None


def test_repeated_correction_bumps_occurrence_instead_of_duplicating(tmp_path: Path):
    queue = tmp_path / "queue.jsonl"
    message = "デプロイ先はVercelではなくGCP Cloud Runにして"

    first = record_correction_candidate(message, session_id="s-1", review_queue_path=queue, now=_NOW)
    second = record_correction_candidate(message, session_id="s-2", review_queue_path=queue, now=_NOW)

    assert first is not None and second is not None
    rows = load_review_candidates(path=queue)
    live = [row for row in rows if row.approval_state == "proposed"]
    assert len(live) == 1
    assert live[0].occurrence_count == 2


def test_captured_correction_is_never_auto_approved():
    candidate = extract_correction_candidate(
        "コンテキストは今後は必ずContext Shareに記録して",
        session_id="session-corr-3",
        now=_NOW,
    )
    assert candidate is not None
    # Even with a sanctioned applies_to overlap and a high occurrence count,
    # freeform captured text must stay human-review gated (fail-closed).
    from dataclasses import replace

    recurred = replace(candidate, occurrence_count=10)
    allowed, reason = classify_auto_approval(recurred)

    assert allowed is False
    assert "human review" in reason


def test_per_turn_wiring_records_correction_alongside_outcome(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"

    record_practical_outcome_and_candidates(
        session_id="session-corr-4",
        user_message="ダッシュボードは新規作成ではなく既存の社内Dashboardを更新して",
        final_response="完了しました。",
        completed=True,
        interrupted=False,
        tool_turn_count=1,
        outcome_path=outcome_path,
        review_queue_path=queue_path,
    )

    reasons = {row.extraction_reason for row in load_review_candidates(path=queue_path)}
    assert EXTRACTION_REASON in reasons


def test_approved_captured_correction_is_recalled_for_related_japanese_request(tmp_path: Path):
    """End-to-end: capture → human approve → durable evidence → Japanese recall."""
    queue = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"

    candidate = record_correction_candidate(
        "議事録はMeeting InboxではなくDriveの議事録共有ドライブに保存して",
        session_id="session-corr-5",
        review_queue_path=queue,
        now=_NOW,
    )
    assert candidate is not None
    approve_candidate(candidate.candidate_id, queue_path=queue, evidence_path=evidence_path)

    ledger = EvidenceLedger(load_evidence_jsonl(evidence_path))
    resolver = IntentResolver(ledger=ledger)
    result = resolver.resolve("議事録の保存先を整理して", platform="cli")

    assert candidate.evidence.evidence_id in result.retrieval_evidence_ids
    assert any("議事録" in constraint for constraint in result.applicable_constraints)
