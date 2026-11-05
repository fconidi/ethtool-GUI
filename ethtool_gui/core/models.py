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
