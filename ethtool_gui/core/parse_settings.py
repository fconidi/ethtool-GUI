"""Read pause, ring, channels, coalescing, features, EEE, WoL, RSS and raw dumps."""

from __future__ import annotations

import re

from .models import (Channels, Coalesce, Eee, Feature, FeaturesInfo, Field, Pause,
                     Ring, Rss, Wol)
from .parse_basic import Runner, _checked, _require_valid, read_link
from .runner import run_ethtool

NOT_REPORTED = "not reported by the driver"

_PAIR = re.compile(r"^([A-Za-z][^:]*?):\s*(.*)$")


def _sections(text: str) -> dict[str, dict[str, str]]:
    """Split output into ``{section: {key: value}}``; keys before a heading go in ``""``."""
    out: dict[str, dict[str, str]] = {"": {}}
    current = ""
    for line in text.splitlines():
        m = _PAIR.match(line.strip())
        if not m or not line.strip():
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        if value == "" and not line.startswith("\t"):
            if key.startswith(("Pre-set", "Current")):
                current = "max" if key.startswith("Pre-set") else "cur"
                out.setdefault(current, {})
            continue
        out[current][key] = value
    return out


def _int(value: str | None, what: str = NOT_REPORTED) -> Field[int]:
    if value is None or not value.isdigit():
        return Field(None, False, what)
    return Field(int(value))


def _onoff(value: str | None) -> Field[bool]:
    if value in ("on", "off"):
        return Field(value == "on")
    return Field(None, False, NOT_REPORTED)


def _fetch(iface: str, run: Runner, args: list[str]):
    _require_valid(iface)
    result = run(args + [iface])
    return result, _checked(result, iface)


def read_pause(iface: str, run: Runner = run_ethtool) -> Pause:
    result, reason = _fetch(iface, run, ["-a"])
    if reason is not None:
        return Pause.unsupported(reason)
    pairs = _sections(result.stdout)[""]
    return Pause(autoneg=_onoff(pairs.get("Autonegotiate")),
                 rx=_onoff(pairs.get("RX")), tx=_onoff(pairs.get("TX")))


def read_ring(iface: str, run: Runner = run_ethtool) -> Ring:
    result, reason = _fetch(iface, run, ["-g"])
    if reason is not None:
        return Ring.unsupported(reason)
    sec = _sections(result.stdout)
    cur, mx = sec.get("cur", {}), sec.get("max", {})
    return Ring(rx=_int(cur.get("RX")), rx_max=_int(mx.get("RX")),
                tx=_int(cur.get("TX")), tx_max=_int(mx.get("TX")),
                rx_mini=_int(cur.get("RX Mini")), rx_mini_max=_int(mx.get("RX Mini")),
                rx_jumbo=_int(cur.get("RX Jumbo")), rx_jumbo_max=_int(mx.get("RX Jumbo")))


def read_channels(iface: str, run: Runner = run_ethtool) -> Channels:
    result, reason = _fetch(iface, run, ["-l"])
    if reason is not None:
        return Channels.unsupported(reason)
    sec = _sections(result.stdout)
    cur, mx = sec.get("cur", {}), sec.get("max", {})
    return Channels(rx=_int(cur.get("RX")), rx_max=_int(mx.get("RX")),
                    tx=_int(cur.get("TX")), tx_max=_int(mx.get("TX")),
                    other=_int(cur.get("Other")), other_max=_int(mx.get("Other")),
                    combined=_int(cur.get("Combined")), combined_max=_int(mx.get("Combined")))


def read_coalesce(iface: str, run: Runner = run_ethtool) -> Coalesce:
    result, reason = _fetch(iface, run, ["-c"])
    if reason is not None:
        return Coalesce(supported=False, reason=reason)
    values: dict[str, int | bool] = {}
    for line in result.stdout.splitlines():
        adaptive = re.match(r"^Adaptive RX:\s*(\S+)\s+TX:\s*(\S+)", line)
        if adaptive:
            for name, val in (("adaptive-rx", adaptive.group(1)), ("adaptive-tx", adaptive.group(2))):
                if val in ("on", "off"):
                    values[name] = val == "on"
            continue
        m = re.match(r"^([a-z][a-z0-9-]*):\s*(\d+)\s*$", line)
        if m:
            values[m.group(1)] = int(m.group(2))
    return Coalesce(values=values)


_FEATURE = re.compile(r"^\s*([A-Za-z0-9_-]+):\s*(on|off)(?:\s*\[(fixed)\])?(?:\s*\[requested (on|off)\])?\s*$")


def read_features(iface: str, run: Runner = run_ethtool) -> FeaturesInfo:
    result, reason = _fetch(iface, run, ["-k"])
    if reason is not None:
        return FeaturesInfo(supported=False, reason=reason)
    features: dict[str, Feature] = {}
    for line in result.stdout.splitlines():
        m = _FEATURE.match(line)
        if m:
            features[m.group(1)] = Feature(
                on=m.group(2) == "on", fixed=m.group(3) is not None,
                requested=None if m.group(4) is None else m.group(4) == "on")
    return FeaturesInfo(features=features)


def _eee_pairs(text: str) -> dict[str, list[str]]:
    pairs: dict[str, list[str]] = {}
    key = None
    for line in text.splitlines():
        m = re.match(r"^\t(\S[^:]*):\s*(.*)$", line)
        if m:
            key = m.group(1)
            pairs[key] = [m.group(2).strip()]
        elif line.startswith("\t ") and key is not None:
            pairs[key].append(line.strip())
        else:
            key = None
    return pairs


def _eee_modes(pairs: dict[str, list[str]], key: str) -> list[str]:
    words = " ".join(pairs.get(key, [])).split()
    return [] if not words or words[0] == "Not" else words


def read_eee(iface: str, run: Runner = run_ethtool) -> Eee:
    result, reason = _fetch(iface, run, ["--show-eee"])
    if reason is not None:
        return Eee.unsupported(reason)
    pairs = _eee_pairs(result.stdout)
    status = " ".join(pairs.get("EEE status", []))
    enabled = Field(True) if status.startswith("enabled") else \
        Field(False) if status.startswith("disabled") else _onoff(None)
    active = Field("- active" in status) if status else _onoff(None)
    lpi = re.match(r"(\d+)", " ".join(pairs.get("Tx LPI", [])))
    return Eee(enabled=enabled, active=active, tx_lpi_us=_int(lpi.group(1) if lpi else None),
               supported_modes=_eee_modes(pairs, "Supported EEE link modes"),
               advertised_modes=_eee_modes(pairs, "Advertised EEE link modes"),
               partner_modes=_eee_modes(pairs, "Link partner advertised EEE link modes"))


def read_wol(iface: str, run: Runner = run_ethtool) -> Wol:
    link = read_link(iface, run=run)
    return Wol(supported=link.wol_supported.value, current=link.wol_current.value,
               supported_flag=link.wol_supported.value is not None)


def read_rss(iface: str, run: Runner = run_ethtool) -> Rss:
    result, reason = _fetch(iface, run, ["-x"])
    if reason is not None:
        return Rss(supported=False, reason=reason)
    indirection: list[int] = []
    key = None
    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*\d+:\s+([\d\s]+)$", line)
        if m:
            indirection.extend(int(n) for n in m.group(1).split())
        elif line.strip() == "RSS hash key:" and i + 1 < len(lines):
            key = lines[i + 1].strip() or None
    return Rss(raw=result.stdout, indirection=indirection, hash_key=key)


def read_ntuple(iface: str, run: Runner = run_ethtool) -> Field[str]:
    """Read the driver's RX classification rules without changing them."""
    return _dump(iface, run, ["-n"])


def _dump(iface: str, run: Runner, args: list[str]) -> Field[str]:
    result, reason = _fetch(iface, run, args)
    if reason is not None:
        return Field(None, False, reason)
    return Field(result.stdout)


def read_eeprom_dump(iface: str, run: Runner = run_ethtool) -> Field[str]:
    return _dump(iface, run, ["-e"])


def read_registers_dump(iface: str, run: Runner = run_ethtool) -> Field[str]:
    return _dump(iface, run, ["-d"])


def read_phy_stats(iface: str, run: Runner = run_ethtool) -> Field[str]:
    return _dump(iface, run, ["--phy-statistics"])
