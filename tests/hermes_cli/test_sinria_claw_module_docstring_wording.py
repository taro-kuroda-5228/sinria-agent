from pathlib import Path

import hermes_cli.claw as claw


def test_claw_source_avoids_hardcoded_hermes_claw_module_docstring_examples():
    source = Path(claw.__file__).read_text(encoding="utf-8")
    assert '"""hermes claw — OpenClaw migration commands.' not in source
    assert '    hermes claw migrate' not in source
    assert '    hermes claw cleanup' not in source
    assert '"""claw — OpenClaw migration commands.' in source
    assert '    claw migrate' in source
    assert '    claw cleanup' in source
