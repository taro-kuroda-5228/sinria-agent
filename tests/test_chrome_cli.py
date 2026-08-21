import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import sinria_cli.chrome as chrome
from sinria_cli.chrome import (
    EXTENSION_ID,
    default_source_dir,
    install_chrome_runtime,
    inspect_chrome_runtime,
)


def test_bundled_chrome_assets_live_inside_the_installed_python_package():
    source = default_source_dir().resolve()
    package_dir = (Path(__file__).parents[1] / "sinria_cli").resolve()

    assert source.is_relative_to(package_dir)
    assert (source / "manifest.json").is_file()
    assert (source / "native-host" / "sinria_chrome_bridge.py").is_file()


def test_packaged_chrome_assets_match_the_reviewable_app_source():
    root = Path(__file__).parents[1]
    app = root / "apps" / "sinria-in-chrome"
    packaged = root / "sinria_cli" / "chrome_extension"
    runtime_files = [
        Path("manifest.json"),
        Path("sidepanel.html"),
        Path("native-host/sinria_chrome_bridge.py"),
        *sorted(path.relative_to(app) for path in (app / "src").iterdir() if path.is_file()),
    ]

    for relative in runtime_files:
        assert (packaged / relative).read_bytes() == (app / relative).read_bytes(), relative


def test_sinria_cli_registers_chrome_command():
    result = subprocess.run(
        [sys.executable, "-m", "sinria_cli.main", "chrome", "--help"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "{open,install,status,uninstall}" in result.stdout


def _source_tree(root: Path) -> Path:
    app = root / "apps" / "sinria-in-chrome"
    (app / "src").mkdir(parents=True)
    (app / "native-host").mkdir()
    (app / "manifest.json").write_text('{"name":"Sinria in Chrome"}', encoding="utf-8")
    (app / "sidepanel.html").write_text("<main>Sinria</main>", encoding="utf-8")
    (app / "src" / "service-worker.js").write_text("// worker", encoding="utf-8")
    (app / "native-host" / "sinria_chrome_bridge.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    return app


def test_install_stages_extension_and_local_native_host(tmp_path):
    source = _source_tree(tmp_path / "checkout")
    home = tmp_path / ".sinria"
    chrome_support = tmp_path / "Chrome"

    result = install_chrome_runtime(source, home, [chrome_support])

    assert result.extension_dir == home / "chrome" / "extension"
    assert (result.extension_dir / "manifest.json").exists()
    assert not (result.extension_dir / "native-host").exists()
    assert result.native_host.stat().st_mode & 0o111
    manifest = json.loads((chrome_support / "NativeMessagingHosts" / "ai.sinria.chrome_bridge.json").read_text())
    assert manifest["path"] == str(result.native_host)
    assert manifest["allowed_origins"] == [f"chrome-extension://{EXTENSION_ID}/"]


def test_install_reconciles_stale_native_host_manifest(tmp_path):
    source = _source_tree(tmp_path / "checkout")
    home = tmp_path / ".sinria"
    chrome_support = tmp_path / "Chrome"
    manifest_path = chrome_support / "NativeMessagingHosts" / "ai.sinria.chrome_bridge.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text('{"path":"/tmp/deleted-worktree/host.py"}', encoding="utf-8")

    result = install_chrome_runtime(source, home, [chrome_support])

    assert json.loads(manifest_path.read_text())["path"] == str(result.native_host)
    status = inspect_chrome_runtime(home, [chrome_support])
    assert status.installed is True
    assert status.problems == []


def test_status_reports_missing_components_without_mutation(tmp_path):
    home = tmp_path / ".sinria"
    chrome_support = tmp_path / "Chrome"

    status = inspect_chrome_runtime(home, [chrome_support])

    assert status.installed is False
    assert "extension" in " ".join(status.problems).lower()
    assert not home.exists()


def test_chrome_command_honors_install_action(monkeypatch, tmp_path, capsys):
    browser = tmp_path / "Google Chrome"
    browser.touch()
    install = chrome.ChromeInstall(
        extension_dir=tmp_path / "extension",
        native_host=tmp_path / "native-host",
        manifest_paths=(),
        profile_dir=tmp_path / "profile",
    )
    monkeypatch.setattr(chrome, "default_source_dir", lambda: tmp_path / "source")
    monkeypatch.setattr(chrome, "default_sinria_home", lambda: tmp_path / ".sinria")
    monkeypatch.setattr(chrome, "default_chrome_support_dirs", lambda: ())
    monkeypatch.setattr(chrome, "install_chrome_runtime", lambda *args: install)
    monkeypatch.setattr(chrome, "_chrome_app", lambda: browser)
    monkeypatch.setattr(
        chrome,
        "launch_chrome",
        lambda *_: (_ for _ in ()).throw(AssertionError("install must not launch Chrome")),
    )

    assert chrome.chrome_command(SimpleNamespace(action="install")) == 0
    output = capsys.readouterr().out
    assert "runtime installed" in output
    assert "every computer" in output
    assert "each Chrome profile" in output
    assert str(install.extension_dir) in output
    assert f"API_SERVER_CORS_ORIGINS=chrome-extension://{EXTENSION_ID}" in output
    assert "http://127.0.0.1:8642" in output
    assert "Save & test" in output


def test_chrome_status_distinguishes_runtime_from_profile_activation(
    monkeypatch, tmp_path, capsys
):
    status = chrome.ChromeStatus(
        installed=True,
        problems=[],
        extension_dir=tmp_path / "extension",
        native_host=tmp_path / "native-host",
        manifest_paths=(),
    )
    browser = tmp_path / "Google Chrome"
    browser.touch()
    monkeypatch.setattr(chrome, "default_sinria_home", lambda: tmp_path / ".sinria")
    monkeypatch.setattr(chrome, "default_chrome_support_dirs", lambda: ())
    monkeypatch.setattr(chrome, "inspect_chrome_runtime", lambda *_: status)
    monkeypatch.setattr(chrome, "_managed_chrome_path", lambda *_: browser)

    assert chrome.chrome_command(SimpleNamespace(action="status")) == 0
    output = capsys.readouterr().out
    assert "Sinria in Chrome runtime: installed" in output
    assert "does not verify Chrome profile activation" in output


def test_managed_chrome_paths_keep_cross_platform_runtime_bundles(monkeypatch, tmp_path):
    install = chrome.ChromeInstall(
        extension_dir=tmp_path / "extension",
        native_host=tmp_path / "native-host",
        manifest_paths=(),
        profile_dir=tmp_path / "profile",
    )

    monkeypatch.setattr(chrome.platform, "system", lambda: "Windows")
    windows = chrome._managed_chrome_path(install)
    assert windows.name == "chrome.exe"
    assert windows.parent.name == "chrome-win64"

    monkeypatch.setattr(chrome.platform, "system", lambda: "Linux")
    linux = chrome._managed_chrome_path(install)
    assert linux.name == "chrome"
    assert linux.parent.name == "chrome-linux64"
