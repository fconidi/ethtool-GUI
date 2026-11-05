"""Test doubles."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

_SCRIPT = """#!{python}
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
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
    """
    directory = Path(directory)
    table = [{"argv": list(argv), "code": code, "stdout": out, "stderr": err}
             for argv, (code, out, err) in responses.items()]
    (directory / "responses.json").write_text(json.dumps(table), encoding="utf-8")
    script = directory / "ethtool"
    script.write_text(_SCRIPT.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script
