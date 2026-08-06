#!/usr/bin/env python3
"""Register isolated Sinria development worktrees outside the primary checkout.

The Sinria primary checkout is read-only for development (enforced by
``scripts/sinria_primary_checkout_guard.py``). Coding-agent sessions therefore
have to work in a separate, registered Git worktree. Agent harnesses tend to
default to an *in-repo* worktree directory such as ``.claude/worktrees/``, which
sits inside the primary checkout and is consequently blocked for every shell
command — a dead end that cannot even be escaped from, because creating the
replacement worktree would itself need a shell.

This helper is the sanctioned way out. It is the only program the primary
checkout guard allows to run from inside the primary checkout, so it is
deliberately narrow:

* it never writes into the primary checkout's working tree;
* it refuses to place a worktree inside the primary checkout at all;
* it validates ``--branch``/``--base`` against a conservative ref grammar,
  resolves ``--base`` to a commit itself, and passes ``--`` to Git, so a
  caller-supplied value can never be parsed as a Git option;
* it prints paths and validated branch names only — never rejected input,
  command input, file contents, credentials, or environment values.

Usage::

    python3 scripts/sinria_worktree_bootstrap.py status
    python3 scripts/sinria_worktree_bootstrap.py create --name my-change
    python3 scripts/sinria_worktree_bootstrap.py list

The workspace root defaults to ``<primary>-worktrees`` next to the primary
checkout (so ``~/sinria`` yields ``~/sinria-worktrees``) and can be overridden
with ``SINRIA_WORKTREE_ROOT`` or ``--workspace-root``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BRANCH_NAMESPACE = "sinria"

# One segment of a ref name. Deliberately narrower than `git check-ref-format`:
# it admits only what Sinria branches actually use, so revision syntax (`~`,
# `^`, `@{}`, `:`), shell metacharacters, whitespace, control characters and
# leading hyphens are all outside the alphabet rather than special-cased.
_REF_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
_MAX_REF_LEN = 128

_INSIDE_PRIMARY_MSG = (
    "refused: the requested worktree path is inside the primary checkout. "
    "Sinria development worktrees must live outside it "
    "(the primary checkout is read-only for development)."
)
# Rejected values are never quoted back: the message describes the rule only.
_BRANCH_MSG = (
    "refused: --branch must be a plain branch name — slash-separated segments of "
    "[A-Za-z0-9._-] starting with a letter, digit or underscore, at most "
    f"{_MAX_REF_LEN} characters, and not a 'refs/...' path. "
    "The default is sinria/<name>."
)
_BASE_MSG = (
    "refused: --base must be a plain branch name, tag or commit id — "
    "slash-separated segments of [A-Za-z0-9._-] starting with a letter, digit or "
    f"underscore, at most {_MAX_REF_LEN} characters. Revision syntax such as "
    "'~', '^', '@{...}' and ':' is not accepted."
)
_BASE_UNRESOLVED_MSG = (
    "refused: --base does not resolve to a commit in the primary checkout. "
    "Pass an existing branch, tag or commit id."
)


# ── Ref validation ─────────────────────────────────────────────────────────


def _is_safe_ref(value: str) -> bool:
    """True for a conservative, unambiguous ref name.

    Every rejection here is a fail-closed refusal *before* Git runs, which is
    what keeps a caller-supplied string from ever reaching Git's option parser
    or its revision grammar.
    """
    if not value or len(value) > _MAX_REF_LEN or ".." in value:
        return False
    return all(
        _REF_SEGMENT_RE.match(segment) and not segment.endswith((".", ".lock"))
        for segment in value.split("/")
    )


def is_safe_branch(value: str) -> bool:
    """A branch name the helper is willing to create or check out."""
    # `refs/heads/x` as a branch name would land at `refs/heads/refs/heads/x`.
    return _is_safe_ref(value) and not value.startswith("refs/")


def is_safe_base(value: str) -> bool:
    """A revision the helper is willing to hand to Git as a start point."""
    return _is_safe_ref(value)


# ── Path helpers ───────────────────────────────────────────────────────────


def _resolve(path: Path) -> Path:
    return Path(os.path.expanduser(str(path))).resolve(strict=False)


def is_inside(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` itself or lives underneath it."""
    try:
        _resolve(path).relative_to(_resolve(root))
        return True
    except (OSError, ValueError):
        return False


def resolve_primary(explicit: str | None = None, cwd: Path | None = None) -> Path:
    """Locate the primary checkout: explicit flag, env, then the git common dir."""
    if explicit:
        return _resolve(Path(explicit))

    env_value = os.environ.get("SINRIA_PRIMARY_CHECKOUT", "").strip()
    if env_value:
        return _resolve(Path(env_value))

    start = cwd or Path.cwd()
    common = _git(["rev-parse", "--git-common-dir"], cwd=start, check=False)
    if common is not None:
        common_dir = Path(common.strip())
        if not common_dir.is_absolute():
            common_dir = start / common_dir
        common_dir = _resolve(common_dir)
        if common_dir.name == ".git":
            return common_dir.parent

    raise SystemExit(
        "error: cannot locate the Sinria primary checkout. "
        "Set SINRIA_PRIMARY_CHECKOUT or pass --primary."
    )


def helper_checkout() -> Path:
    """The checkout this file itself lives in.

    ``--primary`` is caller-supplied, so it cannot be the only anchor for
    "outside the primary checkout": pointing it at a decoy repository would
    make the containment check answer a question about the decoy while the
    worktree still lands in the read-only tree. The guard allowance pins the
    sanctioned invocation to ``<primary>/scripts/``, so the directory this file
    sits in is an anchor the caller does not control.
    """
    return _resolve(Path(__file__).resolve().parents[1])


def resolve_workspace_root(explicit: str | None, primary: Path) -> Path:
    if explicit:
        return _resolve(Path(explicit))
    env_value = os.environ.get("SINRIA_WORKTREE_ROOT", "").strip()
    if env_value:
        return _resolve(Path(env_value))
    return _resolve(primary.parent / f"{primary.name}-worktrees")


# ── Git plumbing ───────────────────────────────────────────────────────────


def _git(args: list[str], cwd: Path, check: bool = True) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        if check:
            raise SystemExit("error: git is not available on PATH.")
        return None
    if result.returncode != 0:
        if check:
            raise SystemExit(f"error: git {args[0]} failed: {_first_line(result.stderr)}")
        return None
    return result.stdout


def _first_line(stderr: str, limit: int = 200) -> str:
    """Git's own first line of diagnosis, bounded.

    Everything the helper passes to Git has already been validated, so this can
    only echo sanctioned values; the bound keeps an unexpected multi-line or
    oversized message from turning into a transcript dump.
    """
    line = stderr.strip().splitlines()[0].strip() if stderr.strip() else "no diagnostic"
    return line if len(line) <= limit else f"{line[:limit]}…"


def registered_worktrees(primary: Path) -> list[dict]:
    """Every worktree git knows about, excluding the primary checkout itself."""
    raw = _git(["worktree", "list", "--porcelain"], cwd=primary, check=False) or ""
    entries: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line[len("worktree ") :].strip(), "branch": None}
        elif line.startswith("branch ") and current:
            current["branch"] = line[len("branch ") :].strip().replace("refs/heads/", "", 1)
        elif line.startswith("detached") and current:
            current["branch"] = None
    if current:
        entries.append(current)

    out = []
    for entry in entries:
        path = Path(entry["path"])
        if _resolve(path) == _resolve(primary):
            continue
        out.append(
            {
                "name": path.name,
                "path": str(_resolve(path)),
                "branch": entry["branch"],
                "inside_primary": is_inside(path, primary),
            }
        )
    return out


def _branch_exists(primary: Path, branch: str) -> bool:
    return _rev_parse(primary, f"refs/heads/{branch}") is not None


def _resolve_commit(primary: Path, base: str) -> str | None:
    """The commit id ``base`` names, or ``None`` when it names nothing or a non-commit.

    Peeling with ``^{commit}`` is what rejects a tag that points at a tree or a
    blob, and returning the id means Git is later handed an object name rather
    than a string it would have to interpret a second time.
    """
    return _rev_parse(primary, f"{base}^{{commit}}")


def _rev_parse(primary: Path, spec: str) -> str | None:
    # `--end-of-options` is belt-and-braces: `spec` is validated before it gets
    # here, so it cannot start with a hyphen in the first place.
    out = _git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", spec],
        cwd=primary,
        check=False,
    )
    return out.strip() if out and out.strip() else None


# ── Commands ───────────────────────────────────────────────────────────────


def _sinria_home() -> Path:
    value = os.environ.get("SINRIA_HOME", "").strip()
    return _resolve(Path(value)) if value else _resolve(Path.home() / ".sinria")


def guard_sync_state(primary: Path) -> tuple[bool | None, Path, Path]:
    """Whether the installed hook copy of the guard matches the checkout.

    A guard fix in the repository has no effect until the copy the `PreToolUse`
    hook actually executes is refreshed, so drift is worth surfacing. Returns
    ``None`` when there is nothing to compare.
    """
    source = primary / "scripts" / "sinria_primary_checkout_guard.py"
    installed = _sinria_home() / "bin" / "sinria_primary_checkout_guard.py"
    if not source.is_file() or not installed.is_file():
        return None, source, installed
    try:
        return source.read_bytes() == installed.read_bytes(), source, installed
    except OSError:
        return None, source, installed


def _guard_refresh_command(source: Path, installed: Path) -> str:
    """The command that makes a guard fix in the checkout take effect.

    Named, never run: replacing the copy the `PreToolUse` hook executes changes
    how every future session behaves, which is a deploy and therefore a human's
    decision. Reporting drift without the remedy just moves the dead end.
    """
    return f"cp {source} {installed}"


def _session_command(path: Path) -> str:
    return f"cd {path} && claude"


def _bootstrap_command(primary: Path) -> str:
    return f"python3 {primary / 'scripts' / Path(__file__).name} create --name <slug>"


def _emit(payload: dict, lines: list[str], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    primary = resolve_primary(args.primary)
    root = resolve_workspace_root(args.workspace_root, primary)
    worktrees = registered_worktrees(primary)
    blocked = [w["path"] for w in worktrees if w["inside_primary"]]
    external = [w["path"] for w in worktrees if not w["inside_primary"]]
    cwd_is_primary = is_inside(Path.cwd(), primary)
    guard_in_sync, guard_source, guard_installed = guard_sync_state(primary)
    guard_refresh = (
        _guard_refresh_command(guard_source, guard_installed) if guard_in_sync is False else None
    )

    payload = {
        "primary": str(primary),
        "workspace_root": str(root),
        "workspace_root_inside_primary": is_inside(root, primary),
        "cwd_is_primary": cwd_is_primary,
        "external_worktrees": sorted(external),
        "blocked_worktrees": sorted(blocked),
        "guard_in_sync": guard_in_sync,
        "guard_installed_path": str(guard_installed),
        "guard_refresh_command": guard_refresh,
        "healthy": not blocked and guard_in_sync is not False,
        "next_command": _bootstrap_command(primary),
    }

    lines = [
        "Sinria development workspace",
        f"  primary checkout : {primary}  (read-only for development)",
        f"  workspace root   : {root}",
        f"  session cwd      : {'inside the primary checkout' if cwd_is_primary else 'outside the primary checkout'}",
        f"  external worktrees: {len(external)}",
    ]
    if blocked:
        lines.append("")
        lines.append("  ! worktrees registered INSIDE the primary checkout (shell commands are blocked there):")
        for path in sorted(blocked):
            lines.append(f"      {path}")
        lines.append("    Do not develop in these. Check each for unmerged work")
        lines.append("    (git -C <path> status --porcelain) before retiring it.")
    if guard_in_sync is False:
        lines.append("")
        lines.append("  ! the installed guard the PreToolUse hook runs is out of date:")
        lines.append(f"      installed: {guard_installed}")
        lines.append(f"      checkout : {guard_source}")
        lines.append("    Repository fixes to the guard take effect only after it is refreshed.")
        lines.append(f"    Refresh it yourself (this is a deploy): {guard_refresh}")
    lines.append("")
    lines.append("  Create an isolated worktree with:")
    lines.append(f"    {_bootstrap_command(primary)}")
    return _emit(payload, lines, args.json)


def cmd_list(args: argparse.Namespace) -> int:
    primary = resolve_primary(args.primary)
    root = resolve_workspace_root(args.workspace_root, primary)
    worktrees = registered_worktrees(primary)
    payload = {"primary": str(primary), "workspace_root": str(root), "worktrees": worktrees}
    lines = [f"{w['name']}\t{w['branch'] or '(detached)'}\t{w['path']}" for w in worktrees]
    return _emit(payload, lines or ["(no worktrees registered)"], args.json)


def _refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return EXIT_REFUSED


def _create_worktree_locked(
    args: argparse.Namespace,
    primary: Path,
    target: Path,
    branch: str,
) -> int:
    """Recheck registry state and create one worktree while holding its lock."""
    for existing in registered_worktrees(primary):
        if existing["path"] == str(target):
            payload = {
                "path": existing["path"],
                "branch": existing["branch"],
                "created": False,
                "next_command": _session_command(target),
            }
            return _emit(
                payload,
                [f"already registered: {target}", f"  {_session_command(target)}"],
                args.json,
            )

    if target.exists() and any(target.iterdir()):
        return _refuse(
            f"refused: {target} already exists and is not an empty directory. "
            "Pick another --name or clear the path first."
        )

    # `--` ends Git's option parsing: without it a value like `--force` in the
    # <commit-ish> position is consumed as an option, and the worktree is built
    # from HEAD with Git's already-checked-out safeguards silently disabled.
    if _branch_exists(primary, branch):
        add_args = ["worktree", "add", "--", str(target), branch]
    else:
        start_point = _resolve_commit(primary, args.base)
        if start_point is None:
            return _refuse(_BASE_UNRESOLVED_MSG)
        add_args = ["worktree", "add", "-b", branch, "--", str(target), start_point]

    _git(add_args, cwd=primary)
    payload = {
        "path": str(target),
        "branch": branch,
        "created": True,
        "next_command": _session_command(target),
    }
    lines = [
        f"created worktree: {target}",
        f"  branch: {branch}",
        "",
        "  Start the development session there:",
        f"    {_session_command(target)}",
    ]
    return _emit(payload, lines, args.json)


def cmd_create(args: argparse.Namespace) -> int:
    name = args.name
    if not _NAME_RE.match(name or ""):
        return _refuse(
            "refused: --name must be a single path segment matching "
            "[A-Za-z0-9][A-Za-z0-9._-]{0,63} (no slashes, no leading dot or dash)."
        )

    # `--branch=` (explicitly empty) must be refused, not silently defaulted;
    # the derived name is validated too, since a directory name such as
    # `x.lock` is legal for --name but illegal as a ref.
    branch = f"{_BRANCH_NAMESPACE}/{name}" if args.branch is None else args.branch
    if not is_safe_branch(branch):
        return _refuse(_BRANCH_MSG)
    if not is_safe_base(args.base):
        return _refuse(_BASE_MSG)

    primary = resolve_primary(args.primary)
    root = resolve_workspace_root(args.workspace_root, primary)
    target = _resolve(root / name)
    for anchor in (primary, helper_checkout()):
        if is_inside(root, anchor) or is_inside(target, anchor):
            return _refuse(_INSIDE_PRIMARY_MSG)

    repo_import_root = Path(__file__).resolve().parents[1]
    if str(repo_import_root) not in sys.path:
        sys.path.insert(0, str(repo_import_root))
    from sinria_workspace_lock import git_worktree_registry_lock

    root.mkdir(parents=True, exist_ok=True)
    with git_worktree_registry_lock(primary):
        return _create_worktree_locked(args, primary, target, branch)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sinria_worktree_bootstrap.py",
        description="Register isolated Sinria development worktrees outside the primary checkout.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--primary", default=None, help="Path to the Sinria primary checkout.")
        p.add_argument(
            "--workspace-root",
            default=None,
            help="Directory that holds development worktrees (must be outside the primary checkout).",
        )
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    status = sub.add_parser("status", help="Diagnose the development workspace.")
    _common(status)
    status.set_defaults(func=cmd_status)

    listing = sub.add_parser("list", help="List registered worktrees.")
    _common(listing)
    listing.set_defaults(func=cmd_list)

    create = sub.add_parser("create", help="Create an isolated worktree outside the primary checkout.")
    _common(create)
    create.add_argument("--name", required=True, help="Worktree directory name (single path segment).")
    create.add_argument("--branch", default=None, help="Branch to check out (default: sinria/<name>).")
    create.add_argument("--base", default="HEAD", help="Base revision for a new branch (default: HEAD).")
    create.set_defaults(func=cmd_create)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return EXIT_ERROR
        return int(exc.code or EXIT_OK)


if __name__ == "__main__":
    raise SystemExit(main())
