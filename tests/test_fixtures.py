import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

NAMES = [
    "settings-r8169-linkdown.txt", "driver-r8169.txt", "eee-r8169.txt",
    "settings-veth.txt", "eee-unsupported.txt", "settings-bridge.txt",
    "settings-e1000e.txt", "driver-e1000e.txt", "module-unsupported-netlink.txt",
    "nodevice.txt", "unsupported.txt", "stats-e1000e.txt", "pause-e1000e.txt",
    "ring-e1000e.txt", "channels-e1000e.txt", "coalesce-e1000e.txt",
    "features-e1000e.txt", "module-sfp.txt", "cable-test.txt", "rss-e1000e.txt",
    "udevadm-net-setup-link-ifnames0.stdout.txt",
    "udevadm-net-setup-link-ifnames0.stderr.txt", "default-link.txt",
]


class FixturesTest(unittest.TestCase):
    def test_every_fixture_exists_and_is_pure_output(self):
        for name in NAMES:
            with self.subTest(name=name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                self.assertTrue(text.strip())
                for line in text.splitlines():
                    self.assertFalse(line.startswith("#"), line)

    def test_readme_lists_each_fixture_as_real_or_synthetic(self):
        entries = {}
        for line in (FIXTURES / "README.txt").read_text(encoding="utf-8").splitlines()[1:]:
            name, kind, _source = line.split(": ", 2)
            entries[name] = kind
        for name in NAMES:
            with self.subTest(name=name):
                self.assertIn(entries.get(name), ("real", "synthetic"))

    def test_tabs_are_preserved(self):
        self.assertIn("\tSupported ports: [ TP\t MII ]", (FIXTURES / "settings-r8169-linkdown.txt").read_text())


if __name__ == "__main__":
    unittest.main()
