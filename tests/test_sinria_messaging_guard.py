from pathlib import Path

import pytest

from agent.sinria_egress import SinriaEgressBlocked, guard_messaging_egress


ASK_EGRESS = {
    "mode": "ask",
    "confidential_external_send": "ask",
    "redact_secrets_before_external_send": True,
    "classify_lightweight": True,
}


def test_messaging_guard_allows_non_confidential_external_message(tmp_path):
    decision = guard_messaging_egress(
        "discord:12345",
        "Public demo starts at 10:00",
        config=ASK_EGRESS,
        audit_path=Path(tmp_path) / "audit.jsonl",
    )

    assert decision.destination_type == "messaging"
    assert decision.external is True
    assert decision.likely_confidential is False
    assert decision.action == "allow"


def test_messaging_guard_asks_before_external_confidential_message_and_audits_safely(tmp_path):
    audit_path = Path(tmp_path) / "audit.jsonl"

    with pytest.raises(SinriaEgressBlocked):
        guard_messaging_egress(
            "slack:#general",
            "社外秘 password=example-secret を送ります",
            config=ASK_EGRESS,
            audit_path=audit_path,
        )

    audit = audit_path.read_text(encoding="utf-8")
    assert "messaging" in audit
    assert "ask" in audit
    assert "example-secret" not in audit
    assert "password=example-secret" not in audit


def test_messaging_guard_blocks_external_confidential_message_in_block_mode(tmp_path):
    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_messaging_egress(
            "email:team@example.com",
            "confidential board memo",
            config=dict(ASK_EGRESS, mode="block"),
            audit_path=Path(tmp_path) / "audit.jsonl",
        )

    assert exc.value.decision.action == "block"


def test_messaging_guard_allows_external_confidential_message_in_allow_mode(tmp_path):
    decision = guard_messaging_egress(
        "telegram:-1001",
        "confidential board memo",
        config=dict(ASK_EGRESS, mode="allow"),
        audit_path=Path(tmp_path) / "audit.jsonl",
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True
