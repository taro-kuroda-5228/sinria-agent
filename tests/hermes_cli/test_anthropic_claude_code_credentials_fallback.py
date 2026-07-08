"""Claude Code subscription credential resolution edge cases."""

import json


def test_read_claude_code_credentials_prefers_valid_file_when_keychain_expired(monkeypatch, tmp_path):
    """An expired macOS Keychain record must not shadow a valid ~/.claude file.

    Claude Code can leave an older Keychain credential while the file-backed
    credential has already been refreshed. Sinria should use the valid,
    refreshable credential instead of failing Anthropic auth.
    """
    from agent import anthropic_adapter as adapter

    monkeypatch.setattr(
        adapter,
        "_read_claude_code_credentials_from_keychain",
        lambda: {
            "accessToken": "cc-expired-keychain",
            "refreshToken": "cc-expired-refresh",
            "expiresAt": 1,
            "source": "macos_keychain",
        },
    )
    monkeypatch.setattr(adapter.Path, "home", lambda: tmp_path)

    cred_dir = tmp_path / ".claude"
    cred_dir.mkdir()
    (cred_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "cc-valid-file",
                    "refreshToken": "cc-valid-refresh",
                    "expiresAt": 9999999999999,
                }
            }
        ),
        encoding="utf-8",
    )

    creds = adapter.read_claude_code_credentials()

    assert creds["accessToken"] == "cc-valid-file"
    assert creds["source"] == "claude_code_credentials_file"


def test_resolve_anthropic_token_uses_valid_file_when_keychain_expired(monkeypatch, tmp_path):
    """Runtime resolver should recover from stale Keychain credentials."""
    from agent import anthropic_adapter as adapter

    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        adapter,
        "_read_claude_code_credentials_from_keychain",
        lambda: {
            "accessToken": "cc-expired-keychain",
            "refreshToken": "cc-expired-refresh",
            "expiresAt": 1,
            "source": "macos_keychain",
        },
    )
    monkeypatch.setattr(adapter.Path, "home", lambda: tmp_path)

    cred_dir = tmp_path / ".claude"
    cred_dir.mkdir()
    (cred_dir / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "cc-valid-file",
                    "refreshToken": "cc-valid-refresh",
                    "expiresAt": 9999999999999,
                }
            }
        ),
        encoding="utf-8",
    )

    assert adapter.resolve_anthropic_token() == "cc-valid-file"
