"""Local-only Sinria Ambient Capture ingestion contracts.

This package handles metadata contracts for Android capture bundles. Raw audio,
transcripts, and speaker embeddings stay under ~/.sinria/private/ambient-capture.
"""

from .ingest import IngestReport, ingest_capture_bundle
from .schema import CaptureManifest

__all__ = ["CaptureManifest", "IngestReport", "ingest_capture_bundle"]
