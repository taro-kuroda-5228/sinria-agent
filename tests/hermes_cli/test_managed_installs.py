import re
from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli.config import (
    format_managed_message,
    get_managed_system,
    recommended_update_command,
)
from hermes_cli.main import cmd_update
from tools.skills_hub import OptionalSkillSource


def test_get_managed_system_homebrew(monkeypatch):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")

    assert get_managed_system() == "Homebrew"
    assert recommended_update_command() == "brew upgrade sinria-agent"


def test_format_managed_message_homebrew(monkeypatch):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")

    message = format_managed_message("update Sinria")

    assert "managed by Homebrew" in message
    assert "brew upgrade sinria-agent" in message


def test_recommended_update_command_defaults_to_sinria_update(monkeypatch):
    monkeypatch.delenv("HERMES_MANAGED", raising=False)

    with patch("hermes_cli.config.detect_install_method", return_value="git"):
        assert recommended_update_command() == "sinria update"


def test_cmd_update_blocks_managed_homebrew(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")

    with patch("hermes_cli.main.subprocess.run") as mock_run:
        cmd_update(SimpleNamespace())

    assert not mock_run.called
    captured = capsys.readouterr()
    assert "managed by Homebrew" in captured.err
    assert "brew upgrade sinria-agent" in captured.err



def test_format_managed_message_uses_sinria_product_name(monkeypatch):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    message = format_managed_message("update Sinria Agent")

    assert "this Sinria Agent installation is managed by Homebrew" in message
    assert "this Hermes installation" not in message
    assert "brew upgrade sinria-agent" in message



def test_cmd_update_blocks_managed_homebrew_with_sinria_branding(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    with patch("hermes_cli.main.subprocess.run") as mock_run:
        cmd_update(SimpleNamespace())

    assert not mock_run.called
    captured = capsys.readouterr()
    assert "Cannot update Sinria Agent" in captured.err
    assert "this Sinria Agent installation is managed by Homebrew" in captured.err
    # The fully-branded "Cannot update Sinria Agent" string trivially contains
    # the substring "Cannot update Sinria", so a plain `not in` check can never
    # pass. The real intent is: the message must never use the un-branded
    # "Cannot update Sinria" *without* the " Agent" suffix. Enforce that with a
    # negative-lookahead word-boundary regex instead.
    assert not re.search(r"Cannot update Sinria(?! Agent)", captured.err)


def test_optional_skill_source_honors_env_override(monkeypatch, tmp_path):
    optional_dir = tmp_path / "optional-skills"
    optional_dir.mkdir()
    monkeypatch.setenv("HERMES_OPTIONAL_SKILLS", str(optional_dir))

    source = OptionalSkillSource()

    assert source._optional_dir == optional_dir
