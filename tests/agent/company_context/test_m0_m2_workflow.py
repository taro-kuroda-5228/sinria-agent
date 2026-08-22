from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.company_context import OAuthLifecycle, run_synthetic_full_loop


def test_oauth_lifecycle_pkce_scope_escalation_rotation_and_revoke():
    oauth = OAuthLifecycle(owner_id="member")
    verifier = "workflow-verifier"
    start = oauth.begin({"workspace_read"}, verifier=verifier)
    with pytest.raises(PermissionError):
        oauth.callback(state=start["state"], verifier="wrong", code="code")
    # Failed callback consumes state; a fresh authorization is required.
    start = oauth.begin({"workspace_read"}, verifier=verifier)
    grant = oauth.callback(state=start["state"], verifier=verifier, code="code")
    assert grant["scopes"] == frozenset({"workspace_read"})
    with pytest.raises(PermissionError):
        oauth.require("gmail_read")
    oauth.escalate({"gmail_read"})
    assert oauth.complete_escalation()["scopes"] == frozenset({"workspace_read", "gmail_read"})
    assert oauth.rotate() == 3
    oauth.revoke()
    with pytest.raises(PermissionError):
        oauth.require()


def test_full_loop_proves_real_entrypoint_and_fail_closed_remote(tmp_path: Path):
    result = run_synthetic_full_loop(tmp_path, remote_available=True)
    assert result["revision"] == "r2"
    assert "revised canonical" in result["context"]
    assert result["gmail_state"] == "sent"
    assert result["canonical_visible"] is False
    assert result["retrieval_after_revoke"] == ""
    assert result["audit_ok"] is True
    assert "drive.ingest" in result["audit_events"]

    denied = run_synthetic_full_loop(tmp_path / "denied", remote_available=False)
    assert denied["context"] == ""
    assert "egress.denied" in denied["audit_events"]
    assert denied["retrieval_after_revoke"] == ""
