from pathlib import Path

import pytest

from agent.sinria_egress import SinriaEgressBlocked, guard_web_query_egress


ASK_EGRESS = {
    "mode": "ask",
    "confidential_external_send": "ask",
    "redact_secrets_before_external_send": True,
    "classify_lightweight": True,
}


def test_external_web_search_non_sensitive_query_is_allowed():
    decision = guard_web_query_egress("web_search", "latest FDA AI guidance", config=ASK_EGRESS)

    assert decision.destination_type == "web_search"
    assert decision.external is True
    assert decision.likely_confidential is False
    assert decision.action == "allow"


def test_external_web_search_confidential_query_is_blocked_for_approval(tmp_path: Path):
    audit_path = tmp_path / "egress.jsonl"

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_web_query_egress(
            "web_search",
            "患者ID 12345 の社外秘 契約書 token=secret-value を検索",
            config=ASK_EGRESS,
            audit_path=audit_path,
        )

    assert exc.value.decision.action == "ask"
    assert exc.value.decision.destination_type == "web_search"
    audit = audit_path.read_text(encoding="utf-8")
    assert "secret-value" not in audit
    assert "[REDACTED]" in audit


def test_browser_navigation_secret_like_url_is_blocked(tmp_path: Path):
    audit_path = tmp_path / "browser-egress.jsonl"

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_web_query_egress(
            "browser",
            "https://example.com/callback?api_key=secret-value",
            config=ASK_EGRESS,
            audit_path=audit_path,
        )

    assert exc.value.decision.destination_type == "browser"
    assert exc.value.decision.action == "ask"
    assert "secret-value" not in audit_path.read_text(encoding="utf-8")


def test_internal_web_query_destination_is_allowed_even_if_confidential():
    decision = guard_web_query_egress(
        "internal_search",
        "社外秘 token=secret-value",
        config=ASK_EGRESS,
    )

    assert decision.external is False
    assert decision.action == "allow"
