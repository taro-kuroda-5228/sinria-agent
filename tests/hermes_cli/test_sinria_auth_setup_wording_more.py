from pathlib import Path

import hermes_cli.auth as auth
import hermes_cli.setup as setup_mod


def test_auth_source_uses_dynamic_model_hints():
    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "After subscribing, run `hermes model` again to finish setup." not in source
    assert "Run `hermes model` again to switch to Nous Portal." not in source
    assert "Run `hermes model` or configure an API key to use Hermes." not in source
    assert "_cli_command_name()} model` again to finish setup" in source
    assert "_cli_command_name()} model` again to switch to Nous Portal" in source
    assert "configure an API key to use {_product_name()}" in source


def test_setup_source_avoids_selected_hermes_runtime_literals():
    source = Path(setup_mod.__file__).read_text(encoding="utf-8")
    assert "removed from ~/.hermes/.env" not in source
    assert "via the Hermes auth store" not in source
    assert "for 'hermes setup tts'" not in source
    assert "runtime .env" in source
    assert "runtime auth store" in source
    assert "for `<cli> setup tts`" in source
