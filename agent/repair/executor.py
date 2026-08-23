"""Real side effects for the Repair Orchestrator (Phase 2).

Everything that touches the world lives here — isolated git worktrees, verify
commands, the claude_code local-execution adapter, and PR creation — behind a
single class the orchestrator receives by injection, so the state machine is
fully testable with a fake.

Confidentiality: raw command output stays in local variables; only exit codes
and bounded, redacted tails cross this boundary. Adapter invocations go
through ``invoke_local_execution_adapter`` unchanged, inheriting its two-factor
approval gate (env allowlist + env approval) and its raw-content-never-to-cloud
contract. The task's ``localAdapterExecutionApproved`` policy flag is set by
this executor because reaching it already requires ``repair.enabled`` — Taro's
standing approval for repair-scoped local execution.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from hermes_constants import get_sinria_home

from .maintenance import measure_ticket_metric
from .storage import PrivateStorageUnsupportedError, ensure_private_dir

_TAIL_MAX_CHARS = 400

# pytest short-summary lines: "FAILED tests/x.py::test_y - reason" /
# "ERROR tests/x.py - reason". The id is the first whitespace-delimited token.
_PYTEST_FAILURE_RE = re.compile(r"^(?:FAILED|ERROR) +(\S+)", re.MULTILINE)


class RepairExecutionError(RuntimeError):
    """Raised on unrecoverable executor failures. Message must stay sanitized."""


def worktrees_dir(home: Path | None = None) -> Path:
    return (home or get_sinria_home()) / "repair" / "worktrees"


class RepairExecutor:
    """Injectable side-effect boundary for the repair state machine."""

    def __init__(self, *, home: Path | None = None):
        self._home = home

    # ── low-level process runner (single choke point, easy to fake) ────

    def _run(self, argv: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd) if cwd is not None else None,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return completed.returncode, completed.stdout or "", completed.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        except OSError as exc:
            return 127, "", type(exc).__name__

    # ── commands ────────────────────────────────────────────────────────

    def run_command(self, command: str, cwd: Path, timeout: int = 3600) -> tuple[int, str]:
        """Run one contract command (no shell). Returns (exit_code, redacted tail)."""
        try:
            argv = shlex.split(command)
        except ValueError:
            return 2, "unparseable command"
        if not argv:
            return 2, "empty command"
        code, stdout, stderr = self._run(argv, cwd=cwd, timeout=timeout)
        tail = (stdout + "\n" + stderr)[-_TAIL_MAX_CHARS:]
        try:
            from agent.redact import redact_sensitive_text

            tail = redact_sensitive_text(tail, force=True)
        except Exception:
            tail = ""
        return code, tail

    def run_verify_command(
        self, command: str, cwd: Path, timeout: int = 3600
    ) -> tuple[int, frozenset, str]:
        """Run one verify command and parse the failing test ids.

        The repo-wide suite can be red for reasons unrelated to a repair
        (measured: 82 pre-existing failures on origin/main, 2026-07-12), so
        the orchestrator gates on failure SETS — pre-existing failures are
        tolerated, new ones reject. Raw output stays in local variables; only
        exit code, test ids (repo-relative code identifiers), and a redacted
        tail cross this boundary.
        """
        try:
            argv = shlex.split(command)
        except ValueError:
            return 2, frozenset(), "unparseable command"
        if not argv:
            return 2, frozenset(), "empty command"
        code, stdout, stderr = self._run(argv, cwd=cwd, timeout=timeout)
        combined = stdout + "\n" + stderr
        failures = frozenset(_PYTEST_FAILURE_RE.findall(combined))
        tail = combined[-_TAIL_MAX_CHARS:]
        try:
            from agent.redact import redact_sensitive_text

            tail = redact_sensitive_text(tail, force=True)
        except Exception:
            tail = ""
        return code, failures, tail

    # ── git worktree lifecycle ──────────────────────────────────────────

    def _base_ref(self, repo_root: Path) -> str:
        for candidate in ("origin/main", "origin/master"):
            code, _out, _err = self._run(
                ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", candidate]
            )
            if code == 0:
                return candidate
        return "HEAD"

    def prepare_worktree(self, repo_root: Path, branch: str) -> Path:
        directory = worktrees_dir(self._home) / branch.replace("/", "-")
        if directory.exists():
            raise RepairExecutionError(f"worktree directory already exists for branch {branch}")
        repair_root = directory.parent.parent
        try:
            ensure_private_dir(directory.parent, root=repair_root)
        except PrivateStorageUnsupportedError as exc:
            raise RepairExecutionError("private repair storage is unsupported on this platform") from exc
        base = self._base_ref(repo_root)
        code, _out, _err = self._run(
            ["git", "-C", str(repo_root), "worktree", "add", "-b", branch, str(directory), base]
        )
        if code != 0:
            raise RepairExecutionError(f"git worktree add failed for branch {branch} (exit {code})")
        ensure_private_dir(directory, root=repair_root)
        # Worktrees don't carry the checkout's virtualenv; verify commands
        # (e.g. scripts/run_tests.sh) probe <root>/.venv, so link it through.
        venv_source = Path(repo_root) / ".venv"
        venv_target = directory / ".venv"
        if venv_source.is_dir() and not venv_target.exists():
            try:
                venv_target.symlink_to(venv_source)
            except OSError:
                pass
        return directory

    def remove_worktree(self, repo_root: Path, worktree: Path) -> None:
        self._run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree)])

    def rev_parse(self, worktree: Path, ref: str = "HEAD") -> str:
        code, out, _err = self._run(["git", "-C", str(worktree), "rev-parse", ref])
        if code != 0:
            raise RepairExecutionError(f"git rev-parse {ref} failed (exit {code})")
        return out.strip()

    def commit_all(self, worktree: Path, message: str) -> bool:
        """Stage and commit everything; False when there was nothing to commit."""
        self._run(["git", "-C", str(worktree), "add", "-A"])
        code, _out, _err = self._run(["git", "-C", str(worktree), "commit", "-m", message])
        return code == 0

    def diff_stats(self, worktree: Path, base_ref: str) -> tuple[list[str], int]:
        """Changed files and total changed lines between base_ref and HEAD.

        ``--no-renames`` is load-bearing: default rename detection collapses a
        "move + edit" into one combined ``old => new`` path, which would slip
        past the exact-prefix protected-path gates in ``evaluate_patch_diff``.
        A rename must surface as an explicit delete + add so the source path
        stays visible to the guards.
        """
        code, out, _err = self._run(
            ["git", "-C", str(worktree), "diff", "--no-renames", "--numstat", f"{base_ref}..HEAD"]
        )
        if code != 0:
            raise RepairExecutionError(f"git diff --numstat failed (exit {code})")
        files: list[str] = []
        total = 0
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, deleted, path = parts
            files.append(path.strip())
            for count in (added, deleted):
                if count.strip().isdigit():
                    total += int(count)
        return files, total

    def measure_ticket_metric(self, ticket, worktree: Path) -> float:
        """Re-measure an objective metadata-only maintenance signal."""
        return measure_ticket_metric(worktree, ticket)

    # ── adapter delegation ──────────────────────────────────────────────

    def invoke_adapter(
        self,
        *,
        ticket,
        phase: str,
        instructions: str,
        worktree: Path,
        engine: str,
    ) -> dict:
        """Delegate work to Sinria natively or an explicitly approved adapter."""
        if engine == "sinria_native":
            from .native_executor import NativeRepairError, NativeRepairExecutor

            prompt = "\n".join(
                [
                    f"Repair ticket: {ticket.ticket_id}",
                    f"Phase: {phase}",
                    f"Repository: {ticket.repo}",
                    f"Error class: {ticket.exc_class}",
                    f"Code location: {ticket.code_location}",
                    instructions,
                ]
            )
            try:
                return NativeRepairExecutor(
                    output_dir=(self._home or get_sinria_home()) / "repair" / "native-runs"
                ).run(worktree, prompt)
            except NativeRepairError as exc:
                return {"status": "failed", "reason": str(exc)}

        from sinria_agentos_handlers import LocalExecutionIdentity
        import sinria_local_execution_adapters as adapters

        identity = LocalExecutionIdentity(
            workspace_id="sinria-local",
            member_id="sinria-repair-orchestrator",
            instance_id="local",
        )
        task = {
            "id": f"{ticket.ticket_id}-{phase}",
            "taskKind": "implementation",
            "repoPath": str(worktree),
            "acceptanceCriteria": instructions,
            "policy": {
                "localAdapterExecutionApproved": True,
                "adapterRawContextAllowed": False,
            },
        }
        return adapters.invoke_local_execution_adapter(
            engine_id=engine,
            task=task,
            identity=identity,
            working_dir=str(worktree),
        )

    # ── PR creation ─────────────────────────────────────────────────────

    def open_pr(self, worktree: Path, branch: str, title: str, body: str) -> str:
        code, _out, _err = self._run(
            ["git", "-C", str(worktree), "push", "-u", "origin", branch], timeout=300
        )
        if code != 0:
            raise RepairExecutionError(f"git push failed for branch {branch} (exit {code})")
        code, out, _err = self._run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch],
            cwd=worktree,
            timeout=300,
        )
        if code != 0:
            raise RepairExecutionError(f"gh pr create failed for branch {branch} (exit {code})")
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        return lines[-1] if lines else ""
