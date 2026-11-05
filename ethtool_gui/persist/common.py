"""Shared pieces of the persistence program: errors, tool calls and careful file writes.

Files are only touched through a directory descriptor opened without following
symlinks, so a symlink planted at the directory, the final name or the temporary
name is refused instead of followed.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.runner import EthtoolMissing, EthtoolTimeout, Result

# ``run(argv)``: argv[0] is the tool's short name ("nmcli", "udevadm"); the
# program maps it to a fixed absolute path.
Run = Callable[[list[str]], Result]
REASON_MAX = 200


class PersistError(Exception):
    """A refusal or failure, reported as ``{"ok": false, "error": code, "reason": ...}``."""

    def __init__(self, code: str, reason: str | None = None) -> None:
        super().__init__(code if reason is None else f"{code}: {reason}")
        self.code, self.reason = code, reason
        # Set by the NM backend: the tool failed for sure and the profile was re-read unchanged.
        self.unchanged = False


class PersistConflict(PersistError):
    """The ``.link`` file would not be the one applied, or would change naming."""

    def __init__(self, reason: str) -> None:
        super().__init__("conflict", reason)


@dataclass(frozen=True)
class Env:
    run: Run
    link_dir: Path
    sys_root: Path
    default_links: tuple[str, ...] = ()


@dataclass
class Outcome:
    entry: dict | None  # what the state file records for the interface; None: nothing managed
    written: list[str]
    unsupported: list[str]


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:REASON_MAX]
    return ""


def call(run: Run, argv: list[str]) -> Result:
    try:
        return run(argv)
    except EthtoolMissing:
        raise PersistError("tool_missing", argv[0]) from None
    except EthtoolTimeout:
        raise PersistError("timeout", argv[0]) from None


def check(result: Result, code: str) -> Result:
    if result.returncode != 0:
        raise PersistError(code, first_line(result.stderr) or first_line(result.stdout)
                           or f"exit code {result.returncode}")
    return result


def dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# -- files -----------------------------------------------------------------

def open_dir(path: Path) -> int:
    """Descriptor of a real directory owned by us and not writable by others."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise PersistError("unsafe_path", f"{path}: {exc.strerror}") from None
    st = os.fstat(fd)
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        os.close(fd)
        raise PersistError("unsafe_path", f"{path} has the wrong owner or mode")
    return fd


def read_file(dir_fd: int, name: str, limit: int, private: bool = False) -> bytes | None:
    """Contents of a regular file (never through a symlink); None if absent."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP for a symlink
        raise PersistError("unsafe_path", f"{name}: {exc.strerror}") from None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise PersistError("unsafe_path", f"{name} is not a small regular file")
        if private and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            raise PersistError("unsafe_path", f"{name} has the wrong owner or mode")
        return fh.read(limit + 1)


def write_file(dir_fd: int, name: str, data: bytes, mode: int) -> None:
    """Replace ``name`` atomically: O_EXCL temp file in the same directory, fsync, rename."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        st = None
    if st is not None and not stat.S_ISREG(st.st_mode):
        raise PersistError("unsafe_path", f"{name} is not a regular file")
    tmp = f".{name}.{secrets.token_hex(6)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise PersistError("unsafe_path", f"{tmp}: {exc.strerror}") from None
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise
    os.fsync(dir_fd)


def remove_file(dir_fd: int, name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=dir_fd)
    os.fsync(dir_fd)
