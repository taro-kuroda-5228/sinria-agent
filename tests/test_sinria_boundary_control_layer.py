import json
from types import SimpleNamespace

import pytest

from agent.sinria_egress import (
    SinriaEgressBlocked,
    classify_sinria_data_class,
    export_sinria_boundary_compliance_report,
    guard_model_provider_egress,
    preview_external_egress,
    resolve_sinria_boundary_control,
    route_model_provider_for_payload,
)


BASE_CONFIG = {
    "sinria": {
        "policy": {
            "active_profile": "hybrid_confidential",
            "profiles": {
                "hybrid_confidential": {
                    "deployment_mode": "hybrid_confidential",
                    "provider_trust": "approved_cloud",
                }
            },
        },
        "boundary_control": {
            "deployment_modes": {
                "full_on_prem": {
                    "external_model_egress": "block",
                    "network_egress": "deny_by_default",
                    "allowed_provider_trust": ["local_only", "sovereign_private"],
                    "human_approval_required": True,
                },
                "hybrid_confidential": {
                    "external_model_egress": "sanitized_only",
                    "network_egress": "allowlist",
                    "allowed_provider_trust": ["local_only", "approved_cloud"],
                    "human_approval_required": True,
                },
                "cloud_enhanced": {
                    "external_model_egress": "allow_public_low_risk",
                    "network_egress": "allowlist",
                    "allowed_provider_trust": ["local_only", "approved_cloud", "trusted_frontier"],
                    "human_approval_required": False,
                },
            },
            "data_policy_matrix": {
                "public": {
                    "external_egress": "allow",
                    "model_route": "approved_cloud_or_local",
                    "approval": "not_required",
                    "audit": True,
                },
                "internal": {
                    "external_egress": "ask",
                    "model_route": "approved_cloud_or_local",
                    "approval": "required",
                    "audit": True,
                },
                "phi_pii": {
                    "external_egress": "block",
                    "model_route": "local_only",
                    "approval": "clinical_or_security_required",
                    "audit": True,
                },
                "credential": {
                    "external_egress": "block",
                    "model_route": "no_model_egress",
                    "approval": "not_permitted",
                    "audit": True,
                },
                "classified": {
                    "external_egress": "block",
                    "model_route": "air_gapped_only",
                    "approval": "security_required",
                    "audit": True,
                },
            },
            "provider_trust_registry": {
                "local_vllm": {
                    "trust_level": "local_only",
                    "external_egress": False,
                    "approved_data_classes": ["public", "internal", "phi_pii", "credential", "classified"],
                    "training_use": False,
                    "retention": "local_policy",
                },
                "openai_enterprise": {
                    "trust_level": "approved_cloud",
                    "external_egress": True,
                    "approved_data_classes": ["public", "internal"],
                    "training_use": False,
                    "retention": "contract_defined",
                    "requires_sanitization": True,
                },
            },
        },
    }
}


def test_resolve_boundary_control_exposes_commercial_policy_matrix():
    boundary = resolve_sinria_boundary_control(BASE_CONFIG)

    assert boundary["active_profile"] == "hybrid_confidential"
    assert boundary["deployment_mode"] == "hybrid_confidential"
    assert set(boundary["deployment_modes"]) >= {"full_on_prem", "hybrid_confidential", "cloud_enhanced"}
    assert boundary["data_policy_matrix"]["phi_pii"]["model_route"] == "local_only"
    assert boundary["data_policy_matrix"]["credential"]["external_egress"] == "block"
    assert boundary["provider_trust_registry"]["openai_enterprise"]["requires_sanitization"] is True


def test_model_routing_blocks_phi_from_approved_cloud_provider():
    route = route_model_provider_for_payload(
        "患者ID: MRN-123456 の検査結果を要約して",
        provider_key="openai_enterprise",
        config=BASE_CONFIG,
    )

    assert route["data_class"] == "phi_pii"
    assert route["allowed"] is False
    assert route["required_model_route"] == "local_only"
    assert route["external_egress"] == "block"
    assert "not approved" in route["reason"].lower()


def test_model_routing_blocks_credentials_from_approved_cloud_provider():
    route = route_model_provider_for_payload(
        "[REDACTED_PRIVATE_KEY]\nsynthetic-fixture\n[END REDACTED KEY]",
        provider_key="openai_enterprise",
        config=BASE_CONFIG,
    )

    assert route["data_class"] == "credential"
    assert route["allowed"] is False
    assert route["required_model_route"] == "no_model_egress"
    assert route["external_egress"] == "block"


def test_model_routing_allows_phi_and_credentials_only_on_local_provider():
    phi_route = route_model_provider_for_payload(
        "患者ID: TEST-12345 の検査結果を要約して",
        provider_key="local_vllm",
        config=BASE_CONFIG,
    )
    credential_route = route_model_provider_for_payload(
        "[REDACTED_PRIVATE_KEY]\nsynthetic-fixture\n[END REDACTED KEY]",
        provider_key="local_vllm",
        config=BASE_CONFIG,
    )

    assert phi_route["data_class"] == "phi_pii"
    assert phi_route["allowed"] is True
    assert credential_route["data_class"] == "credential"
    assert credential_route["allowed"] is True


def test_model_routing_allows_internal_only_as_sanitized_approved_cloud():
    route = route_model_provider_for_payload(
        "confidential internal Q3 operating memo without identifiers",
        provider_key="openai_enterprise",
        config=BASE_CONFIG,
    )

    assert route["data_class"] == "internal"
    assert route["allowed"] is True
    assert route["requires_sanitization"] is True
    assert route["approval"] == "required"


def test_egress_preview_is_sanitized_and_structured_for_human_review():
    preview = preview_external_egress(
        "model_provider",
        {"prompt": "患者ID: MRN-123456 を外部に送る"},
        provider_key="openai_enterprise",
        config=BASE_CONFIG,
    )

    serialized = json.dumps(preview, ensure_ascii=False)
    assert preview["destination_type"] == "model_provider"
    assert preview["data_class"] in {"phi_pii", "credential"}
    assert preview["raw_content_included"] is False
    assert "MRN-123456" not in serialized
    assert "raw sensitive fixture" not in serialized
    assert "[REDACTED]" in serialized
    assert preview["decision"]["action"] == "block"


def test_compliance_report_exports_no_raw_content_and_actionable_controls():
    report = export_sinria_boundary_compliance_report(BASE_CONFIG)

    assert report["product"] == "Sinria Boundary Control Layer"
    assert report["active_profile"] == "hybrid_confidential"
    assert report["raw_content_included"] is False
    assert report["controls"] >= [
        "deployment_mode_profiles",
        "data_policy_matrix",
        "provider_trust_registry",
        "egress_preview",
        "audit_metadata_only",
    ]
    assert report["data_policy_matrix"]["phi_pii"]["external_egress"] == "block"
    assert report["provider_trust_registry"]["local_vllm"]["external_egress"] is False


def test_model_provider_guard_uses_boundary_registry_to_block_phi_from_approved_cloud(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    agent = SimpleNamespace(
        base_url="https://api.openai.com/v1",
        provider="openai",
        model="gpt-enterprise-synthetic",
        sinria_provider_key="openai_enterprise",
        sinria_egress_config={"mode": "allow", "confidential_external_send": "allow"},
        sinria_boundary_config=BASE_CONFIG,
        sinria_egress_audit_path=audit_path,
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(agent, [{"role": "user", "content": "患者ID: TEST-12345 の検査結果を要約"}])

    assert exc.value.decision.action == "block"
    assert "Boundary Control" in exc.value.decision.reason
    audit = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit["provider_key"] == "openai_enterprise"
    assert audit["model"] == "gpt-enterprise-synthetic"
    assert audit["boundary_decision"]["data_class"] == "phi_pii"
    assert audit["raw_content_included"] is False
    serialized = json.dumps(audit, ensure_ascii=False)
    assert "TEST-12345" not in serialized


def test_model_provider_guard_allows_phi_on_registered_local_provider(tmp_path):
    agent = SimpleNamespace(
        base_url="http://localhost:11434/v1",
        provider="ollama",
        model="local-med-synthetic",
        sinria_provider_key="local_vllm",
        sinria_egress_config={"mode": "block", "confidential_external_send": "block"},
        sinria_boundary_config=BASE_CONFIG,
        sinria_egress_audit_path=tmp_path / "audit.jsonl",
    )

    decision = guard_model_provider_egress(agent, [{"role": "user", "content": "患者ID: TEST-12345 の検査結果を要約"}])

    assert decision.action == "allow"
    assert decision.external is False
    assert "Boundary Control" in decision.reason
