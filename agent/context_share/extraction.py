"""Session-to-evidence extraction for Sinria Context Share v2.

This module intentionally uses deterministic, sanitized rules rather than an LLM.
Raw transcript text is never stored in candidates; only source pointers and fixed
constraint summaries are retained.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .evidence import ContextEvidence


@dataclass(frozen=True)
class EvidenceCandidate:
    candidate_id: str
    evidence: ContextEvidence
    approval_state: str = "proposed"
    approved_at: str | None = None
    raw_context_stored: bool = False
    external_action_performed: bool = False
    extraction_reason: str = "deterministic_prior_correction_rule"
    # Dedup/audit metadata: one row represents a correction *class*; repeated
    # occurrences bump the counter instead of appending duplicate rows.
    occurrence_count: int = 1
    last_seen_at: str | None = None
    merged_into: str | None = None
    approved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


@dataclass(frozen=True)
class ExtractionRule:
    keywords: tuple[str, ...]
    applies_to: tuple[str, ...]
    summary: str
    sanitized_sample: str
    scope: str = "personal"
    sensitivity: str = "internal"
    confidence: float = 0.86


_RULES = [
    ExtractionRule(
        keywords=("HermesではなくSinria", "Sinriaとして", "sinria", "hermes"),
        applies_to=("sinria", "identity", "sinria_identity"),
        summary="Use Sinria-native paths/labels and avoid Hermes residue in user-facing artifacts unless discussing legacy internals.",
        sanitized_sample="Sinria-native identity correction",
        confidence=0.94,
    ),
    ExtractionRule(
        keywords=("過去の記録", "意図を推論", "1回1回", "コンテキスト", "Context Share"),
        applies_to=("context_share", "self_improvement"),
        summary="Context Share must retrieve prior corrections and infer intent from durable records before action, without forcing repeated user restatement.",
        sanitized_sample="prior-correction intent inference requirement",
        confidence=0.92,
    ),
    ExtractionRule(
        keywords=("自己改善", "自律的", "修正", "self-improvement"),
        applies_to=("self_improvement", "skills", "memory"),
        summary="Self-improvement must convert repeated prior corrections into memory, skills, tests, and runbooks instead of one-off apologies.",
        sanitized_sample="self-improvement correction loop",
        confidence=0.9,
    ),
    ExtractionRule(
        keywords=("Team Mode", "Company OS", "組織", "metadata-only", "オンプレ"),
        applies_to=("team_mode", "company_os", "org_context"),
        summary="Team Mode shares metadata-only Company OS control-plane rows; raw/private context stays local/on-prem.",
        sanitized_sample="metadata-only Team Mode boundary",
        scope="org",
        confidence=0.9,
    ),
]


def _matches(rule: ExtractionRule, content: str) -> bool:
    content_l = content.lower()
    hits = 0
    for keyword in rule.keywords:
        if keyword.lower() in content_l:
            hits += 1
    # Require two weak keywords, or one strong Japanese/phrase keyword.
    return hits >= 2 or any(keyword in content for keyword in rule.keywords if len(keyword) >= 8)


def _safe_digest(text: str) -> str:
    """Return a compact opaque digest that cannot look like PII/phone digits."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return digest.translate(str.maketrans("0123456789", "abcdefghij"))


def _candidate_id(session_id: str, summary: str) -> str:
    return f"ctx-candidate-{_safe_digest(session_id + chr(10) + summary)}"


def _evidence_id(candidate_id: str) -> str:
    return candidate_id.replace("ctx-candidate-", "ctx-ev-")


def _message_time(message: dict[str, Any]) -> datetime | None:
    raw = message.get("timestamp") or message.get("session_started")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _parse_since_cutoff(since: str | None, *, now: datetime | None = None) -> datetime | None:
    if not since:
        return None
    base = now or datetime.now(timezone.utc)
    text = since.strip().lower()
    if text.endswith("d") and text[:-1].isdigit():
        return base - timedelta(days=int(text[:-1]))
    if text.endswith("h") and text[:-1].isdigit():
        return base - timedelta(hours=int(text[:-1]))
    try:
        parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        raise ValueError(f"unsupported since value: {since!r}; use 30d, 12h, or ISO timestamp")


def _within_since(message: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    message_time = _message_time(message)
    if message_time is None:
        return False
    return message_time >= cutoff


def extract_candidates_from_messages(messages: Iterable[dict[str, Any]], *, since: str | None = None, now: datetime | None = None) -> list[EvidenceCandidate]:
    cutoff = _parse_since_cutoff(since, now=now)
    candidates: dict[str, EvidenceCandidate] = {}
    for message in messages:
        if not _within_since(message, cutoff):
            continue
        if message.get("role") not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or message.get("snippet") or "")
        session_id = str(message.get("session_id") or "unknown-session")
        valid_from = str(message.get("timestamp") or "2026-06-06T00:00:00Z")
        for rule in _RULES:
            if not _matches(rule, content):
                continue
            cid = _candidate_id(session_id, rule.summary)
            candidates[cid] = EvidenceCandidate(
                candidate_id=cid,
                evidence=ContextEvidence(
                    evidence_id=_evidence_id(cid),
                    source_session_id=session_id,
                    source_kind="user_correction" if message.get("role") == "user" else "repeated_failure",
                    scope=rule.scope,  # type: ignore[arg-type]
                    summary=rule.summary,
                    sanitized_sample=rule.sanitized_sample,
                    sensitivity=rule.sensitivity,  # type: ignore[arg-type]
                    applies_to=list(rule.applies_to),
                    valid_from=valid_from,
                    confidence=rule.confidence,
                    human_approved=False,
                ),
            )
    return list(candidates.values())


def discover_session_evidence_candidates(db, *, limit_per_query: int = 20, since: str | None = None, now: datetime | None = None) -> list[EvidenceCandidate]:
    queries = [
        "Sinria OR Hermes OR コンテキスト OR 自己改善",
        "過去の記録 OR 意図を推論 OR Context Share",
        "Team Mode OR Company OS OR metadata-only",
    ]
    messages: list[dict[str, Any]] = []
    for query in queries:
        try:
            messages.extend(db.search_messages(query=query, role_filter=["user", "assistant"], limit=limit_per_query, offset=0))
        except TypeError:
            messages.extend(db.search_messages(query, role_filter=["user", "assistant"], limit=limit_per_query, offset=0))
    return extract_candidates_from_messages(messages, since=since, now=now)
