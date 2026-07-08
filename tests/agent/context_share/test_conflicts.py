from agent.context_share.conflicts import detect_evidence_conflicts, resolve_non_conflicting_evidence
from agent.context_share.evidence import ContextEvidence
from agent.context_share.intent_resolver import IntentResolver


def _evidence(
    evidence_id,
    summary,
    confidence=0.9,
    valid_from="2026-06-06T00:00:00Z",
    supersedes=None,
    applies_to=None,
):
    return ContextEvidence(
        evidence_id=evidence_id,
        source_session_id=f"session-{evidence_id}",
        source_kind="user_correction",
        scope="personal",
        summary=summary,
        sanitized_sample=summary,
        sensitivity="internal",
        applies_to=list(applies_to or ["sinria_identity"]),
        valid_from=valid_from,
        confidence=confidence,
        human_approved=True,
        supersedes=supersedes or [],
    )


def test_detects_known_contradictory_correction_pair():
    old = _evidence("old", "Use Hermes labels in user-facing artifacts.", confidence=0.7)
    new = _evidence("new", "Use Sinria-native labels and avoid Hermes residue in user-facing artifacts.", confidence=0.95)

    conflicts = detect_evidence_conflicts([old, new])

    assert conflicts
    assert conflicts[0].winning_evidence_id == "new"
    assert conflicts[0].losing_evidence_id == "old"


def test_resolver_omits_losing_conflicting_constraint_and_preserves_trace():
    old = _evidence("old", "Use Hermes labels in user-facing artifacts.", confidence=0.7)
    new = _evidence("new", "Use Sinria-native labels and avoid Hermes residue in user-facing artifacts.", confidence=0.95)
    resolver = IntentResolver(default_evidence=[old, new], include_durable=False)

    prompt = resolver.resolve("Sinria identity work", project="sinria").format_for_prompt()

    assert "Use Sinria-native labels" in prompt
    assert "Use Hermes labels" not in prompt
    assert "Conflict resolution" in prompt
    assert "new supersedes old" in prompt or "new wins over old" in prompt


def test_resolve_non_conflicting_evidence_honors_explicit_supersedes():
    old = _evidence("old", "Use older completion policy.", confidence=0.99)
    new = _evidence("new", "Use newer completion policy with browser verification.", confidence=0.8, supersedes=["old"])

    active, conflicts = resolve_non_conflicting_evidence([old, new])

    assert [item.evidence_id for item in active] == ["new"]
    assert not conflicts


def test_resolve_non_conflicting_evidence_honors_transitive_explicit_supersedes():
    old = _evidence("old", "Use original deployment lane.", confidence=0.99, applies_to=["deployment"])
    middle = _evidence(
        "middle",
        "Use temporary deployment lane.",
        confidence=0.92,
        valid_from="2026-06-10T00:00:00Z",
        supersedes=["old"],
        applies_to=["deployment"],
    )
    new = _evidence(
        "new",
        "Use current deployment lane.",
        confidence=0.85,
        valid_from="2026-07-05T00:00:00Z",
        supersedes=["middle"],
        applies_to=["deployment"],
    )

    active, conflicts = resolve_non_conflicting_evidence([old, middle, new])

    assert [item.evidence_id for item in active] == ["new"]
    assert not conflicts


def test_newer_decision_overrides_old_deploy_target_lane_without_explicit_supersedes():
    """Startup decisions change: newer approved corrections must beat old runtime guidance.

    The resolver cannot rely on the user repeating the latest lane separation.
    If old evidence says a GCP workflow may route through Vercel/stable runtime
    and newer evidence says to use an independent GCP repo instead, the old
    guidance must be suppressed even without a hand-authored supersedes list.
    """
    old = _evidence(
        "old-vercel-parity",
        "MedEvidence GCP work may use Vercel-equivalent route dispatch through /Users/tarokuroda/med_evi-2 as temporary parity.",
        confidence=0.95,
        valid_from="2026-06-15T00:00:00Z",
        applies_to=["medevidence", "gcp", "vercel_boundary"],
    )
    new = _evidence(
        "new-independent-gcp",
        "MedEvidence GCP work must use /Users/tarokuroda/medevidence-gcp as the independent implementation lane instead of importing, routing through, or modifying /Users/tarokuroda/med_evi-2 or the Vercel stable runtime.",
        confidence=0.88,
        valid_from="2026-07-05T00:00:00Z",
        applies_to=["medevidence", "gcp", "vercel_boundary"],
    )

    active, conflicts = resolve_non_conflicting_evidence([old, new])

    assert [item.evidence_id for item in active] == ["new-independent-gcp"]
    assert conflicts
    assert conflicts[0].winning_evidence_id == "new-independent-gcp"
    assert conflicts[0].losing_evidence_id == "old-vercel-parity"


def test_japanese_new_decision_overrides_old_lane_without_explicit_supersedes():
    old = _evidence(
        "old-medspot-default",
        "MedEvidenceのGCP作業では一時的にmed_evi-2を参照して同等経路を使う。",
        confidence=0.96,
        valid_from="2026-06-20T00:00:00Z",
        applies_to=["medevidence", "gcp", "repo_lane"],
    )
    new = _evidence(
        "new-gcp-only",
        "今後はMedEvidenceのGCP作業ではmed_evi-2ではなくmedevidence-gcpを必ず使い、Vercel stableへrouteしない。",
        confidence=0.82,
        valid_from="2026-07-05T00:00:00Z",
        applies_to=["medevidence", "gcp", "repo_lane"],
    )

    active, conflicts = resolve_non_conflicting_evidence([old, new])

    assert [item.evidence_id for item in active] == ["new-gcp-only"]
    assert conflicts[0].winning_evidence_id == "new-gcp-only"


def test_newer_additive_guidance_does_not_suppress_old_non_conflicting_constraint():
    old = _evidence(
        "old-browser-smoke",
        "MedEvidence GCP work requires non-PHI browser smoke after deploy.",
        confidence=0.95,
        valid_from="2026-06-20T00:00:00Z",
        applies_to=["medevidence", "gcp", "verification"],
    )
    new = _evidence(
        "new-log-smoke",
        "MedEvidence GCP work also requires Cloud Run log readback after deploy.",
        confidence=0.9,
        valid_from="2026-07-05T00:00:00Z",
        applies_to=["medevidence", "gcp", "verification"],
    )

    active, conflicts = resolve_non_conflicting_evidence([old, new])

    assert [item.evidence_id for item in active] == ["old-browser-smoke", "new-log-smoke"]
    assert not conflicts


def test_resolver_prompt_surfaces_decision_override_conflict_for_current_task():
    old = _evidence(
        "old-gcp-primary",
        "MedEvidence GCP primary smoke can dispatch through the Vercel stable search route.",
        confidence=0.95,
        valid_from="2026-06-15T00:00:00Z",
        applies_to=["medevidence", "gcp", "vercel_boundary"],
    )
    new = _evidence(
        "new-gcp-independent",
        "MedEvidence GCP work now uses the independent /Users/tarokuroda/medevidence-gcp repo; do not route to Vercel stable or /Users/tarokuroda/med_evi-2.",
        confidence=0.88,
        valid_from="2026-07-05T00:00:00Z",
        applies_to=["medevidence", "gcp", "vercel_boundary"],
    )
    resolver = IntentResolver(default_evidence=[old, new], include_durable=False)

    prompt = resolver.resolve("メドエビデンスGCP版の検索品質を改善して", platform="discord").format_for_prompt()

    assert "new-gcp-independent wins over old-gcp-primary" in prompt
    assert "independent /Users/tarokuroda/medevidence-gcp" in prompt
    assert "dispatch through the Vercel stable search route" not in prompt


def test_current_user_override_suppresses_stale_durable_guidance_before_it_is_saved():
    old = _evidence(
        "old-gcp-primary",
        "MedEvidence GCP primary smoke can dispatch through the Vercel stable search route and /Users/tarokuroda/med_evi-2.",
        confidence=0.99,
        valid_from="2026-06-15T00:00:00Z",
        applies_to=["medevidence", "gcp", "vercel_boundary"],
    )
    resolver = IntentResolver(default_evidence=[old], include_durable=False)

    prompt = resolver.resolve(
        "今後はMedEvidence GCP作業ではmed_evi-2ではなくmedevidence-gcpを使い、Vercel stableへrouteしないで検索品質を改善して",
        platform="discord",
    ).format_for_prompt()

    assert "Current user request overrides old-gcp-primary" in prompt
    assert "dispatch through the Vercel stable search route" not in prompt


def test_current_user_non_override_reminder_does_not_suppress_matching_constraint():
    old = _evidence(
        "old-browser-smoke",
        "MedEvidence GCP work requires browser smoke after deploy.",
        confidence=0.99,
        valid_from="2026-06-15T00:00:00Z",
        applies_to=["medevidence", "gcp", "verification"],
    )
    resolver = IntentResolver(default_evidence=[old], include_durable=False)

    prompt = resolver.resolve(
        "MedEvidence GCP作業でbrowser smokeを忘れないで改善して",
        platform="discord",
    ).format_for_prompt()

    assert "old-browser-smoke" in prompt
    assert "requires browser smoke" in prompt
