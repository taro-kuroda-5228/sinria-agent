"""Tests for tools.approval_store — file-backed sanitized approval queue."""

import json

import pytest

import tools.approval_store as store


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "get_sinria_home", lambda: tmp_path)
    return tmp_path


def _record(approval_id="abc123", command="rm -rf ./build"):
    store.record_pending(
        approval_id,
        "discord:42",
        {
            "command": command,
            "description": "Recursive delete",
            "pattern_keys": ["rm_rf"],
        },
    )


class TestRecordPending:
    def test_writes_sanitized_record(self, tmp_path):
        _record()
        path = tmp_path / "approvals" / "pending" / "abc123.json"
        assert path.exists()
        rec = json.loads(path.read_text())
        assert rec["id"] == "abc123"
        assert rec["session_key"] == "discord:42"
        assert rec["command_preview"] == "rm -rf ./build"
        assert rec["truncated"] is False
        assert len(rec["command_sha256"]) == 64
        assert rec["pattern_keys"] == ["rm_rf"]
        assert "T" in rec["requested_at"]
        # metadata や生の追加キーは保存されない
        assert "metadata" not in rec
        assert "command" not in rec

    def test_long_command_is_truncated_with_digest(self, tmp_path):
        _record(approval_id="big", command="x" * 5000)
        rec = json.loads((tmp_path / "approvals" / "pending" / "big.json").read_text())
        assert len(rec["command_preview"]) == store.PREVIEW_LIMIT
        assert rec["truncated"] is True
        assert len(rec["command_sha256"]) == 64

    def test_collaboration_binding_omits_raw_preview_and_survives_readback(self, tmp_path):
        store.record_pending(
            "bound",
            "discord:42",
            {
                "command": "secret-sensitive-command",
                "description": "dangerous command",
                "metadata": {
                    "work_item_id": "task-1",
                    "work_item_version": 7,
                    "requester_actor_id": "owner",
                    "required_capability": "review",
                    "payload_sha256": "a" * 64,
                    "require_distinct_approver": True,
                    "allowed_role_ids": ["role-1"],
                },
            },
        )
        rec = json.loads(
            (tmp_path / "approvals" / "pending" / "bound.json").read_text()
        )
        assert rec["command_preview"] == ""
        assert "secret-sensitive-command" not in json.dumps(rec)
        assert rec["collaboration_binding"]["work_item_version"] == 7
        assert rec["collaboration_binding"]["allowed_role_ids"] == ["role-1"]

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(store, "get_sinria_home", lambda: (_ for _ in ()).throw(RuntimeError))
        store.record_pending("x", "s", {"command": "ls"})  # must not raise


class TestListPending:
    def test_lists_sorted_by_requested_at(self):
        _record("a1")
        _record("a2")
        ids = [r["id"] for r in store.list_pending()]
        assert ids == sorted(ids) == ["a1", "a2"]

    def test_stale_entries_are_dropped(self, tmp_path):
        _record("old")
        p = tmp_path / "approvals" / "pending" / "old.json"
        rec = json.loads(p.read_text())
        rec["requested_at"] = "2000-01-01T00:00:00+00:00"
        p.write_text(json.dumps(rec))
        assert store.list_pending(max_age_seconds=60) == []
        assert not p.exists()  # stale は削除される

    def test_stale_cleanup_removes_orphan_response(self, tmp_path):
        # Write a pending record with an associated response, then age the
        # pending entry.  list_pending must remove BOTH files so the
        # responses/ directory does not accumulate orphans after a crash.
        _record("stale_with_resp")
        assert store.write_response("stale_with_resp", "once") is True
        pending_path = tmp_path / "approvals" / "pending" / "stale_with_resp.json"
        response_path = tmp_path / "approvals" / "responses" / "stale_with_resp.json"
        assert response_path.exists()
        # Age the pending entry past max_age
        rec = json.loads(pending_path.read_text())
        rec["requested_at"] = "2000-01-01T00:00:00+00:00"
        pending_path.write_text(json.dumps(rec))
        store.list_pending(max_age_seconds=60)
        assert not pending_path.exists()
        assert not response_path.exists()

    def test_corrupt_file_is_skipped(self, tmp_path):
        _record("ok")
        (tmp_path / "approvals" / "pending" / "bad.json").write_text("{not json")
        ids = [r["id"] for r in store.list_pending()]
        assert ids == ["ok"]

    def test_empty_when_dir_missing(self):
        assert store.list_pending() == []


class TestResponses:
    def test_write_then_poll_roundtrip(self, tmp_path):
        _record("r1")
        assert store.write_response("r1", "deny") is True
        assert store.poll_response("r1") == "deny"
        # read+delete: 2回目は None
        assert store.poll_response("r1") is None

    def test_write_requires_pending(self):
        assert store.write_response("ghost", "once") is False

    def test_write_rejects_bad_choice(self):
        _record("r2")
        assert store.write_response("r2", "always") is False
        assert store.write_response("r2", "session") is False
        assert store.poll_response("r2") is None

    def test_clear_pending_removes_file(self, tmp_path):
        _record("c1")
        store.clear_pending("c1")
        assert store.list_pending() == []
        store.clear_pending("c1")  # idempotent / never raises


class TestPathSafety:
    def test_malformed_approval_id_is_rejected(self, tmp_path):
        # Invalid identifiers must not alias a valid ID through normalization.
        store.record_pending("../../evil", "s", {"command": "ls"})
        outside = tmp_path / "evil.json"
        assert not outside.exists()
        pending_dir = tmp_path / "approvals" / "pending"
        assert not pending_dir.exists()
