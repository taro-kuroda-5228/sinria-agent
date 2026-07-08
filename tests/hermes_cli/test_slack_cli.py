"""Tests for Slack CLI helpers."""

from argparse import Namespace
import importlib

from hermes_cli.slack_cli import _build_full_manifest, slack_manifest_command


class TestSlackFullManifest:
    """Generated full Slack app manifest used by the Slack manifest command."""

    def test_app_home_messages_are_writable(self):
        manifest = _build_full_manifest("Hermes", "Your Hermes agent on Slack")

        assert manifest["features"]["app_home"] == {
            "home_tab_enabled": False,
            "messages_tab_enabled": True,
            "messages_tab_read_only_enabled": False,
        }

    def test_private_channel_directory_scope_is_included(self):
        manifest = _build_full_manifest("Hermes", "Your Hermes agent on Slack")

        bot_scopes = manifest["oauth_config"]["scopes"]["bot"]
        assert "groups:read" in bot_scopes

    def test_assistant_features_remain_enabled(self):
        manifest = _build_full_manifest("Hermes", "Your Hermes agent on Slack")

        assert "assistant_view" in manifest["features"]
        assert "assistant:write" in manifest["oauth_config"]["scopes"]["bot"]
        bot_events = manifest["settings"]["event_subscriptions"]["bot_events"]
        assert "assistant_thread_started" in bot_events



def test_slack_manifest_write_hint_uses_sinria(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    target = tmp_path / "slack-manifest.json"

    rc = slack_manifest_command(Namespace(write=str(target), slashes_only=False, name=None, description=None))

    assert rc == 0
    err = capsys.readouterr().err
    assert "`sinria setup`" in err
    assert "`hermes setup`" not in err
    assert "pick your Sinria app" in err
    assert "pick your Hermes app" not in err



def test_slack_manifest_defaults_use_sinria_identity(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    import hermes_cli.slack_cli as slack_cli

    slack_cli = importlib.reload(slack_cli)
    manifest = slack_cli._build_full_manifest("", "")

    assert manifest["display_information"]["description"] == "Your Sinria agent on Slack"
    assert manifest["features"]["assistant_view"]["assistant_description"] == "Chat with Sinria in threads and DMs."
