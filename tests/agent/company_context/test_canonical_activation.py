from agent.company_context.canonical_activation import (
    ARTIFACTS, CASConflict, CanonicalActivation, EligibilityError, Proposal,
)


def bundle(pid="p", profile="profile", team="team", revision=1, **flags):
    return Proposal(pid, profile, team, revision, {a: f"{a} revision {revision}" for a in ARTIFACTS}, **flags)


def test_canonical_activation_reindexes_next_turn_and_revoke_restores(tmp_path):
    sent = []
    service = CanonicalActivation(tmp_path, company_os_sink=sent.append)
    with pytest.raises(EligibilityError):
        service.activate(bundle(approved=True, replay_success=False, canary_success=True))
    first = service.activate(bundle(approved=True, replay_success=True, canary_success=True))
    assert first.revision == 1
    assert service.decide("profile", "team", "skill") == "revision=1"
    second = service.activate(bundle("p2", revision=2, approved=True, replay_success=True, canary_success=True))
    assert service.decide("profile", "team", "revision 2") == "revision=2"
    service.revoke("profile", "team", 2)
    assert service.decide("profile", "team", "revision 1") == "revision=1"
    assert len(sent) == 3
    assert all("artifact" not in payload or all("body" not in str(v) for v in payload["artifact"].values()) for payload in sent)


def test_manifest_chain_scope_and_idempotency(tmp_path):
    service = CanonicalActivation(tmp_path)
    result = service.activate(bundle(approved=True, replay_success=True, canary_success=True))
    assert service.activate(bundle(approved=True, replay_success=True, canary_success=True)).manifest_hash == result.manifest_hash
    with pytest.raises(CASConflict):
        service.activate(bundle("p2", revision=2, approved=True, replay_success=True, canary_success=True), expected_head="bad")
    assert service.verify("profile", "team")["chain_valid"]
    assert service.decide("other", "team", "skill") == "no-canonical-context"


import pytest
