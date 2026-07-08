import importlib
import os
import subprocess
from pathlib import Path


def test_sinria_tui_node_bootstrap_defaults_to_sinria_home_without_wrapper(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_HOME", raising=False)

    import hermes_constants
    import hermes_cli.main as main_mod

    importlib.reload(hermes_constants)
    main_mod = importlib.reload(main_mod)

    helper = tmp_path / "scripts" / "lib" / "node-bootstrap.sh"
    helper.parent.mkdir(parents=True)
    helper.write_text("# test helper\n", encoding="utf-8")
    (tmp_path / ".sinria" / "node" / "bin").mkdir(parents=True)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main_mod.shutil, "which", lambda _name: None)

    captured_env = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs["env"])
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main_mod.subprocess, "run", fake_run)

    main_mod._ensure_tui_node()

    assert captured_env["HERMES_HOME"] == str(tmp_path / ".sinria")
    assert str(tmp_path / ".sinria" / "node" / "bin") in os.environ["PATH"].split(os.pathsep)
