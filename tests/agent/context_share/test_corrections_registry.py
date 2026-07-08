
from agent.context_share.corrections import CorrectionRegistry
from agent.context_share.evidence import ContextEvidence


def _evidence(evidence_id, summary, *, kind="user_correction", applies_to=("completion",), supersedes=None):
    return ContextEvidence(
        evidence_id=evidence_id,
        source_session_id="session-1",
        source_kind=kind,
        scope="personal",
        summary=summary,
        sanitized_sample=summary[:80],
        sensitivity="internal",
        applies_to=list(applies_to),
        valid_from="2026-06-06T00:00:00Z",
        supersedes=supersedes,
        confidence=0.9,
        human_approved=True,
    )


def test_promotes_durable_correction_but_rejects_stale_task_progress():
    registry = CorrectionRegistry()
    promoted = registry.promote(_evidence("ev-1", "Do not claim completion without tests/build/API/UI/browser verification when relevant."))
    stale = registry.promote(_evidence("ev-pr", "PR #10 was merged and phase 3 is done", kind="workflow_outcome"))

    assert promoted is not None
    assert promoted.constraint_text.startswith("Do not claim completion")
    assert stale is None


def test_superseded_correction_is_not_returned_as_active_constraint():
    registry = CorrectionRegistry()
    registry.promote(_evidence("ev-old", "Use old Hermes labels", applies_to=("identity",)))
    registry.promote(_evidence("ev-new", "Use Sinria-native paths/labels; avoid Hermes residue.", applies_to=("identity",), supersedes=["ev-old"]))

    constraints = registry.active_constraints_for("identity")
    assert [constraint.evidence_id for constraint in constraints] == ["ev-new"]
