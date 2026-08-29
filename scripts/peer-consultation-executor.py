#!/usr/bin/env python3
"""Local evidence resolver for consultation.v1 peer requests.

Only bounded consultation metadata crosses Company OS. Google Workspace source
bodies are fetched and reduced locally; cell contents are never printed.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sinria_consultation import validate_consultation

DASHBOARD_ID = "1D6SACTdRdCtAaXcQcLYqohJg8ncKwAKFekr9DfDSHbc"
PREFLIGHT_RANGE = "📱 今日の進捗!A1:E8"


class WorkspaceResolverError(RuntimeError):
    """Safe machine-readable resolver failure without credential/source detail."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sheet_values(resource_id: str, range_name: str) -> list[list[object]]:
    if resource_id != DASHBOARD_ID:
        raise WorkspaceResolverError("workspace_resource_not_allowlisted")
    import urllib.error, urllib.parse, urllib.request
    from sinria_constants import get_sinria_home
    token_path = Path(get_sinria_home()) / "google_token.json"
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceResolverError("workspace_token_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceResolverError("workspace_token_invalid") from exc
    required = ("client_id", "client_secret", "refresh_token")
    if not all(isinstance(payload.get(key), str) and payload[key] for key in required):
        raise WorkspaceResolverError("workspace_token_invalid")
    refresh = urllib.parse.urlencode({"client_id": payload["client_id"], "client_secret": payload["client_secret"],
        "refresh_token": payload["refresh_token"], "grant_type": "refresh_token"}).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=refresh, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            access_token = json.loads(response.read())["access_token"]
    except Exception as exc:
        raise WorkspaceResolverError("workspace_token_refresh_failed") from exc
    encoded_range = urllib.parse.quote(range_name, safe="")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{resource_id}/values/{encoded_range}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        code = "workspace_source_access_denied" if exc.code in {401, 403} else "workspace_source_unavailable"
        raise WorkspaceResolverError(code) from exc
    except Exception as exc:
        raise WorkspaceResolverError("workspace_source_unavailable") from exc
    return result.get("values", [])


def workspace_preflight() -> dict:
    try:
        _sheet_values(DASHBOARD_ID, PREFLIGHT_RANGE)
    except WorkspaceResolverError as exc:
        return {"ok": False, "workspaceAccess": False, "errorCode": exc.code, "rawContextStored": False}
    except Exception:
        return {"ok": False, "workspaceAccess": False, "errorCode": "workspace_source_unavailable", "rawContextStored": False}
    return {"ok": True, "workspaceAccess": True, "resourceId": DASHBOARD_ID,
            "range": PREFLIGHT_RANGE, "rawContextStored": False}

def execute(envelope: dict) -> dict:
    event = envelope.get("event", envelope)
    if not isinstance(event, dict): raise ValueError("invalid event envelope")
    meta = validate_consultation(event.get("consultationMetadata"))
    if not meta or meta["type"] != "consultation_request":
        # Preserve existing synthetic canary behavior.
        preview = event.get("sanitizedPreview")
        if not isinstance(preview, str) or not preview.startswith("Synthetic metadata-only") or event.get("bodyRef") is not None:
            raise ValueError("unsupported peer event")
        return {"summary": "Synthetic peer task executed; sanitized completion receipt returned.", "refs": [f"run://event/{event.get('eventId', 'unknown')}"], "rawContextStored": False, "externalActionPerformed": False}
    aggregate: list[list[object]] = []
    for ref in meta["sourceRefs"]:
        if ref["provider"] != "google_workspace" or not ref.get("range"):
            raise ValueError("consultation source range is required")
        aggregate.extend(_sheet_values(ref["resourceId"], ref["range"]))
    flat = " ".join(str(cell) for row in aggregate for cell in row).lower()
    has_peer = "peer executor" in flat or "sinria" in flat
    has_gate = "承認" in flat or "approval" in flat
    recommendation = (
        "共有正本の最新進捗とpeer運用記録を根拠に、担当Sinria間のconsultation request/responseを通常経路とし、外部操作はdecision gateへ分離して継続する。"
        if has_peer else
        "共有正本の参照は成功したがpeer運用記録を確認できないため、担当者とsource versionを確認してから相談運用を開始する。"
    )
    response = {
        "schemaVersion": "consultation.v1", "type": "consultation_response", "consultationId": meta["consultationId"],
        "recommendation": recommendation, "sourceRefs": meta["sourceRefs"], "confidence": 0.9 if has_peer and has_gate else 0.65,
        "assumptions": ["共有正本の指定範囲が最新である"], "dissent": [],
        "unresolvedQuestions": [] if has_peer else ["peer運用記録の正本位置を確認する"],
        "humanDecisionRequired": False, "allowedOperations": ["read", "draft"], "sensitivity": "internal",
        "rawContextStored": False, "externalActionPerformed": False,
    }
    return {"summary": "Consultation response created from locally resolved Company Knowledge references.",
            "consultationMetadata": validate_consultation(response), "refs": [f"run://consultation/{meta['consultationId']}"],
            "rawContextStored": False, "externalActionPerformed": False}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        result = workspace_preflight()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 2
    try:
        value = json.load(sys.stdin)
        print(json.dumps(execute(value), ensure_ascii=False))
        return 0
    except WorkspaceResolverError as exc:
        print(json.dumps({"error": "consultation execution rejected", "errorCode": exc.code}))
        return 2
    except Exception:
        print(json.dumps({"error": "consultation execution rejected", "errorCode": "consultation_execution_rejected"}))
        return 2
if __name__ == "__main__": raise SystemExit(main())
