"""Sinria-native environment variables should be first-class.

Legacy HERMES_* variables may remain as compatibility aliases, but Sinria
installations must not require operators to configure Hermes-branded names.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import hermes_constants
from hermes_cli import config as cli_config


def test_sinria_home_env_takes_precedence_over_legacy_hermes_home(monkeypatch):
    monkeypatch.setenv("SINRIA_HOME", "/tmp/sinria-home")
    monkeypatch.setenv("HERMES_HOME", "/tmp/hermes-home")

    assert hermes_constants.get_hermes_home() == Path("/tmp/sinria-home")



def test_sinria_cli_name_selects_sinria_default_home(monkeypatch):
    monkeypatch.delenv("SINRIA_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")

    assert hermes_constants.get_hermes_home() == Path.home() / ".sinria"



def test_cli_command_name_accepts_sinria_native_env(monkeypatch):
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.setenv("SINRIA_CLI_NAME", "sinria")

    assert cli_config._cli_command_name() == "sinria"
    assert cli_config._product_name() == "Sinria Agent"
