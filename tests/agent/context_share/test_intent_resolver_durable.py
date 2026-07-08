import json

from agent.context_share.intent_resolver import build_context_resolver_prompt


def test_build_context_resolver_prompt_loads_durable_local_evidence(monkeypatch, tmp_path):
    store_dir = tmp_path / "context_share"
    store_dir.mkdir()
    (store_dir / "evidence.jsonl").write_text(json.dumps({
        "evidence_id": "ev-durable-context-share",
        "source_session_id": "session-durable",
        "source_kind": "user_correction",
        "scope": "personal",
        "summary": "Durable record says Context Share must retrieve prior corrections before action.",
        "sanitized_sample": "durable prior correction",
        "sensitivity": "internal",
        "applies_to": ["context_share"],
        "valid_from": "2026-06-06T00:00:00Z",
        "confidence": 0.96,
        "human_approved": True,
    }) + "\n", encoding="utf-8")
    monkeypatch.setenv("SINRIA_HOME", str(tmp_path))

    prompt = build_context_resolver_prompt("Context Shareの過去指摘を反映して")

    assert "Durable record says Context Share must retrieve prior corrections before action." in prompt
    assert "ev-durable-context-share" in prompt
