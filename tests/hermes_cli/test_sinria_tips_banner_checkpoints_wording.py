from pathlib import Path

import hermes_cli.banner as banner
import hermes_cli.checkpoints as checkpoints
import hermes_cli.tips as tips


def test_banner_source_uses_cli_neutral_update_docstrings():
    source = Path(banner.__file__).read_text(encoding="utf-8")
    assert "Check whether a Hermes update is available." not in source
    assert "Return the active Hermes git checkout, or None if this isn't a git install." not in source
    assert "Check whether a CLI update is available." in source
    assert "Return the active CLI git checkout, or None if this isn't a git install." in source


def test_checkpoints_source_avoids_hardcoded_hermes_examples():
    source = Path(checkpoints.__file__).read_text(encoding="utf-8")
    assert '"""`hermes checkpoints` CLI subcommand.' not in source
    assert 'store at ``~/.hermes/checkpoints/``.' not in source
    assert '    hermes checkpoints' not in source
    assert '"""`checkpoints` CLI subcommand.' in source
    assert 'store under the runtime home.' in source
    assert '    checkpoints' in source


def test_tips_source_avoids_selected_hardcoded_hermes_resume_profile_strings():
    source = Path(tips.__file__).read_text(encoding="utf-8")
    assert 'resume it later with /resume or hermes -c.' not in source
    assert 'Ctrl+Z suspends Hermes to the background' not in source
    assert 'hermes -c resumes your most recent CLI session.' not in source
    assert 'hermes -p work chat runs under a specific profile' not in source
    assert "'hermes profile create coder' creates the 'coder' command." not in source
    assert 'When exiting, Hermes prints a resume command' not in source
    assert 'hermes -r SESSION_ID resumes any specific past session by its ID.' not in source
    assert 'resume it later with /resume or the CLI resume shortcut.' in source
    assert 'Ctrl+Z suspends the CLI to the background' in source
    assert 'The `-c` flag resumes your most recent CLI session.' in source
    assert 'The `-p work chat` form runs under a specific profile' in source
    assert 'creating `profile create coder` creates the `coder` command.' in source
    assert 'When exiting, the CLI prints a resume command' in source
    assert 'The `-r SESSION_ID` form resumes any specific past session by its ID.' in source
