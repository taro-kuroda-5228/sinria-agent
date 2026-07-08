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



def test_model_help_uses_sinria_client_id_default():
    out = _run_help("model")
    assert "default:\n                        sinria-cli" in out or "default: sinria-cli" in out
    assert "default: hermes-cli" not in out



def test_proxy_start_help_uses_sinria_command_hint():
    out = _run_help("proxy", "start")
    assert "See `sinria proxy\n                       providers`." in out or "See `sinria proxy providers`." in out
    assert "See `hermes proxy providers`" not in out



def test_backup_help_uses_sinria_filename():
    out = _run_help("backup")
    assert "~/sinria-\n                        backup-<timestamp>.zip" in out or "~/sinria-backup-<timestamp>.zip" in out
    assert "excludes the local agent codebase" in out
    assert "excludes the sinria-agent codebase" not in out
    assert "~/hermes-backup-<timestamp>.zip" not in out



def test_plugins_install_help_uses_sinria_examples():
    out = _run_help("plugins", "install")
    assert "anpicasso/sinria-plugin-\n               chrome-profiles" in out or "anpicasso/sinria-plugin-chrome-profiles" in out
    assert "`sinria plugins enable <name>`" in out
    assert "anpicasso/hermes-plugin-chrome-profiles" not in out



def test_profile_create_help_uses_sinria_update_hint():
    out = _run_help("profile", "create")
    assert "opts\n                       out of `sinria update` skill sync" in out or "opts out of `sinria update` skill sync" in out
    assert "opts out of `hermes update` skill sync" not in out



def test_dashboard_help_uses_sinria_process_labels():
    out = _run_help("dashboard")
    assert "Stop all running sinria dashboard processes and exit" in out
    assert "List running sinria dashboard processes and exit" in out
    assert "running hermes dashboard processes" not in out



def test_logs_help_uses_sinria_examples():
    out = _run_help("logs")
    assert "sinria logs" in out
    assert "hermes logs" not in out



def test_update_help_uses_no_hermes_agent_label():
    out = _run_help("update")
    assert "Update Sinria" not in out



def test_root_help_uses_sinria_examples():
    out = _run_help()
    assert "sinria setup" in out
    assert "sinria logs" in out
    assert "hermes setup" not in out
    assert "hermes logs" not in out



def test_status_help_uses_sinria_examples():
    out = _run_help("status")
    assert "sinria status" in out or "sinria" in out
    assert "hermes status" not in out



def test_pairing_help_uses_sinria_examples():
    out = _run_help("pairing")
    assert "sinria pairing" in out
    assert "hermes pairing" not in out



def test_send_help_uses_sinria_examples():
    out = _run_help("send")
    assert "sinria send" in out
    assert "hermes send" not in out
