"""Readers the helper may run as root, and JSON conversion of their results."""

from __future__ import annotations

import enum
from typing import Any

from ..core import parse_basic, parse_settings, stats
from ..core.module import read_module
from ..core.snapshot import _plain

READERS = {
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
    "rss": parse_settings.read_rss,
    "ntuple": parse_settings.read_ntuple,
    "eeprom_dump": parse_settings.read_eeprom_dump,
    "registers_dump": parse_settings.read_registers_dump,
    "phy_stats": parse_settings.read_phy_stats,
}


def to_jsonable(obj: Any) -> Any:
    """Plain JSON data for a reader result (same shape as a snapshot section)."""
    value = _plain(obj)
    return _enums(value)


def _enums(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _enums(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_enums(v) for v in value]
    return value
