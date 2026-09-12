"""Installer contract for the durable member-side LINE peer relay."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-sinria-line-peer-relay.py"


def _load():
    spec = importlib.util.spec_from_file_location("install_line_peer_relay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plist_pins_primary_checkout_and_contains_no_credentials(tmp_path, monkeypatch):
    module = _load()
    root = tmp_path / "sinria-agent"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "sinria-line-peer-relay.py").write_text("# relay\n")
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("")
    monkeypatch.setattr(module, "get_sinria_home", lambda: tmp_path / ".sinria")

    plist = module.build_plist(root=root, host="127.0.0.1", port=8765)

    assert plist["Label"] == "ai.sinria.line-peer-relay"
    assert plist["WorkingDirectory"] == str(root.resolve())
    assert str(root.resolve() / "scripts" / "sinria-line-peer-relay.py") in plist["ProgramArguments"]
    serialized = repr(plist)
    assert "SINRIA_LINE_PEER_RELAY_TOKEN" not in serialized
    assert "SINRIA_LOCAL_API_KEY" not in serialized
    assert plist["EnvironmentVariables"] == {"SINRIA_HOME": str(tmp_path / ".sinria"), "PYTHONUNBUFFERED": "1"}


def test_plist_rejects_non_loopback_bind(tmp_path, monkeypatch):
    module = _load()
    root = tmp_path / "sinria-agent"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "sinria-line-peer-relay.py").write_text("# relay\n")
    (root / "venv/bin").mkdir(parents=True)
    (root / "venv/bin/python").write_text("")
    monkeypatch.setattr(module, "get_sinria_home", lambda: tmp_path / ".sinria")
    with pytest.raises(SystemExit, match="loopback"):
        module.build_plist(root=root, host="0.0.0.0", port=8765)


def test_resolve_primary_checkout_fails_closed_for_unresolvable_separate_git_dir(tmp_path):
    module = _load()
    primary = tmp_path / "primary"
    git_dir = tmp_path / "repo-data.git"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "--separate-git-dir", str(git_dir), str(primary)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "Test"], check=True)
    (primary / "seed").write_text("seed")
    subprocess.run(["git", "-C", str(primary), "add", "seed"], check=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", "-b", "linked", str(linked)], check=True, capture_output=True)

    with pytest.raises(SystemExit, match="primary checkout could not be resolved"):
        module.resolve_primary_checkout(linked)
