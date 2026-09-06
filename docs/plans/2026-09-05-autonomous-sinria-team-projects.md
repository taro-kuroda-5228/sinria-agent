# Autonomous Sinria Team Projects Implementation Plan

> **For Sinria:** Use Sinria subagent-driven-development workflow to implement this plan task-by-task.

**Goal:** Build a local-first project orchestration runtime in which multiple Sinria workers can accept a bounded project, execute a dependency graph, review/revise results, and close the project only after evidence-backed acceptance criteria pass.

**Architecture:** Add a dependency-free Python core with a durable metadata-only control store, deterministic capability-based assignment, risk-based approval gates, idempotent execution attempts, review/revision transitions, and restart recovery. Raw confidential context may remain in a local personal store or Company Knowledge and is passed by typed local references; credentials remain in a local vault and are also passed by reference. Keep transport and model execution pluggable so the existing Company OS peer layer can become an adapter without copying confidential payloads into the orchestration control plane. Prove the real workflow with two distinct local worker identities and a restart in the middle.

**Tech Stack:** Python 3.11 dataclasses/typing/json/pathlib; pytest through `scripts/run_tests.sh`; existing Sinria privacy and peer-collaboration conventions.

---

## Target Completion Contract

**Desired endpoint:** A caller submits a project goal, explicit acceptance criteria, and safety classification; Sinria decomposes it through a planner callback, assigns dependency-ready tasks to capable fresh workers, executes local handlers, reviews outcomes, revises bounded failures, and persists an evidence-backed terminal project state.

**Primary workflow that must work:** create project → plan DAG → assign ready tasks → execute distinct worker handlers → review each result → revise rejected work up to a bound → block approval-required side effects → recover after process restart → evaluate project acceptance → mark `completed` only when all criteria are verified.

**Acceptance criteria:**
- Orchestration state is metadata-only and rejects raw bodies, credential values, PHI-shaped fields, and external URL evidence. Confidential content may be retained in local personal storage or Company Knowledge and referenced with `local://` or `company-knowledge://`; credentials use `vault://` references.
- Dependencies are respected; failed/blocked prerequisites prevent downstream execution.
- Assignment is deterministic and capability/freshness aware; a missing capable worker produces `waiting_worker` rather than fake completion.
- Internal, non-egress, reversible operations execute autonomously, including local and Company Knowledge writes and local deletion explicitly marked recoverable. Explicit approval is limited to external egress, irreversible deletion, billing, auth/permission changes, production actions, and external release of clinical/patient data.
- Every execution has a stable idempotency key and bounded attempts; restart recovery does not duplicate an accepted task.
- Reviewer verdicts are `accepted`, `revision_requested`, or `decision_required`; revision is bounded and escalation is terminal until human action.
- Project status becomes `completed` only after every task is accepted and every project acceptance criterion has an evidence reference.
- A real local smoke uses two worker identities, at least three dependent tasks, one revision, one approval gate, and a new orchestrator instance reading the same store after restart.

**Non-goals / must-not-change:**
- No production Company OS deploy, database migration, Gateway restart, external send, billing/auth change, or employee-machine modification.
- Do not replace the existing consultation runtime; add an orchestration core and adapter boundary.
- Do not store raw documents, prompts, PHI/PII, credentials, or model transcripts in project state.

**Safety gates:** Production rollout and installation on employee Sinria instances require separate human approval. External egress, irreversible deletion, billing, auth/permission changes, production actions, and external clinical release fail closed without approval; internal reversible work remains autonomous.

**Verification before done:** Focused RED/GREEN tests; peer/runtime regression tests; static secret/PHI scan; real CLI/local smoke with persisted readback and exact terminal states; diff review.

## Task 1: Define the project state contract with RED tests

**Files:**
- Create: `tests/test_team_project_orchestrator.py`
- Create: `sinria_team_projects.py`

**Steps:**
1. Write failing tests for schema validation, DAG validation, safe metadata, operation gates, evidence requirements, and terminal completion rules.
2. Run `scripts/run_tests.sh tests/test_team_project_orchestrator.py -q` and confirm expected import/behavior failures.
3. Implement only the dataclasses, validators, and serialization needed for GREEN.
4. Re-run focused tests.

## Task 2: Add durable state and restart recovery

**Files:**
- Modify: `tests/test_team_project_orchestrator.py`
- Modify: `sinria_team_projects.py`

**Steps:**
1. Add failing tests for atomic JSON persistence, version/revision checks, stable idempotency keys, and recovery of an interrupted `running` task without duplicating accepted tasks.
2. Verify RED.
3. Implement a metadata-only `JsonProjectStore` and recovery transitions.
4. Verify GREEN.

## Task 3: Add planning, assignment, execution, review, and revision

**Files:**
- Modify: `tests/test_team_project_orchestrator.py`
- Modify: `sinria_team_projects.py`

**Steps:**
1. Add failing behavior tests for dependency ordering, capability/freshness assignment, two worker identities, evidence-backed results, accepted/revision/decision-required verdicts, and bounded retries.
2. Verify RED.
3. Implement `TeamProjectOrchestrator` with injected planner, executor registry, reviewer, and acceptance evaluator.
4. Verify GREEN and refactor while green.

## Task 4: Add a real local smoke entrypoint

**Files:**
- Create: `scripts/sinria-team-project-smoke.py`
- Create: `tests/test_team_project_smoke.py`

**Steps:**
1. Add a failing subprocess test for a three-task project using planner/researcher/reviewer worker identities, one revision, one explicit approval, persisted restart, and a final `completed` readback.
2. Verify RED.
3. Implement the smoke CLI without network calls or confidential data.
4. Verify GREEN and run the CLI directly.

## Task 5: Integrate boundaries and regression verification

**Files:**
- Modify: `tests/test_team_project_orchestrator.py`
- Modify documentation only if needed for the adapter contract.

**Steps:**
1. Add/verify tests proving the existing consultation path remains unchanged and project control-plane payloads contain no raw content.
2. Run focused tests through `scripts/run_tests.sh`.
3. Run peer-collaboration regressions.
4. Run diff checks and a staged secret/PHI/residue scan.
5. Commit and push the isolated feature branch; do not deploy or restart production services.
