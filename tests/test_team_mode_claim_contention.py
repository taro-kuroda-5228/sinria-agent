"""Two instances racing for one task: exactly one claim wins, the loser gets a
clean rejection (never a silent double-execution).

オペレータパケット検証項目: _claim_one の実 PostgreSQL での 2 接続同時 claim 競合
(FOR UPDATE OF agent_tasks SKIP LOCKED が片方だけ claim 成功すること)は、この
マシンから本番 PG へ接続できないため、実機(本番 PG への 2 接続)で別途検証する。"""
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


def _load(name, rel):
    path = Path(__file__).resolve().parents[1] / rel
    return SourceFileLoader(name, str(path)).load_module()


@pytest.fixture(scope="module")
def smoke():
    return _load("smoke_team_mode", "scripts/smoke_team_mode_local_routing.py")


def test_second_instance_claim_is_rejected(smoke):
    store = smoke.build_mock_store_with_single_task(
        target_member_id="member_taro", target_instance_id=None
    )
    first = smoke.attempt_claim(store, member_id="member_taro", instance_id="inst_macbook")
    second = smoke.attempt_claim(store, member_id="member_taro", instance_id="inst_macmini")
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"]  # a clean machine-readable reason, not a crash


def test_wrong_member_claim_is_rejected(smoke):
    store = smoke.build_mock_store_with_single_task(
        target_member_id="member_taro", target_instance_id=None
    )
    res = smoke.attempt_claim(store, member_id="member_kikuchi", instance_id="inst_k1")
    assert res["ok"] is False


def test_instance_pinned_task_rejects_other_instance(smoke):
    store = smoke.build_mock_store_with_single_task(
        target_member_id="member_taro", target_instance_id="inst_macbook"
    )
    res = smoke.attempt_claim(store, member_id="member_taro", instance_id="inst_other")
    assert res["ok"] is False
    ok = smoke.attempt_claim(store, member_id="member_taro", instance_id="inst_macbook")
    assert ok["ok"] is True


def test_shared_queue_task_claimed_by_exactly_one_instance(smoke):
    # A shared_queue task carries no member/instance target. Two unrelated
    # instances race; the first wins, the second gets a clean rejection — never
    # a silent double-claim.
    store = smoke.build_mock_store_with_single_task(
        target_member_id=None, target_instance_id=None, execution_mode="shared_queue"
    )
    first = smoke.attempt_claim(store, member_id="member_taro", instance_id="inst_macbook")
    second = smoke.attempt_claim(store, member_id="member_kikuchi", instance_id="inst_k1")
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"]


def test_member_routed_task_not_taken_by_unrelated_member(smoke):
    # A member-routed task must NOT be claimed by a different member even when
    # that member would happily drain a shared queue. (Guards against treating
    # every null-instance task as shared.)
    store = smoke.build_mock_store_with_single_task(
        target_member_id="member_taro", target_instance_id=None, execution_mode="member"
    )
    res = smoke.attempt_claim(store, member_id="member_kikuchi", instance_id="inst_k1")
    assert res["ok"] is False
    assert res["reason"]
    # The rightful member still claims it.
    ok = smoke.attempt_claim(store, member_id="member_taro", instance_id="inst_macbook")
    assert ok["ok"] is True
