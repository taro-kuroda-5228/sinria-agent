"""Tests for the built-in throttled self-improvement promotion (P1 loop-close).

The per-turn choke point already records outcomes and queues candidates; this
module is the missing OUTPUT side that promotes recurring low-risk classes into
durable, resolver-visible evidence automatically — once per interval, for every
Sinria install, with no manual cron/launchd setup.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.context_share.evidence import ContextEvidence
from agent.context_share.extraction import EvidenceCandidate
from agent.context_share.review_queue import append_candidate_deduplicated
from agent.context_share.loop_maintenance import (
    maintenance_marker_path,
    promotion_due,
    run_due_promotion,
)


def _low_risk_candidate(cid: str, occ: int) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=cid,
        evidence=ContextEvidence(
            evidence_id=cid.replace("ctx-candidate-", "ctx-ev-"),
            source_session_id="sess-x",
            source_kind="workflow_outcome",
            scope="project",
            summary="Practical-completion gap detected: apply Goal-Actual-Gap-Cause-Durable Fix.",
            sanitized_sample="goal=practical_action; actual=incomplete",
            sensitivity="internal",
            applies_to=["self_improvement", "practical_completion", "context_share"],
            valid_from="2026-07-01T00:00:00Z",
            confidence=0.88,
            human_approved=False,
        ),
        approval_state="proposed",
        raw_context_stored=False,
        external_action_performed=False,
        extraction_reason="goal_actual_gap_practical_completion_loop",
        occurrence_count=occ,
    )


def _now() -> datetime:
    return datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


# --- throttle -------------------------------------------------------------

def test_promotion_due_when_no_prior_run():
    assert promotion_due(now=_now(), last_run=None, min_interval_hours=24.0) is True


def test_promotion_not_due_within_interval():
    last = _now() - timedelta(hours=5)
    assert promotion_due(now=_now(), last_run=last, min_interval_hours=24.0) is False


def test_promotion_due_after_interval():
    last = _now() - timedelta(hours=25)
    assert promotion_due(now=_now(), last_run=last, min_interval_hours=24.0) is True


# --- run_due_promotion ----------------------------------------------------

def test_run_due_promotion_promotes_low_risk_and_writes_marker(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    ev = tmp_path / "evidence.jsonl"
    marker = tmp_path / "last_promotion.json"
    append_candidate_deduplicated(_low_risk_candidate("ctx-candidate-lowrisk", 3), path=queue)

    report = run_due_promotion(
        now=_now(), queue_path=queue, evidence_path=ev, marker_path=marker,
        min_interval_hours=24.0, min_occurrences=3,
    )

    assert report is not None
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r["human_approved"] is True for r in promoted)
    assert marker.exists()
    saved = json.loads(marker.read_text(encoding="utf-8"))
    assert saved["last_run_at"].startswith("2026-07-06T12:00:00")


def test_run_due_promotion_skips_when_recently_run(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    ev = tmp_path / "evidence.jsonl"
    marker = tmp_path / "last_promotion.json"
    marker.write_text(json.dumps({"last_run_at": (_now() - timedelta(hours=2)).isoformat()}), encoding="utf-8")
    append_candidate_deduplicated(_low_risk_candidate("ctx-candidate-lowrisk", 3), path=queue)

    report = run_due_promotion(
        now=_now(), queue_path=queue, evidence_path=ev, marker_path=marker,
        min_interval_hours=24.0, min_occurrences=3,
    )

    assert report is None  # throttled
    assert not ev.exists() or ev.read_text(encoding="utf-8").strip() == ""


def test_marker_path_lives_under_context_share(tmp_path: Path):
    p = maintenance_marker_path(home=tmp_path)
    assert p == tmp_path / "context_share" / "last_promotion.json"


# --- install-type flag: recurring-correction auto-promotion ----------------

from dataclasses import replace

from agent.context_share.auto_triage import classify_auto_approval
from agent.context_share.correction_capture import extract_correction_candidate


def _recurring_correction(occ: int, text: str = "今後は必ずテストを先に書いて") -> EvidenceCandidate:
    candidate = extract_correction_candidate(text, session_id="sess-x")
    assert candidate is not None, "correction marker text should produce a candidate"
    return replace(candidate, occurrence_count=occ)


def test_correction_denied_by_default_even_when_recurring():
    ok, reason = classify_auto_approval(_recurring_correction(9), min_occurrences=3)
    assert ok is False
    assert "human review" in reason


def test_recurring_correction_eligible_when_opted_in():
    ok, _ = classify_auto_approval(
        _recurring_correction(3), min_occurrences=3, allow_correction_auto_promote=True
    )
    assert ok is True


def test_one_off_correction_not_eligible_even_when_opted_in():
    ok, _ = classify_auto_approval(
        _recurring_correction(1), min_occurrences=3, allow_correction_auto_promote=True
    )
    assert ok is False  # recurrence is still required


def test_correction_with_deny_marker_never_eligible_when_opted_in():
    c = _recurring_correction(9, text="本番デプロイは今後は必ず承認を得てから")
    ok, reason = classify_auto_approval(c, min_occurrences=3, allow_correction_auto_promote=True)
    assert ok is False
    assert "deny marker" in reason


def test_run_due_promotion_opt_out_keeps_correction_gated(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    ev = tmp_path / "evidence.jsonl"
    marker = tmp_path / "last_promotion.json"
    append_candidate_deduplicated(_recurring_correction(3), path=queue)
    run_due_promotion(now=_now(), queue_path=queue, evidence_path=ev, marker_path=marker, min_occurrences=3)
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()] if ev.exists() else []
    assert not any("corr" in r["evidence_id"] for r in promoted)


def test_run_due_promotion_opt_in_promotes_recurring_correction(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    ev = tmp_path / "evidence.jsonl"
    marker = tmp_path / "last_promotion.json"
    append_candidate_deduplicated(_recurring_correction(3), path=queue)
    run_due_promotion(
        now=_now(), queue_path=queue, evidence_path=ev, marker_path=marker,
        min_occurrences=3, allow_correction_auto_promote=True,
    )
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r["source_kind"] == "user_correction" and r["human_approved"] is True for r in promoted)


# --- config flag resolution -----------------------------------------------

from agent.context_share.loop_maintenance import (  # noqa: E402
    correction_autopromote_enabled,
    run_builtin_promotion_if_due,
)


def test_autopromote_flag_default_false():
    assert correction_autopromote_enabled(None) is False
    assert correction_autopromote_enabled({}) is False
    assert correction_autopromote_enabled({"context_share": {}}) is False
    assert correction_autopromote_enabled({"context_share": {"auto_promote_recurring_corrections": False}}) is False


def test_autopromote_flag_true_when_set():
    assert correction_autopromote_enabled({"context_share": {"auto_promote_recurring_corrections": True}}) is True


# --- built-in choke-point wiring: derives hermetic paths -------------------

def test_builtin_promotion_derives_paths_and_promotes_low_risk(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    append_candidate_deduplicated(_low_risk_candidate("ctx-candidate-lowrisk", 3), path=queue)
    report = run_builtin_promotion_if_due(review_queue_path=queue, now=_now(), config={})
    assert report is not None
    ev = tmp_path / "evidence.jsonl"
    assert ev.exists()
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r["human_approved"] is True for r in promoted)
    assert (tmp_path / "last_promotion.json").exists()


def test_builtin_promotion_correction_gated_by_default(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    append_candidate_deduplicated(_recurring_correction(3), path=queue)
    run_builtin_promotion_if_due(review_queue_path=queue, now=_now(), config={})
    ev = tmp_path / "evidence.jsonl"
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()] if ev.exists() else []
    assert not any(r["source_kind"] == "user_correction" for r in promoted)


def test_builtin_promotion_opt_in_promotes_correction(tmp_path: Path):
    queue = tmp_path / "review_queue.jsonl"
    append_candidate_deduplicated(_recurring_correction(3), path=queue)
    run_builtin_promotion_if_due(
        review_queue_path=queue, now=_now(),
        config={"context_share": {"auto_promote_recurring_corrections": True}},
    )
    ev = tmp_path / "evidence.jsonl"
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r["source_kind"] == "user_correction" and r["human_approved"] is True for r in promoted)


# --- integration: the per-turn choke point closes the loop by default ------

from agent.context_share.outcome_gap import record_practical_outcome_and_candidates  # noqa: E402


def test_per_turn_choke_point_runs_builtin_promotion(tmp_path: Path):
    """The universal per-turn seam (CLI/gateway/cron all pass through it) must
    promote recurring low-risk classes into durable evidence with no manual
    cron/launchd setup — this is what makes loop-closing a default feature."""
    queue = tmp_path / "review_queue.jsonl"
    outcome = tmp_path / "outcome_gap.jsonl"
    append_candidate_deduplicated(_low_risk_candidate("ctx-candidate-seed", 3), path=queue)

    record_practical_outcome_and_candidates(
        session_id="sess-x",
        user_message="実装して",
        final_response="done",
        completed=True,
        interrupted=False,
        outcome_path=outcome,
        review_queue_path=queue,
    )

    ev = tmp_path / "evidence.jsonl"
    assert ev.exists(), "choke point must have run the built-in promotion"
    promoted = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r["human_approved"] is True for r in promoted)
