"""Source-level wiring assertions (P1 Task C precedent:
tests/agent/test_model_routing.py::test_loop_wires_routing_signal...)."""

from pathlib import Path


def test_agent_init_configures_strategist_after_escalation_model():
    source = Path("agent/agent_init.py").read_text(encoding="utf-8")
    escalation = source.index("agent.escalation_model")
    strategist = source.index("configure_strategist")
    assert escalation < strategist, "strategist config belongs with model routing config"


def test_loop_injects_plan_after_user_message_append():
    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    anchor = source.index("_persist_user_message_idx = current_turn_user_idx")
    plan = source.index("maybe_request_plan")
    marker = source.index("_strategist_plan")
    assert anchor < plan < marker, "plan side-call must follow the user-message append"


def test_loop_adds_strategist_guidance_inside_verify_nudge():
    source = Path("agent/conversation_loop.py").read_text(encoding="utf-8")
    nudge = source.index("should_nudge_verification")
    correction = source.index("maybe_request_correction")
    synthetic = source.index("_verify_after_act_synthetic")
    assert nudge < correction < synthetic, (
        "correction side-call must run when the nudge fires, before the "
        "nudge message is appended"
    )
