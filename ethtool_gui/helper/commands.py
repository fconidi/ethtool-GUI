"""Helper commands that keep no state: root-only reads, blink, cable test, self-test.

Each takes an interface name the daemon has already validated and a ``run``
callable ``run(args, timeout) -> Result``.
"""

from __future__ import annotations

from typing import Callable

from ..core.cable import run_cable_test
from ..core.runner import ErrorKind, Result, classify_error
from .readers import READERS, to_jsonable

Run = Callable[[list[str], float], Result]

TIMEOUT = 5.0
CABLE_TEST_TIMEOUT = 30.0
BLINK_EXTRA = 5
DETAIL_MAX = 2000
OUTPUT_MAX = 64 * 1024


class CommandError(Exception):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


def check(result: Result) -> Result:
    """Raise a CommandError naming the failure if ethtool did not succeed."""
    if result.returncode != 0:
        kind = classify_error(result.stderr)
        code = "ethtool_failed" if kind is ErrorKind.OTHER else kind.value
        raise CommandError(code, result.stderr.strip()[:DETAIL_MAX])
    return result


def confirmed(request: dict) -> None:
    if request.get("confirmed") is not True:
        raise CommandError("confirmation_required")


def read(iface: str, request: dict, run: Run) -> dict:
    what = request.get("what")
    if not isinstance(what, str) or what not in READERS:
        raise CommandError("bad_request", "what")
    return {"data": to_jsonable(READERS[what](iface, run=lambda a: run(a, TIMEOUT)))}


def blink(iface: str, request: dict, run: Run) -> dict:
    seconds = request.get("seconds")
    if type(seconds) is not int or not 1 <= seconds <= 60:
        raise CommandError("bad_request", "seconds")
    check(run(["-p", iface, str(seconds)], seconds + BLINK_EXTRA))
    return {}


def cable_test(iface: str, request: dict, run: Run) -> dict:
    confirmed(request)
    result = run_cable_test(iface, run=lambda a: run(a, CABLE_TEST_TIMEOUT))
    return {"data": to_jsonable(result)}


def selftest(iface: str, request: dict, run: Run) -> dict:
    mode = request.get("mode")
    if mode not in ("online", "offline"):
        raise CommandError("bad_request", "mode")
    if mode == "offline":
        confirmed(request)
    result = run(["-t", iface, mode], TIMEOUT)
    out = result.stdout
    if "The test result is" not in out:
        check(result)
    return {"data": {"passed": "The test result is PASS" in out, "output": out[:OUTPUT_MAX]}}
