"""NetworkManager backend: ``nmcli connection modify`` on the device's profile.

The profile is found by UUID (``GENERAL.CON-UUID``), never by name. Only the
profile changes: the connection is never brought up, down or reapplied, since
ethtool already applied the live value. The old value of every property about
to change is read first and kept, so ``remove`` can put it back.
"""

from __future__ import annotations

import re
from typing import Callable

from ..helper.operations import SHORT_FEATURES
from . import detect
from .common import Env, Outcome, PersistError, call, check, dedupe, first_line
from .matrix import GROUP_KEYS, NM, SUPPORT, field_name, fields_of, items, merge, subset
from .request import validate_values

ETH = "802-3-ethernet."
WOL_NAMES = {"p": "phy", "u": "unicast", "m": "multicast", "b": "broadcast", "a": "arp",
             "g": "magic"}  # NetworkManager has no flag for SecureOn ("s")
_LINK_PROPS = {"autoneg": ETH + "auto-negotiate", "speed": ETH + "speed", "duplex": ETH + "duplex"}
_NM_FEATURE = {long: short for short, long in SHORT_FEATURES.items()}

_WOL_WORDS = frozenset(WOL_NAMES.values()) | {"none", "default", "ignore"}
_DECIMAL = re.compile(r"[0-9]{1,10}")
_FLAGS = re.compile(r"[0-9]{1,10}|0x[0-9a-fA-F]{1,8}")
_ETHTOOL_WORDS = frozenset({"", "on", "off", "yes", "no", "true", "false", "ignore", "default"})


def prop_for(group: str, key: str) -> str | None:
    """The fixed nmcli property for a field, None if NetworkManager cannot keep it."""
    if not SUPPORT[(field_name(group, key), NM)]:
        return None
    if group == "link":
        return _LINK_PROPS[key]
    if group == "wol":
        return ETH + "wake-on-lan"
    if group == "features":
        return "ethtool.feature-" + _NM_FEATURE.get(key, key)
    return f"ethtool.{group}-{key}"


ALL_PROPS = frozenset(p for g, keys in GROUP_KEYS.items() for k in keys
                      if (p := prop_for(g, k)) is not None)


def valid_previous(prop: str, value: object) -> bool:
    """A value read back from nmcli (or the state file) that is safe to pass to nmcli."""
    if prop not in ALL_PROPS or not isinstance(value, str):
        return False
    if prop == ETH + "auto-negotiate":
        return value in ("yes", "no", "true", "false")
    if prop == ETH + "speed":
        return _DECIMAL.fullmatch(value) is not None
    if prop == ETH + "duplex":
        return value in ("", "half", "full")
    if prop == ETH + "wake-on-lan":
        return (value == "" or _FLAGS.fullmatch(value) is not None
                or all(word in _WOL_WORDS for word in value.split(",")))
    return value in _ETHTOOL_WORDS or _DECIMAL.fullmatch(value) is not None


def _settings(values: dict) -> tuple[list[tuple[str | None, str, str]], list[str]]:
    """``(field or None, property, value)`` to set, and the unsupported fields."""
    changes: list[tuple[str | None, str, str]] = []
    unsupported = []
    for field, group, key, value in items(values):
        prop = prop_for(group, key)
        if prop is None or (group == "wol" and "s" in value):
            unsupported.append(field)
        elif group == "link" and key == "autoneg":
            if value:  # negotiate everything: no forced speed or duplex
                changes += [(field, prop, "yes"), (None, _LINK_PROPS["speed"], "0"),
                            (None, _LINK_PROPS["duplex"], "")]
            else:
                changes.append((field, prop, "no"))
        elif group == "wol":
            changes.append((field, prop, "none" if value == "d"
                            else ",".join(WOL_NAMES[c] for c in value)))
        elif type(value) is bool:
            on = ("1", "0") if group == "coalesce" else ("on", "off")
            changes.append((field, prop, on[0] if value else on[1]))
        else:  # validated int, or "half"/"full"
            changes.append((field, prop, str(value)))
    return changes, unsupported


def _read(uuid: str, prop: str, env: Env) -> str | None:
    """Current profile value of ``prop``; None if this nmcli does not know the property."""
    result = call(env.run, ["nmcli", "-g", prop, "connection", "show", uuid])
    if result.returncode != 0:
        if "invalid field" in result.stderr.lower():
            return None
        check(result, "nmcli_failed")
    value = result.stdout.strip().replace(", ", ",")
    if not valid_previous(prop, value):
        raise PersistError("unexpected_value", prop)
    return value


def _modify(uuid: str, pairs: list[tuple[str, str]], env: Env) -> None:
    argv = ["nmcli", "connection", "modify", uuid]
    for prop, value in pairs:
        argv += [prop, value]
    check(call(env.run, argv), "nmcli_failed")


def persist(iface: str, values: dict, env: Env, entry: dict | None = None,
            commit: Callable[[dict], None] | None = None) -> Outcome:
    """Write ``values`` to the profile. ``commit(new_entry)`` runs just before the change."""
    values = validate_values(values)
    uuid = detect.nm_uuid(iface, env.run)
    if uuid is None:
        raise PersistError("no_profile")
    if entry is not None and entry.get("uuid") != uuid:
        raise PersistError("profile_changed")
    changes, unsupported = _settings(values)
    version = detect.nm_version(env.run) if changes else None
    previous = dict(entry["previous"]) if entry else {}
    kept = []
    for field, prop, value in changes:
        if not detect.prop_supported(prop, version):
            unsupported.append(field)
            continue
        if prop not in previous:
            old = _read(uuid, prop, env)
            if old is None:
                unsupported.append(field)
                continue
            previous[prop] = old
        kept.append((field, prop, value))
    unsupported = dedupe([f for f in unsupported if f is not None])
    written = dedupe([f for f, _, _ in kept if f is not None])
    if not written:
        return Outcome(entry, [], unsupported)
    merged = merge(entry["values"] if entry else {}, subset(values, written))
    new_entry = {"backend": NM, "uuid": uuid, "values": merged, "fields": fields_of(merged),
                 "previous": previous}
    if commit is not None:
        commit(new_entry)  # recorded first: a crash after the change still leaves the old values
    _modify(uuid, [(prop, value) for _, prop, value in kept], env)
    return Outcome(new_entry, written, unsupported)


def remove(entry: dict, env: Env) -> None:
    """Put back the recorded values; a profile deleted meanwhile is not an error."""
    previous = entry["previous"]
    if not previous:
        return
    argv = ["nmcli", "connection", "modify", entry["uuid"]]
    for prop in sorted(previous):
        argv += [prop, previous[prop]]
    result = call(env.run, argv)
    if result.returncode != 0:
        if "unknown connection" in result.stderr.lower():
            return
        raise PersistError("nmcli_failed", first_line(result.stderr) or f"exit code {result.returncode}")


def current(entry: dict, env: Env) -> dict[str, str]:
    """The profile's present values of the properties this program changed."""
    out = {}
    for prop in sorted(entry["previous"]):
        try:
            value = _read(entry["uuid"], prop, env)
        except PersistError:
            value = None
        if value is not None:
            out[prop] = value
    return out
