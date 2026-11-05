import unittest

from ethtool_gui.core import module
from ethtool_gui.core.module import Level, Reading
from ethtool_gui.core.runner import InvalidInterfaceName, NoSuchInterface
from tests.test_parse_basic import ParseBase, fixture


class ReadModuleTest(ParseBase):
    def setUp(self):
        super().setUp()
        run = self.run_for({("-m", "eth0"): (0, fixture("module-sfp.txt"), "")})
        self.m = module.read_module("eth0", run=run)
        self.by_name = {r.name: r for r in self.m.readings}

    def test_identity(self):
        self.assertTrue(self.m.supported)
        self.assertEqual(self.m.identifier, "0x03 (SFP)")
        self.assertEqual(self.m.vendor, "FS")
        self.assertEqual(self.m.part, "SFP-GE-LX")

    def test_readings(self):
        self.assertEqual(len(self.m.readings), 5)
        b = self.by_name["Laser bias current"]
        self.assertEqual((b.value, b.unit), (18.402, "mA"))
        tx = self.by_name["Laser output power"]
        self.assertEqual((tx.value, tx.unit), (-7.30, "dBm"))
        rx = self.by_name["Receiver signal average optical power"]
        self.assertEqual((rx.value, rx.unit), (-9.20, "dBm"))
        t = self.by_name["Module temperature"]
        self.assertEqual((t.value, t.unit), (38.5, "C"))
        v = self.by_name["Module voltage"]
        self.assertEqual((v.value, v.unit), (3.318, "V"))

    def test_thresholds(self):
        rx = self.by_name["Receiver signal average optical power"]
        self.assertEqual((rx.alarm_high, rx.alarm_low, rx.warn_high, rx.warn_low), (-1.0, -21.0, -2.0, -20.0))
        t = self.by_name["Module temperature"]
        self.assertEqual((t.alarm_high, t.alarm_low, t.warn_high, t.warn_low), (95.0, -45.0, 85.0, -40.0))
        self.assertEqual(self.by_name["Laser bias current"].warn_high, 60.0)

    def test_levels_ok_for_fixture(self):
        self.assertTrue(all(module.level(r) is Level.OK for r in self.m.readings))

    def test_alarm_flags(self):
        self.assertIs(self.m.alarm_flags["Laser bias current high alarm"], False)
        self.assertIn("Laser rx power low warning", self.m.alarm_flags)
        self.assertNotIn("Alarm/warning flags implemented", self.m.alarm_flags)

    def test_alarm_flag_on(self):
        text = fixture("module-sfp.txt").replace("Laser bias current high alarm             : Off",
                                                  "Laser bias current high alarm             : On")
        m = module.parse_module(text)
        self.assertIs(m.alarm_flags["Laser bias current high alarm"], True)

    def test_mw_only_power(self):
        m = module.parse_module("\tLaser output power : 0.5000 mW\n")
        self.assertEqual((m.readings[0].value, m.readings[0].unit), (0.5, "mW"))

    def test_unsupported(self):
        run = self.run_for({("-m", "eth0"): (95, "", fixture("module-unsupported-netlink.txt"))})
        m = module.read_module("eth0", run=run)
        self.assertFalse(m.supported)
        self.assertEqual(m.reason, "Operation not supported")
        self.assertEqual(m.readings, [])

    def test_invalid_name_does_not_run(self):
        def boom(args):
            raise AssertionError("runner called")
        with self.assertRaises(InvalidInterfaceName):
            module.read_module("-E", run=boom)

    def test_nodevice(self):
        run = self.run_for({("-m", "eth9"): (19, "", fixture("nodevice.txt"))})
        with self.assertRaises(NoSuchInterface):
            module.read_module("eth9", run=run)


class LevelTest(unittest.TestCase):
    def r(self, value, **kw):
        return Reading("x", value, "dBm", **kw)

    def test_boundaries(self):
        kw = dict(warn_low=-20.0, alarm_low=-25.0, warn_high=-2.0, alarm_high=-1.0)
        self.assertIs(module.level(self.r(-10, **kw)), Level.OK)
        self.assertIs(module.level(self.r(-20, **kw)), Level.OK)
        self.assertIs(module.level(self.r(-2, **kw)), Level.OK)
        self.assertIs(module.level(self.r(-20.5, **kw)), Level.WARN)
        self.assertIs(module.level(self.r(-1.5, **kw)), Level.WARN)
        self.assertIs(module.level(self.r(-25, **kw)), Level.WARN)
        self.assertIs(module.level(self.r(-25.1, **kw)), Level.ALARM)
        self.assertIs(module.level(self.r(-0.5, **kw)), Level.ALARM)

    def test_no_thresholds(self):
        self.assertIs(module.level(self.r(999)), Level.OK)

    def test_partial_thresholds(self):
        self.assertIs(module.level(self.r(5, warn_high=4.0)), Level.WARN)
        self.assertIs(module.level(self.r(-99, warn_high=4.0)), Level.OK)


class EdgeCaseTest(unittest.TestCase):
    def test_no_light_is_minus_inf(self):
        m = module.parse_module("\tReceiver signal average optical power : 0.0000 mW / -inf dBm\n"
                                "\tLaser rx power low alarm threshold : 0.0050 mW / -23.01 dBm\n")
        r = m.readings[0]
        self.assertEqual((r.value, r.unit), (float("-inf"), "dBm"))
        self.assertIs(module.level(r), Level.ALARM)

    def test_minus_inf_alarm_and_no_thresholds(self):
        inf = float("-inf")
        self.assertIs(module.level(Reading("x", inf, "dBm", alarm_low=-23.0, threshold_unit="dBm")), Level.ALARM)
        self.assertIs(module.level(Reading("x", inf, "dBm", warn_low=-20.0, threshold_unit="dBm")), Level.WARN)
        self.assertIs(module.level(Reading("x", inf, "dBm")), Level.OK)

    def test_unit_mismatch_is_ok(self):
        m = module.parse_module("\tLaser output power : 0.0000 mW\n"
                                "\tLaser output power low alarm threshold : 0.0501 mW / -13.00 dBm\n")
        self.assertEqual(m.readings[0].unit, "mW")
        self.assertIs(module.level(m.readings[0]), Level.OK)

    def test_no_diagnostics(self):
        m = module.parse_module("\tIdentifier : 0x03 (SFP)\n\tVendor name : FS\n\tVendor PN : X\n"
                                "\tOptical diagnostics support : No\n")
        self.assertEqual(m.readings, [])
        self.assertEqual((m.vendor, m.part), ("FS", "X"))

    def test_qsfp_channel_lines(self):
        m = module.parse_module("\tIdentifier : 0x11 (QSFP28)\n"
                                "\tLaser output power (Channel 1) : 0.5 mW / -3.01 dBm\n")
        self.assertEqual(m.readings, [])

    def test_garbage(self):
        m = module.parse_module("\x00 garbage\n::\n\t:\n\tfoo\n")
        self.assertEqual(m.readings, [])


if __name__ == "__main__":
    unittest.main()
