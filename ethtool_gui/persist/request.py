"""Validation of the one request the persistence program accepts.

``{"op": "status"|"persist"|"remove", "iface": str, "values": {...}}``; ``values``
only with ``persist``. A ``permanent_mac`` sent along with ``persist`` is accepted
for compatibility and ignored: the program reads the address itself. The interface is checked
first, before anything else is looked at. Values go through the helper's own
validators, so nothing reaches nmcli or a file that the helper would not accept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core.ifaces import valid_name
from ..helper.operations import OPS, SHORT_FEATURES, ValidationError
from .common import PersistError
from .matrix import GROUP_KEYS

FIELDS_BY_OP = {
    "status": frozenset({"op", "iface"}),
    "remove": frozenset({"op", "iface"}),
    "persist": frozenset({"op", "iface", "values", "permanent_mac"}),
}
ZERO_MAC = "00:00:00:00:00:00"
_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")


@dataclass(frozen=True)
class Request:
    op: str
    iface: str
    values: dict


def check_iface(iface: object, sys_root: Path) -> str:
    if not isinstance(iface, str) or not valid_name(iface):
        raise PersistError("bad_iface")
    if not (Path(sys_root) / iface).exists():
        raise PersistError("no_device")
    return iface


def validate_values(values: object) -> dict:
    """Validated copy of ``values``; feature names are turned into ethtool's long names."""
    if not isinstance(values, dict) or not values:
        raise PersistError("bad_values", "values must be a non-empty object")
    out = {}
    for group, sub in values.items():
        if group not in GROUP_KEYS:
            raise PersistError("bad_values", "unknown group")
        try:
            OPS[group].validate(sub)
        except ValidationError as exc:
            raise PersistError("bad_values", f"{group}: {exc}") from None
        if group == "features":
            sub = {SHORT_FEATURES.get(k, k): v for k, v in sub.items()}
        out[group] = dict(sub)
    return out


def validate_mac(value: object) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not _MAC.fullmatch(value.lower()):
        raise PersistError("bad_request", "permanent_mac")
    value = value.lower()
    return "" if value == ZERO_MAC else value


def parse(obj: dict, sys_root: Path) -> Request:
    iface = check_iface(obj.get("iface"), sys_root)
    op = obj.get("op")
    if not isinstance(op, str) or op not in FIELDS_BY_OP:
        raise PersistError("bad_request", "op")
    if not set(obj) <= FIELDS_BY_OP[op]:
        raise PersistError("bad_request", "unexpected field")
    if op != "persist":
        return Request(op, iface, {})
    return Request(op, iface, validate_values(obj.get("values")))
