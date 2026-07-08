"""Root guards for the Sinria Sales Agent OS app (apps/chatops-crm).

Architecture note (why several of these guards were rewritten)
-------------------------------------------------------------
These guards were originally written at the "publish Sinria AgentOS baseline"
era (commit 882edae31) when ``apps/chatops-crm`` was an *inert prototype*: a CRM
board with disabled buttons, **no send capability**, and a TypeScript mirror of
the cloud bridge schema at ``apps/chatops-crm/lib/schema.ts``.

That app has since shipped as the production **Sinria Sales Agent OS**
(medical-horizon-ai-agent-crm.vercel.app). The recorded, shipped contract is an
*approval-gated sending workflow*, and the standalone Sales app must **not**
carry the cloud-schema TypeScript module (``lib/schema.ts``,
``lib/cloud-boundary.ts``, ``lib/repositories.ts``, ``lib/db.ts``,
``app/api/agent-tasks/route.ts``, ``app/api/review-requests/route.ts`` are now
*forbidden* inside the Sales app — see
``apps/chatops-crm/tests/schema-static.test.mjs`` ``forbiddenSalesAppPaths``).

The safety invariants the old guards pinned did **not** disappear — they moved:

* The shared cloud **agent_tasks bridge allowlist** and the **policy-boolean
  envelope** now live only in the SQL contract
  (``docs/sinria-hybrid-bridge-cloud-schema.sql``) plus the Sales app's
  server-side ``agent_tasks`` insert (``app/api/sales/draft/route.ts``).
* The **sanitized cloud surface** (no raw PHI/PII/secrets, ``sanitized_summary``
  required, ``external_action_performed = false`` on cloud note/audit rows) is
  pinned in the SQL contract + ``lib/sales-redaction.ts`` + the draft route's
  sanitized audit write.
* The **external send boundary** is now an *approval gate* (it is allowed, but
  guarded): two-stage server approval (``approve_for_real_send`` requires a
  prior dry-run approval *and* a connected/authorized sender) plus a daemon-side
  double gate (``RUN_APPROVED_OUTREACH=yes`` and
  ``approval_status == 'approved_for_real_send'``), with raw bodies executed
  locally via the public ``agent_tasks`` queue.

Each rewritten guard re-pins one of those moved invariants at equal-or-stronger
strength; the per-test docstrings call out the specific regression each catches.
"""

from pathlib import Path
import json

# Standalone-boundary modules: the cloud-schema TypeScript mirror that USED to
# live in the Sales app. The shipped architecture forbids them here (the cloud
# schema/agent-task surface lives in apps/company-os and in the SQL contract).
# Mirrors apps/chatops-crm/tests/schema-static.test.mjs forbiddenSalesAppPaths so
# these Python root guards fail too if the cloud-schema TS files are ever
# re-introduced into the standalone Sales app.
FORBIDDEN_CLOUD_SCHEMA_MODULES_IN_SALES_APP = [
    "apps/chatops-crm/lib/schema.ts",
    "apps/chatops-crm/lib/cloud-boundary.ts",
    "apps/chatops-crm/lib/repositories.ts",
    "apps/chatops-crm/lib/db.ts",
    "apps/chatops-crm/app/api/agent-tasks/route.ts",
    "apps/chatops-crm/app/api/review-requests/route.ts",
]


def test_chatops_crm_app_has_vercel_ready_next_scaffold_and_boundary_copy():
    """Vercel-ready scaffold + the Sales-only boundary copy.

    Stale-era replacement: the old guard read ``lib/schema.ts`` and pinned the
    inert-prototype page copy ("Sinria Hybrid Agent Bridge") plus the README
    phrase "Forbidden in cloud task rows". The TS schema mirror is now a
    *forbidden* file in this app, and the page mounts the production Workspace.

    Regression caught: deleting the Next scaffold; mounting something other than
    the standalone Sales Agent OS Workspace; dropping the Sales-only boundary
    documentation; OR re-introducing the cloud-schema TS module into the
    standalone Sales app. The cloud "Forbidden in cloud task rows" invariant is
    re-pinned where it actually lives (the SQL contract) below.
    """
    root = Path("apps/chatops-crm")

    assert (root / "package.json").exists()
    assert (root / "app/page.tsx").exists()
    assert (root / "app/layout.tsx").exists()
    page = (root / "app/page.tsx").read_text(encoding="utf-8")
    workspace = (root / "app/Workspace.tsx").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    # The root route is the standalone Sales Agent OS Workspace (not the old
    # inert "Hybrid Agent Bridge" board).
    assert "import { Workspace }" in page
    assert "<Workspace />" in page
    assert "Sinria Sales Agent OS" in workspace

    # Sales-only boundary copy is documented and intact.
    assert "This app must remain Sales-only" in readme
    assert "Forbidden surfaces in this app" in readme

    # Standalone boundary: the cloud-schema TS mirror stays out of this app.
    for forbidden in FORBIDDEN_CLOUD_SCHEMA_MODULES_IN_SALES_APP:
        assert not Path(forbidden).exists(), (
            f"{forbidden} must not live inside the standalone Sales app"
        )

    # The cloud "Forbidden in cloud task rows" invariant now lives in the SQL
    # contract: cloud note/audit rows are hard-constrained to never record an
    # external action.
    cloud_schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")
    assert "check (external_action_performed = false)" in cloud_schema


def test_chatops_crm_app_exposes_sales_board_with_approval_gated_send():
    """Sales board exposes a send capability that is approval-gated (not inert).

    Stale-era replacement: this guard previously asserted the app had **no send
    capability** ("no-contact/no-send" in page.tsx) and pinned the prototype
    type names from ``lib/schema.ts``. The shipped contract *inverts* that: the
    Sales Agent OS DOES send, but only behind a human approval gate. Pinning the
    old "without send capability" property would now actively protect a
    regression that removed the safety gate while keeping send, so it is
    replaced by the modern equal-strength invariant.

    Regression caught: the real-send server action losing its two-stage approval
    precondition (a draft promoted straight to real send without a prior dry-run
    approval / dry-run-sent state), OR losing the authorized-sender fail-closed
    check. Both are the actual external-send safety gates.
    """
    root = Path("apps/chatops-crm")
    page = (root / "app/page.tsx").read_text(encoding="utf-8")
    draft_route = (root / "app/api/sales/draft/route.ts").read_text(encoding="utf-8")

    # The board is the production Sales Agent OS workspace.
    assert "<Workspace />" in page

    # Send exists but is gated. The real-send action is present...
    assert '"approve_for_real_send"' in draft_route
    # ...and it requires a PRIOR dry-run approval (two-stage gate). If someone
    # deletes this precondition, a draft could jump straight to real send.
    assert 'prev !== "approved_for_dry_run" && draft.send_status !== "dry_run_sent"' in draft_route
    # ...and it fails closed when no authorized/connected sender is known.
    assert "接続済み送信者が未確認のため実送信を承認できません" in draft_route

    # The downstream queue distinguishes real outbound kinds (so the daemon's
    # gated handlers are the ones that actually egress).
    assert "real_send" in draft_route
    assert "real_form_submit" in draft_route


def test_hybrid_bridge_cloud_schema_connects_crm_tables_to_agent_review_tables():
    schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")

    for table_name in [
        "companies",
        "contacts",
        "leads",
        "campaigns",
        "outreach_drafts",
        "outreach_jobs",
        "interactions",
        "agent_notes",
        "audit_logs",
    ]:
        assert f"create table if not exists {table_name}" in schema
    assert "agent_task_id text references agent_tasks(id)" in schema
    assert "review_request_id text references review_requests(id)" in schema
    assert "check (job_kind not in ('real_send', 'real_form_submit') or status in ('blocked', 'requires_human_confirmation'))" in schema


def test_hybrid_bridge_cloud_schema_constrains_task_and_job_policy_values():
    schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")

    assert "check (side_effect in ('read', 'draft', 'write', 'send', 'delete'))" in schema
    assert "check (sensitivity in ('public', 'internal', 'confidential', 'patient'))" in schema
    assert "check (status in ('pending', 'claimed', 'running', 'waiting_review', 'completed', 'failed_recoverable', 'cancel_requested', 'cancelled'))" in schema
    assert "check (job_kind in ('sync', 'status', 'dry_run', 'real_send', 'real_form_submit'))" in schema
    assert "check (status in ('queued', 'running', 'completed', 'blocked', 'requires_human_confirmation', 'failed_recoverable'))" in schema


def test_chatops_crm_app_agent_task_schema_matches_shared_bridge_app_allowlist():
    """The Sales app's agent_tasks rows match the shared cloud bridge allowlist.

    Stale-era replacement: the old guard read the ``HybridBridgeAppId`` union
    from the now-forbidden ``lib/schema.ts`` TS mirror. The allowlist invariant
    survives in two real, shipped places re-pinned here:
      1. the SQL contract's hard CHECK on ``agent_tasks.app_id``, and
      2. the Sales app's actual server-side insert into ``agent_tasks`` with
         ``app_id: "chatops_crm"`` (apps/chatops-crm/app/api/sales/draft/route.ts).

    Regression caught: widening/altering the cloud ``agent_tasks`` app_id
    allowlist; OR the Sales app emitting a bridge row under a wrong/spoofed
    ``app_id`` that the cloud CHECK would reject (breaking the bridge). Also
    re-fails if the cloud-schema TS mirror is smuggled back into the Sales app.
    """
    readme = Path("apps/chatops-crm/README.md").read_text(encoding="utf-8")
    cloud_schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")
    draft_route = Path("apps/chatops-crm/app/api/sales/draft/route.ts").read_text(encoding="utf-8")

    # The shared bridge allowlist (the cross-app contract) is fixed in SQL.
    assert "check (app_id in ('chatops_crm', 'sierra_service', 'consent_agent'))" in cloud_schema

    # The Sales app emits bridge rows under its own allowlisted app_id only.
    assert 'app_id: "chatops_crm"' in draft_route

    # The standalone Sales app must NOT carry the cloud-schema TS mirror.
    assert not Path("apps/chatops-crm/lib/schema.ts").exists()

    # Sales-only README still documents its single-OS boundary (no Company OS /
    # cross-OS surfaces mounted here).
    assert "Sinria Sales Agent OS" in readme
    assert "must remain Sales-only" in readme


def test_chatops_crm_agent_task_envelope_carries_shared_policy_booleans_for_sierra_exports():
    """The shared agent-task policy-boolean envelope is enforced end to end.

    Stale-era replacement: the old guard read the ``AgentTaskPolicy`` TypeScript
    type from the now-forbidden ``lib/schema.ts``. The envelope invariant is
    re-pinned where it actually ships:
      1. the SQL contract columns/defaults on ``agent_tasks`` (the durable
         cross-app envelope), and
      2. the Sales app stamping every policy field on each ``agent_tasks``
         insert (so a Sales-emitted bridge row carries the full envelope, not a
         partial/missing-policy row).

    Regression caught: dropping or relaxing a policy column/default in the cloud
    contract; OR the Sales app stamping ``external_action_performed`` /
    ``human_approval_required`` / ``review_required`` etc. with unsafe values (or
    omitting them) on its bridge rows.
    """
    cloud_schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")
    draft_route = Path("apps/chatops-crm/app/api/sales/draft/route.ts").read_text(encoding="utf-8")

    # 1. Durable envelope columns + safe defaults live in the SQL contract.
    for expected_column in [
        "risk_level text not null",
        "allowed_to_run_on_prem boolean not null default true",
        "autonomous_execution_allowed boolean not null default false",
        "review_required boolean not null default true",
        "required_review_role text",
        "check (required_review_role is null or required_review_role in ('admin', 'compliance', 'physician'))",
        "human_approval_required boolean not null default false",
        "external_action_performed boolean not null default false",
        "external_egress boolean not null default false",
        "recoverable boolean not null default true",
        "stopped_at text not null default 'draft_response'",
        "citation_ids text[] not null default '{}'",
    ]:
        assert expected_column in cloud_schema

    # 2. The Sales app stamps the full policy envelope on each bridge row, and
    #    never claims an external action was performed at enqueue time.
    for stamped_field in [
        "risk_level:",
        "allowed_to_run_on_prem:",
        "autonomous_execution_allowed:",
        "review_required:",
        "required_review_role:",
        "human_approval_required:",
        "external_egress:",
        "recoverable:",
        "citation_ids:",
    ]:
        assert stamped_field in draft_route
    assert "external_action_performed: false" in draft_route

    # The TS policy mirror stays out of the standalone Sales app.
    assert not Path("apps/chatops-crm/lib/schema.ts").exists()


def test_chatops_crm_app_uses_patched_next_and_keeps_send_behind_approval():
    """Patched Next.js pin + send stays behind the server approval gate.

    Stale-era replacement: this guard previously pinned the inert review board
    (``"disabled"`` and ``"Demo only"`` in page.tsx). The shipped page mounts the
    real Sales Workspace, so the inert-button pins are gone; the *real* safety
    property is that the production send path is approval-gated on the server.

    Regression caught: downgrading to the known-bad pinned Next version; mounting
    a page other than the standalone Workspace; OR the server real-send action
    losing its "must be dry-run approved / dry-run sent first" precondition.
    """
    package_json = json.loads(Path("apps/chatops-crm/package.json").read_text(encoding="utf-8"))
    page = Path("apps/chatops-crm/app/page.tsx").read_text(encoding="utf-8")
    draft_route = Path("apps/chatops-crm/app/api/sales/draft/route.ts").read_text(encoding="utf-8")

    # Keep the Next.js version pin away from the known-bad build.
    assert package_json["dependencies"]["next"] != "16.0.8"
    assert package_json["dependencies"]["next"] == "16.2.6"

    # The page mounts the production Workspace (no inert demo board).
    assert "import { Workspace }" in page
    assert "<Workspace />" in page

    # The real-send action keeps its two-stage server precondition.
    assert '"approve_for_real_send"' in draft_route
    assert 'prev !== "approved_for_dry_run" && draft.send_status !== "dry_run_sent"' in draft_route


def test_chatops_crm_cloud_surface_carries_only_sanitized_agent_notes_and_audits():
    """Raw PHI/PII/secrets never cross to the cloud note/audit surface.

    Stale-era replacement: the old guard read ``lib/schema.ts`` type names and
    pinned page strings ("Operational log", "raw PHI/PII/secrets stay on-prem")
    from the inert prototype. The same boundary now ships across the SQL
    contract, the Sales redaction helper, and the draft route's sanitized audit
    write — re-pinned here, plus the no-raw-secret negative guard against the
    current shipped sources.

    Regression caught: a cloud ``agent_notes``/``audit_logs`` row gaining a raw
    body field or being allowed to record an external action; the redaction
    helper losing email/phone/url scrubbing; the draft route writing an
    unsanitized audit summary; OR any literal raw PHI/PII/secret leaking into the
    Sales surface files.
    """
    cloud_schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")
    redaction = Path("apps/chatops-crm/lib/sales-redaction.ts").read_text(encoding="utf-8")
    draft_route = Path("apps/chatops-crm/app/api/sales/draft/route.ts").read_text(encoding="utf-8")

    # Cloud note/audit rows must be sanitized and must never claim an external
    # action was performed.
    assert "create table if not exists agent_notes" in cloud_schema
    assert "create table if not exists audit_logs" in cloud_schema
    assert "sanitized_summary text not null" in cloud_schema
    # The CHECK is pinned on at least two cloud tables (audit_logs and
    # improvement_candidates; agent_notes carries the column without a CHECK).
    assert cloud_schema.count("check (external_action_performed = false)") >= 2

    # The redaction helper actually scrubs the egress channels.
    for scrubber in ["redactEmail", "redactPhone", "redactUrl", "sanitizeForOutboundSummary"]:
        assert scrubber in redaction

    # The Sales draft route writes a sanitized audit summary (never the raw body)
    # and flags the persisted audit row as non-external.
    assert "sanitizeForOutboundSummary(" in draft_route
    assert "external_action_performed: false" in draft_route

    # Negative guard: no raw PHI/PII/secret literal anywhere on the Sales surface.
    combined = "\n".join([
        cloud_schema,
        redaction,
        draft_route,
        Path("apps/chatops-crm/lib/sales-db.ts").read_text(encoding="utf-8"),
        Path("apps/chatops-crm/app/page.tsx").read_text(encoding="utf-8"),
        Path("apps/chatops-crm/app/Workspace.tsx").read_text(encoding="utf-8"),
    ])
    for forbidden in ["MRN-", "4111-1111", "山田太郎", "raw_secret"]:
        assert forbidden not in combined


def test_chatops_crm_shared_bridge_serves_sierra_and_consent_review_apps():
    """The shared agent_tasks bridge still serves the sierra/consent review apps.

    Stale-era replacement: the old guard read demo seed rows
    (``review_demo_sierra``, ``SAFE-CONSENT-001``, ``demoCrm.agentTasks.map``)
    out of the inert prototype's ``lib/schema.ts`` + page.tsx. Those demo seeds
    no longer exist (the prototype board was removed). The durable invariant —
    that the *shared* bridge allowlists the sierra_service and consent_agent
    apps alongside chatops_crm, on the same review/agent-task tables — survives
    in the SQL contract and is re-pinned here. The demo-seed assertions are
    intentionally dropped (they pinned removed prototype fixtures, not a safety
    property).

    Regression caught: removing ``sierra_service`` or ``consent_agent`` from the
    shared bridge allowlist, or decoupling the review-request / agent-task
    linkage the cross-app review flow depends on.
    """
    cloud_schema = Path("docs/sinria-hybrid-bridge-cloud-schema.sql").read_text(encoding="utf-8")

    # The shared bridge serves all three apps on one allowlist.
    assert "check (app_id in ('chatops_crm', 'sierra_service', 'consent_agent'))" in cloud_schema

    # Review flow linkage the sierra/consent review apps rely on is intact.
    assert "create table if not exists review_requests" in cloud_schema
    assert "review_request_id text references review_requests(id)" in cloud_schema
    assert "agent_task_id text references agent_tasks(id)" in cloud_schema

    # The standalone Sales app does not host the shared agent-task/review API
    # routes (those belong to the cloud/company-os surface, not this Sales app).
    assert not Path("apps/chatops-crm/app/api/agent-tasks/route.ts").exists()
    assert not Path("apps/chatops-crm/app/api/review-requests/route.ts").exists()
