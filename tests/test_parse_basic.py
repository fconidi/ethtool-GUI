import functools
import tempfile
import unittest
from pathlib import Path

from ethtool_gui.core import parse_basic, runner
from tests.fakes import make_fake_ethtool

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


class ParseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_for(self, responses):
        script = make_fake_ethtool(Path(self.tmp.name), responses)
        return functools.partial(runner.run_ethtool, binary=str(script))


class ReadLinkTest(ParseBase):
    def link(self, name, iface="eth0"):
        return parse_basic.read_link(iface, run=self.run_for({(iface,): (0, fixture(name), "")}))

    def test_e1000e(self):
        li = self.link("settings-e1000e.txt", "eno1")
        self.assertEqual(li.state.value, "up")
        self.assertEqual(li.speed_mbps.value, 1000)
        self.assertEqual(li.duplex.value, "Full")
        self.assertIs(li.autoneg.value, True)
        self.assertIn("1000baseT/Full", li.supported_modes.value)
        self.assertEqual(len(li.supported_modes.value), 5)
        self.assertIn("1000baseT/Half", li.partner_modes.value)
        self.assertEqual(li.port.value, "Twisted Pair")
        self.assertEqual(li.transceiver.value, "internal")
        self.assertEqual(li.mdix.value, "on (auto)")
        self.assertEqual(li.wol_supported.value, "pumbg")
        self.assertEqual(li.wol_current.value, "g")

    def test_r8169_linkdown(self):
        li = self.link("settings-r8169-linkdown.txt")
        self.assertEqual(li.state.value, "down")
        self.assertIsNone(li.speed_mbps.value)
        self.assertIsNone(li.duplex.value)
        self.assertIs(li.autoneg.value, True)
        self.assertEqual(li.wol_supported.value, "pumbg")
        self.assertEqual(li.wol_current.value, "d")
        self.assertEqual(li.partner_modes.value, [])
        self.assertTrue(li.speed_mbps.supported)
        self.assertEqual(li.supported_modes.value[-1], "1000baseT/Full")

    def test_veth(self):
        li = self.link("settings-veth.txt")
        self.assertEqual(li.speed_mbps.value, 10000)
        self.assertIs(li.autoneg.value, False)
        self.assertEqual(li.supported_modes.value, [])
        self.assertEqual(li.advertised_modes.value, [])
        self.assertEqual(li.state.value, "up")
        self.assertIsNone(li.wol_supported.value)

    def test_bridge(self):
        li = self.link("settings-bridge.txt", "vmbr0")
        self.assertIsNone(li.duplex.value)
        self.assertEqual(li.port.value, "Other")
        self.assertIsNone(li.mdix.value)

    def test_unsupported(self):
        li = parse_basic.read_link("eth0", run=self.run_for({("eth0",): (75, "", fixture("unsupported.txt"))}))
        self.assertFalse(li.state.supported)
        self.assertFalse(li.speed_mbps.supported)
        self.assertEqual(li.speed_mbps.reason, "Operation not supported")

    def test_no_permission_does_not_raise(self):
        li = parse_basic.read_link("eth0", run=self.run_for({("eth0",): (1, "", "Operation not permitted\n")}))
        self.assertFalse(li.duplex.supported)

    def test_nodevice(self):
        run = self.run_for({("eth0",): (71, "", fixture("nodevice.txt"))})
        with self.assertRaises(runner.NoSuchInterface):
            parse_basic.read_link("eth0", run=run)

    def test_missing_binary(self):
        run = functools.partial(runner.run_ethtool, binary=str(Path(self.tmp.name) / "none"))
        with self.assertRaises(runner.EthtoolMissing):
            parse_basic.read_link("eth0", run=run)


class InvalidNameTest(unittest.TestCase):
    def test_nothing_is_run(self):
        calls = []

        def run(args):
            calls.append(args)
            raise AssertionError("must not run")

        for fn in (parse_basic.read_link, parse_basic.read_driver, parse_basic.read_permanent_mac):
            for name in ("-E", "../x"):
                with self.assertRaises(runner.InvalidInterfaceName):
                    fn(name, run=run)
        self.assertEqual(calls, [])
        self.assertTrue(issubclass(runner.InvalidInterfaceName, runner.EthtoolError))


class ReadDriverTest(ParseBase):
    def test_e1000e(self):
        run = self.run_for({("-i", "eno1"): (0, fixture("driver-e1000e.txt"), "")})
        d = parse_basic.read_driver("eno1", run=run)
        self.assertEqual(d.driver.value, "e1000e")
        self.assertEqual(d.bus_info.value, "0000:00:1f.6")
        self.assertEqual(d.firmware_version.value, "0.1-4")
        self.assertTrue(d.supports_test)
        self.assertTrue(d.supports_eeprom)
        self.assertTrue(d.supports_register_dump)
        self.assertTrue(d.supports_statistics)

    def test_r8169(self):
        run = self.run_for({("-i", "eth0"): (0, fixture("driver-r8169.txt"), "")})
        d = parse_basic.read_driver("eth0", run=run)
        self.assertEqual(d.driver.value, "r8169")
        self.assertFalse(d.supports_test)
        self.assertFalse(d.supports_eeprom)
        self.assertTrue(d.supports_register_dump)

    def test_nodevice(self):
        run = self.run_for({("-i", "eth0"): (71, "", fixture("nodevice.txt"))})
        with self.assertRaises(runner.NoSuchInterface):
            parse_basic.read_driver("eth0", run=run)

    def test_unsupported(self):
        run = self.run_for({("-i", "eth0"): (75, "", fixture("unsupported.txt"))})
        d = parse_basic.read_driver("eth0", run=run)
        self.assertFalse(d.driver.supported)
        self.assertEqual(d.driver.reason, "Operation not supported")
        self.assertFalse(d.supports_test)


class ReadPermanentMacTest(ParseBase):
    def test_ok(self):
        run = self.run_for({("-P", "eth0"): (0, "Permanent address: aa:bb:cc:dd:ee:ff\n", "")})
        self.assertEqual(parse_basic.read_permanent_mac("eth0", run=run).value, "aa:bb:cc:dd:ee:ff")

    def test_unsupported(self):
        run = self.run_for({("-P", "eth0"): (75, "", "Cannot read permanent address: Operation not supported\n")})
        f = parse_basic.read_permanent_mac("eth0", run=run)
        self.assertFalse(f.supported)
        self.assertIsNotNone(f.reason)


if __name__ == "__main__":
    unittest.main()
