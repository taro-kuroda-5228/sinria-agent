from __future__ import annotations

import importlib.util
from pathlib import Path

from agent.company_context.operations import ContextLedger


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "company_growth_worker.py"
spec = importlib.util.spec_from_file_location("company_growth_worker", SCRIPT)
assert spec and spec.loader
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def test_dry_run_does_not_claim(tmp_path):
    db = tmp_path / "ops.db"
    ledger = ContextLedger(db)
    job_id = ledger.enqueue("profile-a", "jml", {"member_id": "member-a", "state": "active"}, key="jml-member-a")
    ledger.close()

    receipt = worker.run_tick(profile="profile-a", db_path=db, owner="worker-a")
    assert receipt == {
        "ok": True,
        "mode": "dry-run",
        "queued": 1,
        "rawContextStored": False,
        "rawLocatorStored": False,
        "credentialStored": False,
    }
    check = ContextLedger(db)
    assert check.db.execute("SELECT status FROM context_jobs WHERE job_id=?", (job_id,)).fetchone()[0] == "queued"
    check.close()


def test_execute_local_is_durable_and_idempotent(tmp_path):
    db = tmp_path / "ops.db"
    ledger = ContextLedger(db)
    job_id = ledger.enqueue("profile-a", "jml", {"member_id": "member-a", "state": "active"}, key="jml-member-a")
    ledger.close()

    receipt = worker.run_tick(profile="profile-a", db_path=db, owner="worker-a", execute=True)
    assert receipt["ok"] is True
    assert receipt["jobId"] == job_id
    assert receipt["rawContextStored"] is False

    again = worker.run_tick(profile="profile-a", db_path=db, owner="worker-b", execute=True)
    assert again == {"ok": True, "mode": "execute-local", "processed": 0,
                     "rawContextStored": False, "rawLocatorStored": False, "credentialStored": False}
    check = ContextLedger(db)
    assert check.db.execute("SELECT state FROM context_members WHERE profile='profile-a' AND member_id='member-a'").fetchone()[0] == "active"
    check.close()


def test_google_drive_sync_rejects_cross_profile_store_before_source_or_ledger(tmp_path):
    from agent.company_context.store import EncryptedLocalStore, KeyProvider

    class ExplodingDrive:
        def changes_since(self, token):
            raise AssertionError("cross-profile store must be rejected before Drive access")

    class Checkpoint:
        value = None
        def advance(self, value):
            raise AssertionError("checkpoint must not advance")

    store = EncryptedLocalStore(tmp_path / "encrypted.db", KeyProvider(b"k" * 32, profile_id="profile-b"), profile_id="profile-b")
    ledger = ContextLedger(tmp_path / "ops.db")
    ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-cross-profile")
    ledger.close()
    receipt = worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-a", execute=True,
                              drive_source=ExplodingDrive(), checkpoint=Checkpoint(), store=store)
    assert receipt["ok"] is False
    assert receipt["errorType"] == "OwnerMismatchError"
    audit = ContextLedger(tmp_path / "ops.db")
    assert audit.db.execute("SELECT count(*) FROM context_events WHERE kind='google_drive_context_stored'").fetchone()[0] == 0
    audit.close()
    store.close()


def test_google_drive_sync_rejects_change_owner_before_store_event_or_checkpoint(tmp_path):
    class Drive:
        def changes_since(self, token):
            return {"changes": [{"change_id": "foreign-change", "owner_id": "profile-b", "doc_id": "doc-1", "text": "secret"}],
                    "next_token": "must-not-save"}

    class Store:
        profile_id = "profile-a"
        def put(self, *args, **kwargs):
            raise AssertionError("foreign change must be rejected before storage")

    class Checkpoint:
        value = None
        def advance(self, value):
            raise AssertionError("foreign change must not advance checkpoint")

    ledger = ContextLedger(tmp_path / "ops.db")
    ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-owner-mismatch")
    ledger.close()
    receipt = worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-a", execute=True,
                              drive_source=Drive(), checkpoint=Checkpoint(), store=Store())
    assert receipt["ok"] is False
    assert receipt["errorType"] == "OwnerMismatchError"
    audit = ContextLedger(tmp_path / "ops.db")
    assert audit.db.execute("SELECT count(*) FROM context_events WHERE kind='google_drive_context_stored'").fetchone()[0] == 0
    audit.close()


def test_google_drive_sync_accepts_json_checkpoint_shape(tmp_path):
    from agent.company_context.google_adapters import JsonCheckpoint

    class Drive:
        def changes_since(self, token):
            assert token is None
            return {"changes": [{"change_id": "adapter-change", "owner_id": "profile-a", "doc_id": "doc-1", "text": "safe"}],
                    "next_token": "adapter-token"}

    class Store:
        profile_id = "profile-a"
        def __init__(self): self.rows = []
        def put(self, *args, **kwargs): self.rows.append((args, kwargs))

    checkpoint = JsonCheckpoint(tmp_path / "checkpoint.json")
    store = Store()
    ledger = ContextLedger(tmp_path / "ops.db")
    ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-adapter-shape")
    ledger.close()
    receipt = worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-a", execute=True,
                              drive_source=Drive(), checkpoint=checkpoint, store=store)
    assert receipt["ok"] is True
    assert checkpoint.cursor == "adapter-token"
    assert len(store.rows) == 1


def test_execute_no_work_receipt_is_metadata_only(tmp_path):
    receipt = worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-a", execute=True)
    assert receipt == {"ok": True, "mode": "execute-local", "processed": 0,
                       "rawContextStored": False, "rawLocatorStored": False, "credentialStored": False}


def test_unsupported_job_retries_without_raw_error(tmp_path):
    db = tmp_path / "ops.db"
    ledger = ContextLedger(db)
    job_id = ledger.enqueue("profile-a", "provider_send", {"body": "must-not-leak"}, key="provider-send-a")
    ledger.close()

    receipt = worker.run_tick(profile="profile-a", db_path=db, owner="worker-a", execute=True)
    assert receipt == {
        "ok": False,
        "mode": "execute-local",
        "processed": 0,
        "jobId": job_id,
        "errorType": "ValueError",
        "rawContextStored": False,
        "rawLocatorStored": False,
        "credentialStored": False,
    }
    assert "must-not-leak" not in str(receipt)


def test_company_os_transport_job_is_processed_with_readback(tmp_path):
    from agent.company_context.policy import WorkspaceIdentity
    from agent.company_context.state import LocalSyncState
    from agent.company_context.transport import CompanyOsTransport, FakeHttp

    db = tmp_path / "ops.db"
    ledger = ContextLedger(db)
    job_id = ledger.enqueue(
        "profile-a",
        "company_os_task",
        {
            "task_kind": "knowledge_review",
            "title": "Review candidate",
            "instruction": "Review sanitized candidate metadata",
            "summary": "Candidate ready for review",
            "result_refs": ["candidate:candidate-a"],
            "idempotency_key": "company-task-a",
            "human_approval_required": True,
        },
        key="company-task-a",
    )
    ledger.close()
    fake = FakeHttp()
    transport = CompanyOsTransport(
        "https://company-os.invalid",
        identity=WorkspaceIdentity("workspace-a", "member-a", "instance-a"),
        bridge_token="test-only-credential",
        state=LocalSyncState(tmp_path / "transport.json"),
        http=fake,
    )
    receipt = worker.run_tick(
        profile="profile-a",
        db_path=db,
        owner="worker-a",
        execute=True,
        company_os=transport,
    )
    assert receipt["ok"] is True
    assert receipt["jobId"] == job_id
    assert fake.tasks["company-task-a"]["status"] == "waiting_review"
    assert "test-only-credential" not in (tmp_path / "transport.json").read_text()


def test_google_drive_sync_is_encrypted_checkpointed_restart_safe_and_retryable(tmp_path):
    from agent.company_context.retriever import ContextProvider
    from agent.company_context.store import EncryptedLocalStore, KeyProvider

    class FakeDrive:
        def __init__(self, changes):
            self.changes = changes
            self.calls = 0

        def changes_since(self, token):
            self.calls += 1
            return {"changes": self.changes, "next_token": "next-1"} if token is None else {"changes": []}

    class DurableCheckpoint:
        def __init__(self):
            self.value = None
            self.history = []

        def advance(self, value):
            self.history.append(value)
            self.value = value

    class FailingStore:
        def __init__(self, store):
            self.store = store
            self.failed = True

        def put(self, *args, **kwargs):
            if self.failed:
                self.failed = False
                raise OSError("synthetic store failure")
            return self.store.put(*args, **kwargs)

    plaintext = "drive-private-content-sentinel"
    locator = "https://drive.invalid/private-locator-sentinel"
    change = {
        "change_id": "change-1",
        "owner_id": "profile-a",
        "doc_id": "drive-doc-1",
        "text": plaintext,
        "metadata": {"title": "Quarterly plan"},
        "locator": locator,
    }
    checkpoint = DurableCheckpoint()
    drive = FakeDrive([change])
    key_provider = KeyProvider(b"k" * 32, profile_id="profile-a")
    store_path = tmp_path / "encrypted.db"
    store = EncryptedLocalStore(store_path, key_provider, profile_id="profile-a")
    ledger = ContextLedger(tmp_path / "ops.db")
    job_id = ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-sync-1")
    ledger.close()

    receipt = worker.run_tick(
        profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-a", execute=True,
        drive_source=drive, checkpoint=checkpoint, store=store,
    )
    assert receipt["ok"] is True
    assert receipt["jobId"] == job_id
    assert checkpoint.value == "next-1"
    assert checkpoint.history == ["next-1"]
    assert all(receipt.get(flag) is False for flag in ("rawContextStored", "rawLocatorStored", "credentialStored"))
    audit = ContextLedger(tmp_path / "ops.db")
    events = audit.db.execute("SELECT subject, data FROM context_events").fetchall()
    assert all(plaintext not in str(row) and locator not in str(row) for row in events)
    assert all("credential" not in str(row).lower() for row in events)
    audit.close()

    store.close()
    reopened = EncryptedLocalStore(store_path, key_provider, profile_id="profile-a")
    results = ContextProvider(reopened).retrieve(owner_id="profile-a", query=plaintext)
    assert [item["text"] for item in results] == [plaintext]
    assert store_path.read_bytes().find(plaintext.encode()) == -1
    assert store_path.read_bytes().find(locator.encode()) == -1
    assert reopened.db.execute("SELECT count(*) FROM context_documents").fetchone()[0] == 1
    reopened.close()

    # A second durable job with the same stable change is a no-op.
    ledger = ContextLedger(tmp_path / "ops.db")
    ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-sync-2")
    ledger.close()
    store = EncryptedLocalStore(store_path, key_provider, profile_id="profile-a")
    assert worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-b", execute=True, drive_source=drive, checkpoint=checkpoint, store=store)["ok"] is True
    assert store.db.execute("SELECT count(*) FROM context_documents").fetchone()[0] == 1
    store.close()

    # Persistence failure is retried and does not move the checkpoint.
    failed_checkpoint = DurableCheckpoint()
    failing_store = FailingStore(EncryptedLocalStore(tmp_path / "failed.db", key_provider, profile_id="profile-a"))
    ledger = ContextLedger(tmp_path / "ops.db")
    ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-sync-3")
    ledger.close()
    failed = worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-c", execute=True, drive_source=FakeDrive([{**change, "change_id": "change-fail"}]), checkpoint=failed_checkpoint, store=failing_store)
    assert failed["ok"] is False
    assert failed_checkpoint.value is None
    assert ContextLedger(tmp_path / "ops.db").db.execute("SELECT status FROM context_jobs WHERE idempotency_key='drive-sync-3'").fetchone()[0] == "retry"
    failing_store.store.close()


def test_google_drive_event_subject_redacts_untrusted_change_locator(tmp_path):
    class Drive:
        def changes_since(self, token):
            return {"changes": [{
                "change_id": "https://drive.invalid/private/raw-locator",
                "owner_id": "profile-a", "doc_id": "doc-1", "text": "safe",
            }]}

    class Store:
        profile_id = "profile-a"
        def put(self, *args, **kwargs): pass

    class Checkpoint:
        value = None

    ledger = ContextLedger(tmp_path / "ops.db")
    ledger.enqueue("profile-a", "google_drive_sync", {}, key="drive-redaction")
    ledger.close()
    assert worker.run_tick(profile="profile-a", db_path=tmp_path / "ops.db", owner="worker-a",
                           execute=True, drive_source=Drive(), checkpoint=Checkpoint(), store=Store())["ok"]
    audit = ContextLedger(tmp_path / "ops.db")
    row = audit.db.execute("SELECT subject, data FROM context_events WHERE kind='google_drive_context_stored'").fetchone()
    assert "https://drive.invalid/private/raw-locator" not in str(row)
    assert row["subject"] != "https://drive.invalid/private/raw-locator"
    assert row["subject"].startswith("drive-change-")
    assert len(row["subject"]) == len("drive-change-") + 64
    audit.close()
