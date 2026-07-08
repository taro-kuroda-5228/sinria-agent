"""Durable TodoStore persistence (architecture-centric P1, Task B).

In-flight task state must survive process death — durable task state can
no longer depend on transcript survival (compression/truncation) alone.
"""

import json

from tools.todo_tool import TodoStore


def _items():
    return [
        {"id": "step-1", "content": "back up the config", "status": "completed"},
        {"id": "step-2", "content": "edit the target line", "status": "in_progress"},
        {"id": "step-3", "content": "verify service starts", "status": "pending"},
    ]


def test_write_persists_to_disk(tmp_path):
    path = tmp_path / "todos" / "session-1.json"
    store = TodoStore(storage_path=path)
    store.write(_items())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [t["id"] for t in data["todos"]] == ["step-1", "step-2", "step-3"]
    assert data["updated_at"]


def test_load_from_disk_restores_items(tmp_path):
    path = tmp_path / "session-1.json"
    TodoStore(storage_path=path).write(_items())

    fresh = TodoStore(storage_path=path)
    assert not fresh.has_items()
    assert fresh.load_from_disk() is True
    assert [t["id"] for t in fresh.read()] == ["step-1", "step-2", "step-3"]
    assert fresh.read()[1]["status"] == "in_progress"


def test_load_missing_or_corrupt_file_is_safe(tmp_path):
    missing = TodoStore(storage_path=tmp_path / "nope.json")
    assert missing.load_from_disk() is False
    assert missing.read() == []

    corrupt_path = tmp_path / "bad.json"
    corrupt_path.write_text("{not json", encoding="utf-8")
    corrupt = TodoStore(storage_path=corrupt_path)
    assert corrupt.load_from_disk() is False
    assert corrupt.read() == []


def test_no_storage_path_never_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # tmp_path is shared with the hermetic-environment fixture, so compare
    # against a snapshot instead of asserting emptiness.
    before = {str(p) for p in tmp_path.rglob("*")}
    store = TodoStore()
    store.write(_items())
    after = {str(p) for p in tmp_path.rglob("*")}
    assert after == before  # no-path store writes nothing anywhere
    assert len(store.read()) == 3


def test_persist_failure_does_not_break_write(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file, not a directory", encoding="utf-8")
    # storage under a path whose parent is a file → persist must fail silently
    store = TodoStore(storage_path=blocker / "todos.json")
    result = store.write(_items())
    assert len(result) == 3  # in-memory behavior unaffected


def test_agent_init_binds_session_storage():
    from pathlib import Path

    source = Path("agent/agent_init.py").read_text(encoding="utf-8")
    assert "TodoStore(storage_path=" in source
    assert '"todos"' in source


def test_hydrate_prefers_disk_over_history():
    from pathlib import Path

    source = Path("run_agent.py").read_text(encoding="utf-8")
    hydrate = source.index("_hydrate_todo_store")
    assert "load_from_disk" in source[hydrate : hydrate + 2000]
