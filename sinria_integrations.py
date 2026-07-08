"""Sinria integration registry and safe operation planner.

This module is intentionally pure/local: it does not make network calls and it
never reads clinical payloads.  It provides the common metadata and safety gates
needed before concrete SaaS, EMR/EHR, FHIR, or HL7 connectors are implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


class IntegrationDomain(str, Enum):
    """High-level domains used for policy and UI grouping."""

    SAAS = "saas"
    CLINICAL = "clinical"
    FILE = "file"


class SideEffect(str, Enum):
    """Side-effect level for a planned connector operation."""

    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    SEND = "send"
    DELETE = "delete"


class DataSensitivity(str, Enum):
    """Maximum data sensitivity a connector may touch."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PATIENT = "patient"


class ApprovalRole(str, Enum):
    """Approval roles recognized by the connector safety gate."""

    USER = "user"
    ADMIN = "admin"
    COMPLIANCE = "compliance"
    PHYSICIAN = "physician"


@dataclass(frozen=True)
class ConnectorSpec:
    """Static metadata for one SaaS/clinical connector."""

    id: str
    display_name: str
    domain: IntegrationDomain
    protocol: str
    capabilities: tuple[str, ...]
    max_sensitivity: DataSensitivity
    requires_approval_for: tuple[SideEffect, ...] = (
        SideEffect.WRITE,
        SideEffect.SEND,
        SideEffect.DELETE,
    )
    clinical_system: bool = False
    notes: str = ""


@dataclass(frozen=True)
class PlannedOperation:
    """A safe, serializable plan for a connector action.

    ``payload_summary`` must be sanitized before it is stored or shown.  Raw
    patient identifiers, MRNs, emails, tokens, or document bodies do not belong
    in this object.
    """

    connector_id: str
    action: str
    side_effect: SideEffect
    sensitivity: DataSensitivity
    payload_summary: Mapping[str, Any] = field(default_factory=dict)
    approved_by: ApprovalRole | None = None


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str
    required_role: ApprovalRole | None = None


@dataclass(frozen=True)
class MedEvidenceSkillSpec:
    """Metadata for a MedEvidence/OpenClaw skill that Sinria can plan safely.

    This is an adapter manifest, not an executor.  It lets Sinria reason about
    which MedEvidence skills can be invoked with local/sanitized inputs before a
    concrete bridge process is wired up.
    """

    id: str
    display_name: str
    category: str
    accepts_phi: bool
    external_transmission: bool
    max_autonomous_action: str
    approval_required: bool = False


@dataclass(frozen=True)
class MedEvidenceSkillUsageGuide:
    """Sinria-facing usage guidance for one MedEvidence/OpenClaw skill."""

    skill_id: str
    display_name: str
    category: str
    allowed_input: str
    forbidden_input: str
    default_sinria_path: str
    approval_boundary: str
    suggested_planner_call: Mapping[str, Any]


@dataclass(frozen=True)
class MedEvidenceSkillBridgeStub:
    """Sinria-side bridge-skill stub for one MedEvidence/OpenClaw skill.

    These stubs are generated from Sinria's local manifest so agents can route a
    MedEvidence task through the correct planner call without importing or
    executing the MedEvidence TypeScript repository.
    """

    skill_name: str
    source_skill_id: str
    title: str
    description: str
    safe_steps: tuple[str, ...]
    forbidden: tuple[str, ...]
    planner_call: Mapping[str, Any]


@dataclass(frozen=True)
class ConnectorTemplate:
    """Safe starter metadata for configuring a real institution connector.

    Templates intentionally exclude endpoints, tokens, tenant IDs, patient IDs,
    and other secrets. They provide copyable ``integrations.connectors`` shapes
    so Sinria can recognize hospital/SaaS systems before any concrete adapter is
    approved or executed.
    """

    id: str
    display_name: str
    domain: IntegrationDomain
    protocol: str
    config_example: Mapping[str, Any]
    secret_location: str
    safety_notes: tuple[str, ...]


@dataclass(frozen=True)
class EHRExportInventory:
    """De-identified manifest for a local EHR/カルテ export directory.

    The inventory intentionally excludes file names, row contents, PDF text,
    patient identifiers, and absolute paths. It is suitable for planning a local
    import/redaction job without copying clinical data into Sinria logs.
    """

    directory_present: bool
    file_count: int
    skipped_file_count: int
    total_bytes: int
    extensions: Mapping[str, int]
    allowed_extensions: tuple[str, ...]


@dataclass(frozen=True)
class ConnectorRuntimeGate:
    """Decision envelope for a future concrete connector executor.

    Sinria's integration registry is still planning-only.  This object is the
    explicit handoff contract a later SaaS/EMR/EHR adapter must satisfy before
    making a network call or touching a local clinical runtime: the connector
    must be institution-allowlisted, the requested action must match a declared
    capability, Sinria's safety policy must allow the operation, and the executor
    must use only the sanitized payload summary for audit/logging.
    """

    ready_for_execution: bool
    reason: str
    connector_allowlisted: bool
    capability_allowlisted: bool
    redaction_required: bool = True
    raw_payload_allowed_in_logs: bool = False
    external_network_allowed_by_gate: bool = False


_SENSITIVE_PAYLOAD_KEYS = {
    "body",
    "chart",
    "chart_body",
    "clinical_note",
    "document",
    "document_body",
    "email",
    "full_text",
    "mrn",
    "patient_id",
    "patient_name",
    "raw",
    "raw_payload",
    "token",
}
_CONNECTOR_METADATA_KEYS = {
    "id",
    "display_name",
    "domain",
    "protocol",
    "capabilities",
    "max_sensitivity",
    "requires_approval_for",
    "clinical_system",
    "notes",
}
_FORBIDDEN_CONNECTOR_CONFIG_KEYS = {
    "api_key",
    "base_url",
    "client_id",
    "client_secret",
    "endpoint",
    "host",
    "oauth_token",
    "password",
    "patient_id",
    "secret",
    "tenant_id",
    "token",
    "url",
    "username",
}
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_MRN_RE = re.compile(r"\b(?:MRN|カルテ番号|患者ID|patient[_ -]?id)[:#]?\s*[A-Z0-9-]{4,}\b", re.IGNORECASE)
_DEFAULT_EHR_EXPORT_EXTENSIONS = (".csv", ".json", ".pdf", ".txt", ".ndjson", ".xml")


def _reject_forbidden_metadata_keys(context: str, raw: Mapping[str, Any]) -> None:
    """Fail fast when policy/connector metadata contains secrets or endpoints.

    Sinria's integration layer is a local planner. Connector configuration and
    runtime policy may name allowlisted connectors/capabilities, but concrete
    endpoints, tenant identifiers, usernames, tokens, and patient identifiers
    must stay in approved secret/runtime stores. This helper checks nested
    mappings as well so a future adapter cannot accidentally smuggle secrets
    through metadata-only config.
    """

    forbidden: list[str] = []

    def _walk(prefix: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                normalized = key_text.lower().replace("-", "_")
                path = f"{prefix}.{key_text}" if prefix else key_text
                if normalized in _FORBIDDEN_CONNECTOR_CONFIG_KEYS:
                    forbidden.append(path)
                _walk(path, child)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                _walk(f"{prefix}[{index}]", child)

    _walk("", raw)
    if forbidden:
        fields = ", ".join(sorted(forbidden))
        raise ValueError(
            f"{context} must be metadata-only; move secret/endpoint fields "
            f"outside Sinria config/runtime policy: {fields}"
        )


def sanitize_payload_summary(payload_summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a connector payload summary safe enough to persist or display.

    Connector plans are audit metadata, not clinical payload stores.  This helper
    keeps non-identifying shape/count information while replacing obvious raw
    identifiers, tokens, emails, MRNs, and full document bodies with redaction
    markers.  It is intentionally conservative and local-only.
    """

    def _sanitize_value(key: str, value: Any) -> Any:
        normalized_key = key.lower().replace("-", "_")
        if normalized_key in _SENSITIVE_PAYLOAD_KEYS:
            return "[REDACTED]"
        if isinstance(value, str):
            if _EMAIL_RE.search(value) or _MRN_RE.search(value):
                return "[REDACTED]"
            if len(value) > 240:
                return {"summary": value[:120] + "…", "truncated": True}
            return value
        if isinstance(value, Mapping):
            return {str(k): _sanitize_value(str(k), v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_sanitize_value(key, item) for item in value]
        return value

    return {str(key): _sanitize_value(str(key), value) for key, value in (payload_summary or {}).items()}


class IntegrationRegistry:
    """In-memory connector registry with deterministic safety checks."""

    def __init__(self, specs: Iterable[ConnectorSpec] = ()) -> None:
        self._specs: dict[str, ConnectorSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ConnectorSpec) -> None:
        if not spec.id or not spec.id.replace("_", "-").replace("-", "").isalnum():
            raise ValueError(f"Invalid connector id: {spec.id!r}")
        if spec.id in self._specs:
            raise ValueError(f"Connector already registered: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, connector_id: str) -> ConnectorSpec:
        try:
            return self._specs[connector_id]
        except KeyError as exc:
            raise KeyError(f"Unknown connector: {connector_id}") from exc

    def list(self, *, domain: IntegrationDomain | None = None) -> list[ConnectorSpec]:
        specs = self._specs.values()
        if domain is not None:
            specs = [spec for spec in specs if spec.domain == domain]
        return sorted(specs, key=lambda spec: spec.id)

    def plan_operation(
        self,
        connector_id: str,
        action: str,
        *,
        side_effect: SideEffect,
        sensitivity: DataSensitivity,
        payload_summary: Mapping[str, Any] | None = None,
        approved_by: ApprovalRole | None = None,
    ) -> tuple[PlannedOperation, SafetyDecision]:
        spec = self.get(connector_id)
        operation = PlannedOperation(
            connector_id=connector_id,
            action=action,
            side_effect=side_effect,
            sensitivity=sensitivity,
            payload_summary=sanitize_payload_summary(payload_summary),
            approved_by=approved_by,
        )
        return operation, decide_safety(spec, operation)


def _parse_enum(enum_cls: type[Enum], value: Any, *, field_name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    if value is None:
        raise ValueError(f"Missing required connector field: {field_name}")
    normalized = str(value).strip().lower()
    for member in enum_cls:
        if normalized in {member.value.lower(), member.name.lower()}:
            return member
    allowed = ", ".join(member.value for member in enum_cls)
    raise ValueError(f"Invalid {field_name}: {value!r}; expected one of: {allowed}")


def connector_spec_from_mapping(raw: Mapping[str, Any]) -> ConnectorSpec:
    """Build a ``ConnectorSpec`` from local configuration metadata.

    This is the safe extension seam for institution-specific SaaS, EMR/EHR,
    interface-engine, or MedEvidence bridge connectors. It accepts metadata
    only; no credentials or endpoints are contacted, and no clinical payload is
    read. Secrets should stay in ``.env`` or the institution's secret manager.
    """

    _reject_forbidden_metadata_keys("integrations.connectors entries", raw)
    unknown = set(raw) - _CONNECTOR_METADATA_KEYS

    def _required_text(key: str) -> str:
        value = str(raw.get(key, "")).strip()
        if not value:
            raise ValueError(f"Missing required connector field: {key}")
        return value

    capabilities = raw.get("capabilities") or ()
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    if not isinstance(capabilities, (list, tuple)):
        raise ValueError("Connector field 'capabilities' must be a list of strings")

    approval_effects = raw.get("requires_approval_for")
    if approval_effects is None:
        parsed_approval_effects = (
            SideEffect.WRITE,
            SideEffect.SEND,
            SideEffect.DELETE,
        )
    else:
        if isinstance(approval_effects, str):
            approval_effects = [approval_effects]
        if not isinstance(approval_effects, (list, tuple)):
            raise ValueError("Connector field 'requires_approval_for' must be a list of side effects")
        parsed_approval_effects = tuple(
            _parse_enum(SideEffect, item, field_name="requires_approval_for")
            for item in approval_effects
        )

    return ConnectorSpec(
        id=_required_text("id"),
        display_name=_required_text("display_name"),
        domain=_parse_enum(IntegrationDomain, raw.get("domain"), field_name="domain"),  # type: ignore[arg-type]
        protocol=_required_text("protocol"),
        capabilities=tuple(str(item).strip() for item in capabilities if str(item).strip()),
        max_sensitivity=_parse_enum(  # type: ignore[arg-type]
            DataSensitivity,
            raw.get("max_sensitivity"),
            field_name="max_sensitivity",
        ),
        requires_approval_for=parsed_approval_effects,  # type: ignore[arg-type]
        clinical_system=bool(raw.get("clinical_system", False)),
        notes=str(raw.get("notes", "")).strip(),
    )


def registry_from_config(config: Mapping[str, Any]) -> IntegrationRegistry:
    """Return a registry extended by ``integrations.connectors`` config.

    Example shape::

        integrations:
          connectors:
            - id: hospital_fhir_sandbox
              display_name: Hospital FHIR sandbox
              domain: clinical
              protocol: HL7 FHIR R4 REST
              capabilities: [patient_read]
              max_sensitivity: patient
              clinical_system: true

    The function intentionally consumes a passed-in dict so callers can decide
    where configuration comes from and how secrets/tenant policy are handled.
    """

    registry = default_registry()
    integrations_cfg = config.get("integrations")
    if not isinstance(integrations_cfg, Mapping):
        return registry
    raw_connectors = integrations_cfg.get("connectors") or []
    if not isinstance(raw_connectors, list):
        raise ValueError("integrations.connectors must be a list")
    for raw in raw_connectors:
        if not isinstance(raw, Mapping):
            raise ValueError("Each integrations.connectors entry must be a mapping")
        registry.register(connector_spec_from_mapping(raw))
    return registry


def runtime_policy_from_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Extract metadata-only connector runtime policy from local config."""

    if not config:
        return {}
    integrations_cfg = config.get("integrations")
    if not isinstance(integrations_cfg, Mapping):
        return {}
    runtime_cfg = integrations_cfg.get("runtime_policy")
    if not isinstance(runtime_cfg, Mapping):
        return {}
    _reject_forbidden_metadata_keys("integrations.runtime_policy", runtime_cfg)
    return runtime_cfg


def plan_connector_runtime_gate(
    registry: IntegrationRegistry,
    connector_id: str,
    action: str,
    *,
    side_effect: SideEffect,
    sensitivity: DataSensitivity,
    payload_summary: Mapping[str, Any] | None = None,
    approved_by: ApprovalRole | None = None,
    runtime_policy: Mapping[str, Any] | None = None,
) -> tuple[PlannedOperation, SafetyDecision, ConnectorRuntimeGate]:
    """Plan the last local gate before a concrete connector executor runs.

    ``runtime_policy`` is intentionally small and metadata-only::

        integrations:
          runtime_policy:
            allowed_connectors: [hospital_fhir_readonly]
            allowed_capabilities: [patient_read, document_reference_draft]
            external_network_allowed: false

    The function performs no execution. It is a deterministic allowlist and
    redaction handoff for future SaaS/clinical adapters.
    """

    operation, safety = registry.plan_operation(
        connector_id,
        action,
        side_effect=side_effect,
        sensitivity=sensitivity,
        payload_summary=payload_summary,
        approved_by=approved_by,
    )
    spec = registry.get(connector_id)
    policy = runtime_policy or {}
    _reject_forbidden_metadata_keys("integrations.runtime_policy", policy)

    allowed_connectors = tuple(str(item) for item in policy.get("allowed_connectors", ()) or ())
    allowed_capabilities = tuple(str(item) for item in policy.get("allowed_capabilities", ()) or ())
    connector_allowlisted = connector_id in allowed_connectors if allowed_connectors else False
    capability_allowlisted = action in spec.capabilities or action in allowed_capabilities
    external_network_allowed = bool(policy.get("external_network_allowed", False)) and spec.domain != IntegrationDomain.FILE

    if not connector_allowlisted:
        reason = "connector is not allowlisted in integrations.runtime_policy.allowed_connectors"
    elif not capability_allowlisted:
        reason = "action is not declared in connector capabilities or runtime_policy.allowed_capabilities"
    elif not safety.allowed:
        reason = f"blocked by Sinria safety policy: {safety.reason}"
    elif spec.domain != IntegrationDomain.FILE and not external_network_allowed:
        reason = "external network execution is not enabled by integrations.runtime_policy.external_network_allowed"
    else:
        reason = "runtime gate passed for an institution-approved adapter; use sanitized payload only"

    ready = connector_allowlisted and capability_allowlisted and safety.allowed and (
        spec.domain == IntegrationDomain.FILE or external_network_allowed
    )
    return operation, safety, ConnectorRuntimeGate(
        ready_for_execution=ready,
        reason=reason,
        connector_allowlisted=connector_allowlisted,
        capability_allowlisted=capability_allowlisted,
        external_network_allowed_by_gate=external_network_allowed,
    )


_MEDEVIDENCE_SKILLS: tuple[MedEvidenceSkillSpec, ...] = (
    MedEvidenceSkillSpec("consensus-search", "エビデンス統合検索", "search", False, True, "release"),
    MedEvidenceSkillSpec("frontier-search", "フロンティア検索", "search", False, True, "release"),
    MedEvidenceSkillSpec("guideline-search", "ガイドライン検索", "search", False, True, "release"),
    MedEvidenceSkillSpec("intent-router", "意図分析ルーター", "general", False, True, "release"),
    MedEvidenceSkillSpec("fact-checker", "ファクトチェック・引用管理", "research", False, True, "release"),
    MedEvidenceSkillSpec("voice-search", "音声検索", "search", False, True, "release"),
    MedEvidenceSkillSpec("paper-writing", "論文執筆支援", "research", False, True, "release"),
    MedEvidenceSkillSpec("msl-briefing", "MSL エビデンスブリーフィング", "research", False, True, "draft", True),
    MedEvidenceSkillSpec("mi-response-draft", "Medical Information 回答ドラフト", "research", False, True, "draft", True),
    MedEvidenceSkillSpec("guideline-monitor", "ガイドライン改訂モニタ", "search", False, True, "draft", True),
    MedEvidenceSkillSpec("chart-summary", "カルテ要約", "clinical_doc", True, False, "draft"),
    MedEvidenceSkillSpec("referral-letter", "紹介状作成", "clinical_doc", True, False, "draft", True),
    MedEvidenceSkillSpec("after-visit-summary", "受診後サマリー", "patient_facing", True, False, "draft", True),
    MedEvidenceSkillSpec("patient-education", "患者説明スライド", "patient_facing", True, False, "draft", True),
    MedEvidenceSkillSpec("informed-consent", "治験IC説明スライド", "patient_facing", True, False, "draft", True),
    MedEvidenceSkillSpec("econsent", "eConsent", "patient_facing", True, False, "draft", True),
    MedEvidenceSkillSpec("drug-safety-check", "薬剤安全性チェック", "clinical_doc", True, False, "draft"),
    MedEvidenceSkillSpec("surgical-decision", "術式意思決定支援", "clinical_doc", True, False, "draft", True),
    MedEvidenceSkillSpec("clinical-action-plan", "臨床アクションプラン", "clinical_doc", True, False, "draft", True),
    MedEvidenceSkillSpec("medical-fee", "診療報酬算定支援", "coding", False, True, "draft", True),
    MedEvidenceSkillSpec("rehabilitation-advisor", "リハビリテーション支援", "clinical_doc", True, False, "draft", True),
    MedEvidenceSkillSpec("nursing-procedure-guide", "看護手技・ケアガイド", "clinical_doc", True, False, "draft", True),
)


def medevidence_skill_catalog() -> list[MedEvidenceSkillSpec]:
    """Return Sinria's local manifest of MedEvidence/OpenClaw skills.

    The catalog mirrors MedEvidence's OpenClaw skill IDs and safety metadata but
    does not import or execute the MedEvidence repository.  Concrete execution
    remains a later, institution-approved local adapter step.
    """

    return sorted(_MEDEVIDENCE_SKILLS, key=lambda spec: spec.id)


def get_medevidence_skill(skill_id: str) -> MedEvidenceSkillSpec:
    """Fetch one MedEvidence skill from the local adapter manifest."""

    for spec in _MEDEVIDENCE_SKILLS:
        if spec.id == skill_id:
            return spec
    raise KeyError(f"Unknown MedEvidence skill: {skill_id}")


_CONNECTOR_TEMPLATES: tuple[ConnectorTemplate, ...] = (
    ConnectorTemplate(
        id="google_workspace_draft",
        display_name="Google Workspace draft-only connector",
        domain=IntegrationDomain.SAAS,
        protocol="Google REST APIs / OAuth",
        config_example={
            "id": "hospital_google_workspace",
            "display_name": "Hospital Google Workspace",
            "domain": "saas",
            "protocol": "Google REST APIs / OAuth",
            "capabilities": ["gmail_draft", "calendar_draft", "drive_pack", "docs_draft"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Draft-first; no PHI unless institution policy permits Workspace storage.",
        },
        secret_location="OAuth/client secrets stay in .env or institutional secret manager; never in integrations.connectors.",
        safety_notes=(
            "Use for draft packs, internal approvals, and audit artifacts before external send/share.",
            "Sending email or sharing Drive/Docs requires admin or compliance approval.",
        ),
    ),
    ConnectorTemplate(
        id="slack_approval_channel",
        display_name="Slack approval-channel connector",
        domain=IntegrationDomain.SAAS,
        protocol="Slack Web/API Events",
        config_example={
            "id": "hospital_slack_approvals",
            "display_name": "Hospital Slack approval channel",
            "domain": "saas",
            "protocol": "Slack Web/API Events",
            "capabilities": ["message_draft", "approval_request", "channel_summary"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Approval metadata only; avoid PHI in Slack unless explicitly approved.",
        },
        secret_location="Slack bot/user tokens stay in .env or the gateway platform config, not connector metadata.",
        safety_notes=(
            "Use sanitized task summaries and approval IDs, not raw chart text.",
            "Posting to channels is a send side effect and remains approval-gated.",
        ),
    ),
    ConnectorTemplate(
        id="microsoft_365_draft",
        display_name="Microsoft 365 draft-only connector",
        domain=IntegrationDomain.SAAS,
        protocol="Microsoft Graph / OAuth",
        config_example={
            "id": "hospital_microsoft_365",
            "display_name": "Hospital Microsoft 365",
            "domain": "saas",
            "protocol": "Microsoft Graph / OAuth",
            "capabilities": ["outlook_draft", "calendar_draft", "sharepoint_pack", "teams_message_draft"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Draft-first; do not store PHI in M365 unless institution policy explicitly permits it.",
        },
        secret_location="Tenant IDs, app IDs, OAuth tokens, and Graph endpoints stay in .env or institutional secret manager.",
        safety_notes=(
            "Use for internal draft packs and approval artifacts before any Teams/Outlook/SharePoint release.",
            "Sending mail, posting Teams messages, or sharing files is approval-gated.",
        ),
    ),
    ConnectorTemplate(
        id="salesforce_health_cloud_draft",
        display_name="Salesforce / Health Cloud draft connector",
        domain=IntegrationDomain.SAAS,
        protocol="Salesforce REST/Bulk APIs / OAuth",
        config_example={
            "id": "hospital_salesforce_health_cloud",
            "display_name": "Hospital Salesforce Health Cloud",
            "domain": "saas",
            "protocol": "Salesforce REST/Bulk APIs / OAuth",
            "capabilities": ["case_read", "task_draft", "care_plan_draft", "audit_note_draft"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Treat as CRM/workflow metadata unless the deployment has an approved clinical-data boundary.",
        },
        secret_location="Org URLs, connected-app secrets, refresh tokens, and patient/customer identifiers stay outside connector metadata.",
        safety_notes=(
            "Use for sanitized case triage, task drafts, and audit handoffs.",
            "Escalate to clinical-system policy before planning patient-level data in Health Cloud.",
        ),
    ),
    ConnectorTemplate(
        id="jira_service_management_draft",
        display_name="Jira Service Management draft connector",
        domain=IntegrationDomain.SAAS,
        protocol="Atlassian Jira REST APIs / OAuth",
        config_example={
            "id": "hospital_jira_service_management",
            "display_name": "Hospital Jira Service Management",
            "domain": "saas",
            "protocol": "Atlassian Jira REST APIs / OAuth",
            "capabilities": ["issue_read", "ticket_draft", "approval_request", "audit_ticket"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Use sanitized operational metadata; do not place PHI in Jira unless the institution has approved that boundary.",
        },
        secret_location="Atlassian site URLs, cloud IDs, OAuth tokens, and API tokens stay in .env or an institutional secret manager.",
        safety_notes=(
            "Use for IT/service-desk handoffs, approval tasks, and audit tickets with sanitized summaries.",
            "Creating/updating tickets is a write side effect and remains approval-gated for confidential deployments.",
        ),
    ),
    ConnectorTemplate(
        id="servicenow_itsm_draft",
        display_name="ServiceNow ITSM draft connector",
        domain=IntegrationDomain.SAAS,
        protocol="ServiceNow Table/API / OAuth",
        config_example={
            "id": "hospital_servicenow_itsm",
            "display_name": "Hospital ServiceNow ITSM",
            "domain": "saas",
            "protocol": "ServiceNow Table/API / OAuth",
            "capabilities": ["incident_read", "change_request_draft", "approval_request", "audit_record"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Operational workflow metadata only unless ServiceNow is explicitly approved for regulated clinical content.",
        },
        secret_location="Instance URLs, OAuth credentials, integration users, and tokens stay outside integrations.connectors.",
        safety_notes=(
            "Use for change/incident workflow around Sinria integration rollouts and approval evidence.",
            "Do not route raw chart text, patient IDs, or clinical payloads into ITSM records by default.",
        ),
    ),
    ConnectorTemplate(
        id="zendesk_support_draft",
        display_name="Zendesk support draft connector",
        domain=IntegrationDomain.SAAS,
        protocol="Zendesk REST APIs / OAuth",
        config_example={
            "id": "hospital_zendesk_support",
            "display_name": "Hospital Zendesk support",
            "domain": "saas",
            "protocol": "Zendesk REST APIs / OAuth",
            "capabilities": ["ticket_read", "reply_draft", "macro_draft", "audit_note_draft"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Draft-first support operations; avoid PHI unless Zendesk storage is institution-approved.",
        },
        secret_location="Zendesk subdomains, OAuth tokens, API tokens, and user identifiers stay in approved secret storage.",
        safety_notes=(
            "Use for de-identified support triage and approved response drafts.",
            "Sending replies or updating tickets is approval-gated; patient-facing content requires clinical review.",
        ),
    ),
    ConnectorTemplate(
        id="box_enterprise_draft",
        display_name="Box Enterprise draft connector",
        domain=IntegrationDomain.SAAS,
        protocol="Box Platform APIs / OAuth/JWT",
        config_example={
            "id": "hospital_box_enterprise",
            "display_name": "Hospital Box Enterprise",
            "domain": "saas",
            "protocol": "Box Platform APIs / OAuth/JWT",
            "capabilities": ["folder_read", "file_pack_draft", "metadata_template_draft", "approval_artifact"],
            "max_sensitivity": "confidential",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": False,
            "notes": "Use approved folders and retention policies; do not store raw PHI unless Box is explicitly approved for that deployment.",
        },
        secret_location="Enterprise IDs, app config JSON, JWT keys, and access tokens stay outside connector metadata.",
        safety_notes=(
            "Use for controlled document packs and audit artifacts when enterprise DLP/retention policy is configured.",
            "Sharing links, uploads, deletes, or permission changes are gated side effects.",
        ),
    ),
    ConnectorTemplate(
        id="smart_on_fhir_readonly",
        display_name="SMART-on-FHIR read-only clinical connector",
        domain=IntegrationDomain.CLINICAL,
        protocol="HL7 FHIR R4 REST / SMART-on-FHIR OAuth",
        config_example={
            "id": "hospital_fhir_readonly",
            "display_name": "Hospital FHIR read-only sandbox",
            "domain": "clinical",
            "protocol": "HL7 FHIR R4 REST / SMART-on-FHIR OAuth",
            "capabilities": ["patient_read", "encounter_read", "observation_read", "document_reference_draft"],
            "max_sensitivity": "patient",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": True,
            "notes": "Prefer sandbox/read-only scopes first; writeback requires physician approval and adapter review.",
        },
        secret_location="FHIR base URL, client ID/secret, and scopes stay in .env/secret manager; do not log tokens or patient IDs.",
        safety_notes=(
            "Start with read-only patient/observation/document scopes inside the hospital network or VPN.",
            "Any EHR/EMR writeback, patient message, or release path requires physician approval.",
        ),
    ),
    ConnectorTemplate(
        id="hl7v2_interface_engine_readonly",
        display_name="HL7 v2 interface-engine read-only connector",
        domain=IntegrationDomain.CLINICAL,
        protocol="HL7 v2 over MLLP or local file drop",
        config_example={
            "id": "hospital_hl7v2_feed",
            "display_name": "Hospital HL7 v2 read-only feed",
            "domain": "clinical",
            "protocol": "HL7 v2 over MLLP/file drop via interface engine",
            "capabilities": ["adt_read", "oru_read", "mdm_document_draft"],
            "max_sensitivity": "patient",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": True,
            "notes": "Read-only interface-engine feed; acknowledgements and routing changes need institutional review.",
        },
        secret_location="MLLP host/port, VPN details, and feed credentials stay outside config examples and logs.",
        safety_notes=(
            "Use local interface-engine deployment and deterministic parsers before LLM summarization.",
            "Do not generate ADT/ORU/MDM write messages without physician plus interface-owner approval.",
        ),
    ),
    ConnectorTemplate(
        id="ehr_export_file_local",
        display_name="Local EHR/カルテ export-file connector",
        domain=IntegrationDomain.FILE,
        protocol="local CSV/JSON/PDF export directory",
        config_example={
            "id": "hospital_ehr_export_staging",
            "display_name": "Hospital EHR/カルテ export staging",
            "domain": "file",
            "protocol": "local CSV/JSON/PDF export directory",
            "capabilities": ["local_import", "redacted_summary", "audit_pack"],
            "max_sensitivity": "patient",
            "requires_approval_for": ["delete"],
            "clinical_system": True,
            "notes": "Local-only staging for hospitals without approved direct EMR API access.",
        },
        secret_location="File paths and access controls are deployment-specific; raw exports stay on approved local storage.",
        safety_notes=(
            "Safest first deployment path for hospitals where vendor APIs are unavailable.",
            "Summaries must store redacted metadata, not full chart bodies, in Sinria logs.",
        ),
    ),
    ConnectorTemplate(
        id="medevidence_local_bridge",
        display_name="MedEvidence local skill bridge",
        domain=IntegrationDomain.FILE,
        protocol="local MedEvidence/OpenClaw skill checkout",
        config_example={
            "id": "medevidence_local",
            "display_name": "MedEvidence / メドエビデンス local bridge",
            "domain": "file",
            "protocol": "local MedEvidence skill/knowledge adapter",
            "capabilities": ["evidence_search_plan", "fact_check_summary", "clinical_action_plan_draft"],
            "max_sensitivity": "patient",
            "requires_approval_for": ["write", "send", "delete"],
            "clinical_system": True,
            "notes": "Plan only until an institution-approved local adapter is implemented.",
        },
        secret_location="Local checkout path may be configured via skills.external_dirs; no TypeScript imports or patient data reads in setup checks.",
        safety_notes=(
            "Use medevidence_setup_status before planning any bridge work.",
            "Clinical MedEvidence outputs remain drafts until physician approval.",
        ),
    ),
)


def connector_template_catalog() -> list[ConnectorTemplate]:
    """Return safe connector configuration templates for SaaS/clinical setup."""

    return sorted(_CONNECTOR_TEMPLATES, key=lambda template: template.id)


def describe_medevidence_skill_usage(skill_id: str) -> MedEvidenceSkillUsageGuide:
    """Return concrete Sinria instructions for using a MedEvidence skill safely.

    This is still metadata-only. It does not import MedEvidence TypeScript,
    inspect patient payloads, or call external evidence sources. The guide is
    meant to make each OpenClaw skill usable from Sinria by routing the user or
    agent through the local planner and the correct PHI/approval boundary.
    """

    spec = get_medevidence_skill(skill_id)
    if spec.accepts_phi:
        sensitivity = DataSensitivity.PATIENT
        allowed_input = (
            "Local, institution-approved clinical excerpts or structured summaries; "
            "keep raw chart/カルテ bodies in the approved local system whenever possible."
        )
        forbidden_input = (
            "Unapproved external transmission, patient messaging, EHR/EMR writeback, "
            "or full raw chart bodies in Sinria prompts/logs."
        )
        default_path = "Plan a local draft via the medevidence_local bridge."
        approval_boundary = (
            "Release/send/writeback requires physician approval and an institution-approved adapter."
        )
    elif spec.external_transmission:
        sensitivity = DataSensitivity.PUBLIC
        allowed_input = (
            "Public or de-identified medical question, citation target, guideline topic, "
            "or research task with no patient identifiers."
        )
        forbidden_input = (
            "PHI, MRNs/カルテ番号, patient names, contact details, or case-specific clinical notes."
        )
        default_path = "Plan a de-identified public evidence/research operation."
        approval_boundary = (
            "Do not raise sensitivity to patient; de-identify first or choose a PHI-capable draft skill."
        )
    else:
        sensitivity = DataSensitivity.INTERNAL
        allowed_input = "Internal workflow metadata or de-identified operational context."
        forbidden_input = "Raw PHI or external release without an explicit policy gate."
        default_path = "Plan an internal/local draft through the medevidence_local bridge."
        approval_boundary = "External release remains gated by the connector safety decision."

    return MedEvidenceSkillUsageGuide(
        skill_id=spec.id,
        display_name=spec.display_name,
        category=spec.category,
        allowed_input=allowed_input,
        forbidden_input=forbidden_input,
        default_sinria_path=default_path,
        approval_boundary=approval_boundary,
        suggested_planner_call={
            "mode": "plan_medevidence_skill",
            "skill_id": spec.id,
            "sensitivity": sensitivity.value,
            "release": False,
            "payload_summary": {"topic_or_task": "sanitized summary only"},
        },
    )


def medevidence_skill_bridge_stubs() -> list[MedEvidenceSkillBridgeStub]:
    """Return generated Sinria bridge-stub metadata for MedEvidence skills.

    This is the safe short-term skill-migration surface: instead of importing or
    executing MedEvidence/OpenClaw TypeScript, Sinria can expose per-skill
    instructions that always call ``describe_medevidence_skill`` and
    ``plan_medevidence_skill`` first. The returned objects are metadata only and
    contain no patient data, endpoints, tokens, or local file paths.
    """

    stubs: list[MedEvidenceSkillBridgeStub] = []
    for spec in medevidence_skill_catalog():
        guide = describe_medevidence_skill_usage(spec.id)
        if spec.accepts_phi:
            first_step = (
                "Use only local, institution-approved clinical excerpts or structured summaries; "
                "prefer references to approved local systems over raw chart/カルテ text."
            )
        elif spec.external_transmission:
            first_step = (
                "De-identify the question first; public/external evidence workflows must not receive PHI, "
                "MRNs/カルテ番号, patient names, or case-specific notes."
            )
        else:
            first_step = "Use internal workflow metadata or de-identified operational context only."

        stubs.append(
            MedEvidenceSkillBridgeStub(
                skill_name=f"medevidence-{spec.id}",
                source_skill_id=spec.id,
                title=f"MedEvidence bridge: {spec.display_name}",
                description=(
                    f"Sinria-safe bridge instructions for MedEvidence/OpenClaw skill `{spec.id}`. "
                    "Planning only; no TypeScript import/execution and no external calls are performed by the stub."
                ),
                safe_steps=(
                    first_step,
                    "Call sinria_integrations with mode=describe_medevidence_skill to confirm allowed input and approval boundary.",
                    "Call sinria_integrations with mode=plan_medevidence_skill using a sanitized payload_summary before any adapter execution.",
                    "Treat release, patient messaging, SaaS sharing, or EHR/EMR writeback as physician-approved operations only.",
                ),
                forbidden=(
                    guide.forbidden_input,
                    "Do not import or execute MedEvidence TypeScript from Sinria bridge stubs.",
                    "Do not place endpoints, OAuth tokens, patient identifiers, or raw PHI in bridge metadata.",
                ),
                planner_call=guide.suggested_planner_call,
            )
        )
    return stubs


def plan_medevidence_skill_operation(
    skill_id: str,
    *,
    query_summary: Mapping[str, Any] | None = None,
    sensitivity: DataSensitivity = DataSensitivity.PUBLIC,
    release: bool = False,
    approved_by: ApprovalRole | None = None,
) -> tuple[PlannedOperation, SafetyDecision]:
    """Plan a safe local MedEvidence skill operation from Sinria.

    Public evidence-search skills may use external public evidence sources, but
    they must not receive patient-specific content.  PHI-capable clinical skills
    are planned as local drafts by default.  Any release/send/writeback path is
    routed through the ``medevidence_local`` clinical connector and therefore
    requires physician approval.
    """

    med_skill = get_medevidence_skill(skill_id)
    if sensitivity == DataSensitivity.PATIENT and not med_skill.accepts_phi:
        operation = PlannedOperation(
            connector_id="medevidence_local",
            action=f"medevidence.{skill_id}",
            side_effect=SideEffect.READ,
            sensitivity=sensitivity,
            payload_summary=sanitize_payload_summary(query_summary),
            approved_by=approved_by,
        )
        return operation, SafetyDecision(
            allowed=False,
            reason=(
                f"MedEvidence skill {skill_id} is not PHI-capable; de-identify "
                "patient data or choose a local clinical draft skill"
            ),
        )

    side_effect = SideEffect.SEND if release else SideEffect.DRAFT
    registry = default_registry()
    return registry.plan_operation(
        "medevidence_local",
        f"medevidence.{skill_id}",
        side_effect=side_effect,
        sensitivity=sensitivity,
        payload_summary={
            "skill_id": med_skill.id,
            "display_name": med_skill.display_name,
            "category": med_skill.category,
            "external_transmission": med_skill.external_transmission,
            "max_autonomous_action": med_skill.max_autonomous_action,
            "approval_required": med_skill.approval_required,
            "query_summary": query_summary or {},
        },
        approved_by=approved_by,
    )


def inventory_ehr_export_directory(
    export_dir: str | Path,
    *,
    allowed_extensions: Iterable[str] = _DEFAULT_EHR_EXPORT_EXTENSIONS,
) -> EHRExportInventory:
    """Return a de-identified local EHR/カルテ export inventory.

    This is the first concrete local fallback adapter for hospitals that cannot
    expose an approved EMR/EHR API. It scans filesystem metadata only: no file
    contents are opened or parsed, and file names/paths are not returned.
    """

    allowed = tuple(sorted({ext.lower() if str(ext).startswith(".") else f".{str(ext).lower()}" for ext in allowed_extensions}))
    root = Path(export_dir).expanduser()
    if not root.exists():
        raise FileNotFoundError("EHR export directory does not exist")
    if not root.is_dir():
        raise NotADirectoryError("EHR export path must be a directory")

    counts: dict[str, int] = {}
    total_bytes = 0
    file_count = 0
    skipped = 0
    for child in root.iterdir():
        if not child.is_file():
            continue
        suffix = child.suffix.lower()
        if suffix not in allowed:
            skipped += 1
            continue
        file_count += 1
        counts[suffix] = counts.get(suffix, 0) + 1
        try:
            total_bytes += child.stat().st_size
        except OSError:
            skipped += 1

    return EHRExportInventory(
        directory_present=True,
        file_count=file_count,
        skipped_file_count=skipped,
        total_bytes=total_bytes,
        extensions=dict(sorted(counts.items())),
        allowed_extensions=allowed,
    )


def plan_ehr_export_file_import(
    export_dir: str | Path,
    *,
    connector_id: str = "ehr_export_file",
    approved_by: ApprovalRole | None = None,
) -> tuple[PlannedOperation, SafetyDecision]:
    """Plan a local-only EHR/カルテ export import without reading PHI.

    The returned ``PlannedOperation`` contains only de-identified filesystem
    metadata (counts, extensions, byte totals). Absolute paths, filenames, and
    clinical content are intentionally omitted.
    """

    inventory = inventory_ehr_export_directory(export_dir)
    registry = default_registry()
    return registry.plan_operation(
        connector_id,
        "import_local_ehr_export_manifest",
        side_effect=SideEffect.READ,
        sensitivity=DataSensitivity.PATIENT,
        payload_summary={
            "export_dir": "[LOCAL_PATH_REDACTED]",
            "inventory": {
                "directory_present": inventory.directory_present,
                "file_count": inventory.file_count,
                "skipped_file_count": inventory.skipped_file_count,
                "total_bytes": inventory.total_bytes,
                "extensions": dict(inventory.extensions),
                "allowed_extensions": list(inventory.allowed_extensions),
            },
            "content_read": False,
            "filenames_returned": False,
        },
        approved_by=approved_by,
    )


def decide_safety(spec: ConnectorSpec, operation: PlannedOperation) -> SafetyDecision:
    """Apply Sinria's default regulated-data connector policy."""

    sensitivity_order = list(DataSensitivity)
    if sensitivity_order.index(operation.sensitivity) > sensitivity_order.index(spec.max_sensitivity):
        return SafetyDecision(
            allowed=False,
            reason=(
                f"{spec.id} is capped at {spec.max_sensitivity.value} data but "
                f"operation is {operation.sensitivity.value}"
            ),
        )

    if operation.side_effect not in spec.requires_approval_for:
        return SafetyDecision(allowed=True, reason="read/draft operation permitted locally")

    required = ApprovalRole.PHYSICIAN if spec.clinical_system else ApprovalRole.ADMIN
    approver = operation.approved_by
    approved = approver == required or (
        required == ApprovalRole.ADMIN and approver == ApprovalRole.COMPLIANCE
    )
    if approved:
        assert approver is not None
        return SafetyDecision(allowed=True, reason=f"approved by {approver.value}")

    return SafetyDecision(
        allowed=False,
        reason=(
            f"{operation.side_effect.value} requires {required.value} approval before "
            "external side effects are released"
        ),
        required_role=required,
    )


def default_registry() -> IntegrationRegistry:
    """Return Sinria's built-in SaaS and clinical connector metadata."""

    return IntegrationRegistry(
        [
            ConnectorSpec(
                id="google_workspace",
                display_name="Google Workspace",
                domain=IntegrationDomain.SAAS,
                protocol="Google REST APIs",
                capabilities=("gmail_draft", "calendar_draft", "drive_pack", "docs_draft", "sheets_audit"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Draft-first by default; sending/sharing requires approval.",
            ),
            ConnectorSpec(
                id="slack",
                display_name="Slack",
                domain=IntegrationDomain.SAAS,
                protocol="Slack Web/API Events",
                capabilities=("message_draft", "channel_summary", "approval_request"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
            ),
            ConnectorSpec(
                id="microsoft_365",
                display_name="Microsoft 365",
                domain=IntegrationDomain.SAAS,
                protocol="Microsoft Graph / OAuth",
                capabilities=("outlook_draft", "calendar_draft", "sharepoint_pack", "teams_message_draft"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Draft-first by default; Teams/Outlook send/share requires approval.",
            ),
            ConnectorSpec(
                id="salesforce_health_cloud",
                display_name="Salesforce Health Cloud",
                domain=IntegrationDomain.SAAS,
                protocol="Salesforce REST/Bulk APIs / OAuth",
                capabilities=("case_read", "task_draft", "care_plan_draft", "audit_note_draft"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Use for sanitized CRM/care-coordination metadata unless a clinical-data boundary is approved.",
            ),
            ConnectorSpec(
                id="jira_service_management",
                display_name="Jira Service Management",
                domain=IntegrationDomain.SAAS,
                protocol="Atlassian Jira REST APIs / OAuth",
                capabilities=("issue_read", "ticket_draft", "approval_request", "audit_ticket"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Use sanitized operational metadata; ticket writes and notifications require approval.",
            ),
            ConnectorSpec(
                id="servicenow_itsm",
                display_name="ServiceNow ITSM",
                domain=IntegrationDomain.SAAS,
                protocol="ServiceNow Table/API / OAuth",
                capabilities=("incident_read", "change_request_draft", "approval_request", "audit_record"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Operational workflow metadata only unless regulated-data storage is explicitly approved.",
            ),
            ConnectorSpec(
                id="zendesk_support",
                display_name="Zendesk Support",
                domain=IntegrationDomain.SAAS,
                protocol="Zendesk REST APIs / OAuth",
                capabilities=("ticket_read", "reply_draft", "macro_draft", "audit_note_draft"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Draft-first support operations; sending replies or ticket updates requires approval.",
            ),
            ConnectorSpec(
                id="box_enterprise",
                display_name="Box Enterprise",
                domain=IntegrationDomain.SAAS,
                protocol="Box Platform APIs / OAuth/JWT",
                capabilities=("folder_read", "file_pack_draft", "metadata_template_draft", "approval_artifact"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
                notes="Use approved folders and retention/DLP boundaries; sharing/uploads/deletes require approval.",
            ),
            ConnectorSpec(
                id="notion",
                display_name="Notion",
                domain=IntegrationDomain.SAAS,
                protocol="Notion REST API",
                capabilities=("page_draft", "database_row", "knowledge_base_sync"),
                max_sensitivity=DataSensitivity.CONFIDENTIAL,
            ),
            ConnectorSpec(
                id="linear",
                display_name="Linear",
                domain=IntegrationDomain.SAAS,
                protocol="Linear GraphQL API",
                capabilities=("issue_draft", "status_read", "audit_ticket"),
                max_sensitivity=DataSensitivity.INTERNAL,
            ),
            ConnectorSpec(
                id="fhir_r4",
                display_name="FHIR R4 endpoint",
                domain=IntegrationDomain.CLINICAL,
                protocol="HL7 FHIR R4 REST",
                capabilities=("patient_read", "encounter_read", "document_reference_draft", "observation_read"),
                max_sensitivity=DataSensitivity.PATIENT,
                clinical_system=True,
                notes="Use SMART-on-FHIR/OAuth scopes and institution approval before live access.",
            ),
            ConnectorSpec(
                id="hl7v2_mllp",
                display_name="HL7 v2 interface",
                domain=IntegrationDomain.CLINICAL,
                protocol="HL7 v2 over MLLP/file drop",
                capabilities=("adt_read", "oru_read", "mdm_document_draft"),
                max_sensitivity=DataSensitivity.PATIENT,
                clinical_system=True,
                notes="Prefer read-only feeds and local interface-engine deployment.",
            ),
            ConnectorSpec(
                id="ehr_export_file",
                display_name="EHR/カルテ export file",
                domain=IntegrationDomain.FILE,
                protocol="local CSV/JSON/PDF export",
                capabilities=("local_import", "redacted_summary", "audit_pack"),
                max_sensitivity=DataSensitivity.PATIENT,
                requires_approval_for=(SideEffect.DELETE,),
                clinical_system=True,
                notes="Local-only staging path for hospitals where direct EMR APIs are unavailable.",
            ),
            ConnectorSpec(
                id="medevidence_local",
                display_name="MedEvidence / メドエビデンス local bridge",
                domain=IntegrationDomain.FILE,
                protocol="local MedEvidence skill/knowledge adapter",
                capabilities=(
                    "evidence_search_plan",
                    "guideline_lookup_plan",
                    "fact_check_summary",
                    "draft_consent_workspace_plan",
                    "clinical_action_plan_draft",
                ),
                max_sensitivity=DataSensitivity.PATIENT,
                clinical_system=True,
                notes=(
                    "Local/read-only first bridge for operating MedEvidence from Sinria; "
                    "writeback, patient messaging, consent release, or SaaS sharing stay physician-gated."
                ),
            ),
        ]
    )


__all__ = [
    "ApprovalRole",
    "ConnectorRuntimeGate",
    "ConnectorSpec",
    "ConnectorTemplate",
    "DataSensitivity",
    "IntegrationDomain",
    "IntegrationRegistry",
    "EHRExportInventory",
    "MedEvidenceSkillSpec",
    "MedEvidenceSkillUsageGuide",
    "MedEvidenceSkillBridgeStub",
    "PlannedOperation",
    "SafetyDecision",
    "SideEffect",
    "connector_spec_from_mapping",
    "connector_template_catalog",
    "decide_safety",
    "default_registry",
    "describe_medevidence_skill_usage",
    "get_medevidence_skill",
    "medevidence_skill_catalog",
    "medevidence_skill_bridge_stubs",
    "inventory_ehr_export_directory",
    "plan_ehr_export_file_import",
    "plan_connector_runtime_gate",
    "plan_medevidence_skill_operation",
    "registry_from_config",
    "runtime_policy_from_config",
    "sanitize_payload_summary",
]
