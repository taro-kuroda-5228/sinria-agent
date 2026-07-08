from types import SimpleNamespace



def test_sinria_show_config_displays_policy_profile(monkeypatch, capsys, tmp_path):
    import hermes_cli.config as config_mod

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    config = config_mod.load_config()
    config_mod.show_config()

    out = capsys.readouterr().out
    assert "◆ Sinria Policy" in out
    assert "Active:       dogfood_frontier" in out
    assert "Trust:        trusted_frontier" in out
    assert "External:     ask" in out
    assert "JSONL mirror: enabled" in out
    assert "Curator logs: enabled" in out
    assert "Boundary:     cloud_enhanced" in out
    assert "Data classes: public/internal/phi_pii/credential/classified" in out
    assert "Provider reg: local_vllm/openai_enterprise/anthropic_enterprise" in out
    assert "sinria config set sinria.policy.active_profile <profile>" in out



def test_sinria_status_displays_policy_profile(monkeypatch, capsys, tmp_path):
    from hermes_cli import status as status_mod
    import hermes_cli.auth as auth_mod
    import hermes_cli.gateway as gateway_mod

    monkeypatch.setenv("HERMES_CLI_NAME", "sinria")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(status_mod, "load_config", lambda: {
        "model": "",
        "sinria": {
            "policy": {
                "active_profile": "enterprise_guarded_cloud",
                "profiles": {
                    "enterprise_guarded_cloud": {
                        "external_send": "ask",
                        "confidential_external_send": "block_unless_approved",
                        "retain_raw_history_locally": False,
                        "retain_sanitized_training_log": False,
                    }
                },
            }
        },
    }, raising=False)
    monkeypatch.setattr(status_mod, "resolve_requested_provider", lambda requested=None: "openai-codex", raising=False)
    monkeypatch.setattr(status_mod, "resolve_provider", lambda requested=None, **kwargs: "openai-codex", raising=False)
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: "OpenAI Codex", raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_qwen_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_minimax_oauth_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(gateway_mod, "find_gateway_pids", lambda exclude_pids=None: [], raising=False)

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    out = capsys.readouterr().out
    assert "Policy:       enterprise_guarded_cloud" in out
    assert "Egress:       ask" in out
    assert "Confidential: block_unless_approved" in out
    assert "Transcript:   SQLite only" in out
    assert "Curator log:  disabled" in out
