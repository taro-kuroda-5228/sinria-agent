import importlib.util
import plistlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "scripts" / "install-sinria-peer-service.py"
WORKER = ROOT / "scripts" / "sinria-peer-worker.py"


def load_service():
    spec = importlib.util.spec_from_file_location("peer_service", SERVICE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_has_persistent_loop_and_loads_sinria_env():
    source = WORKER.read_text()
    assert "load_dotenv" in source
    assert "while True:" in source
    assert "--poll-interval" in source
    assert "if a.once:" in source
    assert '"status": "poll_error"' in source


def test_plist_pins_primary_checkout_and_contains_no_token(tmp_path, monkeypatch):
    module = load_service()
    primary = tmp_path / "sinria-agent"
    primary.mkdir()
    (primary / "scripts").mkdir()
    (primary / ".venv" / "bin").mkdir(parents=True)
    (primary / ".venv" / "bin" / "python").write_text("")
    (primary / "scripts" / "sinria-peer-worker.py").write_text("")
    (primary / "scripts" / "peer-consultation-executor.py").write_text("")
    plist = module.build_plist(
        root=primary,
        mode="executor",
        member_id="member_kikuchi",
        instance_id="inst_kikuchi_local",
        subject="profile-kikuchi",
        base_url="https://company.example",
        poll_interval=15,
    )
    raw = plistlib.dumps(plist).decode()
    assert str(primary / "scripts" / "sinria-peer-worker.py") in raw
    assert "SINRIA_COMPANY_OS_TRANSPORT_TOKEN" not in raw
    assert "SINRIA_PROFILE" not in raw
    assert plist["KeepAlive"] is True
    assert plist["RunAtLoad"] is True
    assert plist["Label"] == "ai.sinria.peer-worker.executor"


def test_primary_checkout_resolution_uses_git_common_dir(tmp_path, monkeypatch):
    module = load_service()
    linked = tmp_path / "linked"
    primary = tmp_path / "primary"
    linked.mkdir(); primary.mkdir(); (primary / ".git").mkdir()
    monkeypatch.setattr(module, "git_common_dir", lambda _: primary / ".git")
    assert module.resolve_primary_checkout(linked) == primary


def test_python_path_keeps_venv_symlink_instead_of_resolving_base_interpreter(tmp_path):
    module = load_service()
    root = tmp_path / "sinria-agent"
    base = tmp_path / "python3"
    base.write_text("")
    venv = root / ".venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(base)
    assert module.python_path(root) == venv / "python"


def test_executor_workspace_preflight_uses_installed_command_and_safe_json(monkeypatch, tmp_path):
    module = load_service()
    plist = {
        "EnvironmentVariables": {
            "PEER_EXECUTOR_COMMAND": "/safe/venv/python /safe/peer-consultation-executor.py",
        }
    }
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=2, stdout='{"ok": false, "errorCode": "workspace_token_missing"}\n', stderr='')

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.run_workspace_preflight(plist, tmp_path)
    assert captured["command"] == ["/safe/venv/python", "/safe/peer-consultation-executor.py", "--preflight"]
    assert result == {
        "exit": 2,
        "result": {"ok": False, "errorCode": "workspace_token_missing"},
        "error": None,
    }
