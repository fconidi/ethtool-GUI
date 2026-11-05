"""Read NIC statistics and turn successive samples into per-second rates."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .parse_basic import Runner, _checked, _require_valid
from .runner import run_ethtool


@dataclass(frozen=True)
class Stats:
    values: dict[str, int] = field(default_factory=dict)
    supported: bool = True
    reason: str | None = None


_LINE = re.compile(r"^\s+(\S.*?):\s*(\d+)\s*$")


def parse_stats(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            values[m.group(1)] = int(m.group(2))
    return values


def read_stats(iface: str, run: Runner = run_ethtool) -> Stats:
    _require_valid(iface)
    result = run(["-S", iface])
    reason = _checked(result, iface)
    if reason is not None:
        return Stats({}, False, reason)
    return Stats(parse_stats(result.stdout))


class RateTracker:
    """Per-second rates between consecutive samples; never negative."""

    def __init__(self) -> None:
        self._last: dict[str, int] | None = None
        self._last_now = 0.0

    def update(self, sample: dict[str, int], now: float) -> dict[str, float]:
        if self._last is None:
            self._last, self._last_now = dict(sample), now
            return {}
        dt = now - self._last_now
        if not dt > 0 or not math.isfinite(dt):
            return {}
        rates: dict[str, float] = {}
        for name, new in sample.items():
            old = self._last.get(name)
            if old is None:
                continue
            rates[name] = (new - old) / dt if new >= old else 0.0
        self._last, self._last_now = dict(sample), now
        return rates


def group_of(name: str) -> str:
    n = name.lower()
    if "pause" in n or "flow_control" in n:
        return "pause"
    if "collision" in n:
        return "collisions"
    if "drop" in n or "missed" in n or "no_buffer" in n:
        return "drops"
    if "err" in n:
        return "errors"
    if n.endswith("packets"):
        return "packets"
    if n.endswith("bytes"):
        return "bytes"
    return "other"
