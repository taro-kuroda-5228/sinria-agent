from pathlib import Path

import hermes_cli.auth_commands as auth_commands


def test_auth_commands_source_avoids_hardcoded_hermes_root_example():
    source = Path(auth_commands.__file__).read_text(encoding="utf-8")
    assert "<hermes-root>/shared/nous_auth.json" not in source
    assert "<runtime-root>/shared/nous_auth.json" in source
