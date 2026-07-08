#!/usr/bin/env python3
"""Sinria Boundary Control Layer — local, metadata-only egress preview.

This helper lets an operator/buyer see what Sinria *would* do with a payload at
an external boundary, before anything leaves the organization. It is strictly
local and read-only:

- It performs no external send and no model-provider call.
- It never prints raw confidential content; only a sanitized excerpt plus
  structured policy metadata (data class, route, action, approval).

Use synthetic data only. Example:

    python scripts/sinria_boundary_egress_preview.py \
        --provider openai_enterprise --deployment-mode cloud_enhanced \
        --text '患者ID: P-12345 の検査結果を要約'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.sinria_egress import _config_with_deployment_mode, preview_external_egress


def build_preview(
    text: str,
    *,
    destination_type: str = "model_provider",
    provider: str | None = None,
    deployment_mode: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    effective = config
    if deployment_mode:
        effective = _config_with_deployment_mode(config, deployment_mode)
    preview = preview_external_egress(
        destination_type,
        text,
        provider_key=provider or None,
        config=effective,
    )
    # Belt-and-suspenders: a preview must never perform or imply an external action.
    preview["external_action_performed"] = False
    preview["raw_content_included"] = False
    return preview


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sinria Boundary Control Layer local egress preview (no external send)"
    )
    parser.add_argument("--text", default="", help="Synthetic payload text. Reads stdin if omitted.")
    parser.add_argument("--destination-type", default="model_provider")
    parser.add_argument("--provider", default="")
    parser.add_argument("--deployment-mode", default="")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)

    text = args.text
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read()

    preview = build_preview(
        text,
        destination_type=args.destination_type,
        provider=args.provider or None,
        deployment_mode=args.deployment_mode or None,
    )

    if args.format == "json":
        print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Sinria Boundary Control Layer — egress preview")
        print(f"  Destination:  {preview.get('destination_type')}")
        print(f"  Provider:     {preview.get('provider') or '(none)'}")
        print(f"  Data class:   {preview.get('data_class')}")
        print(f"  Action:       {preview.get('action')}")
        print(f"  Allowed:      {preview.get('allowed')}")
        print(f"  Route:        {preview.get('required_route') or '(n/a)'}")
        print(f"  Approval:     {preview.get('approval') or '(none)'} (required={preview.get('approval_required')})")
        print(f"  Raw content:  {preview.get('raw_content_included')}")
        print(f"  External act: {preview.get('external_action_performed')}")
        print(f"  Sanitized:    {preview.get('sanitized_preview')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
