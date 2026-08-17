"""Install and launch the local-first Sinria in Chrome runtime."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

EXTENSION_ID = "pebcacnleolamclolgncigkgojkdghgc"
NATIVE_HOST_NAME = "ai.sinria.chrome_bridge"
NATIVE_MANIFEST_NAME = f"{NATIVE_HOST_NAME}.json"


@dataclass(frozen=True)
class ChromeInstall:
    extension_dir: Path
    native_host: Path
    manifest_paths: tuple[Path, ...]
    profile_dir: Path


@dataclass(frozen=True)
class ChromeStatus:
    installed: bool
    problems: list[str]
    extension_dir: Path
    native_host: Path
    manifest_paths: tuple[Path, ...]


def default_source_dir() -> Path:
    override = os.environ.get("SINRIA_CHROME_SOURCE")
    candidates = [
        Path(override).expanduser() if override else None,
        Path(__file__).resolve().parent / "chrome_extension",
        Path(__file__).resolve().parents[1] / "apps" / "sinria-in-chrome",
        Path(sys.prefix) / "share" / "sinria" / "chrome-extension",
    ]
    for candidate in candidates:
        if candidate and (candidate / "manifest.json").is_file():
            return candidate
    raise FileNotFoundError("Bundled Sinria Chrome extension assets were not found")


def default_sinria_home() -> Path:
    return Path(os.environ.get("SINRIA_HOME", Path.home() / ".sinria")).expanduser()


def default_chrome_support_dirs() -> list[Path]:
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        base = home / "Library" / "Application Support"
        return [base / "Google" / "Chrome", base / "Chromium", base / "Google" / "Chrome for Testing"]
    if system == "Linux":
        base = home / ".config"
        return [base / "google-chrome", base / "chromium"]
    return []


def _manifest(native_host: Path) -> dict:
    return {
        "name": NATIVE_HOST_NAME,
        "description": "Local-only Sinria Chrome API bridge",
        "path": str(native_host),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _runtime_paths(sinria_home: Path, chrome_support_dirs: Iterable[Path]) -> ChromeInstall:
    root = sinria_home / "chrome"
    profile = root / "profile"
    support = list(chrome_support_dirs)
    manifests = [directory / "NativeMessagingHosts" / NATIVE_MANIFEST_NAME for directory in support]
    manifests.append(profile / "NativeMessagingHosts" / NATIVE_MANIFEST_NAME)
    return ChromeInstall(
        extension_dir=root / "extension",
        native_host=root / "native-host" / "sinria_chrome_bridge.py",
        manifest_paths=tuple(manifests),
        profile_dir=profile,
    )


def install_chrome_runtime(source_dir: Path, sinria_home: Path, chrome_support_dirs: Iterable[Path]) -> ChromeInstall:
    """Stage immutable runtime assets and reconcile every native-host entrypoint."""
    source_dir = Path(source_dir).resolve()
    sinria_home = Path(sinria_home).expanduser()
    result = _runtime_paths(sinria_home, chrome_support_dirs)
    required = [source_dir / "manifest.json", source_dir / "sidepanel.html", source_dir / "src", source_dir / "native-host" / "sinria_chrome_bridge.py"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Sinria Chrome assets are incomplete: {', '.join(missing)}")

    staging = result.extension_dir.with_name("extension.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copy2(source_dir / "manifest.json", staging / "manifest.json")
    shutil.copy2(source_dir / "sidepanel.html", staging / "sidepanel.html")
    shutil.copytree(source_dir / "src", staging / "src")
    shutil.rmtree(result.extension_dir, ignore_errors=True)
    staging.replace(result.extension_dir)

    result.native_host.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_dir / "native-host" / "sinria_chrome_bridge.py", result.native_host)
    result.native_host.chmod(0o700)
    for path in result.manifest_paths:
        _write_json(path, _manifest(result.native_host))
    return result


def inspect_chrome_runtime(sinria_home: Path, chrome_support_dirs: Iterable[Path]) -> ChromeStatus:
    result = _runtime_paths(Path(sinria_home).expanduser(), chrome_support_dirs)
    problems: list[str] = []
    if not (result.extension_dir / "manifest.json").is_file():
        problems.append("Chrome extension is not installed")
    if not result.native_host.is_file():
        problems.append("Native host is not installed")
    expected = _manifest(result.native_host)
    for path in result.manifest_paths:
        if not path.is_file():
            problems.append(f"Native host manifest is missing: {path}")
            continue
        try:
            if json.loads(path.read_text(encoding="utf-8")) != expected:
                problems.append(f"Native host manifest is stale: {path}")
        except (OSError, json.JSONDecodeError):
            problems.append(f"Native host manifest is invalid: {path}")
    return ChromeStatus(not problems, problems, result.extension_dir, result.native_host, result.manifest_paths)


def _chrome_app() -> Path | None:
    override = os.environ.get("SINRIA_CHROME_BINARY")
    return Path(override).expanduser() if override else None


def _managed_chrome_path(result: ChromeInstall) -> Path:
    root = result.extension_dir.parent / "browser"
    if platform.system() == "Darwin":
        return root / "Google Chrome for Testing.app"
    if platform.system() == "Windows":
        return root / "chrome-win64" / "chrome.exe"
    return root / "chrome-linux64" / "chrome"


def _download_chrome_for_testing(result: ChromeInstall) -> Path:
    destination = _managed_chrome_path(result)
    executable = destination / "Contents" / "MacOS" / "Google Chrome for Testing" if destination.suffix == ".app" else destination
    if executable.exists():
        return destination
    system = platform.system()
    machine = platform.machine().lower()
    platform_key = {
        ("Darwin", "arm64"): "mac-arm64",
        ("Darwin", "aarch64"): "mac-arm64",
        ("Darwin", "x86_64"): "mac-x64",
        ("Linux", "x86_64"): "linux64",
        ("Windows", "amd64"): "win64",
        ("Windows", "x86_64"): "win64",
    }.get((system, machine))
    if not platform_key:
        raise RuntimeError(f"Chrome for Testing is not available for {system} {machine}")
    metadata_url = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
    with urllib.request.urlopen(metadata_url, timeout=30) as response:
        metadata = json.load(response)
    downloads = metadata["channels"]["Stable"]["downloads"]["chrome"]
    download_url = next(item["url"] for item in downloads if item["platform"] == platform_key)
    if not download_url.startswith("https://storage.googleapis.com/chrome-for-testing-public/"):
        raise RuntimeError("Chrome for Testing download URL was not from the expected Google host")
    root = result.extension_dir.parent / "browser"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        archive = Path(temporary) / "chrome.zip"
        with urllib.request.urlopen(download_url, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        extract = Path(temporary) / "extract"
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                resolved = (extract / member.filename).resolve()
                if extract.resolve() not in resolved.parents and resolved != extract.resolve():
                    raise RuntimeError("Unsafe path in Chrome for Testing archive")
            bundle.extractall(extract)
        if system == "Darwin":
            source = next(extract.glob("*/Google Chrome for Testing.app"))
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(source), str(destination))
        else:
            source_bundle = next(path for path in extract.iterdir() if path.is_dir())
            target_bundle = destination.parent
            if target_bundle.exists():
                shutil.rmtree(target_bundle)
            shutil.move(str(source_bundle), str(target_bundle))
    return destination


def launch_chrome(result: ChromeInstall) -> None:
    app = _chrome_app() or _download_chrome_for_testing(result)
    result.profile_dir.mkdir(parents=True, exist_ok=True)
    url = "chrome://newtab/"
    args = [
        f"--user-data-dir={result.profile_dir}",
        f"--disable-extensions-except={result.extension_dir}",
        f"--load-extension={result.extension_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    if platform.system() == "Darwin":
        subprocess.run(["open", "-na", str(app), "--args", *args], check=True)
    else:
        subprocess.Popen([str(app), *args], start_new_session=True)


def uninstall_chrome_runtime(sinria_home: Path, chrome_support_dirs: Iterable[Path]) -> list[Path]:
    result = _runtime_paths(Path(sinria_home).expanduser(), chrome_support_dirs)
    removed: list[Path] = []
    expected_path = str(result.native_host)
    for manifest in result.manifest_paths:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if data.get("name") == NATIVE_HOST_NAME and data.get("path") == expected_path:
            manifest.unlink()
            removed.append(manifest)
    root = result.extension_dir.parent
    if root.exists():
        shutil.rmtree(root)
        removed.append(root)
    return removed


def chrome_command(args) -> int:
    action = getattr(args, "action", None) or getattr(args, "chrome_command", None) or "open"
    source = default_source_dir()
    home = default_sinria_home()
    support = default_chrome_support_dirs()
    if action == "status":
        status = inspect_chrome_runtime(home, support)
        browser = _chrome_app() or _managed_chrome_path(_runtime_paths(home, support))
        browser_ready = browser.exists()
        ready = status.installed and browser_ready
        print("Sinria in Chrome: installed" if ready else "Sinria in Chrome: not ready")
        print(f"Extension: {status.extension_dir}")
        print(f"Browser: {browser}")
        for problem in status.problems:
            print(f"- {problem}")
        if not browser_ready:
            print("- Managed Chrome for Testing is missing; run `sinria chrome install`")
        return 0 if ready else 1
    if action == "uninstall":
        uninstall_chrome_runtime(home, support)
        print("Sinria in Chrome was removed from this user profile.")
        return 0
    result = install_chrome_runtime(source, home, support)
    if action == "install":
        browser = _chrome_app() or _download_chrome_for_testing(result)
        print("Sinria in Chrome installed.")
        print(f"Extension: {result.extension_dir}")
        print(f"Browser: {browser}")
        return 0
    launch_chrome(result)
    print("Sinria in Chrome opened in a dedicated local Chrome profile.")
    print("Open a web page, then click the Sinria extension icon to use the side panel.")
    return 0
