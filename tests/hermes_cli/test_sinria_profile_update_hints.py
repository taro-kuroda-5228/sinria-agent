from types import SimpleNamespace


def test_profile_use_default_uses_sinria_home(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("hermes_cli.profiles.set_active_profile", lambda name: None)
    monkeypatch.setattr("hermes_constants.display_hermes_home", lambda: "~/.sinria")

    main.cmd_profile(SimpleNamespace(profile_action="use", profile_name="default"))

    out = capsys.readouterr().out
    assert "Switched to: default (~/.sinria)" in out
    assert "Switched to: default (~/.hermes)" not in out



def test_dashboard_stop_no_processes_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_find_stale_dashboard_pids", lambda: [])

    try:
        main.cmd_dashboard(SimpleNamespace(stop=True, status=False))
    except SystemExit as exc:
        assert exc.code == 0

    out = capsys.readouterr().out
    assert "No sinria dashboard processes running." in out
    assert "No hermes dashboard processes running." not in out



def test_optional_backend_refresh_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("tools.lazy_deps.active_features", lambda: ["vision"])
    monkeypatch.setattr("tools.lazy_deps.refresh_active_features", lambda prompt=False: {"vision": "failed: upstream issue"})
    main._refresh_active_lazy_features()

    out = capsys.readouterr().out
    assert "`sinria update` once the upstream issue is resolved." in out
    assert "`hermes update` once the upstream issue is resolved." not in out



def test_profile_update_non_distribution_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main
    import hermes_cli.profile_distribution as pd
    import hermes_cli.profiles as profiles

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(profiles, "normalize_profile_name", lambda name: name)
    monkeypatch.setattr(profiles, "get_profile_dir", lambda name: "/tmp/fake-profile")
    monkeypatch.setattr(pd, "read_manifest", lambda path: None)

    try:
        main.cmd_profile(
            SimpleNamespace(profile_action="update", profile_name="demo", yes=True, force_config=False)
        )
    except SystemExit as exc:
        assert exc.code == 1

    out = capsys.readouterr().out
    assert "Only profiles installed via `sinria profile install` can be updated." in out
    assert "Only profiles installed via `hermes profile install` can be updated." not in out



def test_render_distribution_plan_uses_sinria_requirement_label(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")

    manifest = SimpleNamespace(
        name="demo",
        version="1.2.3",
        description="Example distribution",
        author="Tester",
        hermes_requires=">=0.1.0",
        env_requires=[],
        files=[],
        cron=None,
        mcp_servers=[],
    )
    plan = SimpleNamespace(
        manifest=manifest,
        provenance="local",
        target_dir="/tmp/demo",
        existing=False,
        env_example=False,
        env_requires=[],
        has_cron=False,
        files=[],
    )

    main._render_distribution_plan(plan)

    out = capsys.readouterr().out
    assert "Requires: Sinria >=0.1.0" in out
    assert "Requires: Hermes >=0.1.0" not in out
