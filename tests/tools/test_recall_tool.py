"""Tests for the on-demand recall_context tool (architecture-centric P0, Task 4)."""

import json

from hermes_constants import get_sinria_home


def _seed_evidence():
    store = get_sinria_home() / "context_share" / "evidence.jsonl"
    store.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "evidence_id": "ev-medspot-lock",
            "source_session_id": "session-a",
            "source_kind": "user_correction",
            "scope": "project",
            "summary": "MedSpot work resolves to the medspot repo, not Company OS.",
            "sanitized_sample": "medspot source lock",
            "sensitivity": "internal",
            "applies_to": ["medspot", "source_lock"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "confidence": 0.9,
            "human_approved": True,
        },
        {
            "evidence_id": "ev-verify-completion",
            "source_session_id": "session-b",
            "source_kind": "policy",
            "scope": "workspace",
            "summary": "Verify the real workflow before claiming completion of medspot deploys.",
            "sanitized_sample": "practical completion",
            "sensitivity": "internal",
            "applies_to": ["practical_completion", "medspot"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "confidence": 0.8,
            "human_approved": True,
        },
        {
            "evidence_id": "ev-expired",
            "source_session_id": "session-c",
            "source_kind": "decision",
            "scope": "personal",
            "summary": "Old medspot rule that expired long ago.",
            "sanitized_sample": "expired rule",
            "sensitivity": "internal",
            "applies_to": ["medspot"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-02-01T00:00:00+00:00",
            "confidence": 0.9,
            "human_approved": True,
        },
    ]
    with store.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _seed_memory():
    memories = get_sinria_home() / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    (memories / "MEMORY.md").write_text(
        "- MedSpot productionization is the current P0.\n"
        "- Unrelated line about something else.\n",
        encoding="utf-8",
    )


def test_recall_returns_matching_active_evidence_sorted():
    from tools.recall_tool import recall_context

    _seed_evidence()
    result = json.loads(recall_context(query="medspot"))
    assert result["success"] is True
    ids = [row["evidence_id"] for row in result["evidence"]]
    assert "ev-medspot-lock" in ids
    assert "ev-verify-completion" in ids
    assert "ev-expired" not in ids  # expired evidence never surfaces
    scores = [row["score"] for row in result["evidence"]]
    assert scores == sorted(scores, reverse=True)


def test_recall_includes_memory_lines_case_insensitive():
    from tools.recall_tool import recall_context

    _seed_memory()
    result = json.loads(recall_context(query="MEDSPOT"))
    assert result["success"] is True
    assert any("MedSpot productionization" in line for line in result["memory_lines"])
    assert not any("Unrelated line" in line for line in result["memory_lines"])


def test_recall_empty_query_fails_cleanly():
    from tools.recall_tool import recall_context

    result = json.loads(recall_context(query="   "))
    assert result["success"] is False


def test_recall_missing_stores_returns_empty_lists():
    from tools.recall_tool import recall_context

    result = json.loads(recall_context(query="anything"))
    assert result["success"] is True
    assert result["evidence"] == []
    assert result["memory_lines"] == []


def test_recall_respects_max_results():
    from tools.recall_tool import recall_context

    _seed_evidence()
    result = json.loads(recall_context(query="medspot", max_results=1))
    assert len(result["evidence"]) == 1


def test_recall_tool_is_registered_in_core_toolsets():
    import toolsets

    assert "recall_context" in toolsets._HERMES_CORE_TOOLS
    assert "recall_context" in toolsets.TOOLSETS["memory"]["tools"]
