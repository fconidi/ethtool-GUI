"""Read link, driver and permanent address from ethtool's text output."""

from __future__ import annotations

import re
from typing import Callable

from .ifaces import valid_name
from .models import DriverInfo, Field, LinkInfo
from .runner import ErrorKind, InvalidInterfaceName, NoSuchInterface, Result, classify_error, run_ethtool

Runner = Callable[[list[str]], Result]


def _reason(stderr: str) -> str:
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("netlink error:"):
            line = line[len("netlink error:"):].strip()
        if line:
            return line
    return "unavailable"


def _require_valid(iface: str) -> None:
    if not valid_name(iface):
        raise InvalidInterfaceName(iface)


def _checked(result: Result, iface: str) -> str | None:
    """Return None on success, else the human reason; raise if the device is gone."""
    if result.returncode == 0:
        return None
    if classify_error(result.stderr) is ErrorKind.NO_DEVICE:
        raise NoSuchInterface(iface)
    return _reason(result.stderr)


_KEY_LINE = re.compile(r"^\t(\S[^:]*):\s*(.*)$")


def _settings_pairs(text: str) -> dict[str, str]:
    """Map each tab-indented ``Key: value`` line to its value, joining continuation lines."""
    pairs: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        m = _KEY_LINE.match(line)
        if m:
            key = m.group(1)
            pairs[key] = m.group(2).strip()
        elif line.startswith("\t ") and key is not None:
            pairs[key] = (pairs[key] + " " + line.strip()).strip()
        else:
            key = None  # message level and its continuation, headers
    return pairs


def _modes(pairs: dict[str, str], key: str) -> list[str]:
    value = pairs.get(key, "")
    if not value or value.startswith("Not reported"):
        return []
    return value.split()


def _text(pairs: dict[str, str], key: str, unknown: tuple[str, ...] = ("Unknown",)) -> str | None:
    value = pairs.get(key)
    if value is None or not value or value in unknown:
        return None
    return value


def parse_settings(text: str) -> LinkInfo:
    pairs = _settings_pairs(text)
    speed = re.match(r"(\d+)\s*Mb/s", pairs.get("Speed", ""))
    duplex = pairs.get("Duplex", "")
    autoneg = pairs.get("Auto-negotiation")
    detected = pairs.get("Link detected")
    return LinkInfo(
        state=Field({"yes": "up", "no": "down"}.get(detected or "")),
        speed_mbps=Field(int(speed.group(1)) if speed else None),
        duplex=Field(duplex if duplex in ("Full", "Half") else None),
        autoneg=Field({"on": True, "off": False}.get(autoneg or "")),
        supported_modes=Field(_modes(pairs, "Supported link modes")),
        advertised_modes=Field(_modes(pairs, "Advertised link modes")),
        partner_modes=Field(_modes(pairs, "Link partner advertised link modes")),
        port=Field(_text(pairs, "Port")),
        transceiver=Field(_text(pairs, "Transceiver")),
        mdix=Field(_text(pairs, "MDI-X")),
        wol_supported=Field(_text(pairs, "Supports Wake-on")),
        wol_current=Field(_text(pairs, "Wake-on")),
    )


def read_link(iface: str, run: Runner = run_ethtool) -> LinkInfo:
    _require_valid(iface)
    result = run([iface])
    reason = _checked(result, iface)
    if reason is not None:
        return LinkInfo.unsupported(reason)
    return parse_settings(result.stdout)


def parse_driver(text: str) -> DriverInfo:
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            pairs[key.strip()] = value.strip()

    def flag(name: str) -> bool:
        return pairs.get(name) == "yes"

    return DriverInfo(
        driver=Field(pairs.get("driver") or None),
        version=Field(pairs.get("version") or None),
        firmware_version=Field(pairs.get("firmware-version") or None),
        bus_info=Field(pairs.get("bus-info") or None),
        supports_test=flag("supports-test"),
        supports_eeprom=flag("supports-eeprom-access"),
        supports_register_dump=flag("supports-register-dump"),
        supports_statistics=flag("supports-statistics"),
    )


def read_driver(iface: str, run: Runner = run_ethtool) -> DriverInfo:
    _require_valid(iface)
    result = run(["-i", iface])
    reason = _checked(result, iface)
    if reason is not None:
        return DriverInfo.unsupported(reason)
    return parse_driver(result.stdout)


def read_permanent_mac(iface: str, run: Runner = run_ethtool) -> Field[str]:
    _require_valid(iface)
    result = run(["-P", iface])
    reason = _checked(result, iface)
    if reason is not None:
        return Field(None, False, reason)
    m = re.search(r"Permanent address:\s*(\S+)", result.stdout)
    return Field(m.group(1) if m else None)
