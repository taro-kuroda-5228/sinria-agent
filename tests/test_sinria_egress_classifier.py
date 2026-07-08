from sinria_egress import classify_external_egress


DEFAULT_EGRESS = {
    "mode": "ask",
    "confidential_external_send": "ask",
    "redact_secrets_before_external_send": True,
    "classify_lightweight": True,
}


def test_internal_destination_allows_confidential_content():
    decision = classify_external_egress(
        "internal_file",
        "社外秘: patient John Doe API_KEY=sk-test-placeholder",
        DEFAULT_EGRESS,
    )

    assert decision.external is False
    assert decision.likely_confidential is True
    assert decision.action == "allow"
    assert "internal" in decision.reason


def test_external_non_confidential_content_is_allowed_in_ask_mode():
    decision = classify_external_egress(
        "web_search",
        "latest Python pytest release notes",
        DEFAULT_EGRESS,
    )

    assert decision.external is True
    assert decision.likely_confidential is False
    assert decision.action == "allow"


def test_external_confidential_content_asks_in_ask_mode():
    decision = classify_external_egress(
        "model_provider",
        "社外秘の契約書です。password=example-secret-value",
        DEFAULT_EGRESS,
    )

    assert decision.external is True
    assert decision.likely_confidential is True
    assert decision.action == "ask"
    assert "confidential" in decision.reason.lower()


def test_external_confidential_content_blocks_in_block_mode():
    config = dict(DEFAULT_EGRESS, mode="block")

    decision = classify_external_egress(
        "messaging",
        "患者ID 12345 の検査結果を外部に送って",
        config,
    )

    assert decision.action == "block"


def test_external_confidential_content_allows_in_allow_mode():
    config = dict(DEFAULT_EGRESS, mode="allow")

    decision = classify_external_egress(
        "email",
        "confidential board memo",
        config,
    )

    assert decision.action == "allow"
