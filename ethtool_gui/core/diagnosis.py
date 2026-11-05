"""Turn current readings into short diagnosis hints.

Each hint carries an i18n key and a severity (``info``, ``warn`` or ``alarm``).
Missing data (``None`` or unsupported fields, empty rates) never produces a hint.

Rules and thresholds:

* ``diag.link_down`` (info): link state is ``down``.
* ``diag.low_speed`` (warn): link up at 10 or 100 Mb/s although the supported
  modes include a 1000baseT mode.
* ``diag.duplex_mismatch`` (warn): link up, half duplex while autonegotiation is on.
* ``diag.crc_errors`` (warn): any rate above 0 per second for a counter in the
  ``errors`` group named rx_crc_errors, rx_frame_errors, rx_length_errors or
  rx_align_errors.
* ``diag.collisions`` (warn): the summed ``collisions`` group rate is above 0 per second.
* ``diag.pause_active`` (info): the summed ``pause`` group rate exceeds 1.0 frame
  per second. The pause settings are not needed: counted frames are proof enough.
* ``diag.sfp_rx_low`` (warn/alarm): the module's receive power reading is beyond
  its warning (warn) or alarm (alarm) threshold.

Hints are ordered by severity (alarm, warn, info), then by key.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import LinkInfo, Pause
from .module import Level, ModuleInfo, level
from .stats import group_of

PAUSE_RATE_LIMIT = 1.0
_CRC_COUNTERS = ("rx_crc_errors", "rx_frame_errors", "rx_length_errors", "rx_align_errors")
_RX_POWER = "Receiver signal average optical power"
_ORDER = {"alarm": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class Hint:
    key: str
    severity: str


def _link_hints(link: LinkInfo | None) -> list[Hint]:
    if link is None:
        return []
    state = link.state.value if link.state.supported else None
    if state == "down":
        return [Hint("diag.link_down", "info")]
    if state != "up":
        return []
    hints = []
    speed = link.speed_mbps.value if link.speed_mbps.supported else None
    modes = link.supported_modes.value if link.supported_modes.supported else None
    if speed in (10, 100) and any(m.startswith("1000baseT") for m in modes or []):
        hints.append(Hint("diag.low_speed", "warn"))
    duplex = link.duplex.value if link.duplex.supported else None
    autoneg = link.autoneg.value if link.autoneg.supported else None
    if duplex == "Half" and autoneg is True:
        hints.append(Hint("diag.duplex_mismatch", "warn"))
    return hints


def _rate_hints(rates: dict[str, float] | None) -> list[Hint]:
    hints = []
    rates = rates or {}
    if any(rates.get(n, 0) > 0 and group_of(n) == "errors" for n in _CRC_COUNTERS):
        hints.append(Hint("diag.crc_errors", "warn"))
    if sum(r for n, r in rates.items() if group_of(n) == "collisions") > 0:
        hints.append(Hint("diag.collisions", "warn"))
    if sum(r for n, r in rates.items() if group_of(n) == "pause") > PAUSE_RATE_LIMIT:
        hints.append(Hint("diag.pause_active", "info"))
    return hints


def _module_hints(module: ModuleInfo | None) -> list[Hint]:
    if module is None or not module.supported:
        return []
    worst = None
    for reading in module.readings:
        if reading.name == _RX_POWER:
            lv = level(reading)
            if lv is Level.ALARM or (lv is Level.WARN and worst is None):
                worst = lv
    if worst is None:
        return []
    return [Hint("diag.sfp_rx_low", "alarm" if worst is Level.ALARM else "warn")]


def diagnose(link: LinkInfo | None, rates: dict[str, float] | None,
             module: ModuleInfo | None, pause: Pause | None) -> list[Hint]:
    hints = _link_hints(link) + _rate_hints(rates) + _module_hints(module)
    return sorted(hints, key=lambda h: (_ORDER[h.severity], h.key))
