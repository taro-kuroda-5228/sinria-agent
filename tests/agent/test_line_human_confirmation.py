import sqlite3

from agent.line_human_confirmation import LineHumanConfirmationStore


def test_line_api_acceptance_is_not_human_confirmation(tmp_path):
    store = LineHumanConfirmationStore(tmp_path / "confirm.sqlite3")
    receipt = store.register(
        conversation_id="Cgroup",
        source_type="group",
        expected_sender_id="Ukikuchi",
        expected_reply="Sinria接続準備完了",
        outbound_message_id="m-out",
        purpose="peer_onboarding",
        now_ms=1000,
    )

    assert receipt.status == "api_accepted"
    assert receipt.human_confirmed is False


def test_unrelated_or_unquoted_reply_cannot_confirm(tmp_path):
    store = LineHumanConfirmationStore(tmp_path / "confirm.sqlite3")
    receipt = store.register(
        conversation_id="Cgroup",
        source_type="group",
        expected_sender_id="Ukikuchi",
        expected_reply="Sinria接続準備完了",
        outbound_message_id="m-out",
        purpose="peer_onboarding",
        now_ms=1000,
    )

    assert store.observe(
        conversation_id="Cother", source_type="group", sender_id="Ukikuchi",
        text="Sinria接続準備完了", quoted_message_id="m-out", inbound_message_id="m1", now_ms=1100,
    ) is None
    assert store.observe(
        conversation_id="Cgroup", source_type="group", sender_id="Ukikuchi",
        text="了解です！", quoted_message_id="m-out", inbound_message_id="m2", now_ms=1200,
    ) is None
    assert store.observe(
        conversation_id="Cgroup", source_type="group", sender_id="Ukikuchi",
        text="Sinria接続準備完了", quoted_message_id="", inbound_message_id="m3", now_ms=1300,
    ) is None
    assert store.get(receipt.confirmation_id).status == "api_accepted"


def test_exact_quoted_reply_from_expected_human_confirms(tmp_path):
    store = LineHumanConfirmationStore(tmp_path / "confirm.sqlite3")
    pending = store.register(
        conversation_id="Cgroup",
        source_type="group",
        expected_sender_id="Ukikuchi",
        expected_reply="Sinria接続準備完了",
        outbound_message_id="m-out",
        purpose="peer_onboarding",
        now_ms=1000,
    )

    confirmed = store.observe(
        conversation_id="Cgroup", source_type="group", sender_id="Ukikuchi",
        text="Sinria接続準備完了", quoted_message_id="m-out", inbound_message_id="m-in", now_ms=1400,
    )

    assert confirmed is not None
    assert confirmed.confirmation_id == pending.confirmation_id
    assert confirmed.status == "human_replied"
    assert confirmed.human_confirmed is True
    raw = (tmp_path / "confirm.sqlite3").read_bytes()
    for forbidden in (b"Cgroup", b"Ukikuchi", b"m-out", b"m-in", "Sinria接続準備完了".encode()):
        assert forbidden not in raw


def test_same_inbound_message_is_idempotent(tmp_path):
    store = LineHumanConfirmationStore(tmp_path / "confirm.sqlite3")
    store.register(
        conversation_id="Cgroup", source_type="group", expected_sender_id="Ukikuchi",
        expected_reply="Sinria接続準備完了", outbound_message_id="m-out",
        purpose="peer_onboarding", now_ms=1000,
    )
    kwargs = dict(
        conversation_id="Cgroup", source_type="group", sender_id="Ukikuchi",
        text="Sinria接続準備完了", quoted_message_id="m-out", inbound_message_id="m-in", now_ms=1400,
    )
    first = store.observe(**kwargs)
    second = store.observe(**kwargs)
    assert first == second


def test_expired_confirmation_cannot_be_confirmed(tmp_path):
    store = LineHumanConfirmationStore(tmp_path / "confirm.sqlite3", ttl_seconds=60)
    pending = store.register(
        conversation_id="Cgroup", source_type="group", expected_sender_id="Ukikuchi",
        expected_reply="Sinria接続準備完了", outbound_message_id="m-out",
        purpose="peer_onboarding", now_ms=1000,
    )
    assert store.observe(
        conversation_id="Cgroup", source_type="group", sender_id="Ukikuchi",
        text="Sinria接続準備完了", quoted_message_id="m-out", inbound_message_id="m-in", now_ms=62001,
    ) is None
    assert store.get(pending.confirmation_id, now_ms=62001).status == "expired"
