# Sinria v0.14.6

v0.14.6 supersedes v0.14.5.

## Context isolation correction

- Removes the retired `agent.context_share` package, resolver gates, scripts, tests, and the public-only self-improvement tool that still depended on the legacy namespace.
- Keeps only the fail-open, advisory-only Correction Loop. Prior corrections cannot deny, block, delay, require approval, change permissions, or override the current request.
- Adds release invariants proving the legacy package is absent, active runtime files do not import it, and current-turn correction advice remains connected to the conversation loop.
- Keeps confidential-data filtering under the independent privacy-sanitization module.

## Upgrade from v0.14.5 or earlier

New installations need no migration. Existing installations can inspect compatible legacy local records without writing anything:

```bash
python -m agent.correction_loop.migrate_legacy
```

After reviewing the dry-run output, copy compatible records into the new local stores explicitly:

```bash
python -m agent.correction_loop.migrate_legacy --apply
```

The migration is local, non-destructive, and idempotent. The retired source files remain inert and are never read by the active runtime.
