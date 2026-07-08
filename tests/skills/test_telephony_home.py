import importlib.util
import sys
from pathlib import Path


TELEPHONY_PATH = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "productivity"
    / "telephony"
    / "scripts"
    / "telephony.py"
)


def _load_telephony_module():
    spec = importlib.util.spec_from_file_location("telephony_skill_script", TELEPHONY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_telephony_helper_uses_sinria_home_for_bare_sinria(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_HOME", raising=False)

    telephony = _load_telephony_module()

    assert telephony._hermes_home() == tmp_path / ".sinria"
    assert telephony._env_path() == tmp_path / ".sinria" / ".env"
    assert telephony._state_path() == tmp_path / ".sinria" / "telephony_state.json"


def test_telephony_helper_preserves_explicit_home_and_upstream_default(monkeypatch, tmp_path):
    telephony = _load_telephony_module()

    explicit_home = tmp_path / "custom-runtime"
    monkeypatch.setenv("HERMES_HOME", str(explicit_home))
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    assert telephony._hermes_home() == explicit_home

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    assert telephony._hermes_home() == tmp_path / ".hermes"
