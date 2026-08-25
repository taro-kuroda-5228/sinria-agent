from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "sinria-peer-worker.py"
EXECUTOR = ROOT / "scripts" / "synthetic-peer-executor.py"


def _source() -> str:
    return WORKER.read_text()


def _worker_module():
    spec = importlib.util.spec_from_file_location("sinria_peer_worker_entrypoint", WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_peer_worker_uses_sinria_transport_token_and_fails_fast_when_missing():
    source = _source()
    assert "'SINRIA_COMPANY_OS_TRANSPORT_TOKEN'" in source
    assert "token_env='SINRIA_COMPANY_OS_TRANSPORT_TOKEN'" in source
    assert "CompanyOsTransportClient(os.environ['COMPANY_OS_BASE_URL'])" not in source


def test_peer_worker_preflight_runs_before_executor_configuration():
    source = _source()
    assert "p.add_argument('--preflight'" in source
    assert source.index("if a.preflight:") < source.index("command = command_adapter(")
    assert "client.canary(ident)" in source
    assert "client.list_conversation_runs(" in source


def test_worker_executor_adapter_accepts_the_real_callback_envelope(monkeypatch):
    module = _worker_module()
    monkeypatch.setenv("TEST_PEER_EXECUTOR", f"{sys.executable} {EXECUTOR}")
    adapter = module.command_adapter("TEST_PEER_EXECUTOR", mode="executor")
    result = adapter(
        {"runId": "run_1", "status": "claimed"},
        {
            "eventId": "evt_1",
            "sanitizedPreview": "Synthetic metadata-only task: verify",
            "bodyRef": None,
        },
    )
    assert result["rawContextStored"] is False
    assert result["externalActionPerformed"] is False
    assert result["refs"] == ["run://event/evt_1"]
