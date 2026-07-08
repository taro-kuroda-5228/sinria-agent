from __future__ import annotations


def test_code_execution_tool_prefers_sinria_native_cli_name_in_env_hint(monkeypatch):
    text = __import__("pathlib").Path("tools/code_execution_tool.py").read_text(encoding="utf-8")

    assert 'os.getenv("SINRIA_CLI_NAME")' in text
    assert 'env_hint = "~/.sinria/.env"' in text
