from pathlib import Path

from agent.sinria_egress import EgressDecision, write_egress_audit


def test_egress_audit_redacts_multiple_secret_shapes(tmp_path: Path):
    audit_path = tmp_path / "sinria-egress-audit.jsonl"
    decision = EgressDecision(
        destination_type="messaging",
        external=True,
        likely_confidential=True,
        action="ask",
        reason="confidential-looking content at external egress boundary",
    )

    content = (
        "password=super-secret token=abc123 patient_id=P-12345 sk-test-secret-value ")
    content += "患者ID 99-XYZ 社外秘"

    write_egress_audit(decision, content, audit_path=audit_path)

    audit = audit_path.read_text(encoding="utf-8")
    assert "super-secret" not in audit
    assert "abc123" not in audit
    assert "P-12345" not in audit
    assert "sk-test-secret-value" not in audit
    assert "99-XYZ" not in audit
    assert "[REDACTED]" in audit
    assert "[REDACTED_SK]" in audit
    assert '"content_sha256"' in audit
