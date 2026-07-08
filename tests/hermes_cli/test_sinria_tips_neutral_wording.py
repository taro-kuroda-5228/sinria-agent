import hermes_cli.tips as tips


def test_rendered_sinria_tip_avoids_hermes_branding_for_neutralized_tips(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    rendered = tips._render_tip("hermes dashboard --tui embeds the full agent TUI in your browser via xterm.js and a WebSocket PTY.")

    assert "sinria dashboard --tui" in rendered
    assert "full agent TUI" in rendered
    assert "Hermes TUI" not in rendered


def test_tip_corpus_neutralizes_selected_hermes_product_phrases():
    joined = "\n".join(tips.TIPS)
    assert "Hermes runs on 21 messaging platforms" not in joined
    assert "Hermes loads project context" not in joined
    assert "make Hermes your own" not in joined
    assert "Hermes auto-flushes important facts" not in joined
