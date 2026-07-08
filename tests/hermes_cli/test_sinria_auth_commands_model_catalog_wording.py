from pathlib import Path

import hermes_cli.auth_commands as auth_commands
import hermes_cli.model_catalog as model_catalog


def test_auth_commands_source_avoids_hardcoded_hermes_profile_oauth_example():
    source = Path(auth_commands.__file__).read_text(encoding="utf-8")
    assert '`hermes --profile <name>\n        # auth add nous --type oauth`' not in source
    assert '`<cli> --profile <name>\n        # auth add nous --type oauth`' in source


def test_model_catalog_source_avoids_hardcoded_hermes_model_refresh_example():
    source = Path(model_catalog.__file__).read_text(encoding="utf-8")
    assert 'Used by tests and ``hermes model --refresh``.' not in source
    assert 'Used by tests and ``model --refresh``.' in source
