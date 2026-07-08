#!/usr/bin/env python3
"""Sinria X research subagent wrapper.

Purpose: use Grok/x_search only for public or sanitized X research while keeping
Sinria's main model unchanged. This script never posts, replies, likes, DMs, or
performs external write actions. It records sanitized metadata-only audit events.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?:\+?81[-\s]?)?0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}")),
    ("postal_code_jp", re.compile(r"\b\d{3}-\d{4}\b")),
    ("clinical_private_keyword", re.compile(r"(患者|カルテ|病歴|診断書|検査結果|処方|保険証|同意書原本|PHI|PII)", re.I)),
    ("credential_keyword", re.compile(r"(api[_-]?key|token|secret|password|credential|認証情報|パスワード)", re.I)),
]


def sinria_home() -> Path:
    return Path(os.environ.get("SINRIA_HOME") or Path.home() / ".sinria")


def classify_query(query: str) -> dict[str, Any]:
    hits = [name for name, pattern in SENSITIVE_PATTERNS if pattern.search(query)]
    sensitivity = "public_or_sanitized" if not hits else "blocked_potential_sensitive"
    return {"sensitivity": sensitivity, "matched_categories": hits}


def audit(event: dict[str, Any]) -> None:
    log_dir = sinria_home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "x_research_subagent_audit.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sinria public/sanitized X research subagent")
    parser.add_argument("query", help="Public or sanitized X research query")
    parser.add_argument("--allowed-handle", action="append", default=[], help="Only search these X handles; repeatable")
    parser.add_argument("--excluded-handle", action="append", default=[], help="Exclude these X handles; repeatable")
    parser.add_argument("--from-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--to-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--image", action="store_true", help="Enable image understanding")
    parser.add_argument("--video", action="store_true", help="Enable video understanding")
    args = parser.parse_args(argv)

    query = args.query.strip()
    qhash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    classification = classify_query(query)
    base_event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": "sinria_x_research_subagent",
        "query_sha256_16": qhash,
        "side_effect": "read_only",
        "provider_class": "xai_grok_x_search",
        "external_action_performed": False,
        "raw_query_logged": False,
        **classification,
    }

    if classification["sensitivity"] != "public_or_sanitized":
        event = {**base_event, "egress_decision": "blocked_sanitize_first", "success": False}
        audit(event)
        print(json.dumps({
            "success": False,
            "error_type": "SensitiveQueryBlocked",
            "cause": "Query appears to contain private/sensitive data categories.",
            "matched_categories": classification["matched_categories"],
            "risk": "Raw private, patient, credential, or identifiable data must not be sent to X/xAI.",
            "next_choices": [
                "Rewrite as a public/sanitized X search query.",
                "Ask Sinria to summarize locally first, then search only public keywords.",
            ],
            "external_action_performed": False,
        }, ensure_ascii=False))
        return 2

    try:
        from tools.x_search_tool import check_x_search_requirements, x_search_tool
    except Exception as exc:
        event = {**base_event, "egress_decision": "not_attempted_import_failed", "success": False}
        audit(event)
        print(json.dumps({
            "success": False,
            "error_type": type(exc).__name__,
            "cause": "Sinria x_search tool could not be imported.",
            "stop_point": "before external request",
            "next_action": "Run from the Sinria repo/venv and verify tools.x_search_tool.",
            "external_action_performed": False,
        }, ensure_ascii=False))
        return 3

    if not check_x_search_requirements():
        event = {**base_event, "egress_decision": "not_attempted_auth_missing", "success": False}
        audit(event)
        print(json.dumps({
            "success": False,
            "error_type": "XaiOAuthMissing",
            "cause": "xAI/Grok OAuth or XAI_API_KEY is not configured, so x_search is not available yet.",
            "stop_point": "before external request",
            "next_user_action": "Run `sinria model` and choose xAI Grok OAuth/SuperGrok, or configure XAI_API_KEY if approved.",
            "main_model_unchanged": True,
            "external_action_performed": False,
        }, ensure_ascii=False))
        return 4

    event = {**base_event, "egress_decision": "allowed_public_or_sanitized", "success": None}
    try:
        result = x_search_tool(
            query=query,
            allowed_x_handles=args.allowed_handle,
            excluded_x_handles=args.excluded_handle,
            from_date=args.from_date,
            to_date=args.to_date,
            enable_image_understanding=args.image,
            enable_video_understanding=args.video,
        )
        parsed = json.loads(result)
        event["success"] = bool(parsed.get("success"))
        event["credential_source"] = parsed.get("credential_source")
        event["model"] = parsed.get("model")
        audit(event)
        print(result)
        return 0 if parsed.get("success") else 5
    except Exception as exc:
        event["success"] = False
        event["error_type"] = type(exc).__name__
        audit(event)
        print(json.dumps({
            "success": False,
            "error_type": type(exc).__name__,
            "cause": str(exc),
            "stop_point": "x_search execution",
            "external_action_performed": False,
        }, ensure_ascii=False))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
