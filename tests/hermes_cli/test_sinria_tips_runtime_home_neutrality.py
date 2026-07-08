import hermes_cli.tips as tips


def test_tip_corpus_neutralizes_selected_runtime_home_literals():
    joined = "\n".join(tips.TIPS)
    assert "SOUL.md at ~/.hermes/SOUL.md" not in joined
    assert "~/.hermes/checkpoints/" not in joined
    assert "Cron scripts live in ~/.hermes/scripts/" not in joined
    assert "~/.hermes/interrupt_debug.log" not in joined
    assert "~/.hermes/dashboard-themes/" not in joined
    assert "~/.hermes/dashboard-plugins/" not in joined
    assert "~/.hermes/cache/piper-voices/" not in joined
    assert "~/.hermes/config.yaml" not in joined


def test_rendered_tip_keeps_runtime_home_neutral_phrase(monkeypatch):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    rendered = tips._render_tip(
        "chat --ignore-user-config skips the runtime-home config.yaml — reproducible bug reports and CI runs."
    )

    assert "runtime-home config.yaml" in rendered
    assert "~/.hermes/config.yaml" not in rendered
