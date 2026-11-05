"""State files and timers that undo an unconfirmed link change.

The state file ``<state_dir>/<token>.json`` records what to put back. Removing
it is the lock: whoever unlinks it (the helper's own countdown, ``confirm``,
``cancel`` or the systemd timer running ``ethtool-gui-restore``) owns the
outcome, and everybody else does nothing.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from ..core.ifaces import valid_name

RESTORE_BIN = "/usr/lib/ethtool-gui/ethtool-gui-restore"
UNIT_PREFIX = "ethtool-gui-revert-"
MAX_STATE_BYTES = 64 * 1024
_TOKEN = re.compile(r"[0-9a-f]{16}")


class StateError(Exception):
    """The state directory or a state file is not trustworthy."""


def new_token() -> str:
    return secrets.token_hex(8)


def valid_token(token: object) -> bool:
    return isinstance(token, str) and _TOKEN.fullmatch(token) is not None


def _path(state_dir: Path, token: str) -> Path:
    if not valid_token(token):
        raise StateError("bad token")
    return Path(state_dir) / f"{token}.json"


def unit_name(token: str) -> str:
    if not valid_token(token):
        raise StateError("bad token")
    return UNIT_PREFIX + token


def _ensure_dir(state_dir: Path) -> None:
    try:
        os.mkdir(state_dir, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(state_dir)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise StateError(f"{state_dir} is not a directory owned by this user")
    os.chmod(state_dir, 0o700)


def write_state(state_dir: Path, token: str, iface: str, op: str, saved: dict) -> Path:
    path = _path(state_dir, token)
    _ensure_dir(Path(state_dir))
    data = json.dumps({"iface": iface, "op": op, "saved": saved, "created_at": time.time()})
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(data)
    return path


def read_state(state_dir: Path, token: str) -> dict | None:
    """Return ``{"iface", "op", "saved", ...}``, ``None`` if absent; raise if not trustworthy."""
    path = _path(state_dir, token)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP for a symlink
        raise StateError(str(exc)) from exc
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077 or st.st_size > MAX_STATE_BYTES):
            raise StateError("state file has the wrong owner, mode or size")
        raw = fh.read(MAX_STATE_BYTES + 1)
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StateError("state file is not JSON") from exc
    if (not isinstance(state, dict) or not isinstance(state.get("iface"), str)
            or not valid_name(state["iface"]) or not isinstance(state.get("op"), str)
            or not isinstance(state.get("saved"), dict)):
        raise StateError("state file is malformed")
    return state


def pending_ifaces(state_dir: Path, max_age: float) -> set[str]:
    """Interfaces with a state file younger than ``max_age`` seconds (any helper's)."""
    try:
        names = os.listdir(state_dir)
    except OSError:
        return set()
    found = set()
    now = time.time()
    for name in names:
        token = name[:-5] if name.endswith(".json") else ""
        if not valid_token(token):
            continue
        try:
            state = read_state(state_dir, token)
        except StateError:
            continue
        created = state.get("created_at") if state else None
        if type(created) in (int, float) and now - created < max_age:
            found.add(state["iface"])
    return found


def claim(state_dir: Path, token: str) -> bool:
    """Remove the state file; True only for the caller that removed it."""
    try:
        os.unlink(_path(state_dir, token))
    except FileNotFoundError:
        return False
    return True


def _call(argv: list[str]) -> bool:
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def arm(systemd_run: str, token: str, seconds: float) -> bool:
    """Start the system timer that restores even if the helper dies."""
    return _call([systemd_run, "--on-active=%ds" % seconds, f"--unit={unit_name(token)}",
                  RESTORE_BIN, token])


def disarm(systemctl: str, token: str) -> None:
    _call([systemctl, "stop", f"{unit_name(token)}.timer"])


class Countdown(threading.Thread):
    """Calls ``on_expire`` at the monotonic ``deadline`` unless cancelled; ``on_gone`` if the NIC vanishes.

    Only the disappearance of the interface stops it early: carrier or operstate
    changes are not looked at.
    """

    def __init__(self, token: str, deadline: float, poll: float,
                 present: Callable[[], bool], on_expire: Callable[[], None],
                 on_gone: Callable[[], None]) -> None:
        super().__init__(name=f"ethtool-gui-revert-{token}", daemon=True)
        self._deadline, self._poll = deadline, poll
        self._present, self._on_expire, self._on_gone = present, on_expire, on_gone
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:
        deadline = self._deadline
        while not self._stop.is_set():
            if not self._present():
                self._on_gone()
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._on_expire()
                return
            self._stop.wait(min(self._poll, remaining))
