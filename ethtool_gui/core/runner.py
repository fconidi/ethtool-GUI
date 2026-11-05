"""Run the ethtool binary and classify its failures."""

from __future__ import annotations

import enum
import os
import subprocess
from dataclasses import dataclass


class EthtoolError(Exception):
    """Base class for runner errors."""


class EthtoolMissing(EthtoolError):
    """The ethtool binary could not be found."""


class EthtoolTimeout(EthtoolError):
    """The command did not finish in time."""


class NoSuchInterface(EthtoolError):
    """The interface does not exist (any more)."""


class InvalidInterfaceName(EthtoolError):
    """The name is not a plausible interface name and was not passed to ethtool."""


class ErrorKind(enum.Enum):
    NOT_SUPPORTED = "not_supported"
    NO_PERMISSION = "no_permission"
    NO_DEVICE = "no_device"
    OTHER = "other"


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


def run_ethtool(args: list[str], *, binary: str = "ethtool", timeout: float = 5.0) -> Result:
    env = dict(os.environ, LC_ALL="C")
    try:
        done = subprocess.run([binary, *args], capture_output=True, text=True,
                              errors="replace", timeout=timeout, env=env, check=False)
    except FileNotFoundError as exc:
        raise EthtoolMissing(f"{binary}: command not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise EthtoolTimeout(f"{binary} timed out after {timeout:g} s") from exc
    return Result(done.returncode, done.stdout, done.stderr)


_NO_DEVICE = ("no such device", "no device matches name")
_NO_PERMISSION = ("permission denied", "operation not permitted")
_NOT_SUPPORTED = ("operation not supported",)


def classify_error(stderr: str) -> ErrorKind:
    text = stderr.lower()
    if any(m in text for m in _NO_DEVICE):
        return ErrorKind.NO_DEVICE
    if any(m in text for m in _NO_PERMISSION):
        return ErrorKind.NO_PERMISSION
    if any(m in text for m in _NOT_SUPPORTED):
        return ErrorKind.NOT_SUPPORTED
    return ErrorKind.OTHER
