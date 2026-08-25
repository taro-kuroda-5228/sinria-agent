import json

from agent.browser_receipts import build_browser_receipt_messages, sanitize_browser_receipts
from agent.correction_loop.outcome_gap import apply_practical_completion_guard


def test_sanitize_browser_receipts_accepts_only_verified_bounded_metadata():
    receipts = sanitize_browser_receipts([
        {
            "receipt_id": "wf-1:3:abc12345",
            "action_type": "keypress",
            "verified": True,
            "readback_label": "Search | Sales Navigator",
            "url": "https://example.invalid/secret",
        },
        {"receipt_id": "bad", "action_type": "click", "verified": False},
    ])
    assert receipts == ({
        "receipt_id": "wf-1:3:abc12345",
        "action_type": "keypress",
        "verified": True,
        "readback_label": "Search | Sales Navigator",
    },)
    assert "example.invalid" not in repr(receipts)


def test_browser_receipt_messages_are_tool_backed_completion_evidence():
    messages = build_browser_receipt_messages(({
        "receipt_id": "wf-1:3:abc12345",
        "action_type": "keypress",
        "verified": True,
        "readback_label": "Search | Sales Navigator",
    },))
    assert messages[0]["role"] == "assistant"
    call = messages[0]["tool_calls"][0]
    assert call["function"]["name"] == "sinria_chrome_browser_action"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == call["id"]
    payload = json.loads(messages[1]["content"])
    assert payload["completion_receipt"]["success"] is True
    assert payload["completion_receipt"]["complete"] is True


def test_verified_browser_readback_is_visible_to_practical_completion_guard():
    response = "Verified browser action: keypress\nReadback: Search | Sales Navigator\n調査完了。"
    guarded = apply_practical_completion_guard(
        user_message="Sales Navigatorで候補を検索してreadbackして",
        final_response=response,
        completed=True,
        interrupted=False,
        tool_turn_count=1,
    )
    assert guarded == response


def test_partial_completion_status_does_not_get_reclassified_as_success():
    response = "部分完了 / readback未確認。候補カードはまだ確認できていません。"
    guarded = apply_practical_completion_guard(
        user_message="Sales Navigatorで候補を12名検索して",
        final_response=response,
        completed=True,
        interrupted=False,
        tool_turn_count=0,
    )
    assert guarded == response
