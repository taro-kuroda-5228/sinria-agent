"""Operator reconciliation for indeterminate LINE peer sends."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from gateway.line_peer_routing import LinePeerDeliveryGate


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile-sinria-line-peer-delivery.py"


def _load():
    spec = importlib.util.spec_from_file_location("reconcile_line_peer", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconcile_requires_explicit_human_confirmation(tmp_path):
    module = _load()
    path = tmp_path / "delivery.sqlite3"
    conversation = "sha256:" + "a" * 64
    message = "sha256:" + "b" * 64
    gate = LinePeerDeliveryGate(path)
    gate.claim(conversation, message, "c" * 64)
    gate.transition(conversation, message, "sending")
    gate.close()

    with pytest.raises(SystemExit, match="human review"):
        module.reconcile(path, conversation, message, "retry", human_confirmed=False)
    receipt = module.reconcile(
        path, conversation, message, "retry", human_confirmed=True
    )
    assert receipt == {
        "ok": True,
        "decision": "retry",
        "conversationRef": conversation,
        "messageRef": message,
        "rawContextStored": False,
    }
