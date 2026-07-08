from hermes_cli.config import DEFAULT_CONFIG


def test_sinria_default_config_has_minimal_external_egress_boundary():
    sinria = DEFAULT_CONFIG["sinria"]

    assert sinria["product_name"] == "Sinria"
    assert sinria["egress"] == {
        "mode": "ask",
        "confidential_external_send": "ask",
        "redact_secrets_before_external_send": True,
        "classify_lightweight": True,
    }


def test_sinria_default_policy_allows_internal_confidential_work():
    policy = DEFAULT_CONFIG["sinria"]["policy"]

    assert policy["internal_confidential_data"] == "allowed"
    assert policy["internal_storage"] == "allowed"
    assert policy["protects"] == "external_leakage"


def test_sinria_default_config_includes_commercial_boundary_control_layer():
    boundary = DEFAULT_CONFIG["sinria"]["boundary_control"]

    assert set(boundary["deployment_modes"]) >= {"full_on_prem", "hybrid_confidential", "cloud_enhanced"}
    assert boundary["data_policy_matrix"]["public"]["external_egress"] == "allow"
    assert boundary["data_policy_matrix"]["internal"]["approval"] == "required"
    assert boundary["data_policy_matrix"]["phi_pii"]["model_route"] == "local_only"
    assert boundary["data_policy_matrix"]["credential"]["external_egress"] == "block"
    assert boundary["provider_trust_registry"]["local_vllm"]["external_egress"] is False
    assert boundary["provider_trust_registry"]["openai_enterprise"]["requires_sanitization"] is True
