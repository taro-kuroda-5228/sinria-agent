"""Sanitized, tool-backed completion evidence from Sinria in Chrome."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

_ALLOWED_ACTION_TYPES = frozenset(
    {
        "click",
        "type",
        "select",
        "check",
        "focus",
        "scroll_into_view",
        "keypress",
        "navigate",
        "back",
        "forward",
        "reload",
        "open_tab",
        "close_tab",
        "activate_tab",
        "choose_file",
        "readback",
    }
)
_RECEIPT_ID = re.compile(r"^[A-Za-z0-9:_-]{8,160}$")
_MAX_RECEIPTS = 4


def sanitize_browser_receipts(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    receipts: list[dict[str, Any]] = []
    for item in value[:_MAX_RECEIPTS]:
        if not isinstance(item, dict) or item.get("verified") is not True:
            continue
        receipt_id = str(item.get("receipt_id") or "")
        action_type = str(item.get("action_type") or "")
        if not _RECEIPT_ID.fullmatch(receipt_id) or action_type not in _ALLOWED_ACTION_TYPES:
            continue
        receipts.append(
            {
                "receipt_id": receipt_id,
                "action_type": action_type,
                "verified": True,
                "readback_label": str(
                    item.get("readback_label") or "Browser action readback"
                )[:240],
            }
        )
    return tuple(receipts)


def build_browser_receipt_messages(
    receipts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for receipt in sanitize_browser_receipts(tuple(receipts)):
        call_id = f"browser_receipt_{receipt['receipt_id']}"
        tool_name = "sinria_chrome_browser_action"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(
                                    {
                                        "action_type": receipt["action_type"],
                                        "receipt_id": receipt["receipt_id"],
                                    },
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": json.dumps(
                        {
                            "success": True,
                            "readback_label": receipt["readback_label"],
                            "completion_receipt": {
                                "success": True,
                                "complete": True,
                                "evidence_id": receipt["receipt_id"],
                                "stage": "validated",
                            },
                        },
                        separators=(",", ":"),
                    ),
                },
            ]
        )
    return messages
