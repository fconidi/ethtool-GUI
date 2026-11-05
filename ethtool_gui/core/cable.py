"""Run ``ethtool --cable-test`` and parse the per-pair results."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parse_basic import Runner, _checked, _require_valid
from .runner import run_ethtool


@dataclass
class PairResult:
    pair: str
    status: str
    length_m: float | None = None
    fault_at_m: float | None = None


@dataclass
class CableTestResult:
    pairs: list[PairResult] = field(default_factory=list)
    supported: bool = True
    reason: str | None = None


_CODE = re.compile(r"^Pair (\w+) code (.+?)\s*$")
_DIST = re.compile(r"^Pair (\w+), (fault length|length): (-?\d+(?:\.\d+)?)\s*m\s*$")


def parse_cable_test(text: str) -> list[PairResult]:
    pairs: dict[str, PairResult] = {}
    for line in text.splitlines():
        line = line.strip()
        m = _CODE.match(line)
        if m:
            pairs.setdefault(m.group(1), PairResult(m.group(1), m.group(2))).status = m.group(2)
            continue
        m = _DIST.match(line)
        if m:
            p = pairs.setdefault(m.group(1), PairResult(m.group(1), "Unknown"))
            if m.group(2) == "length":
                p.length_m = float(m.group(3))
            else:
                p.fault_at_m = float(m.group(3))
    return list(pairs.values())


def run_cable_test(iface: str, run: Runner = run_ethtool) -> CableTestResult:
    _require_valid(iface)
    result = run(["--cable-test", iface])
    reason = _checked(result, iface)
    if reason is not None:
        return CableTestResult([], False, reason)
    return CableTestResult(parse_cable_test(result.stdout))
