import unittest

from ethtool_gui.core import parse_settings as ps
from ethtool_gui.core.runner import InvalidInterfaceName, NoSuchInterface, Result
from tests.test_parse_basic import ParseBase, fixture

ARGS = {
    "pause": ("-a", ps.read_pause), "ring": ("-g", ps.read_ring),
    "channels": ("-l", ps.read_channels), "coalesce": ("-c", ps.read_coalesce),
    "features": ("-k", ps.read_features), "eee": ("--show-eee", ps.read_eee),
    "rss": ("-x", ps.read_rss), "eeprom": ("-e", ps.read_eeprom_dump),
    "regs": ("-d", ps.read_registers_dump), "phy": ("--phy-statistics", ps.read_phy_stats),
}


class Base(ParseBase):
    def read(self, flag, reader, name, iface="eno1"):
        return reader(iface, run=self.run_for({(flag, iface): (0, fixture(name), "")}))


class PauseTest(Base):
    def test_values(self):
        p = self.read("-a", ps.read_pause, "pause-e1000e.txt")
        self.assertIs(p.autoneg.value, True)
        self.assertIs(p.rx.value, True)
        self.assertIs(p.tx.value, True)


class RingTest(Base):
    def test_values(self):
        r = self.read("-g", ps.read_ring, "ring-e1000e.txt")
        self.assertEqual((r.rx.value, r.rx_max.value, r.tx.value, r.tx_max.value),
                         (256, 4096, 256, 4096))
        self.assertFalse(r.rx_mini.supported)
        self.assertFalse(r.rx_jumbo_max.supported)


class ChannelsTest(Base):
    def test_values(self):
        c = self.read("-l", ps.read_channels, "channels-e1000e.txt")
        self.assertEqual((c.combined.value, c.combined_max.value, c.other.value), (1, 1, 1))
        self.assertFalse(c.rx.supported)


class CoalesceTest(Base):
    def test_values(self):
        c = self.read("-c", ps.read_coalesce, "coalesce-e1000e.txt")
        self.assertTrue(c.supported)
        self.assertEqual(c.values, {"rx-usecs": 3})


class FeaturesTest(Base):
    def test_values(self):
        f = self.read("-k", ps.read_features, "features-e1000e.txt")
        self.assertTrue(f.supported)
        self.assertIs(f.features["tx-checksum-ipv4"].fixed, True)
        self.assertIs(f.features["tx-checksum-ipv4"].on, False)
        self.assertIs(f.features["rx-vlan-offload"].fixed, False)
        self.assertIs(f.features["rx-vlan-offload"].on, True)
        self.assertNotIn("Features for eno1", f.features)

    def test_requested(self):
        run = lambda a: Result(0, "Features for x:\nfoo: off [requested on]\n", "")
        self.assertIs(ps.read_features("x", run=run).features["foo"].requested, True)


class EeeTest(Base):
    def test_values(self):
        e = self.read("--show-eee", ps.read_eee, "eee-r8169.txt", "eth0")
        self.assertIs(e.enabled.value, True)
        self.assertIs(e.active.value, False)
        self.assertEqual(e.tx_lpi_us.value, 0)
        self.assertEqual(e.supported_modes, ["100baseT/Full", "1000baseT/Full"])
        self.assertEqual(e.advertised_modes, ["100baseT/Full", "1000baseT/Full"])
        self.assertEqual(e.partner_modes, [])

    def test_unsupported_fixture(self):
        run = lambda a: Result(1, "", fixture("eee-unsupported.txt"))
        self.assertFalse(ps.read_eee("eth0", run=run).enabled.supported)


class WolTest(Base):
    def test_values(self):
        w = ps.read_wol("eno1", run=self.run_for({("eno1",): (0, fixture("settings-e1000e.txt"), "")}))
        self.assertEqual((w.supported, w.current, w.supported_flag), ("pumbg", "g", True))

    def test_none(self):
        w = ps.read_wol("v0", run=self.run_for({("v0",): (0, fixture("settings-veth.txt"), "")}))
        self.assertFalse(w.supported_flag)


class RssTest(Base):
    def test_values(self):
        r = self.read("-x", ps.read_rss, "rss-e1000e.txt")
        self.assertEqual(r.indirection, [0] * 16)
        self.assertTrue(r.hash_key.startswith("6d:5a:56"))
        self.assertIn("toeplitz", r.raw)


class DumpTest(Base):
    def test_raw(self):
        f = self.read("-d", ps.read_registers_dump, "stats-e1000e.txt")
        self.assertEqual(f.value, fixture("stats-e1000e.txt"))


class ErrorsTest(Base):
    def test_unsupported(self):
        for name, (flag, reader) in ARGS.items():
            with self.subTest(name):
                out = reader("eno1", run=self.run_for({(flag, "eno1"): (1, "", fixture("unsupported.txt"))}))
                if name in ("eeprom", "regs", "phy"):
                    self.assertFalse(out.supported)
                    self.assertTrue(out.reason)
                elif name in ("coalesce", "features", "rss"):
                    self.assertFalse(out.supported)
                    self.assertTrue(out.reason)
                else:
                    for fld in vars(out).values():
                        if hasattr(fld, "supported"):
                            self.assertFalse(fld.supported)
                            self.assertEqual(fld.reason, "Operation not supported")

    def test_invalid_name_never_runs(self):
        def boom(args):
            raise AssertionError("runner called")
        for name, (_, reader) in ARGS.items():
            with self.subTest(name), self.assertRaises(InvalidInterfaceName):
                reader("-E", run=boom)
        with self.assertRaises(InvalidInterfaceName):
            ps.read_wol("-E", run=boom)

    def test_no_device(self):
        for flag, reader in (ARGS["ring"], ARGS["features"], ARGS["eeprom"]):
            with self.subTest(flag), self.assertRaises(NoSuchInterface):
                reader("nope", run=self.run_for({(flag, "nope"): (1, "", fixture("nodevice.txt"))}))
        with self.assertRaises(NoSuchInterface):
            ps.read_wol("nope", run=self.run_for({("nope",): (1, "", fixture("nodevice.txt"))}))


if __name__ == "__main__":
    unittest.main()
