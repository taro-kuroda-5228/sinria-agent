from argparse import Namespace
import builtins

from hermes_cli import claw, tools_config



def test_sinria_claw_migrate_banner_branding(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    args = Namespace(
        source=str(tmp_path / "missing-openclaw"),
        preset="safe",
        overwrite=False,
        migrate_secrets=False,
        workspace_target=None,
        skill_conflict="skip",
        no_backup=False,
        yes=False,
    )

    claw._cmd_migrate(args)

    out = capsys.readouterr().out
    assert "Sinria — OpenClaw Migration" in out
    assert "Hermes — OpenClaw Migration" not in out



def test_sinria_claw_cleanup_banner_branding(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(claw, "_find_openclaw_dirs", lambda: [])

    args = Namespace(dry_run=False, yes=False, source=None)
    claw._cmd_cleanup(args)

    out = capsys.readouterr().out
    assert "Sinria — OpenClaw Cleanup" in out
    assert "Hermes — OpenClaw Cleanup" not in out



def test_sinria_tools_command_heading_branding(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(tools_config, "load_config", lambda: {})
    monkeypatch.setattr(tools_config, "_get_enabled_platforms", lambda: [])
    monkeypatch.setattr(tools_config, "_prompt_choice", lambda *a, **k: 1)

    tools_config.tools_command(Namespace(summary=False), first_install=False, config={})

    out = capsys.readouterr().out
    assert "Sinria Tool Configuration" in out
    assert "Hermes Tool Configuration" not in out



def test_sinria_spotify_post_setup_manual_hint_branding(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    original_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "hermes_cli.auth":
            raise ImportError("boom")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    tools_config._run_post_setup("spotify")

    out = capsys.readouterr().out
    assert "Run manually: sinria auth spotify" in out
    assert "Run manually: hermes auth spotify" not in out
