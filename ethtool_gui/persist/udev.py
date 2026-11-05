"""Ask udev which ``.link`` file it would apply, before and after writing ours.

systemd applies only the first ``.link`` file that matches a device, so ours
may hide another one or the naming policy of ``99-default.link``. The output of
``udevadm test-builtin net_setup_link`` is read defensively: properties on
stdout (``ID_NET_LINK_FILE=``, ``ID_NET_NAME=``, ``ID_NET_LINK_FILE_DROPINS=``),
log lines on stderr. Anything unexpected is refused as ``cannot_verify``.
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


def _property(lines: list[str], key: str) -> list[str]:
    prefix = key + "="
    return [line[len(prefix):] for line in lines if line.startswith(prefix)]


def parse(stdout: str, stderr: str) -> Probe:
    lines = [line.strip() for line in stdout.splitlines()]
    files = _property(lines, "ID_NET_LINK_FILE")
    names = _property(lines, "ID_NET_NAME")
    dropins = _property(lines, "ID_NET_LINK_FILE_DROPINS")
    if (len(files) != 1 or not _PATH.fullmatch(files[0]) or ".." in files[0].split("/")
            or not files[0].endswith(".link") or len(names) > 1 or len(dropins) > 1):
        raise PersistConflict("cannot_verify")
    name = names[0] if names else None
    if name is not None and not valid_name(name):
        raise PersistConflict("cannot_verify")
    return Probe(files[0], name, bool(dropins and dropins[0].strip()),
                 _NO_NAME in stderr or _NO_NAME in stdout)


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
