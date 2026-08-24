"""Tests for CLI placeholder text in config/setup output."""

import os
from argparse import Namespace
from unittest.mock import patch

import pytest

from hermes_cli.config import config_command, show_config
from hermes_cli.setup import _print_setup_summary


def test_config_set_usage_marks_placeholders(capsys):
    args = Namespace(config_command="set", key=None, value=None)

    with pytest.raises(SystemExit) as exc:
        config_command(args)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Usage: sinria config set <key> <value>" in out


def test_sinria_config_set_usage_marks_placeholders(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    args = Namespace(config_command="set", key=None, value=None)

    with pytest.raises(SystemExit) as exc:
        config_command(args)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Usage: sinria config set <key> <value>" in out
    assert "Usage: hermes config set <key> <value>" not in out


def test_config_unknown_command_help_marks_placeholders(capsys):
    args = Namespace(config_command="wat")

    with pytest.raises(SystemExit) as exc:
        config_command(args)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "sinria config set <key> <value>   Set a config value" in out


def test_sinria_config_unknown_command_help_marks_placeholders(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    args = Namespace(config_command="wat")

    with pytest.raises(SystemExit) as exc:
        config_command(args)

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "sinria config set <key> <value>   Set a config value" in out
    assert "hermes config set <key> <value>   Set a config value" not in out


def test_show_config_marks_placeholders(tmp_path, capsys):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        show_config()

    out = capsys.readouterr().out
    assert "sinria config set <key> <value>" in out


def test_sinria_show_config_marks_placeholders(tmp_path, capsys):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "HERMES_CLI_NAME": "sinria"}):
        show_config()

    out = capsys.readouterr().out
    assert "sinria config set <key> <value>" in out
    assert "hermes config set <key> <value>" not in out


def test_setup_summary_marks_placeholders(tmp_path, capsys):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        _print_setup_summary({"tts": {"provider": "edge"}}, tmp_path)

    out = capsys.readouterr().out
    assert "sinria config set <key> <value>" in out



def test_sinria_setup_summary_marks_placeholders(tmp_path, capsys):
    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path), "HERMES_CLI_NAME": "sinria"}):
        _print_setup_summary({"tts": {"provider": "edge"}}, tmp_path)

    out = capsys.readouterr().out
    assert "sinria config set <key> <value>" in out
    assert "sinria gateway" in out
    assert "sinria doctor" in out
    assert "hermes gateway" not in out
    assert "hermes doctor" not in out



def test_sinria_setup_summary_marks_tts_and_terminal_hints(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("agent.auxiliary_client.get_available_vision_backends", lambda: [])

    _print_setup_summary({"tts": {"provider": "kittentts"}, "terminal": {"backend": "modal"}}, tmp_path)
    out = capsys.readouterr().out

    assert "run 'sinria setup tts'" in out
    assert "run 'sinria setup terminal'" in out
    assert "run 'hermes setup tts'" not in out
    assert "run 'hermes setup terminal'" not in out
