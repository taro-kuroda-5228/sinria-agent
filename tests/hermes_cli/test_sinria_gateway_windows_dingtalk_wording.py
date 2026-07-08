from pathlib import Path

import hermes_cli.dingtalk_auth as dingtalk_auth
import hermes_cli.gateway_windows as gateway_windows


def test_gateway_windows_source_avoids_selected_hardcoded_hermes_task_prose():
    source = Path(gateway_windows.__file__).read_text(encoding="utf-8")
    assert "Default profile: ``Hermes_Gateway``" not in source
    assert "Named profile X: ``Hermes_Gateway_<X>``" not in source
    assert "Lives under ``%LOCALAPPDATA%\\hermes\\gateway-service\\<task_name>.cmd``" not in source
    assert "Hermes installs stay self-contained" not in source
    assert "Default profile: base gateway task name" in source
    assert "Named profile X: base gateway task name with a profile suffix" in source
    assert "Lives under the local app-data gateway-service directory" in source
    assert "installs stay self-contained" in source


def test_dingtalk_auth_source_avoids_hardcoded_hermes_convention_comment():
    source = Path(dingtalk_auth.__file__).read_text(encoding="utf-8")
    assert "Try uv first (Hermes convention), then pip" not in source
    assert "Try uv first (project convention), then pip" in source
