from pathlib import Path


def test_conversation_loop_applies_practical_completion_guard_after_plugins_before_outcome_recording():
    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")

    guard_index = source.rindex("apply_practical_completion_guard")
    transform_index = source.index('"transform_llm_output"')
    post_hook_index = source.index('"post_llm_call"')
    outcome_index = source.index("record_practical_outcome_and_candidates")

    assert transform_index < guard_index < post_hook_index < outcome_index
    assert "original_user_message" in source[guard_index: guard_index + 600]
    assert "tool_turn_count=_turn_tool_count" in source[guard_index: guard_index + 800]
