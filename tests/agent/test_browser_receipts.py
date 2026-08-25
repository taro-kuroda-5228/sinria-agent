from agent.browser_receipts import build_browser_receipt_messages, sanitize_browser_receipts
from agent.correction_loop.outcome_gap import finalize_practical_completion
import json


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
    assert receipts == (
        {
            "receipt_id": "wf-1:3:abc12345",
            "action_type": "keypress",
            "verified": True,
            "readback_label": "Search | Sales Navigator",
        },
    )
    assert "example.invalid" not in repr(receipts)


def test_browser_receipt_messages_are_tool_backed_completion_evidence():
    messages = build_browser_receipt_messages((
        {
            "receipt_id": "wf-1:3:abc12345",
            "action_type": "keypress",
            "verified": True,
            "readback_label": "Search | Sales Navigator",
        },
    ))
    assert messages[0]["role"] == "assistant"
    call = messages[0]["tool_calls"][0]
    assert call["function"]["name"] == "sinria_chrome_browser_action"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == call["id"]
    payload = json.loads(messages[1]["content"])
    assert payload["completion_receipt"]["success"] is True
    assert payload["completion_receipt"]["complete"] is True
    assert payload["success"] is True


def test_browser_receipt_satisfies_practical_completion_guard():
    receipts = ({
        "receipt_id": "wf-1:3:abc12345",
        "action_type": "keypress",
        "verified": True,
        "readback_label": "Search | Sales Navigator",
    },)
    decision = finalize_practical_completion(
        user_message="Sales Navigatorで候補を検索してreadbackして",
        final_response="調査完了。候補10名を実画面から確認しました。",
        completed=True,
        interrupted=False,
        tool_turn_count=1,
        tool_names=("sinria_chrome_browser_action",),
        messages=[
            {"role": "user", "content": "continue"},
            *build_browser_receipt_messages(receipts),
        ],
    )
    assert decision["completed"] is True
    assert decision["completion_reason"] is None


def test_page_readback_receipt_can_verify_practical_completion():
    receipts = sanitize_browser_receipts([
        {
            "receipt_id": "readback:4:snapshot123",
            "action_type": "readback",
            "verified": True,
            "readback_label": "Search | Sales Navigator",
        }
    ])
    assert receipts and receipts[0]["action_type"] == "readback"
    messages = [{"role": "user", "content": "候補を検索してreadbackする"}]
    messages.extend(build_browser_receipt_messages(receipts))
    decision = finalize_practical_completion(
        user_message="候補を検索してreadbackする",
        final_response="実ページで候補を確認し、調査を完了しました。",
        completed=True,
        interrupted=False,
        tool_turn_count=0,
        messages=messages,
    )
    assert decision["completed"] is True
    assert decision["completion_reason"] is None


def test_pending_browser_action_is_not_wrapped_by_completion_guard():
    response = json.dumps(
        {
            "message": "候補1名を確認済み。次の候補を検索します。",
            "actions": [
                {
                    "type": "keypress",
                    "tabId": 441320252,
                    "ref": "e36",
                    "key": "Enter",
                }
            ],
        },
        ensure_ascii=False,
    )
    decision = finalize_practical_completion(
        user_message="Sales Navigatorで候補を12名検索して",
        final_response=response,
        completed=True,
        interrupted=False,
        tool_turn_count=0,
        messages=[{"role": "user", "content": "search"}],
    )
    assert decision["completed"] is True
    assert decision["completion_reason"] is None
    assert decision["final_response"] == response
