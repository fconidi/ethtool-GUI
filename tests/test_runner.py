import tempfile
import unittest
from pathlib import Path

from ethtool_gui.core import runner
from ethtool_gui.core.runner import ErrorKind, classify_error
from tests.fakes import make_fake_ethtool

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


class ClassifyErrorTest(unittest.TestCase):
    def test_not_supported(self):
        self.assertEqual(classify_error("Operation not supported"), ErrorKind.NOT_SUPPORTED)
        self.assertEqual(classify_error("netlink error: Operation not supported"), ErrorKind.NOT_SUPPORTED)
        self.assertEqual(classify_error(fixture("unsupported.txt")), ErrorKind.NOT_SUPPORTED)

    def test_no_permission(self):
        self.assertEqual(classify_error("Permission denied"), ErrorKind.NO_PERMISSION)
        self.assertEqual(classify_error("Operation not permitted"), ErrorKind.NO_PERMISSION)

    def test_no_device(self):
        self.assertEqual(classify_error("No such device"), ErrorKind.NO_DEVICE)
        self.assertEqual(classify_error("Cannot get driver information: No such device"), ErrorKind.NO_DEVICE)
        self.assertEqual(classify_error(fixture("nodevice.txt")), ErrorKind.NO_DEVICE)
        self.assertEqual(classify_error("netlink error: no device matches name (offset 24)\nNo data available"),
                         ErrorKind.NO_DEVICE)

    def test_precedence(self):
        self.assertEqual(classify_error("Operation not supported\nNo such device"), ErrorKind.NO_DEVICE)
        self.assertEqual(classify_error("Operation not supported\nOperation not permitted"), ErrorKind.NO_PERMISSION)

    def test_other(self):
        self.assertEqual(classify_error("something odd"), ErrorKind.OTHER)
        self.assertEqual(classify_error(""), ErrorKind.OTHER)


class RunEthtoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_returns_result(self):
        script = make_fake_ethtool(Path(self.tmp.name), {("eth0",): (3, "out\n", "err\n")})
        res = runner.run_ethtool(["eth0"], binary=str(script))
        self.assertEqual((res.returncode, res.stdout, res.stderr), (3, "out\n", "err\n"))

    def test_missing_binary(self):
        with self.assertRaises(runner.EthtoolMissing):
            runner.run_ethtool(["eth0"], binary=str(Path(self.tmp.name) / "nope"))

    def test_timeout(self):
        script = Path(self.tmp.name) / "slow"
        script.write_text("#!/bin/sh\nsleep 5\n")
        script.chmod(0o755)
        with self.assertRaises(runner.EthtoolTimeout):
            runner.run_ethtool(["x"], binary=str(script), timeout=0.2)

    def test_exception_hierarchy(self):
        for cls in (runner.EthtoolMissing, runner.EthtoolTimeout, runner.NoSuchInterface):
            self.assertTrue(issubclass(cls, runner.EthtoolError))

    def test_locale_forced_to_c(self):
        script = Path(self.tmp.name) / "envprobe"
        script.write_text('#!/bin/sh\necho "$LC_ALL"\n')
        script.chmod(0o755)
        self.assertEqual(runner.run_ethtool([], binary=str(script)).stdout.strip(), "C")


if __name__ == "__main__":
    unittest.main()
