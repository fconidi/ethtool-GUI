"""``ethtool-gui-restore <token>``: undo a change whose helper is gone.

Started as root by the transient systemd timer the helper arms before a link
change. It takes exactly one argument, the token, and only acts on a state file
that the helper wrote. Exit codes: 0 restored (or already handled), 1 ethtool
failed, 2 refused (bad or unknown token, untrustworthy state file).
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.runner import EthtoolError, run_ethtool
from . import revert
from .operations import OPS, RestoreError, ValidationError

STATE_DIR = Path("/run/ethtool-gui")
ETHTOOL_BIN = "/usr/sbin/ethtool"
TIMEOUT = 5.0


def _fail(message: str, code: int) -> int:
    print(f"ethtool-gui-restore: {message}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not revert.valid_token(argv[0]):
        return _fail("expected one token", 2)
    token = argv[0]
    try:
        state = revert.read_state(STATE_DIR, token)
    except revert.StateError as exc:
        return _fail(str(exc), 2)
    if state is None:
        return _fail("unknown token", 2)
    op = OPS.get(state["op"])
    if op is None:
        return _fail("unknown operation", 2)
    try:
        op.argv(state["iface"], state["saved"])  # validate before touching anything
    except ValidationError as exc:
        return _fail(str(exc), 2)
    if not revert.claim(STATE_DIR, token):
        return 0  # the helper handled it in the meantime
    run = lambda args: run_ethtool(args, binary=ETHTOOL_BIN, timeout=TIMEOUT)  # noqa: E731
    try:
        op.restore(state["iface"], state["saved"], run)
    except (RestoreError, EthtoolError) as exc:
        return _fail(str(exc), 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
