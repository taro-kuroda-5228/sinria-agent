# Autonomous Sinria Peer Maintenance Implementation Plan

> **For Sinria:** Use Sinria subagent-driven-development workflow to implement this plan task-by-task.

**Goal:** Eliminate routine LINE relay by allowing trusted Sinria workers to receive, execute, verify, and acknowledge fixed-scope runtime maintenance through Company OS.

**Architecture:** Add a deterministic `sinria-runtime-maintenance` team capability. It accepts no shell text or raw context, requires a fixed Company OS standing-policy reference, fetches only the official `sinria-agent` origin, stages a clean standalone formal release without modifying a dirty developer checkout, runs focused tests, emits metadata-only evidence, and schedules an atomic worker activation after the response is persisted. A fixed dispatcher routes only allowlisted local capabilities.

**Tech Stack:** Python, git, launchd, existing `team-project.v1` transport, pytest through `scripts/run_tests.sh`.

---

### Task 1: Encode the security and staging contract

**Files:**
- Create: `tests/test_peer_runtime_maintenance.py`
- Create: `sinria_peer_runtime.py`

1. Write failing tests for fixed capability/policy, official-origin enforcement, dirty-checkout preservation, standalone release staging, metadata-only receipts, and idempotency.
2. Run the focused test through `scripts/run_tests.sh` and confirm RED due to missing implementation.
3. Implement the smallest staging and receipt functions.
4. Re-run focused tests to GREEN.

### Task 2: Add fixed capability dispatch and delayed activation

**Files:**
- Create: `scripts/sinria-team-project-executor.py`
- Create: `scripts/sinria-peer-runtime-activate.py`
- Modify: `scripts/install-sinria-peer-service.py`
- Test: `tests/test_peer_runtime_maintenance.py`
- Test: `tests/test_peer_service_installer.py`

1. Write RED tests proving arbitrary capabilities/commands are rejected and installer arguments contain no credentials.
2. Route only `control-plane-canary` and `sinria-runtime-maintenance`.
3. Schedule activation only after a receipt is ready; install both roles from the formal release root.
4. Run focused tests to GREEN.

### Task 3: Integrate and verify the real path

**Files:**
- Modify: `scripts/sinria-peer-worker.py` only if response-before-activation ordering requires it.

1. Run team/peer focused suites, Ruff, compileall, and `git diff --check`.
2. Commit, push, open PR, read CI, merge, and sync the primary checkout.
3. Reinstall Taro workers from canonical main with both capabilities.
4. Execute a live metadata-only maintenance canary against Taro and verify request, claim, response, criteria evidence, validator acceptance, heartbeat, and no external action.
5. Send a typed maintenance task to Kikuchi only after the new capability heartbeat is visible. Until then, report the one-time legacy bootstrap boundary rather than claiming LINE-free completion.
