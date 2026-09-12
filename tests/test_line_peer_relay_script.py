"""HTTP smoke for the member-side LINE peer relay entrypoint."""
from __future__ import annotations

import importlib.util
import base64
import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sinria-line-peer-relay.py"
_PURPOSE_TOKEN = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


def _load():
    spec = importlib.util.spec_from_file_location("sinria_line_peer_relay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload():
    return {
        "schemaVersion": "sinria.line-peer.v1",
        "memberId": "member_kikuchi",
        "instanceId": "inst_kikuchi_local",
        "conversationRef": "sha256:" + "a" * 64,
        "messageRef": "sha256:" + "b" * 64,
        "sourceType": "user",
        "senderRef": "sha256:" + "c" * 64,
        "message": "relay smoke",
        "rawContextStored": False,
        "externalActionAllowed": False,
    }


def test_http_relay_requires_purpose_token_and_returns_verified_receipt(tmp_path):
    module = _load()
    server = module.create_server(
        host="127.0.0.1", port=0, relay_token=_PURPOSE_TOKEN,
        member_id="member_kikuchi", instance_id="inst_kikuchi_local",
        local_api_url="http://127.0.0.1:8642", local_api_key="local-key",
        state_path=tmp_path / "relay.sqlite3",
        request_fn=lambda **_: {"choices": [{"message": {"content": "peer answer"}}]},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/line-peer-relay"
    body = json.dumps(_payload()).encode()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/healthz", timeout=3) as response:
            health = json.loads(response.read())
        assert health == {"ok": True, "service": "sinria-line-peer-relay", "rawContextStored": False}

        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url, data=body, method="POST", headers={"Content-Type": "application/json"}), timeout=3)
        assert exc.value.code == 401

        request = Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {_PURPOSE_TOKEN}", "Content-Type": "application/json"
        })
        with urlopen(request, timeout=3) as response:
            receipt = json.loads(response.read())
        assert receipt["response"] == "peer answer"
        assert receipt["memberId"] == "member_kikuchi"
        assert receipt["rawContextStored"] is False
        assert receipt["externalActionPerformed"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_relay_rejects_non_loopback_bind(tmp_path):
    module = _load()
    with pytest.raises(ValueError, match="loopback"):
        module.create_server(
            host="0.0.0.0", port=0, relay_token=_PURPOSE_TOKEN,
            member_id="member_kikuchi", instance_id="inst_kikuchi_local",
            local_api_url="http://127.0.0.1:8642", local_api_key="local-key",
            state_path=tmp_path / "relay.sqlite3",
        )


def test_http_relay_rejects_weak_purpose_token(tmp_path):
    module = _load()
    with pytest.raises(ValueError, match="at least 32"):
        module.create_server(
            host="127.0.0.1", port=0, relay_token="too-short",
            member_id="member_kikuchi", instance_id="inst_kikuchi_local",
            local_api_url="http://127.0.0.1:8642", local_api_key="local-key",
            state_path=tmp_path / "relay.sqlite3",
        )


def test_check_loads_profile_env_without_printing_credentials(tmp_path, monkeypatch, capsys):
    module = _load()
    home = tmp_path / ".sinria"
    home.mkdir()
    (home / ".env").write_text(
        f"SINRIA_LINE_PEER_RELAY_TOKEN={_PURPOSE_TOKEN}\n"
        "SINRIA_MEMBER_ID=member_kikuchi\n"
        "SINRIA_INSTANCE_ID=inst_kikuchi_local\n"
        "SINRIA_LOCAL_API_URL=http://127.0.0.1:8642\n"
        "SINRIA_LOCAL_API_KEY=local-secret\n"
    )
    for name in (
        "SINRIA_LINE_PEER_RELAY_TOKEN", "SINRIA_MEMBER_ID", "SINRIA_INSTANCE_ID",
        "SINRIA_LOCAL_API_URL", "SINRIA_LOCAL_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "get_sinria_home", lambda: home)
    probes = []
    monkeypatch.setattr(
        module,
        "probe_line_peer_local_api",
        lambda url, key: probes.append((url, key)) or {"ok": True},
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--check"])

    assert module.main() == 0
    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert _PURPOSE_TOKEN not in output
    assert "local-secret" not in output
    assert probes == [("http://127.0.0.1:8642", "local-secret")]


def test_check_fails_closed_when_local_api_health_probe_fails(monkeypatch, capsys):
    module = _load()
    values = {
        "SINRIA_LINE_PEER_RELAY_TOKEN": _PURPOSE_TOKEN,
        "SINRIA_MEMBER_ID": "member_kikuchi",
        "SINRIA_INSTANCE_ID": "inst_kikuchi_local",
        "SINRIA_LOCAL_API_URL": "http://127.0.0.1:8642",
        "SINRIA_LOCAL_API_KEY": "local-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        module,
        "probe_line_peer_local_api",
        lambda *_: (_ for _ in ()).throw(module.LinePeerRelayError("private detail")),
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--check"])

    assert module.main() == 2
    output = capsys.readouterr().out
    assert "local_api_unavailable" in output
    assert "private detail" not in output
    assert "local-secret" not in output


def test_check_rejects_non_loopback_local_api(monkeypatch, capsys):
    module = _load()
    values = {
        "SINRIA_LINE_PEER_RELAY_TOKEN": _PURPOSE_TOKEN,
        "SINRIA_MEMBER_ID": "member_kikuchi",
        "SINRIA_INSTANCE_ID": "inst_kikuchi_local",
        "SINRIA_LOCAL_API_URL": "https://external.invalid",
        "SINRIA_LOCAL_API_KEY": "local-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--check"])

    assert module.main() == 2
    output = capsys.readouterr().out
    assert "invalid_local_api_url" in output
    assert _PURPOSE_TOKEN not in output
