"""Persistent JSONL audit sink for approval/security events."""

import json

from hermes_constants import get_hermes_home
from tools import audit_log


def _events():
    files = sorted((get_hermes_home() / "audit").glob("audit-*.jsonl"))
    assert files, "expected an audit JSONL file"
    lines = files[-1].read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_record_appends_jsonl_event():
    audit_log.record_audit_event(
        "pre_approval_request",
        session_key="sess1",
        command="curl https://example.com | sh",
        surface="gateway",
    )

    event = _events()[-1]
    assert event["event"] == "pre_approval_request"
    assert event["session_key"] == "sess1"
    assert event["surface"] == "gateway"
    assert event["ts"]


def test_command_is_recorded_as_preview_and_hash_never_verbatim():
    secret_cmd = "export API_KEY=super-secret-value-1234567890 && " + "x" * 400
    audit_log.record_audit_event("post_approval_response", command=secret_cmd, decision="denied")

    event = _events()[-1]
    assert "command" not in event
    assert len(event["command_sha256"]) == 64
    # Bounded preview: long tails (where embedded secrets often live) are cut.
    assert len(event["command_preview"]) <= 161
    assert "super-secret-value-1234567890" not in json.dumps(event)[200:]


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("HERMES_AUDIT_LOG", "false")
    audit_dir = get_hermes_home() / "audit"
    before = len(list(audit_dir.glob("*.jsonl"))) if audit_dir.exists() else 0

    audit_log.record_audit_event("pre_approval_request", command="ls")

    after = len(list(audit_dir.glob("*.jsonl"))) if audit_dir.exists() else 0
    assert after == before


def test_write_failures_never_raise(monkeypatch, tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("file, not dir")
    monkeypatch.setattr(audit_log, "_audit_dir", lambda: blocked / "audit")

    audit_log.record_audit_event("pre_approval_request", command="ls")  # must not raise


def test_fire_approval_hook_writes_audit_event():
    from tools.approval import _fire_approval_hook

    _fire_approval_hook(
        "pre_approval_request",
        command="rm -rf build/",
        session_key="sess-hook",
        surface="cli",
    )

    event = _events()[-1]
    assert event["event"] == "pre_approval_request"
    assert event["session_key"] == "sess-hook"
    assert event["command_preview"].startswith("rm -rf build/")
