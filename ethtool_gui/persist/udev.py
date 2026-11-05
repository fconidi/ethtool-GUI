"""Ask udev which ``.link`` file it would apply, before and after writing ours.

systemd applies only the first ``.link`` file that matches a device, so ours
may hide another one or the naming policy of ``99-default.link``. The output of
``udevadm test-builtin net_setup_link`` is read defensively: only the lines
``ID_NET_LINK_FILE=``, ``ID_NET_LINK_FILE_DROPINS=`` and ``ID_NET_NAME=`` count.
systemd 257 prints them, and only them, on stdout; the log (parse warnings about
unrelated files, "Parsed configuration file", ...) goes to stderr and is only
searched for the naming message. Stderr is read for the properties only as a
fallback when stdout has none. Without a single, clean ``ID_NET_LINK_FILE=`` the
answer is ``cannot_verify``.

The command runs in test mode ("skipping application of ethtool settings"), so
it does not change the device; even if a version did, it would apply the same
values ethtool has just set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.ifaces import valid_name
from .common import Env, PersistConflict, PersistError, call, check

_PATH = re.compile(r"/[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*")
_NO_NAME = "Name= is not given"  # "Policies didn't yield a name and Name= is not given, ..."


@dataclass(frozen=True)
class Probe:
    link_file: str
    name: str | None
    dropins: bool
    no_name: bool


def _values(lines: list[str], key: str) -> list[str]:
    """Distinct values of ``key=`` lines, in order (a stream may repeat a line)."""
    prefix = key + "="
    return list(dict.fromkeys(line[len(prefix):].strip() for line in lines
                              if line.startswith(prefix)))


def parse(stdout: str, stderr: str) -> Probe:
    text = stdout + "\n" + stderr
    lines = stdout.splitlines()
    if not any(line.startswith("ID_NET_") for line in lines):
        lines = stderr.splitlines()  # fallback only: not where systemd puts them
    files = _values(lines, "ID_NET_LINK_FILE")
    names = _values(lines, "ID_NET_NAME")
    dropins = _values(lines, "ID_NET_LINK_FILE_DROPINS")
    if (len(files) != 1 or not _PATH.fullmatch(files[0]) or ".." in files[0].split("/")
            or not files[0].endswith(".link") or len(names) > 1 or len(dropins) > 1):
        raise PersistConflict("cannot_verify")
    name = names[0] if names else None
    if name is not None and not valid_name(name):
        raise PersistConflict("cannot_verify")
    return Probe(files[0], name, bool(dropins and dropins[0]), _NO_NAME in text)


def probe(iface: str, env: Env) -> Probe:
    if not valid_name(iface):
        raise PersistError("bad_iface")
    argv = ["udevadm", "test-builtin", "net_setup_link", str(env.sys_root / iface)]
    result = check(call(env.run, argv), "udevadm_failed")
    return parse(result.stdout, result.stderr)


def verify(before: Probe, after: Probe, ours: str) -> None:
    """Refuse unless our file is the one applied and naming comes out the same."""
    if after.link_file != ours:
        raise PersistConflict("earlier_link_file")
    if after.dropins:
        raise PersistConflict("link_dropins")
    if after.name != before.name:
        raise PersistConflict("naming_changed")
    if after.no_name and not before.no_name:
        raise PersistConflict("naming_lost")
