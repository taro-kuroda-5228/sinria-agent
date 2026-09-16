"""Read-only tool for sanitized local LINE conversation memory."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agent.line_conversation_memory import LineConversationMemoryStore
from tools.registry import registry


def _iso_to_epoch_ms(
    value: str | None, *, end_of_day: bool = False, time_zone: str = "Asia/Tokyo"
) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    date_only = len(text) == 10
    if date_only:
        text += "T00:00:00"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(time_zone))
    if date_only and end_of_day:
        parsed += timedelta(days=1)
    return int(parsed.timestamp() * 1000)


def line_conversation_search(
    query: str = "",
    limit: int = 10,
    task_id: str | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
    author_member_id: str | None = None,
    group_ref: str | None = None,
    memory_kinds: list[str] | None = None,
    match: str = "any",
    time_zone: str = "Asia/Tokyo",
) -> str:
    query = str(query or "").strip()
    if not query and not any((since, until, author_member_id, group_ref, memory_kinds)):
        return json.dumps({"success": False, "error": "query or scope filter is required"}, ensure_ascii=False)
    bounded_limit = max(1, min(int(limit or 10), 20))
    try:
        after_ms = _iso_to_epoch_ms(since, time_zone=time_zone)
        before_ms = _iso_to_epoch_ms(until, end_of_day=True, time_zone=time_zone)
        with LineConversationMemoryStore() as store:
            store.purge_expired()
            results = store.search(
                query,
                limit=bounded_limit,
                after_ms=after_ms,
                before_ms=before_ms,
                author_member_id=author_member_id,
                group_ref=group_ref,
                memory_kinds=memory_kinds,
                match=match,
            )
            coverage = store.coverage()
    except (OSError, sqlite3.Error, ValueError, OverflowError) as exc:
        return json.dumps(
            {"success": False, "error": f"local LINE memory unavailable: {type(exc).__name__}"},
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "success": True,
            "query": query,
            "limit": bounded_limit,
            "count": len(results),
            "results": results,
            "coverage": coverage,
            "scope": {
                "since": since, "until": until, "author_member_id": author_member_id,
                "group_ref": group_ref, "memory_kinds": memory_kinds, "match": match,
                "time_zone": time_zone,
            },
            "note": (
                "sanitized local summaries only; zero results do not prove a LINE message was absent; "
                "compare the requested time window with coverage; Company Knowledge status is separate"
            ),
        },
        ensure_ascii=False,
    )


LINE_CONVERSATION_SEARCH_SCHEMA = {
    "name": "line_conversation_search",
    "description": (
        "Search sanitized, local-only memory created from allowlisted LINE company conversations. "
        "Use only when prior LINE discussion is relevant. Results are selected summaries, not raw "
        "chat transcripts; durable Company Knowledge remains separately review-gated."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Task-relevant keywords. May be empty when scope filters are supplied."},
            "limit": {"type": "integer", "description": "Maximum results, 1-20 (default 10)."},
            "since": {"type": "string", "description": "Inclusive ISO-8601 date/time lower bound."},
            "until": {"type": "string", "description": "Exclusive ISO-8601 date/time upper bound; a date includes that whole day."},
            "author_member_id": {"type": "string", "description": "Verified local member identity filter, when known."},
            "group_ref": {"type": "string", "description": "Opaque group_ref returned by an earlier result."},
            "memory_kinds": {"type": "array", "items": {"type": "string"}, "description": "Optional memory kinds such as task, status, correction, or discussion."},
            "match": {"type": "string", "enum": ["any", "all"], "description": "Keyword matching mode; default any."},
            "time_zone": {"type": "string", "description": "IANA timezone for offset-free dates; default Asia/Tokyo."},
        },
    },
}


def _check() -> bool:
    return True


registry.register(
    name="line_conversation_search",
    toolset="memory",
    schema=LINE_CONVERSATION_SEARCH_SCHEMA,
    handler=lambda args, **kw: line_conversation_search(
        query=args.get("query", ""), limit=args.get("limit", 10), task_id=kw.get("task_id"),
        since=args.get("since"), until=args.get("until"),
        author_member_id=args.get("author_member_id"), group_ref=args.get("group_ref"),
        memory_kinds=args.get("memory_kinds"), match=args.get("match", "any"),
        time_zone=args.get("time_zone", "Asia/Tokyo"),
    ),
    check_fn=_check,
    emoji="💬",
)
