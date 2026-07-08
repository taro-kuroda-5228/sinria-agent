from pathlib import Path

import hermes_cli.callbacks as callbacks
import hermes_cli.fallback_cmd as fallback_cmd


def test_fallback_cmd_source_avoids_hardcoded_hermes_examples():
    source = Path(fallback_cmd.__file__).read_text(encoding="utf-8")
    assert 'hermes fallback — manage the fallback provider chain.' not in source
    assert 'hermes fallback [list]' not in source
    assert '`hermes model`' not in source
    assert '``~/.hermes/config.yaml``' not in source
    assert 'CLI fallback — manage the fallback provider chain.' in source
    assert 'fallback [list]' in source
    assert '`model`' in source
    assert 'runtime ``config.yaml``' in source


def test_callbacks_source_avoids_hardcoded_secret_env_path():
    source = Path(callbacks.__file__).read_text(encoding="utf-8")
    assert 'The secret is stored in ~/.hermes/.env and never exposed to the model.' not in source
    assert 'The secret is stored in the runtime .env and never exposed to the model.' in source
