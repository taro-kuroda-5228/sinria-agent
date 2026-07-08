"""Recall quality tests: CJK-aware search and relevance-ranked injection.

Prior corrections written in Japanese must be retrievable from Japanese
requests (whitespace tokenization alone cannot match unsegmented CJK text),
and evidence matched to the current task must outrank generic defaults under
the 8-constraint prompt budget.
"""

from agent.context_share.evidence import ContextEvidence, EvidenceLedger
from agent.context_share.intent_resolver import IntentResolver


def _ev(evidence_id, summary, applies_to, *, confidence=0.9, valid_from="2026-06-06T00:00:00Z", sample=None):
    return ContextEvidence(
        evidence_id=evidence_id,
        source_session_id="session-recall",
        source_kind="user_correction",
        scope="personal",
        summary=summary,
        sanitized_sample=sample or summary[:80],
        sensitivity="internal",
        applies_to=applies_to,
        valid_from=valid_from,
        confidence=confidence,
        human_approved=True,
    )


def test_search_matches_japanese_query_via_cjk_bigrams():
    ledger = EvidenceLedger([
        _ev(
            "ev-meeting",
            "Prior user correction: 議事録はDriveの議事録共有ドライブのみを正とし、重複フォルダを作らない。",
            ["meeting_memory", "user_correction_capture"],
        ),
        _ev("ev-unrelated", "Use Sinria-native labels in artifacts.", ["sinria_identity"]),
    ])

    results = ledger.search("議事録の取り込み先を確認して整理して")

    assert [item.evidence_id for item in results] == ["ev-meeting"]


def test_search_single_shared_cjk_bigram_is_not_enough():
    ledger = EvidenceLedger([
        _ev(
            "ev-weak",
            "Prior user correction: 会議の予定は必ずカレンダーを確認する。",
            ["calendar"],
        ),
    ])

    # Only 「確認」 overlaps — one bigram alone must not count as a match.
    assert ledger.search("デプロイの状態を確認") == []


def test_search_ascii_terms_still_match():
    ledger = EvidenceLedger([
        _ev("ev-ascii", "MedEvidence worker parity requires non-PHI smoke verification.", ["medevidence"]),
    ])

    results = ledger.search("verify medevidence worker parity")

    assert [item.evidence_id for item in results] == ["ev-ascii"]


def test_project_matched_evidence_outranks_generic_defaults_under_cap():
    generic = [
        _ev(f"ev-generic-{i}", f"Generic Sinria context share policy number {i} for org safety.", ["context_share"])
        for i in range(9)
    ]
    project_item = _ev(
        "ev-medspot-lesson",
        "MedSpot credentialing uploads must accept only 医師免許証 and 保険医登録票.",
        ["medspot", "credentialing"],
        confidence=0.86,
    )
    resolver = IntentResolver(ledger=EvidenceLedger([*generic, project_item]))

    result = resolver.resolve("MedSpotのcredentialingのコンテキストを踏まえて実装して", project="medspot")

    assert "ev-medspot-lesson" in result.retrieval_evidence_ids
    top8 = result.applicable_constraints[:8]
    assert any("MedSpot credentialing uploads" in constraint for constraint in top8)
    # The project-matched lesson must beat same-or-higher-confidence generic rows.
    assert result.applicable_constraints.index(project_item.summary) == 0


def test_newer_evidence_outranks_older_at_equal_relevance_and_confidence():
    ledger = EvidenceLedger([
        _ev("ev-old", "MedSpot rule variant A for intake ordering.", ["medspot"], valid_from="2026-01-01T00:00:00Z"),
        _ev("ev-new", "MedSpot rule variant B for intake ordering.", ["medspot"], valid_from="2026-07-01T00:00:00Z"),
    ])
    resolver = IntentResolver(ledger=ledger)

    result = resolver.resolve("MedSpotのintakeを実装して", project="medspot")

    ids = result.retrieval_evidence_ids
    assert ids.index("ev-new") < ids.index("ev-old")
