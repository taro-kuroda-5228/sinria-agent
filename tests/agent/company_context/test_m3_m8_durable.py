"""Restart/retry/fault-injection coverage for the durable M3-M8 path."""
import pytest
from agent.company_context.operations import ApprovalBindingError, ContextLedger, FakeProvider, QuotaExceeded

def test_restart_retry_provider_is_idempotent(tmp_path):
    db=tmp_path/"ledger.db"; l=ContextLedger(db); l.enqueue("p1","publish",{},key="k1")
    lease=l.claim("p1","w1"); assert lease
    l.close(); l=ContextLedger(db); l.finish(lease); provider=FakeProvider()
    provider=FakeProvider(); provider.fail_next="drop_response"
    try: l.publish("p1",provider,"a1","hello",key="lost")
    except TimeoutError: pass
    assert l.publish("p1",provider,"a1","hello",key="lost")
    first=l.publish("p1",provider,"a1","hello",key="provider-k")
    assert l.publish("p1",provider,"a1","hello",key="provider-k")==first
    assert provider.writes==2

def test_lease_expiry_and_profile_isolation(tmp_path):
    now=[100.0]; l=ContextLedger(tmp_path/"x.db",clock=lambda:now[0])
    l.enqueue("p1","x",{},key="x"); assert l.claim("p1","a",ttl=2)
    assert l.claim("p1","b") is None; now[0]=103; assert l.claim("p1","b")
    assert l.claim("p2","c") is None

def test_approval_binding_canary_promotion_and_rollback(tmp_path):
    l=ContextLedger(tmp_path/"x.db"); l.propose("p","pr","one"); approval=l.approve("p","pr",actor="u")
    assert l.activate("p","pr",approval,canary=True)=="pr:1"
    with pytest.raises(ApprovalBindingError, match="consumed|invalid"):
        l.activate("p", "pr", approval, canary=True)
    l.propose("p","pr2","two"); approval2=l.approve("p","pr2",actor="u"); l.activate("p","pr2",approval2)
    assert l.rollback("p")=="pr:1"
    try: l.continuation(approval,"other","pr")
    except ApprovalBindingError: pass
    else: assert False

def test_hold_retention_quota_jml(tmp_path):
    l=ContextLedger(tmp_path/"x.db"); l.set_quota("p",1); l.reserve("p",1)
    try: l.reserve("p",1)
    except QuotaExceeded: pass
    else: assert False
    l.retain("p","keep",reason="legal"); l._event("p","old","drop",{}); l._event("p","old","keep",{})
    assert l.purge("p",before=10**20)>=1; l.jml("p","u","leaver")
