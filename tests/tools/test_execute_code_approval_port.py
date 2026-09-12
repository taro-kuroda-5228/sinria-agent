"""Distribution executor wiring: real subprocesses, temporary approval state."""
import json
import os
import subprocess
import sys

import pytest

from tools import approval_store


def run_executor(code):
    setup = '''
import json
from tools import approval, code_execution_tool as execution, terminal_tool
approval._get_approval_config = lambda: {"gateway_timeout": 0}
approval._get_approval_mode = lambda: "manual"
approval._fire_approval_hook = lambda *a, **kw: None
approval._is_gateway_approval_context = lambda: True
approval.set_current_session_key("distribution-executor")
terminal_tool._get_env_config = lambda: {"env_type": "local"}
execution._load_config = lambda: {"mode": "strict", "timeout": 10}
'''
    result = subprocess.run(
        [sys.executable, "-c", setup + f"\nprint(execution.execute_code({code!r}))"],
        env=os.environ.copy(), text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_real_executor_requires_grant_and_executes_exactly_one_retry(tmp_path):
    marker = tmp_path / "executions.txt"
    code = f"from pathlib import Path\np = Path({str(marker)!r})\np.write_text(p.read_text() + 'run\\n' if p.exists() else 'run\\n')\nprint('approved execution')"
    first = run_executor(code)
    assert not marker.exists(), first
    assert first["status"] == "approval_required"
    rows = approval_store.list_pending()
    assert len(rows) == 1
    approval_id = rows[0]["id"]
    assert approval_store.write_response(approval_id, "once")
    second = run_executor(code)
    assert second["status"] == "success", second
    assert marker.read_text() == "run\n"
    third = run_executor(code)
    assert third["status"] == "approval_required", third
    assert marker.read_text() == "run\n"
    assert not approval_store.write_response(approval_id, "once")


@pytest.mark.parametrize("env_type", ["local", "ssh", "docker"])
def test_guard_denial_precedes_local_or_remote_spawn(monkeypatch, env_type):
    from tools import approval, code_execution_tool as execution, terminal_tool
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": env_type})
    seen = []
    def guard(code, backend, **kwargs):
        seen.append((code, backend))
        return {"approved": False, "status": "approval_required", "message": "waiting"}
    monkeypatch.setattr(approval, "check_execute_code_guard", guard)
    def unexpected(*args, **kwargs):
        pytest.fail("spawn/remote dispatch before approval")
    monkeypatch.setattr(execution, "_execute_remote", unexpected)
    monkeypatch.setattr(execution.subprocess, "Popen", unexpected)
    result = json.loads(execution.execute_code("print(42)"))
    assert seen == [("print(42)", env_type)]
    assert result["status"] == "approval_required"


@pytest.mark.parametrize("mount_config", [
    {"docker_volumes": ["/test-host:/workspace"]},
    {"docker_mount_cwd_to_workspace": True},
    {"docker_extra_args": ["--mount", "type=bind,src=/test-host,dst=/workspace"]},
])
def test_host_exposed_docker_requires_script_approval(monkeypatch, mount_config):
    from tools import approval, code_execution_tool as execution, terminal_tool
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": "docker", **mount_config})
    monkeypatch.setattr(approval, "_get_approval_mode", lambda: "manual")
    monkeypatch.setattr(approval, "_is_gateway_approval_context", lambda: True)
    requests = []
    def request(*args, **kwargs):
        requests.append(kwargs)
        return {"approved": False, "status": "approval_required"}
    monkeypatch.setattr(approval, "request_gateway_approval", request)
    monkeypatch.setattr(execution, "_execute_remote", lambda *a: pytest.fail("unguarded remote spawn"))
    result = json.loads(execution.execute_code("print(42)"))
    assert result["status"] == "approval_required"
    assert requests[0]["allow_session"] is False
    assert requests[0]["allow_permanent"] is False
