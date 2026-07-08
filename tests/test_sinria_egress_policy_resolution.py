from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.sinria_egress import (
    SinriaEgressBlocked,
    _load_sinria_egress_config,
    guard_messaging_egress,
    guard_model_provider_egress,
    resolve_sinria_retention_policy,
)



def test_load_sinria_egress_config_applies_active_policy_profile(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "sinria": {
                "egress": {
                    "mode": "ask",
                    "confidential_external_send": "ask",
                    "redact_secrets_before_external_send": True,
                    "classify_lightweight": True,
                },
                "policy": {
                    "active_profile": "sovereign_local_only",
                    "profiles": {
                        "sovereign_local_only": {
                            "external_send": "block",
                            "confidential_external_send": "block",
                            "provider_trust": "local_only",
                        }
                    },
                },
            }
        },
    )

    cfg = _load_sinria_egress_config(SimpleNamespace())

    assert cfg["profile"] == "sovereign_local_only"
    assert cfg["mode"] == "block"
    assert cfg["confidential_external_send"] == "block"
    assert cfg["provider_trust"] == "local_only"



def test_resolve_sinria_retention_policy_uses_active_profile(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "sinria": {
                "policy": {
                    "active_profile": "sovereign_local_only",
                    "profiles": {
                        "sovereign_local_only": {
                            "retain_raw_history_locally": True,
                            "retain_sanitized_training_log": False,
                        }
                    },
                }
            }
        },
    )

    retention = resolve_sinria_retention_policy(
        {
            "sinria": {
                "policy": {
                    "active_profile": "sovereign_local_only",
                    "profiles": {
                        "sovereign_local_only": {
                            "retain_raw_history_locally": True,
                            "retain_sanitized_training_log": False,
                        }
                    },
                }
            }
        }
    )

    assert retention["profile"] == "sovereign_local_only"
    assert retention["retain_raw_history_locally"] is True
    assert retention["retain_sanitized_training_log"] is False



def test_explicit_agent_egress_config_overrides_policy_profile(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "sinria": {
                "egress": {"mode": "ask", "confidential_external_send": "ask"},
                "policy": {
                    "active_profile": "sovereign_local_only",
                    "profiles": {
                        "sovereign_local_only": {
                            "external_send": "block",
                            "confidential_external_send": "block",
                        }
                    },
                },
            }
        },
    )

    cfg = _load_sinria_egress_config(
        SimpleNamespace(sinria_egress_config={"mode": "allow", "profile": "dogfood_frontier"})
    )

    assert cfg["mode"] == "allow"
    assert cfg["profile"] == "dogfood_frontier"
    assert cfg["confidential_external_send"] == "block"



def test_guard_model_provider_uses_active_policy_profile_from_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "sinria": {
                "egress": {
                    "mode": "ask",
                    "confidential_external_send": "ask",
                    "redact_secrets_before_external_send": True,
                    "classify_lightweight": True,
                },
                "policy": {
                    "active_profile": "sovereign_local_only",
                    "profiles": {
                        "sovereign_local_only": {
                            "external_send": "block",
                            "confidential_external_send": "block",
                        }
                    },
                },
            }
        },
    )

    agent = SimpleNamespace(
        base_url="https://api.openai.com/v1",
        provider="openai",
        model="gpt-test",
        session_id="test-session",
        sinria_egress_audit_path=Path(tmp_path) / "sinria-egress-audit.jsonl",
    )

    with pytest.raises(SinriaEgressBlocked) as exc:
        guard_model_provider_egress(
            agent,
            [{"role": "user", "content": "confidential board memo"}],
        )

    assert exc.value.decision.action == "block"
    assert "confidential" in exc.value.decision.reason.lower()



def test_guard_messaging_uses_active_policy_profile_in_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "sinria": {
                "egress": {
                    "mode": "ask",
                    "confidential_external_send": "ask",
                    "redact_secrets_before_external_send": True,
                    "classify_lightweight": True,
                },
                "policy": {
                    "active_profile": "enterprise_guarded_cloud",
                    "profiles": {
                        "enterprise_guarded_cloud": {
                            "external_send": "ask",
                            "confidential_external_send": "block_unless_approved",
                        }
                    },
                },
            }
        },
    )

    audit_path = Path(tmp_path) / "audit.jsonl"
    with pytest.raises(SinriaEgressBlocked):
        guard_messaging_egress(
            "slack:#general",
            "confidential board memo",
            audit_path=audit_path,
        )

    audit = audit_path.read_text(encoding="utf-8")
    assert '"policy_profile": "enterprise_guarded_cloud"' in audit
