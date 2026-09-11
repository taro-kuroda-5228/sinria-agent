from pathlib import Path

import hermes_cli.browser_connect as browser_connect


def _write_leveldb(profile: Path, value: str) -> Path:
    leveldb = profile / "Local Storage" / "leveldb"
    leveldb.mkdir(parents=True)
    (leveldb / "CURRENT").write_text("MANIFEST-000001\n")
    (leveldb / "MANIFEST-000001").write_text(value)
    (leveldb / "000003.log").write_text(value)
    (leveldb / "LOCK").write_text("live lock")
    return leveldb


def test_mirror_profile_auth_refreshes_local_storage_without_lock(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "copy"
    _write_leveldb(src / "Profile 1", "fresh session")
    stale = _write_leveldb(dst / "Default", "stale session")
    (stale / "stale-only.ldb").write_text("obsolete")

    failed = browser_connect._mirror_profile_auth(str(src), str(dst), "Profile 1")

    copied = dst / "Default" / "Local Storage" / "leveldb"
    assert failed == 0
    assert (copied / "MANIFEST-000001").read_text() == "fresh session"
    assert (copied / "000003.log").read_text() == "fresh session"
    assert not (copied / "LOCK").exists()
    assert not (copied / "stale-only.ldb").exists()


def test_mirror_profile_auth_preserves_previous_local_storage_on_copy_failure(
    tmp_path, monkeypatch
):
    src = tmp_path / "source"
    dst = tmp_path / "copy"
    _write_leveldb(src / "Profile 1", "fresh session")
    old = _write_leveldb(dst / "Default", "known-good session")

    real_copytree = browser_connect.shutil.copytree

    def fail_local_storage_copy(source, target, *args, **kwargs):
        if Path(source).name == "Local Storage":
            raise OSError("simulated copy failure")
        return real_copytree(source, target, *args, **kwargs)

    monkeypatch.setattr(browser_connect.shutil, "copytree", fail_local_storage_copy)

    failed = browser_connect._mirror_profile_auth(str(src), str(dst), "Profile 1")

    assert failed == 1
    assert (old / "MANIFEST-000001").read_text() == "known-good session"


def test_mirror_profile_auth_restores_previous_local_storage_on_swap_failure(
    tmp_path, monkeypatch
):
    src = tmp_path / "source"
    dst = tmp_path / "copy"
    _write_leveldb(src / "Profile 1", "fresh session")
    old = _write_leveldb(dst / "Default", "known-good session")
    destination = dst / "Default" / "Local Storage"
    real_replace = browser_connect.os.replace

    def fail_new_directory_swap(source, target):
        if Path(target) == destination and ".refresh-" in Path(source).name:
            raise OSError("simulated atomic swap failure")
        return real_replace(source, target)

    monkeypatch.setattr(browser_connect.os, "replace", fail_new_directory_swap)

    failed = browser_connect._mirror_profile_auth(str(src), str(dst), "Profile 1")

    assert failed == 1
    assert (old / "MANIFEST-000001").read_text() == "known-good session"
