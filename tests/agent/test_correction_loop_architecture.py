from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = (
    "agent/conversation_loop.py",
    "agent/tool_executor.py",
    "run_agent.py",
    "cron/scheduler.py",
    "tools/approval.py",
)


def test_correction_record_rejects_execution_control_fields():
    from agent.correction_loop.records import CorrectionRecord

    payload = {
        "correction_id": "corr-1",
        "fingerprint": "fp-1",
        "scope": "tooling",
        "trigger_signature": ["patch", "toolerror"],
        "mistake_class": "tool_failure",
        "fix_steps": ["retry with a focused file edit"],
        "verification_steps": ["read back the changed lines"],
        "evidence_refs": ["turn-1"],
        "confidence": "high",
        "created_at": "2026-08-05T00:00:00+00:00",
        "deny": True,
    }

    with pytest.raises(ValueError, match="execution-control field"):
        CorrectionRecord.from_mapping(payload)


def test_correction_retrieval_failure_is_non_blocking():
    from agent.correction_loop.retrieval import retrieve_advice

    def broken_loader():
        raise PermissionError("unavailable")

    assert retrieve_advice("edit and verify the file", loader=broken_loader) == ()


def test_correction_advice_explicitly_has_no_execution_authority(monkeypatch):
    from agent.correction_loop import advice
    from agent.correction_loop.records import CorrectionRecord

    record = CorrectionRecord(
        correction_id="corr-stale",
        fingerprint="fp-stale",
        scope="tooling",
        trigger_signature=("edit", "file"),
        mistake_class="stale_prior_correction",
        checks=("Check the relevant prior mistake without changing the current request.",),
        fix_steps=("Apply a compatible method improvement.",),
        verification_steps=("Verify the requested edit completed.",),
        evidence_refs=("turn-old",),
        confidence="high",
        created_at="2026-08-05T00:00:00+00:00",
    )
    monkeypatch.setattr(advice, "load_correction_records", lambda: (record,))

    block = advice.format_correction_advice("edit the file")
    assert "cannot deny, block, delay, require approval" in block
    assert "execute the current request" in block


def test_active_runtime_has_no_context_share_gate_or_resolver_import():
    for relative in RUNTIME_FILES:
        source = (ROOT / relative).read_text(encoding="utf-8").lower()
        assert "agent.context_share" not in source, relative
        assert "context share resolver" not in source, relative
        assert "_context_share_pre_action" not in source, relative


def test_correction_advice_is_connected_to_the_current_turn_user_injection():
    source = (ROOT / "agent/conversation_loop.py").read_text(encoding="utf-8")

    assert "from agent.correction_loop.advice import format_correction_advice" in source
    assert "format_correction_advice(user_message)" in source
    assert "fail-open and has no execution-policy authority" in source


def test_legacy_context_share_package_is_absent():
    assert not (ROOT / "agent/context_share").exists()


def test_verify_nudges_use_correction_store(tmp_path: Path):
    from agent.correction_loop.outcome_gap import verify_nudges_path

    assert verify_nudges_path(tmp_path) == tmp_path / "corrections" / "verify_nudges.jsonl"
