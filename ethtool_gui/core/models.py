"""Plain data containers shared by the readers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Field(Generic[T]):
    value: T | None = None
    supported: bool = True
    reason: str | None = None


def _unsupported(reason: str | None) -> "Field":
    return Field(None, False, reason)


@dataclass
class LinkInfo:
    state: Field[str] = field(default_factory=Field)
    speed_mbps: Field[int] = field(default_factory=Field)
    duplex: Field[str] = field(default_factory=Field)
    autoneg: Field[bool] = field(default_factory=Field)
    supported_modes: Field[list[str]] = field(default_factory=Field)
    advertised_modes: Field[list[str]] = field(default_factory=Field)
    partner_modes: Field[list[str]] = field(default_factory=Field)
    port: Field[str] = field(default_factory=Field)
    transceiver: Field[str] = field(default_factory=Field)
    mdix: Field[str] = field(default_factory=Field)
    wol_supported: Field[str] = field(default_factory=Field)
    wol_current: Field[str] = field(default_factory=Field)

    @classmethod
    def unsupported(cls, reason: str | None) -> "LinkInfo":
        return cls(**{name: _unsupported(reason) for name in cls.__dataclass_fields__})


@dataclass
class DriverInfo:
    driver: Field[str] = field(default_factory=Field)
    version: Field[str] = field(default_factory=Field)
    firmware_version: Field[str] = field(default_factory=Field)
    bus_info: Field[str] = field(default_factory=Field)
    supports_test: bool = False
    supports_eeprom: bool = False
    supports_register_dump: bool = False
    supports_statistics: bool = False

    @classmethod
    def unsupported(cls, reason: str | None) -> "DriverInfo":
        return cls(driver=_unsupported(reason), version=_unsupported(reason),
                   firmware_version=_unsupported(reason), bus_info=_unsupported(reason))


def _all_unsupported(cls, reason: str | None):
    return cls(**{name: _unsupported(reason) for name in cls.__dataclass_fields__})


@dataclass
class Pause:
    autoneg: Field[bool] = field(default_factory=Field)
    rx: Field[bool] = field(default_factory=Field)
    tx: Field[bool] = field(default_factory=Field)

    @classmethod
    def unsupported(cls, reason: str | None) -> "Pause":
        return _all_unsupported(cls, reason)


@dataclass
class Ring:
    rx: Field[int] = field(default_factory=Field)
    rx_max: Field[int] = field(default_factory=Field)
    tx: Field[int] = field(default_factory=Field)
    tx_max: Field[int] = field(default_factory=Field)
    rx_mini: Field[int] = field(default_factory=Field)
    rx_mini_max: Field[int] = field(default_factory=Field)
    rx_jumbo: Field[int] = field(default_factory=Field)
    rx_jumbo_max: Field[int] = field(default_factory=Field)

    @classmethod
    def unsupported(cls, reason: str | None) -> "Ring":
        return _all_unsupported(cls, reason)


@dataclass
class Channels:
    rx: Field[int] = field(default_factory=Field)
    rx_max: Field[int] = field(default_factory=Field)
    tx: Field[int] = field(default_factory=Field)
    tx_max: Field[int] = field(default_factory=Field)
    other: Field[int] = field(default_factory=Field)
    other_max: Field[int] = field(default_factory=Field)
    combined: Field[int] = field(default_factory=Field)
    combined_max: Field[int] = field(default_factory=Field)

    @classmethod
    def unsupported(cls, reason: str | None) -> "Channels":
        return _all_unsupported(cls, reason)


@dataclass
class Coalesce:
    """Values by ethtool name (``rx-usecs``, ``adaptive-rx`` ...); ``n/a`` entries are omitted."""

    values: dict[str, int | bool] = field(default_factory=dict)
    supported: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class Feature:
    on: bool
    fixed: bool = False
    requested: bool | None = None


@dataclass
class FeaturesInfo:
    features: dict[str, Feature] = field(default_factory=dict)
    supported: bool = True
    reason: str | None = None


@dataclass
class Eee:
    enabled: Field[bool] = field(default_factory=Field)
    active: Field[bool] = field(default_factory=Field)
    tx_lpi_us: Field[int] = field(default_factory=Field)
    supported_modes: list[str] = field(default_factory=list)
    advertised_modes: list[str] = field(default_factory=list)
    partner_modes: list[str] = field(default_factory=list)

    @classmethod
    def unsupported(cls, reason: str | None) -> "Eee":
        return cls(enabled=_unsupported(reason), active=_unsupported(reason),
                   tx_lpi_us=_unsupported(reason))


@dataclass
class Wol:
    supported: str | None = None
    current: str | None = None
    supported_flag: bool = False


@dataclass
class Rss:
    supported: bool = True
    reason: str | None = None
    raw: str = ""
    indirection: list[int] = field(default_factory=list)
    hash_key: str | None = None
