from sinria_integrations import (
    ApprovalRole,
    DataSensitivity,
    IntegrationDomain,
    SideEffect,
    connector_template_catalog,
    default_registry,
    describe_medevidence_skill_usage,
    medevidence_skill_bridge_stubs,
    medevidence_skill_catalog,
    inventory_ehr_export_directory,
    plan_connector_runtime_gate,
    plan_ehr_export_file_import,
    plan_medevidence_skill_operation,
    registry_from_config,
    runtime_policy_from_config,
    sanitize_payload_summary,
)


def test_default_registry_includes_saas_and_clinical_connectors():
    registry = default_registry()

    saas_ids = {spec.id for spec in registry.list(domain=IntegrationDomain.SAAS)}
    clinical_ids = {spec.id for spec in registry.list(domain=IntegrationDomain.CLINICAL)}

    assert {
        "google_workspace",
        "slack",
        "microsoft_365",
        "salesforce_health_cloud",
        "jira_service_management",
        "servicenow_itsm",
        "zendesk_support",
        "box_enterprise",
        "notion",
        "linear",
    }.issubset(saas_ids)
    assert {"fhir_r4", "hl7v2_mllp"}.issubset(clinical_ids)
    assert registry.get("fhir_r4").clinical_system is True
    assert "カルテ" in registry.get("ehr_export_file").display_name


def test_default_registry_includes_medevidence_local_bridge():
    registry = default_registry()

    spec = registry.get("medevidence_local")

    assert "メドエビデンス" in spec.display_name
    assert spec.domain == IntegrationDomain.FILE
    assert spec.max_sensitivity == DataSensitivity.PATIENT
    assert spec.clinical_system is True
    assert "fact_check_summary" in spec.capabilities


def test_connector_template_catalog_provides_safe_saas_clinical_and_medevidence_starters():
    templates = {template.id: template for template in connector_template_catalog()}

    assert {
        "google_workspace_draft",
        "microsoft_365_draft",
        "salesforce_health_cloud_draft",
        "jira_service_management_draft",
        "servicenow_itsm_draft",
        "zendesk_support_draft",
        "box_enterprise_draft",
        "smart_on_fhir_readonly",
        "hl7v2_interface_engine_readonly",
        "medevidence_local_bridge",
    }.issubset(templates)
    fhir = templates["smart_on_fhir_readonly"]
    med = templates["medevidence_local_bridge"]

    assert fhir.domain == IntegrationDomain.CLINICAL
    assert fhir.config_example["clinical_system"] is True
    assert fhir.config_example["max_sensitivity"] == "patient"
    assert "secret" in fhir.secret_location.lower()
    assert "physician approval" in " ".join(fhir.safety_notes)

    assert "メドエビデンス" in med.config_example["display_name"]
    assert "TypeScript imports" in med.secret_location


def test_registry_from_config_adds_institution_specific_connectors():
    registry = registry_from_config(
        {
            "integrations": {
                "connectors": [
                    {
                        "id": "hospital_fhir_sandbox",
                        "display_name": "Hospital FHIR sandbox",
                        "domain": "clinical",
                        "protocol": "HL7 FHIR R4 REST via institution VPN",
                        "capabilities": ["patient_read", "document_reference_draft"],
                        "max_sensitivity": "patient",
                        "clinical_system": True,
                        "notes": "Local policy-approved sandbox metadata only.",
                    },
                    {
                        "id": "crm_triage",
                        "display_name": "CRM triage",
                        "domain": "saas",
                        "protocol": "REST API",
                        "capabilities": "lead_summary",
                        "max_sensitivity": "internal",
                    },
                ]
            }
        }
    )

    clinical = registry.get("hospital_fhir_sandbox")
    crm = registry.get("crm_triage")

    assert clinical.clinical_system is True
    assert clinical.max_sensitivity == DataSensitivity.PATIENT
    assert crm.capabilities == ("lead_summary",)
    _operation, decision = registry.plan_operation(
        "hospital_fhir_sandbox",
        "create_document_reference",
        side_effect=SideEffect.WRITE,
        sensitivity=DataSensitivity.PATIENT,
        approved_by=ApprovalRole.ADMIN,
    )
    assert decision.allowed is False
    assert decision.required_role == ApprovalRole.PHYSICIAN


def test_registry_from_config_rejects_invalid_connector_metadata():
    try:
        registry_from_config(
            {
                "integrations": {
                    "connectors": [
                        {
                            "id": "bad_connector",
                            "display_name": "Bad connector",
                            "domain": "clinical",
                            "protocol": "REST",
                            "capabilities": ["read"],
                            "max_sensitivity": "raw_patient_chart",
                        }
                    ]
                }
            }
        )
    except ValueError as exc:
        assert "Invalid max_sensitivity" in str(exc)
    else:
        raise AssertionError("invalid connector metadata should be rejected")


def test_registry_from_config_rejects_endpoint_or_secret_fields_in_connector_metadata():
    try:
        registry_from_config(
            {
                "integrations": {
                    "connectors": [
                        {
                            "id": "hospital_m365",
                            "display_name": "Hospital Microsoft 365",
                            "domain": "saas",
                            "protocol": "Microsoft Graph / OAuth",
                            "capabilities": ["outlook_draft"],
                            "max_sensitivity": "confidential",
                            "tenant_id": "should-not-live-here",
                            "client_secret": "should-not-live-here",
                        }
                    ]
                }
            }
        )
    except ValueError as exc:
        assert "metadata-only" in str(exc)
        assert "client_secret" in str(exc)
        assert "tenant_id" in str(exc)
    else:
        raise AssertionError("secret/endpoint connector metadata should be rejected")


def test_registry_from_config_rejects_nested_secret_fields_in_connector_metadata():
    try:
        registry_from_config(
            {
                "integrations": {
                    "connectors": [
                        {
                            "id": "hospital_m365",
                            "display_name": "Hospital Microsoft 365",
                            "domain": "saas",
                            "protocol": "Microsoft Graph / OAuth",
                            "capabilities": ["outlook_draft"],
                            "max_sensitivity": "confidential",
                            "adapter": {"token": "should-not-live-here"},
                        }
                    ]
                }
            }
        )
    except ValueError as exc:
        assert "metadata-only" in str(exc)
        assert "adapter.token" in str(exc)
    else:
        raise AssertionError("nested secret connector metadata should be rejected")


def test_saas_send_is_blocked_until_admin_or_compliance_approval():
    registry = default_registry()

    _operation, decision = registry.plan_operation(
        "google_workspace",
        "send_gmail",
        side_effect=SideEffect.SEND,
        sensitivity=DataSensitivity.CONFIDENTIAL,
        payload_summary={"kind": "redacted draft"},
    )
    assert decision.allowed is False
    assert decision.required_role == ApprovalRole.ADMIN

    _operation, approved = registry.plan_operation(
        "google_workspace",
        "send_gmail",
        side_effect=SideEffect.SEND,
        sensitivity=DataSensitivity.CONFIDENTIAL,
        approved_by=ApprovalRole.COMPLIANCE,
    )
    assert approved.allowed is True


def test_clinical_write_requires_physician_approval():
    registry = default_registry()

    _operation, admin_decision = registry.plan_operation(
        "fhir_r4",
        "create_document_reference",
        side_effect=SideEffect.WRITE,
        sensitivity=DataSensitivity.PATIENT,
        approved_by=ApprovalRole.ADMIN,
    )
    assert admin_decision.allowed is False
    assert admin_decision.required_role == ApprovalRole.PHYSICIAN

    _operation, physician_decision = registry.plan_operation(
        "fhir_r4",
        "create_document_reference",
        side_effect=SideEffect.WRITE,
        sensitivity=DataSensitivity.PATIENT,
        approved_by=ApprovalRole.PHYSICIAN,
    )
    assert physician_decision.allowed is True


def test_connector_sensitivity_cap_blocks_overbroad_data():
    registry = default_registry()

    _operation, decision = registry.plan_operation(
        "linear",
        "create_issue",
        side_effect=SideEffect.DRAFT,
        sensitivity=DataSensitivity.PATIENT,
    )
    assert decision.allowed is False
    assert "capped at internal" in decision.reason


def test_ehr_export_local_import_allows_patient_read_without_network_side_effect():
    registry = default_registry()

    _operation, decision = registry.plan_operation(
        "ehr_export_file",
        "import_redacted_local_export",
        side_effect=SideEffect.READ,
        sensitivity=DataSensitivity.PATIENT,
        payload_summary={"source": "local export", "identifiers": "redacted"},
    )
    assert decision.allowed is True
    assert "locally" in decision.reason


def test_ehr_export_inventory_uses_metadata_only_and_no_filenames(tmp_path):
    export_dir = tmp_path / "ehr-exports"
    export_dir.mkdir()
    (export_dir / "患者ID-SECRET-1234.csv").write_text("raw,phi\n", encoding="utf-8")
    (export_dir / "chart-note.pdf").write_bytes(b"%PDF fake")
    (export_dir / "ignore.exe").write_bytes(b"nope")

    inventory = inventory_ehr_export_directory(export_dir)
    operation, decision = plan_ehr_export_file_import(export_dir)

    assert inventory.file_count == 2
    assert inventory.skipped_file_count == 1
    assert inventory.extensions == {".csv": 1, ".pdf": 1}
    assert decision.allowed is True
    assert operation.connector_id == "ehr_export_file"
    assert operation.payload_summary["export_dir"] == "[LOCAL_PATH_REDACTED]"
    assert operation.payload_summary["content_read"] is False
    assert operation.payload_summary["filenames_returned"] is False
    serialized = str(operation.payload_summary)
    assert "SECRET" not in serialized
    assert "chart-note" not in serialized


def test_medevidence_local_bridge_allows_local_draft_but_gates_release():
    registry = default_registry()

    operation, local_decision = registry.plan_operation(
        "medevidence_local",
        "draft_consent_workspace_plan",
        side_effect=SideEffect.DRAFT,
        sensitivity=DataSensitivity.PATIENT,
        payload_summary={"kind": "consent workspace plan", "identifiers": "redacted"},
    )
    assert operation.payload_summary["kind"] == "consent workspace plan"
    assert local_decision.allowed is True

    _operation, release_decision = registry.plan_operation(
        "medevidence_local",
        "release_patient_message",
        side_effect=SideEffect.SEND,
        sensitivity=DataSensitivity.PATIENT,
        approved_by=ApprovalRole.ADMIN,
    )
    assert release_decision.allowed is False
    assert release_decision.required_role == ApprovalRole.PHYSICIAN


def test_medevidence_skill_catalog_maps_openclaw_skill_safety_metadata():
    catalog = {spec.id: spec for spec in medevidence_skill_catalog()}

    assert "consensus-search" in catalog
    assert "chart-summary" in catalog
    assert "clinical-action-plan" in catalog
    assert catalog["consensus-search"].external_transmission is True
    assert catalog["consensus-search"].accepts_phi is False
    assert catalog["chart-summary"].accepts_phi is True
    assert catalog["clinical-action-plan"].max_autonomous_action == "draft"


def test_describe_medevidence_skill_usage_guides_safe_sinria_path():
    public_guide = describe_medevidence_skill_usage("consensus-search")
    clinical_guide = describe_medevidence_skill_usage("chart-summary")

    assert public_guide.suggested_planner_call["mode"] == "plan_medevidence_skill"
    assert public_guide.suggested_planner_call["sensitivity"] == "public"
    assert "de-identified" in public_guide.allowed_input
    assert "PHI" in public_guide.forbidden_input

    assert clinical_guide.suggested_planner_call["sensitivity"] == "patient"
    assert "local draft" in clinical_guide.default_sinria_path
    assert "physician approval" in clinical_guide.approval_boundary


def test_medevidence_skill_bridge_stubs_route_every_skill_through_safe_planner():
    manifest_ids = {spec.id for spec in medevidence_skill_catalog()}
    stubs = {stub.source_skill_id: stub for stub in medevidence_skill_bridge_stubs()}

    assert set(stubs) == manifest_ids
    public_stub = stubs["consensus-search"]
    clinical_stub = stubs["chart-summary"]

    assert public_stub.skill_name == "medevidence-consensus-search"
    assert public_stub.planner_call["mode"] == "plan_medevidence_skill"
    assert public_stub.planner_call["sensitivity"] == "public"
    assert any("must not receive PHI" in step for step in public_stub.safe_steps)
    assert any("Do not import or execute MedEvidence TypeScript" in item for item in public_stub.forbidden)

    assert clinical_stub.planner_call["sensitivity"] == "patient"
    assert any("raw chart/カルテ text" in step for step in clinical_stub.safe_steps)
    assert any("EHR/EMR writeback" in step for step in clinical_stub.safe_steps)


def test_medevidence_public_search_blocks_patient_payloads():
    operation, decision = plan_medevidence_skill_operation(
        "consensus-search",
        sensitivity=DataSensitivity.PATIENT,
        query_summary={"patient_name": "Example Patient", "topic": "糖尿病"},
    )

    assert decision.allowed is False
    assert "not PHI-capable" in decision.reason
    assert operation.payload_summary["patient_name"] == "[REDACTED]"


def test_medevidence_phi_capable_skill_plans_local_draft_and_release_requires_physician():
    operation, draft_decision = plan_medevidence_skill_operation(
        "chart-summary",
        sensitivity=DataSensitivity.PATIENT,
        query_summary={"source": "local EHR export", "mrn": "MRN: ABCD-1234"},
    )

    assert draft_decision.allowed is True
    assert operation.connector_id == "medevidence_local"
    assert operation.payload_summary["query_summary"]["mrn"] == "[REDACTED]"

    _operation, release_decision = plan_medevidence_skill_operation(
        "chart-summary",
        sensitivity=DataSensitivity.PATIENT,
        release=True,
        approved_by=ApprovalRole.ADMIN,
    )
    assert release_decision.allowed is False
    assert release_decision.required_role == ApprovalRole.PHYSICIAN

    _operation, physician_release = plan_medevidence_skill_operation(
        "chart-summary",
        sensitivity=DataSensitivity.PATIENT,
        release=True,
        approved_by=ApprovalRole.PHYSICIAN,
    )
    assert physician_release.allowed is True


def test_payload_summary_is_sanitized_before_planning():
    registry = default_registry()

    operation, decision = registry.plan_operation(
        "fhir_r4",
        "summarize_chart",
        side_effect=SideEffect.READ,
        sensitivity=DataSensitivity.PATIENT,
        payload_summary={
            "patient_name": "Taro Example",
            "mrn": "MRN: ABCD-1234",
            "contact": "patient@example.test",
            "metadata": {"document_body": "full chart text should not persist"},
            "safe_count": 3,
        },
    )

    assert decision.allowed is True
    assert operation.payload_summary == {
        "patient_name": "[REDACTED]",
        "mrn": "[REDACTED]",
        "contact": "[REDACTED]",
        "metadata": {"document_body": "[REDACTED]"},
        "safe_count": 3,
    }


def test_connector_runtime_gate_requires_explicit_allowlist_and_redacted_payload():
    registry = default_registry()

    operation, safety, gate = plan_connector_runtime_gate(
        registry,
        "fhir_r4",
        "patient_read",
        side_effect=SideEffect.READ,
        sensitivity=DataSensitivity.PATIENT,
        payload_summary={"patient_name": "Example Patient", "mrn": "MRN: ABCD-1234"},
        runtime_policy={"allowed_connectors": [], "external_network_allowed": True},
    )

    assert safety.allowed is True
    assert gate.ready_for_execution is False
    assert gate.connector_allowlisted is False
    assert gate.raw_payload_allowed_in_logs is False
    assert operation.payload_summary["patient_name"] == "[REDACTED]"
    assert operation.payload_summary["mrn"] == "[REDACTED]"


def test_connector_runtime_gate_passes_only_for_allowlisted_network_connector():
    registry = default_registry()

    _operation, _safety, gate = plan_connector_runtime_gate(
        registry,
        "fhir_r4",
        "patient_read",
        side_effect=SideEffect.READ,
        sensitivity=DataSensitivity.PATIENT,
        runtime_policy={
            "allowed_connectors": ["fhir_r4"],
            "external_network_allowed": True,
        },
    )

    assert gate.ready_for_execution is True
    assert gate.connector_allowlisted is True
    assert gate.capability_allowlisted is True
    assert gate.external_network_allowed_by_gate is True


def test_connector_runtime_gate_keeps_saas_network_blocked_until_policy_enables_it():
    registry = default_registry()

    _operation, _safety, gate = plan_connector_runtime_gate(
        registry,
        "google_workspace",
        "gmail_draft",
        side_effect=SideEffect.DRAFT,
        sensitivity=DataSensitivity.CONFIDENTIAL,
        runtime_policy={"allowed_connectors": ["google_workspace"]},
    )

    assert gate.ready_for_execution is False
    assert "external network execution is not enabled" in gate.reason


def test_runtime_policy_from_config_reads_metadata_only_runtime_gate_policy():
    policy = runtime_policy_from_config(
        {
            "integrations": {
                "runtime_policy": {
                    "allowed_connectors": ["hospital_fhir_readonly"],
                    "allowed_capabilities": ["patient_read"],
                    "external_network_allowed": False,
                }
            }
        }
    )

    assert policy["allowed_connectors"] == ["hospital_fhir_readonly"]
    assert policy["external_network_allowed"] is False


def test_runtime_policy_rejects_endpoint_or_secret_fields_before_adapter_handoff():
    registry = default_registry()

    try:
        plan_connector_runtime_gate(
            registry,
            "fhir_r4",
            "patient_read",
            side_effect=SideEffect.READ,
            sensitivity=DataSensitivity.PATIENT,
            runtime_policy={
                "allowed_connectors": ["fhir_r4"],
                "external_network_allowed": True,
                "adapter": {"base_url": "https://ehr.invalid/fhir", "token": "secret"},
            },
        )
    except ValueError as exc:
        assert "integrations.runtime_policy" in str(exc)
        assert "adapter.base_url" in str(exc)
        assert "adapter.token" in str(exc)
    else:
        raise AssertionError("runtime policy must not carry adapter endpoints or secrets")


def test_runtime_policy_from_config_rejects_patient_identifiers():
    try:
        runtime_policy_from_config(
            {
                "integrations": {
                    "runtime_policy": {
                        "allowed_connectors": ["hospital_fhir_readonly"],
                        "patient_id": "SHOULD-NOT-BE-HERE",
                    }
                }
            }
        )
    except ValueError as exc:
        assert "metadata-only" in str(exc)
        assert "patient_id" in str(exc)
    else:
        raise AssertionError("runtime policy must not carry patient identifiers")


def test_sanitize_payload_summary_truncates_long_free_text():
    sanitized = sanitize_payload_summary({"excerpt": "x" * 300})

    assert sanitized["excerpt"]["truncated"] is True
    assert len(sanitized["excerpt"]["summary"]) < 130
