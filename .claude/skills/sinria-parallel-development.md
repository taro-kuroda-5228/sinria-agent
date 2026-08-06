# Sinria Parallel Development Rule for Claude Code

Use this skill/rule at the start of any session that will edit this repository,
and immediately whenever a `Bash`, `Write`, or `Edit` call is refused with
"Sinria primary checkout is read-only for development".

## The constraint

The Sinria primary checkout (`SINRIA_PRIMARY_CHECKOUT`, normally `~/sinria`) is
read-only for development. A `PreToolUse` hook running
`scripts/sinria_primary_checkout_guard.py` refuses:

- any `Write`/`Edit`/`MultiEdit`/`NotebookEdit` whose target is inside it;
- any `Bash` command whose session cwd is inside it;
- any `Bash` command that names a path inside it.

`Read`, `Grep`, and `Glob` stay available, so the primary checkout is still the
place to *read* the canonical tree.

## What "names a path inside it" means

The guard resolves the command before comparing, so all of these are the same
refusal, not just the literal spelling:

- `rm -rf <primary>/x` and `rm -rf ~/sinria/x`
- `rm -rf ../../sinria/x` — relative traversal out of the worktree
- `cd ../.. && rm -rf sinria` — `cd` moves the anchor for later words
- `rm -rf $REPO/x` when `REPO` resolves to the primary checkout
- `rm -rf ../../sinri*` or `rm -rf ../../sinri{a,b}` — a pattern that can
  produce it (quoted braces are left alone, so `curl -d '{"a":1}'` is fine)
- `python3 -c '...shutil.rmtree("../../sinria")...'` — a path inside quoted code

## When the guard cannot resolve, it refuses

If a command contains a value the guard cannot determine, it refuses instead of
guessing, because that value could be the primary checkout:

- a command substitution — `$(...)` or backticks;
- a variable with no value in the hook's environment (one exported only by your
  shell profile is invisible to the hook, so "unknown" is not "harmless");
- `cd -`, or a command that does not parse (an unterminated quote).

This is expected behaviour in a worktree too. The fix is to rewrite the command
with literal paths, or single-quote text you do not want expanded — `awk
'{print $1}'` is fine, because single quotes suppress expansion. Do not work
around it by disabling the hook.

## Do not create an in-repo worktree

Do **not** use the harness's default worktree workflow here. It creates
`<repo>/.claude/worktrees/<name>`, which is inside the primary checkout, so
every shell command in that worktree is blocked and the session cannot recover.
The same applies to any other in-repo path such as `.worktrees/`.

Development worktrees live **outside** the primary checkout, under the
workspace root — `~/sinria-worktrees` by default, overridable with
`SINRIA_WORKTREE_ROOT`.

## The sanctioned flow

`scripts/sinria_worktree_bootstrap.py` is the only program the guard allows to
run from inside the primary checkout, so this works even from a blocked
session:

```bash
# Diagnose: where is the workspace root, and are any worktrees mis-placed?
python3 scripts/sinria_worktree_bootstrap.py status

# Register an isolated worktree outside the primary checkout
python3 scripts/sinria_worktree_bootstrap.py create --name my-change --branch fix/my-change

# List what is already registered
python3 scripts/sinria_worktree_bootstrap.py list
```

`create` prints the path and the command to start the session there. Restart
the session with that worktree as cwd — `Bash` and the write tools then work
normally, and `scripts/run_tests.sh` finds the shared venv automatically.

The helper refuses to place a worktree inside the primary checkout, so it
cannot reproduce the failure it exists to prevent.

`--branch` and `--base` accept plain ref names only: slash-separated segments
of `[A-Za-z0-9._-]` starting with a letter, digit or underscore. Revision
syntax (`~`, `^`, `@{...}`, `:`) and `refs/...` paths are refused, and `--base`
must resolve to a commit. This is what stops a value such as `--force` from
being read as a Git option instead of a revision; the refusal names the rule,
never the rejected value.

## Leave Sinria's own worktrees alone

Sinria registers worktrees of its own, and they are not development worktrees:

- `sinria -w` / `--worktree` (`cli.py`) puts a per-CLI-session worktree at
  `<repo>/.worktrees/sinria-<id>`;
- gateway sessions take persistent leases through `gateway/workspace_lease.py`
  under `<SINRIA_HOME>/worktrees/gateway-sessions/`.

They run Sinria's own executor, never a Claude Code session, so the guard never
sees them. Do not migrate, prune, or "align" them because of this rule. You
still must not develop inside `<repo>/.worktrees/` yourself — it is inside the
primary checkout, so every shell command there is refused.

## A stale installed guard is Taro's call, not yours

The hook runs an *installed copy* of the guard, so a repository fix to
`scripts/sinria_primary_checkout_guard.py` has no effect until that copy is
refreshed. `python3 scripts/sinria_worktree_bootstrap.py status` reports the
drift and prints the exact refresh command.

Report it; do not run it. Replacing the copy the hook executes changes every
future session on the machine, which is a deploy and needs Taro's explicit
approval — the same class as production deploys and migrations. The same
applies to editing or removing the hook itself.

## Allow-list boundaries

The guard opens for a *bare* invocation only: `python3` (or `pythonN.N`)
running exactly `<primary>/scripts/sinria_worktree_bootstrap.py`, with no shell
metacharacters, no command chaining, no substitutions, and no argument that
points back into the primary checkout. Do not try to widen it; run everything
else from the worktree.

## Verification expectation

Before claiming done, run the focused tests for the touched layer with
`scripts/run_tests.sh`. For changes to this flow that means at minimum
`tests/scripts/test_primary_checkout_guard.py`,
`tests/scripts/test_sinria_worktree_bootstrap.py`, and
`tests/test_claude_worktree_workflow_alignment.py`.

## Related

- `.claude/skills/sinria-correction-loop.md` — Correction Loop / Company OS rules.
- `AGENTS.md` → "Claude Code / Correction Loop Parity" — canonical statement.
