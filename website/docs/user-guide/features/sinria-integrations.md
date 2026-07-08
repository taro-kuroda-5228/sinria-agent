---
title: Sinria integrations
---

# Sinria integrations

Sinria includes a local, safety-first integration planner for SaaS products, hospital clinical systems, and MedEvidence / メドエビデンス bridge work. The planner is metadata-only: it does not call external APIs, import MedEvidence TypeScript code, read patient charts, or transmit PHI.

Use it to define what a connector is allowed to touch before implementing a concrete adapter.

## Connector templates

The `sinria_integrations` tool supports `mode=list_connector_templates`. It returns copyable starter stanzas for `integrations.connectors`, including:

- Google Workspace draft artifacts.
- Microsoft 365 / Microsoft Graph draft artifacts.
- Salesforce Health Cloud draft/task metadata.
- Jira Service Management, ServiceNow ITSM, and Zendesk support workflow drafts.
- Box Enterprise controlled document-pack drafts.
- Slack approval-channel summaries.
- SMART-on-FHIR / FHIR R4 read-only clinical access.
- HL7 v2 interface-engine read-only feeds.
- Local EHR/カルテ CSV/JSON/PDF export staging.
- Local MedEvidence / メドエビデンス bridge planning, including generated Sinria-side bridge stubs for each MedEvidence/OpenClaw skill.

Templates intentionally exclude endpoint URLs, OAuth client secrets, access tokens, VPN details, patient identifiers, and raw clinical payloads. Store secrets in `.env` or the institution's approved secret manager instead. Sinria now fails fast if an `integrations.connectors` entry contains secret/endpoint-like fields such as `url`, `base_url`, `client_secret`, `tenant_id`, or `token`.

Example clinical metadata only:

```yaml
integrations:
  runtime_policy:
    allowed_connectors: [hospital_fhir_readonly]
    allowed_capabilities: [patient_read, document_reference_draft]
    external_network_allowed: false
  connectors:
    - id: hospital_fhir_readonly
      display_name: Hospital FHIR read-only sandbox
      domain: clinical
      protocol: HL7 FHIR R4 REST / SMART-on-FHIR OAuth
      capabilities: [patient_read, encounter_read, observation_read, document_reference_draft]
      max_sensitivity: patient
      requires_approval_for: [write, send, delete]
      clinical_system: true
      notes: Prefer sandbox/read-only scopes first; writeback requires physician approval and adapter review.
```

## Safety policy

Sinria's default policy is conservative:

- Read/draft operations are allowed locally when they stay within the connector's sensitivity cap.
- SaaS write/send/delete operations require admin or compliance approval.
- Clinical system write/send/delete operations require physician approval.
- MedEvidence clinical skills are planned as local drafts by default; release, patient messaging, or EHR/EMR writeback remains physician-gated.
- Public MedEvidence evidence-search skills must not receive PHI, MRNs/カルテ番号, patient names, contact details, or case-specific clinical notes.

Use `mode=list_medevidence_skill_stubs` to get generated Sinria bridge-stub metadata for every MedEvidence/OpenClaw skill. Each stub carries the safe input rule, forbidden PHI/external-transmission boundary, and a suggested `mode=plan_medevidence_skill` call, so Sinria can make MedEvidence skills usable while keeping execution behind local approval gates.

Before wiring any concrete SaaS/EMR/EHR adapter, run `sinria_integrations` with `mode=integration_readiness_report`, then `mode=plan_connector_runtime_gate`. The readiness report is a local preflight that summarizes configured connector IDs, runtime allowlists, unknown allowlist entries, connectors missing runtime allowlisting, and MedEvidence planning readiness without returning endpoint URLs, credentials, TypeScript source, file contents, or clinical data. The runtime gate is still local and metadata-only; it verifies the institution-owned `integrations.runtime_policy` allowlist, rejects endpoint/secret/patient-identifier fields anywhere in that policy, confirms the action is a declared/allowlisted capability, preserves sanitized `payload_summary` output for audit logs, and keeps raw payload logging disabled. Non-file connectors remain blocked unless `external_network_allowed: true` is explicitly set for that deployment.

For hospitals without approved direct EMR/EHR APIs, start with the local EHR/カルテ export-file connector and redacted summaries. Concrete vendor/API adapters should only be added after institution-specific review of scopes, network boundary, audit logging, and approval workflow.

The local fallback can be planned with `sinria_integrations` `mode=plan_ehr_export_import` and an approved local `export_dir`. That mode reads directory metadata only and returns counts, extensions, and byte totals; it does not return paths, filenames, PDF/text contents, rows, patient identifiers, or raw PHI.
