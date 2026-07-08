"""Verified-trajectory capture for the distillation loop (P2, Task A)."""

import json

from hermes_constants import get_sinria_home

from agent.training_capture import capture_verified_trajectory


def _messages():
    return [
        {"role": "system", "content": "You are Sinria. MEMORY: private things."},
        {"role": "user", "content": "earlier unrelated request"},
        {"role": "assistant", "content": "earlier unrelated answer"},
        {"role": "user", "content": "Fix the failing build now"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "terminal", "arguments": "{\"command\": \"make test\"}"},
                }
            ],
        },
        {"role": "tool", "name": "terminal", "tool_call_id": "call_1", "content": "tests passed"},
        {"role": "user", "content": "nudge", "_verify_after_act_synthetic": True},
        {"role": "assistant", "content": "Verified: the test suite passed."},
    ]


def _capture(messages=None, user_message="Fix the failing build now"):
    return capture_verified_trajectory(
        messages=messages or _messages(),
        user_message=user_message,
        final_response="Verified: the test suite passed.",
        session_id="session-1",
        model="qwen2.5-7b",
        provider="custom",
        tier="small",
    )


def test_capture_writes_final_turn_slice_only():
    path = _capture()
    assert path is not None and path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    roles = [t["role"] for t in data["turns"]]
    assert "system" not in roles
    contents = json.dumps(data["turns"], ensure_ascii=False)
    assert "earlier unrelated" not in contents  # prior turns never captured
    assert "MEMORY" not in contents
    assert data["meta"]["model"] == "qwen2.5-7b"
    assert data["meta"]["tier"] == "small"


def test_synthetic_scaffolding_dropped():
    path = _capture()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert not any(t.get("_verify_after_act_synthetic") for t in data["turns"])
    assert "nudge" not in json.dumps(data["turns"])


def test_index_row_is_metadata_only():
    _capture()
    index = get_sinria_home() / "training" / "trajectories" / "index.jsonl"
    row = json.loads(index.read_text(encoding="utf-8").splitlines()[-1])
    assert set(row) <= {"timestamp", "session_id", "model", "provider", "tier", "path", "turns"}


def test_sensitive_content_rejects_whole_capture():
    messages = _messages()
    messages[3]["content"] = "Fix the build and email taro@example.com when done"
    path = _capture(messages=messages)
    assert path is None
    # Nothing with the address persisted anywhere under training/.
    training = get_sinria_home() / "training"
    if training.exists():
        for f in training.rglob("*.json*"):
            assert "taro@example.com" not in f.read_text(encoding="utf-8")


def test_raw_secret_never_persists():
    messages = _messages()
    # Placeholder shaped like a credential assignment (trips the sanitizer)
    # but deliberately NOT key-shaped, so secret scanners don't flag the repo.
    fake_value = "TEST_PLACEHOLDER_" + "VALUE"
    messages[5]["content"] = f"api_key={fake_value} loaded; tests passed"
    _capture(messages=messages)  # may capture redacted or reject — both fine
    training = get_sinria_home() / "training"
    if training.exists():
        for f in training.rglob("*.json*"):
            assert fake_value not in f.read_text(encoding="utf-8")


def test_loop_and_init_wiring():
    from pathlib import Path

    loop = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    assert "capture_verified_trajectory" in loop
    assert "training_capture_enabled" in loop
    init = Path("agent/agent_init.py").read_text(encoding="utf-8")
    assert "capture_verified_trajectories" in init
