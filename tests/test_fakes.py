import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.fakes import make_fake_ethtool


class FakeEthtoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.script = make_fake_ethtool(Path(self.tmp.name), {
            ("eth0",): (0, "Settings for eth0:\n\tSpeed: 1000Mb/s\n", ""),
            ("-m", "eth0"): (75, "", "netlink error: Operation not supported\n"),
        })

    def run_fake(self, *args):
        return subprocess.run([str(self.script), *args], capture_output=True, text=True)

    def test_fake_ethtool_returns_scripted_output(self):
        done = self.run_fake("eth0")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "Settings for eth0:\n\tSpeed: 1000Mb/s\n")
        self.assertEqual(done.stderr, "")

    def test_exit_code_and_stderr_are_returned(self):
        done = self.run_fake("-m", "eth0")
        self.assertEqual(done.returncode, 75)
        self.assertEqual(done.stdout, "")
        self.assertIn("Operation not supported", done.stderr)

    def test_unknown_argv_exits_1_with_unexpected(self):
        done = self.run_fake("-S", "eth0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("unexpected", done.stderr)


if __name__ == "__main__":
    unittest.main()
