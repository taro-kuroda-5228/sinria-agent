"""Sync reviewed Company OS knowledge metadata into the encrypted local index."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .store import EncryptedLocalStore

SOURCE = "company-os-validated"
_FORBIDDEN_KEYS = {
    "rawBody",
    "rawContext",
    "rawEvidence",
    "rawSkillBody",
    "credential",
    "token",
    "secret",
    "patientData",
}


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN_KEYS:
                raise ValueError(f"forbidden manifest field: {key}")
            _assert_no_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(child)


def validate_manifest(payload: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
    manifest = payload.get("manifest") if payload.get("ok") is True else payload
    if not isinstance(manifest, dict):
        raise ValueError("manifest is required")
    _assert_no_forbidden_keys(manifest)
    if manifest.get("workspaceId") != workspace_id:
        raise ValueError("manifest workspace mismatch")
    if manifest.get("metadataOnly") is not True:
        raise ValueError("manifest must be metadata-only")
    safety = manifest.get("safety")
    if not isinstance(safety, dict) or any(value is not False for value in safety.values()):
        raise ValueError("manifest safety envelope is invalid")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("manifest entries must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("manifest entry must be an object")
        for required in ("knowledgeId", "title", "sanitizedSummary", "reviewedByMemberId", "reviewedAt", "version"):
            if not entry.get(required):
                raise ValueError(f"manifest entry missing {required}")
        if not isinstance(entry.get("citationRefs"), list):
            raise ValueError("manifest entry citationRefs must be a list")
    return manifest


def sync_manifest(
    store: EncryptedLocalStore,
    *,
    owner_id: str,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, int]:
    """Replace this owner's reviewed-knowledge projection; never touches other sources."""
    if store.workspace_id != workspace_id:
        raise ValueError("local store workspace mismatch")
    manifest = validate_manifest(payload, workspace_id=workspace_id)
    keep: set[str] = set()
    for entry in manifest["entries"]:
        knowledge_id = str(entry["knowledgeId"])
        doc_id = f"company-knowledge:{knowledge_id}"
        keep.add(doc_id)
        citations = [str(value) for value in entry.get("citationRefs", []) if str(value).strip()]
        citation = citations[0] if citations else f"company-os:{knowledge_id}"
        labels = [
            "Internal",
            "company-knowledge",
            str(entry.get("assetKind", "knowledge")),
            *[str(value) for value in entry.get("scopeKeys", [])],
        ]
        store.put(
            doc_id,
            owner_id,
            f"{entry['title']}\n{entry['sanitizedSummary']}",
            {
                "classification": "Internal",
                "citation": citation,
                "citations": citations,
                "expires_at": entry.get("expiresAt"),
                "labels": labels,
                "profile_id": store.profile_id,
                "workspace_id": store.workspace_id,
                "knowledge_version": entry["version"],
                "reviewed_at": entry["reviewedAt"],
                "reviewed_by": entry["reviewedByMemberId"],
                "source": SOURCE,
            },
            source=SOURCE,
        )
    removed = store.prune_source(owner_id, SOURCE, keep)
    return {"upserted": len(keep), "removed": removed}


def fetch_manifest(
    *,
    url: str,
    workspace_id: str,
    token: str,
    transport_subject: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}{urlencode({'workspaceId': workspace_id})}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "x-sinria-transport-subject": transport_subject,
            "x-sinria-workspace-id": workspace_id,
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicit configured Company OS URL
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest response must be an object")
    return payload


def sync_manifest_from_env(
    store: EncryptedLocalStore,
    *,
    owner_id: str,
    workspace_id: str,
    environ: dict[str, str] | None = None,
) -> dict[str, int] | None:
    env = environ or os.environ
    url = env.get("SINRIA_COMPANY_CONTEXT_MANIFEST_URL", "").strip()
    token = (
        env.get("SINRIA_COMPANY_OS_TRANSPORT_TOKEN", "").strip()
        or env.get("COMPANY_OS_BRIDGE_TOKEN", "").strip()
    )
    transport_subject = env.get("SINRIA_COMPANY_OS_TRANSPORT_SUBJECT", "").strip()
    if not url or not token or not transport_subject:
        return None
    payload = fetch_manifest(
        url=url,
        workspace_id=workspace_id,
        token=token,
        transport_subject=transport_subject,
    )
    return sync_manifest(store, owner_id=owner_id, workspace_id=workspace_id, payload=payload)
