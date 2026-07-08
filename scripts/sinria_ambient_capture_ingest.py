#!/usr/bin/env python3
"""Local-only ingest helper for Sinria Android Ambient Capture bundles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.ambient_capture.ingest import DEFAULT_RUNTIME_ROOT, ingest_capture_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a Sinria Android Ambient Capture bundle locally.")
    parser.add_argument("--bundle", required=True, help="Path to capture bundle containing manifest.json and encrypted chunks.")
    parser.add_argument(
        "--runtime-root",
        default=str(DEFAULT_RUNTIME_ROOT),
        help="Local Sinria ambient-capture runtime root. Defaults to ~/.sinria/private/ambient-capture.",
    )
    parser.add_argument("--local-only", action="store_true", help="Required safety acknowledgement: no cloud/external actions.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    args = parser.parse_args()

    if not args.local_only:
        parser.error("--local-only is required; external actions are not supported by this helper")

    report = ingest_capture_bundle(Path(args.bundle), runtime_root=Path(args.runtime_root))
    payload = report.to_json_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"ingested {report.capture_id} into {report.local_inbox_path}")
        print("external_action_performed=false raw_audio_cloud_stored=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
