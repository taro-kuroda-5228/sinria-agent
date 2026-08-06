"""On-demand recall over durable Correction Loop evidence and memory files.

Architecture-centric P0 (docs/plans/2026-07-06-architecture-centric-agent-os-p0.md,
Task 4): small-context models cannot afford inject-everything memory, so the
runtime exposes recall as a tool — the model retrieves prior corrections,
decisions, and memory lines when it needs them instead of carrying them in
every prompt.  Returns sanitized summaries only (the evidence store never
holds raw session text), and is strictly read-only.
"""

import json
import re

from tools.registry import registry

_ASCII_TERM_RE = re.compile(r"[a-z0-9_]{2,}")


def _memory_lines_matching(query_l: str, terms: list[str], max_lines: int) -> list[str]:
    from hermes_constants import get_sinria_home

    matched: list[str] = []
    memories = get_sinria_home() / "memories"
    for filename in ("MEMORY.md", "USER.md"):
        path = memories / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            line_l = stripped.lower()
            if any(term in line_l for term in terms) or (
                len(query_l) >= 2 and query_l in line_l
            ):
                matched.append(f"{filename}: {stripped}")
                if len(matched) >= max_lines:
                    return matched
    return matched


def recall_context(query: str, max_results: int = 6, task_id: str = None) -> str:
    """Search durable Correction Loop evidence + memory files for a query."""
    query = (query or "").strip()
    if not query:
        return json.dumps(
            {"success": False, "error": "query is required"}, ensure_ascii=False
        )
    max_results = max(1, min(int(max_results or 6), 20))

    from agent.correction_loop.evidence import EvidenceLedger
    from agent.correction_loop.storage import load_durable_evidence

    try:
        evidence_items = load_durable_evidence()
    except (ValueError, OSError):
        evidence_items = []

    ledger = EvidenceLedger(evidence_items)
    scored = ledger.search_scored(query)[:max_results]
    evidence_rows = [
        {
            "evidence_id": item.evidence_id,
            "summary": item.summary,
            "scope": item.scope,
            "applies_to": item.applies_to,
            "score": score,
        }
        for score, item in scored
    ]

    query_l = query.lower()
    terms = [t for t in _ASCII_TERM_RE.findall(query_l)]
    memory_lines = _memory_lines_matching(query_l, terms, max_lines=max_results)

    return json.dumps(
        {
            "success": True,
            "query": query,
            "evidence": evidence_rows,
            "memory_lines": memory_lines,
            "note": "sanitized summaries only; use memory tool to update entries",
        },
        ensure_ascii=False,
    )


registry.register(
    name="recall_context",
    toolset="memory",
    schema={
        "name": "recall_context",
        "description": (
            "Retrieve prior durable corrections, decisions, and memory lines "
            "relevant to a query, on demand. Use this when acting on a topic "
            "that may have prior user corrections or constraints that are not "
            "visible in the current context (small-context models: prefer this "
            "over relying on injected memory). Read-only; returns sanitized "
            "summaries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic, project, or keywords to recall (English or Japanese).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum evidence entries to return (default 6, max 20).",
                },
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: recall_context(
        query=args.get("query", ""),
        max_results=args.get("max_results", 6),
        task_id=kw.get("task_id"),
    ),
    emoji="🧠",
)
