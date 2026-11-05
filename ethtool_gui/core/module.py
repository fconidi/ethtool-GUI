"""Read SFP/QSFP module identity and optical diagnostics from ``ethtool -m``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .parse_basic import Runner, _checked, _require_valid
from .runner import run_ethtool


class Level(Enum):
    OK = "ok"
    WARN = "warn"
    ALARM = "alarm"


@dataclass
class Reading:
    name: str
    value: float
    unit: str
    warn_low: float | None = None
    alarm_low: float | None = None
    warn_high: float | None = None
    alarm_high: float | None = None


@dataclass
class ModuleInfo:
    supported: bool = True
    reason: str | None = None
    identifier: str | None = None
    vendor: str | None = None
    part: str | None = None
    readings: list[Reading] = field(default_factory=list)
    alarm_flags: dict[str, bool] = field(default_factory=dict)


# (reading name, name used in the threshold lines)
_QUANTITIES = (
    ("Laser bias current", "Laser bias current"),
    ("Laser output power", "Laser output power"),
    ("Receiver signal average optical power", "Laser rx power"),
    ("Module temperature", "Module temperature"),
    ("Module voltage", "Module voltage"),
)
_NUM = r"(-?\d+(?:\.\d+)?)"
_FLAG = re.compile(r".+ (?:alarm|warning)$")


def _pairs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and line.startswith("\t"):
            out[key.strip()] = value.strip()
    return out


def _number(value: str | None) -> tuple[float, str] | None:
    """Pick the figure to use: dBm if present, degrees C for temperature, else the first number."""
    if not value:
        return None
    for pattern, unit in ((_NUM + r"\s*dBm", "dBm"), (_NUM + r"\s*degrees C", "C")):
        m = re.search(pattern, value)
        if m:
            return float(m.group(1)), unit
    m = re.match(_NUM + r"\s*(\S*)", value)
    return (float(m.group(1)), m.group(2)) if m else None


def parse_module(text: str) -> ModuleInfo:
    pairs = _pairs(text)
    info = ModuleInfo(
        identifier=pairs.get("Identifier") or None,
        vendor=pairs.get("Vendor name") or None,
        part=pairs.get("Vendor PN") or None,
    )
    for key, value in pairs.items():
        if _FLAG.match(key) and value in ("On", "Off"):
            info.alarm_flags[key] = value == "On"
    for name, tname in _QUANTITIES:
        reading = _number(pairs.get(name))
        if reading is None:
            continue
        r = Reading(name, reading[0], reading[1])
        for attr, label in (("alarm_high", "high alarm"), ("alarm_low", "low alarm"),
                            ("warn_high", "high warning"), ("warn_low", "low warning")):
            t = _number(pairs.get(f"{tname} {label} threshold"))
            setattr(r, attr, t[0] if t else None)
        info.readings.append(r)
    return info


def level(r: Reading) -> Level:
    def beyond(low: float | None, high: float | None) -> bool:
        return (low is not None and r.value < low) or (high is not None and r.value > high)

    if beyond(r.alarm_low, r.alarm_high):
        return Level.ALARM
    if beyond(r.warn_low, r.warn_high):
        return Level.WARN
    return Level.OK


def read_module(iface: str, run: Runner = run_ethtool) -> ModuleInfo:
    _require_valid(iface)
    result = run(["-m", iface])
    reason = _checked(result, iface)
    if reason is not None:
        return ModuleInfo(supported=False, reason=reason)
    return parse_module(result.stdout)
