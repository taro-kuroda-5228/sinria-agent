from pathlib import Path
from types import SimpleNamespace



def test_restore_stashed_changes_prompt_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    restored = main._restore_stashed_changes(
        ["git"],
        Path("/tmp"),
        "stash@{0}",
        prompt_user=True,
        input_fn=lambda prompt, default="": "n",
    )

    assert restored is False
    out = capsys.readouterr().out
    assert "if Sinria behaves unexpectedly" in out
    assert "if Hermes behaves unexpectedly" not in out



def test_restore_stashed_changes_drop_notice_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_resolve_stash_selector", lambda *args, **kwargs: None)

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    restored = main._restore_stashed_changes(["git"], Path("/tmp"), "stash@{0}")

    assert restored is True
    out = capsys.readouterr().out
    assert "Sinria couldn't find the stash entry to drop" in out
    assert "Hermes couldn't find the stash entry to drop" not in out
    assert "if Sinria behaves unexpectedly" in out



def test_upstream_prompt_uses_sinria_wording(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_has_upstream_remote", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "_should_skip_upstream_prompt", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    main._sync_with_upstream_if_needed(["git"], Path("/tmp"))

    out = capsys.readouterr().out
    assert "official Sinria upstream repository" in out
    assert "upstream Sinria/Hermes codebase" in out
    assert "official Hermes repository" not in out
