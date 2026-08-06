from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from cron.action_runtime import (
    CronAction,
    CronActionState,
    CronActionStore,
    InvalidTransition,
    StaleActionVersion,
)


def make_store(tmp_path):
    return CronActionStore(tmp_path / "actions.db")


def test_create_is_typed_and_survives_reopen(tmp_path):
    store = make_store(tmp_path)
    action = store.create(
        action_id="action-1",
        profile="work",
        payload={"command": "echo secret", "target": "local"},
        expires_at=200.0,
        actor_id="scheduler",
        now=100.0,
    )
    assert isinstance(action, CronAction)
    assert action.state is CronActionState.PROPOSED
    assert action.version == 1
    store.close()

    reopened = CronActionStore(tmp_path / "actions.db")
    assert reopened.get("action-1").payload["command"] == "echo secret"
    assert reopened.get("action-1").profile == "work"


def test_compare_and_swap_transition_increments_version(tmp_path):
    store = make_store(tmp_path)
    store.create("a", "p", {"x": 1}, now=10.0)
    updated = store.transition(
        "a", CronActionState.AWAITING_DECISION, expected_version=1, actor_id="system", now=11
    )
    assert updated.state is CronActionState.AWAITING_DECISION
    assert updated.version == 2
    with pytest.raises(StaleActionVersion):
        store.transition("a", CronActionState.APPROVED, expected_version=1, actor_id="x", now=12)


def test_decision_is_one_winner_under_concurrency(tmp_path):
    db = tmp_path / "actions.db"
    setup = CronActionStore(db)
    setup.create("a", "p", {}, now=1)
    setup.transition("a", CronActionState.AWAITING_DECISION, expected_version=1, now=2)
    setup.close()

    def decide(state):
        store = CronActionStore(db)
        try:
            return store.decide("a", state, actor_id=f"actor-{state.value}", now=3)
        except InvalidTransition:
            return None
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, [CronActionState.APPROVED, CronActionState.REJECTED]))
    assert sum(result is not None for result in results) == 1
    assert CronActionStore(db).get("a").state in {
        CronActionState.APPROVED,
        CronActionState.REJECTED,
    }


def test_execution_lease_is_exclusive_and_can_expire(tmp_path):
    store = make_store(tmp_path)
    store.create("a", "p", {}, now=1)
    store.transition("a", CronActionState.AWAITING_DECISION, expected_version=1, now=2)
    store.decide("a", CronActionState.APPROVED, actor_id="reviewer", now=3)
    leased = store.acquire_execution_lease("a", "worker-1", ttl=10, now=4)
    assert leased.state is CronActionState.EXECUTING
    assert leased.lease_owner == "worker-1"
    with pytest.raises(InvalidTransition):
        store.acquire_execution_lease("a", "worker-2", ttl=10, now=5)
    recovered = store.expire(now=20)[0]
    assert recovered.state is CronActionState.NEEDS_REVIEW


def test_expiry_transitions_pending_action_and_audit_has_no_payload(tmp_path):
    store = make_store(tmp_path)
    store.create("a", "p", {"secret": "do-not-audit"}, expires_at=10, now=1)
    store.transition("a", CronActionState.AWAITING_DECISION, expected_version=1, now=2)
    expired = store.expire(now=10)
    assert expired[0].state is CronActionState.EXPIRED
    row = store.connection.execute(
        "select metadata_json from cron_action_events where action_id = 'a'"
    ).fetchall()
    assert row
    assert all("do-not-audit" not in event[0] for event in row)


def test_profile_default_path_uses_sinria_home(tmp_path, monkeypatch):
    monkeypatch.setattr("sinria_constants.get_sinria_home", lambda: tmp_path)
    store = CronActionStore()
    assert store.db_path == tmp_path / "cron" / "actions.db"
    store.close()


def test_list_actions_can_isolate_profile(tmp_path):
    store = make_store(tmp_path)
    store.create("work-action", "work", {"kind": "work"})
    store.create("personal-action", "personal", {"kind": "personal"})

    assert [action.action_id for action in store.list_actions(profile="work")] == [
        "work-action"
    ]


def _approved(store, action_id="a", *, expires_at=None):
    store.create(action_id, "p", {}, expires_at=expires_at, now=1)
    store.transition(action_id, CronActionState.AWAITING_DECISION, expected_version=1, now=2)
    store.decide(action_id, CronActionState.APPROVED, actor_id="reviewer", now=3)


@pytest.mark.parametrize("state", [
    CronActionState.PROPOSED,
    CronActionState.AWAITING_DECISION,
    CronActionState.REJECTED,
    CronActionState.COMPLETED,
    CronActionState.FAILED,
])
def test_acquire_only_allows_approved_or_needs_review_recovery(tmp_path, state):
    store = make_store(tmp_path)
    store.create("a", "p", {}, now=1)
    if state is not CronActionState.PROPOSED:
        store.transition("a", CronActionState.AWAITING_DECISION, expected_version=1, now=2)
    if state is CronActionState.APPROVED:
        store.decide("a", state, actor_id="reviewer", now=3)
    elif state is CronActionState.REJECTED:
        store.decide("a", state, actor_id="reviewer", now=3)
    elif state in {CronActionState.COMPLETED, CronActionState.FAILED}:
        store.decide("a", CronActionState.APPROVED, actor_id="reviewer", now=3)
        leased = store.acquire_execution_lease("a", "worker", now=4)
        if state is CronActionState.COMPLETED:
            store.release_execution_lease("a", "worker", leased.lease_token, outcome=CronActionState.VERIFYING, now=5)
            store.transition("a", CronActionState.COMPLETED, expected_version=5, now=6)
        else:
            store.release_execution_lease("a", "worker", leased.lease_token, outcome=state, now=5)
    with pytest.raises(InvalidTransition):
        store.acquire_execution_lease("a", "worker", now=6)
    assert store.get("a").state is state


def test_needs_review_can_be_intentionally_recovered(tmp_path):
    store = make_store(tmp_path)
    _approved(store)
    leased = store.acquire_execution_lease("a", "worker", ttl=1, now=4)
    store.expire(now=5)
    assert store.get("a").state is CronActionState.NEEDS_REVIEW
    recovered = store.acquire_execution_lease("a", "worker-2", now=6)
    assert recovered.state is CronActionState.EXECUTING


def test_cross_store_lease_cas_has_one_winner_and_one_event(tmp_path):
    db = tmp_path / "actions.db"
    setup = CronActionStore(db)
    _approved(setup)
    setup.close()

    def acquire(owner):
        store = CronActionStore(db)
        try:
            return store.acquire_execution_lease("a", owner, now=4)
        except (InvalidTransition, StaleActionVersion):
            return None
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ["one", "two"]))
    assert sum(result is not None for result in results) == 1
    check = CronActionStore(db)
    events = check.connection.execute(
        "select event_type from cron_action_events where action_id='a' and event_type='lease_acquired'"
    ).fetchall()
    assert len(events) == 1
    check.close()


def test_expiry_is_enforced_by_decide_transition_and_acquire(tmp_path):
    store = make_store(tmp_path)
    store.create("a", "p", {}, expires_at=3, now=1)
    store.transition("a", CronActionState.AWAITING_DECISION, expected_version=1, now=2)
    with pytest.raises(InvalidTransition):
        store.decide("a", CronActionState.APPROVED, actor_id="reviewer", now=3)
    assert store.get("a").state is CronActionState.EXPIRED

    _approved(store, "b", expires_at=5)
    with pytest.raises(InvalidTransition):
        store.acquire_execution_lease("b", "worker", now=5)
    assert store.get("b").state is CronActionState.EXPIRED


def test_proposed_actions_expire_without_sweeper_transition_error(tmp_path):
    store = make_store(tmp_path)
    store.create("a", "p", {}, expires_at=10, now=1)
    expired = store.expire(now=10)
    assert len(expired) == 1
    assert expired[0].state is CronActionState.EXPIRED


def test_lease_renew_and_release_reject_expired_lease(tmp_path):
    store = make_store(tmp_path)
    _approved(store)
    leased = store.acquire_execution_lease("a", "worker", ttl=1, now=4)
    with pytest.raises(InvalidTransition):
        store.renew_execution_lease("a", "worker", leased.lease_token, now=5)
    with pytest.raises(InvalidTransition):
        store.release_execution_lease("a", "worker", leased.lease_token, now=5)
    assert store.get("a").state is CronActionState.EXECUTING


def test_audit_metadata_is_allowlisted_and_control_safe(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr("cron.action_runtime.record_audit_event", lambda *args, **kwargs: seen.append((args, kwargs)))
    store = make_store(tmp_path)
    store.create("a", "p", {"secret": "never persist to metadata"}, actor_id="actor\nunsafe", now=1)
    row = store.connection.execute("select metadata_json from cron_action_events where action_id='a'").fetchone()
    metadata = json.loads(row[0])
    assert set(metadata) <= {"profile", "lease_owner", "lease_expires_at", "payload_sha256"}
    assert all(not any(ord(char) < 32 for char in str(value)) for value in metadata.values())
    assert "never persist to metadata" not in json.dumps(seen)


def test_cron_directory_db_and_sqlite_sidecars_are_owner_only(tmp_path):
    store = make_store(tmp_path)
    store.create("a", "p", {}, now=1)
    store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.close()
    cron_dir = tmp_path
    assert os.stat(cron_dir).st_mode & 0o777 == 0o700
    assert os.stat(tmp_path / "actions.db").st_mode & 0o777 == 0o600
    for sidecar in (tmp_path / "actions.db-wal", tmp_path / "actions.db-shm"):
        if sidecar.exists():
            assert os.stat(sidecar).st_mode & 0o777 == 0o600


def test_update_payload_is_version_checked_and_pending_actions_are_queryable(tmp_path):
    store = make_store(tmp_path)
    action = store.create(
        action_id="action-delivery",
        profile="default",
        payload={"job_id": "job-1", "summary": "Approval required"},
        expires_at=500.0,
        now=100.0,
    )
    waiting = store.transition(
        action.action_id,
        CronActionState.AWAITING_DECISION,
        expected_version=action.version,
        now=101.0,
    )

    delivered = store.update_payload(
        action.action_id,
        {"delivery": {"platform": "discord", "chat_id": "42", "message_id": "99"}},
        expected_version=waiting.version,
        now=102.0,
    )

    assert delivered.version == waiting.version + 1
    assert delivered.payload["job_id"] == "job-1"
    assert delivered.payload["delivery"]["message_id"] == "99"
    pending = store.list_actions(states={CronActionState.AWAITING_DECISION})
    assert [item.action_id for item in pending] == [action.action_id]
    with pytest.raises(StaleActionVersion):
        store.update_payload(action.action_id, {"delivery": {}}, expected_version=waiting.version)
