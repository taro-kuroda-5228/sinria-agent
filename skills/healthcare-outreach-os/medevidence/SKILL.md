---
name: medevidence
description: "MedEvidence / メドエビデンス連携: existing MedEvidence repo, OpenClaw skills, clinical evidence workflows, and Sinria-safe handoff."
version: 0.1.0
author: Sinria Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [sinria, medevidence, medical, evidence, clinical, openclaw, healthcare]
    related_skills: [sinria-agent, google-workspace]
---

# MedEvidence / メドエビデンス

Use this skill when Sinria needs to inspect, support, or operate MedEvidence / メドエビデンス work.

## Project identity and boundary

- MedEvidence is a medical evidence / consent-agent product and codebase.
- Sinria is the AgentOS substrate that can build, support, and operate MedEvidence workflows.
- Do not merge the two product identities in user-facing text: say "Sinria can operate/support MedEvidence" rather than "MedEvidence is Sinria".

## Known local repository

On Taro's current machine, the working copy has been observed at:

```text
~/med_evi-2
```

Before editing it, inspect its project instructions (especially `CLAUDE.md`, `MEMORY.md`, and `.claude/rules/` when present).  Do not assume this path exists on every Sinria install; if missing, ask for or discover the repository path.

## What MedEvidence contains

From the local project guide, MedEvidence includes:

- Next.js 15 + Supabase medical AI platform.
- OpenClaw-style skill layer under `packages/core/src/openclaw/`.
- Medical skills such as consensus search, frontier search, intent router, fact checker, paper writing, guideline search, drug safety check, voice search, surgical decision support, and clinical action planning.
- Specialist subagents for chart summarization, referral letters, web intake, reimbursement support, and clinical action plans.
- Multi-source medical search: PubMed, Google, medRxiv, ClinicalTrials.gov, Cochrane.

## Sinria-safe operating rules

1. Treat patient, hospital, and tenant data as regulated/confidential.
2. Prefer local repo inspection, tests, docs, and sanitized fixtures.
3. Do not send clinical payloads to external services unless the deployment policy explicitly allows it.
4. Draft-only by default for Google Workspace or other SaaS side effects; physician approval is required for clinical consent workflows.
5. When mapping MedEvidence OpenClaw skills into Sinria, preserve audit trails, source citations, model/provider choices, and tenant boundaries.

## Useful commands

From `~/med_evi-2`:

```bash
npm run lint
npx tsc --noEmit
npm run test
npm run build
npm run check:workspace-deps
```

For quick architecture inspection:

```bash
# Use file/search tools when available; shell equivalent shown for humans.
rg "SkillRegistry|MedicalSkill|registerCoreSkills" packages/core/src/openclaw packages/core/src/skills
```

## Sinria integration path

- Short term: load this skill in Sinria and inspect MedEvidence locally for workflow-specific support.
- Bridge path: expose MedEvidence skills as Sinria skills or connector-backed tools through a local, audited adapter rather than direct external network calls.
- Safer first adapters: read-only evidence search, guideline lookup, fact-check summarization, and draft consent workspace planning.
- Current Sinria adapter manifest: `sinria_integrations.medevidence_skill_catalog()` lists the MedEvidence/OpenClaw skill IDs and safety metadata, `describe_medevidence_skill_usage()` returns a concrete Sinria usage guide for one skill, and `plan_medevidence_skill_operation()` turns an intended use into a sanitized `PlannedOperation` via the `medevidence_local` connector.
- PHI rule: public/external-transmitting skills such as `consensus-search`, `guideline-search`, and `fact-checker` must receive de-identified/public queries only; PHI-capable skills such as `chart-summary`, `drug-safety-check`, and `clinical-action-plan` are local draft operations by default.
- Higher-risk adapters: EHR/カルテ/EMR writeback, patient messaging, consent release, or SaaS sharing. These require explicit institution policy and human approval gates.

## Handoff checklist

When a Sinria task changes MedEvidence-related behavior, report:

- Which repository/path was used.
- Whether any patient/confidential data was touched (should normally be "no").
- Commands/tests run.
- Any blocked external egress or missing credentials.
- Next safe adapter or skill-migration step.
