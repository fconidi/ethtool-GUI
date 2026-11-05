"""Real ethtool and helper checks on an isolated veth interface."""

import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(*args, timeout=10, check=True):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=check)


class NetnsIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() != 0 or not shutil.which("ip") or not shutil.which("ethtool"):
            raise unittest.SkipTest("root, ip and ethtool are required")
        cache = Path(os.environ.get("XDG_CACHE_HOME", ""))
        if not cache.is_absolute() or not cache.is_relative_to(Path.home() / ".cache") or not cache.exists():
            raise unittest.SkipTest("XDG_CACHE_HOME must be an existing local cache directory")
        cls.tmp = tempfile.TemporaryDirectory(prefix="netns-", dir=cache)
        cls.state = Path(cls.tmp.name) / "state"
        cls.name = "eg" + uuid.uuid4().hex[:8]
        cls.unit_prefix = "ethtool-gui-revert-"
        try:
            run("ip", "netns", "add", cls.name)
            run("ip", "-n", cls.name, "link", "add", "dummy0", "type", "dummy")
            run("ip", "-n", cls.name, "link", "add", "veth0", "type", "veth", "peer", "name", "veth1")
            run("ip", "netns", "exec", cls.name, "ethtool", "-K", "veth0", "gro", "on")
            run("ip", "netns", "exec", cls.name, "ethtool", "-K", "veth0", "gro", "off")
            cls.wrapper = Path(cls.tmp.name) / "restore"
            cls.wrapper.write_text(
                "#!/usr/bin/python3\nimport os, sys\nfrom pathlib import Path\n"
                f"os.setns(os.open('/run/netns/{cls.name}', os.O_RDONLY), 0)\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "from ethtool_gui.helper import restore\n"
                f"restore.STATE_DIR = Path({str(cls.state)!r})\n"
                "sys.exit(restore.main())\n"
            )
            cls.wrapper.chmod(0o700)
        except (OSError, subprocess.CalledProcessError) as exc:
            run("ip", "netns", "del", cls.name, check=False)
            cls.tmp.cleanup()
            raise unittest.SkipTest(f"network namespace/veth or writable GRO unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        run("ip", "netns", "del", cls.name, check=False)
        cls.tmp.cleanup()

    def setUp(self):
        run("ip", "netns", "exec", self.name, "ethtool", "-K", "veth0", "gro", "off")

    def _gro(self):
        output = run("ip", "netns", "exec", self.name, "ethtool", "-k", "veth0").stdout
        match = re.search(r"^generic-receive-offload: (on|off)", output, re.M)
        self.assertIsNotNone(match)
        return match.group(1) == "on"

    def _helper(self):
        code = (
            "import sys; from pathlib import Path; "
            "from ethtool_gui.helper import daemon, revert; "
            f"daemon.STATE_DIR=Path({str(self.state)!r}); "
            "daemon.REVERT_SECONDS=3; "
            f"revert.RESTORE_BIN={str(self.wrapper)!r}; "
            "sys.exit(daemon.main(sys.stdin.buffer, sys.stdout.buffer))"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen(["ip", "netns", "exec", self.name, sys.executable, "-c", code],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stderr.close)
        return proc

    def _apply(self, proc):
        proc.stdin.write(b'{"id":1,"cmd":"apply","iface":"veth0","op":"features","values":{"gro":true}}\n')
        proc.stdin.flush()
        ready, _, _ = select.select([proc.stdout], [], [], 8)
        self.assertTrue(ready, "helper did not reply")
        reply = json.loads(proc.stdout.readline())
        self.assertTrue(reply.get("ok"), reply)
        self.assertTrue(self._gro())
        return reply

    def _await_restore(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not self._gro():
                return
            time.sleep(0.2)
        self.fail("GRO was not restored")

    def test_real_readers_on_dummy_and_veth(self):
        code = (
            "import json; from ethtool_gui.core.parse_basic import read_link, read_driver; "
            "from ethtool_gui.core.stats import read_stats; "
            "print(json.dumps({'driver':read_driver('veth0').driver.value, "
            "'stats':read_stats('veth0').supported, 'link':read_link('veth0').state.value}))"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(["ip", "netns", "exec", self.name, sys.executable, "-c", code],
                                capture_output=True, text=True, env=env, timeout=10, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["driver"], "veth")
        self.assertTrue(data["stats"])
        self.assertIn(data["link"], ("up", "down"))

    def test_countdown_restores_feature(self):
        proc = self._helper()
        try:
            self._apply(proc)
            self._await_restore()
        finally:
            proc.stdin.close()
            proc.wait(timeout=8)

    def test_client_close_restores_feature(self):
        proc = self._helper()
        self._apply(proc)
        proc.stdin.close()
        proc.wait(timeout=8)
        self.assertFalse(self._gro())

    def test_systemd_watchdog_restores_after_helper_kill(self):
        if not shutil.which("systemd-run") or not shutil.which("systemctl"):
            self.skipTest("systemd transient timers are unavailable")
        proc = self._helper()
        reply = self._apply(proc)
        if reply.get("watchdog") != "systemd":
            proc.stdin.close()
            proc.wait(timeout=8)
            self.skipTest("systemd did not arm the restore timer")
        unit = f"{self.unit_prefix}{reply['token']}"
        self.addCleanup(run, "systemctl", "stop", f"{unit}.timer", check=False)
        self.addCleanup(run, "systemctl", "reset-failed", f"{unit}.service", check=False)
        proc.kill()
        proc.wait(timeout=5)
        proc.stdin.close()
        self._await_restore()


if __name__ == "__main__":
    unittest.main()
