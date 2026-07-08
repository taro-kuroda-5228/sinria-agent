from pathlib import Path

import hermes_cli.relaunch as relaunch


def test_relaunch_source_avoids_hardcoded_hermes_failure_text():
    source = Path(relaunch.__file__).read_text(encoding="utf-8")
    assert 'Hermes relaunch failed:' not in source
    assert 're-run hermes.' not in source
    assert '{_product_name()} relaunch failed:' in source
    assert 're-run {cli}.' in source
