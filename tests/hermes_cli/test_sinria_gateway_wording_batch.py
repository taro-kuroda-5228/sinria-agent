from pathlib import Path

import hermes_cli.gateway as gateway


def test_gateway_source_avoids_selected_hermes_runtime_literals():
    source = Path(gateway.__file__).read_text(encoding="utf-8")
    assert "Gateway subcommand for hermes CLI." not in source
    assert "Handles: hermes gateway" not in source
    assert "leaving ``hermes update`` users" not in source
    assert "only PIDs belonging to the current\n            Hermes profile are returned." not in source
    assert "Return running gateway PIDs mapped to Hermes profiles via PID files." not in source
    assert "For ``~/.hermes/profiles/<name>``" not in source
    assert "Default ``~/.hermes`` returns ``sinria-gateway``" not in source
    assert "Profile ``~/.hermes/profiles/coder`` returns ``sinria-gateway-coder``." not in source
    assert "Gateway subcommand for the local CLI." in source
    assert "Handles: <cli> gateway" in source
    assert "only PIDs belonging to the current\n            active profile are returned." in source
    assert "mapped to profiles via PID files" in source
    assert "runtime-home named profile" in source
    assert "default runtime home returns ``sinria-gateway``" in source


def test_gateway_source_avoids_selected_hermes_setup_literals():
    source = Path(gateway.__file__).read_text(encoding="utf-8")
    assert "so Hermes can't create it from this user session" not in source
    assert "``hermes setup gateway``" not in source
    assert "User-installed platform plugins under ~/.hermes/plugins/" not in source
    assert "Hermes will log in directly" not in source
    assert "where Hermes delivers cron results and notifications" not in source
    assert "Hermes connects via the BlueBubbles REST API" not in source
    assert "Hermes will connect automatically over WebSocket" not in source
    assert "runtime-home plugins directory" in source
    assert "_gateway_product_name()" in source
