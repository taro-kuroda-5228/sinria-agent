from pathlib import Path

import hermes_cli.relaunch as relaunch


def test_relaunch_source_avoids_selected_hardcoded_hermes_doc_prose():
    source = Path(relaunch.__file__).read_text(encoding="utf-8")
    assert 'used by\n    ``hermes`` itself.' not in source
    assert 'self-relaunched hermes.' not in source
    assert 'Find the hermes entry point.' not in source
    assert 'with hermes.' not in source
    assert 'fresh hermes invocation.' not in source
    assert 'run hermes again as if the user had typed' not in source
    assert '"hermes exited, then\n    new hermes started"' not in source
    assert 'used by\n    the CLI itself.' in source
    assert 'self-relaunched CLI process.' in source
    assert 'Find the active CLI entry point.' in source
    assert 'with the CLI.' in source
    assert 'fresh CLI invocation.' in source
    assert 'run the CLI again as if the user had typed' in source
    assert '"the old process exited,\n    then the new CLI process started"' in source
