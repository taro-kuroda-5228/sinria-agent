from pathlib import Path

from agent.correction_loop.migrate_legacy import migrate


def test_migration_is_non_destructive_and_idempotent(tmp_path: Path):
    legacy = tmp_path / "context_share"
    legacy.mkdir()
    source = legacy / "evidence.jsonl"
    source.write_text('{"evidence_id":"ev-1"}\n', encoding="utf-8")

    dry_run = migrate(tmp_path, apply=False)
    target = tmp_path / "corrections" / "evidence.jsonl"
    assert dry_run == [(source, target, "copy")]
    assert not target.exists()

    applied = migrate(tmp_path, apply=True)
    assert applied == [(source, target, "copy")]
    assert source.exists()
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    again = migrate(tmp_path, apply=True)
    assert again == [(source, target, "exists")]
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_migration_routes_correction_and_repair_metrics_to_independent_stores(tmp_path: Path):
    legacy = tmp_path / "context_share"
    legacy.mkdir()
    (legacy / "verify_nudges.jsonl").write_text("{}\n", encoding="utf-8")
    (legacy / "routing_signals.jsonl").write_text("{}\n", encoding="utf-8")
    (legacy / "code_defects.jsonl").write_text("{}\n", encoding="utf-8")

    migrate(tmp_path, apply=True)

    assert (tmp_path / "corrections" / "verify_nudges.jsonl").exists()
    assert (tmp_path / "corrections" / "routing_signals.jsonl").exists()
    assert (tmp_path / "repair" / "code_defects.jsonl").exists()
    assert not (tmp_path / "corrections" / "code_defects.jsonl").exists()
