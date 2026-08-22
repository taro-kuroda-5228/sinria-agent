from __future__ import annotations

import pytest

from agent.company_context import knowledge_manifest
from agent.company_context.knowledge_manifest import SOURCE, sync_manifest, sync_manifest_from_env
from agent.company_context.retriever import ContextProvider
from agent.company_context.runtime import CompanyContextRuntime, ContextIdentity, ContextRuntimeConfig
from agent.company_context.store import EncryptedLocalStore, KeyProvider


def _payload(entries):
    return {
        "ok": True,
        "manifest": {
            "workspaceId": "medical-horizon",
            "generatedAt": "2026-08-20T03:00:00Z",
            "metadataOnly": True,
            "entries": entries,
            "reviewQueue": {"pendingCount": 0, "expiredCount": 0},
            "safety": {
                "rawContextStored": False,
                "rawEvidenceStored": False,
                "rawSkillBodyStored": False,
                "credentialStoredInCloud": False,
                "externalActionPerformed": False,
            },
        },
    }


def _entry(knowledge_id="asset-1", *, expires_at="2099-01-01T00:00:00Z"):
    return {
        "knowledgeId": knowledge_id,
        "assetKind": "validated_insight",
        "title": "病院PoCの進め方",
        "sanitizedSummary": "承認済み基盤で限定的に開始する approved-baseline",
        "confidence": "high",
        "scopeKeys": ["product"],
        "reuseTargets": ["company_context"],
        "citationRefs": ["drive:file-fingerprint"],
        "ownerMemberId": "member_taro",
        "reviewedByMemberId": "member_kikuchi",
        "reviewedAt": "2026-08-20T02:00:00Z",
        "expiresAt": expires_at,
        "version": 2,
    }


def _store(tmp_path):
    return EncryptedLocalStore(
        tmp_path / "context.db",
        KeyProvider(b"k" * 32, profile_id="profile-taro"),
        profile_id="profile-taro",
        workspace_id="medical-horizon",
    )


def test_reviewed_manifest_reaches_real_turn_context_with_citation(tmp_path):
    store = _store(tmp_path)
    assert sync_manifest(
        store,
        owner_id="member_taro",
        workspace_id="medical-horizon",
        payload=_payload([_entry()]),
    ) == {"upserted": 1, "removed": 0}

    runtime = CompanyContextRuntime(
        ContextRuntimeConfig(
            enabled=True,
            profile_id="profile-taro",
            workspace_id="medical-horizon",
            owner_id="member_taro",
        ),
        ContextProvider(store),
    )
    message = runtime.message_for_turn(
        "approved-baseline",
        ContextIdentity(
            profile_id="profile-taro",
            workspace_id="medical-horizon",
            owner_id="member_taro",
            session_id="session-1",
        ),
    )
    assert message is not None
    assert "承認済み基盤" in message["content"]
    assert "drive:file-fingerprint" in message["content"]


def test_manifest_sync_prunes_revoked_entries_without_touching_other_sources(tmp_path):
    store = _store(tmp_path)
    store.put("personal", "member_taro", "個人のローカル文脈 personal-baseline", source="personal")
    sync_manifest(store, owner_id="member_taro", workspace_id="medical-horizon", payload=_payload([_entry()]))
    result = sync_manifest(store, owner_id="member_taro", workspace_id="medical-horizon", payload=_payload([]))

    assert result == {"upserted": 0, "removed": 1}
    assert store.search("member_taro", "approved-baseline") == []
    assert len(store.search("member_taro", "personal-baseline")) == 1
    row = store.db.execute("SELECT source FROM context_documents").fetchone()
    assert row["source"] != SOURCE


def test_expired_or_malformed_expiry_is_suppressed_locally(tmp_path):
    store = _store(tmp_path)
    sync_manifest(
        store,
        owner_id="member_taro",
        workspace_id="medical-horizon",
        payload=_payload([_entry(expires_at="2020-01-01T00:00:00Z")]),
    )
    provider = ContextProvider(store)
    assert provider.retrieve(owner_id="member_taro", query="approved-baseline") == []


def test_manifest_sync_rejects_local_store_workspace_mismatch(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="local store workspace mismatch"):
        sync_manifest(
            store,
            owner_id="member_taro",
            workspace_id="other-workspace",
            payload=_payload([]),
        )


def test_environment_sync_prefers_subject_specific_transport_token(tmp_path, monkeypatch):
    store = _store(tmp_path)
    captured = {}

    def fake_fetch_manifest(**kwargs):
        captured.update(kwargs)
        return _payload([])

    monkeypatch.setattr(knowledge_manifest, "fetch_manifest", fake_fetch_manifest)
    result = sync_manifest_from_env(
        store,
        owner_id="member_taro",
        workspace_id="medical-horizon",
        environ={
            "SINRIA_COMPANY_CONTEXT_MANIFEST_URL": "https://company-os.example.test/api/knowledge-assets/manifest",
            "SINRIA_COMPANY_OS_TRANSPORT_TOKEN": "subject-token",
            "COMPANY_OS_BRIDGE_TOKEN": "legacy-shared-token",
            "SINRIA_COMPANY_OS_TRANSPORT_SUBJECT": "profile-taro",
        },
    )

    assert result == {"upserted": 0, "removed": 0}
    assert captured["token"] == "subject-token"
    assert captured["transport_subject"] == "profile-taro"
