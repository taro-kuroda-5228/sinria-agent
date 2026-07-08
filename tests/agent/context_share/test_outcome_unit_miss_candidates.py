from pathlib import Path

from agent.context_share.outcome_gap import record_outcome_unit_miss_candidate


def test_outcome_unit_miss_candidate_is_review_gated_and_sanitized(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    candidate = record_outcome_unit_miss_candidate(
        session_id="session-outcome-loop",
        os_id="application_agent_os",
        app_module_id="medevidence",
        outcome_id="outcome-medevidence-provenance",
        goal_summary="clinical evidence workflow provenance visible",
        actual_summary="provenance missing from operator view",
        review_queue_path=queue,
    )

    assert candidate.candidate_id.startswith("ctx-candidate-outcome-unit-")
    assert candidate.raw_context_stored is False
    assert candidate.external_action_performed is False
    assert candidate.approval_state == "proposed"
    assert candidate.evidence.source_kind == "workflow_outcome"
    assert candidate.evidence.scope == "project"
    assert candidate.evidence.sensitivity == "internal"
    assert "os_id=application_agent_os" in candidate.evidence.sanitized_sample
    assert "app_module_id=medevidence" in candidate.evidence.sanitized_sample
    assert "provenance missing" not in candidate.evidence.sanitized_sample
    assert queue.exists()
    assert "raw_context_stored" in queue.read_text(encoding="utf-8")
