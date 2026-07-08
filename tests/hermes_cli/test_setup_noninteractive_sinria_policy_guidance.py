import hermes_cli.setup as setup



def test_noninteractive_setup_guidance_mentions_sinria_policy_profile(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    setup.print_noninteractive_setup_guidance("headless test")

    out = capsys.readouterr().out
    assert "sinria config set sinria.policy.active_profile dogfood_frontier" in out
    assert "Available Sinria policy profiles: dogfood_frontier, enterprise_guarded_cloud, sovereign_local_only" in out
    assert "Configure Sinria using environment variables or config commands:" in out
    assert "Configure Hermes using environment variables or config commands:" not in out
