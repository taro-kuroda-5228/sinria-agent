"""Convergence KPI noise split tests.

`execution_incomplete` / `interrupted_or_failed` gap classes describe
in-progress or interrupted turns, not violations of an approved constraint.
Counting them as recurrence makes zero-convergence structurally impossible
(observed live: 794 execution_incomplete recurrences vs 8 genuine ones).
The KPI must alert on genuine violation classes only, while still reporting
progress-noise classes transparently.
"""

from datetime import datetime, timezone
from pathlib import Path

import agent.context_share.outcome_gap as og
from agent.context_share.loop_metrics import candidate_id_for_record, compute_loop_status
from agent.context_share.review_queue import approve_candidate


def _record_incomplete(outcome_path: Path, queue_path: Path, *, session: str, when: datetime):
    """A practical-action turn that simply had not finished yet."""
    record = og.assess_practical_outcome(
        session_id=session,
        user_message="Sales Agent OSを直して実務で動くようにして",
        final_response="ブロックされています。エラーを調査中です。",
        completed=True,
        interrupted=False,
        tool_turn_count=2,
        now=when,
    )
    og.append_outcome_record(record, path=outcome_path)
    if record.gap_detected:
        og.append_candidate_deduplicated(og._candidate_for_record(record), path=queue_path)
    return record


def _record_unverified_claim(outcome_path: Path, queue_path: Path, *, session: str, when: datetime):
    """A genuine violation: claims completion without visible verification."""
    record = og.assess_practical_outcome(
        session_id=session,
        user_message="Sales Agent OSを直して実務で動くようにして",
        final_response="完了しました。設定しました。",
        completed=True,
        interrupted=False,
        tool_turn_count=1,
        now=when,
    )
    og.append_outcome_record(record, path=outcome_path)
    if record.gap_detected:
        og.append_candidate_deduplicated(og._candidate_for_record(record), path=queue_path)
    return record


def test_progress_noise_class_is_flagged_and_does_not_trip_recurrence_alert(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    record = _record_incomplete(outcome_path, queue_path, session="s-noise", when=t1)
    approve_candidate(
        candidate_id_for_record(record.record_id),
        queue_path=queue_path,
        evidence_path=evidence_path,
        approved_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    # Same in-progress class occurs again after the approval.
    _record_incomplete(outcome_path, queue_path, session="s-noise-2", when=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc))

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path, evidence_path=evidence_path)
    serialized = status.to_dict()

    noise_recs = [rec for rec in status.recurrences if rec.noise_class]
    assert len(noise_recs) == 1
    assert noise_recs[0].gap_summary.endswith(":execution_incomplete")
    assert serialized["approved_recurrence_alert"] is False
    assert serialized["progress_noise_gap_count"] == 1


def test_genuine_violation_class_still_trips_recurrence_alert(tmp_path: Path):
    outcome_path = tmp_path / "outcomes.jsonl"
    queue_path = tmp_path / "queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    record = _record_unverified_claim(outcome_path, queue_path, session="s-real", when=t1)
    approve_candidate(
        candidate_id_for_record(record.record_id),
        queue_path=queue_path,
        evidence_path=evidence_path,
        approved_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    _record_unverified_claim(outcome_path, queue_path, session="s-real-2", when=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc))

    status = compute_loop_status(outcome_path=outcome_path, queue_path=queue_path, evidence_path=evidence_path)
    serialized = status.to_dict()

    genuine = [rec for rec in status.recurrences if not rec.noise_class]
    assert len(genuine) == 1
    assert genuine[0].recurrence_after_fix == 1
    assert serialized["approved_recurrence_alert"] is True
    assert serialized["progress_noise_gap_count"] == 0
