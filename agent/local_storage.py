"""Lossless native filesystem compression for explicitly quiescent archives.

No transcript format change: existing readers still open the same pathname.
Callers MUST establish the archive is not being written; age is not a lock.
Never invoke this automatically against active session storage.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def _digest(path: Path) -> bytes:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def compress_inactive(path: Path, *, min_age_days: int = 30) -> dict:
    """Compress one caller-quiesced archive, verify, then atomically replace."""
    path = Path(path)
    if min_age_days < 1:
        raise ValueError('minimum archive age must be positive')
    initial = path.lstat()
    skipped = {'status': 'skipped', 'saved_bytes': 0}
    if (path.is_symlink() or not path.is_file() or initial.st_nlink != 1
            or initial.st_size < 65536
            or time.time() - initial.st_mtime < min_age_days * 86400):
        return skipped
    if sys.platform != 'darwin':
        return {'status': 'unsupported', 'saved_bytes': 0}
    before = initial.st_blocks * 512
    expected = _digest(path)
    with tempfile.TemporaryDirectory(prefix='.sinria-compress-', dir=path.parent) as root:
        target = Path(root) / 'archive'
        subprocess.run(['/usr/bin/ditto', '--hfsCompression', '--noclone', str(path), str(target)],
                       check=True, capture_output=True)
        after = target.stat().st_blocks * 512
        if _digest(target) != expected:
            raise RuntimeError('compressed archive failed integrity check')
        current = path.lstat()
        if ((current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                != (initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns)):
            return {'status': 'changed', 'saved_bytes': 0}
        if after >= before:
            return skipped
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(target, path)
        if _digest(path) != expected:
            raise RuntimeError('archive readback failed integrity check')
        return {'status': 'compressed', 'saved_bytes': before - after,
                'before_bytes': before, 'after_bytes': after}
