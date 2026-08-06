from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_registry_lock_path_is_shared_by_linked_worktrees(tmp_path: Path) -> None:
    from sinria_workspace_lock import git_worktree_registry_lock_path

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    primary.mkdir()
    _git(primary, "init", "-b", "main")
    _git(primary, "config", "user.email", "sinria-test@example.invalid")
    _git(primary, "config", "user.name", "Sinria Test")
    (primary / "README.md").write_text("test\n", encoding="utf-8")
    _git(primary, "add", "README.md")
    _git(primary, "commit", "-m", "initial")
    _git(primary, "worktree", "add", "-b", "linked", str(linked), "HEAD")

    primary_lock = git_worktree_registry_lock_path(primary)
    linked_lock = git_worktree_registry_lock_path(linked)

    assert primary_lock == linked_lock
    assert primary_lock.parent == (primary / ".git").resolve()
    assert primary_lock.name == "sinria-worktree-registry.lock"


def _function_calls_name(path: Path, function_name: str, called_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return any(
        isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name)
            and node.func.id == called_name
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == called_name
        )
        for node in ast.walk(function)
    )


def test_all_worktree_mutation_entrypoints_use_the_shared_registry_lock() -> None:
    assert _function_calls_name(
        ROOT / "cli.py", "_worktree_registry_lock", "git_worktree_registry_lock"
    )
    assert _function_calls_name(
        ROOT / "gateway" / "workspace_lease.py",
        "_filesystem_lock",
        "git_worktree_registry_lock",
    )
    assert _function_calls_name(
        ROOT / "scripts" / "sinria_worktree_bootstrap.py",
        "cmd_create",
        "git_worktree_registry_lock",
    )


def test_concurrent_bootstrap_for_same_name_reuses_one_locked_lease(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    workspace_root = tmp_path / "worktrees"
    primary.mkdir()
    _git(primary, "init", "-b", "main")
    _git(primary, "config", "user.email", "sinria-test@example.invalid")
    _git(primary, "config", "user.name", "Sinria Test")
    (primary / "README.md").write_text("test\n", encoding="utf-8")
    _git(primary, "add", "README.md")
    _git(primary, "commit", "-m", "initial")

    command = [
        sys.executable,
        str(ROOT / "scripts" / "sinria_worktree_bootstrap.py"),
        "create",
        "--primary",
        str(primary),
        "--workspace-root",
        str(workspace_root),
        "--name",
        "same-session",
        "--json",
    ]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    payloads = [json.loads(stdout) for stdout, _stderr in results]
    assert sorted(payload["created"] for payload in payloads) == [False, True]
    assert len({payload["path"] for payload in payloads}) == 1
    assert _git(primary, "status", "--porcelain") == ""
