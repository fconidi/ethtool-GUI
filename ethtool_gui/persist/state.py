"""``managed.json``: what this program wrote for each interface, and what was there before.

``{"version": 1, "ifaces": {iface: entry}}`` where an entry is
``{"backend": "nm", "uuid", "values", "fields", "previous": {property: old value}}`` or
``{"backend": "networkd", "mac", "values", "fields"}``. The file is root's, 0600,
in a 0700 directory, replaced atomically; everything in it is validated again on
load, as if it came from the request.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
from typing import Iterator

from ..core.ifaces import valid_name
from ..helper import revert
from . import nm
from .common import PersistError, open_dir, read_file, write_file
from .detect import UUID
from .matrix import NETWORKD, NM, fields_of
from .request import validate_mac, validate_values

VERSION = 1
MAX_BYTES = 256 * 1024
MODE = 0o600


def _state_error(reason: str) -> PersistError:
    return PersistError("state_error", reason)


def _open_parent(path: Path) -> int:
    try:
        revert._ensure_dir(path.parent)
    except (revert.StateError, OSError) as exc:
        raise _state_error(str(exc)) from None
    try:
        return open_dir(path.parent)
    except PersistError as exc:
        raise _state_error(exc.reason or exc.code) from None


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock so two runs never interleave their read-modify-write."""
    fd = _open_parent(path)
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            lock = os.open(path.name + ".lock", flags, MODE, dir_fd=fd)
        except OSError as exc:
            raise _state_error(f"lock: {exc.strerror}") from None
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            os.close(lock)
    finally:
        os.close(fd)


def _clean(iface: object, entry: object) -> dict:
    if not isinstance(iface, str) or not valid_name(iface) or not isinstance(entry, dict):
        raise _state_error("malformed entry")
    try:
        values = validate_values(entry.get("values"))
    except PersistError:
        raise _state_error("malformed values") from None
    backend = entry.get("backend")
    clean = {"backend": backend, "values": values, "fields": fields_of(values)}
    if backend == NM:
        uuid, previous = entry.get("uuid"), entry.get("previous")
        if not isinstance(uuid, str) or not UUID.fullmatch(uuid) or not isinstance(previous, dict):
            raise _state_error("malformed profile entry")
        if not all(nm.valid_previous(p, v) for p, v in previous.items()):
            raise _state_error("malformed previous value")
        clean.update(uuid=uuid, previous=dict(previous))
    elif backend == NETWORKD:
        try:
            clean["mac"] = validate_mac(entry.get("mac"))
        except PersistError:
            clean["mac"] = ""
        if not clean["mac"]:
            raise _state_error("malformed mac")
    else:
        raise _state_error("unknown backend")
    return clean


def load(path: Path) -> dict[str, dict]:
    fd = _open_parent(path)
    try:
        try:
            raw = read_file(fd, path.name, MAX_BYTES, private=True)
        except PersistError as exc:
            raise _state_error(exc.reason or exc.code) from None
    finally:
        os.close(fd)
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _state_error("not JSON") from None
    if (not isinstance(data, dict) or data.get("version") != VERSION
            or not isinstance(data.get("ifaces"), dict)):
        raise _state_error("malformed")
    return {iface: _clean(iface, entry) for iface, entry in data["ifaces"].items()}


def save(path: Path, entries: dict[str, dict]) -> None:
    data = json.dumps({"version": VERSION, "ifaces": entries}, sort_keys=True).encode("utf-8")
    fd = _open_parent(path)
    try:
        write_file(fd, path.name, data, MODE)
    finally:
        os.close(fd)


def record(path: Path, entries: dict[str, dict], iface: str, entry: dict) -> None:
    entries[iface] = entry
    save(path, entries)


def forget(path: Path, entries: dict[str, dict], iface: str) -> None:
    entries.pop(iface, None)
    save(path, entries)
