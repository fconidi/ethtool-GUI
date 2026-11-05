"""List network interfaces from sysfs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}")


def valid_name(name: str) -> bool:
    return _NAME.fullmatch(name) is not None


@dataclass(frozen=True)
class Iface:
    name: str
    is_physical: bool
    operstate: str
    mac: str


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def list_interfaces(sys_root: Path = Path("/sys/class/net"), include_virtual: bool = False) -> list[Iface]:
    try:
        entries = sorted(Path(sys_root).iterdir())
    except OSError:
        return []
    found = []
    for entry in entries:
        if not valid_name(entry.name) or not entry.is_dir():
            continue
        physical = (entry / "device").exists()
        if not physical and not include_virtual:
            continue
        found.append(Iface(entry.name, physical, _read(entry / "operstate"), _read(entry / "address")))
    return found
