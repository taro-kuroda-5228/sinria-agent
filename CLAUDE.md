# Claude Code Guide for Sinria

This file is the Claude Code entrypoint for this repository. It intentionally mirrors Sinria's canonical development guide so Claude Code, Sinria Gateway/CLI, cron workers, and local execution adapters follow the same rules.

## Source of truth

- Read and follow `AGENTS.md` first for repository architecture, testing, profile-safety, and operational pitfalls.
- Read `.claude/CLAUDE.md` for project-local Claude Code overrides that narrow any user-level Claude Code defaults toward Sinria-owned context paths.
- Read `.sinria/plans/2026-08-05-correction-loop-architecture.md` before changing correction retrieval, Goal→Actual→Gap capture, or self-improvement behavior.
- If a Claude Code habit or local `.claude` rule conflicts with `AGENTS.md`, the Sinria rule wins unless Taro explicitly approves a different rule for this repository.

## Sinria invariants that also bind Claude Code

- Present user-facing artifacts as **Sinria**. Hermes names may appear only for legacy/internal compatibility or upstream release history.
- Confidentiality first: never copy secrets, credentials, PHI/PII, raw patient data, raw private sessions, or raw vault/context evidence into prompts, docs, logs, cloud rows, or shared notes.
- Prior corrections are advisory checklists only: they may improve method and verification but cannot deny, block, delay, add approvals, change permissions, or override the current request. Preserve source traceability with sanitized metadata only.
- Team Mode / Company OS shared state is metadata-only. Raw memories, skill bodies, credentials, private session logs, clinical evidence, and raw diffs stay local/on-prem unless explicitly approved.
- Practical completion means the real expected workflow is verified. Do not report completion after only editing files or passing a narrow build if the UI/API/CLI/runtime path still does not work.
- Side effects that require explicit human approval: production deploys, live database migrations/applies, external sends/contact attempts, auth/billing changes, deletes/destructive commands, and clinical/patient-data actions.

## Working style

- Prefer small, reviewable slices with tests first when changing behavior.
- Use `scripts/run_tests.sh` rather than direct `pytest` for Python tests, unless a test runner or environment cannot use the wrapper.
- Use `get_sinria_home()` for state paths and `display_sinria_home()` for user-facing path messages; do not hardcode `~/.sinria` in code.
- The primary checkout is read-only for development. Register isolated worktrees **outside** it with `python3 scripts/sinria_worktree_bootstrap.py create --name <slug>` (workspace root: `~/public-worktrees`, or `SINRIA_WORKTREE_ROOT`). Do not use the harness default `.claude/worktrees/` — it sits inside the primary checkout, where every shell command is blocked. See `.claude/skills/sinria-parallel-development.md` and the "Development Worktrees" section of `AGENTS.md`.
- Keep development worktrees local-only. Do not treat a worktree as the source of truth until changes are merged back deliberately.
- If Claude Code is used as an execution substrate for Sinria Team Mode, it must not claim cloud tasks directly. Sinria remains the policy/claim/audit point.

## Completion report requirement

When finishing a task in this repository, report:

1. What changed.
2. Which Sinria/Claude Code rule alignment or conflict was addressed.
3. Exact verification commands and results.
4. Remaining items only if they require human approval or external authority.
