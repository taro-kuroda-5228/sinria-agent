from pathlib import Path

import hermes_cli.tips as tips


def test_tips_source_avoids_selected_hardcoded_profile_update_memory_plugin_examples():
    source = Path(tips.__file__).read_text(encoding="utf-8")
    assert 'hermes profile create coder creates an isolated profile' not in source
    assert 'hermes update syncs new bundled skills to ALL profiles automatically.' not in source
    assert 'hermes memory setup lets you configure an external memory provider' not in source
    assert 'hermes webhook subscribe creates event-driven webhook routes' not in source
    assert 'hermes models routes vision, compression, and aux tasks' not in source
    assert 'same as hermes -w' not in source
    assert 'hermes plugins install owner/repo installs plugins directly from GitHub.' not in source
    assert 'hermes acp runs Hermes as an ACP server' not in source
    assert 'hermes login supports OAuth-based auth' not in source
    assert 'The `profile create coder` command creates an isolated profile' in source
    assert 'The `update` command syncs new bundled skills to ALL profiles automatically.' in source
    assert 'The `memory setup` command lets you configure an external memory provider' in source
    assert 'The `webhook subscribe` command creates event-driven webhook routes' in source
    assert 'The `models` command routes vision, compression, and aux tasks' in source
    assert 'same as `-w`' in source
    assert 'The `plugins install owner/repo` command installs plugins directly from GitHub.' in source
    assert 'The `acp` command runs the CLI as an ACP server' in source
    assert 'The `login` command supports OAuth-based auth' in source


def test_tips_source_avoids_selected_hardcoded_curator_dashboard_snapshot_examples():
    source = Path(tips.__file__).read_text(encoding="utf-8")
    assert "'hermes profile create ls' would be rejected." not in source
    assert 'hermes profile create backup --clone-all copies everything' not in source
    assert 'hermes claw migrate --dry-run previews OpenClaw migration' not in source
    assert 'via hermes tools.' not in source
    assert 'snapshot of Hermes config' not in source
    assert 'while Hermes is working.' not in source
    assert 'hermes curator run --dry-run previews' not in source
    assert 'hermes auth reset <provider> clears all cooldowns' not in source
    assert 'hermes dashboard --tui embeds the full agent TUI' not in source
    assert 'makes Hermes block commands' not in source
    assert '`profile create ls` would be rejected.' in source
    assert 'The `profile create backup --clone-all` command copies everything' in source
    assert 'The `claw migrate --dry-run` command previews OpenClaw migration' in source
    assert 'via `tools`.' in source
    assert 'snapshot of CLI config' in source
    assert 'while the CLI is working.' in source
    assert 'The `curator run --dry-run` command previews' in source
    assert 'The `auth reset <provider>` command clears all cooldowns' in source
    assert 'The `dashboard --tui` command embeds the full agent TUI' in source
    assert 'makes the CLI block commands' in source
