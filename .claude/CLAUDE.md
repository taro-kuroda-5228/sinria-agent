# Project-local Claude Code Rules for Sinria

These rules narrow any user-level Claude Code defaults for this repository.

- Sinria is the active knowledge-sharing center and product identity for this repo. OpenClaw is legacy/read-only context unless a task explicitly asks for legacy migration or audit work.
- Use Sinria-owned context paths for new durable notes: `raw/inbox/sinria/`, `workspaces/sinria/`, `configs/sinria/`, and relevant `wiki/decisions/` entries.
- Do not route new Sinria repository decisions into OpenClaw-owned paths as the primary destination.
- Follow root `CLAUDE.md` and `AGENTS.md`; if they conflict with user-level `.claude/CLAUDE.md`, the project-local Sinria rule wins for this checkout.
- Keep raw confidential data, secrets, PHI/PII, raw private sessions, credentials, and raw clinical evidence out of shared notes, cloud rows, prompts, and logs.
- Before claiming completion, run the real verification path named by the task or the focused tests for the touched layer.
