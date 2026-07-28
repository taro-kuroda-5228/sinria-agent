from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_sh_uses_sinria_runtime_defaults_and_commands():
    text = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'SINRIA_HOME_DEFAULT="$HOME/.sinria"' in text
    assert 'HERMES_HOME="${SINRIA_HOME:-${HERMES_HOME:-$SINRIA_HOME_DEFAULT}}"' in text
    assert "default (non-root):  ~/.sinria/sinria-agent" in text
    assert "Run 'sinria setup' after install." in text
    assert "/usr/local/bin/sinria" in text
    assert "/root/.sinria via compatibility alias" in text
    assert "/usr/local/bin/hermes" not in text
    assert "/root/.hermes" not in text
    assert "Run 'sinria gateway install' later." in text
    assert "Try: sinria gateway start" in text
    assert "You can start manually: sinria gateway" in text
    assert "To restart later: sinria gateway" in text
    assert "Skipped. Start the gateway later with: sinria gateway" in text
    assert "Extracting to $HERMES_HOME/node/" in text
    assert "Created $HERMES_HOME/.env from template" in text
    assert "Created $HERMES_HOME/config.yaml from template" in text
    assert "Configuration directory ready: $HERMES_HOME/" in text
    assert "Skills synced to $HERMES_HOME/skills/" in text
    assert "Setting up sinria command..." in text
    assert "Installed sinria launcher → $command_link_display_dir/sinria" in text
    assert "command -v sinria" in text
    assert "Reload your shell to use 'sinria' command:" in text
    assert "sinria setup" in text
    assert "sinria gateway install" in text
    assert '${GREEN}hermes${NC}' not in text
    assert '${GREEN}hermes config' not in text
    assert '${GREEN}hermes update' not in text
    assert "Nous Research" not in text
    assert "Medical-Horizon/sinria-agent" not in text


def test_skill_sync_reports_the_active_sinria_home():
    text = (ROOT / "tools" / "skills_sync.py").read_text(encoding="utf-8")

    assert 'print(f"Syncing bundled skills into {SKILLS_DIR} ...")' in text
    assert "~/.hermes/skills/" not in text



def test_install_ps1_is_single_line_iex_safe_bootstrap():
    text = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert text.count("\n") == 1
    assert "[scriptblock]::Create" in text
    assert "install.full.ps1" in text
    assert "param(" not in text
    assert "HermesHome" not in text


def test_install_full_ps1_uses_sinria_operator_commands():
    text = (ROOT / "scripts" / "install.full.ps1").read_text(encoding="utf-8")

    assert '[string]$SinriaHome = "$env:LOCALAPPDATA\\sinria"' in text
    assert '[string]$InstallDir = "$env:LOCALAPPDATA\\sinria\\sinria-agent"' in text
    assert "Cloning Sinria repository" in text
    assert "Adding Sinria to PATH" in text
    assert "An AI agent platform by Medical Horizon" in text
    assert 'Write-Host "   sinria              "' in text
    assert 'Write-Host "   sinria config       "' in text
    assert 'Write-Host "   sinria update       "' in text
    assert 'Write-Host "   hermes              "' not in text
    assert "$sinriaCmd = \"$InstallDir\\venv\\Scripts\\sinria.exe\"" in text
    assert "$hermesCmd = \"$InstallDir\\venv\\Scripts\\hermes.exe\"" not in text
    assert "Configure via the GUI or 'sinria setup'." in text
    assert "Running 'sinria whatsapp' to pair via QR code..." in text
    assert "Start the gateway later with: sinria gateway" in text
    assert "Run manually: sinria gateway" in text
    assert "Setting up sinria command..." in text
    assert "Set SINRIA_HOME=$SinriaHome" in text
    assert "Set legacy HERMES_HOME=$SinriaHome for compatibility" in text
    assert "sinria command ready" in text
    assert "Created $envPath from template" in text
    assert "Created $configPath from template" in text
    assert "Configuration directory ready: $SinriaHome/" in text
    assert "Syncing bundled skills to $SinriaHome\\skills\\ ..." in text
    assert "Open a new PowerShell window and re-run 'sinria setup tools' later." in text
    assert 'Write-Host "   sinria setup        "' in text
    assert 'Write-Host "   sinria gateway      "' in text

    assert "Configure via the GUI or 'hermes setup'." not in text
    assert "Run manually: hermes gateway" not in text
    assert "github.com/taro-kuroda-5228/sinria-agent.git" in text
    assert "archive/refs/heads/$Branch.zip" in text
    assert "NousResearch/sinria-agent" not in text


def test_install_cmd_is_sinria_branded_and_points_to_medical_horizon():
    text = (ROOT / "scripts" / "install.cmd").read_text(encoding="utf-8")

    assert "Sinria Agent Installer" in text
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.cmd" in text
    assert "raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1" in text
    assert "Hermes Agent Installer" not in text
    assert "NousResearch/hermes-agent" not in text
