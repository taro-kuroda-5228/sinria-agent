from tools.code_execution_tool import build_execute_code_schema


def test_sinria_strict_execute_code_schema_points_at_sinria_env(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    schema = build_execute_code_schema(mode="strict")

    assert "~/.sinria/.env" in schema["description"]
    assert "~/.hermes/.env" not in schema["description"]


def test_hermes_strict_execute_code_schema_keeps_hermes_env(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)

    schema = build_execute_code_schema(mode="strict")

    assert "~/.hermes/.env" in schema["description"]
