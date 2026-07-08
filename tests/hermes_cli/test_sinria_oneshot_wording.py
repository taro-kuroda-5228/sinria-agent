from pathlib import Path

import hermes_cli.oneshot as oneshot


def test_oneshot_source_avoids_selected_hardcoded_hermes_command_prose():
    source = Path(oneshot.__file__).read_text(encoding="utf-8")
    assert 'configured for "cli" in `hermes tools`.' not in source
    assert 'Model / provider selection mirrors `hermes chat`:' not in source
    assert '"hermes -z: failed to validate --toolsets:' not in source
    assert '"hermes -z: --toolsets all enables every toolset;' not in source
    assert 'f"hermes -z: ignoring unknown --toolsets entries:' not in source
    assert '"hermes -z: ignoring disabled MCP servers' not in source
    assert '"hermes -z: --toolsets did not contain any valid toolsets.' not in source
    assert '"hermes -z: --provider requires --model' not in source
    assert 'Best-effort SessionDB for ``hermes -z`` / oneshot mode.' not in source
    assert "when hermes is invoked for" not in source
    assert 'configured for "cli" in `<cli> tools`.' in source
    assert 'Model / provider selection mirrors `<cli> chat`:' in source
    assert 'f"{_cli_oneshot_name()}: failed to validate --toolsets:' in source
    assert 'f"{_cli_oneshot_name()}: --toolsets all enables every toolset;' in source
    assert 'f"{_cli_oneshot_name()}: ignoring unknown --toolsets entries:' in source
    assert 'f"{_cli_oneshot_name()}: ignoring disabled MCP servers' in source
    assert 'f"{_cli_oneshot_name()}: --toolsets did not contain any valid toolsets.' in source
    assert 'f"{_cli_oneshot_name()}: --provider requires --model' in source
    assert 'Best-effort SessionDB for oneshot (``-z``) mode.' in source
    assert "when the CLI is invoked for" in source
