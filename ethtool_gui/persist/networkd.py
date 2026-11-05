"""systemd ``.link`` backend: ``<link_dir>/10-ethtool-gui-<iface>.link``.

The file is built only from validated values and fixed key names, starts with
the ``# ethtool-gui managed`` header, and matches the permanent MAC address (or
``OriginalName=`` when there is none). Because only the first matching file is
applied, the naming keys of the file in use now (``99-default.link`` or our own
earlier file) are carried over, and udev is asked before and after writing; on
any doubt the previous state of the directory is put back and the change refused.
"""

from __future__ import annotations

import os

from ..core.ifaces import valid_name
from . import udev
from .common import (Env, Outcome, PersistConflict, PersistError, open_dir, read_file,
                     remove_file, write_file)
from .matrix import NETWORKD, SUPPORT, fields_of, items, merge, subset
from .request import validate_mac, validate_values

HEADER = "# ethtool-gui managed"
PREFIX = "10-ethtool-gui-"
MAX_BYTES = 64 * 1024
MODE = 0o644

BITS = {10: "10M", 100: "100M", 1000: "1G", 2500: "2500M", 5000: "5G", 10000: "10G"}
WOL = {"p": "phy", "u": "unicast", "m": "multicast", "b": "broadcast", "a": "arp",
       "g": "magic", "s": "secureon", "d": "off"}
RING = {"rx": "ReceiveBufferSize", "tx": "TransmitBufferSize"}
OUR_KEYS = ("AutoNegotiation", "BitsPerSecond", "Duplex", "WakeOnLan",
            "ReceiveBufferSize", "TransmitBufferSize")
NAMING = {
    "NamePolicy": frozenset({"kernel", "database", "onboard", "slot", "path", "mac", "keep"}),
    "AlternativeNamesPolicy": frozenset({"database", "onboard", "slot", "path", "mac"}),
    "MACAddressPolicy": frozenset({"persistent", "random", "none"}),
}


def file_name(iface: str) -> str:
    if not isinstance(iface, str) or not valid_name(iface):
        raise PersistError("bad_iface")
    return f"{PREFIX}{iface}.link"


def is_managed(data: bytes) -> bool:
    return data.split(b"\n", 1)[0] == HEADER.encode()


def settings(values: dict) -> tuple[dict[str, str], list[str], list[str]]:
    """``[Link]`` keys, written fields and unsupported fields for validated values."""
    keys: dict[str, str] = {}
    written, unsupported = [], []
    for field, group, key, value in items(values):
        if not SUPPORT[(field, NETWORKD)] or (group == "ring" and value < 1):
            unsupported.append(field)
            continue
        if group == "link":
            keys[{"autoneg": "AutoNegotiation", "speed": "BitsPerSecond", "duplex": "Duplex"}[key]] = (
                ("yes" if value else "no") if key == "autoneg"
                else BITS[value] if key == "speed" else value)
        elif group == "wol":
            keys["WakeOnLan"] = " ".join(WOL[c] for c in value)
        else:
            keys[RING[key]] = str(value)
        written.append(field)
    return keys, written, unsupported


def parse_naming(data: bytes, own: bool) -> dict[str, str]:
    """Naming keys of the file in use; refuse anything this program would not carry over."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PersistConflict("unsupported_default") from None
    section, naming = None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            section = line
            if section not in ("[Match]", "[Link]"):
                raise PersistConflict("unsupported_default")
            continue
        if section != "[Link]":
            continue
        key, _, value = (part.strip() for part in line.partition("="))
        words = value.split()
        if key in NAMING:
            if (not words or not set(words) <= NAMING[key]
                    or (key == "MACAddressPolicy" and len(words) != 1)):
                raise PersistConflict("unsupported_default")
            naming[key] = " ".join(words)
        elif not (own and key in OUR_KEYS):
            raise PersistConflict("unsupported_default")
    return naming


def render(iface: str, mac: str, naming: dict[str, str], keys: dict[str, str]) -> bytes:
    match = f"PermanentMACAddress={mac}" if mac else f"OriginalName={iface}"
    lines = [HEADER, "[Match]", match, "", "[Link]"]
    lines += [f"{k}={naming[k]}" for k in NAMING if k in naming]
    lines += [f"{k}={keys[k]}" for k in OUR_KEYS if k in keys]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _naming_in_use(before: udev.Probe, ours: str, old: bytes | None, env: Env) -> dict[str, str]:
    if before.dropins:
        raise PersistConflict("link_dropins")
    if before.link_file == ours:
        if old is None:
            raise PersistConflict("cannot_verify")
        return parse_naming(old, own=True)
    if before.link_file not in env.default_links:
        raise PersistConflict("custom_link_file")
    directory, _, name = before.link_file.rpartition("/")
    fd = open_dir(directory or "/")
    try:
        data = read_file(fd, name, MAX_BYTES)
    finally:
        os.close(fd)
    if data is None:
        raise PersistConflict("cannot_verify")
    return parse_naming(data, own=False)


def persist(iface: str, values: dict, env: Env, mac: str, entry: dict | None = None) -> Outcome:
    values, mac = validate_values(values), validate_mac(mac)
    name = file_name(iface)
    _, written, unsupported = settings(values)
    if not written:
        return Outcome(entry, [], unsupported)
    merged = merge(entry["values"] if entry else {}, subset(values, written))
    keys, _, _ = settings(merged)
    ours = str(env.link_dir / name)
    fd = open_dir(env.link_dir)
    try:
        old = read_file(fd, name, MAX_BYTES)
        if old is not None and not is_managed(old):
            raise PersistError("foreign_file", name)
        before = udev.probe(iface, env)
        naming = _naming_in_use(before, ours, old, env)
        write_file(fd, name, render(iface, mac, naming, keys), MODE)
        try:
            udev.verify(before, udev.probe(iface, env), ours)
        except BaseException:
            if old is None:
                remove_file(fd, name)
            else:
                write_file(fd, name, old, MODE)
            raise
    finally:
        os.close(fd)
    new_entry = {"backend": NETWORKD, "mac": mac, "values": merged, "fields": fields_of(merged)}
    return Outcome(new_entry, written, unsupported)


def present(iface: str, env: Env) -> bool:
    fd = open_dir(env.link_dir)
    try:
        data = read_file(fd, file_name(iface), MAX_BYTES)
    finally:
        os.close(fd)
    return data is not None and is_managed(data)


def remove(iface: str, env: Env) -> bool:
    """Delete our file for ``iface`` if it carries the header; True if one was deleted."""
    name = file_name(iface)
    fd = open_dir(env.link_dir)
    try:
        data = read_file(fd, name, MAX_BYTES)
        if data is None or not is_managed(data):
            return False
        remove_file(fd, name)
        return True
    finally:
        os.close(fd)
