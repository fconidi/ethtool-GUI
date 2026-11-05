import unittest

from ethtool_gui.core.diagnosis import Hint, diagnose
from ethtool_gui.core.models import Field, LinkInfo, Pause
from ethtool_gui.core.module import ModuleInfo, Reading
from ethtool_gui.core import parse_basic
from tests.test_parse_basic import ParseBase, fixture

RX = "Receiver signal average optical power"


def link(**kw):
    base = dict(state="up", speed_mbps=1000, duplex="Full", autoneg=True,
                supported_modes=["10baseT/Full", "1000baseT/Full"])
    base.update(kw)
    return LinkInfo(**{k: Field(v) for k, v in base.items()})


def keys(hints):
    return [h.key for h in hints]


class DiagnoseTest(ParseBase):
    def test_healthy_fixture_empty(self):
        run = self.run_for({("eno1",): (0, fixture("settings-e1000e.txt"), "")})
        li = parse_basic.read_link("eno1", run=run)
        self.assertEqual(diagnose(li, {}, None, None), [])

    def test_low_speed(self):
        self.assertEqual(diagnose(link(speed_mbps=100), {}, None, None), [Hint("diag.low_speed", "warn")])

    def test_low_speed_needs_gigabit_support_and_speed(self):
        self.assertEqual(diagnose(link(speed_mbps=100, supported_modes=["100baseT/Full"]), {}, None, None), [])
        self.assertEqual(diagnose(link(speed_mbps=None), {}, None, None), [])

    def test_duplex_mismatch(self):
        self.assertEqual(keys(diagnose(link(duplex="Half"), {}, None, None)), ["diag.duplex_mismatch"])
        self.assertEqual(diagnose(link(duplex="Half", autoneg=False), {}, None, None), [])

    def test_crc(self):
        h = diagnose(link(), {"rx_crc_errors": 0.5}, None, None)
        self.assertEqual(h, [Hint("diag.crc_errors", "warn")])
        self.assertEqual(diagnose(link(), {"rx_crc_errors": 0.0}, None, None), [])

    def test_collisions(self):
        self.assertEqual(keys(diagnose(link(), {"collisions": 2.0}, None, None)), ["diag.collisions"])

    def test_pause(self):
        self.assertEqual(diagnose(link(), {"rx_pause": 0.5}, None, None), [])
        h = diagnose(link(), {"rx_pause": 2.0}, None, Pause())
        self.assertEqual(h, [Hint("diag.pause_active", "info")])

    def test_link_down(self):
        self.assertEqual(diagnose(link(state="down", speed_mbps=None), {}, None, None),
                         [Hint("diag.link_down", "info")])

    def test_sfp_levels(self):
        def mod(v):
            return ModuleInfo(readings=[Reading(RX, v, "dBm", -20.0, -21.0, -2.0, -1.0, "dBm")])
        self.assertEqual(diagnose(None, {}, mod(-9.0), None), [])
        self.assertEqual(diagnose(None, {}, mod(-20.5), None), [Hint("diag.sfp_rx_low", "warn")])
        self.assertEqual(diagnose(None, {}, mod(-30.0), None), [Hint("diag.sfp_rx_low", "alarm")])
        self.assertEqual(diagnose(None, {}, ModuleInfo(supported=False), None), [])

    def test_ordering(self):
        h = diagnose(link(speed_mbps=100), {"rx_pause": 5.0, "rx_crc_errors": 1.0}, None, None)
        self.assertEqual(keys(h), ["diag.crc_errors", "diag.low_speed", "diag.pause_active"])

    def test_all_none(self):
        self.assertEqual(diagnose(None, {}, None, None), [])
        self.assertEqual(diagnose(LinkInfo(), {}, None, None), [])
        self.assertEqual(diagnose(LinkInfo.unsupported("x"), {}, None, None), [])
