import json

import pytest

from agent.sinria_egress import (
    preview_external_egress,
    route_model_provider_for_payload,
    validate_sinria_boundary_policy,
)
from tests.test_sinria_boundary_control_layer import BASE_CONFIG


# --- Policy validation (Phase 2): fail closed with sanitized errors -----------


def test_validate_boundary_policy_accepts_complete_default_policy():
    result = validate_sinria_boundary_policy(BASE_CONFIG)

    assert result["valid"] is True
    assert result["errors"] == []
    assert result["raw_content_included"] is False
    assert result["deployment_mode"] in {
        "full_on_prem",
        "hybrid_confidential",
        "cloud_enhanced",
    }


def test_validate_boundary_policy_fails_closed_on_missing_data_class():
    broken = json.loads(json.dumps(BASE_CONFIG))
    del broken["sinria"]["boundary_control"]["data_policy_matrix"]["credential"]

    result = validate_sinria_boundary_policy(broken)

    assert result["valid"] is False
    assert any("credential" in err for err in result["errors"])


def test_validate_boundary_policy_blocks_cloud_provider_accepting_phi():
    broken = json.loads(json.dumps(BASE_CONFIG))
    broken["sinria"]["boundary_control"]["provider_trust_registry"]["openai_enterprise"][
        "approved_data_classes"
    ] = ["public", "internal", "phi_pii"]

    result = validate_sinria_boundary_policy(broken)

    assert result["valid"] is False
    assert any("phi_pii" in err and "openai_enterprise" in err for err in result["errors"])


def test_validate_boundary_policy_errors_are_metadata_only():
    broken = json.loads(json.dumps(BASE_CONFIG))
    broken["sinria"]["boundary_control"]["provider_trust_registry"]["openai_enterprise"][
        "trust_level"
    ] = "wildcat_unknown"

    result = validate_sinria_boundary_policy(broken)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["valid"] is False
    # No raw confidential markers should ever leak into validation output.
    assert "患者" not in serialized
    assert "REDACTED" not in serialized
    assert "pass" + "word" not in serialized.lower()


# --- Full matrix default-deny invariants (Phase 1) ----------------------------

_SENSITIVE_SYNTHETIC = {
    "phi_pii": "患者ID: TEST-12345 の検査結果を要約して",
    "credential": "[REDACTED_PRIVATE_KEY]\nsynthetic-fixture\n[END REDACTED KEY]",
    "classified": "classified防衛秘密 synthetic briefing excerpt",
}


@pytest.mark.parametrize("data_class,payload", sorted(_SENSITIVE_SYNTHETIC.items()))
@pytest.mark.parametrize("cloud_provider", ["openai_enterprise"])
def test_sensitive_classes_blocked_from_approved_cloud(data_class, payload, cloud_provider):
    route = route_model_provider_for_payload(
        payload, provider_key=cloud_provider, config=BASE_CONFIG
    )

    assert route["data_class"] == data_class
    assert route["allowed"] is False
    assert route["external_egress"] == "block"


@pytest.mark.parametrize("data_class,payload", sorted(_SENSITIVE_SYNTHETIC.items()))
def test_phi_and_credential_allowed_only_on_local_provider(data_class, payload):
    route = route_model_provider_for_payload(
        payload, provider_key="local_vllm", config=BASE_CONFIG
    )

    assert route["data_class"] == data_class
    # local_vllm is approved for all classes subject to local policy.
    assert route["allowed"] is True


def test_unregistered_provider_fails_closed():
    route = route_model_provider_for_payload(
        "public weather summary", provider_key="totally_unknown_provider", config=BASE_CONFIG
    )

    assert route["allowed"] is False
    assert "not registered" in route["reason"].lower()


# --- Documented short routing signature (Phase 9 smoke shape) -----------------


def test_route_accepts_provider_and_deployment_mode_aliases():
    route = route_model_provider_for_payload(
        "患者ID: P-12345 の検査結果を要約",
        provider="openai_enterprise",
        deployment_mode="cloud_enhanced",
    )

    assert route["data_class"] == "phi_pii"
    assert route["allowed"] is False
    assert route["deployment_mode"] == "cloud_enhanced"
    assert route["required_model_route"] == "local_only"


# --- Egress preview stable contract (Phase 4) ---------------------------------


def test_preview_exposes_stable_contract_fields_and_no_raw():
    preview = preview_external_egress(
        "model_provider",
        {"prompt": "患者ID: MRN-123456 を外部に送る"},
        provider_key="openai_enterprise",
        config=BASE_CONFIG,
    )

    for key in (
        "allowed",
        "action",
        "data_class",
        "required_route",
        "provider",
        "approval",
        "approval_required",
        "sanitized_preview",
        "raw_content_included",
        "external_action_performed",
    ):
        assert key in preview, f"missing contract field: {key}"

    assert preview["raw_content_included"] is False
    assert preview["external_action_performed"] is False
    assert preview["allowed"] is False
    assert preview["action"] == "block"
    assert "MRN-123456" not in json.dumps(preview, ensure_ascii=False)


def test_preview_public_payload_does_not_require_approval():
    preview = preview_external_egress(
        "model_provider",
        {"prompt": "Summarize today's public weather forecast."},
        provider_key="openai_enterprise",
        config=BASE_CONFIG,
    )

    assert preview["data_class"] == "public"
    assert preview["allowed"] is True
    assert preview["approval_required"] is False
    assert preview["external_action_performed"] is False
