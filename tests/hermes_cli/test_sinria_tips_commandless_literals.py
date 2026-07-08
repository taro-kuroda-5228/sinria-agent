import hermes_cli.tips as tips


def test_tip_corpus_neutralizes_selected_command_prefixed_literals():
    joined = "\n".join(tips.TIPS)
    assert 'hermes status --deep runs deeper diagnostic checks across all components.' not in joined
    assert 'hermes -z "<prompt>" is the purest one-shot' not in joined
    assert 'hermes chat --pass-session-id' not in joined
    assert 'hermes chat --image path/to/pic.png' not in joined
    assert 'hermes dump --show-keys' not in joined
    assert 'hermes fallback manages' not in joined
    assert 'hermes pairing rotates' not in joined


def test_rendered_sinria_tip_keeps_commandless_literal_readable(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    rendered = tips._render_tip('chat --pass-session-id injects the session ID into the system prompt so the agent can self-reference it.')

    assert rendered.startswith('chat --pass-session-id')
    assert 'hermes chat --pass-session-id' not in rendered
