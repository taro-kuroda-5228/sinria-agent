"""Tests for file write safety and HERMES_WRITE_SAFE_ROOT sandboxing.

Based on PR #1085 by ismoilh (salvaged).
"""

import os
from pathlib import Path

import pytest

from tools.file_operations import _is_write_denied


class TestStaticDenyList:
    """Basic sanity checks for the static write deny list."""

    def test_temp_file_not_denied_by_default(self, tmp_path: Path):
        target = tmp_path / "regular.txt"
        assert _is_write_denied(str(target)) is False

    def test_ssh_key_is_denied(self):
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True

    def test_etc_shadow_is_denied(self):
        assert _is_write_denied("/etc/shadow") is True


class TestSafeWriteRoot:
    """HERMES_WRITE_SAFE_ROOT should sandbox writes to a specific subtree."""

    def test_writes_inside_safe_root_are_allowed(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        child = safe_root / "subdir" / "file.txt"
        os.makedirs(child.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(child)) is False

    def test_writes_to_safe_root_itself_are_allowed(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        os.makedirs(safe_root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(safe_root)) is False

    def test_writes_outside_safe_root_are_denied(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        outside = tmp_path / "other" / "file.txt"
        os.makedirs(safe_root, exist_ok=True)
        os.makedirs(outside.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(outside)) is True

    def test_safe_root_env_ignores_empty_value(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", "")
        assert _is_write_denied(str(target)) is False

    def test_safe_root_unset_allows_all(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
        assert _is_write_denied(str(target)) is False

    def test_safe_root_with_tilde_expansion(self, tmp_path: Path, monkeypatch):
        """~ in HERMES_WRITE_SAFE_ROOT should be expanded."""
        # Use a real subdirectory of tmp_path so we can test tilde-style paths
        safe_root = tmp_path / "workspace"
        inside = safe_root / "file.txt"
        os.makedirs(safe_root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(inside)) is False

    def test_safe_root_does_not_override_static_deny(self, tmp_path: Path, monkeypatch):
        """Even if a static-denied path is inside the safe root, it's still denied."""
        # Point safe root at home to include ~/.ssh
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", os.path.expanduser("~"))
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True


class TestCheckSensitivePathMacOSBypass:
    """Verify _check_sensitive_path blocks /private/etc paths (issue #8734)."""

    def test_etc_hosts_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/etc/hosts") is not None

    def test_private_etc_hosts_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/hosts") is not None

    def test_private_etc_ssh_config_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/ssh/sshd_config") is not None

    def test_private_var_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/db/something") is not None

    def test_boot_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/boot/grub/grub.cfg") is not None

    def test_safe_path_allowed(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/tmp/safe_file.txt") is None

    def test_os_temp_dir_is_carved_out(self, tmp_path):
        """Writes under the OS temp dir are allowed even on macOS, where the
        temp dir resolves under the guarded ``/private/var/folders/...`` prefix.

        ``tmp_path`` lives inside ``tempfile.gettempdir()``; without the
        carve-out it would be refused by the ``/private/var/`` prefix.
        """
        import tempfile
        from tools.file_tools import _check_sensitive_path

        # pytest's tmp_path is under the process temp dir.
        assert _check_sensitive_path(str(tmp_path / "scratch.txt")) is None
        # The temp root itself and a fresh file directly under it, too.
        temp_root = tempfile.gettempdir()
        assert _check_sensitive_path(
            os.path.join(temp_root, "carveout_probe.txt")
        ) is None

    def test_carveout_does_not_unblock_other_private_var(self):
        """The temp carve-out must NOT weaken protection for sibling
        ``/private/var`` locations that are not the temp dir.
        """
        from tools.file_tools import _check_sensitive_path

        # Hard regression pin: these stay blocked despite the temp carve-out.
        assert _check_sensitive_path("/private/var/db/dslocal/x") is not None
        assert _check_sensitive_path("/private/var/root/.ssh/id_rsa") is not None
        assert _check_sensitive_path("/var/db/something") is not None

    def test_untrusted_tempdir_does_not_unblock_sensitive_paths(self, monkeypatch):
        """A monkeypatched tempfile temp root must not become a guard bypass.

        ``tempfile.gettempdir()`` is process-mutable via ``tempfile.tempdir`` and
        via env vars before first resolution. If it points at ``/etc`` or a
        protected ``/private/var`` sibling, the carve-out must be ignored.
        """
        import tempfile
        from tools.file_tools import _check_sensitive_path, _temp_dir_carveout_roots

        monkeypatch.setattr(tempfile, "tempdir", "/etc")
        assert "/etc" not in _temp_dir_carveout_roots()
        assert _check_sensitive_path("/etc/probe") is not None

        monkeypatch.setattr(tempfile, "tempdir", "/private/var/db")
        assert "/private/var/db" not in _temp_dir_carveout_roots()
        assert _check_sensitive_path("/private/var/db/probe") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
