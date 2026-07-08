from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "devops"
    / "watchers"
    / "scripts"
    / "_watermark.py"
)


def load_watermark_module():
    spec = importlib.util.spec_from_file_location("watcher_watermark_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRuntimeHomeFallback:
    def test_bare_sinria_cli_name_defaults_watcher_state_to_dot_sinria(self, tmp_path, monkeypatch):
        module = load_watermark_module()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.delenv("WATCHER_STATE_DIR", raising=False)
        monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

        assert module._state_dir() == tmp_path / ".sinria" / "watcher-state"

    def test_explicit_hermes_home_still_wins_for_sinria(self, tmp_path, monkeypatch):
        module = load_watermark_module()
        override = tmp_path / "runtime-home"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(override))
        monkeypatch.delenv("WATCHER_STATE_DIR", raising=False)
        monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

        assert module._state_dir() == override / "watcher-state"

    def test_watcher_state_dir_override_wins_for_sinria(self, tmp_path, monkeypatch):
        module = load_watermark_module()
        override = tmp_path / "watchers"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("WATCHER_STATE_DIR", str(override))
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

        assert module._state_dir() == override
