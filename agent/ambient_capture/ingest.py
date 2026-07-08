"""Local-only ingestion for Android Ambient Capture bundles."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import CaptureManifest

DEFAULT_RUNTIME_ROOT = Path.home() / ".sinria" / "private" / "ambient-capture"


@dataclass(frozen=True)
class IngestReport:
    capture_id: str
    device_model: str
    android_version: str
    local_inbox_path: Path
    chunk_count: int
    raw_audio_cloud_stored: bool = False
    raw_transcript_cloud_stored: bool = False
    speaker_embedding_cloud_stored: bool = False
    external_action_performed: bool = False
    human_review_required: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "device_model": self.device_model,
            "android_version": self.android_version,
            "local_inbox_path": str(self.local_inbox_path),
            "chunk_count": self.chunk_count,
            "raw_audio_cloud_stored": self.raw_audio_cloud_stored,
            "raw_transcript_cloud_stored": self.raw_transcript_cloud_stored,
            "speaker_embedding_cloud_stored": self.speaker_embedding_cloud_stored,
            "external_action_performed": self.external_action_performed,
            "human_review_required": self.human_review_required,
        }


def ingest_capture_bundle(bundle_path: str | Path, runtime_root: str | Path = DEFAULT_RUNTIME_ROOT) -> IngestReport:
    """Validate and copy a capture bundle into the local Sinria private inbox."""

    bundle = Path(bundle_path).expanduser().resolve()
    root = Path(runtime_root).expanduser()
    manifest_path = bundle / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = CaptureManifest(**manifest_data)

    for chunk in manifest.chunks:
        source_chunk = bundle / chunk
        if not source_chunk.exists() or not source_chunk.is_file():
            raise FileNotFoundError(f"missing encrypted chunk: {chunk}")

    destination = root / "inbox" / manifest.capture_id
    destination_chunks = destination / "chunks"
    destination_chunks.mkdir(parents=True, exist_ok=True)

    shutil.copy2(manifest_path, destination / "manifest.json")
    for chunk in manifest.chunks:
        source_chunk = bundle / chunk
        target_chunk = destination / chunk
        target_chunk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_chunk, target_chunk)

    report = IngestReport(
        capture_id=manifest.capture_id,
        device_model=manifest.device_model,
        android_version=manifest.android_version,
        local_inbox_path=destination,
        chunk_count=len(manifest.chunks),
    )
    (destination / "ingest-report.json").write_text(
        json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report
