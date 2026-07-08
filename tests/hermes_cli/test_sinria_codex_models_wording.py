from pathlib import Path

import hermes_cli.codex_models as codex_models


def test_codex_models_source_avoids_selected_hardcoded_hermes_comment_prose():
    source = Path(codex_models.__file__).read_text(encoding="utf-8")
    assert "entitlement; Hermes does not." not in source
    assert "while Hermes openai-codex talks to the same" not in source
    assert "entitlement; the CLI does not." in source
    assert "while the CLI's openai-codex provider talks to the same" in source
