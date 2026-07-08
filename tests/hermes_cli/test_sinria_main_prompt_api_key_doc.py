import hermes_cli.main as main


def test_prompt_api_key_doc_avoids_hermes_home_literal():
    doc = main._prompt_api_key.__doc__ or ""
    assert "~/.hermes/.env" not in doc
    assert "runtime ``.env``" in doc
    assert "hermes setup" not in doc
    assert "hermes model" not in doc
