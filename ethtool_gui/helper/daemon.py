"""Privileged helper: applies settings for the window and undoes unconfirmed ones.

It talks JSON lines on stdin/stdout (see ``core.protocol``). It takes no
command-line arguments: the paths below are fixed. A link change is undone
after ``REVERT_SECONDS`` unless confirmed, on ``cancel``, and when stdin closes
because the window exited or died. A systemd timer started before the change
undoes it even if this process is killed.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ..core.ifaces import valid_name
from ..core.protocol import encode_message, iter_messages
from ..core.runner import (EthtoolError, EthtoolMissing, EthtoolTimeout, InvalidInterfaceName,
                           NoSuchInterface, Result, run_ethtool)
from . import commands, revert
from .commands import DETAIL_MAX, TIMEOUT, CommandError, check
from .operations import OPS, Operation, RestoreError, ValidationError

ETHTOOL_BIN = "/usr/sbin/ethtool"
SYS_ROOT = Path("/sys/class/net")
REVERT_SECONDS = 15
STATE_DIR = Path("/run/ethtool-gui")
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
POLL_SECONDS = 0.5
STALE_SECONDS = 300  # older state files (a helper and its timer both died) no longer block
MAX_LINE = 64 * 1024
MAX_IN_FLIGHT = 16

# Fields each command accepts besides "id" and "cmd".
FIELDS = {
    "ping": (), "read": ("iface", "what"), "apply": ("iface", "op", "values"),
    "confirm": ("token",), "cancel": ("token",), "blink": ("iface", "seconds"),
    "cable_test": ("iface", "confirmed"), "selftest": ("iface", "mode", "confirmed"),
}


@dataclass
class Pending:
    iface: str
    op: str
    saved: dict
    countdown: revert.Countdown


def _run(args: list[str], timeout: float = TIMEOUT) -> Result:
    return run_ethtool(args, binary=ETHTOOL_BIN, timeout=timeout)


def _runner(timeout: float = TIMEOUT):
    return lambda args: _run(args, timeout)


class Daemon:
    def __init__(self, stdout: IO[bytes]) -> None:
        self._out = stdout
        self._write_lock = threading.Lock()
        self._lock = threading.Lock()
        self._pending: dict[str, Pending] = {}
        self._busy: set[str] = set()
        self._workers: list[threading.Thread] = []
        self._countdowns: list[revert.Countdown] = []
        self._serial = 0

    def _write(self, obj: dict) -> None:
        data = encode_message(obj)
        with self._write_lock:
            try:
                self._out.write(data)
                self._out.flush()
            except (OSError, ValueError):
                pass

    # -- lifecycle -------------------------------------------------------

    def run(self, stdin: IO[bytes]) -> int:
        try:
            for request in iter_messages(stdin, MAX_LINE):
                self._dispatch(request)
        finally:
            for worker in list(self._workers):
                worker.join()
            for token in list(self._pending):
                self._finish(token, "closed")
            for countdown in list(self._countdowns):
                countdown.join()
        return 0

    def _dispatch(self, request: dict) -> None:
        if set(request) == {"error"} and request["error"] in ("bad_json", "line_too_long"):
            self._write({"id": None, "ok": False, "error": request["error"]})
            return
        ident = request.get("id")
        if type(ident) is not int or not 0 <= ident < 2 ** 53:
            self._write({"id": None, "ok": False, "error": "bad_request"})
            return
        self._workers = [w for w in self._workers if w.is_alive()]
        if len(self._workers) >= MAX_IN_FLIGHT:
            self._write({"id": ident, "ok": False, "error": "too_many_requests"})
            return
        self._serial += 1
        worker = threading.Thread(target=lambda: self._write(self._answer(ident, request)),
                                  name=f"ethtool-gui-request-{self._serial}", daemon=True)
        self._workers.append(worker)
        worker.start()

    def _answer(self, ident: int, request: dict) -> dict:
        cmd = request.get("cmd")
        if not isinstance(cmd, str) or cmd not in FIELDS:
            return {"id": ident, "ok": False, "error": "unknown_command"}
        if not set(request) - {"id", "cmd"} <= set(FIELDS[cmd]):
            return {"id": ident, "ok": False, "error": "bad_request"}
        error, detail = None, None
        try:
            return {"id": ident, "ok": True, **getattr(self, f"_cmd_{cmd}")(request)}
        except CommandError as exc:
            error, detail = exc.code, exc.detail
        except ValidationError as exc:
            error, detail = "bad_values", str(exc)
        except InvalidInterfaceName:
            error = "bad_iface"
        except NoSuchInterface:
            error = "no_device"
        except EthtoolMissing:
            error = "ethtool_missing"
        except EthtoolTimeout:
            error = "timeout"
        except (revert.StateError, OSError) as exc:
            error, detail = "state_error", str(exc)[:DETAIL_MAX]
        except Exception as exc:  # keep serving whatever a handler does
            error, detail = "internal_error", type(exc).__name__
        reply = {"id": ident, "ok": False, "error": error}
        if detail:
            reply["detail"] = detail
        return reply

    # -- argument checks ---------------------------------------------------

    @staticmethod
    def _iface(request: dict) -> str:
        iface = request.get("iface")
        if not isinstance(iface, str) or not valid_name(iface):
            raise CommandError("bad_iface")
        if not (SYS_ROOT / iface).exists():
            raise CommandError("no_device")
        return iface

    @staticmethod
    def _token(request: dict) -> str:
        token = request.get("token")
        if not revert.valid_token(token):
            raise CommandError("bad_token")
        return token

    # -- commands --------------------------------------------------------

    def _cmd_ping(self, request: dict) -> dict:
        return {}

    def _cmd_read(self, request: dict) -> dict:
        return commands.read(self._iface(request), request, _run)

    def _cmd_blink(self, request: dict) -> dict:
        return commands.blink(self._iface(request), request, _run)

    def _cmd_cable_test(self, request: dict) -> dict:
        return commands.cable_test(self._iface(request), request, _run)

    def _cmd_selftest(self, request: dict) -> dict:
        return commands.selftest(self._iface(request), request, _run)

    def _cmd_apply(self, request: dict) -> dict:
        iface = self._iface(request)
        name = request.get("op")
        op = OPS.get(name) if isinstance(name, str) else None
        if op is None:
            raise CommandError("unknown_op")
        values = request.get("values")
        argv = op.argv(iface, values)
        with self._lock:
            if iface in self._busy or iface in revert.pending_ifaces(STATE_DIR, STALE_SECONDS):
                raise CommandError("busy")
            self._busy.add(iface)
        registered = False
        try:
            if not op.is_link_affecting:
                check(_run(argv))
                return {}
            reply = self._apply_guarded(iface, op, values, argv)
            registered = True
            return reply
        finally:
            if not registered:
                with self._lock:
                    self._busy.discard(iface)

    def _apply_guarded(self, iface: str, op: Operation, values: dict, argv: list[str]) -> dict:
        saved = op.snapshot(iface, _runner(), values)
        if not saved:
            raise CommandError("cannot_snapshot")
        token = revert.new_token()
        revert.write_state(STATE_DIR, token, iface, op.name, saved)
        armed = revert.arm(SYSTEMD_RUN, token, REVERT_SECONDS)
        try:
            check(_run(argv))
        except BaseException:
            # Part of the change may have taken effect: put everything back now.
            if revert.claim(STATE_DIR, token):
                try:
                    op.restore(iface, saved, _runner())
                except (RestoreError, EthtoolError):
                    pass
            revert.disarm(SYSTEMCTL, token)
            raise
        countdown = revert.Countdown(
            token, REVERT_SECONDS, POLL_SECONDS,
            present=lambda: (SYS_ROOT / iface).exists(),
            on_expire=lambda: self._finish(token, "timeout"),
            on_gone=lambda: self._finish(token, "gone", restore=False))
        with self._lock:
            self._pending[token] = Pending(iface, op.name, saved, countdown)
            self._countdowns.append(countdown)
        countdown.start()
        return {"token": token, "seconds": REVERT_SECONDS,
                "watchdog": "systemd" if armed else "in-process only"}

    def _take(self, token: str) -> Pending | None:
        with self._lock:
            pending = self._pending.pop(token, None)
        if pending is not None:
            pending.countdown.cancel()
        return pending

    def _release(self, pending: Pending) -> None:
        with self._lock:
            self._busy.discard(pending.iface)

    def _finish(self, token: str, reason: str, restore: bool = True) -> bool:
        """End a pending change, restoring it unless ``restore`` is False."""
        pending = self._take(token)
        if pending is None:
            return False
        claimed = revert.claim(STATE_DIR, token)
        revert.disarm(SYSTEMCTL, token)
        event = {"event": "reverted", "token": token, "reason": reason, "restored": False}
        if restore and claimed:
            try:
                OPS[pending.op].restore(pending.iface, pending.saved, _runner())
                event["restored"] = True
            except (RestoreError, EthtoolError) as exc:
                event["error"] = str(exc)[:DETAIL_MAX] or type(exc).__name__
        self._release(pending)
        self._write(event)
        return True

    def _cmd_confirm(self, request: dict) -> dict:
        token = self._token(request)
        pending = self._take(token)
        if pending is None:
            raise CommandError("unknown_token")
        claimed = revert.claim(STATE_DIR, token)
        revert.disarm(SYSTEMCTL, token)
        self._release(pending)
        if not claimed:
            raise CommandError("expired")  # the system timer restored it already
        return {}

    def _cmd_cancel(self, request: dict) -> dict:
        if not self._finish(self._token(request), "cancel"):
            raise CommandError("unknown_token")
        return {}


def main(stdin: IO[bytes], stdout: IO[bytes], argv: list[str] | None = None) -> int:
    if argv:
        print("ethtool-gui-helper: no arguments are accepted", file=sys.stderr)
        return 2
    return Daemon(stdout).run(stdin)
