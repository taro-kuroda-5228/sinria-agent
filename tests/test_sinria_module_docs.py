from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hermes_cli_package_docstring_is_compatibility_framed_for_sinria():
    text = (ROOT / "hermes_cli" / "__init__.py").read_text(encoding="utf-8")

    assert "Sinria/Hermes compatibility CLI" in text
    assert "runtime via ``SINRIA_CLI_NAME`` / ``HERMES_CLI_NAME``" in text
    assert "Hermes CLI - Unified command-line interface for Sinria." not in text
    assert "- hermes chat" not in text
