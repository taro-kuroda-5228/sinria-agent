"""Security-focused integration tests for CLI worktree setup."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo for testing real cli._setup_worktree behavior."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


def _force_remove_worktree(info: dict | None) -> None:
    if not info:
        return
    subprocess.run(
        ["git", "worktree", "remove", info["path"], "--force"],
        cwd=info["repo_root"],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "branch", "-D", info["branch"]],
        cwd=info["repo_root"],
        capture_output=True,
        check=False,
    )


class TestWorktreeIncludeSecurity:
    def test_rejects_parent_directory_file_traversal(self, git_repo):
        import cli as cli_mod

        outside_file = git_repo.parent / "sensitive.txt"
        outside_file.write_text("SENSITIVE DATA")
        (git_repo / ".worktreeinclude").write_text("../sensitive.txt\n")

        info = None
        try:
            info = cli_mod._setup_worktree(str(git_repo))
            assert info is not None

            wt_path = Path(info["path"])
            assert not (wt_path.parent / "sensitive.txt").exists()
            assert not (wt_path / "../sensitive.txt").resolve().exists()
        finally:
            _force_remove_worktree(info)

    def test_rejects_parent_directory_directory_traversal(self, git_repo):
        import cli as cli_mod

        outside_dir = git_repo.parent / "outside-dir"
        outside_dir.mkdir()
        (outside_dir / "secret.txt").write_text("SENSITIVE DIR DATA")
        (git_repo / ".worktreeinclude").write_text("../outside-dir\n")

        info = None
        try:
            info = cli_mod._setup_worktree(str(git_repo))
            assert info is not None

            wt_path = Path(info["path"])
            escaped_dir = wt_path.parent / "outside-dir"
            assert not escaped_dir.exists()
            assert not escaped_dir.is_symlink()
        finally:
            _force_remove_worktree(info)

    def test_rejects_symlink_that_resolves_outside_repo(self, git_repo):
        import cli as cli_mod

        outside_file = git_repo.parent / "linked-secret.txt"
        outside_file.write_text("LINKED SECRET")
        (git_repo / "leak.txt").symlink_to(outside_file)
        (git_repo / ".worktreeinclude").write_text("leak.txt\n")

        info = None
        try:
            info = cli_mod._setup_worktree(str(git_repo))
            assert info is not None

            assert not (Path(info["path"]) / "leak.txt").exists()
        finally:
            _force_remove_worktree(info)

    def test_allows_valid_file_include(self, git_repo):
        import cli as cli_mod

        (git_repo / ".gitignore").write_text(".env\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "ignore local env"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        (git_repo / ".env").write_text("SECRET=[REDACTED]\n")
        (git_repo / ".worktreeinclude").write_text(".env\n")

        info = None
        try:
            info = cli_mod._setup_worktree(str(git_repo))
            assert info is not None

            copied = Path(info["path"]) / ".env"
            assert copied.exists()
            got = copied.read_text()
            want = (git_repo / ".env").read_text()
            assert got == want, f"got={got!r} want={want!r}"
        finally:
            _force_remove_worktree(info)

    def test_rejects_unignored_include_and_rolls_back_worktree(self, git_repo):
        import cli as cli_mod

        (git_repo / "local.txt").write_text("local state\n")
        (git_repo / ".worktreeinclude").write_text("local.txt\n")

        info = cli_mod._setup_worktree(str(git_repo))

        assert info is None
        worktrees = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert worktrees.count("worktree ") == 1
        branches = subprocess.run(
            ["git", "branch", "--list", "sinria/sinria-*"],
            cwd=git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert branches == ""

    def test_allows_valid_directory_include(self, git_repo):
        import cli as cli_mod

        (git_repo / ".gitignore").write_text(".venv\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "ignore local venv"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
        source_dir = git_repo / ".venv" / "lib"
        source_dir.mkdir(parents=True)
        (source_dir / "marker.txt").write_text("venv marker")
        (git_repo / ".worktreeinclude").write_text(".venv/\n")

        info = None
        try:
            info = cli_mod._setup_worktree(str(git_repo))
            assert info is not None

            linked_dir = Path(info["path"]) / ".venv"
            assert linked_dir.is_symlink()
            assert (linked_dir / "lib" / "marker.txt").read_text() == "venv marker"
        finally:
            _force_remove_worktree(info)
