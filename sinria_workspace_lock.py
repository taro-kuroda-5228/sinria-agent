"""Cross-process locking for mutations of a Git worktree registry.

Every linked worktree of one repository shares the same Git common directory.
Anchoring the lock there lets CLI, Gateway, and bootstrap operations serialize
registry mutations even when they keep their worktrees under different roots.
This module intentionally supports Python 3.9 because the bootstrap escape hatch
runs with the system ``python3`` on macOS.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import threading
from typing import Iterator, Union


_REGISTRY_THREAD_LOCK = threading.Lock()
_LOCK_FILENAME = "sinria-worktree-registry.lock"


def git_worktree_common_dir(repo_root: Union[str, Path]) -> Path:
    """Return the Git metadata directory shared by all linked worktrees."""
    repo = Path(repo_root).expanduser().resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout or "not a Git repository").strip()
        raise ValueError("Could not resolve Git common directory: %s" % detail)
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = repo / common
    return common.resolve()


def git_worktree_registry_lock_path(repo_root: Union[str, Path]) -> Path:
    """Return the one lock path shared by every worktree mutation entrypoint."""
    return git_worktree_common_dir(repo_root) / _LOCK_FILENAME


@contextmanager
def git_worktree_registry_lock(repo_root: Union[str, Path]) -> Iterator[None]:
    """Serialize worktree registry mutations across threads and processes."""
    lock_path = git_worktree_registry_lock_path(repo_root)
    with _REGISTRY_THREAD_LOCK:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        windows_lock = os.name == "nt"
        try:
            if windows_lock:  # pragma: no cover - exercised on Windows CI
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if windows_lock:  # pragma: no cover - exercised on Windows CI
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
