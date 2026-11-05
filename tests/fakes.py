"""Test doubles."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

_SCRIPT = """#!{python}
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.log"), "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.path.join(here, "responses.json"), encoding="utf-8") as fh:
    table = json.load(fh)
for entry in table:
    if entry["argv"] == sys.argv[1:]:
        sys.stdout.write(entry["stdout"])
        sys.stderr.write(entry["stderr"])
        sys.exit(entry["code"])
sys.stderr.write("unexpected: " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(1)
"""


def make_fake_ethtool(directory: Path, responses: dict[tuple[str, ...], tuple[int, str, str]]) -> Path:
    """Write an executable ``ethtool`` that answers exactly the given argv tails.

    ``responses`` maps an argv tuple to ``(exit_code, stdout, stderr)``.
    Any other argv prints "unexpected" on stderr and exits 1.
    Every invocation is appended to ``calls.log`` (see ``read_calls``).
    """
    directory = Path(directory)
    table = [{"argv": list(argv), "code": code, "stdout": out, "stderr": err}
             for argv, (code, out, err) in responses.items()]
    (directory / "responses.json").write_text(json.dumps(table), encoding="utf-8")
    script = directory / "ethtool"
    script.write_text(_SCRIPT.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def read_calls(directory: Path, name: str = "calls.log") -> list[list[str]]:
    """Return the argv tails recorded by a fake, oldest first."""
    path = Path(directory) / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


_LOGGER = """#!{python}
import json, sys
with open({log!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit({code})
"""


def make_fake_command(directory: Path, name: str, code: int = 0) -> Path:
    """Write an executable that records its argv in ``<name>.log`` and exits with ``code``."""
    directory = Path(directory)
    script = directory / name
    log = str(directory / f"{name}.log")
    script.write_text(_LOGGER.format(python=sys.executable, log=log, code=code), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script
