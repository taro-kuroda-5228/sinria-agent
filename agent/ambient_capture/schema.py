"""Schema objects for local Android Ambient Capture bundles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

_AUDIO_RAW_SUFFIXES = {".wav", ".m4a", ".aac", ".flac", ".mp3"}


@dataclass(frozen=True)
class CaptureManifest:
    """Validated metadata for one local Android capture bundle.

    The manifest intentionally accepts only relative encrypted chunk paths. Raw
    audio paths, absolute paths, cloud URLs, and parent traversal are rejected so
    ingestion cannot accidentally copy private material into shared surfaces.
    """

    capture_id: str
    device_model: str
    android_version: str
    chunks: list[str]
    raw_audio_cloud_stored: bool = False
    raw_transcript_cloud_stored: bool = False
    speaker_embedding_cloud_stored: bool = False
    external_action_performed: bool = False
    human_review_required: bool = True
    source: str = "android-ambient-capture"
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.capture_id.strip():
            raise ValueError("capture_id is required")
        if self.device_model != "Google Pixel 8":
            raise ValueError("MVP target device must be Google Pixel 8")
        if self.android_version != "Android 16":
            raise ValueError("MVP target OS must be Android 16")
        if not self.chunks:
            raise ValueError("at least one encrypted chunk is required")
        for chunk in self.chunks:
            self._validate_chunk_path(chunk)
        if self.raw_audio_cloud_stored:
            raise ValueError("raw_audio_cloud_stored must remain false")
        if self.raw_transcript_cloud_stored:
            raise ValueError("raw_transcript_cloud_stored must remain false")
        if self.speaker_embedding_cloud_stored:
            raise ValueError("speaker_embedding_cloud_stored must remain false")
        if self.external_action_performed:
            raise ValueError("external_action_performed must remain false")
        if not self.human_review_required:
            raise ValueError("human_review_required must remain true for MVP imports")

    @staticmethod
    def _validate_chunk_path(chunk: str) -> None:
        lowered = chunk.lower()
        if "://" in lowered:
            raise ValueError("capture chunks must be local relative encrypted chunk paths")
        path = PurePosixPath(chunk)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("capture chunks must be relative encrypted chunk paths")
        if path.suffix.lower() != ".enc":
            raise ValueError("capture chunks must be relative encrypted chunk paths")
        if any(lowered.endswith(suffix) for suffix in _AUDIO_RAW_SUFFIXES):
            raise ValueError("raw audio paths are not accepted in capture manifests")
