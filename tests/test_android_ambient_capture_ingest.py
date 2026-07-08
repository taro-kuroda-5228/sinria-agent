import json
from pathlib import Path

from agent.ambient_capture.ingest import ingest_capture_bundle


def test_ingest_capture_bundle_writes_local_only_report(tmp_path):
    bundle = tmp_path / "capture-pixel8-android16-smoke"
    chunks = bundle / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "chunk-0001.enc").write_bytes(b"synthetic encrypted audio")
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "capture_id": "capture-pixel8-android16-smoke",
                "device_model": "Google Pixel 8",
                "android_version": "Android 16",
                "chunks": ["chunks/chunk-0001.enc"],
            }
        ),
        encoding="utf-8",
    )

    runtime_root = tmp_path / "runtime"
    report = ingest_capture_bundle(bundle, runtime_root=runtime_root)

    assert report.capture_id == "capture-pixel8-android16-smoke"
    assert report.external_action_performed is False
    assert report.raw_audio_cloud_stored is False
    assert report.local_inbox_path == runtime_root / "inbox" / "capture-pixel8-android16-smoke"
    report_json = json.loads((report.local_inbox_path / "ingest-report.json").read_text(encoding="utf-8"))
    assert report_json["device_model"] == "Google Pixel 8"
    assert report_json["android_version"] == "Android 16"
    assert report_json["raw_audio_cloud_stored"] is False
    assert "synthetic encrypted audio" not in json.dumps(report_json)


def test_ingest_capture_bundle_rejects_missing_chunk(tmp_path):
    bundle = tmp_path / "bad-capture"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "capture_id": "bad-capture",
                "device_model": "Google Pixel 8",
                "android_version": "Android 16",
                "chunks": ["chunks/missing.enc"],
            }
        ),
        encoding="utf-8",
    )

    try:
        ingest_capture_bundle(bundle, runtime_root=tmp_path / "runtime")
    except FileNotFoundError as exc:
        assert "missing encrypted chunk" in str(exc)
    else:
        raise AssertionError("missing encrypted chunk was accepted")
