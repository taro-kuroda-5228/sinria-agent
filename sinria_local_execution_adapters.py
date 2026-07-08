"""Local execution adapter registry: sinria_native / claude_code / codex / antigravity.

Sinria stays the Team Mode runtime of record. When a task is better implemented by
another LOCAL developer/agent app, Sinria — AFTER it has claimed the cloud task
under its own member/instance identity — may invoke an APPROVED local adapter as an
execution substrate. Adapters:

  * never claim cloud tasks directly and never become independent cloud actors;
  * keep raw prompts, raw outputs, raw diffs, tokens and private logs LOCAL;
  * surface to the cloud only a sanitized run summary + local artifact refs + the
    no-action safety flags.

Non-native engines are policy-gated by ``SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES``
(comma-separated allowlist). Availability (the app being installed) is NOT consent
to use it — it must also be allowlisted. External writes/deploys/sends stay blocked
unless the originating task policy + human approval permit them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any, Optional

from sinria_agentos_handlers import LocalExecutionIdentity
from sinria_constants import get_sinria_home

__all__ = [
    "LocalExecutionAdapterCapability",
    "list_local_execution_adapters",
    "allowed_execution_engines",
    "adapter_availability",
    "invoke_local_execution_adapter",
    "select_execution_engine",
    # Task 2 helpers (independently testable)
    "_build_adapter_prompt",
    "_build_claude_code_command",
    "_SINRIA_ADAPTER_PREAMBLE",
    # Task 3 helpers (independently testable)
    "_persist_adapter_artifacts",
]

NATIVE_ENGINE = "sinria_native"

# Engine id → (display name, CLI binary to probe, supported task kinds)
_DEVELOPER_ADAPTERS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("claude_code", "Claude Code", "claude", ("implementation", "code_review", "sales_outreach_plan")),
    ("codex", "Codex", "codex", ("implementation", "code_review")),
    ("antigravity", "Antigravity", "antigravity", ("implementation", "code_review")),
)


@dataclass(frozen=True)
class LocalExecutionAdapterCapability:
    engine_id: str
    display_name: str
    member_id: str
    instance_id: str
    available: bool
    supported_task_kinds: tuple[str, ...]
    local_only: bool = True
    human_approval_required_for_external_actions: bool = True
    raw_context_shared_with_adapter: bool = False
    credential_stored_in_cloud: bool = False
    raw_prompt_stored_in_cloud: bool = False
    raw_diff_stored_in_cloud: bool = False


def _installed(binary: str) -> bool:
    return which(binary) is not None


def list_local_execution_adapters(
    member_id: str, instance_id: str
) -> list[LocalExecutionAdapterCapability]:
    adapters = [
        LocalExecutionAdapterCapability(
            engine_id=NATIVE_ENGINE,
            display_name="Sinria native",
            member_id=member_id,
            instance_id=instance_id,
            available=True,
            supported_task_kinds=("*",),
        )
    ]
    for engine_id, display, binary, kinds in _DEVELOPER_ADAPTERS:
        adapters.append(
            LocalExecutionAdapterCapability(
                engine_id=engine_id,
                display_name=display,
                member_id=member_id,
                instance_id=instance_id,
                available=_installed(binary),
                supported_task_kinds=kinds,
            )
        )
    return adapters


def allowed_execution_engines() -> set[str]:
    """Engines the local employee/workspace policy permits. Native is always on."""
    raw = os.environ.get("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "")
    engines = {e.strip() for e in raw.split(",") if e.strip()}
    engines.add(NATIVE_ENGINE)
    return engines


def adapter_availability(member_id: str, instance_id: str) -> dict[str, dict[str, Any]]:
    """Sanitized, cloud-safe availability map (no secrets, no raw anything)."""
    allowed = allowed_execution_engines()
    return {
        a.engine_id: {
            "available": a.available,
            "allowed": a.engine_id in allowed,
            "local_only": a.local_only,
            "supported_task_kinds": list(a.supported_task_kinds),
            "credential_stored_in_cloud": a.credential_stored_in_cloud,
            "raw_prompt_stored_in_cloud": a.raw_prompt_stored_in_cloud,
            "raw_diff_stored_in_cloud": a.raw_diff_stored_in_cloud,
        }
        for a in list_local_execution_adapters(member_id, instance_id)
    }


# Preamble injected at the top of every adapter prompt to enforce Sinria Team Mode
# constraints. This is a module-level constant so it can be independently tested
# and never accidentally omitted.
_SINRIA_ADAPTER_PREAMBLE = (
    "You are Claude Code running as a local execution adapter under Sinria Team Mode.\n"
    "Do not claim cloud tasks. Do not send/deploy/delete/migrate. "
    "Do not expose secrets/PHI/PII.\n"
    "Return a concise implementation summary and local file paths changed."
)

_DEFAULT_MAX_TURNS = 12
_MAX_TURNS_CAP = 50
_DEFAULT_TIMEOUT_SECONDS = 600

# The claude CLI `--output-format json` emits a fixed `subtype` enum. We allowlist
# it (rather than trust the value verbatim) so a future or mocked binary cannot
# push an arbitrary/oversized string into the cloud-visible sanitizedSummary.
_ALLOWED_CLI_SUBTYPES = {"success", "error_max_turns", "error_during_execution", ""}


def _build_adapter_prompt(task: dict[str, Any]) -> str:
    """Build the local-only prompt for the claude -p invocation.

    The raw prompt stays in this local variable only and must never appear
    in the returned dict.  The preamble is always prepended.
    """
    policy = task.get("policy") or {}
    raw_context_allowed = policy.get("adapterRawContextAllowed", False)

    if raw_context_allowed:
        # Raw context is permitted locally but still never returned to cloud.
        task_text = str(task.get("taskText") or task.get("task_text") or "")
        repo_path = str(task.get("repoPath") or task.get("repo_path") or "")
        criteria = str(task.get("acceptanceCriteria") or task.get("acceptance_criteria") or "")
        body_parts = [f"Task: {task_text}"] if task_text else []
        if repo_path:
            body_parts.append(f"Repo path: {repo_path}")
        if criteria:
            body_parts.append(f"Acceptance criteria: {criteria}")
    else:
        # Sanitized-only: include only structural task metadata. acceptanceCriteria
        # is treated as structural (constraints / definition-of-done), NOT raw task
        # description, so it is intentionally permitted here; taskText is excluded.
        task_id = str(task.get("id") or task.get("taskId") or "")
        task_kind = str(task.get("taskKind") or task.get("task_kind") or "")
        repo_path = str(task.get("repoPath") or task.get("repo_path") or "")
        criteria = str(task.get("acceptanceCriteria") or task.get("acceptance_criteria") or "")
        body_parts = []
        if task_id:
            body_parts.append(f"Task ID: {task_id}")
        if task_kind:
            body_parts.append(f"Task kind: {task_kind}")
        if repo_path:
            body_parts.append(f"Repo path: {repo_path}")
        if criteria:
            body_parts.append(f"Acceptance criteria: {criteria}")

    body = "\n".join(body_parts) if body_parts else "(no additional task details)"
    return f"{_SINRIA_ADAPTER_PREAMBLE}\n\n{body}"


def _build_claude_code_command(prompt: str, max_turns: int) -> list[str]:
    """Build the argv list for `claude -p`.

    Returns a list suitable for subprocess.run(...).  Never uses a shell
    string to avoid injection.  --dangerously-skip-permissions is NOT
    included (Team Mode adapter must not bypass safety checks).
    """
    bin_name = os.environ.get("SINRIA_CLAUDE_CODE_BIN", "claude")
    return [
        bin_name,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
    ]


def _resolve_max_turns() -> int:
    """Return a bounded max-turns value from env or default."""
    raw = os.environ.get("SINRIA_CLAUDE_CODE_MAX_TURNS", "")
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return _DEFAULT_MAX_TURNS
    return max(1, min(value, _MAX_TURNS_CAP))


def _resolve_timeout() -> int:
    """Return timeout in seconds from env or default."""
    raw = os.environ.get("SINRIA_CLAUDE_CODE_TIMEOUT_SECONDS", "")
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return _DEFAULT_TIMEOUT_SECONDS


def _local_execution_approved(task: dict[str, Any]) -> bool:
    """Return True only when BOTH the env gate AND the task policy gate are satisfied.

    env gate:  SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED must be "1", "true", or "yes"
               (case-insensitive; surrounding whitespace is ignored).
    policy gate: task["policy"]["localAdapterExecutionApproved"] must be exactly True.

    Both must be set; either alone is not sufficient.
    """
    raw_env = os.environ.get("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "")
    env_ok = raw_env.strip().lower() in {"1", "true", "yes"}
    policy = task.get("policy") or {}
    task_ok = policy.get("localAdapterExecutionApproved") is True
    return env_ok and task_ok


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_ARTIFACT_SIZE_BOUND = 65536  # max chars stored per stdout/stderr artifact (local only)


def _safe_fs_component(value: str, default: str) -> str:
    """Sanitize an arbitrary string for use as a filesystem path component."""
    if not value:
        return default
    sanitized = _SAFE_ID_RE.sub("_", value)
    # Truncate to keep paths reasonable.
    return sanitized[:64] or default


def _persist_adapter_artifacts(
    task_id: str,
    engine_id: str,
    prompt: str,
    stdout: str,
    stderr: str,
    summary_dict: dict[str, Any],
) -> list[str]:
    """Write raw run artifacts to a local-only directory; return local:// refs.

    Files written (none appear in the returned cloud-visible result):
      prompt.txt   — raw prompt (local only)
      stdout.txt   — raw stdout, or stdout.json if it parses as JSON (local only)
      stderr.txt   — raw stderr, bounded (local only)
      summary.json — sanitized summary dict (same content as the cloud return)

    Returns a list of ``local://`` refs pointing to summary.json (and the run
    dir itself for convenience).  Returns ``[]`` on any filesystem error so
    the caller can degrade gracefully without crashing.
    """
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        safe_task = _safe_fs_component(task_id, "unknown-task")
        safe_engine = _safe_fs_component(engine_id, "unknown-engine")
        run_dir_rel = f"local-adapter-runs/{date_str}/{safe_task}-{safe_engine}"
        run_dir: Path = Path(get_sinria_home()) / run_dir_rel
        run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        # stdout — store as .json if it parses cleanly, else .txt
        stdout_bounded = stdout[:_ARTIFACT_SIZE_BOUND] if stdout else ""
        try:
            json.loads(stdout_bounded)
            (run_dir / "stdout.json").write_text(stdout_bounded, encoding="utf-8")
        except (json.JSONDecodeError, ValueError):
            (run_dir / "stdout.txt").write_text(stdout_bounded, encoding="utf-8")

        stderr_bounded = (stderr or "")[:_ARTIFACT_SIZE_BOUND]
        (run_dir / "stderr.txt").write_text(stderr_bounded, encoding="utf-8")

        (run_dir / "summary.json").write_text(
            json.dumps(summary_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        summary_ref = f"local://{run_dir_rel}/summary.json"
        return [summary_ref]
    except Exception:  # noqa: BLE001
        # Disk errors must never crash the adapter call or leak raw content.
        return []


def _recoverable(reason: str) -> dict[str, Any]:
    return {
        "status": "failed_recoverable",
        "sanitizedSummary": reason,
        "externalActionPerformed": False,
        "rawPromptStoredInCloud": False,
        "rawOutputStoredInCloud": False,
        "rawDiffStoredInCloud": False,
        "credentialStoredInCloud": False,
        "localArtifactRefs": [],
    }


def invoke_local_execution_adapter(
    *,
    engine_id: str,
    task: dict[str, Any],
    identity: "LocalExecutionIdentity",
    dry_run: bool = False,
    working_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Policy-gated adapter invocation.

    Returns ``failed_recoverable`` when the engine is not allowlisted, not
    installed, or cannot handle the task kind. ``dry_run=True`` reports a plan
    WITHOUT starting any external app. Never prints tokens / raw prompts / raw
    diffs / private logs into the (cloud-visible) return value.
    """
    allowed = allowed_execution_engines()
    if engine_id not in allowed:
        return _recoverable(
            f"engine {engine_id} is not allowlisted (set SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES)"
        )

    caps = {a.engine_id: a for a in list_local_execution_adapters(identity.member_id, identity.instance_id)}
    cap = caps.get(engine_id)
    if cap is None:
        return _recoverable(f"unknown execution engine {engine_id}")
    if not cap.available:
        return _recoverable(f"engine {engine_id} is not installed on this instance")

    task_kind = str(task.get("taskKind") or task.get("task_kind") or "")
    if (
        engine_id != NATIVE_ENGINE
        and "*" not in cap.supported_task_kinds
        and task_kind not in cap.supported_task_kinds
    ):
        return _recoverable(f"engine {engine_id} does not support task kind {task_kind!r}")

    base = {
        "engineId": engine_id,
        "externalActionPerformed": False,
        "rawPromptStoredInCloud": False,
        "rawOutputStoredInCloud": False,
        "rawDiffStoredInCloud": False,
        "credentialStoredInCloud": False,
        "localArtifactRefs": [],
    }
    if dry_run:
        scope = working_dir or "<scoped repo path>"
        return {
            **base,
            "status": "planned",
            "sanitizedCommandSummary": (
                f"{cap.display_name}: would execute {task_kind or 'task'} in {scope} "
                "(no external send/deploy)"
            ),
        }

    # sinria_native is the Team Mode runtime of record (always allowed per
    # allowed_execution_engines) and is dispatched through Sinria's own native
    # path, not this developer-adapter. It is exempt from the local-adapter
    # approval gate, and must never fall through to the developer-adapter
    # subprocess path added in later tasks. If it ever reaches here, take no action.
    if engine_id == NATIVE_ENGINE:
        return {
            **base,
            "status": "waiting_review",
            "sanitizedCommandSummary": (
                "sinria_native executes via the native dispatch path, not the local execution adapter"
            ),
        }

    # Real invocation of external local apps (claude/codex/antigravity) is gated
    # behind a two-factor approval check: env var + task policy must BOTH be set.
    if not _local_execution_approved(task):
        return {
            **base,
            "status": "waiting_review",
            "sanitizedCommandSummary": (
                f"{cap.display_name}: local execution requires SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED "
                "env var and task policy localAdapterExecutionApproved=True"
            ),
        }

    # Both gates passed: build prompt and argv, invoke subprocess.
    # Raw prompt and raw stdout/stderr remain LOCAL variables only — they MUST NOT
    # appear in the returned dict (cloud-visible).
    max_turns = _resolve_max_turns()
    timeout_secs = _resolve_timeout()

    _raw_prompt = _build_adapter_prompt(task)
    cmd = _build_claude_code_command(_raw_prompt, max_turns)

    cwd_arg = working_dir or None
    task_id = str(task.get("id") or task.get("taskId") or task.get("task_id") or "")
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            cwd=cwd_arg,
        )
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired carries .stdout/.stderr (partial raw output) on the live
        # exception object. Never let it escape to a caller/logger that could surface
        # that raw content to the cloud — collapse to a sanitized failed_recoverable.
        sanitized_reason = f"{cap.display_name}: timed out after {timeout_secs}s"
        _timeout_summary = {
            "status": "failed_recoverable",
            "sanitizedSummary": sanitized_reason,
            "externalActionPerformed": False,
            "rawPromptStoredInCloud": False,
            "rawOutputStoredInCloud": False,
            "rawDiffStoredInCloud": False,
            "credentialStoredInCloud": False,
        }
        # Persist partial output locally (raw output stays local, NOT in cloud return).
        _partial_stdout = ""
        _partial_stderr = ""
        if exc.output is not None:
            raw = exc.output
            _partial_stdout = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if exc.stderr is not None:
            raw = exc.stderr
            _partial_stderr = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        local_refs = _persist_adapter_artifacts(
            task_id, engine_id, _raw_prompt, _partial_stdout, _partial_stderr, _timeout_summary
        )
        return {**base, **_timeout_summary, "localArtifactRefs": local_refs}
    except OSError as exc:
        # e.g. the binary vanished after the availability check, or exec failed.
        # Surface only the exception type name — never argv, prompt, or raw context.
        sanitized_reason = f"{cap.display_name}: failed to start ({type(exc).__name__})"
        _oserr_summary = {
            "status": "failed_recoverable",
            "sanitizedSummary": sanitized_reason,
            "externalActionPerformed": False,
            "rawPromptStoredInCloud": False,
            "rawOutputStoredInCloud": False,
            "rawDiffStoredInCloud": False,
            "credentialStoredInCloud": False,
        }
        local_refs = _persist_adapter_artifacts(
            task_id, engine_id, _raw_prompt, "", "", _oserr_summary
        )
        return {**base, **_oserr_summary, "localArtifactRefs": local_refs}

    # Parse stdout to extract a sanitized summary; discard the rest locally.
    _raw_stdout = completed.stdout
    _raw_stderr = completed.stderr
    sanitized_summary: str
    if completed.returncode != 0:
        sanitized_summary = (
            f"{cap.display_name}: process exited with code {completed.returncode}"
        )
        _nonzero_summary = {
            "status": "failed_recoverable",
            "sanitizedSummary": sanitized_summary,
            "externalActionPerformed": False,
            "rawPromptStoredInCloud": False,
            "rawOutputStoredInCloud": False,
            "rawDiffStoredInCloud": False,
            "credentialStoredInCloud": False,
        }
        local_refs = _persist_adapter_artifacts(
            task_id, engine_id, _raw_prompt, _raw_stdout, _raw_stderr, _nonzero_summary
        )
        return {**base, **_nonzero_summary, "localArtifactRefs": local_refs}

    try:
        parsed = json.loads(_raw_stdout)
        # Surface only structural metadata (turn count, subtype) — never raw text
        # from result/output/summary fields, which may contain sensitive content.
        num_turns = parsed.get("num_turns")
        subtype = parsed.get("subtype") or ""
        if subtype not in _ALLOWED_CLI_SUBTYPES:
            # Defense in depth: only known structural enum values may cross to cloud.
            subtype = "unknown"
        meta_parts = []
        # Only surface an INTEGER turn count. A non-int num_turns (e.g. from a
        # compromised or mocked binary returning {"num_turns": "<raw text>"}) is
        # dropped so it can never carry raw content into the cloud-visible summary —
        # mirroring the subtype allowlist above. bool is an int subclass, exclude it.
        if isinstance(num_turns, int) and not isinstance(num_turns, bool):
            meta_parts.append(f"turns={num_turns}")
        if subtype:
            meta_parts.append(f"subtype={subtype}")
        meta_str = ", ".join(meta_parts)
        sanitized_summary = (
            f"{cap.display_name}: completed ({task.get('taskKind') or task.get('task_kind') or 'task'})"
            + (f" ({meta_str})" if meta_str else "")
        )
    except (json.JSONDecodeError, AttributeError):
        sanitized_summary = (
            f"{cap.display_name}: completed ({task.get('taskKind') or task.get('task_kind') or 'task'}) "
            "(output was not JSON; stored locally only)"
        )

    _success_summary = {
        "status": "completed",
        "sanitizedSummary": sanitized_summary,
        "externalActionPerformed": False,
        "rawPromptStoredInCloud": False,
        "rawOutputStoredInCloud": False,
        "rawDiffStoredInCloud": False,
        "credentialStoredInCloud": False,
    }
    local_refs = _persist_adapter_artifacts(
        task_id, engine_id, _raw_prompt, _raw_stdout, _raw_stderr, _success_summary
    )
    return {**base, **_success_summary, "localArtifactRefs": local_refs}


def select_execution_engine(task: dict[str, Any], identity: "LocalExecutionIdentity") -> str:
    """Pick the preferred engine when installed AND policy/env allow it, else native."""
    policy = task.get("policy") or {}
    preferred = (
        policy.get("preferredExecutionEngine")
        or task.get("preferredExecutionEngine")
        or NATIVE_ENGINE
    )
    allowed_by_policy = set(policy.get("allowedExecutionEngines") or [NATIVE_ENGINE])
    allowed_by_env = allowed_execution_engines()
    caps = {a.engine_id: a for a in list_local_execution_adapters(identity.member_id, identity.instance_id)}
    cap = caps.get(preferred)
    if (
        preferred in allowed_by_policy
        and preferred in allowed_by_env
        and cap is not None
        and cap.available
    ):
        return preferred
    return NATIVE_ENGINE
