#!/usr/bin/env python3
"""Sinria Boundary Control Layer — metadata-only status/compliance report.

This helper is local/read-only. It does not deploy, send, migrate, bill, change
credentials, or perform external actions. Audit inputs are summarized as counts
only; raw confidential payloads are never exported.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.sinria_egress import export_sinria_boundary_compliance_report


def _default_audit_path() -> Path:
    try:
        from sinria_constants import get_sinria_home

        return Path(get_sinria_home()) / "logs" / "sinria-egress-audit.jsonl"
    except Exception:
        return Path.home() / ".sinria" / "logs" / "sinria-egress-audit.jsonl"


def _load_config() -> dict:
    try:
        from hermes_cli.config import load_config

        loaded = load_config() or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def summarize_audit(audit_path: Path | str | None = None) -> dict[str, Any]:
    """Return metadata-only counts from the Sinria egress audit JSONL."""
    path = Path(audit_path) if audit_path else _default_audit_path()
    actions: Counter[str] = Counter()
    destinations: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    data_classes: Counter[str] = Counter()
    total = 0
    malformed = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                malformed += 1
                continue
            total += 1
            decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
            boundary_decision = row.get("boundary_decision") if isinstance(row.get("boundary_decision"), dict) else {}
            actions.update([str(decision.get("action") or "unknown")])
            destinations.update([str(decision.get("destination_type") or "unknown")])
            provider_key = str(row.get("provider_key") or "").strip()
            if provider_key:
                providers.update([provider_key])
            data_class = str(boundary_decision.get("data_class") or "").strip()
            if data_class:
                data_classes.update([data_class])
    return {
        "audit_path": str(path),
        "raw_content_included": False,
        "total_records": total,
        "malformed_records": malformed,
        "actions": dict(sorted(actions.items())),
        "destinations": dict(sorted(destinations.items())),
        "providers": dict(sorted(providers.items())),
        "data_classes": dict(sorted(data_classes.items())),
    }


def build_status_report(config: dict | None = None, *, audit_path: Path | str | None = None) -> dict[str, Any]:
    compliance = export_sinria_boundary_compliance_report(config or {})
    return {
        **compliance,
        "report_type": "boundary_control_status",
        "raw_content_included": False,
        "external_action_performed": False,
        "audit": summarize_audit(audit_path),
        "approval_required_for": [
            "production deploys",
            "external sends",
            "live database migrations",
            "billing/auth changes",
            "clinical or patient-data actions",
        ],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    audit = report.get("audit", {}) if isinstance(report.get("audit"), dict) else {}
    lines = [
        "# Sinria Boundary Control Layer — status report",
        "",
        f"Deployment mode: {report.get('deployment_mode', '')}",
        f"Raw content included: {report.get('raw_content_included')}",
        f"External action performed: {report.get('external_action_performed')}",
        "",
        "## Audit summary",
        f"Total records: {audit.get('total_records', 0)}",
        f"Actions: {json.dumps(audit.get('actions', {}), ensure_ascii=False, sort_keys=True)}",
        f"Providers: {json.dumps(audit.get('providers', {}), ensure_ascii=False, sort_keys=True)}",
        f"Data classes: {json.dumps(audit.get('data_classes', {}), ensure_ascii=False, sort_keys=True)}",
        "",
        "Safety boundary: metadata-only local report; no deploy/send/migration/billing/auth/clinical action performed.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sinria Boundary Control Layer status/compliance report")
    parser.add_argument("--audit-path", default=str(_default_audit_path()))
    parser.add_argument("--output", default="")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)

    report = build_status_report(_load_config(), audit_path=args.audit_path)
    body = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n" if args.format == "json" else _render_markdown(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")
        print(f"Sinria Boundary Control Layer report written: {output_path}")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
