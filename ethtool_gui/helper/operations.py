"""The fixed list of changes the helper may make, and how to undo them.

Every operation turns typed values into ``ethtool`` arguments itself: ints are
range checked, words come from fixed tables and no string from the request is
passed through. There is deliberately no EEPROM, flash, reset or register write.
"""

from __future__ import annotations

import re
from typing import Callable

from ..core import parse_basic, parse_settings
from ..core.ifaces import valid_name
from ..core.runner import Result

Runner = Callable[[list[str]], Result]


class ValidationError(ValueError):
    """The values do not describe an allowed change."""


class RestoreError(Exception):
    """ethtool refused to put the saved values back."""


SPEEDS = (10, 100, 1000, 2500, 5000, 10000)
WOL_LETTERS = "pumbagsd"
MAX_KEYS = 64
_ADVERTISE = re.compile(r"0x[0-9a-f]{1,32}")

# Link mode name (as printed by ethtool) -> bit of the ``advertise`` mask.
MODE_BITS = {
    "10baseT/Half": 0, "10baseT/Full": 1, "100baseT/Half": 2, "100baseT/Full": 3,
    "1000baseT/Half": 4, "1000baseT/Full": 5, "10000baseT/Full": 12, "2500baseX/Full": 15,
    "1000baseKX/Full": 17, "10000baseKX4/Full": 18, "10000baseKR/Full": 19,
    "20000baseMLD2/Full": 21, "20000baseKR2/Full": 22, "40000baseKR4/Full": 23,
    "40000baseCR4/Full": 24, "40000baseSR4/Full": 25, "40000baseLR4/Full": 26,
    "25000baseCR/Full": 31, "25000baseKR/Full": 32, "25000baseSR/Full": 33,
    "50000baseCR2/Full": 34, "50000baseKR2/Full": 35, "100000baseKR4/Full": 36,
    "100000baseSR4/Full": 37, "100000baseCR4/Full": 38, "100000baseLR4_ER4/Full": 39,
    "50000baseSR2/Full": 40, "1000baseX/Full": 41, "10000baseCR/Full": 42,
    "10000baseSR/Full": 43, "10000baseLR/Full": 44, "10000baseLRM/Full": 45,
    "10000baseER/Full": 46, "2500baseT/Full": 47, "5000baseT/Full": 48,
}

# ``ethtool -K`` short names and the names ``ethtool -k`` prints for them.
SHORT_FEATURES = {
    "rx": "rx-checksumming", "tx": "tx-checksumming", "sg": "scatter-gather",
    "tso": "tcp-segmentation-offload", "ufo": "udp-fragmentation-offload",
    "gso": "generic-segmentation-offload", "gro": "generic-receive-offload",
    "lro": "large-receive-offload", "rxvlan": "rx-vlan-offload", "txvlan": "tx-vlan-offload",
    "ntuple": "ntuple-filters", "rxhash": "receive-hashing",
}
LONG_FEATURES = frozenset(SHORT_FEATURES.values()) | {
    "tx-checksum-ipv4", "tx-checksum-ip-generic", "tx-checksum-ipv6", "tx-checksum-sctp",
    "tx-scatter-gather", "tx-tcp-segmentation", "tx-tcp-ecn-segmentation",
    "tx-tcp-mangleid-segmentation", "tx-tcp6-segmentation", "tx-udp-segmentation",
    "rx-gro-hw", "rx-gro-list", "rx-udp-gro-forwarding", "rx-vlan-filter", "rx-fcs", "rx-all",
    "tx-nocache-copy", "hw-tc-offload", "rx-vlan-stag-hw-parse", "rx-vlan-stag-filter",
    "tx-vlan-stag-hw-insert",
}
FEATURES = frozenset(SHORT_FEATURES) | LONG_FEATURES

COALESCE_BOOL = ("adaptive-rx", "adaptive-tx", "cqe-mode-rx", "cqe-mode-tx")
COALESCE_INT = (
    "rx-usecs", "rx-frames", "rx-usecs-irq", "rx-frames-irq",
    "tx-usecs", "tx-frames", "tx-usecs-irq", "tx-frames-irq", "stats-block-usecs",
    "pkt-rate-low", "rx-usecs-low", "rx-frames-low", "tx-usecs-low", "tx-frames-low",
    "pkt-rate-high", "rx-usecs-high", "rx-frames-high", "tx-usecs-high", "tx-frames-high",
    "sample-interval",
)
COALESCE_MAX = 10_000_000
RING_KEYS = ("rx", "rx-mini", "rx-jumbo", "tx")
RING_MAX = 1 << 20
CHANNEL_KEYS = ("rx", "tx", "other", "combined")
CHANNEL_MAX = 4096


def _onoff(flag: bool) -> str:
    return "on" if flag else "off"


def _mapping(values: object) -> dict:
    if not isinstance(values, dict) or not values or len(values) > MAX_KEYS:
        raise ValidationError("values must be a non-empty object")
    if not all(isinstance(k, str) for k in values):
        raise ValidationError("keys must be strings")
    return values


def _only(values: dict, allowed) -> None:
    if not set(values) <= set(allowed):
        raise ValidationError("unknown key")


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValidationError("expected true or false")
    return value


def _int(value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"expected an integer from {low} to {high}")
    return value


class Operation:
    name = ""
    flag = ""
    is_link_affecting = False

    def validate(self, values: object) -> list[str]:
        """Return the ethtool arguments that follow the interface name."""
        return self._args(_mapping(values))

    def argv(self, iface: str, values: object) -> list[str]:
        if not isinstance(iface, str) or not valid_name(iface):
            raise ValidationError("bad interface name")
        return [self.flag, iface, *self.validate(values)]

    def snapshot(self, iface: str, run: Runner, values: dict | None = None) -> dict:
        """Current values for the keys in ``values``; ``{}`` if they cannot all be read."""
        current = self._current(iface, run)
        wanted = list(values) if values else list(current)
        if not all(key in current for key in wanted):
            return {}
        return {key: current[key] for key in wanted}

    def restore(self, iface: str, saved: dict, run: Runner) -> None:
        if not saved:
            return
        result = run(self.argv(iface, saved))
        if result.returncode != 0:
            raise RestoreError(result.stderr.strip() or f"exit code {result.returncode}")

    # -- per operation ----------------------------------------------------

    def _args(self, values: dict) -> list[str]:
        raise NotImplementedError

    def _current(self, iface: str, run: Runner) -> dict:
        raise NotImplementedError


class _Fields(Operation):
    """Operations whose values map one to one onto fields of a reader's result."""

    keys: tuple[str, ...] = ()
    reader = None
    attrs: dict[str, str] = {}

    def _current(self, iface: str, run: Runner) -> dict:
        info = type(self).reader(iface, run=run)
        out = {}
        for key in self.keys:
            field = getattr(info, self.attrs.get(key, key))
            if field.supported and field.value is not None:
                out[key] = field.value
        return out


class Link(Operation):
    name, flag, is_link_affecting = "link", "-s", True

    def _args(self, values: dict) -> list[str]:
        _only(values, ("autoneg", "speed", "duplex", "advertise"))
        autoneg = _bool(values["autoneg"]) if "autoneg" in values else None
        args = [] if autoneg is None else ["autoneg", _onoff(autoneg)]
        if autoneg is False:
            if "speed" not in values or "duplex" not in values or "advertise" in values:
                raise ValidationError("autoneg off needs speed and duplex, and no advertise")
            if type(values["speed"]) is not int or values["speed"] not in SPEEDS:
                raise ValidationError("unsupported speed")
            if values["duplex"] not in ("half", "full"):
                raise ValidationError("duplex must be half or full")
            return args + ["speed", str(values["speed"]), "duplex", values["duplex"]]
        if "speed" in values or "duplex" in values:
            raise ValidationError("speed and duplex need autoneg off")
        if "advertise" in values:
            mask = values["advertise"]
            if not isinstance(mask, str) or not _ADVERTISE.fullmatch(mask.lower()):
                raise ValidationError("advertise must be a hex mask such as 0x2f")
            args += ["advertise", mask.lower()]
        if not args:
            raise ValidationError("nothing to change")
        return args

    def snapshot(self, iface: str, run: Runner, values: dict | None = None) -> dict:
        info = parse_basic.read_link(iface, run=run)
        if info.autoneg.value is True:
            saved: dict = {"autoneg": True}
            modes = info.advertised_modes.value or []
            if modes and all(m in MODE_BITS for m in modes):
                saved["advertise"] = "0x%x" % sum(1 << MODE_BITS[m] for m in set(modes))
            elif values and "advertise" in values:
                return {}  # the old mask cannot be put back
            return saved
        speed, duplex = info.speed_mbps.value, info.duplex.value
        if info.autoneg.value is False and speed in SPEEDS and duplex in ("Full", "Half"):
            return {"autoneg": False, "speed": speed, "duplex": duplex.lower()}
        return {}


class WakeOnLan(Operation):
    name, flag = "wol", "-s"

    def _args(self, values: dict) -> list[str]:
        _only(values, ("wol",))
        letters = values["wol"]
        if (not isinstance(letters, str) or not letters or len(set(letters)) != len(letters)
                or not set(letters) <= set(WOL_LETTERS) or ("d" in letters and letters != "d")):
            raise ValidationError(f"wol must be letters from {WOL_LETTERS!r}, or d alone")
        return ["wol", letters]

    def _current(self, iface: str, run: Runner) -> dict:
        current = parse_settings.read_wol(iface, run=run).current
        try:
            self._args({"wol": current})
        except ValidationError:
            return {}
        return {"wol": current}


class Pause(_Fields):
    name, flag = "pause", "-A"
    keys = ("autoneg", "rx", "tx")
    reader = parse_settings.read_pause

    def _args(self, values: dict) -> list[str]:
        _only(values, self.keys)
        return [w for k in self.keys if k in values for w in (k, _onoff(_bool(values[k])))]


class _Counts(_Fields):
    limit = 0

    def _args(self, values: dict) -> list[str]:
        _only(values, self.keys)
        return [w for k in self.keys if k in values for w in (k, str(_int(values[k], 0, self.limit)))]


class Ring(_Counts):
    name, flag, is_link_affecting = "ring", "-G", True
    keys, limit = RING_KEYS, RING_MAX
    reader = parse_settings.read_ring
    attrs = {"rx-mini": "rx_mini", "rx-jumbo": "rx_jumbo"}


class Channels(_Counts):
    name, flag, is_link_affecting = "channels", "-L", True
    keys, limit = CHANNEL_KEYS, CHANNEL_MAX
    reader = parse_settings.read_channels


class Coalesce(Operation):
    name, flag = "coalesce", "-C"

    def _args(self, values: dict) -> list[str]:
        _only(values, COALESCE_BOOL + COALESCE_INT)
        args = []
        for key in sorted(values):
            if key in COALESCE_BOOL:
                args += [key, _onoff(_bool(values[key]))]
            else:
                args += [key, str(_int(values[key], 0, COALESCE_MAX))]
        return args

    def _current(self, iface: str, run: Runner) -> dict:
        info = parse_settings.read_coalesce(iface, run=run)
        return {k: v for k, v in info.values.items()
                if (k in COALESCE_BOOL and type(v) is bool)
                or (k in COALESCE_INT and type(v) is int and 0 <= v <= COALESCE_MAX)}


class Features(Operation):
    name, flag, is_link_affecting = "features", "-K", True

    def _args(self, values: dict) -> list[str]:
        _only(values, FEATURES)
        return [w for k in sorted(values) for w in (k, _onoff(_bool(values[k])))]

    def snapshot(self, iface: str, run: Runner, values: dict | None = None) -> dict:
        info = parse_settings.read_features(iface, run=run)
        names = [SHORT_FEATURES.get(k, k) for k in (values or ())]
        saved = {}
        for name in names:
            feature = info.features.get(name)
            if name not in LONG_FEATURES or feature is None or feature.fixed:
                return {}  # unknown or fixed: it could not be put back
            saved[name] = feature.on
        return saved


class Eee(Operation):
    name, flag, is_link_affecting = "eee", "--set-eee", True
    keys = ("eee", "tx-lpi")

    def _args(self, values: dict) -> list[str]:
        _only(values, self.keys)
        return [w for k in self.keys if k in values for w in (k, _onoff(_bool(values[k])))]

    def _current(self, iface: str, run: Runner) -> dict:
        info = parse_settings.read_eee(iface, run=run)
        if not info.enabled.supported or info.enabled.value is None:
            return {}
        return {"eee": info.enabled.value, "tx-lpi": info.tx_lpi_us.value is not None}


OPS: dict[str, Operation] = {op.name: op for op in (
    Link(), WakeOnLan(), Pause(), Ring(), Channels(), Coalesce(), Features(), Eee())}
