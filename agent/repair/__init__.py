"""Sinria codebase self-repair loop — Phase 2 repair machinery.

Modules (kept import-light; import submodules directly):

- ``contract``     — per-repo ``.sinria/repair.yaml`` opt-in loader (fail-closed)
- ``risk``         — risk classification, self-repair recursion guard, diff gates
- ``tickets``      — durable repair-ticket store + state machine + ledgers
- ``intake``       — defect summaries → ticket creation / issue proposals
- ``evidence_intake`` — confirmation-gated screenshot/paste metadata intake
- ``executor``     — real side effects (worktree, commands, adapter, PR)
- ``orchestrator`` — drives tickets through the state machine (PR-proposal only)

Design doc: docs/plans/2026-07-06-codebase-self-repair-loop-design.md.
Safety invariant: this package is permanently human-only — the orchestrator
must never patch these files (enforced in ``risk.SELF_REPAIR_PROTECTED_PATHS``).
"""
