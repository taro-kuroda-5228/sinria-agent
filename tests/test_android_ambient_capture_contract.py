from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "android-ambient-capture"
DOC_DIR = ROOT / "docs" / "ambient-capture"
AGENT_DIR = ROOT / "agent" / "ambient_capture"

pytestmark = pytest.mark.skipif(
    not (APP_DIR.exists() and DOC_DIR.exists() and AGENT_DIR.exists()),
    reason="ambient-capture product overlay is not included in the public distribution",
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_android_ambient_capture_project_uses_sinria_native_paths_and_pixel8_target():
    readme = read_text(APP_DIR / "README.md")
    architecture = read_text(DOC_DIR / "architecture.md")
    privacy = read_text(DOC_DIR / "privacy-safety.md")
    combined = "\n".join([readme, architecture, privacy])

    assert "Google Pixel 8" in combined
    assert "Android 16" in combined
    assert "Foreground Service" in combined
    assert "~/.sinria/private/ambient-capture" in combined
    assert "~/.hermes" not in combined
    assert "hermes chat" not in combined
    assert "hermes config" not in combined
    assert "Hermes Agent" not in combined


def test_android_manifest_declares_recording_permissions_and_service():
    manifest = read_text(APP_DIR / "app" / "src" / "main" / "AndroidManifest.xml")

    assert "android.permission.RECORD_AUDIO" in manifest
    assert "android.permission.FOREGROUND_SERVICE" in manifest
    assert "android.permission.FOREGROUND_SERVICE_MICROPHONE" in manifest
    assert "AmbientCaptureService" in manifest
    assert 'android:foregroundServiceType="microphone"' in manifest


def test_android_gradle_config_aligns_java_and_kotlin_targets_for_real_apk_build():
    app_gradle = read_text(APP_DIR / "app" / "build.gradle.kts")

    assert "sourceCompatibility = JavaVersion.VERSION_17" in app_gradle
    assert "targetCompatibility = JavaVersion.VERSION_17" in app_gradle
    assert "jvmTarget = \"17\"" in app_gradle


def test_android_app_writes_local_encrypted_chunks_and_safe_manifest_not_raw_audio_bundle():
    service = read_text(APP_DIR / "app" / "src" / "main" / "java" / "com" / "sinria" / "ambientcapture" / "AmbientCaptureService.kt")
    writer = read_text(APP_DIR / "app" / "src" / "main" / "java" / "com" / "sinria" / "ambientcapture" / "EncryptedCaptureWriter.kt")

    combined = service + "\n" + writer
    assert "AES/GCM/NoPadding" in combined
    assert "AndroidKeyStore" in combined
    assert '"chunks/chunk-' in combined
    assert '.enc"' in combined
    assert '.put("raw_audio_cloud_stored", false)' in combined
    assert '.put("speaker_embedding_cloud_stored", false)' in combined
    assert '.put("external_action_performed", false)' in combined
    assert "delete()" in combined


def test_android_smoke_script_exports_only_encrypted_bundles_then_runs_local_ingest():
    script = read_text(ROOT / "scripts" / "sinria_ambient_capture_android_smoke.sh")

    assert ".sinria/private/ambient-capture/android-smoke" in script
    assert "export-bundles" in script
    assert "sinria_ambient_capture_ingest.py" in script
    assert "--local-only" in script
    assert "temp-recordings" not in script
    assert "pull" not in script


def test_android_smoke_uses_debug_only_control_activity_not_exported_microphone_service():
    script = read_text(ROOT / "scripts" / "sinria_ambient_capture_android_smoke.sh")
    main_manifest = read_text(APP_DIR / "app" / "src" / "main" / "AndroidManifest.xml")
    debug_manifest = read_text(APP_DIR / "app" / "src" / "debug" / "AndroidManifest.xml")
    smoke_activity = read_text(
        APP_DIR
        / "app"
        / "src"
        / "debug"
        / "java"
        / "com"
        / "sinria"
        / "ambientcapture"
        / "SmokeControlActivity.kt"
    )

    assert 'android:name=".AmbientCaptureService"' in main_manifest
    assert 'android:exported="false"' in main_manifest
    assert 'android:name=".SmokeControlActivity"' in debug_manifest
    assert 'android:exported="true"' in debug_manifest
    assert "/.SmokeControlActivity" in script
    assert "start-foreground-service -n \"$SERVICE\"" not in script
    assert "Intent(this, AmbientCaptureService::class.java)" in smoke_activity
    assert "startForegroundService" in smoke_activity


def test_local_runtime_layout_is_outside_git_and_data_classes_are_guarded():
    layout = read_text(DOC_DIR / "local-runtime-layout.md")
    privacy = read_text(DOC_DIR / "privacy-safety.md")
    gitignore = read_text(ROOT / ".gitignore")

    assert "~/.sinria/private/ambient-capture/audio/" in layout
    assert "~/.sinria/private/ambient-capture/speaker-profiles/" in layout
    assert "Raw audio" in privacy
    assert "Speaker embeddings" in privacy
    assert "biometric PII" in privacy
    assert "external_action_performed=false" in privacy
    assert "raw_audio_cloud_stored=false" in privacy

    assert "apps/android-ambient-capture/**/*.wav" in gitignore
    assert "apps/android-ambient-capture/**/*.m4a" in gitignore
    assert "apps/android-ambient-capture/**/speaker-profiles/" in gitignore


def test_python_ingest_contract_rejects_unsafe_bundle_paths():
    from agent.ambient_capture.schema import CaptureManifest

    safe_manifest = CaptureManifest(
        capture_id="capture-pixel8-android16-smoke",
        device_model="Google Pixel 8",
        android_version="Android 16",
        chunks=["chunks/chunk-0001.enc"],
    )
    assert safe_manifest.raw_audio_cloud_stored is False
    assert safe_manifest.external_action_performed is False

    try:
        CaptureManifest(
            capture_id="bad",
            device_model="Google Pixel 8",
            android_version="Android 16",
            chunks=["/tmp/raw.wav"],
        )
    except ValueError as exc:
        assert "relative encrypted chunk paths" in str(exc)
    else:
        raise AssertionError("absolute raw audio path was accepted")
