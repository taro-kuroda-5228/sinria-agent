"""Private filesystem primitives for local self-repair artifacts.

On POSIX, managed path components are opened relative to directory descriptors so
concurrent pathname swaps cannot redirect writes outside the trusted root.  The
helpers fail closed on Windows: mode bits do not provide an owner-only ACL and
this dependency-free module cannot honestly make that privacy guarantee there.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
from typing import IO

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
_WINDOWS_UNSUPPORTED = (
    "private repair artifact storage requires POSIX owner permissions; "
    "Windows ACL support is not implemented"
)


class PrivateStorageUnsupportedError(NotImplementedError):
    """Raised when owner-only repair storage cannot be guaranteed."""


def _absolute(path: Path) -> Path:
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise ValueError("private path must not contain '..'")
    return Path(os.path.abspath(os.fspath(path)))


def _scoped(path: Path, root: Path | None) -> tuple[Path, Path | None]:
    absolute = _absolute(path)
    if root is None:
        return absolute, None
    absolute_root = _absolute(root)
    try:
        absolute.relative_to(absolute_root)
    except ValueError as exc:
        raise ValueError("private path must be within root") from exc
    return absolute, absolute_root


def _require_private_platform() -> None:
    if os.name == "nt":
        raise PrivateStorageUnsupportedError(_WINDOWS_UNSUPPORTED)

    # ``os.rename`` implementation but is not itself listed in supports_dir_fd.
    required = (os.open, os.mkdir, os.stat, os.unlink)
    if not all(function in os.supports_dir_fd for function in required):
        raise NotImplementedError("private repair storage requires descriptor-relative filesystem operations")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "fchmod"):
        raise NotImplementedError("private repair storage requires POSIX no-follow and descriptor chmod support")


def _dir_flags(*, nofollow: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    if nofollow:
        flags |= os.O_NOFOLLOW
    return flags


def _validate_dir_fd(fd: int, path: Path) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"private directory path is not a directory: {path}")


def _open_trusted_dir(path: Path) -> int:
    """Open a caller-selected anchor, allowing aliases above the managed scope."""
    fd = os.open(path, _dir_flags(nofollow=False))
    try:
        _validate_dir_fd(fd, path)
        return fd
    except Exception:
        os.close(fd)
        raise


def _walk_private_dirs(anchor_fd: int, anchor: Path, parts: tuple[str, ...]) -> int:
    """Create/open *parts* beneath an already-open anchor without following links."""
    current_fd = anchor_fd
    current_path = anchor
    try:
        for part in parts:
            current_path /= part
            try:
                os.mkdir(part, PRIVATE_DIR_MODE, dir_fd=current_fd)
            except FileExistsError:
                pass
            info = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise OSError(f"refusing symlink ancestor for private path: {current_path}")
            child_fd = os.open(part, _dir_flags(nofollow=True), dir_fd=current_fd)
            try:
                _validate_dir_fd(child_fd, current_path)
                os.fchmod(child_fd, PRIVATE_DIR_MODE)
            except Exception:
                os.close(child_fd)
                raise
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _ensure_explicit_root_fd(root: Path) -> int:
    """Open/create a root without following the root entry itself.

    Aliases in ancestors of the caller-designated root are trusted; every
    component from the nearest existing ancestor through the root is managed.
    """
    missing = [root.name]
    anchor = root.parent
    while True:
        try:
            anchor_fd = _open_trusted_dir(anchor)
            break
        except FileNotFoundError:
            if anchor.parent == anchor:
                raise
            missing.append(anchor.name)
            anchor = anchor.parent
    return _walk_private_dirs(anchor_fd, anchor, tuple(reversed(missing)))


def _ensure_private_dir_fd(path: Path, root: Path | None) -> int:
    """Return an fd for *path*, creating and hardening only the managed scope."""
    path, root = _scoped(path, root)
    _require_private_platform()

    if root is not None:
        root_fd = _ensure_explicit_root_fd(root)
        relative = path.relative_to(root)
        if not relative.parts:
            return root_fd
        return _walk_private_dirs(root_fd, root, relative.parts)

    # With no explicit root, the nearest existing parent is the trusted anchor.
    # The requested directory itself is always managed, even when it exists.
    missing = [path.name]
    anchor = path.parent
    while True:
        try:
            anchor_info = os.lstat(anchor)
        except FileNotFoundError:
            anchor_info = None
        if anchor_info is not None and stat.S_ISLNK(anchor_info.st_mode):
            # Without an explicit trusted root, an existing alias is part of
            # the managed path and must be reopened with O_NOFOLLOW below.
            missing.append(anchor.name)
            anchor = anchor.parent
            continue
        try:
            anchor_fd = _open_trusted_dir(anchor)
            break
        except FileNotFoundError:
            if anchor.parent == anchor:
                raise
            missing.append(anchor.name)
            anchor = anchor.parent
    return _walk_private_dirs(anchor_fd, anchor, tuple(reversed(missing)))


def ensure_private_dir(path: Path, *, root: Path | None = None) -> None:
    """Create *path* and enforce 0700 within the managed POSIX scope.

    Existing aliases above an explicit root (or above the nearest existing
    parent when no root is supplied) are trusted. Symlinks inside the managed
    scope are rejected by descriptor-relative ``O_NOFOLLOW`` opens.
    """
    fd = _ensure_private_dir_fd(Path(path), root)
    os.close(fd)


def _stat_target(parent_fd: int, name: str, path: Path) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        raise OSError(f"refusing symlink private file: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"private file is not regular: {path}")
    if info.st_nlink != 1:
        raise OSError(f"refusing hard-linked private file: {path}")
    return info


def _open_parent(path: Path, root: Path | None) -> tuple[Path, Path | None, int]:
    path, root = _scoped(path, root)
    parent_fd = _ensure_private_dir_fd(path.parent, root)
    return path, root, parent_fd


def _open_fd(path: Path, flags: int, *, root: Path | None = None) -> int:
    path, _root, parent_fd = _open_parent(Path(path), root)
    try:
        safe_flags = flags | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        while True:
            _stat_target(parent_fd, path.name, path)
            try:
                fd = os.open(path.name, safe_flags, dir_fd=parent_fd)
                break
            except FileNotFoundError:
                try:
                    fd = os.open(
                        path.name,
                        safe_flags | os.O_CREAT | os.O_EXCL,
                        PRIVATE_FILE_MODE,
                        dir_fd=parent_fd,
                    )
                    break
                except FileExistsError:
                    # Another process created the file between our open calls.
                    # Revalidate the new target before trying the existing-file path.
                    continue
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError(f"private file is not regular: {path}")
            # Reject aliases before chmod, truncation, append, or any other mutation.
            if info.st_nlink != 1:
                raise OSError(f"refusing hard-linked private file: {path}")
            os.fchmod(fd, PRIVATE_FILE_MODE)
            return fd
        except Exception:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def open_private(
    path: Path, mode: str, *, encoding: str | None = None, root: Path | None = None,
) -> IO:
    """Open a checked regular POSIX file privately, truncating after validation."""
    modes = {
        "a": (os.O_WRONLY | os.O_APPEND, False),
        "a+b": (os.O_RDWR | os.O_APPEND, False),
        "w": (os.O_WRONLY, True),
        "wb": (os.O_WRONLY, True),
        "w+": (os.O_RDWR, True),
    }
    try:
        flags, truncate = modes[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported private open mode: {mode}") from exc
    fd = _open_fd(Path(path), flags, root=root)
    try:
        if truncate:
            os.ftruncate(fd, 0)
        binary = "b" in mode
        return os.fdopen(fd, mode, encoding=None if binary else encoding)
    except Exception:
        os.close(fd)
        raise


def _lock(fd: int) -> None:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("private write made no progress")
        view = view[written:]


def append_private_text(path: Path, text: str, *, root: Path | None = None) -> None:
    """Serialize and append one complete UTF-8 payload across POSIX processes."""
    data = text.encode("utf-8")
    fd = _open_fd(Path(path), os.O_WRONLY | os.O_APPEND, root=root)
    try:
        _lock(fd)
        try:
            _write_all(fd, data)
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def write_private(path: Path, data: str | bytes, *, root: Path | None = None) -> None:
    """Atomically replace a private regular POSIX file with text or binary data."""
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    path, _root, parent_fd = _open_parent(Path(path), root)
    temp_name: str | None = None
    fd = -1
    try:
        _stat_target(parent_fd, path.name, path)
        for _ in range(100):
            candidate = f".{path.name}.{secrets.token_hex(8)}.tmp"
            try:
                flags = os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW
                flags |= getattr(os, "O_CLOEXEC", 0)
                fd = os.open(candidate, flags, PRIVATE_FILE_MODE, dir_fd=parent_fd)
                temp_name = candidate
                break
            except FileExistsError:
                continue
        if temp_name is None:
            raise FileExistsError("unable to allocate private temporary file")
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"private temporary file is unsafe: {path}")
        os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _stat_target(parent_fd, path.name, path)
        os.replace(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temp_name = None
        os.fsync(parent_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def write_private_text(path: Path, text: str, *, root: Path | None = None) -> None:
    """Atomically replace a private file with UTF-8 text."""
    write_private(path, text, root=root)
