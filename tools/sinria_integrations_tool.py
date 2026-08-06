"""Tool surface for Sinria's local integration planner.

The tool is deliberately metadata-only: it lists connector/MedEvidence manifests
and returns sanitized operation plans.  It never calls SaaS, EMR/EHR, FHIR, HL7,
or MedEvidence runtimes directly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import yaml

from sinria_clinical_workflow import run_synthetic_clinical_workflow_demo
from sinria_integrations import (
    ApprovalRole,
    DataSensitivity,
    IntegrationDomain,
    SideEffect,
    connector_template_catalog,
    default_registry,
    describe_medevidence_skill_usage,
    medevidence_skill_catalog,
    medevidence_skill_bridge_stubs,
    plan_connector_runtime_gate,
    plan_ehr_export_file_import,
    plan_medevidence_skill_operation,
    registry_from_config,
    runtime_policy_from_config,
)
from sinria_medevidence_gcp_runtime import (
    MEDEVIDENCE_GCP_PROJECT,
    MEDEVIDENCE_GCP_REGION,
    MEDEVIDENCE_GCP_SERVICE,
    MedEvidenceGcpRuntimeTarget,
    build_dogfood_result_record,
    gcloud_proxy_command,
    write_dogfood_result_to_vault,
)
from tools.registry import registry


_MEDEVIDENCE_OPENCLAW_SKILLS_REL = Path("packages/core/src/openclaw/skills")


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _connector_to_dict(spec) -> dict[str, Any]:
    data = asdict(spec)
    for key in ("domain", "max_sensitivity"):
        data[key] = _enum_value(data[key])
    data["requires_approval_for"] = [_enum_value(item) for item in data["requires_approval_for"]]
    return data


def _operation_result(operation, decision) -> dict[str, Any]:
    return {
        "operation": asdict(operation),
        "decision": asdict(decision),
        "safety_note": (
            "Sinria produced a local sanitized plan only. No SaaS, EMR/EHR, "
            "FHIR/HL7, or MedEvidence runtime was contacted. Clinical release, "
            "writeback, send, and delete actions require the returned approval role."
        ),
    }


def _active_config_integrations() -> Mapping[str, Any] | None:
    """Load only ``integrations`` metadata from the active Sinria config.

    The integration planner must never read or expose secrets. This helper
    therefore returns a tiny dict containing only the top-level ``integrations``
    section used by ``registry_from_config``; model/provider keys, API tokens,
    and other configuration are ignored.
    """

    import sinria_constants

    config_path = Path(sinria_constants.get_config_path())
    if not config_path.exists():
        return None
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError("active config.yaml must contain a mapping")
    integrations_cfg = raw.get("integrations")
    if integrations_cfg is None:
        return None
    if not isinstance(integrations_cfg, Mapping):
        raise ValueError("integrations must be a mapping in active config.yaml")
    return {"integrations": integrations_cfg}


def _registry_from_request_or_active_config(config: Mapping[str, Any] | None):
    """Return registry plus a user-facing source label for auditability."""

    if config is not None:
        return registry_from_config(config), "request_config"
    active = _active_config_integrations()
    if active:
        return registry_from_config(active), "active_config"
    return default_registry(), "builtins"


def _configured_external_skill_dirs() -> list[str]:
    """Return configured external skill dirs without exposing unrelated config."""

    try:
        from agent.skill_utils import get_external_skills_dirs

        return [str(path) for path in get_external_skills_dirs()]
    except Exception:
        return []


def _discover_medevidence_root(candidate: str | None = None) -> Path | None:
    """Find a local MedEvidence checkout using explicit/local-only hints."""

    candidates: list[Path] = []
    if candidate:
        candidates.append(Path(candidate).expanduser())
    candidates.extend(
        [
            Path("~/med_evi-2").expanduser(),
            Path("~/MedEvidence").expanduser(),
            Path("~/medevidence").expanduser(),
        ]
    )
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if (resolved / _MEDEVIDENCE_OPENCLAW_SKILLS_REL).is_dir():
            return resolved
    return None


def _medevidence_setup_status(medevidence_root: str | None = None) -> dict[str, Any]:
    """Return local MedEvidence bridge readiness without importing/executing it."""

    expected = {spec.id for spec in medevidence_skill_catalog()}
    root = _discover_medevidence_root(medevidence_root)
    discovered: set[str] = set()
    skills_dir = None
    if root is not None:
        skills_dir = root / _MEDEVIDENCE_OPENCLAW_SKILLS_REL
        discovered = {
            path.stem
            for path in skills_dir.glob("*.ts")
            if path.name != "index.ts" and not path.name.endswith(".test.ts")
        }

    external_dirs = _configured_external_skill_dirs()
    repo_path = str(root) if root is not None else None
    root_configured_as_external_dir = bool(
        repo_path and any(Path(path).resolve() == root for path in external_dirs)
    )
    skills_dir_configured_as_external_dir = bool(
        skills_dir and any(Path(path).resolve() == skills_dir for path in external_dirs)
    )

    return {
        "success": True,
        "mode": "medevidence_setup_status",
        "safety_note": (
            "Local filesystem metadata only. Sinria did not import MedEvidence code, "
            "execute skills, read patient data, or call external networks."
        ),
        "medevidence_root": repo_path,
        "openclaw_skills_dir": str(skills_dir) if skills_dir else None,
        "manifest_skill_count": len(expected),
        "discovered_openclaw_skill_count": len(discovered),
        "discovered_openclaw_skill_ids": sorted(discovered),
        "missing_from_local_repo": sorted(expected - discovered),
        "not_in_sinria_manifest": sorted(discovered - expected),
        "configured_external_skill_dirs": external_dirs,
        "root_configured_as_external_dir": root_configured_as_external_dir,
        "skills_dir_configured_as_external_dir": skills_dir_configured_as_external_dir,
        "recommended_bridge_path": (
            "Use sinria_integrations mode=plan_medevidence_skill for current safe "
            "planning. Direct execution should be added later through an "
            "institution-approved local adapter, not by importing arbitrary TS code."
        ),
    }


def _integration_readiness_report(
    config: Mapping[str, Any] | None = None,
    medevidence_root: str | None = None,
) -> dict[str, Any]:
    """Return a local deployment-readiness report for connector execution work.

    The report is intentionally metadata-only. It validates connector metadata,
    summarizes runtime allowlists, and includes MedEvidence local checkout status
    without reading PHI, loading TypeScript, or touching external networks.
    """

    reg, source = _registry_from_request_or_active_config(config)
    policy_config = config if config is not None else _active_config_integrations()
    runtime_policy = runtime_policy_from_config(policy_config)
    builtins = {spec.id for spec in default_registry().list()}
    connector_ids = [spec.id for spec in reg.list()]
    configured_connector_ids = [connector_id for connector_id in connector_ids if connector_id not in builtins]
    allowed_connectors = [str(item) for item in runtime_policy.get("allowed_connectors", ()) or ()]
    allowed_capabilities = [str(item) for item in runtime_policy.get("allowed_capabilities", ()) or ()]
    connector_id_set = set(connector_ids)
    connectors_missing_runtime_allowlist = [
        connector_id for connector_id in configured_connector_ids if connector_id not in allowed_connectors
    ]
    unknown_allowed_connectors = [
        connector_id for connector_id in allowed_connectors if connector_id not in connector_id_set
    ]
    med_status = _medevidence_setup_status(medevidence_root)
    med_ready_for_planning = bool(
        med_status.get("medevidence_root")
        and not med_status.get("missing_from_local_repo")
    )

    blockers: list[str] = []
    if configured_connector_ids and not allowed_connectors:
        blockers.append("Configured institution connectors exist but integrations.runtime_policy.allowed_connectors is empty.")
    if connectors_missing_runtime_allowlist:
        blockers.append("Some configured institution connectors are not in integrations.runtime_policy.allowed_connectors.")
    if unknown_allowed_connectors:
        blockers.append("Runtime policy allowlists connector IDs that are not registered in built-in or config metadata.")

    return {
        "success": True,
        "mode": "integration_readiness_report",
        "config_source": source,
        "safety_note": (
            "Local metadata validation only. Sinria did not read clinical payloads, "
            "load MedEvidence TypeScript, or call SaaS/EMR/EHR networks."
        ),
        "connector_count": len(connector_ids),
        "configured_connector_ids": configured_connector_ids,
        "runtime_policy": {
            "allowed_connectors": allowed_connectors,
            "allowed_capabilities": allowed_capabilities,
            "external_network_allowed": bool(runtime_policy.get("external_network_allowed", False)),
        },
        "connectors_missing_runtime_allowlist": connectors_missing_runtime_allowlist,
        "unknown_allowed_connectors": unknown_allowed_connectors,
        "medevidence": {
            "root_found": bool(med_status.get("medevidence_root")),
            "openclaw_skill_count": med_status.get("discovered_openclaw_skill_count", 0),
            "manifest_skill_count": med_status.get("manifest_skill_count", 0),
            "missing_from_local_repo": med_status.get("missing_from_local_repo", []),
            "not_in_sinria_manifest": med_status.get("not_in_sinria_manifest", []),
            "ready_for_planning": med_ready_for_planning,
        },
        "blockers": blockers,
        "next_actions": (
            "Use list_connector_templates to add safe metadata stanzas, keep endpoints/tokens in approved secret stores, "
            "then run plan_connector_runtime_gate before any institution-approved adapter execution."
        ),
    }


def _medevidence_gcp_target_from_payload(payload_summary: Mapping[str, Any] | None) -> MedEvidenceGcpRuntimeTarget:
    payload = payload_summary or {}
    raw_target = payload.get("runtime_target")
    target: Mapping[str, Any] = raw_target if isinstance(raw_target, Mapping) else payload
    return MedEvidenceGcpRuntimeTarget(
        project=str(target.get("project") or MEDEVIDENCE_GCP_PROJECT),
        region=str(target.get("region") or MEDEVIDENCE_GCP_REGION),
        service=str(target.get("service") or MEDEVIDENCE_GCP_SERVICE),
        url=target.get("url"),
        revision=target.get("revision"),
    )


def sinria_integrations(
    mode: str,
    connector_id: str | None = None,
    action: str | None = None,
    side_effect: str = "read",
    sensitivity: str = "public",
    approved_by: str | None = None,
    payload_summary: Mapping[str, Any] | None = None,
    skill_id: str | None = None,
    release: bool = False,
    config: Mapping[str, Any] | None = None,
    medevidence_root: str | None = None,
    export_dir: str | None = None,
    clinical_document_kind: str | None = None,
    task_id: str | None = None,
) -> str:
    """List or plan Sinria SaaS/clinical/MedEvidence integration operations."""

    del task_id  # The planner is pure/local and does not need task context.
    normalized_mode = str(mode or "").strip().lower()

    try:
        if normalized_mode == "list_connectors":
            reg, source = _registry_from_request_or_active_config(config)
            connectors = [_connector_to_dict(spec) for spec in reg.list()]
            return json.dumps({"success": True, "config_source": source, "connectors": connectors}, ensure_ascii=False, default=_json_default)

        if normalized_mode == "list_connector_templates":
            templates = [asdict(template) for template in connector_template_catalog()]
            return json.dumps(
                {
                    "success": True,
                    "templates": templates,
                    "safety_note": (
                        "Templates are metadata-only starter stanzas for integrations.connectors. "
                        "Do not place endpoints, OAuth secrets, tokens, patient identifiers, or raw PHI in them."
                    ),
                },
                ensure_ascii=False,
                default=_json_default,
            )

        if normalized_mode == "list_medevidence_skills":
            skills = [asdict(spec) for spec in medevidence_skill_catalog()]
            return json.dumps({"success": True, "skills": skills}, ensure_ascii=False)

        if normalized_mode == "list_medevidence_skill_stubs":
            stubs = [asdict(stub) for stub in medevidence_skill_bridge_stubs()]
            return json.dumps(
                {
                    "success": True,
                    "stubs": stubs,
                    "safety_note": (
                        "Generated Sinria bridge-stub metadata only. No MedEvidence TypeScript was imported or executed, "
                        "no patient data was read, and no external network was contacted."
                    ),
                },
                ensure_ascii=False,
                default=_json_default,
            )

        if normalized_mode == "describe_medevidence_skill":
            if not skill_id:
                raise ValueError("skill_id is required for describe_medevidence_skill")
            guide = describe_medevidence_skill_usage(skill_id)
            return json.dumps(
                {
                    "success": True,
                    "guide": asdict(guide),
                    "safety_note": (
                        "Metadata-only Sinria usage guide. No MedEvidence code was imported, "
                        "no patient data was read, and no external network was contacted."
                    ),
                },
                ensure_ascii=False,
            )

        if normalized_mode == "medevidence_setup_status":
            return json.dumps(_medevidence_setup_status(medevidence_root), ensure_ascii=False)

        if normalized_mode == "integration_readiness_report":
            return json.dumps(_integration_readiness_report(config, medevidence_root), ensure_ascii=False)

        if normalized_mode == "medevidence_gcp_runtime_plan":
            target = _medevidence_gcp_target_from_payload(payload_summary)
            command = gcloud_proxy_command(target, port=int((payload_summary or {}).get("port", 18081)))
            return json.dumps(
                {
                    "success": True,
                    "mode": "medevidence_gcp_runtime_plan",
                    "runtime_target": asdict(target),
                    "proxy_command": command,
                    "safety_note": (
                        "GCP版MedEvidence runtime target locked before execution. "
                        "No network call was made by this planning mode; run the returned proxy command only for non-PHI smoke/execution."
                    ),
                },
                ensure_ascii=False,
                default=_json_default,
            )

        if normalized_mode == "record_medevidence_dogfood_result":
            payload = payload_summary or {}
            target = _medevidence_gcp_target_from_payload(payload)
            record = build_dogfood_result_record(
                target=target,
                title=str(payload.get("title") or "MedEvidence GCP dogfood result"),
                findings=list(payload.get("findings") or []),
                verified_commands=list(payload.get("verified_commands") or []),
            )
            result: dict[str, Any] = {
                "success": True,
                "mode": "record_medevidence_dogfood_result",
                "record": record,
                "written_path": None,
                "safety_note": (
                    "Dogfood result is sanitized and Sinria-centered. Raw PHI, tokens, and MedEvidence runtime bodies are not stored."
                ),
            }
            vault_root = payload.get("vault_root")
            if vault_root:
                path = write_dogfood_result_to_vault(record, vault_root=vault_root)
                result["written_path"] = str(path)
            else:
                result["next_action"] = "Pass payload_summary.vault_root to write under public knowledge base."
            return json.dumps(result, ensure_ascii=False, default=_json_default)

        if normalized_mode == "run_clinical_workflow_demo":
            if not action:
                raise ValueError("action is required for run_clinical_workflow_demo")
            if payload_summary is not None and not isinstance(payload_summary, Mapping):
                raise ValueError("payload_summary must be a mapping")
            payload = payload_summary or {}
            unknown_fields = set(payload) - {"document_kind"}
            if unknown_fields:
                raise ValueError("payload_summary contains unsupported clinical demo metadata")
            document_kind = clinical_document_kind or payload.get("document_kind") or "discharge_summary"
            return json.dumps(
                run_synthetic_clinical_workflow_demo(
                    action=action,
                    document_kind=str(document_kind),
                    actor_role=approved_by,
                ),
                ensure_ascii=False,
                default=_json_default,
            )

        if normalized_mode == "plan_ehr_export_import":
            if not export_dir:
                raise ValueError("export_dir is required for plan_ehr_export_import")
            operation, decision = plan_ehr_export_file_import(
                export_dir,
                connector_id=connector_id or "ehr_export_file",
                approved_by=ApprovalRole(approved_by) if approved_by else None,
            )
            return json.dumps({"success": True, **_operation_result(operation, decision)}, ensure_ascii=False, default=_json_default)

        if normalized_mode == "plan_connector_operation":
            if not connector_id:
                raise ValueError("connector_id is required for plan_connector_operation")
            if not action:
                raise ValueError("action is required for plan_connector_operation")
            reg, source = _registry_from_request_or_active_config(config)
            operation, decision = reg.plan_operation(
                connector_id,
                action,
                side_effect=SideEffect(side_effect),
                sensitivity=DataSensitivity(sensitivity),
                payload_summary=payload_summary,
                approved_by=ApprovalRole(approved_by) if approved_by else None,
            )
            return json.dumps({"success": True, "config_source": source, **_operation_result(operation, decision)}, ensure_ascii=False, default=_json_default)

        if normalized_mode == "plan_connector_runtime_gate":
            if not connector_id:
                raise ValueError("connector_id is required for plan_connector_runtime_gate")
            if not action:
                raise ValueError("action is required for plan_connector_runtime_gate")
            reg, source = _registry_from_request_or_active_config(config)
            policy_config = config if config is not None else _active_config_integrations()
            operation, decision, gate = plan_connector_runtime_gate(
                reg,
                connector_id,
                action,
                side_effect=SideEffect(side_effect),
                sensitivity=DataSensitivity(sensitivity),
                payload_summary=payload_summary,
                approved_by=ApprovalRole(approved_by) if approved_by else None,
                runtime_policy=runtime_policy_from_config(policy_config),
            )
            return json.dumps(
                {
                    "success": True,
                    "config_source": source,
                    **_operation_result(operation, decision),
                    "runtime_gate": asdict(gate),
                },
                ensure_ascii=False,
                default=_json_default,
            )

        if normalized_mode == "plan_medevidence_skill":
            if not skill_id:
                raise ValueError("skill_id is required for plan_medevidence_skill")
            operation, decision = plan_medevidence_skill_operation(
                skill_id,
                query_summary=payload_summary,
                sensitivity=DataSensitivity(sensitivity),
                release=bool(release),
                approved_by=ApprovalRole(approved_by) if approved_by else None,
            )
            return json.dumps({"success": True, **_operation_result(operation, decision)}, ensure_ascii=False, default=_json_default)

        raise ValueError(
            "mode must be one of: list_connectors, list_connector_templates, "
            "list_medevidence_skills, list_medevidence_skill_stubs, "
            "describe_medevidence_skill, medevidence_setup_status, "
            "integration_readiness_report, medevidence_gcp_runtime_plan, "
            "record_medevidence_dogfood_result, run_clinical_workflow_demo, "
            "plan_ehr_export_import, plan_connector_operation, "
            "plan_connector_runtime_gate, plan_medevidence_skill"
        )
    except Exception as exc:
        if normalized_mode == "run_clinical_workflow_demo" and not isinstance(
            exc, ValueError
        ):
            return json.dumps(
                {
                    "success": False,
                    "error": "Synthetic clinical workflow demo failed safely.",
                },
                ensure_ascii=False,
            )
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


SINRIA_INTEGRATIONS_SCHEMA = {
    "name": "sinria_integrations",
    "description": (
        "List Sinria SaaS/EMR/EHR connectors and safe connector templates, and "
        "plan sanitized, approval-gated operations including runtime allowlist "
        "gates and MedEvidence / メドエビデンス skill bridge use, plus GCP版MedEvidence "
        "runtime target-lock planning, sanitized dogfood-result recording, and a "
        "deterministic offline synthetic Clinical Gateway drafting/approval demo. "
        "Planning and demo modes perform no external API calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "list_connectors",
                    "list_connector_templates",
                    "list_medevidence_skills",
                    "list_medevidence_skill_stubs",
                    "describe_medevidence_skill",
                    "medevidence_setup_status",
                    "integration_readiness_report",
                    "medevidence_gcp_runtime_plan",
                    "record_medevidence_dogfood_result",
                    "run_clinical_workflow_demo",
                    "plan_ehr_export_import",
                    "plan_connector_operation",
                    "plan_connector_runtime_gate",
                    "plan_medevidence_skill",
                ],
            },
            "connector_id": {"type": "string"},
            "action": {"type": "string"},
            "side_effect": {"type": "string", "enum": ["read", "draft", "write", "send", "delete"], "default": "read"},
            "sensitivity": {"type": "string", "enum": ["public", "internal", "confidential", "patient"], "default": "public"},
            "approved_by": {"type": "string", "enum": ["user", "admin", "compliance", "physician"]},
            "payload_summary": {"type": "object", "description": "Sanitized metadata only; never include raw PHI, MRNs, tokens, or full clinical documents."},
            "skill_id": {"type": "string", "description": "MedEvidence/OpenClaw skill id, e.g. consensus-search or chart-summary."},
            "release": {"type": "boolean", "default": False, "description": "For MedEvidence plans, request a release/send path instead of local draft."},
            "config": {"type": "object", "description": "Optional local connector metadata shaped like integrations.connectors; no secrets. If omitted, Sinria reads only integrations.* from the active config.yaml."},
            "medevidence_root": {"type": "string", "description": "Optional local MedEvidence checkout path for metadata-only setup status checks."},
            "export_dir": {"type": "string", "description": "Local EHR/カルテ export directory for metadata-only import planning; file contents and names are not returned."},
            "clinical_document_kind": {
                "type": "string",
                "enum": ["discharge_summary", "referral_letter"],
                "description": "Bundled synthetic document kind for run_clinical_workflow_demo.",
            },
        },
        "required": ["mode"],
    },
}


registry.register(
    name="sinria_integrations",
    toolset="sinria_integrations",
    schema=SINRIA_INTEGRATIONS_SCHEMA,
    handler=lambda args, **kw: sinria_integrations(
        mode=args.get("mode", ""),
        connector_id=args.get("connector_id"),
        action=args.get("action"),
        side_effect=args.get("side_effect", "read"),
        sensitivity=args.get("sensitivity", "public"),
        approved_by=args.get("approved_by"),
        payload_summary=args.get("payload_summary"),
        skill_id=args.get("skill_id"),
        release=args.get("release", False),
        config=args.get("config"),
        medevidence_root=args.get("medevidence_root"),
        export_dir=args.get("export_dir"),
        clinical_document_kind=args.get("clinical_document_kind"),
        task_id=kw.get("task_id"),
    ),
    check_fn=lambda: True,
    description="Local Sinria SaaS/clinical/MedEvidence connector planner",
    emoji="🏥",
)
