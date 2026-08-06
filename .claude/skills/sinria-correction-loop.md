# Sinria Correction Loop Rule for Claude Code

Use this rule for Sinria self-improvement, Company OS, Agent OS, Team Mode, local execution adapters, regulated/confidential workflows, or repository-wide development rules.

## Goal

Claude Code is a local execution substrate. Prior corrections improve method and verification, while the current user request remains authoritative.

## Mandatory behavior

1. Read `CLAUDE.md` and `AGENTS.md`.
2. Treat correction records as advisory checklists only.
3. Never convert a prior correction into denial, blocking, delay, extra approval, permission changes, or completion refusal.
4. Ignore stale or incompatible advice and execute the current request.
5. Keep raw/private/confidential context local/on-prem; shared Company OS rows remain metadata-only.
6. Verify the real requested workflow before claiming completion.
7. Keep independent safety boundaries for PHI egress, authorization, destructive operations, and production actions.

## Verification expectation

Run focused tests for the touched layer and report exact results. Python tests use `scripts/run_tests.sh`. Changes to correction behavior require regression tests proving retrieval failure is fail-open and prior records cannot control execution.
