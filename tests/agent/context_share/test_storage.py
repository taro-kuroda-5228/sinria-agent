
import json

from agent.context_share.storage import load_durable_evidence


def test_loads_durable_evidence_from_local_jsonl(tmp_path):
    store = tmp_path / "evidence.jsonl"
    store.write_text(json.dumps({
        "evidence_id": "ev-durable",
        "source_session_id": "session-durable",
        "source_kind": "user_correction",
        "scope": "personal",
        "summary": "Durable context says prior corrections must be applied before action.",
        "sanitized_sample": "durable correction summary",
        "sensitivity": "internal",
        "applies_to": ["context_share"],
        "valid_from": "2026-06-06T00:00:00Z",
        "confidence": 0.91,
        "human_approved": True,
    }) + "\n", encoding="utf-8")

    evidence = load_durable_evidence(path=store)

    assert [item.evidence_id for item in evidence] == ["ev-durable"]
    assert evidence[0].summary.startswith("Durable context")
