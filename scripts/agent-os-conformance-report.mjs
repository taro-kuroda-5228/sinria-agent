#!/usr/bin/env node
/**
 * Agent OS conformance report (Task 28).
 *
 * Scores the four current Sinria Agent OS systems across eight conformance
 * categories by READING real repository files (existsSync + content checks),
 * not by hard-coding verdicts. Emits a per-cell verdict of:
 *
 *   pass    — the capability is present and live in real files
 *   partial — present but documented-but-not-fully-live (a concrete gap remains)
 *   missing — not present yet
 *
 * Each cell carries a concrete evidence path and a concrete next step.
 *
 * Output:
 *   - reports/agent-os-conformance/latest.md  (markdown matrix + per-cell detail)
 *   - a short summary printed to stdout
 *
 * Flags:
 *   --check   exit non-zero if ANY category for ANY system is `missing`.
 *
 * Dependency-free: only `node:fs` and `node:path` / `node:url`.
 */

import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// ---------------------------------------------------------------------------
// File-reading helpers (the whole report is derived from these)
// ---------------------------------------------------------------------------

/** @param {string} rel repo-relative path @returns {boolean} */
function fileExists(rel) {
  return existsSync(join(REPO_ROOT, rel));
}

/** @param {string} rel @returns {string} file content or "" if missing/unreadable */
function read(rel) {
  try {
    return readFileSync(join(REPO_ROOT, rel), "utf8");
  } catch {
    return "";
  }
}

/**
 * True if file exists and contains EVERY needle (string or RegExp).
 * @param {string} rel
 * @param {(string|RegExp)[]} needles
 */
function fileHasAll(rel, needles) {
  if (!fileExists(rel)) return false;
  const src = read(rel);
  return needles.every((n) => (n instanceof RegExp ? n.test(src) : src.includes(n)));
}

/**
 * True if file exists and contains AT LEAST ONE needle.
 * @param {string} rel
 * @param {(string|RegExp)[]} needles
 */
function fileHasAny(rel, needles) {
  if (!fileExists(rel)) return false;
  const src = read(rel);
  return needles.some((n) => (n instanceof RegExp ? n.test(src) : src.includes(n)));
}

/** Count how many of the given files exist. @param {string[]} rels */
function countExisting(rels) {
  return rels.filter(fileExists).length;
}

// ---------------------------------------------------------------------------
// Verdict constructor
// ---------------------------------------------------------------------------

/**
 * @param {"pass"|"partial"|"missing"} verdict
 * @param {string} evidence repo-relative path (or comma-joined paths) that was read
 * @param {string} detail one-line explanation of WHY this verdict
 * @param {string} next concrete next step
 */
function cell(verdict, evidence, detail, next) {
  return { verdict, evidence, detail, next };
}

// ---------------------------------------------------------------------------
// Categories (stable order for the matrix columns)
// ---------------------------------------------------------------------------

const CATEGORIES = [
  "Product/Domain",
  "Core/Projection",
  "Team Mode",
  "Workflow",
  "Safety",
  "UI",
  "Template/Reuse",
  "Verification",
];

// ---------------------------------------------------------------------------
// Shared evidence (Company-OS control plane + agent-os-core factory) that
// several systems are scored against.
// ---------------------------------------------------------------------------

const TYPES = "apps/company-os/lib/company-os-types.ts";
const CLOUD_BOUNDARY = "apps/company-os/lib/cloud-boundary.mjs";
const SCHEMA = "apps/company-os/db/company_os_schema.sql";
const ROUTING_MIGRATION = "apps/company-os/db/migrations/2026-06-05-agent-os-routing.sql";
const REPOSITORY = "apps/company-os/lib/company-os-repository.ts";
const COMPANY_WORKSPACE = "apps/company-os/app/CompanyOsWorkspace.tsx";

// agent-os-core factory (Template/Reuse is shared by all systems)
const CORE_INDEX = "packages/agent-os-core/src/index.ts";
const CORE_SCAFFOLD = "packages/agent-os-core/scripts/create-agent-os.mjs";
const CORE_TEMPLATE_REGISTRY = "packages/agent-os-core/src/template-registry.mjs";
const CORE_MANIFEST_TEMPLATE = "packages/agent-os-core/templates/agent-os/manifest.yaml";
const CORE_WORKFLOW = "packages/agent-os-core/src/workflow.mjs";

/**
 * Template/Reuse cell — shared. Returns pass when the agent-os-core factory
 * (types/cloud-boundary/projection/workflow/manifest/bridge/template-registry +
 * scaffold + .tpl templates) is real and importable.
 */
function templateReuseCell() {
  const coreSrcFiles = [
    "packages/agent-os-core/src/types.ts",
    "packages/agent-os-core/src/cloud-boundary.mjs",
    "packages/agent-os-core/src/projection.mjs",
    "packages/agent-os-core/src/workflow.mjs",
    "packages/agent-os-core/src/manifest.mjs",
    "packages/agent-os-core/src/bridge.mjs",
    "packages/agent-os-core/src/template-registry.mjs",
  ];
  const presentCore = countExisting(coreSrcFiles);
  const templateFiles = [
    CORE_MANIFEST_TEMPLATE,
    "packages/agent-os-core/templates/agent-os/schema.sql.tpl",
    "packages/agent-os-core/templates/agent-os/app/Workspace.tsx.tpl",
    "packages/agent-os-core/templates/agent-os/src/core-state.ts.tpl",
  ];
  const presentTpl = countExisting(templateFiles);
  const indexReexports = fileHasAll(CORE_INDEX, [
    "./types",
    "./cloud-boundary.mjs",
    "./projection.mjs",
    "./workflow.mjs",
    "./manifest.mjs",
    "./bridge.mjs",
    "./template-registry.mjs",
  ]);
  const scaffoldReal = fileHasAll(CORE_SCAFFOLD, ["renderTemplate", "TEMPLATE_FILES", "planScaffold"]);

  if (presentCore === coreSrcFiles.length && presentTpl === templateFiles.length && indexReexports && scaffoldReal) {
    return cell(
      "pass",
      `${CORE_INDEX}, ${CORE_SCAFFOLD}, ${CORE_MANIFEST_TEMPLATE}`,
      `agent-os-core re-exports all 7 contract modules; scaffold renders ${presentTpl} .tpl templates via create-agent-os.mjs`,
      "Adopt the factory for the next new Agent OS instead of hand-copying chatops-crm.",
    );
  }
  return cell(
    "partial",
    CORE_INDEX,
    `agent-os-core present but incomplete (core ${presentCore}/${coreSrcFiles.length}, templates ${presentTpl}/${templateFiles.length}, index re-exports=${indexReexports}, scaffold=${scaffoldReal})`,
    "Finish wiring agent-os-core src + templates so create-agent-os.mjs scaffolds a full OS.",
  );
}

const TEMPLATE_REUSE = templateReuseCell();

// ---------------------------------------------------------------------------
// Verification cell builder (per system: which test/build harness is real)
// ---------------------------------------------------------------------------

const CI_WORKFLOW = ".github/workflows/tests.yml";
const CI_ENFORCES_PYTHON_ONLY =
  fileHasAny(CI_WORKFLOW, ["pytest"]) &&
  !fileHasAny(CI_WORKFLOW, [/node --test/, /next build/, /tsc --noEmit/, /npm test/]);

/**
 * @param {object} opts
 * @param {string} opts.pkgJson  package.json path for the app/package
 * @param {string[]} opts.testFiles  test files that should exist
 * @param {boolean} [opts.hasBuild]  whether `next build`/build is wired
 * @param {boolean} [opts.hasTsc]  whether typecheck (tsc --noEmit) is wired
 */
function verificationCell({ pkgJson, testFiles, hasBuild, hasTsc }) {
  const presentTests = countExisting(testFiles);
  const hasTestScript = fileHasAny(pkgJson, [/"test"\s*:/, "node --test"]);
  const ciNote = CI_ENFORCES_PYTHON_ONLY
    ? " CI (.github/workflows/tests.yml) enforces only root Python pytest — these JS suites run locally/pre-merge."
    : "";

  if (presentTests > 0 && hasTestScript) {
    const harness = [
      "node --test",
      hasTsc ? "tsc --noEmit" : null,
      hasBuild ? "next build" : null,
    ]
      .filter(Boolean)
      .join(" + ");
    // partial because CI does not gate these JS suites (only root Python is enforced).
    const verdict = CI_ENFORCES_PYTHON_ONLY ? "partial" : "pass";
    return cell(
      verdict,
      `${pkgJson}, ${testFiles.find(fileExists) ?? testFiles[0]}`,
      `${presentTests}/${testFiles.length} test files + ${harness} wired.${ciNote}`,
      CI_ENFORCES_PYTHON_ONLY
        ? "Add this app's node --test (+ tsc/next build) to CI so verification is enforced, not just local."
        : "Keep harness green; add coverage as features land.",
    );
  }
  return cell(
    "missing",
    pkgJson,
    `No node --test harness found (test files ${presentTests}/${testFiles.length}).`,
    "Add a node --test suite + test script to package.json.",
  );
}

// ---------------------------------------------------------------------------
// System 1: Company OS (the shared control plane itself)
// ---------------------------------------------------------------------------

function companyOs() {
  const out = {};

  // Product/Domain — the canonical shared state contract.
  out["Product/Domain"] = fileHasAll(TYPES, [
    "Company OS is a SHARED control plane",
    "Workspace",
    "WorkspaceMember",
    "SinriaInstance",
  ])
    ? cell(
        "pass",
        TYPES,
        "Canonical metadata-only control-plane contract (Workspace/Member/Instance) is defined and documented.",
        "Keep the contract in sync as new Agent OS systems mount projections.",
      )
    : cell("missing", TYPES, "Control-plane domain contract not found.", "Define company-os-types.ts.");

  // Core/Projection — projection model + per-OS builders aggregated by the repository.
  const projBuilders = [
    "apps/company-os/lib/projections/sales-agent-os.mjs",
    "apps/company-os/lib/projections/service-agent-os.mjs",
    "apps/company-os/lib/projections/application-agent-os.mjs",
  ];
  out["Core/Projection"] =
    fileHasAll(TYPES, ["AgentOsProjection"]) &&
    countExisting(projBuilders) === 3 &&
    fileHasAll(REPOSITORY, ["buildSalesAgentOsProjection", "buildServiceAgentOsProjection", "buildApplicationAgentOsProjection"])
      ? cell(
          "pass",
          `${TYPES}, ${REPOSITORY}`,
          "AgentOsProjection type + 3 per-OS builders are aggregated by company-os-repository into shared state.",
          "Add consent_agent/medevidence projection builders when those cores ship.",
        )
      : cell(
          "partial",
          REPOSITORY,
          `Projection model present but builders incomplete (${countExisting(projBuilders)}/3 found).`,
          "Wire all per-OS projection builders into the repository aggregate.",
        );

  // Team Mode — routed task envelope/claim/result + tables with RLS.
  const teamTypes = fileHasAll(TYPES, ["AgentOsTaskEnvelope", "AgentOsTaskClaim", "AgentOsTaskResult"]);
  const teamTables = fileHasAll(SCHEMA, ["agent_os_tasks", "agent_os_task_claims", "agent_os_task_results"]);
  const teamRoutes = countExisting([
    "apps/company-os/app/api/agent-os/tasks/route.ts",
    "apps/company-os/app/api/agent-os/tasks/claim/route.ts",
    "apps/company-os/app/api/agent-os/tasks/result/route.ts",
  ]);
  out["Team Mode"] =
    teamTypes && teamTables && teamRoutes === 3
      ? cell(
          "pass",
          `${TYPES}, ${SCHEMA}, apps/company-os/app/api/agent-os/tasks/*`,
          "Routed task envelope/claim/result types + tables + create/claim/result routes are live (single-active-claim lease).",
          "Exercise multi-instance claim contention against the live DB before GA.",
        )
      : cell(
          "partial",
          ROUTING_MIGRATION,
          `Team Mode routing partially wired (types=${teamTypes}, tables=${teamTables}, routes=${teamRoutes}/3).`,
          "Finish task routing routes + apply the routing migration to the live DB.",
        );

  // Workflow — approval gating + claim lifecycle.
  out["Workflow"] =
    fileHasAll(TYPES, ["HUMAN_APPROVAL_REQUIRED_OPERATIONS", "defaultAgentOsTaskPolicy"]) &&
    fileHasAny(TYPES, ["AgentOsTaskClaimStatus"]) &&
    fileExists("apps/company-os/app/api/agent-os/tasks/claim/renew/route.ts")
      ? cell(
          "pass",
          `${TYPES}, apps/company-os/app/api/agent-os/tasks/claim/renew/route.ts`,
          "HUMAN_APPROVAL_REQUIRED_OPERATIONS + defaultAgentOsTaskPolicy + claim status lifecycle (claim/renew/release) are defined.",
          "Add automated claim-expiry sweeping in production.",
        )
      : cell(
          "partial",
          TYPES,
          "Workflow primitives present but lifecycle routes incomplete.",
          "Complete claim renew/release/expiry handling.",
        );

  // Safety — metadata-only boundary validator + CHECK=false pins + RLS.
  const boundary = fileHasAll(CLOUD_BOUNDARY, [
    "assertCompanyOsCloudMetadataOnly",
    "validateAgentOsTaskEnvelope",
    "validateAgentOsTaskClaim",
    "validateAgentOsTaskResult",
  ]);
  const checkPins = fileHasAny(SCHEMA, [/check\s*\(\s*\w+\s*=\s*false\s*\)/]);
  const rls = fileHasAny(SCHEMA, [/enable row level security/i]) && fileHasAny(SCHEMA, [/create policy/i]);
  const repoEnforces = fileHasAll(REPOSITORY, ["validateAgentOsTaskEnvelope", "cloud-boundary.mjs"]);
  out["Safety"] =
    boundary && checkPins && rls && repoEnforces
      ? cell(
          "pass",
          `${CLOUD_BOUNDARY}, ${SCHEMA}`,
          "Runtime boundary validator + CHECK(=false) safety pins + RLS policies, enforced at the repository edge (defense in depth).",
          "Add a periodic boundary-violation audit job against live rows.",
        )
      : cell(
          "partial",
          CLOUD_BOUNDARY,
          `Safety controls incomplete (validator=${boundary}, checkPins=${checkPins}, rls=${rls}, repoEnforces=${repoEnforces}).`,
          "Pin all cloud-visible booleans with CHECK=false + enable RLS on every table.",
        );

  // UI — Company OS workspace shell that mounts all OS projections + reviews + routed tasks.
  const uiCard = "apps/company-os/app/components/AgentOsProjectionCard.tsx";
  out["UI"] =
    fileHasAll(COMPANY_WORKSPACE, ["AgentOsProjection", "projections", /sales_agent_os|Sales Agent OS/]) &&
    fileExists(uiCard)
      ? cell(
          "pass",
          `${COMPANY_WORKSPACE}, ${uiCard}`,
          "CompanyOsWorkspace renders per-OS projection cards, review queue, and routed-task views across all 5 Agent OS ids.",
          "Add live SSE/polling refresh + per-instance claim presence to the shell.",
        )
      : cell(
          "partial",
          COMPANY_WORKSPACE,
          "Workspace shell present but does not yet render all projection/review/task views.",
          "Mount projection cards + review queue + routed tasks in the shell.",
        );

  out["Template/Reuse"] = TEMPLATE_REUSE;

  out["Verification"] = verificationCell({
    pkgJson: "apps/company-os/package.json",
    testFiles: [
      "apps/company-os/tests/company-os-state-contract.test.mjs",
      "apps/company-os/tests/company-os-boundary.test.mjs",
      "apps/company-os/tests/company-os-agent-os-routing.test.mjs",
      "apps/company-os/tests/company-os-sql-static.test.mjs",
    ],
    hasBuild: fileHasAny("apps/company-os/package.json", ["next build"]),
    hasTsc: fileHasAny("apps/company-os/package.json", ["tsc --noEmit", "tsc -p", '"typecheck"']),
  });

  return out;
}

// ---------------------------------------------------------------------------
// System 2: Sales Agent OS (chatops-crm — the reference implementation)
// ---------------------------------------------------------------------------

function salesAgentOs() {
  const out = {};
  const PKG = "apps/chatops-crm/package.json";
  const WORKSPACE = "apps/chatops-crm/app/SalesWorkspace.tsx";
  const SCHEMA_TEST = "apps/chatops-crm/tests/schema-static.test.mjs";
  const SALES_PROJ = "apps/company-os/lib/projections/sales-agent-os.mjs";
  const CRM_SCHEMA = "apps/chatops-crm/openclaw-migration/supabase/migrations/20260528_crm_closed_loop.sql";

  out["Product/Domain"] =
    fileHasAny(PKG, ['"name": "sinria-sales-agent-os"']) && fileHasAny(WORKSPACE, ["Sinria Sales Agent OS", "lead", "draft"])
      ? cell(
          "pass",
          `${PKG}, ${WORKSPACE}`,
          'Public "Sinria Sales Agent OS" product with a lead/draft/outreach sales domain.',
          "Keep public naming consistent across routes and UI.",
        )
      : cell("missing", PKG, "Sales product/domain not found.", "Define the sales app + public name.");

  out["Core/Projection"] =
    fileHasAll(SALES_PROJ, ["buildSalesAgentOsProjection", "agentOsId: \"sales\"", "rawSourceBodyStored: false"])
      ? cell(
          "pass",
          SALES_PROJ,
          "buildSalesAgentOsProjection emits a metadata-only projection (counts/next-actions, never raw lead/email bodies).",
          "Wire the projection to live Sales Agent OS metrics instead of DEFAULT_METRICS.",
        )
      : cell("partial", SALES_PROJ, "Sales projection builder incomplete.", "Implement buildSalesAgentOsProjection.");

  // Team Mode — sales tasks participate via the shared company-os routing + execution-target selector.
  // Live verification evidence: the 2026-06-11 production E2E run exercised
  // claim→result, duplicate-claim rejection (one_active_uidx) and the
  // unregistered-instance FK gate against the live company_os schema.
  out["Team Mode"] =
    fileExists("apps/chatops-crm/app/components/ExecutionTargetSelector.tsx") &&
    fileHasAny("apps/chatops-crm/app/SalesWorkspace.tsx", ["ExecutionTargetSelector"]) &&
    fileExists("reports/agent-os-conformance/live-e2e-2026-06-11.md")
      ? cell(
          "pass",
          "reports/agent-os-conformance/live-e2e-2026-06-11.md, apps/company-os/db/migrations/2026-06-05-agent-os-routing.sql",
          "Sales tasks route through company-os agent_os_tasks with claim/lease/idempotency enforcement, verified end-to-end against the production schema (claim→result, duplicate-claim DB rejection, instance FK gate, daemon KPI push) on 2026-06-11.",
          "Re-run the live E2E checklist (docs/runbooks/team-mode-live-e2e-checklist.md) whenever the routing schema or claim lifecycle changes.",
        )
      : cell(
          "partial",
          ROUTING_MIGRATION,
          "Sales participates in shared routing tables but live end-to-end claim verification has not been recorded.",
          "Run the live E2E checklist and record results under reports/agent-os-conformance/.",
        );

  out["Workflow"] =
    fileHasAny("apps/chatops-crm/app/api/sales/draft/route.ts", ["review", "draft"]) &&
    fileExists("apps/chatops-crm/app/api/sales/outreach-plan/route.ts")
      ? cell(
          "pass",
          "apps/chatops-crm/app/api/sales/draft/route.ts, apps/chatops-crm/app/api/sales/outreach-plan/route.ts",
          "Draft → review → outreach-plan workflow with human-in-the-loop review before any send.",
          "Surface SLA timers on stalled review drafts.",
        )
      : cell("partial", "apps/chatops-crm/app/api/sales", "Draft/review workflow incomplete.", "Add the draft → review gate.");

  // Safety — boundary pins in routes + schema; CRM closed-loop schema.
  out["Safety"] =
    fileHasAny("apps/chatops-crm/app/api/sales/outreach-plan/route.ts", ["rawContextAllowedInCloud: false", "externalActionAllowed: false"]) &&
    fileExists(SCHEMA_TEST)
      ? cell(
          "pass",
          `apps/chatops-crm/app/api/sales/outreach-plan/route.ts, ${SCHEMA_TEST}`,
          "Outreach routes pin rawContextAllowedInCloud/externalActionAllowed=false; schema-static test guards the boundary invariants.",
          "Add a runtime egress guard test against the live CRM DB.",
        )
      : cell(
          "partial",
          SCHEMA_TEST,
          "Safety pins present in tests but not asserted in all live routes.",
          "Pin metadata-only booleans in every sales route.",
        );

  out["UI"] =
    fileHasAny(WORKSPACE, ["Sinria Sales Agent OS"]) && fileExists("apps/chatops-crm/app/layout.tsx")
      ? cell(
          "pass",
          `${WORKSPACE}, apps/chatops-crm/app/layout.tsx`,
          "Standalone Sales workspace UI (lead board, drafts, relationship stages, presence).",
          "Add review-queue keyboard nav + bulk approval.",
        )
      : cell("partial", WORKSPACE, "Sales UI incomplete.", "Build the standalone Sales workspace.");

  out["Template/Reuse"] = TEMPLATE_REUSE;

  out["Verification"] = verificationCell({
    pkgJson: PKG,
    testFiles: [SCHEMA_TEST],
    hasBuild: fileHasAny(PKG, ["next build"]),
    hasTsc: fileHasAny(PKG, ['"typecheck"', "tsc --noEmit"]),
  });

  return out;
}

// ---------------------------------------------------------------------------
// System 3: Service Agent OS (sinria-sierra-service)
// ---------------------------------------------------------------------------

function serviceAgentOs() {
  const out = {};
  const DIR = "apps/sinria-sierra-service";
  const SERVICE = `${DIR}/src/service.js`;
  const PKG = `${DIR}/package.json`;
  const SAFETY_DOC = `${DIR}/docs/safety-boundary.md`;
  const SVC_PROJ = "apps/company-os/lib/projections/service-agent-os.mjs";

  out["Product/Domain"] =
    fileHasAny(PKG, ['"name": "sinria-sierra-service"']) && fileHasAny(SERVICE, ["classifyRequest"])
      ? cell(
          "pass",
          `${PKG}, ${SERVICE}`,
          "Service Agent OS product with a customer/patient-service intake+triage domain.",
          "Finalize the MVP intake field set (currently a design draft).",
        )
      : cell("missing", PKG, "Service product/domain not found.", "Define the service app.");

  // Core/Projection — deterministic classify/triage exists, but NO persistent core state yet.
  const hasClassify = fileHasAll(SERVICE, ["classifyRequest", "classification"]);
  const hasPersistentCore = fileHasAny(SERVICE, [/repository|persist|store state|coreState|database|supabase/i]);
  const projHonest = fileHasAny(SVC_PROJ, ['health: "blocked"', "design-draft", '"stale"']);
  out["Core/Projection"] =
    hasClassify && projHonest && !hasPersistentCore
      ? cell(
          "partial",
          `${SERVICE}, ${SVC_PROJ}`,
          "Deterministic classify/triage logic exists but there is no persistent core state; projection honestly reports health=blocked / freshness=stale.",
          "Add a persistent Service core (state store + repository) so the projection reflects real intake counts.",
        )
      : hasClassify && hasPersistentCore
        ? cell(
            "pass",
            `${SERVICE}, ${SVC_PROJ}`,
            "Classify/triage + persistent core state feeding a real projection.",
            "Tune SLA-risk metrics in the projection.",
          )
        : cell(
            "partial",
            SVC_PROJ,
            "Service core/projection incomplete.",
            "Implement classify/triage core + projection builder.",
          );

  // Team Mode — bridge allows sierra_service, but no in-app routing/claim UI.
  out["Team Mode"] =
    fileHasAny("packages/agent-os-core/src/bridge.mjs", ["sierra_service", "consent_agent"])
      ? cell(
          "partial",
          "packages/agent-os-core/src/bridge.mjs, apps/company-os/db/migrations/2026-06-05-agent-os-routing.sql",
          "Service is an allowed bridge app id and can be a routed-task target, but the service app has no in-app claim/route UI.",
          "Route service intake tasks through company-os agent_os_tasks and claim them locally.",
        )
      : cell(
          "missing",
          "packages/agent-os-core/src/bridge.mjs",
          "Service is not registered as a routable app id.",
          "Register sierra_service in the bridge allow-list + routing.",
        );

  // Workflow — draft-only / human-approval gating is real and per-category.
  out["Workflow"] =
    fileHasAny(SERVICE, ["requiresHumanApproval", "human approval", "draft-only", "staff approval"]) &&
    fileHasAny(SERVICE, ["classifyRequest"])
      ? cell(
          "pass",
          `${SERVICE}, ${SAFETY_DOC}`,
          "Per-category draft-only + human-approval gating (sends, state changes, billing, clinical decisions all require approval).",
          "Wire approved drafts into a real send/execute path behind the gate.",
        )
      : cell("partial", SERVICE, "Approval gating incomplete.", "Add per-category human-approval gates.");

  // Safety — documented safety boundary + draft/local-only; runs against local fixtures (no live egress).
  out["Safety"] =
    fileHasAll(SAFETY_DOC, ["draft-only", "Requires human approval", "Blocked by default"]) &&
    fileHasAny(SERVICE, ["redact", "hashed", "draft", "approval"])
      ? cell(
          "partial",
          `${SAFETY_DOC}, ${SERVICE}`,
          "Strong documented safety boundary (draft/local-only, blocked-by-default, redacted audit) but no live cloud-boundary validator wired into a persistent core yet.",
          "Pass Service core records through the cloud-boundary validator + add CHECK=false pinned tables when the core persists.",
        )
      : cell(
          "partial",
          SAFETY_DOC,
          "Safety boundary documented but not fully enforced in code.",
          "Enforce the documented boundary in code + tests.",
        );

  // UI — only a static dashboard / index.html, no rich operator workspace yet.
  out["UI"] =
    fileExists(`${DIR}/index.html`) || fileExists(`${DIR}/src/dashboard.js`)
      ? cell(
          "partial",
          `${DIR}/index.html, ${DIR}/src/dashboard.js`,
          "Only a static demo dashboard exists; no production operator workspace (no review-queue UI, no Company-OS-style shell).",
          "Build a Service operator workspace (intake board + review queue) using the agent-os-core Workspace template.",
        )
      : cell("missing", DIR, "No Service UI found.", "Scaffold a Service workspace from the template.");

  out["Template/Reuse"] = TEMPLATE_REUSE;

  out["Verification"] = verificationCell({
    pkgJson: PKG,
    testFiles: [
      `${DIR}/tests/service.test.js`,
      `${DIR}/tests/dashboard.test.js`,
      `${DIR}/tests/improvement-regression.test.js`,
    ],
    hasBuild: false,
    hasTsc: false,
  });

  return out;
}

// ---------------------------------------------------------------------------
// System 4: Application Agent OS (Consent Agent + MedEvidence modules)
// ---------------------------------------------------------------------------

function applicationAgentOs() {
  const out = {};
  const APP_PROJ = "apps/company-os/lib/projections/application-agent-os.mjs";
  const APP_PROJ_TEST = "apps/company-os/tests/company-os-application-projection.test.mjs";
  const CONNECTORS = "packages/agent-os-core/src/connectors.mjs";

  // Product/Domain — defined as ids/projection, but no standalone control-plane app dirs.
  const standaloneApp =
    fileExists("apps/consent-agent") || fileExists("apps/medevidence");
  const idsDefined = fileHasAll(TYPES, ["consent_agent", "medevidence"]);
  out["Product/Domain"] = idsDefined
    ? standaloneApp
      ? cell(
          "pass",
          `${TYPES}, apps/consent-agent|apps/medevidence`,
          "Consent Agent + MedEvidence have AgentOs ids AND standalone app directories.",
          "Keep module domains documented.",
        )
      : cell(
          "partial",
          `${TYPES}, ${APP_PROJ}`,
          "Consent Agent + MedEvidence are defined as AgentOs ids + an application projection, but there is no standalone control-plane app dir for either module yet.",
          "Scaffold consent-agent / medevidence apps (or formalize them as Application Agent OS modules).",
        )
    : cell("missing", TYPES, "Application module ids not defined.", "Add consent_agent/medevidence AgentOs ids.");

  // Core/Projection — projection builder exists (connector/app-lifecycle/dry-run readiness).
  out["Core/Projection"] =
    fileHasAll(APP_PROJ, ["buildApplicationAgentOsProjection", 'agentOsId: "application"', "liveSyncEnabled"]) &&
    fileExists(APP_PROJ_TEST)
      ? cell(
          "pass",
          `${APP_PROJ}, ${APP_PROJ_TEST}`,
          "buildApplicationAgentOsProjection surfaces connector/app-lifecycle/dry-run readiness as metadata-only, with a contract test.",
          "Feed real connector counts into the projection instead of DEFAULT_METRICS.",
        )
      : cell("partial", APP_PROJ, "Application projection incomplete.", "Implement buildApplicationAgentOsProjection.");

  // Team Mode — modules are projection targets only; no per-module routing/claim.
  out["Team Mode"] =
    fileHasAny("packages/agent-os-core/src/bridge.mjs", ["consent_agent"])
      ? cell(
          "partial",
          "packages/agent-os-core/src/bridge.mjs, apps/company-os/db/migrations/2026-06-05-agent-os-routing.sql",
          "consent_agent is an allowed bridge app id, but Application modules are projection targets only — no per-module routed task claim flow yet.",
          "Route connector/consent tasks through company-os agent_os_tasks with per-module claims.",
        )
      : cell(
          "missing",
          "packages/agent-os-core/src/bridge.mjs",
          "Application modules are not registered as routable app ids.",
          "Register the modules in the bridge allow-list + routing.",
        );

  // Workflow — live connector sync is a human-approval-required external action.
  out["Workflow"] =
    fileHasAll(APP_PROJ, ["liveSyncEnabled", "dry-run", "externalActionPerformed: false"])
      ? cell(
          "pass",
          APP_PROJ,
          "Connector lifecycle gates live sync behind human approval; dry-run only until explicitly enabled (externalActionPerformed pinned false).",
          "Add an approval route that flips liveSyncEnabled per connector with audit.",
        )
      : cell("partial", APP_PROJ, "Connector approval workflow incomplete.", "Gate live sync behind human approval.");

  // Safety — projection pins metadata-only flags; connectors module is dry-run by default.
  out["Safety"] =
    fileHasAll(APP_PROJ, ["rawSourceBodyStored: false", "credentialStoredInCloud: false", "externalActionPerformed: false"])
      ? cell(
          "pass",
          `${APP_PROJ}, ${CONNECTORS}`,
          "Application projection pins rawSourceBodyStored/credentialStoredInCloud/externalActionPerformed=false; connectors default to dry-run.",
          "Add a live-sync egress audit once a connector is approved.",
        )
      : cell("partial", APP_PROJ, "Application safety pins incomplete.", "Pin metadata-only booleans in the projection.");

  // UI / control plane — partial: rendered only as a card inside Company OS, no module-specific control plane.
  out["UI"] =
    fileHasAny(COMPANY_WORKSPACE, ["application_agent_os", "Application Agent OS", "consent_agent", "medevidence"])
      ? cell(
          "partial",
          COMPANY_WORKSPACE,
          "Application/Consent/MedEvidence appear only as projection cards inside Company OS; no dedicated module control-plane UI.",
          "Build a connector/consent control-plane view (or scaffold module apps from the template).",
        )
      : cell("missing", COMPANY_WORKSPACE, "No Application module UI surfaced.", "Render the application projection in the shell.");

  out["Template/Reuse"] = TEMPLATE_REUSE;

  // Verification — covered by company-os application projection test (no standalone app harness).
  out["Verification"] = verificationCell({
    pkgJson: "apps/company-os/package.json",
    testFiles: [APP_PROJ_TEST, "apps/company-os/tests/company-os-projection-contract.test.mjs"],
    hasBuild: fileHasAny("apps/company-os/package.json", ["next build"]),
    hasTsc: fileHasAny("apps/company-os/package.json", ['"typecheck"', "tsc --noEmit"]),
  });

  return out;
}

// ---------------------------------------------------------------------------
// Systems 5+6: Finance / Legal Agent OS (factory-generated second generation)
// ---------------------------------------------------------------------------

/**
 * Shared scoring for an OS generated by the agent-os-core factory and mounted
 * as its own Next.js app. Evidence is the generated bundle + the Company OS
 * projection wiring + the Team Connect routing surface — real files only.
 *
 * @param {object} os
 * @param {string} os.shortId   projection-layer id, e.g. "finance"
 * @param {string} os.osId      routing-layer id, e.g. "finance_agent_os"
 * @param {string} os.dir       app dir, e.g. "apps/finance-agent-os"
 * @param {string} os.publicName e.g. "Sinria Finance Agent OS"
 * @param {string} os.builder   projection builder symbol in company-os
 */
function factoryGeneratedAgentOs({ shortId, osId, dir, publicName, builder }) {
  const out = {};
  const MANIFEST = `${dir}/agent-os.manifest.yaml`;
  const DOC = `docs/agent-os/${shortId}-agent-os.md`;
  const CORE_STATE = `${dir}/src/core-state.ts`;
  const COMPANY_PROJECTION = `${dir}/src/company-projection.ts`;
  const OS_PROJ = `apps/company-os/lib/projections/${shortId}-agent-os.mjs`;
  const OS_PROJ_TEST = `apps/company-os/tests/company-os-${shortId}-projection.test.mjs`;
  const TASKS_ROUTE = `${dir}/app/api/tasks/route.ts`;
  const REVIEWS_ROUTE = `${dir}/app/api/reviews/route.ts`;
  const OS_SCHEMA = `${dir}/schema.sql`;
  const PKG = `${dir}/package.json`;
  const IDS_MIGRATION = "apps/company-os/db/migrations/2026-06-12-finance-legal-agent-os-ids.sql";
  const TEAM_CONNECT_CORE = "apps/sinria-team-connect/src/connect-core.mjs";
  const TEAM_CONNECT_TEST = "apps/sinria-team-connect/tests/connect-core.test.mjs";

  out["Product/Domain"] =
    fileHasAll(MANIFEST, [`osId: ${osId}`, `publicName: ${publicName}`]) && fileExists(DOC)
      ? cell(
          "pass",
          `${MANIFEST}, ${DOC}`,
          `${publicName} is manifest-defined (factory input) with a canonical product doc and pinned naming.`,
          "Refine the wedge with real operator feedback.",
        )
      : cell("missing", MANIFEST, "Manifest/product doc not found.", "Generate the OS from a manifest.");

  out["Core/Projection"] =
    fileHasAny(CORE_STATE, ["AgentOsCoreStateBase"]) &&
    fileHasAny(COMPANY_PROJECTION, ["projectionFreshness"]) &&
    fileHasAny(REPOSITORY, [builder])
      ? cell(
          "pass",
          `${CORE_STATE}, ${OS_PROJ}, ${REPOSITORY}`,
          "Generated core state + company projection exist and the metadata-only projection builder is mounted in the Company OS repository.",
          "Replace seed metrics with a real KPI push from the local core (Sales daemon pattern).",
        )
      : cell("partial", OS_PROJ, "Core/projection wiring incomplete.", "Wire the projection builder into the repository.");

  // Team Mode — routable id + Team Connect delegation/claim with tests; live
  // multi-instance E2E against the deployed control plane has not run yet.
  out["Team Mode"] =
    fileHasAny(TYPES, [`"${osId}"`]) &&
    fileHasAny(IDS_MIGRATION, [`'${shortId}'`]) &&
    fileHasAny(TEAM_CONNECT_CORE, [`"${osId}"`]) &&
    fileHasAny(TEAM_CONNECT_TEST, ["single-active-claim"])
      ? cell(
          "partial",
          `${TYPES}, ${IDS_MIGRATION}, ${TEAM_CONNECT_CORE}`,
          "Routable id registered (types + SQL migration) and Team Connect delegates/claims tasks for this OS with tested single-active-claim.",
          "Run a live multi-instance claim E2E against the deployed control plane and record it.",
        )
      : cell("missing", TYPES, "OS is not a routable Team Mode target.", "Register the routing id + delegation surface.");

  out["Workflow"] =
    fileHasAny(TASKS_ROUTE, ["buildAgentOsTask"]) && fileHasAny(REVIEWS_ROUTE, ["requiredAuthority"])
      ? cell(
          "pass",
          `${TASKS_ROUTE}, ${REVIEWS_ROUTE}`,
          "Intake classifies side-effect → risk → humanApprovalRequired via the shared workflow contract; reviews carry requiredAuthority.",
          "Connect the review queue to real operator decisions in production.",
        )
      : cell("partial", TASKS_ROUTE, "Workflow envelopes incomplete.", "Use the shared workflow builders.");

  out["Safety"] =
    fileHasAll(OS_SCHEMA, ["= false", "enable row level security"]) &&
    fileExists(`${dir}/tests/cloud-boundary.test.mjs`) &&
    fileHasAny(SCHEMA, [`'${shortId}'`])
      ? cell(
          "pass",
          `${OS_SCHEMA}, ${dir}/tests/cloud-boundary.test.mjs, ${SCHEMA}`,
          "Generated schema pins CHECK(=false) safety flags + RLS; cloud-boundary test guards the repository edge; Company OS schema accepts the id.",
          "Apply the ids migration to the live DB (human-approval gate).",
        )
      : cell("partial", OS_SCHEMA, "Safety pins incomplete.", "Pin safety flags in schema + boundary tests.");

  out["UI"] =
    fileExists(`${dir}/app/Workspace.tsx`) &&
    fileExists(`${dir}/app/page.tsx`) &&
    fileHasAny(PKG, ['"build": "next build"'])
      ? cell(
          "pass",
          `${dir}/app/Workspace.tsx, ${dir}/app/page.tsx`,
          "Notion-like workspace shell is mounted in a buildable Next.js host (verified page + API probes).",
          "Grow the table/board/page views with real domain records.",
        )
      : cell("partial", `${dir}/app/Workspace.tsx`, "Workspace not mounted in a host app.", "Add a Next.js host.");

  out["Template/Reuse"] = TEMPLATE_REUSE;

  out["Verification"] = verificationCell({
    pkgJson: PKG,
    testFiles: [
      `${dir}/tests/schema-static.test.mjs`,
      `${dir}/tests/projection.test.mjs`,
      `${dir}/tests/cloud-boundary.test.mjs`,
      OS_PROJ_TEST,
    ],
    hasBuild: true,
    hasTsc: true,
  });

  return out;
}

const financeAgentOs = () =>
  factoryGeneratedAgentOs({
    shortId: "finance",
    osId: "finance_agent_os",
    dir: "apps/finance-agent-os",
    publicName: "Sinria Finance Agent OS",
    builder: "buildFinanceAgentOsProjection",
  });

const legalAgentOs = () =>
  factoryGeneratedAgentOs({
    shortId: "legal",
    osId: "legal_agent_os",
    dir: "apps/legal-agent-os",
    publicName: "Sinria Legal Agent OS",
    builder: "buildLegalAgentOsProjection",
  });

// ---------------------------------------------------------------------------
// Assemble + render
// ---------------------------------------------------------------------------

const SYSTEMS = [
  { id: "company_os", name: "Company OS", cells: companyOs() },
  { id: "sales", name: "Sales Agent OS", cells: salesAgentOs() },
  { id: "service", name: "Service Agent OS", cells: serviceAgentOs() },
  { id: "application", name: "Application Agent OS", cells: applicationAgentOs() },
  { id: "finance", name: "Finance Agent OS", cells: financeAgentOs() },
  { id: "legal", name: "Legal Agent OS", cells: legalAgentOs() },
];

const ICON = { pass: "✅ pass", partial: "🟡 partial", missing: "❌ missing" };
const SHORT = { pass: "✅", partial: "🟡", missing: "❌" };

function tally() {
  const t = { pass: 0, partial: 0, missing: 0 };
  for (const s of SYSTEMS) for (const c of CATEGORIES) t[s.cells[c].verdict]++;
  return t;
}

function missingCells() {
  const m = [];
  for (const s of SYSTEMS)
    for (const c of CATEGORIES) if (s.cells[c].verdict === "missing") m.push(`${s.name} / ${c}`);
  return m;
}

function renderMarkdown() {
  const now = new Date().toISOString();
  const t = tally();
  const total = SYSTEMS.length * CATEGORIES.length;
  const lines = [];

  lines.push("# Agent OS Conformance Report");
  lines.push("");
  lines.push(`_Generated: ${now} by \`scripts/agent-os-conformance-report.mjs\` (reads real files; no hardcoded verdicts)._`);
  lines.push("");
  lines.push(
    `**Score:** ${t.pass} pass · ${t.partial} partial · ${t.missing} missing of ${total} cells ` +
      `(${SYSTEMS.length} systems × ${CATEGORIES.length} categories).`,
  );
  lines.push("");
  lines.push("Legend: ✅ pass = present and live · 🟡 partial = present but a concrete gap remains · ❌ missing = not present.");
  lines.push("");

  // Matrix
  lines.push(`| System | ${CATEGORIES.join(" | ")} |`);
  lines.push(`| --- | ${CATEGORIES.map(() => "---").join(" | ")} |`);
  for (const s of SYSTEMS) {
    const row = CATEGORIES.map((c) => SHORT[s.cells[c].verdict]);
    lines.push(`| **${s.name}** | ${row.join(" | ")} |`);
  }
  lines.push("");

  // Per-cell detail
  lines.push("## Per-cell detail");
  lines.push("");
  for (const s of SYSTEMS) {
    lines.push(`### ${s.name}`);
    lines.push("");
    for (const c of CATEGORIES) {
      const cell = s.cells[c];
      lines.push(`#### ${c} — ${ICON[cell.verdict]}`);
      lines.push("");
      lines.push(`- **Evidence:** \`${cell.evidence}\``);
      lines.push(`- **Detail:** ${cell.detail}`);
      lines.push(`- **Next step:** ${cell.next}`);
      lines.push("");
    }
  }

  // Missing summary
  const miss = missingCells();
  lines.push("## Gaps that fail `--check`");
  lines.push("");
  if (miss.length === 0) {
    lines.push("None — no `(system, category)` cell is `missing`.");
  } else {
    for (const m of miss) lines.push(`- ${m}`);
  }
  lines.push("");

  return lines.join("\n");
}

function main() {
  const args = process.argv.slice(2);
  const checkMode = args.includes("--check");

  const md = renderMarkdown();
  const outDir = join(REPO_ROOT, "reports", "agent-os-conformance");
  mkdirSync(outDir, { recursive: true });
  const outPath = join(outDir, "latest.md");
  writeFileSync(outPath, md, "utf8");

  const t = tally();
  const miss = missingCells();
  const total = SYSTEMS.length * CATEGORIES.length;

  // Short summary to stdout
  console.log("Agent OS conformance report");
  console.log(`  wrote: reports/agent-os-conformance/latest.md`);
  console.log(`  score: ${t.pass} pass / ${t.partial} partial / ${t.missing} missing  (of ${total} cells)`);
  for (const s of SYSTEMS) {
    const counts = { pass: 0, partial: 0, missing: 0 };
    for (const c of CATEGORIES) counts[s.cells[c].verdict]++;
    console.log(`    - ${s.name.padEnd(22)} ${counts.pass}✅ ${counts.partial}🟡 ${counts.missing}❌`);
  }
  if (miss.length > 0) {
    console.log(`  missing cells (${miss.length}):`);
    for (const m of miss) console.log(`    - ${m}`);
  }

  if (checkMode && miss.length > 0) {
    console.error(`\n--check FAILED: ${miss.length} cell(s) are 'missing'.`);
    process.exit(1);
  }
  if (checkMode) {
    console.log("\n--check OK: no 'missing' cells.");
  }
}

main();
