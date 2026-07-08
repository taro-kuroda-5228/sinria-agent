from pathlib import Path

import hermes_cli.checkpoints as checkpoints
import hermes_cli.debug as debug
import hermes_cli.tips as tips


def test_checkpoints_source_avoids_selected_hardcoded_command_examples():
    source = Path(checkpoints.__file__).read_text(encoding="utf-8")
    assert "Clear with: hermes checkpoints clear-legacy" not in source
    assert "Wire subcommands onto the ``hermes checkpoints`` parser." not in source
    assert "bare `hermes checkpoints` → status" not in source
    assert "Clear with: checkpoints clear-legacy" in source
    assert "Wire subcommands onto the ``checkpoints`` parser." in source
    assert "bare `checkpoints` → status" in source


def test_debug_source_uses_dynamic_usage_help():
    source = Path(debug.__file__).read_text(encoding="utf-8")
    assert "Opportunistic sweep of expired pastes on every ``hermes debug`` call." not in source
    assert "best-effort — any failure is swallowed so ``hermes debug`` stays" not in source
    assert 'print("Usage: hermes debug <command>")' not in source
    assert "Opportunistic sweep of expired pastes on every ``debug`` call." in source
    assert "best-effort — any failure is swallowed so ``debug`` stays" in source
    assert 'print(f"Usage: {_cli_command_name()} debug <command>")' in source


def test_tips_source_avoids_selected_hardcoded_cli_examples_more():
    source = Path(tips.__file__).read_text(encoding="utf-8")
    assert '"hermes -w creates an isolated git worktree' not in source
    assert '"hermes chat -t web,terminal enables only specific toolsets' not in source
    assert '"hermes doctor --fix diagnoses and auto-repairs' not in source
    assert '"hermes gateway install sets up Hermes as a system service' not in source
    assert '"Save money: hermes tools disables unused tools' not in source
    assert '"BlueBubbles brings iMessage to Hermes via a local macOS server.' not in source
    assert 'The `-w` flag creates an isolated git worktree' in source
    assert 'The `chat -t web,terminal` form enables only specific toolsets' in source
    assert 'The `doctor --fix` command diagnoses and auto-repairs' in source
    assert 'The `gateway install` command sets up the CLI as a system service' in source
    assert 'Save money: `tools` disables unused tools, `skills config` trims skills down.' in source
    assert 'BlueBubbles brings iMessage support to the CLI via a local macOS server.' in source
