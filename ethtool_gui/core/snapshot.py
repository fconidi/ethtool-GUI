"""Capture, store and compare interface settings.

A snapshot is a JSON-serialisable dict. A ``Field`` is stored as its plain value
when supported and as ``{"unsupported": reason}`` otherwise. A section whose
reader failed (other than the device vanishing) is ``{"error": text}``.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import parse_basic, parse_settings, stats
from .ifaces import valid_name
from .models import Field
from .module import read_module
from .runner import EthtoolError, InvalidInterfaceName, NoSuchInterface, run_ethtool

VERSION = 1


@dataclass(frozen=True)
class Change:
    path: str
    old: Any
    new: Any


def _plain(obj: Any) -> Any:
    if isinstance(obj, Field):
        return obj.value if obj.supported else {"unsupported": obj.reason}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def take(iface: str, run=run_ethtool) -> dict:
    if not valid_name(iface):
        raise InvalidInterfaceName(iface)
    readers = {
        "link": parse_basic.read_link,
        "driver": parse_basic.read_driver,
        "permanent_mac": parse_basic.read_permanent_mac,
        "pause": parse_settings.read_pause,
        "ring": parse_settings.read_ring,
        "channels": parse_settings.read_channels,
        "coalesce": parse_settings.read_coalesce,
        "features": parse_settings.read_features,
        "eee": parse_settings.read_eee,
        "wol": parse_settings.read_wol,
        "stats": stats.read_stats,
        "module": read_module,
    }
    snap: dict[str, Any] = {
        "version": VERSION,
        "iface": iface,
        "taken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    for name, reader in readers.items():
        try:
            snap[name] = _plain(reader(iface, run=run))
        except (NoSuchInterface, InvalidInterfaceName):
            raise
        except EthtoolError as exc:
            snap[name] = {"error": str(exc)}
    return snap


def default_directory() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "share"
    return root / "ethtool-gui" / "snapshots"


def save(snap: dict, directory: Path) -> Path:
    iface = snap.get("iface")
    if not isinstance(iface, str) or not valid_name(iface):
        raise InvalidInterfaceName(str(iface))
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    data = json.dumps(snap, indent=2, sort_keys=True) + "\n"
    n = 1
    while True:
        name = f"{iface}-{stamp}.json" if n == 1 else f"{iface}-{stamp}-{n}.json"
        path = directory / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        return path


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict) and value:
        for key, sub in value.items():
            _flatten(sub, f"{prefix}.{key}" if prefix else str(key), out)
    else:
        out[prefix] = value


def diff(a: dict, b: dict, include_stats: bool = False) -> list[Change]:
    flat_a: dict[str, Any] = {}
    flat_b: dict[str, Any] = {}
    skip = {"taken_at"} | (set() if include_stats else {"stats"})
    for src, flat in ((a, flat_a), (b, flat_b)):
        _flatten({k: v for k, v in src.items() if k not in skip}, "", flat)
    return [Change(p, flat_a.get(p), flat_b.get(p))
            for p in sorted(flat_a.keys() | flat_b.keys())
            if flat_a.get(p) != flat_b.get(p) or (p in flat_a) != (p in flat_b)]


def _value_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def to_text(snap_or_changes: dict | list) -> str:
    if isinstance(snap_or_changes, list):
        if not snap_or_changes:
            return "no differences"
        return "\n".join(f"{c.path}: {_value_text(c.old)} -> {_value_text(c.new)}"
                         for c in snap_or_changes)
    lines: list[str] = []
    for section, content in snap_or_changes.items():
        if isinstance(content, dict) and content:
            flat: dict[str, Any] = {}
            _flatten(content, "", flat)
            lines.append(f"[{section}]")
            lines.extend(f"{k}: {_value_text(v)}" for k, v in flat.items())
        else:
            lines.append(f"{section}: {_value_text(content)}")
    return "\n".join(lines)
