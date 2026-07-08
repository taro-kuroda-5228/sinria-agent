import builtins
import importlib
import os
from pathlib import Path


def _block_hermes_constants(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "hermes_constants":
            raise ImportError("simulate standalone fallback")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_file_safety_standalone_fallback_uses_explicit_hermes_home(monkeypatch, tmp_path):
    _block_hermes_constants(monkeypatch)
    explicit_home = tmp_path / "explicit-runtime"
    monkeypatch.setenv("HERMES_HOME", str(explicit_home))
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    from agent import file_safety

    file_safety = importlib.reload(file_safety)

    denied = file_safety.build_write_denied_paths(str(tmp_path))

    assert os.path.realpath(explicit_home / ".env") in denied
    assert os.path.realpath(tmp_path / ".sinria" / ".env") not in denied


def test_file_safety_standalone_fallback_uses_sinria_home_for_bare_sinria(monkeypatch, tmp_path):
    _block_hermes_constants(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_HOME", raising=False)

    from agent import file_safety

    file_safety = importlib.reload(file_safety)

    denied = file_safety.build_write_denied_paths(str(tmp_path))

    assert os.path.realpath(tmp_path / ".sinria" / ".env") in denied
    assert os.path.realpath(tmp_path / ".hermes" / ".env") not in denied


def test_file_safety_standalone_fallback_preserves_upstream_hermes_home(monkeypatch, tmp_path):
    _block_hermes_constants(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    from agent import file_safety

    file_safety = importlib.reload(file_safety)

    denied = file_safety.build_write_denied_paths(str(tmp_path))

    assert os.path.realpath(tmp_path / ".hermes" / ".env") in denied
    assert os.path.realpath(tmp_path / ".sinria" / ".env") not in denied
