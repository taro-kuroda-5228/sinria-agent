from __future__ import annotations

import importlib
import os
import subprocess
import sys
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_sinria_curator_help_uses_sinria_command_and_home(tmp_path):
    env = os.environ.copy()
    env["HERMES_CLI_NAME"] = "sinria"
    env["HOME"] = str(tmp_path)
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.curator", "--help"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert "usage: sinria curator" in result.stdout
    assert "~/.sinria/skills/" in result.stdout
    assert "hermes curator" not in result.stdout
    assert "~/.hermes/skills/" not in result.stdout


def test_sinria_curator_rollback_guidance_uses_sinria_command_and_home(tmp_path, monkeypatch):
    home = tmp_path / ".sinria"
    home.mkdir()
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    import hermes_constants
    import agent.curator_backup as curator_backup
    import hermes_cli.curator as curator_cli

    importlib.reload(hermes_constants)
    importlib.reload(curator_backup)
    importlib.reload(curator_cli)

    buf = StringIO()
    with redirect_stdout(buf):
        rc = curator_cli._cmd_rollback(Namespace(list=False, backup_id=None, yes=True))

    out = buf.getvalue()
    assert rc == 1
    assert "`sinria curator backup`" in out
    assert "~/.sinria/skills/" in out
    assert "`hermes curator backup`" not in out
    assert "~/.hermes/skills/" not in out
