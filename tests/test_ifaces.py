import tempfile
import unittest
from pathlib import Path

from ethtool_gui.core.ifaces import list_interfaces, valid_name


class ValidNameTest(unittest.TestCase):
    def test_accepts(self):
        for name in ("eth0", "enp3s0f1", "wlan0.100", "a", "_x", "-x", "a" * 15):
            self.assertTrue(valid_name(name), name)

    def test_rejects(self):
        for name in (".", "..", "../x", "eth0:1", ".hidden", "a" * 16, "", "a/b", "a b", "eth0\n"):
            self.assertFalse(valid_name(name), repr(name))


class ListInterfacesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, phys, state, mac in (("eno1", True, "up", "aa:bb:cc:dd:ee:01"),
                                       ("lo", False, "unknown", "00:00:00:00:00:00"),
                                       ("veth1", False, "up", "aa:bb:cc:dd:ee:02")):
            d = self.root / name
            d.mkdir()
            (d / "operstate").write_text(state + "\n")
            (d / "address").write_text(mac + "\n")
            if phys:
                (d / "device").mkdir()
        (self.root / "bonding_masters").write_text("")

    def test_physical_only_by_default(self):
        got = list_interfaces(self.root)
        self.assertEqual([i.name for i in got], ["eno1"])
        self.assertTrue(got[0].is_physical)
        self.assertEqual(got[0].operstate, "up")
        self.assertEqual(got[0].mac, "aa:bb:cc:dd:ee:01")

    def test_include_virtual(self):
        got = list_interfaces(self.root, include_virtual=True)
        self.assertEqual([i.name for i in got], ["eno1", "lo", "veth1"])
        self.assertFalse(got[1].is_physical)

    def test_missing_root(self):
        self.assertEqual(list_interfaces(self.root / "none"), [])


if __name__ == "__main__":
    unittest.main()
