"""Which settings each persistence backend can keep (design §8c.11).

A field is ``<group>.<key>`` (``link.speed``, ``ring.rx``, ``features.<long name>``)
or just ``<group>`` when the key repeats it (``wol``, ``eee``). Groups and keys
are those of the helper operations.
"""

from __future__ import annotations

from ..helper.operations import (CHANNEL_KEYS, COALESCE_BOOL, COALESCE_INT, LONG_FEATURES,
                                 OPS, RING_KEYS)

NM, NETWORKD = "nm", "networkd"

GROUP_KEYS: dict[str, tuple[str, ...]] = {
    "link": ("autoneg", "speed", "duplex", "advertise"),
    "wol": ("wol",),
    "pause": OPS["pause"].keys,
    "ring": RING_KEYS,
    "channels": CHANNEL_KEYS,
    "coalesce": COALESCE_BOOL + COALESCE_INT,
    "features": tuple(sorted(LONG_FEATURES)),
    "eee": OPS["eee"].keys,
}
REPLACED_WHOLE = ("link", "wol")  # a new value replaces the group, it is not merged key by key


def field_name(group: str, key: str) -> str:
    return group if key == group else f"{group}.{key}"


FIELDS: tuple[str, ...] = tuple(field_name(g, k) for g, keys in GROUP_KEYS.items() for k in keys)

# NetworkManager has no faithful representation of channels, EEE or an arbitrary
# advertised-modes mask; cqe-mode and UFO have no ``ethtool.*`` property at all.
_NM_UNSUPPORTED = frozenset(
    {"link.advertise", "coalesce.cqe-mode-rx", "coalesce.cqe-mode-tx",
     "features.udp-fragmentation-offload"}
    | {field_name("channels", k) for k in GROUP_KEYS["channels"]}
    | {field_name("eee", k) for k in GROUP_KEYS["eee"]})
_NETWORKD_SUPPORTED = frozenset({"link.autoneg", "link.speed", "link.duplex", "wol",
                                 "ring.rx", "ring.tx"})

SUPPORT: dict[tuple[str, str], bool] = {}
for _field in FIELDS:
    SUPPORT[(_field, NM)] = _field not in _NM_UNSUPPORTED
    SUPPORT[(_field, NETWORKD)] = _field in _NETWORKD_SUPPORTED
del _field


def items(values: dict) -> list[tuple[str, str, str, object]]:
    """``(field, group, key, value)`` for validated values, in a fixed order."""
    out = []
    for group, keys in GROUP_KEYS.items():
        sub = values.get(group) or {}
        out += [(field_name(group, k), group, k, sub[k]) for k in keys if k in sub]
    return out


def fields_of(values: dict) -> list[str]:
    return [field for field, *_ in items(values)]


def subset(values: dict, fields: list[str]) -> dict:
    """Only the keys whose field is in ``fields``."""
    out: dict = {}
    for field, group, key, value in items(values):
        if field in fields:
            out.setdefault(group, {})[key] = value
    return out


def merge(old: dict, new: dict) -> dict:
    out = {group: dict(sub) for group, sub in old.items()}
    for group, sub in new.items():
        if group in REPLACED_WHOLE:
            out[group] = dict(sub)
        else:
            out.setdefault(group, {}).update(sub)
    return out
