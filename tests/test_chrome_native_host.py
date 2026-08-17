import importlib.util
from pathlib import Path


HOST_PATH = Path(__file__).parents[1] / "apps" / "sinria-in-chrome" / "native-host" / "sinria_chrome_bridge.py"
SPEC = importlib.util.spec_from_file_location("sinria_chrome_bridge", HOST_PATH)
HOST = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(HOST)


def test_load_local_api_token_reads_sinria_env_without_exporting_other_values(tmp_path):
    (tmp_path / ".env").write_text("OTHER=value\nAPI_SERVER_KEY=local-secret\n", encoding="utf-8")

    assert HOST.load_local_api_token(tmp_path) == "local-secret"


def test_authorization_header_prefers_explicit_session_token(tmp_path):
    (tmp_path / ".env").write_text("API_SERVER_KEY=local-secret\n", encoding="utf-8")

    explicit = HOST.authorized_headers({"Authorization": "Bearer session-secret"}, tmp_path)
    automatic = HOST.authorized_headers({}, tmp_path)

    assert explicit["Authorization"] == "Bearer session-secret"
    assert automatic["Authorization"] == "Bearer local-secret"
