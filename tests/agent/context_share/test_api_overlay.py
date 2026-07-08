from agent.conversation_loop import _append_single_context_share_overlay, _strip_context_share_sections


def test_overlay_replaces_existing_context_share_block():
    base = "System root\n\n## Context Share Resolver\n\nold resolver block\n- stale\n\n## Other Section\n\nkeep me"
    new_block = "## Context Share Resolver\n\nnew resolver block\n- current"

    result = _append_single_context_share_overlay(base, new_block)

    assert result.count("## Context Share Resolver") == 1
    assert "old resolver block" not in result
    assert "new resolver block" in result
    assert "## Other Section" in result


def test_overlay_does_not_duplicate_same_block():
    block = "## Context Share Resolver\n\ncurrent resolver block"
    result = _append_single_context_share_overlay(f"System root\n\n{block}", block)

    assert result.count("## Context Share Resolver") == 1
    assert result.endswith(block)


def test_ephemeral_gateway_resolver_is_stripped_before_append():
    gateway_ephemeral = "gateway context\n\n## Context Share Resolver\n\ngateway resolver block\n\n## Gateway Footer\n\nkeep footer"

    stripped = _strip_context_share_sections(gateway_ephemeral)

    assert "## Context Share Resolver" not in stripped
    assert "gateway resolver block" not in stripped
    assert "gateway context" in stripped
    assert "## Gateway Footer" in stripped
