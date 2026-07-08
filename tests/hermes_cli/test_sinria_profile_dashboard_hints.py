from types import SimpleNamespace


def test_resume_session_summary_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    class FakeDB:
        def get_session(self, target):
            return {"message_count": 2, "input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}

        def get_session_title(self, target):
            return "My Session"

        def close(self):
            pass

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_read_tui_active_session_file", lambda path: None)
    monkeypatch.setattr("hermes_state.SessionDB", FakeDB)
    main._print_tui_exit_summary("sess_123", "/tmp/active-session")

    out = capsys.readouterr().out
    assert "sinria --tui --resume sess_123" in out
    assert 'sinria --tui -c "My Session"' in out
    assert "hermes --tui --resume" not in out



def test_curator_notice_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("agent.curator.is_enabled", lambda: True)
    monkeypatch.setattr("agent.curator.load_state", lambda: {})
    monkeypatch.setattr("agent.curator.get_interval_hours", lambda: 72)
    main._print_curator_first_run_notice()

    out = capsys.readouterr().out
    assert "sinria curator run --dry-run" in out
    assert "sinria curator pause" in out
    assert "hermes curator" not in out



def test_dashboard_restart_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_find_stale_dashboard_pids", lambda: [1234])
    monkeypatch.setattr(main.sys, "platform", "win32")

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: Result())
    main._kill_stale_dashboard_processes(reason="test")

    out = capsys.readouterr().out
    assert "sinria dashboard --port <port>" in out
    assert "hermes dashboard --port <port>" not in out



def test_profile_alias_hint_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr("hermes_cli.profiles.check_alias_collision", lambda name: "collision")
    monkeypatch.setattr("hermes_cli.profiles.create_profile", lambda **kwargs: __import__('pathlib').Path("/tmp/profile"))
    monkeypatch.setattr("hermes_cli.profiles.seed_profile_skills", lambda profile_dir: None)
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")

    args = SimpleNamespace(
        profile_action="create",
        profile_name="work",
        clone=False,
        clone_all=False,
        no_alias=False,
        no_skills=False,
        clone_from=None,
    )

    main.cmd_profile(args)
    out = capsys.readouterr().out
    assert "sinria profile alias work --name <custom>" in out
    assert "sinria -p work chat" in out
    assert "hermes profile alias work --name <custom>" not in out



def test_dashboard_status_uses_sinria(monkeypatch, capsys):
    import hermes_cli.main as main

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setattr(main, "_find_stale_dashboard_pids", lambda: [])

    count = main._report_dashboard_status()

    assert count == 0
    out = capsys.readouterr().out
    assert "No sinria dashboard processes running." in out
    assert "No hermes dashboard processes running." not in out
