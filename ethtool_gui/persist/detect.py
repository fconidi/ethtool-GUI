"""Which backend keeps an interface's settings, and what the installed nmcli knows."""

from __future__ import annotations

import enum
import os
import re
import stat
from pathlib import Path

from ..core.ifaces import valid_name
from .common import PersistError, Run, call


class Backend(str, enum.Enum):
    NM = "nm"
    NETWORKD = "networkd"
    NONE = "none"


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_VERSION = re.compile(r"\bversion (\d{1,4})\.(\d{1,4})\b")

ETHERNET_PROPS = {
    "802-3-ethernet.auto-negotiate": (1, 0), "802-3-ethernet.speed": (1, 0),
    "802-3-ethernet.duplex": (1, 0), "802-3-ethernet.wake-on-lan": (1, 2),
}
# The first ethtool offloads (1.14); most other kernel feature names followed in
# 1.20 and the GRO list/forwarding ones later. Kept conservative: a property the
# installed nmcli still does not know is caught when its old value is read.
_FEATURES_FIRST = frozenset({"rx", "tx", "sg", "tso", "gso", "gro", "lro", "rxvlan", "txvlan",
                             "ntuple", "rxhash"})
_FEATURES_LATE = frozenset({"rx-gro-list", "rx-udp-gro-forwarding"})
_ETHTOOL_PREFIXES = {"ethtool.ring-": (1, 26), "ethtool.coalesce-": (1, 26),
                     "ethtool.pause-": (1, 32)}


def nm_uuid(iface: str, run: Run) -> str | None:
    """UUID of the profile active on ``iface``; None if NetworkManager does not manage it."""
    if not isinstance(iface, str) or not valid_name(iface):
        raise PersistError("bad_iface")
    try:
        result = call(run, ["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", iface])
    except PersistError as exc:
        if exc.code == "tool_missing":
            return None
        raise
    if result.returncode != 0:
        return None  # unknown device, or NetworkManager not running
    text = result.stdout.strip()
    if not text:
        return None
    if not UUID.fullmatch(text):
        raise PersistError("cannot_verify", "nmcli printed an unexpected profile id")
    return text


def backend_for(iface: str, run: Run, link_dir: Path | None = None) -> Backend:
    if nm_uuid(iface, run) is not None:
        return Backend.NM
    if link_dir is None:
        return Backend.NETWORKD
    try:
        is_dir = stat.S_ISDIR(os.lstat(link_dir).st_mode)
    except OSError:
        is_dir = False
    return Backend.NETWORKD if is_dir else Backend.NONE


def nm_version(run: Run) -> tuple[int, int] | None:
    try:
        result = call(run, ["nmcli", "--version"])
    except PersistError as exc:
        if exc.code == "tool_missing":
            return None
        raise
    match = _VERSION.search(result.stdout) if result.returncode == 0 else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def min_version(prop: str) -> tuple[int, int] | None:
    if prop in ETHERNET_PROPS:
        return ETHERNET_PROPS[prop]
    if prop.startswith("ethtool.feature-"):
        name = prop[len("ethtool.feature-"):]
        if name in _FEATURES_FIRST:
            return (1, 14)
        return (1, 32) if name in _FEATURES_LATE else (1, 20)
    for prefix, version in _ETHTOOL_PREFIXES.items():
        if prop.startswith(prefix):
            return version
    return None


def prop_supported(prop: str, version: tuple[int, int] | None) -> bool:
    needed = min_version(prop)
    return needed is not None and version is not None and version >= needed


def nm_supports(prop: str, run: Run) -> bool:
    """True if the installed nmcli is new enough for ``prop`` (fail closed when unknown)."""
    return prop_supported(prop, nm_version(run))
