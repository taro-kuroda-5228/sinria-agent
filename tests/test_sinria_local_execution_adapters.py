"""Tests for the local execution adapter registry (Task 10A / 10B / Task 2 / Task 3)."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sinria_agentos_handlers import LocalExecutionIdentity  # noqa: E402
import sinria_local_execution_adapters  # noqa: E402
from sinria_local_execution_adapters import (  # noqa: E402
    _SINRIA_ADAPTER_PREAMBLE,
    _build_adapter_prompt,
    _build_claude_code_command,
    adapter_availability,
    invoke_local_execution_adapter,
    list_local_execution_adapters,
    select_execution_engine,
)

IDENTITY = LocalExecutionIdentity("medical_horizon", "taro", "taro-local")


def test_adapter_registry_lists_native_and_developer_apps_without_cloud_secrets():
    adapters = list_local_execution_adapters(member_id="taro", instance_id="taro-local")
    engine_ids = {a.engine_id for a in adapters}
    assert "sinria_native" in engine_ids
    assert {"claude_code", "codex", "antigravity"} <= engine_ids
    assert all(a.local_only is True for a in adapters)
    assert all(a.credential_stored_in_cloud is False for a in adapters)
    assert all(a.raw_prompt_stored_in_cloud is False for a in adapters)
    assert all(a.raw_diff_stored_in_cloud is False for a in adapters)


def test_claude_code_adapter_supports_sales_outreach_plan_without_cloud_raw_content():
    adapters = {a.engine_id: a for a in list_local_execution_adapters(member_id="member_kikuchi", instance_id="inst_kikuchi_local")}
    assert "sales_outreach_plan" in adapters["claude_code"].supported_task_kinds
    assert adapters["claude_code"].local_only is True
    assert adapters["claude_code"].raw_prompt_stored_in_cloud is False
    assert adapters["claude_code"].raw_diff_stored_in_cloud is False


def test_claude_code_can_execute_sales_outreach_plan_when_double_gate_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _bin: True)
    monkeypatch.setattr(sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path)
    fake = _make_fake_subprocess(0, {"num_turns": 1, "subtype": "success", "result": "RAW SALES BODY SHOULD STAY LOCAL"})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task={
            "id": "sales-cc-1",
            "taskKind": "sales_outreach_plan",
            "taskText": "RAW PRIVATE SALES INSTRUCTION",
            "policy": {
                "localAdapterExecutionApproved": True,
                "adapterRawContextAllowed": False,
            },
        },
        identity=LocalExecutionIdentity("medical-horizon", "member_kikuchi", "inst_kikuchi_local"),
        dry_run=False,
        working_dir=str(ROOT),
    )

    assert result["status"] == "completed"
    assert "sales_outreach_plan" in result["sanitizedSummary"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "RAW PRIVATE SALES INSTRUCTION" not in serialized
    assert "RAW SALES BODY SHOULD STAY LOCAL" not in serialized
    assert result["externalActionPerformed"] is False
    assert all(ref.startswith("local://") for ref in result["localArtifactRefs"])


def test_unavailable_adapter_returns_recoverable_failure(monkeypatch):
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "codex")
    result = invoke_local_execution_adapter(
        engine_id="antigravity",
        task={"id": "task-1", "taskKind": "implementation"},
        identity=IDENTITY,
        dry_run=True,
    )
    assert result["status"] == "failed_recoverable"
    assert result["externalActionPerformed"] is False


def test_native_engine_dry_run_is_no_action_and_metadata_only():
    result = invoke_local_execution_adapter(
        engine_id="sinria_native",
        task={"taskKind": "sales_outreach_plan"},
        identity=IDENTITY,
        dry_run=True,
    )
    assert result["status"] == "planned"
    assert result["externalActionPerformed"] is False
    assert result["rawPromptStoredInCloud"] is False
    assert result["rawOutputStoredInCloud"] is False
    assert result["rawDiffStoredInCloud"] is False
    assert result["credentialStoredInCloud"] is False


def test_disallowed_engine_blocked_even_when_allowlist_empty(monkeypatch):
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "")
    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task={"taskKind": "implementation"},
        identity=IDENTITY,
        dry_run=True,
    )
    assert result["status"] == "failed_recoverable"
    assert "allowlist" in result["sanitizedSummary"]


def test_select_execution_engine_falls_back_to_native_when_env_disallows(monkeypatch):
    monkeypatch.delenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", raising=False)
    task = {
        "policy": {
            "preferredExecutionEngine": "claude_code",
            "allowedExecutionEngines": ["sinria_native", "claude_code"],
        }
    }
    # Policy allows claude_code but env allowlist does not → native.
    assert select_execution_engine(task, IDENTITY) == "sinria_native"


def test_adapter_availability_is_cloud_safe(monkeypatch):
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    avail = adapter_availability("taro", "taro-local")
    assert avail["sinria_native"]["allowed"] is True
    assert avail["claude_code"]["allowed"] is True
    assert avail["antigravity"]["allowed"] is False
    for entry in avail.values():
        assert entry["credential_stored_in_cloud"] is False
        assert entry["raw_prompt_stored_in_cloud"] is False
        assert entry["raw_diff_stored_in_cloud"] is False


def test_claude_code_real_run_requires_local_execution_approval(monkeypatch):
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.delenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", raising=False)
    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task={"id": "task-1", "taskKind": "implementation", "taskText": "safe synthetic task"},
        identity=IDENTITY,
        dry_run=False,
        working_dir=str(ROOT),
    )
    assert result["status"] == "waiting_review"
    assert result["externalActionPerformed"] is False
    assert result["rawPromptStoredInCloud"] is False


def test_claude_code_real_run_requires_task_policy_to_allow_adapter(monkeypatch):
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task={"id": "task-1", "taskKind": "implementation", "policy": {"localAdapterExecutionApproved": False}},
        identity=IDENTITY,
        dry_run=False,
        working_dir=str(ROOT),
    )
    assert result["status"] == "waiting_review"
    assert result["externalActionPerformed"] is False
    assert result["rawPromptStoredInCloud"] is False


def test_native_engine_real_run_is_exempt_from_local_adapter_gate(monkeypatch):
    # sinria_native is the runtime of record; the developer-adapter approval gate
    # must not apply to it, and it must never fall through to a developer-adapter
    # subprocess path. No env/policy approval is set here, yet native is not blocked
    # by the gate — it returns the "native dispatch path" no-action result.
    monkeypatch.delenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", raising=False)
    result = invoke_local_execution_adapter(
        engine_id="sinria_native",
        task={"taskKind": "implementation"},
        identity=IDENTITY,
        dry_run=False,
        working_dir=str(ROOT),
    )
    assert result["status"] == "waiting_review"
    assert result["externalActionPerformed"] is False
    assert "native dispatch path" in result["sanitizedCommandSummary"]


# ── Task 2: Claude Code command builder tests ──────────────────────────────


def test_build_adapter_prompt_always_starts_with_preamble():
    task = {"id": "t-1", "taskKind": "implementation", "repoPath": "/repo"}
    prompt = _build_adapter_prompt(task)
    assert prompt.startswith(_SINRIA_ADAPTER_PREAMBLE)


def test_build_adapter_prompt_sanitized_mode_excludes_raw_task_text():
    # When adapterRawContextAllowed is False (default), taskText must not appear
    # in the prompt body.
    raw_text = "SECRET_BUSINESS_LOGIC: do the thing"
    task = {
        "id": "t-2",
        "taskKind": "implementation",
        "taskText": raw_text,
        "policy": {"adapterRawContextAllowed": False},
    }
    prompt = _build_adapter_prompt(task)
    assert raw_text not in prompt
    # But safe fields (task ID, kind) may appear.
    assert "t-2" in prompt


def test_build_adapter_prompt_raw_context_allowed_includes_task_text():
    task_text = "implement the foo widget"
    task = {
        "id": "t-3",
        "taskKind": "implementation",
        "taskText": task_text,
        "policy": {"adapterRawContextAllowed": True},
    }
    prompt = _build_adapter_prompt(task)
    assert task_text in prompt
    assert prompt.startswith(_SINRIA_ADAPTER_PREAMBLE)


def test_build_claude_code_command_argv_shape():
    prompt = "do something"
    cmd = _build_claude_code_command(prompt, max_turns=12)
    # Must begin with the binary name and -p flag.
    assert cmd[0] in {"claude", "sinria-claude"}  # default or custom bin
    assert cmd[1] == "-p"
    assert cmd[2] == prompt
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "--max-turns" in cmd
    assert "12" in cmd


def test_build_claude_code_command_no_dangerous_flag():
    cmd = _build_claude_code_command("prompt text", max_turns=5)
    assert "--dangerously-skip-permissions" not in cmd


def test_build_claude_code_command_custom_binary(monkeypatch):
    monkeypatch.setenv("SINRIA_CLAUDE_CODE_BIN", "my-claude")
    # Re-call the function; it reads env at call time.
    cmd = _build_claude_code_command("prompt", max_turns=3)
    assert cmd[0] == "my-claude"


def _make_fake_subprocess(returncode: int, stdout_dict: dict) -> object:
    """Return a mock function for subprocess.run that records calls."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout=json.dumps(stdout_dict),
            stderr="",
        )

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


def test_invoke_adapter_calls_claude_with_correct_argv(monkeypatch):
    """After both gates pass, subprocess.run must be called with the expected argv."""
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    # Make 'claude' appear installed.
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _: True)

    fake = _make_fake_subprocess(0, {"result": "wrote 3 files"})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    task = {
        "id": "t-10",
        "taskKind": "implementation",
        "policy": {"localAdapterExecutionApproved": True},
    }
    invoke_local_execution_adapter(
        engine_id="claude_code",
        task=task,
        identity=IDENTITY,
        dry_run=False,
        working_dir=str(ROOT),
    )

    assert len(fake.calls) == 1
    called_argv = fake.calls[0]
    # Verify shape: [binary, "-p", <prompt>, "--output-format", "json", "--max-turns", <n>]
    assert called_argv[1] == "-p"
    assert "--output-format" in called_argv
    idx_of = called_argv.index("--output-format")
    assert called_argv[idx_of + 1] == "json"
    assert "--max-turns" in called_argv
    assert "--dangerously-skip-permissions" not in called_argv
    # The preamble must be in the prompt arg (argv[2]).
    assert _SINRIA_ADAPTER_PREAMBLE in called_argv[2]


def test_invoke_adapter_result_does_not_leak_raw_prompt_or_stdout(monkeypatch):
    """The returned dict must not contain the raw prompt text or raw stdout body."""
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _: True)

    raw_stdout_body = "EXTREMELY_SENSITIVE_OUTPUT_12345"
    raw_task_text = "SECRET_TASK_TEXT_ABCDE"
    fake = _make_fake_subprocess(0, {"result": raw_stdout_body})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    task = {
        "id": "t-11",
        "taskKind": "implementation",
        "taskText": raw_task_text,
        "policy": {
            "localAdapterExecutionApproved": True,
            "adapterRawContextAllowed": False,  # sanitized mode: taskText must not leak
        },
    }
    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=task,
        identity=IDENTITY,
        dry_run=False,
    )

    serialized = json.dumps(result)
    assert raw_task_text not in serialized, "raw task text must not appear in returned dict"
    assert raw_stdout_body not in serialized, "raw stdout body must not appear in returned dict"


def test_invoke_adapter_no_action_flags_remain_false_on_success(monkeypatch):
    """externalActionPerformed and all raw*StoredInCloud flags must stay False."""
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _: True)

    fake = _make_fake_subprocess(0, {"result": "3 files modified"})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    task = {
        "id": "t-12",
        "taskKind": "implementation",
        "policy": {"localAdapterExecutionApproved": True},
    }
    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=task,
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] == "completed"
    assert result["externalActionPerformed"] is False
    assert result["rawPromptStoredInCloud"] is False
    assert result["rawOutputStoredInCloud"] is False
    assert result["rawDiffStoredInCloud"] is False
    assert result["credentialStoredInCloud"] is False


def test_invoke_adapter_nonzero_returncode_is_failed_recoverable_without_leak(monkeypatch):
    """A non-zero exit collapses to failed_recoverable and never leaks raw stderr."""
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _: True)

    secret_stderr = "SENSITIVE_STDERR_BODY_98765"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=2, stdout="", stderr=secret_stderr)

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake_run)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task={"id": "t-13", "taskKind": "implementation", "policy": {"localAdapterExecutionApproved": True}},
        identity=IDENTITY,
        dry_run=False,
    )
    assert result["status"] == "failed_recoverable"
    assert result["externalActionPerformed"] is False
    assert secret_stderr not in json.dumps(result)


def test_invoke_adapter_timeout_is_failed_recoverable_without_leak(monkeypatch):
    """A subprocess timeout must not let the exception's raw .stdout/.stderr escape."""
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _: True)

    secret_partial = "PARTIAL_OUTPUT_BEFORE_TIMEOUT_55555"

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1, output=secret_partial, stderr=secret_partial)

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake_run)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task={"id": "t-14", "taskKind": "implementation", "policy": {"localAdapterExecutionApproved": True}},
        identity=IDENTITY,
        dry_run=False,
    )
    assert result["status"] == "failed_recoverable"
    assert "timed out" in result["sanitizedSummary"]
    assert secret_partial not in json.dumps(result)


# ── Task 3: local artifact persistence tests ──────────────────────────────────


def _gate_env(monkeypatch):
    """Enable both approval gates."""
    monkeypatch.setenv("SINRIA_ALLOWED_LOCAL_EXECUTION_ENGINES", "claude_code")
    monkeypatch.setenv("SINRIA_LOCAL_ADAPTER_EXECUTION_APPROVED", "1")
    monkeypatch.setattr(sinria_local_execution_adapters, "_installed", lambda _: True)


def _approved_task(extra: dict | None = None) -> dict:
    base = {
        "id": "t-art-1",
        "taskKind": "implementation",
        "taskText": "safe synthetic task",
        "policy": {"localAdapterExecutionApproved": True},
    }
    if extra:
        base.update(extra)
    return base


def test_successful_run_returns_local_artifact_ref_and_no_raw_content(monkeypatch, tmp_path):
    """On success, result must have a local:// ref and must not contain raw prompt/stdout."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    raw_stdout_body = "raw stdout body SENSITIVE_CONTENT_XYZ"
    fake = _make_fake_subprocess(0, {"num_turns": 5, "result": raw_stdout_body})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task(),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] in {"completed", "waiting_review"}
    serialized = json.dumps(result)
    assert "safe synthetic task" not in serialized, "raw task text must not reach cloud"
    assert "raw stdout body" not in serialized, "raw stdout must not reach cloud"
    assert result["rawPromptStoredInCloud"] is False
    assert result["rawOutputStoredInCloud"] is False
    assert len(result["localArtifactRefs"]) >= 1
    assert result["localArtifactRefs"][0].startswith("local://")


def test_successful_run_persists_summary_json_locally(monkeypatch, tmp_path):
    """summary.json must exist under tmp_path and contain no raw content."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    fake = _make_fake_subprocess(0, {"num_turns": 3, "subtype": "success"})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task(),
        identity=IDENTITY,
        dry_run=False,
    )

    # Derive path from the first local:// ref
    ref = result["localArtifactRefs"][0]
    assert ref.startswith("local://")
    rel = ref[len("local://"):]
    summary_path = tmp_path / rel
    assert summary_path.exists(), f"summary.json not found at {summary_path}"

    summary = json.loads(summary_path.read_text())
    assert summary["status"] == "completed"
    assert summary["rawPromptStoredInCloud"] is False
    assert summary["rawOutputStoredInCloud"] is False


def test_successful_run_persists_prompt_txt_locally(monkeypatch, tmp_path):
    """prompt.txt must be written locally and contain the preamble."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    fake = _make_fake_subprocess(0, {"num_turns": 1})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task(),
        identity=IDENTITY,
        dry_run=False,
    )

    # Find prompt.txt under tmp_path
    found = list(tmp_path.rglob("prompt.txt"))
    assert found, "prompt.txt was not written locally"
    content = found[0].read_text()
    assert _SINRIA_ADAPTER_PREAMBLE in content


def test_nonzero_returncode_persists_artifacts_and_attaches_refs(monkeypatch, tmp_path):
    """Non-zero exit must persist artifacts locally and return local:// refs."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    secret_stderr = "SENSITIVE_STDERR_FOR_ARTIFACT_TEST_99"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=2, stdout="", stderr=secret_stderr
        )

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake_run)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-art-2"}),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] == "failed_recoverable"
    # Must not leak raw stderr to cloud
    assert secret_stderr not in json.dumps(result)
    # Must have a local:// ref
    assert len(result["localArtifactRefs"]) >= 1
    assert result["localArtifactRefs"][0].startswith("local://")
    # summary.json must exist
    ref = result["localArtifactRefs"][0]
    summary_path = tmp_path / ref[len("local://"):]
    assert summary_path.exists()
    # stderr must be stored locally
    stderr_files = list(tmp_path.rglob("stderr.txt"))
    assert stderr_files, "stderr.txt was not written"
    assert secret_stderr in stderr_files[0].read_text()


def test_timeout_persists_artifacts_and_attaches_refs(monkeypatch, tmp_path):
    """Timeout must persist partial artifacts locally and return local:// refs."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    partial_out = "PARTIAL_TIMEOUT_OUTPUT_ART_TEST_77"
    partial_err = "PARTIAL_TIMEOUT_STDERR_ART_TEST_77"

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=1, output=partial_out, stderr=partial_err
        )

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake_run)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-art-3"}),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] == "failed_recoverable"
    serialized = json.dumps(result)
    assert partial_out not in serialized, "partial stdout must not reach cloud"
    assert partial_err not in serialized, "partial stderr must not reach cloud"
    assert len(result["localArtifactRefs"]) >= 1
    assert result["localArtifactRefs"][0].startswith("local://")
    # Locally stored stderr must contain partial data
    stderr_files = list(tmp_path.rglob("stderr.txt"))
    assert stderr_files, "stderr.txt was not written on timeout"
    assert partial_err in stderr_files[0].read_text()


def test_timeout_with_bytes_output_persists_without_leak(monkeypatch, tmp_path):
    """A real subprocess timeout yields bytes .output/.stderr (text=True decodes
    only on success). The bytes branch must decode locally and never leak to cloud."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    partial_err = "BYTES_TIMEOUT_STDERR_88"

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=cmd, timeout=1, output=b"BYTES_TIMEOUT_OUTPUT_88", stderr=partial_err.encode()
        )

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake_run)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-art-bytes"}),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] == "failed_recoverable"
    assert "BYTES_TIMEOUT_OUTPUT_88" not in json.dumps(result)
    assert partial_err not in json.dumps(result)
    # bytes stderr is decoded and stored locally
    stderr_files = list(tmp_path.rglob("stderr.txt"))
    assert stderr_files, "stderr.txt was not written on bytes-timeout"
    assert partial_err in stderr_files[0].read_text()


def test_oserr_persists_prompt_and_attaches_refs(monkeypatch, tmp_path):
    """OSError (binary missing) must still persist prompt locally and return local:// ref."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    def fake_run(cmd, **kwargs):
        raise OSError("No such file or directory")

    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake_run)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-art-4"}),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] == "failed_recoverable"
    assert "No such file or directory" not in json.dumps(result), "OSError message must not leak to cloud"
    assert len(result["localArtifactRefs"]) >= 1
    assert result["localArtifactRefs"][0].startswith("local://")
    found_prompts = list(tmp_path.rglob("prompt.txt"))
    assert found_prompts, "prompt.txt must be written even on OSError"


def test_artifact_write_failure_degrades_gracefully(monkeypatch, tmp_path):
    """If artifact persistence fails (e.g. disk error), the result must not crash
    and must still return a valid failed_recoverable or completed dict with no leaks."""
    _gate_env(monkeypatch)
    # Make get_sinria_home return a path that we'll make unwritable via a broken mock
    # that raises on mkdir.
    import pathlib

    def broken_mkdir(self, *args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(pathlib.Path, "mkdir", broken_mkdir)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    fake = _make_fake_subprocess(0, {"num_turns": 2})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-art-5"}),
        identity=IDENTITY,
        dry_run=False,
    )

    # Must not crash; result must be a valid dict.
    assert result["status"] in {"completed", "failed_recoverable", "waiting_review"}
    assert result["rawPromptStoredInCloud"] is False
    assert result["rawOutputStoredInCloud"] is False
    # On artifact write failure, refs degrade to [].
    assert result["localArtifactRefs"] == []


def test_no_action_flags_remain_false_on_all_artifact_paths(monkeypatch, tmp_path):
    """All no-action safety flags must remain False across all exit paths."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    fake = _make_fake_subprocess(0, {"num_turns": 1, "subtype": "success"})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-art-6"}),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["externalActionPerformed"] is False
    assert result["rawPromptStoredInCloud"] is False
    assert result["rawOutputStoredInCloud"] is False
    assert result["rawDiffStoredInCloud"] is False
    assert result["credentialStoredInCloud"] is False


def test_invoke_adapter_drops_non_int_num_turns_from_cloud_summary(monkeypatch, tmp_path):
    """A compromised/mocked binary returning a non-int num_turns must not leak it into
    the cloud-visible sanitizedSummary — only an integer turn count is surfaced
    (mirrors the subtype allowlist). This closes a raw-leak path into the Supabase
    sanitized_summary column."""
    _gate_env(monkeypatch)
    monkeypatch.setattr(
        sinria_local_execution_adapters, "get_sinria_home", lambda: tmp_path
    )

    secret = "STOLEN_PHI_VIA_NUM_TURNS_4242"
    fake = _make_fake_subprocess(0, {"num_turns": secret, "subtype": "success"})
    monkeypatch.setattr(sinria_local_execution_adapters.subprocess, "run", fake)

    result = invoke_local_execution_adapter(
        engine_id="claude_code",
        task=_approved_task({"id": "t-numturns"}),
        identity=IDENTITY,
        dry_run=False,
    )

    assert result["status"] == "completed"
    assert secret not in json.dumps(result), "non-int num_turns leaked into the cloud result"
    # A non-int num_turns is dropped entirely (no "turns=" fragment).
    assert "turns=" not in result["sanitizedSummary"]
