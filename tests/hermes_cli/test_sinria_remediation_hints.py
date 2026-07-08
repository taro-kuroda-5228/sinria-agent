import subprocess
import sys

import pytest



def test_require_tty_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit) as exc:
        main._require_tty("tools")

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "'sinria tools' requires an interactive terminal" in err
    assert "'hermes tools' requires an interactive terminal" not in err



def _run_help(*args):
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *args, "--help"],
        capture_output=True,
        text=True,
        env={**__import__('os').environ, "HERMES_CLI_NAME": "sinria"},
        check=False,
    ).stdout



def test_computer_use_help_uses_sinria():
    out = _run_help("computer-use")
    assert "sinria computer-use install" in out
    assert "`sinria\ntools`" in out or "`sinria tools`" in out
    assert "hermes computer-use install" not in out



def test_mcp_help_uses_sinria():
    out = _run_help("mcp")
    assert "Use 'sinria\nmcp add'" in out or "Use 'sinria mcp add'" in out
    assert "'sinria\nmcp serve' to expose Sinria" in out or "'sinria mcp serve' to expose Sinria" in out
    assert "Use 'hermes mcp add'" not in out



def test_mcp_serve_help_uses_sinria():
    out = _run_help("mcp")
    assert "Run Sinria as an MCP server" in out
    assert "Run Hermes as an MCP server" not in out
