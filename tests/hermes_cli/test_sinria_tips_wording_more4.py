from pathlib import Path

import hermes_cli.tips as tips


def test_tips_source_avoids_selected_hardcoded_hermes_path_examples():
    source = Path(tips.__file__).read_text(encoding="utf-8")
    assert 'The plan skill saves markdown plans under .hermes/plans/ in the active workspace.' not in source
    assert 'HERMES_ENABLE_PROJECT_PLUGINS=1 auto-loads repo-local plugins from ./.hermes/plugins/ — trust-gated by design.' not in source
    assert 'The plan skill saves markdown plans under the workspace-local plans directory.' in source
    assert 'HERMES_ENABLE_PROJECT_PLUGINS=1 auto-loads repo-local plugins from the project-local plugins directory — trust-gated by design.' in source
