import os
import subprocess
import sys



def _run_help(*args):
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *args, "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "HERMES_CLI_NAME": "sinria"},
        check=False,
    ).stdout



def test_login_help_uses_sinria():
    out = _run_help("login")
    assert "Run OAuth device authorization flow for Sinria Agent" in out
    assert "Run OAuth device authorization flow for Hermes CLI" not in out



def test_auth_spotify_help_uses_sinria():
    out = _run_help("auth", "spotify")
    assert "Authenticate Sinria with Spotify via PKCE" in out
    assert "Authenticate Hermes with Spotify via PKCE" not in out



def test_chat_help_uses_sinria():
    out = _run_help("chat")
    assert "Start an interactive chat session with Sinria Agent" in out
    assert "Start an interactive chat session with Hermes" not in out



def test_import_help_uses_sinria():
    out = _run_help("import")
    assert "created Sinria backup into your Sinria home directory" in out or "created Sinria\nbackup into your Sinria home directory" in out
    assert "created Hermes backup into your Hermes home directory" not in out



def test_claw_help_uses_sinria():
    out = _run_help("claw")
    assert "from OpenClaw to Sinria" in out
    assert "from OpenClaw to Hermes" not in out



def test_profile_help_uses_sinria():
    out = _run_help("profile")
    assert "multiple isolated Sinria instances" in out
    assert "multiple isolated Hermes instances" not in out



def test_mcp_help_label_uses_sinria():
    out = _run_help("mcp")
    assert "Manage MCP server connections and run Sinria Agent as an MCP server" in out
    assert "Manage MCP servers and run Hermes as an MCP server" not in out
