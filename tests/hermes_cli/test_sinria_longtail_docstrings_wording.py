from pathlib import Path

import hermes_cli.env_loader as env_loader
import hermes_cli.goals as goals
import hermes_cli.mcp_config as mcp_config
import hermes_cli.profile_distribution as profile_distribution
import hermes_cli.relaunch as relaunch
import hermes_cli.skin_engine as skin_engine
import hermes_cli.web_server as web_server
import hermes_cli.webhook as webhook


def test_longtail_module_docstrings_avoid_selected_hardcoded_hermes_wording():
    relaunch_source = Path(relaunch.__file__).read_text(encoding="utf-8")
    env_loader_source = Path(env_loader.__file__).read_text(encoding="utf-8")
    goals_source = Path(goals.__file__).read_text(encoding="utf-8")
    profile_source = Path(profile_distribution.__file__).read_text(encoding="utf-8")
    skin_source = Path(skin_engine.__file__).read_text(encoding="utf-8")
    test_commands_source = Path('tests/hermes_cli/test_commands.py').read_text(encoding="utf-8")
    mcp_source = Path(mcp_config.__file__).read_text(encoding="utf-8")
    web_server_source = Path(web_server.__file__).read_text(encoding="utf-8")
    webhook_source = Path(webhook.__file__).read_text(encoding="utf-8")

    assert "Unified self-relaunch for Hermes CLI." not in relaunch_source
    assert "``hermes sessions browse``" not in relaunch_source
    assert "Also works when ``hermes`` is not on PATH" not in relaunch_source
    assert "Helpers for loading Hermes .env files consistently across entrypoints." not in env_loader_source
    assert "the Ralph loop for Hermes" not in goals_source
    assert "If not, Hermes feeds a" not in goals_source
    assert "cli.HermesCLI" not in goals_source
    assert "shareable, packaged Hermes profiles via git" not in profile_source
    assert "A distribution is a Hermes profile" not in profile_source
    assert "``hermes profile export/import``" not in profile_source
    assert "``hermes skills install <url>``" not in profile_source
    assert "Hermes CLI skin/theme engine." not in skin_source
    assert "Skins are defined as YAML files in ~/.hermes/skins/" not in skin_source
    assert "MCP Server Management CLI — ``hermes mcp`` subcommand." not in mcp_source
    assert "hermes webhook — manage dynamic webhook subscriptions from the CLI." not in webhook_source
    assert "Failed to spawn hermes update" not in web_server_source
    assert "used by `hermes slack manifest`" not in test_commands_source

    assert "Unified self-relaunch for the CLI." in relaunch_source
    assert "``sessions browse``" in relaunch_source
    assert "the CLI command is not on PATH" in relaunch_source
    assert "Helpers for loading runtime .env files consistently across entrypoints." in env_loader_source
    assert "the Ralph loop for the CLI" in goals_source
    assert "If not, the CLI feeds a" in goals_source
    assert "the main CLI class or the gateway" in goals_source
    assert "shareable, packaged runtime profiles via git" in profile_source
    assert "A distribution is a runtime profile" in profile_source
    assert "``profile export/import``" in profile_source
    assert "``skills install <url>``" in profile_source
    assert "CLI skin/theme engine." in skin_source
    assert "Skins are defined as YAML files in the runtime-home skins directory" in skin_source
    assert "MCP Server Management CLI." in mcp_source
    assert "<cli> webhook subscribe <name> [options]" in webhook_source
    assert "Failed to spawn background update" in web_server_source
    assert "Slack app manifest used by the Slack manifest command" in test_commands_source
