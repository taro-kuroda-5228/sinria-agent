from agent.context_share.evidence import ContextEvidence
from agent.context_share.intent_resolver import IntentResolver


def _evidence(idx):
    return ContextEvidence(
        evidence_id=f"trace-{idx}",
        source_session_id=f"session-{idx}",
        source_kind="user_correction",
        scope="personal",
        summary=f"Context Share traceability constraint {idx}.",
        sanitized_sample=f"traceability {idx}",
        sensitivity="internal",
        applies_to=["context_share"],
        valid_from=f"2026-06-{idx:02d}T00:00:00Z",
        confidence=0.8,
        human_approved=True,
    )


def test_source_traceability_covers_every_retrieved_evidence_id():
    resolver = IntentResolver(default_evidence=[_evidence(idx) for idx in range(1, 12)], include_durable=False)
    resolution = resolver.resolve("Context Shareを改善して", project="sinria")
    prompt = resolution.format_for_prompt()

    assert len(resolution.retrieval_evidence_ids) == len(resolution.source_trace)
    for evidence_id in resolution.retrieval_evidence_ids:
        assert evidence_id in prompt
