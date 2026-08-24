from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.sinria_egress import SinriaEgressBlocked, guard_model_provider_egress


ASK_EGRESS = {
    "mode": "ask",
    "confidential_external_send": "ask",
    "redact_secrets_before_external_send": True,
    "classify_lightweight": True,
}


def _agent(tmp_path, *, base_url="https://api.openai.com/v1", provider="openai", egress=None):
    return SimpleNamespace(
        base_url=base_url,
        provider=provider,
        model="gpt-test",
        session_id="test-session",
        sinria_boundary_config=False,
        sinria_egress_config=egress or ASK_EGRESS,
        sinria_egress_audit_path=Path(tmp_path) / "sinria-egress-audit.jsonl",
    )


def test_model_provider_guard_allows_internal_local_confidential_payload(tmp_path):
    agent = _agent(tmp_path, base_url="http://localhost:11434/v1", provider="ollama")

    decision = guard_model_provider_egress(
        agent,
        [{"role": "user", "content": "社外秘 password=example-secret"}],
    )

    assert decision.action == "allow"
    assert decision.external is False


def test_model_provider_guard_allows_external_non_confidential_payload(tmp_path):
    agent = _agent(tmp_path)

    decision = guard_model_provider_egress(
        agent,
        [{"role": "user", "content": "Summarize public Python release notes"}],
    )

    assert decision.action == "allow"
    assert decision.external is True


def test_model_provider_guard_blocks_external_confidential_payload_in_block_mode(tmp_path):
    agent = _agent(tmp_path, egress=dict(ASK_EGRESS, mode="block"))

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(
            agent,
            [{"role": "user", "content": "社外秘の契約書 password=example-secret"}],
        )

    assert "model_provider" in str(exc.value)
    audit = agent.sinria_egress_audit_path.read_text(encoding="utf-8")
    assert "example-secret" not in audit
    assert "password=example-secret" not in audit
    assert "block" in audit


def test_model_provider_guard_allows_external_confidential_payload_in_allow_mode(tmp_path):
    agent = _agent(tmp_path, egress=dict(ASK_EGRESS, mode="allow"))

    decision = guard_model_provider_egress(
        agent,
        [{"role": "user", "content": "confidential board memo"}],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_model_provider_guard_ask_mode_uses_interactive_gateway_approval(tmp_path, monkeypatch):
    agent = _agent(tmp_path, egress=dict(ASK_EGRESS, profile=""))
    captured = {}

    def fake_request_gateway_approval(preview, description, **kwargs):
        captured["preview"] = preview
        captured["description"] = description
        captured["kwargs"] = kwargs
        return {"approved": True, "choice": "once"}

    monkeypatch.setattr("tools.approval.request_gateway_approval", fake_request_gateway_approval)

    decision = guard_model_provider_egress(
        agent,
        [{"role": "user", "content": "confidential board memo password=example-secret"}],
    )

    assert decision.action == "ask"
    assert decision.external is True
    assert captured["kwargs"]["pattern_key"] == "sinria_egress:model_provider"
    assert captured["kwargs"]["allow_session"] is True
    assert captured["kwargs"]["allow_permanent"] is False
    assert captured["kwargs"]["metadata"]["approval_kind"] == "sinria_egress"
    assert "model_provider" in captured["preview"]
    assert "example-secret" not in captured["preview"]
    assert "password=example-secret" not in captured["preview"]
    audit = agent.sinria_egress_audit_path.read_text(encoding="utf-8")
    assert "example-secret" not in audit


def test_model_provider_guard_ask_mode_blocks_when_interactive_approval_denied(tmp_path, monkeypatch):
    agent = _agent(tmp_path, egress=dict(ASK_EGRESS, profile=""))

    monkeypatch.setattr(
        "tools.approval.request_gateway_approval",
        lambda *args, **kwargs: {"approved": False, "message": "denied"},
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(
            agent,
            [{"role": "user", "content": "confidential board memo"}],
        )

    assert exc.value.decision.action == "ask"
    assert "model_provider" in str(exc.value)


def test_dogfood_frontier_allows_confidential_concept_discussion_with_audit(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "user",
                "content": "Sinria should discuss confidential agencies, military security concepts, and patient ID redaction policy.",
            }
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True
    assert "dogfood_frontier" in decision.reason
    audit = agent.sinria_egress_audit_path.read_text(encoding="utf-8")
    assert "dogfood_frontier" in audit


def test_dogfood_frontier_allows_patient_identifier_policy_language_without_values(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "user",
                "content": "Keep patient ID / 患者ID patterns blocked, but discuss the policy and redaction design.",
            }
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_dogfood_frontier_blocks_concrete_patient_identifier_to_model_provider(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(
            agent,
            [{"role": "user", "content": "Summarize this chart for patient ID: ABC12345"}],
        )

    assert exc.value.decision.action == "block"
    assert "concrete secret" in exc.value.decision.reason


def test_dogfood_frontier_allows_synthetic_patient_identifier_fixture_in_history(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "tool",
                "content": "362|      keyFindings: [\"患者ID 12345 の対麻痺は12%（95%CI 8〜16%）\"],",
            },
            {"role": "user", "content": "cronは止めて。エラーが出ているけど原因は？"},
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_dogfood_frontier_blocks_patient_identifier_without_synthetic_context(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(agent, [{"role": "user", "content": "患者ID 12345 の件を要約して"}])

    assert exc.value.decision.action == "block"
    assert "concrete secret" in exc.value.decision.reason


def test_dogfood_frontier_allows_system_prompt_secret_policy_labels(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "system",
                "content": (
                    "User treats credentials/tokens as highly sensitive. "
                    "Help page: Token: usage. Example labels include api_key: placeholder."
                ),
            },
            {"role": "user", "content": "Reply exactly: SINRIA_OK"},
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_dogfood_frontier_allows_synthetic_secret_fixture_in_tool_history(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "tool",
                "content": "tests/test_guard.py:270 with pytest.raises(...): api_key=example-secret-token",
            },
            {"role": "user", "content": "Continue the Sinria implementation plan."},
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_dogfood_frontier_allows_documented_sk_placeholder_in_tool_history(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "tool",
                "content": "docs example: sinria auth add openrouter --api-key sk-your-api-key",
            },
            {"role": "user", "content": "Continue the Sinria implementation plan."},
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_dogfood_frontier_allows_code_expression_named_token_in_source_diff(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    decision = guard_model_provider_egress(
        agent,
        [
            {
                "role": "tool",
                "content": "source diff: + token = match.group(0)  # inspect placeholder token shape",
            },
            {"role": "user", "content": "Continue the Sinria implementation plan."},
        ],
    )

    assert decision.action == "allow"
    assert decision.external is True
    assert decision.likely_confidential is True


def test_dogfood_frontier_blocks_concrete_secret_to_model_provider(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(
            agent,
            [{"role": "user", "content": "Please use api_key=example-secret-token for this request"}],
        )

    assert exc.value.decision.action == "block"
    assert "concrete secret" in exc.value.decision.reason
    audit = agent.sinria_egress_audit_path.read_text(encoding="utf-8")
    assert "example-secret-token" not in audit
    assert "api_key=example-secret-token" not in audit


def test_dogfood_frontier_blocks_concrete_sk_token_to_model_provider(tmp_path):
    agent = _agent(
        tmp_path,
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        egress=dict(ASK_EGRESS, profile="dogfood_frontier"),
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(
            agent,
            [{"role": "user", "content": "Use sk-live1234567890 for this request"}],
        )

    assert exc.value.decision.action == "block"
    assert "concrete secret" in exc.value.decision.reason
