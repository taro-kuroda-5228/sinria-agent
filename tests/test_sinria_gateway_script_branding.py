from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sinria-gateway"


def _load_script_module():
    namespace = runpy.run_path(str(SCRIPT), run_name="sinria_gateway_script")
    return SimpleNamespace(**namespace)



def test_gateway_script_uses_sinria_runtime_home_and_label(monkeypatch, tmp_path):
    module = _load_script_module()

    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")
    monkeypatch.setenv("SINRIA_HOME", str(tmp_path / "sinria-home"))

    assert module._runtime_cli_name() == "sinria"
    assert module._runtime_home_dir() == tmp_path / "sinria-home"
    assert module._launchd_label() == "ai.sinria.gateway"

    plist = module.generate_launchd_plist()
    assert "ai.sinria.gateway" in plist
    assert str(tmp_path / "sinria-home" / "logs" / "gateway.log") in plist
    assert str(tmp_path / "sinria-home" / "logs" / "gateway.error.log") in plist



def test_gateway_script_help_no_longer_references_hermes_home():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "~/.hermes/logs/gateway.log" not in text
    assert "~/.hermes/gateway.json" not in text
    assert "~/.sinria/gateway.json" in text
