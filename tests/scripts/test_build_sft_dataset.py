"""Tests for scripts/build_sft_dataset.py (P2, Task B)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_sft_dataset", REPO_ROOT / "scripts" / "build_sft_dataset.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trajectory():
    return {
        "meta": {"model": "qwen2.5-7b", "tier": "small", "timestamp": "2026-07-07T00:00:00Z",
                 "session_id": "s1", "provider": "custom"},
        "turns": [
            {"role": "user", "content": "Fix the failing build now"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "terminal", "arguments": "{\"command\": \"make test\"}"}],
            },
            {"role": "tool", "name": "terminal", "content": "tests passed"},
            {"role": "assistant", "content": "Verified: the test suite passed."},
        ],
    }


def test_example_uses_text_tool_call_contract():
    mod = _load_module()
    example = mod.trajectory_to_example(_trajectory())
    messages = example["messages"]
    assert messages[0]["role"] == "user"
    # Native tool calls become the runtime's text-mode contract.
    assistant_tool_turn = messages[1]
    assert assistant_tool_turn["role"] == "assistant"
    assert "```tool_call" in assistant_tool_turn["content"]
    assert '"name": "terminal"' in assistant_tool_turn["content"]
    # Tool results become explicit tool-result user turns.
    assert "[tool result" in messages[2]["content"]
    assert messages[2]["role"] == "user"
    assert messages[-1] == {"role": "assistant", "content": "Verified: the test suite passed."}


def test_sensitive_example_is_refused():
    mod = _load_module()
    bad = _trajectory()
    bad["turns"][0]["content"] = "email taro@example.com about it"
    assert mod.trajectory_to_example(bad) is None


def test_dedup_by_digest():
    mod = _load_module()
    examples = mod.build_examples([_trajectory(), _trajectory()])
    assert len(examples) == 1


def test_cli_writes_jsonl(tmp_path):
    mod = _load_module()
    root = tmp_path / "trajectories" / "2026-07-07"
    root.mkdir(parents=True)
    (root / "a.json").write_text(json.dumps(_trajectory()), encoding="utf-8")
    out = tmp_path / "dataset.jsonl"
    count = mod.run(trajectories_dir=tmp_path / "trajectories", out_path=out, max_examples=10)
    assert count == 1
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["messages"][0]["role"] == "user"
