import pytest
from datetime import datetime, timedelta, timezone
from agent.company_context.policy import WorkspaceIdentity
from agent.company_context.contracts import AuthContext, canonical_workspace_id, ActionTier
from agent.company_context.google_auth import GoogleAuthProfile, OAuthProfileStore
from agent.company_context.action_policy import ActionPolicy, ActionRequest
from agent.company_context.data_policy import Classification, allow_egress
from agent.company_context.governance import may_approve, Lifecycle
from agent.company_context.audit import ImmutableAudit, release_allowed
from agent.company_context.flags import FeatureFlags

def test_scope_and_alias_fail_closed():
    ctx=AuthContext(WorkspaceIdentity("medical-horizon","taro","i1"),"oauth",frozenset({"workspace_read"}))
    assert canonical_workspace_id("medical_horizon")=="medical-horizon"
    with pytest.raises(PermissionError): ctx.require("other")

def test_oauth_metadata_never_accepts_credentials():
    p=GoogleAuthProfile("p1","subject-taro","taro@example.invalid",frozenset({"workspace_read"}))
    s=OAuthProfileStore(); s.save(p); assert s.get("p1").read_only
    with pytest.raises(ValueError): s.save(p, access_token="do-not-store")

def test_action_deny_default_and_dry_run_boundary():
    policy=ActionPolicy(); assert policy.enforce_before_provider(ActionRequest("draft",ActionTier.A0,"medical-horizon",dry_run=True)) is False
    with pytest.raises(PermissionError): policy.authorize(ActionRequest("unknown",ActionTier.A0,"medical-horizon"))
    with pytest.raises(PermissionError): policy.authorize(ActionRequest("send",ActionTier.A4,"medical-horizon",resource_id="r"))

def test_sensitive_egress_is_denied():
    for c in (Classification.PHI, Classification.Secret, Classification.Unknown): assert not allow_egress(c,"remote_model",approved_provider=True).allowed

def test_jml_and_separation():
    assert not may_approve(proposer="i1",approver="i1",approver_role="reviewer")
    l=Lifecycle(); l.leaver(); assert not l.token_valid and not l.retrieval_enabled

def test_audit_tamper_and_release_gate():
    a=ImmutableAudit(); a.append("grant",{"actor":"taro"}); assert a.verify(); a.events[0].metadata["actor"]="evil"; assert not a.verify(); assert not release_allowed(audit_ok=False,required_gates={"tests":True},tier=4)

def test_kill_switch_precedes_flags():
    f=FeatureFlags({"growth_action_enabled":True}); assert f.enabled("growth_action_enabled"); f.kill(); assert not f.enabled("growth_action_enabled")
