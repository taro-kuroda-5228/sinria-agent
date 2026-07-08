
import pytest

from agent.context_share.evidence import ContextEvidence, EvidenceLedger, SensitiveContextError


def _evidence(evidence_id="ev-1", summary="Use Sinria-native labels", **overrides):
    data = dict(
        evidence_id=evidence_id,
        source_session_id="session-1",
        source_kind="user_correction",
        scope="personal",
        summary=summary,
        sanitized_sample="Sinria-native labels",
        sensitivity="internal",
        applies_to=["sinria_identity"],
        valid_from="2026-06-06T00:00:00Z",
        confidence=0.9,
        human_approved=True,
    )
    data.update(overrides)
    return ContextEvidence(**data)


def test_evidence_rejects_raw_secret_like_samples():
    with pytest.raises(SensitiveContextError):
        _evidence(
            evidence_id="ev-secret",
            sanitized_sample="Authorization: Bearer [REDACTED_SK]",
        )


def test_evidence_rejects_raw_sensitive_summary_even_when_marked_internal():
    with pytest.raises(SensitiveContextError):
        _evidence(summary="Correct prior behavior for patient MRN-123456")


def test_ledger_preserves_source_pointer_and_supersedes_old_evidence():
    ledger = EvidenceLedger()
    old = _evidence("ev-old", "Prefer old public labels")
    new = _evidence(
        "ev-new",
        "Use Sinria-native paths and labels; avoid legacy residue in user-facing artifacts.",
        applies_to=["identity"],
        supersedes=["ev-old"],
        source_session_id="session-new",
        sanitized_sample="Sinria-native paths/labels only",
        confidence=0.95,
    )

    ledger.add(old)
    ledger.add(new)

    active = ledger.active_for("identity")
    assert [item.evidence_id for item in active] == ["ev-new"]
    assert active[0].source_session_id == "session-new"


def test_ledger_filters_unapproved_expired_and_future_evidence():
    ledger = EvidenceLedger([
        _evidence("ev-unapproved", "Unapproved correction", human_approved=False),
        _evidence("ev-expired", "Expired correction", expires_at="2020-01-01T00:00:00Z"),
        _evidence("ev-future", "Future correction", valid_from="2999-01-01T00:00:00Z"),
        _evidence("ev-active", "Active correction"),
    ])

    assert [item.evidence_id for item in ledger.active_for("sinria_identity")] == ["ev-active"]


def test_ledger_applies_transitive_supersedes_chain():
    old = _evidence("ev-old", "Original runtime lane", applies_to=["medevidence"])
    middle = _evidence(
        "ev-middle",
        "Temporary runtime lane",
        applies_to=["medevidence"],
        supersedes=["ev-old"],
        valid_from="2026-06-10T00:00:00Z",
    )
    newest = _evidence(
        "ev-newest",
        "Independent runtime lane",
        applies_to=["medevidence"],
        supersedes=["ev-middle"],
        valid_from="2026-07-05T00:00:00Z",
    )

    ledger = EvidenceLedger([old, middle, newest])

    assert [item.evidence_id for item in ledger.active_for("medevidence")] == ["ev-newest"]
