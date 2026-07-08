import json
from datetime import datetime, timezone

from agent.context_share.extraction import (
    discover_session_evidence_candidates,
    extract_candidates_from_messages,
)
from agent.context_share.review_queue import approve_candidate, load_review_candidates, write_review_candidates
from agent.context_share.storage import load_durable_evidence


class FakeSessionDB:
    def __init__(self):
        self.queries = []

    def search_messages(self, query, role_filter=None, limit=20, offset=0, **kwargs):
        self.queries.append(query)
        if "Sinria" not in query and "コンテキスト" not in query:
            return []
        return [
            {
                "session_id": "session-context-share",
                "role": "user",
                "content": "Sinriaは過去の記録から意図を推論し、1回1回説明しなくても動くべき。HermesではなくSinriaとして振る舞って。",
                "timestamp": 1_780_000_000,
                "session_started": 1_780_000_000,
                "source": "discord",
            }
        ]


def test_extract_candidates_sanitizes_and_preserves_source_traceability():
    candidates = extract_candidates_from_messages([
        {
            "session_id": "session-1",
            "role": "user",
            "content": "SinriaはHermesではなくSinria。患者 MRN-123456 のような生情報は共有しない。",
            "timestamp": 1_780_000_000,
            "source": "discord",
        }
    ])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.evidence.source_session_id == "session-1"
    assert candidate.candidate_id.startswith("ctx-candidate-")
    assert candidate.evidence.evidence_id.startswith("ctx-ev-")
    assert not any(ch.isdigit() for ch in candidate.candidate_id)
    assert not any(ch.isdigit() for ch in candidate.evidence.evidence_id)
    assert "MRN-123456" not in candidate.evidence.summary
    assert candidate.approval_state == "proposed"
    assert candidate.raw_context_stored is False


def test_discovers_session_candidates_from_search_db_without_llm():
    db = FakeSessionDB()
    candidates = discover_session_evidence_candidates(db, limit_per_query=5)

    assert candidates
    assert candidates[0].evidence.source_session_id == "session-context-share"
    assert any("Sinria" in q or "コンテキスト" in q for q in db.queries)


def test_since_filter_excludes_out_of_window_messages():
    candidates = extract_candidates_from_messages([
        {
            "session_id": "old-session",
            "role": "user",
            "content": "Context Shareは過去の指摘を行動制約として自動適用する必要がある。",
            "timestamp": "2026-01-01T00:00:00Z",
            "source": "discord",
        },
        {
            "session_id": "new-session",
            "role": "user",
            "content": "Context Shareは過去の指摘を行動制約として自動適用する必要がある。",
            "timestamp": "2026-06-05T00:00:00Z",
            "source": "discord",
        },
    ], since="30d", now=datetime(2026, 6, 6, tzinfo=timezone.utc))

    assert [candidate.evidence.source_session_id for candidate in candidates] == ["new-session"]


def test_review_queue_approves_candidate_into_durable_evidence(tmp_path):
    candidates = extract_candidates_from_messages([
        {
            "session_id": "session-2",
            "role": "user",
            "content": "Context Shareは過去の指摘を行動制約として自動適用する必要がある。",
            "timestamp": 1_780_000_001,
            "source": "discord",
        }
    ])
    queue_path = tmp_path / "review_queue.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"

    write_review_candidates(candidates, path=queue_path)
    loaded = load_review_candidates(path=queue_path)
    approved = approve_candidate(loaded[0].candidate_id, queue_path=queue_path, evidence_path=evidence_path, reviewer="taro")

    durable = load_durable_evidence(path=evidence_path)
    assert approved.approval_state == "approved"
    assert durable[0].human_approved is True
    assert durable[0].source_session_id == "session-2"


def test_review_queue_loads_legacy_operational_candidates_without_crashing(tmp_path):
    queue_path = tmp_path / "review_queue.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "id": "ctx-candidate-legacy123",
                "kind": "outcome_gap",
                "source": "company-os-team-mode-monitor",
                "summary": "Local-only monitor found a stale script reference; keep repair review-gated.",
                "created_at": "2026-06-14T03:35:00Z",
                "risk_level": "internal",
                "raw_context_stored": False,
                "external_action_performed": False,
                "human_review_required": True,
                "durable_fix_suggestion": "Add regression coverage for local-only monitor scripts.",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    candidates = load_review_candidates(path=queue_path)

    assert [candidate.candidate_id for candidate in candidates] == ["ctx-candidate-legacy123"]
    assert candidates[0].evidence.source_session_id == "company-os-team-mode-monitor"
    assert candidates[0].evidence.source_kind == "repeated_failure"
    assert candidates[0].evidence.human_approved is False
    assert candidates[0].raw_context_stored is False
    assert candidates[0].external_action_performed is False
