
from agent.context_share.evidence import ContextEvidence
from agent.context_share.improvement_candidates import ImprovementCandidateBuilder


def _ev(summary, sensitivity="internal"):
    return ContextEvidence(
        evidence_id="ev-repeat",
        source_session_id="session-repeat",
        source_kind="user_correction",
        scope="personal",
        summary=summary,
        sanitized_sample=summary[:80],
        sensitivity=sensitivity,
        applies_to=["self_improvement"],
        valid_from="2026-06-06T00:00:00Z",
        confidence=0.95,
        human_approved=True,
    )


def test_repeated_prior_correction_violation_proposes_memory_skill_and_test_updates():
    builder = ImprovementCandidateBuilder()
    candidates = builder.from_violation(
        violated_evidence=_ev("Sinria must convert repeated corrections into memory, skills, tests, and runbooks."),
        observed_action="Assistant acknowledged correction but did not update memory, skill, test, or runbook.",
        repeat_count=2,
    )

    actions = {candidate.action for candidate in candidates}
    assert {"memory_replace", "skill_patch", "test_addition", "runbook_update"} <= actions
    assert all(candidate.human_review_required is False for candidate in candidates if candidate.action == "memory_replace")


def test_clinical_or_org_policy_candidate_requires_human_review():
    builder = ImprovementCandidateBuilder()
    candidates = builder.from_violation(
        violated_evidence=_ev("Clinical context policy changed for patient consent evidence.", sensitivity="clinical"),
        observed_action="Assistant attempted to auto-apply clinical policy.",
        repeat_count=1,
    )

    assert candidates
    assert all(candidate.human_review_required for candidate in candidates)
