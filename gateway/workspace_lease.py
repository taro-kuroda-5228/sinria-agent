"""Persistent, privacy-preserving Git worktree leases for gateway sessions.

A lease is deterministic for a gateway session key, but only a SHA-256 digest is
used in filesystem paths and branch names.  Leases are intentionally persistent:
a crashed or restarted gateway must never delete uncommitted agent work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import threading

from sinria_workspace_lock import git_worktree_registry_lock


class WorkspaceLeaseError(RuntimeError):
    """Raised when an isolated workspace cannot be acquired safely."""


@dataclass(frozen=True)
class WorkspaceLease:
    path: Path
    branch: str
    digest: str
    is_isolated: bool


class GitWorkspaceLeaseManager:
    """Create and reuse one persistent Git worktree per gateway session."""

    def __init__(
        self,
        repo_root: str | Path,
        leases_root: str | Path,
        base_ref: str = "HEAD",
        enabled: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.leases_root = Path(leases_root).expanduser().resolve()
        self.base_ref = str(base_ref or "HEAD")
        self.enabled = bool(enabled)
        self._thread_lock = threading.Lock()

    @staticmethod
    def session_digest(session_key: str) -> str:
        if not session_key:
            raise WorkspaceLeaseError("session key is required for workspace isolation")
        return hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:20]

    def acquire(self, session_key: str) -> WorkspaceLease:
        """Return the stable lease for *session_key* or fail closed."""
        if not self.enabled:
            return WorkspaceLease(
                path=self.repo_root,
                branch="",
                digest="",
                is_isolated=False,
            )

        digest = self.session_digest(session_key)
        branch = f"sinria/session-{digest}"
        path = self.leases_root / f"session-{digest}"

        self.leases_root.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self._filesystem_lock():
            self._validate_repo()
            return self._acquire_locked(path=path, branch=branch, digest=digest)

    def _validate_repo(self) -> None:
        try:
            actual = Path(self._git("rev-parse", "--show-toplevel")).resolve()
        except WorkspaceLeaseError as exc:
            raise WorkspaceLeaseError("configured repo root is not a Git worktree") from exc
        if actual != self.repo_root:
            raise WorkspaceLeaseError("configured repo root does not match its Git top level")

    def _base_pin_path(self) -> Path:
        """Return a pin path scoped to this repository and exact base ref."""
        identity = f"{self.repo_root}\0{self.base_ref}".encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()[:20]
        return self.leases_root / f".base-{digest}.commit"

    @staticmethod
    def _looks_like_object_id(value: str) -> bool:
        return len(value) in {40, 64} and all(
            char in "0123456789abcdef" for char in value
        )

    def _persist_base_commit(self, commit: str) -> None:
        pin_path = self._base_pin_path()
        temporary = pin_path.with_name(
            f"{pin_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(f"{commit}\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, pin_path)
        except OSError as exc:
            raise WorkspaceLeaseError(
                "failed to persist workspace base commit"
            ) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _load_pinned_base_commit(self) -> str | None:
        pin_path = self._base_pin_path()
        try:
            commit = pin_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise WorkspaceLeaseError(
                "failed to read persisted workspace base commit"
            ) from exc

        if not self._looks_like_object_id(commit):
            raise WorkspaceLeaseError("persisted workspace base commit is invalid")
        try:
            return self._git("rev-parse", "--verify", f"{commit}^{{commit}}")
        except WorkspaceLeaseError as exc:
            raise WorkspaceLeaseError(
                "persisted workspace base commit is unavailable"
            ) from exc

    def _resolve_base_commit(self) -> str:
        """Resolve the configured ref, falling back only to its exact persisted pin."""
        try:
            commit = self._git("rev-parse", "--verify", f"{self.base_ref}^{{commit}}")
        except WorkspaceLeaseError as ref_error:
            pinned = self._load_pinned_base_commit()
            if pinned is not None:
                return pinned
            raise WorkspaceLeaseError(
                f"configured base ref is unavailable: {self.base_ref}"
            ) from ref_error

        self._persist_base_commit(commit)
        return commit

    def _acquire_locked(self, *, path: Path, branch: str, digest: str) -> WorkspaceLease:
        worktrees = self._registered_worktrees()
        registered = worktrees.get(path.resolve())

        if path.exists():
            if registered is None:
                raise WorkspaceLeaseError(
                    f"lease path exists but is not a registered Git worktree: {path}"
                )
            if registered != branch:
                raise WorkspaceLeaseError(
                    f"lease path is registered to an unexpected branch: {path}"
                )
            return WorkspaceLease(path.resolve(), branch, digest, True)

        if registered is not None:
            raise WorkspaceLeaseError(
                f"Git worktree registry points to a missing lease path: {path}"
            )

        branch_ref = f"refs/heads/{branch}"
        branch_exists = self._git_ok("show-ref", "--verify", "--quiet", branch_ref)
        base_commit: str | None = None
        if not branch_exists:
            # Existing leases and branches are self-contained. A deleted
            # deployment base must not interrupt them. New branches use an
            # immutable commit, with a persisted pin surviving ref deletion
            # and gateway restarts without ever using the primary checkout.
            base_commit = self._resolve_base_commit()
        try:
            if branch_exists:
                # A stable branch may remain after an operator deliberately
                # removed only the worktree. Reattach it without rewriting it.
                self._git("worktree", "add", str(path), branch)
            else:
                # Defensive invariant: fail closed even under optimized runtimes.
                if base_commit is None:
                    raise WorkspaceLeaseError(
                        "resolved workspace base commit is missing"
                    )
                self._git("worktree", "add", "-b", branch, str(path), base_commit)
        except WorkspaceLeaseError as exc:
            raise WorkspaceLeaseError(
                f"failed to create isolated workspace lease at {path}"
            ) from exc

        refreshed = self._registered_worktrees()
        if refreshed.get(path.resolve()) != branch:
            raise WorkspaceLeaseError("created workspace failed Git registry verification")
        if path.resolve() == self.repo_root:
            raise WorkspaceLeaseError("workspace isolation resolved to the primary checkout")
        return WorkspaceLease(path.resolve(), branch, digest, True)

    def _registered_worktrees(self) -> dict[Path, str]:
        output = self._git("worktree", "list", "--porcelain")
        result: dict[Path, str] = {}
        current_path: Path | None = None
        current_branch = ""
        for line in [*output.splitlines(), ""]:
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree ")).resolve()
                current_branch = ""
            elif line.startswith("branch "):
                current_branch = line.removeprefix("branch ").removeprefix("refs/heads/")
            elif not line and current_path is not None:
                result[current_path] = current_branch
                current_path = None
                current_branch = ""
        return result

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "git command failed").strip()
            raise WorkspaceLeaseError(detail)
        return proc.stdout.strip()

    def _git_ok(self, *args: str) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return proc.returncode == 0

    def _filesystem_lock(self):
        """Serialize Git registry mutations with CLI and bootstrap processes."""
        return git_worktree_registry_lock(self.repo_root)


def workspace_manager_from_config(
    config: dict,
    *,
    default_repo_root: str | Path,
    sinria_home: str | Path,
) -> GitWorkspaceLeaseManager:
    """Build a lease manager from the Sinria-native config block."""
    raw = config.get("workspace_isolation") or {}
    if not isinstance(raw, dict):
        raise WorkspaceLeaseError("workspace_isolation config must be a mapping")

    enabled_raw = raw.get("enabled", False)
    if isinstance(enabled_raw, str):
        enabled = enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled = bool(enabled_raw)

    repo_root = raw.get("repo_root") or default_repo_root
    leases_root = raw.get("root") or (Path(sinria_home) / "worktrees" / "gateway-sessions")
    base_ref = raw.get("base_ref") or "HEAD"
    return GitWorkspaceLeaseManager(
        repo_root=repo_root,
        leases_root=leases_root,
        base_ref=str(base_ref),
        enabled=enabled,
    )
