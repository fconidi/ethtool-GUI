"""Entry point of ``ethtool-gui-helper``, started through pkexec."""

import signal
import sys

from . import daemon


def _exit(signum, frame):
    raise SystemExit(128 + signum)  # unwinds through Daemon.run, which restores first


for _sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, _exit)

sys.exit(daemon.main(sys.stdin.buffer, sys.stdout.buffer, sys.argv[1:]))
