from pathlib import Path

import hermes_cli.auth as auth
import hermes_cli.setup as setup_mod


def test_setup_source_avoids_selected_hermes_setup_examples():
    source = Path(setup_mod.__file__).read_text(encoding="utf-8")
    assert "between `sinria tools` and `hermes setup tools`" not in source
    assert "Both `hermes setup tools` and `sinria tools` use the same flow:" not in source
    assert "same flow used by ``hermes model``" not in source
    assert "provider added to ``hermes model``" not in source
    assert "shared hermes model flow" not in source
    assert "hermes setup           — full or quick" not in source
    assert "<cli> setup tools" in source
    assert "Both ``<cli> setup tools`` and ``<cli> tools`` use the same flow:" in source
    assert "same flow used by ``<cli> model``" in source
    assert "provider added to ``model``" in source
    assert "<cli> setup model" in source


def test_auth_source_avoids_selected_hermes_model_and_product_literals():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "re-authenticate with `hermes model` to re-fetch" not in source
    assert "Re-authenticate with `hermes model`." not in source
    assert "re-run `hermes model` to refetch" not in source
    assert "Existing Codex credentials found in Hermes auth store." not in source
    assert "Existing xAI OAuth credentials found in Hermes auth store." not in source
    assert "_cli_command_name()} model` to re-fetch" in source
    assert "re-run `<cli> model` to refetch" in source
    assert "{_product_name()} auth store" in source
