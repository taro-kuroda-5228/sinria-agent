from pathlib import Path

import hermes_cli.debug as debug
import hermes_cli.tips as tips


def test_debug_source_avoids_selected_hardcoded_debug_invocation_comment():
    source = Path(debug.__file__).read_text(encoding="utf-8")
    assert 'intended to be called from every ``hermes debug`` invocation with' not in source
    assert 'intended to be called from every ``debug`` invocation with' in source


def test_tips_source_avoids_selected_hardcoded_skill_mcp_auth_backup_examples():
    source = Path(tips.__file__).read_text(encoding="utf-8")
    assert 'hermes chat --source telegram tags the session for filtering in Sinria sessions list.' not in source
    assert 'hermes skills search react --source skills-sh searches the skills.sh public directory.' not in source
    assert 'hermes mcp serve runs Hermes itself as an MCP server for other agents.' not in source
    assert 'hermes auth add lets you add multiple API keys for credential pool rotation.' not in source
    assert 'hermes backup creates a zip backup of your entire Sinria home directory.' not in source
    assert 'hermes profile export coder -o backup.tar.gz creates a portable profile archive.' not in source
    assert 'hermes skills install official/security/1password installs optional skills from the repo.' not in source
    assert 'Cron jobs can attach skills: hermes cron add --skill blogwatcher "Check for new posts".' not in source
    assert 'The `chat --source telegram` form tags the session for filtering in the sessions list.' in source
    assert 'The `skills search react --source skills-sh` command searches the skills.sh public directory.' in source
    assert 'The `mcp serve` command runs the CLI itself as an MCP server for other agents.' in source
    assert 'The `auth add` command lets you add multiple API keys for credential pool rotation.' in source
    assert 'The `backup` command creates a zip backup of your entire runtime home directory.' in source
    assert 'The `profile export coder -o backup.tar.gz` command creates a portable profile archive.' in source
    assert 'The `skills install official/security/1password` command installs optional skills from the repo.' in source
    assert 'Cron jobs can attach skills: `cron add --skill blogwatcher \\"Check for new posts\\"`.' in source
