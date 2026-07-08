import importlib
import json
import os
import subprocess
import time
from pathlib import Path


SINRIA_WRAPPER = Path("/opt/homebrew/bin/sinria")
SINRIA_RUNTIME_WRAPPER = Path("/Users/tarokuroda/.sinria/bin/sinria")
SINRIA_REPO = Path("/Users/tarokuroda/sinria")


def test_sinria_named_runtime_defaults_to_sinria_home_without_wrapper(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("SINRIA_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    import hermes_constants

    hermes_constants = importlib.reload(hermes_constants)

    assert hermes_constants.get_hermes_home() == tmp_path / ".sinria"
    assert hermes_constants.get_default_hermes_root() == tmp_path / ".sinria"
    assert hermes_constants.display_hermes_home() == "~/.sinria"


def test_installed_sinria_wrapper_uses_two_stage_sinria_runtime_scaffold():
    assert SINRIA_WRAPPER.exists()
    assert SINRIA_RUNTIME_WRAPPER.exists()

    wrapper = SINRIA_WRAPPER.read_text(encoding="utf-8")
    runtime_wrapper = SINRIA_RUNTIME_WRAPPER.read_text(encoding="utf-8")

    assert "exec /Users/tarokuroda/.sinria/bin/sinria \"$@\"" in wrapper
    assert "SINRIA_HOME=\"${SINRIA_HOME:-$HOME/.sinria}\"" in runtime_wrapper
    assert "HERMES_HOME=\"$SINRIA_HOME\"" in runtime_wrapper
    assert "SINRIA_CLI_NAME=sinria" in runtime_wrapper
    assert "HERMES_CLI_NAME=sinria" in runtime_wrapper
    assert str(SINRIA_REPO) in runtime_wrapper
    assert ".openclaw/workspace/sinria" not in runtime_wrapper
    assert "--profile sinria" not in runtime_wrapper


def test_sinria_help_uses_sinria_command_name():
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(Path("/tmp") / "sinria-help-test")
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "--help"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert "usage: sinria" in result.stdout
    assert "usage: hermes" not in result.stdout
    assert "Sinria Agent - AI assistant" in result.stdout
    assert "Sinria - AI assistant" not in result.stdout
    assert "~/.sinria/checkpoints/" in result.stdout
    assert "~/.hermes/checkpoints/" not in result.stdout
    assert "Back up Sinria home directory" in result.stdout
    assert "Back up Hermes home directory" not in result.stdout
    assert "Run Sinria Agent as an ACP" in result.stdout
    assert "Run Sinria as an ACP" not in result.stdout
    assert "setup               Interactive setup wizard" in result.stdout
    assert "status              Show status of all components" in result.stdout
    assert "doctor              Check configuration and dependencies" in result.stdout
    assert "sinria debug share" in result.stdout
    assert "checkpoints         Inspect / prune / clear ~/.sinria/checkpoints/" in result.stdout
    assert "uninstall           Uninstall Sinria Agent" in result.stdout
    assert "acp                 Run Sinria Agent as an ACP" in result.stdout
    assert "logs                View and filter Sinria log files" in result.stdout
    assert "setup               Configure Sinria with an interactive wizard." not in result.stdout
    assert "Run 'hermes tools' with no subcommand for the interactive configuration UI." not in result.stdout
    assert "Install agent-browser + Playwright Chromium into ~/.hermes/node/" not in result.stdout


def test_sinria_version_uses_sinria_identity_and_runtime_home(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)

    # Seed a fresh update-check cache in the runtime home so the "behind"
    # result is deterministic. Without this, `sinria --version` shells out to
    # `git rev-list HEAD..origin/main` against the installed checkout, whose
    # count depends on the developer's local git/network state (flaky: 0 when
    # the checkout is up to date → no "run 'sinria update'" line).
    #
    # check_for_updates() (hermes_cli/banner.py) honours the cache only when
    # both `ts` is fresh (<6h) AND `rev` matches HERMES_REVISION. The wrapper
    # does not set HERMES_REVISION, so the cached rev must be null. A positive
    # `behind` triggers the "run 'sinria update'" banner line.
    runtime_home = tmp_path / "runtime-home"
    runtime_home.mkdir(parents=True, exist_ok=True)
    (runtime_home / ".update_check").write_text(
        json.dumps({"ts": time.time(), "behind": 1, "rev": None}),
        encoding="utf-8",
    )

    hermes_update_check = Path.home() / ".hermes" / ".update_check"
    hermes_update_check_before = (
        hermes_update_check.stat().st_mtime_ns if hermes_update_check.exists() else None
    )

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert "Sinria Agent v" in result.stdout
    assert "Sinria v" not in result.stdout
    assert "Project: /Users/tarokuroda/sinria" in result.stdout
    assert "run 'sinria update'" in result.stdout
    hermes_update_check_after = (
        hermes_update_check.stat().st_mtime_ns if hermes_update_check.exists() else None
    )
    assert hermes_update_check_after == hermes_update_check_before
    assert (tmp_path / "runtime-home" / ".update_check").exists()


def test_sinria_status_uses_sinria_identity_for_local_runtime_guidance(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "status"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert "Sinria Agent Status" in result.stdout
    assert "Sinria Status" not in result.stdout
    assert "Run 'sinria doctor'" in result.stdout
    assert "Run 'hermes doctor'" not in result.stdout
    assert "Run 'sinria setup'" in result.stdout
    assert "Run 'hermes setup'" not in result.stdout


def test_sinria_cron_empty_list_guidance_uses_sinria_command_name(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "cron", "list"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert "Create one with 'sinria cron create ...'" in result.stdout
    assert "Create one with 'hermes cron create ...'" not in result.stdout


def test_sinria_cron_status_gateway_guidance_uses_sinria_command_name(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "cron", "status"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert "sinria gateway install" in result.stdout
    assert "sinria gateway            # Or run in foreground" in result.stdout
    assert "hermes gateway install" not in result.stdout


def test_sinria_doctor_missing_local_setup_guidance_uses_sinria_command_name(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "doctor"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=60,
    )

    assert "Run 'sinria setup'" in result.stdout
    assert "run 'sinria doctor --fix'" in result.stdout
    assert "Run 'hermes setup'" not in result.stdout
    assert "run 'hermes doctor --fix'" not in result.stdout
    assert "~/.sinria/.env" in result.stdout or str(tmp_path / "runtime-home" / ".env") in result.stdout
    assert "No GITHUB_TOKEN" in result.stdout


def test_sinria_chat_missing_provider_guidance_uses_sinria_identity_and_home(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "chat", "-q", "Say OK"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=30,
    )

    output_compact = "".join(result.stdout.split())
    assert "Run 'sinria model'" in result.stdout
    assert str(tmp_path / "runtime-home" / ".env") in output_compact
    assert "Run 'hermes model'" not in result.stdout
    assert "~/.hermes/.env" not in result.stdout


def test_sinria_cli_paths_use_sinria_home_by_default_and_with_override(tmp_path):
    env = os.environ.copy()
    env.pop("SINRIA_HOME", None)
    env.pop("HERMES_HOME", None)

    default_config = subprocess.run(
        [str(SINRIA_WRAPPER), "config", "path"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    ).stdout.strip()
    default_env = subprocess.run(
        [str(SINRIA_WRAPPER), "config", "env-path"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    ).stdout.strip()

    assert default_config == str(Path.home() / ".sinria" / "config.yaml")
    assert default_env == str(Path.home() / ".sinria" / ".env")

    runtime_home = tmp_path / "runtime-home"
    env["SINRIA_HOME"] = str(runtime_home)

    override_config = subprocess.run(
        [str(SINRIA_WRAPPER), "config", "path"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    ).stdout.strip()
    override_env = subprocess.run(
        [str(SINRIA_WRAPPER), "config", "env-path"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    ).stdout.strip()

    assert override_config == str(runtime_home / "config.yaml")
    assert override_env == str(runtime_home / ".env")


def test_sinria_wrapper_clears_external_messaging_env_leakage(tmp_path):
    env = os.environ.copy()
    env["SINRIA_HOME"] = str(tmp_path / "runtime-home")
    env.pop("HERMES_HOME", None)
    env["DISCORD_BOT_TOKEN"] = "should-not-leak"
    env["DISCORD_ALLOWED_USERS"] = "user1,user2"

    result = subprocess.run(
        [str(SINRIA_WRAPPER), "config", "env-path"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.stdout.strip() == str(tmp_path / "runtime-home" / ".env")
    assert "should-not-leak" not in result.stdout
    assert "should-not-leak" not in result.stderr
