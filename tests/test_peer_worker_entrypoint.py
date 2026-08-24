from pathlib import Path


def _source() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts" / "sinria-peer-worker.py").read_text()


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
