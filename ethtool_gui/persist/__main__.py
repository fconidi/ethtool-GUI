"""Entry point of ``ethtool-gui-persist``, started as root through pkexec.

It takes no command-line arguments: the paths below are fixed. It reads one
JSON request line (at most ``MAX_LINE`` bytes) from stdin, answers one JSON
line on stdout and exits 0 on success, 1 on a refusal or failure, 2 if any
argument was given.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from ..core.protocol import encode_message, iter_messages
from ..core.runner import Result, run_ethtool
from . import detect, networkd, nm, state
from .common import Env, PersistError, first_line
from .detect import Backend
from .request import Request, parse

NMCLI = "/usr/bin/nmcli"
UDEVADM = "/usr/bin/udevadm"
LINK_DIR = Path("/etc/systemd/network")
STATE_FILE = Path("/var/lib/ethtool-gui/managed.json")
SYS_ROOT = Path("/sys/class/net")
DEFAULT_LINKS = ("/usr/lib/systemd/network/99-default.link",
                 "/lib/systemd/network/99-default.link")
TIMEOUT = 15.0
MAX_LINE = 64 * 1024


def run_tool(argv: list[str]) -> Result:
    binary = {"nmcli": NMCLI, "udevadm": UDEVADM}[argv[0]]
    return run_ethtool(argv[1:], binary=binary, timeout=TIMEOUT)


def _env() -> Env:
    return Env(run=run_tool, link_dir=LINK_DIR, sys_root=SYS_ROOT, default_links=DEFAULT_LINKS)


def _status(req: Request, entry: dict | None, env: Env) -> dict:
    detected = detect.backend_for(req.iface, env.run, env.link_dir)
    reply = {"ok": True, "backend": entry["backend"] if entry else detected.value,
             "detected": detected.value, "managed": entry is not None,
             "fields": entry["fields"] if entry else [], "values": entry["values"] if entry else {}}
    if entry and entry["backend"] == "nm":
        reply["persisted"] = nm.current(entry, env)
    elif entry:
        reply["file"] = networkd.present(req.iface, env)
    return reply


def _persist(req: Request, entries: dict, env: Env) -> dict:
    entry = entries.get(req.iface)
    detected = detect.backend_for(req.iface, env.run, env.link_dir)
    if entry is not None and entry["backend"] != detected.value:
        raise PersistError("backend_changed", entry["backend"])
    if detected is Backend.NM:
        committed = []

        def commit(new_entry: dict) -> None:  # old values on disk before the profile changes
            state.record(STATE_FILE, dict(entries), req.iface, new_entry)
            committed.append(True)
        try:
            outcome = nm.persist(req.iface, req.values, env, entry, commit=commit)
        except BaseException:
            if committed:
                state.save(STATE_FILE, entries)  # the change did not happen: back to before
            raise
    elif detected is Backend.NETWORKD:
        outcome = networkd.persist(req.iface, req.values, env, req.mac, entry)
    else:
        raise PersistError("no_backend")
    if outcome.written:
        state.record(STATE_FILE, entries, req.iface, outcome.entry)
    return {"ok": True, "backend": detected.value, "written": outcome.written,
            "unsupported": outcome.unsupported}


def _remove(req: Request, entries: dict, env: Env) -> dict:
    entry = entries.get(req.iface)
    if entry is None:
        if networkd.remove(req.iface, env):  # our own file, left by a lost state file
            return {"ok": True, "backend": "networkd", "removed": []}
        raise PersistError("not_managed")
    if entry["backend"] == "nm":
        nm.remove(entry, env)
    else:
        networkd.remove(req.iface, env)
    state.forget(STATE_FILE, entries, req.iface)
    return {"ok": True, "backend": entry["backend"], "removed": entry["fields"]}


def handle(obj: dict) -> dict:
    try:
        req = parse(obj, SYS_ROOT)  # the interface is checked before anything else
        env = _env()
        with state.locked(STATE_FILE):
            entries = state.load(STATE_FILE)
            if req.op == "status":
                return _status(req, entries.get(req.iface), env)
            if req.op == "persist":
                return _persist(req, entries, env)
            return _remove(req, entries, env)
    except PersistError as exc:
        reply = {"ok": False, "error": exc.code}
        if exc.reason:
            reply["reason"] = exc.reason
        return reply
    except OSError as exc:
        return {"ok": False, "error": "io_error", "reason": first_line(exc.strerror or str(exc))}
    except Exception as exc:  # never a traceback on the wire
        return {"ok": False, "error": "internal_error", "reason": type(exc).__name__}


def main(stdin: IO[bytes], stdout: IO[bytes], argv: list[str] | None = None) -> int:
    if argv:
        print("ethtool-gui-persist: no arguments are accepted", file=sys.stderr)
        return 2
    request = next(iter_messages(stdin, MAX_LINE), None)
    if request is None:
        reply = {"ok": False, "error": "bad_request", "reason": "no request"}
    elif set(request) == {"error"} and request["error"] in ("bad_json", "line_too_long"):
        reply = {"ok": False, "error": request["error"]}
    else:
        reply = handle(request)
    stdout.write(encode_message(reply))
    stdout.flush()
    return 0 if reply["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.stdin.buffer, sys.stdout.buffer, sys.argv[1:]))
