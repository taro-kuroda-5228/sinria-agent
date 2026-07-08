"""Tests for the architecture-centric eval ladder scorers (P0, Task 6)."""

import json
from pathlib import Path

import yaml

from evals.architecture_centric.run_eval import run_tasks
from evals.architecture_centric.scoring import score_task

TASKS_PATH = Path("evals/architecture_centric/tasks.yaml")
MOCKS_PATH = Path("evals/architecture_centric/mock_responses.example.json")


def _task(check, task_id="t", level="L0"):
    return {"id": task_id, "level": level, "prompt": "p", "check": check}


# ── tool_call_block ─────────────────────────────────────────────


def test_tool_call_block_pass():
    output = (
        "```tool_call\n"
        '{"name": "read_file", "arguments": {"path": "README.md"}}\n'
        "```"
    )
    result = score_task(
        _task({"type": "tool_call_block", "expected_tool": "read_file", "required_args": ["path"]}),
        output,
    )
    assert result["passed"] is True


def test_tool_call_block_wrong_tool_fails():
    output = '```tool_call\n{"name": "write_file", "arguments": {"path": "x"}}\n```'
    result = score_task(
        _task({"type": "tool_call_block", "expected_tool": "read_file"}), output
    )
    assert result["passed"] is False


def test_tool_call_block_missing_required_arg_fails():
    output = '```tool_call\n{"name": "read_file", "arguments": {}}\n```'
    result = score_task(
        _task({"type": "tool_call_block", "expected_tool": "read_file", "required_args": ["path"]}),
        output,
    )
    assert result["passed"] is False


def test_tool_call_block_no_block_fails():
    result = score_task(
        _task({"type": "tool_call_block", "expected_tool": "read_file"}),
        "I would read the file now.",
    )
    assert result["passed"] is False


def test_tool_call_block_forbidden_tool():
    output = '```tool_call\n{"name": "delete_file", "arguments": {"path": "x"}}\n```'
    result = score_task(
        _task({"type": "tool_call_block", "forbidden_tool": "delete_file"}), output
    )
    assert result["passed"] is False
    # No block at all is fine when only a forbidden tool is specified.
    ok = score_task(
        _task({"type": "tool_call_block", "forbidden_tool": "delete_file"}),
        "I cannot delete files with the available tools.",
    )
    assert ok["passed"] is True


# ── contains_all ────────────────────────────────────────────────


def test_contains_all_required_and_forbidden():
    check = {"type": "contains_all", "required": ["sinria"], "forbidden": ["hermes"]}
    assert score_task(_task(check), "The product is Sinria.")["passed"] is True
    assert score_task(_task(check), "The product is Hermes Sinria.")["passed"] is False
    assert score_task(_task(check), "No product name here.")["passed"] is False


# ── json_keys ───────────────────────────────────────────────────


def test_json_keys_pass_with_fenced_json():
    check = {"type": "json_keys", "required_keys": ["status", "summary"]}
    output = '```json\n{"status": "ok", "summary": "done"}\n```'
    assert score_task(_task(check), output)["passed"] is True


def test_json_keys_allowed_values():
    check = {
        "type": "json_keys",
        "required_keys": ["classification"],
        "allowed_values": {"classification": ["emergency", "routine"]},
    }
    assert score_task(_task(check), '{"classification": "routine"}')["passed"] is True
    assert score_task(_task(check), '{"classification": "banana"}')["passed"] is False
    assert score_task(_task(check), "not json")["passed"] is False


# ── regex ───────────────────────────────────────────────────────


def test_regex_ordered_steps():
    check = {"type": "regex", "pattern": r"1\..*2\..*3\.", "flags": "is"}
    assert score_task(_task(check), "1. backup\n2. edit\n3. verify")["passed"] is True
    assert score_task(_task(check), "just do it")["passed"] is False


# ── run_tasks + shipped assets ──────────────────────────────────


def test_run_tasks_aggregates_levels():
    tasks = [
        _task({"type": "contains_all", "required": ["a"]}, task_id="t1", level="L0"),
        _task({"type": "contains_all", "required": ["zz"]}, task_id="t2", level="L1"),
    ]
    report = run_tasks(tasks, lambda task: "a")
    assert report["levels"]["L0"]["passed"] == 1
    assert report["levels"]["L1"]["passed"] == 0
    assert report["pass_rate"] == 0.5
    assert {r["id"] for r in report["results"]} == {"t1", "t2"}


def test_shipped_tasks_and_mocks_are_consistent():
    tasks = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))["tasks"]
    mocks = json.loads(MOCKS_PATH.read_text(encoding="utf-8"))
    assert len(tasks) >= 8
    levels = {t["level"] for t in tasks}
    assert {"L0", "L1", "L2", "L3"} <= levels
    # Every task has a mock response, and the mock suite passes 100% —
    # the example file documents what a passing model looks like.
    for task in tasks:
        assert task["id"] in mocks, f"missing mock for {task['id']}"
    report = run_tasks(tasks, lambda task: mocks[task["id"]])
    failures = [r for r in report["results"] if not r["passed"]]
    assert not failures, failures
