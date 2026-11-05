import functools
import tempfile
import unittest
from pathlib import Path

from ethtool_gui.core import runner
from ethtool_gui.helper import operations as ops
from ethtool_gui.helper.operations import OPS, ValidationError
from tests.fakes import make_fake_ethtool, read_calls
from tests.test_parse_basic import fixture

FORCED = ("Settings for eth0:\n\tSpeed: 100Mb/s\n\tDuplex: Half\n"
          "\tAuto-negotiation: off\n\tLink detected: yes\n")


class AllowlistTest(unittest.TestCase):
    def test_exact_operation_set(self):
        self.assertEqual(set(OPS), {"link", "wol", "pause", "ring", "channels",
                                    "coalesce", "features", "eee"})

    def test_no_destructive_operations(self):
        for name in OPS:
            for bad in ("eeprom", "flash", "reset", "register"):
                self.assertNotIn(bad, name)
        flags = {op.flag for op in OPS.values()}
        self.assertEqual(flags, {"-s", "-A", "-G", "-L", "-C", "-K", "--set-eee"})

    def test_link_affecting(self):
        self.assertEqual({n for n, op in OPS.items() if op.is_link_affecting},
                         {"link", "features", "ring", "channels", "eee"})


class ValidateTest(unittest.TestCase):
    def bad(self, op, values):
        with self.assertRaises(ValidationError):
            OPS[op].validate(values)

    def test_link_forced(self):
        self.assertEqual(OPS["link"].validate({"autoneg": False, "speed": 100, "duplex": "half"}),
                         ["autoneg", "off", "speed", "100", "duplex", "half"])

    def test_link_autoneg_with_advertise(self):
        self.assertEqual(OPS["link"].validate({"autoneg": True, "advertise": "0x2F"}),
                         ["autoneg", "on", "advertise", "0x2f"])

    def test_link_invalid(self):
        self.bad("link", {})
        self.bad("link", {"speed": 100, "duplex": "full"})                    # needs autoneg off
        self.bad("link", {"autoneg": True, "speed": 1000, "duplex": "full"})
        self.bad("link", {"autoneg": False, "speed": 100})                    # duplex missing
        self.bad("link", {"autoneg": False, "speed": 123, "duplex": "full"})
        self.bad("link", {"autoneg": False, "speed": "100", "duplex": "full"})
        self.bad("link", {"autoneg": False, "speed": True, "duplex": "full"})
        self.bad("link", {"autoneg": False, "speed": 100, "duplex": "Full; reboot"})
        self.bad("link", {"autoneg": "on"})
        self.bad("link", {"autoneg": True, "advertise": "0x2f msglvl 0"})
        self.bad("link", {"autoneg": True, "advertise": "47"})
        self.bad("link", {"autoneg": False, "speed": 100, "duplex": "full", "advertise": "0x8"})
        self.bad("link", {"autoneg": True, "phyad": 1})
        self.bad("link", ["autoneg", "on"])
        self.bad("link", None)

    def test_wol(self):
        self.assertEqual(OPS["wol"].validate({"wol": "gu"}), ["wol", "gu"])
        self.assertEqual(OPS["wol"].validate({"wol": "d"}), ["wol", "d"])
        for v in ("", "x", "gg", "dg", "g s", "pumbagsdx", 5):
            self.bad("wol", {"wol": v})

    def test_pause(self):
        self.assertEqual(OPS["pause"].validate({"tx": True, "rx": False}),
                         ["rx", "off", "tx", "on"])
        self.bad("pause", {"rx": 1})

    def test_ring_and_channels(self):
        self.assertEqual(OPS["ring"].validate({"tx": 512, "rx": 1024, "rx-jumbo": 0}),
                         ["rx", "1024", "rx-jumbo", "0", "tx", "512"])
        self.assertEqual(OPS["channels"].validate({"combined": 4}), ["combined", "4"])
        self.bad("ring", {"rx": -1})
        self.bad("ring", {"rx": 1 << 30})
        self.bad("ring", {"rx": 1.5})
        self.bad("channels", {"queues": 2})

    def test_coalesce(self):
        self.assertEqual(OPS["coalesce"].validate({"rx-usecs": 3, "adaptive-rx": True}),
                         ["adaptive-rx", "on", "rx-usecs", "3"])
        self.bad("coalesce", {"rx-usecs": "3"})
        self.bad("coalesce", {"adaptive-rx": 1})
        self.bad("coalesce", {"--help": 1})

    def test_features(self):
        self.assertEqual(OPS["features"].validate({"tso": False, "gro": True}),
                         ["gro", "on", "tso", "off"])
        self.assertEqual(OPS["features"].validate({"generic-receive-offload": False}),
                         ["generic-receive-offload", "off"])
        for name in ("bogus", "-E", "tso off", "loopback\n", ""):
            self.bad("features", {name: True})
        self.bad("features", {"tso": "off"})

    def test_eee(self):
        self.assertEqual(OPS["eee"].validate({"tx-lpi": False, "eee": True}),
                         ["eee", "on", "tx-lpi", "off"])
        self.bad("eee", {"eee": True, "tx-timer": 5})

    def test_argv_rejects_bad_iface(self):
        for name in ("-E", "..", "eth0:1", "a" * 16):
            with self.assertRaises(ValidationError):
                OPS["pause"].argv(name, {"rx": True})
        self.assertEqual(OPS["pause"].argv("eth0", {"rx": True}), ["-A", "eth0", "rx", "on"])


class SnapshotRestoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def run_for(self, responses):
        script = make_fake_ethtool(self.dir, responses)
        return functools.partial(runner.run_ethtool, binary=str(script))

    def test_link_snapshot_autoneg_keeps_advertise_mask(self):
        run = self.run_for({("eth0",): (0, fixture("settings-e1000e.txt"), "")})
        saved = OPS["link"].snapshot("eth0", run, {"autoneg": False, "speed": 100, "duplex": "half"})
        self.assertEqual(saved, {"autoneg": True, "advertise": "0x2f"})

    def test_link_snapshot_forced(self):
        run = self.run_for({("eth0",): (0, FORCED, "")})
        self.assertEqual(OPS["link"].snapshot("eth0", run),
                         {"autoneg": False, "speed": 100, "duplex": "half"})

    def test_restore_autoneg_has_no_speed_or_duplex(self):
        run = self.run_for({("-s", "eth0", "autoneg", "on", "advertise", "0x2f"): (0, "", "")})
        OPS["link"].restore("eth0", {"autoneg": True, "advertise": "0x2f"}, run)
        self.assertEqual(read_calls(self.dir), [["-s", "eth0", "autoneg", "on", "advertise", "0x2f"]])

    def test_restore_forced_sets_all_three(self):
        run = self.run_for({("-s", "eth0", "autoneg", "off", "speed", "100", "duplex", "half"): (0, "", "")})
        OPS["link"].restore("eth0", {"autoneg": False, "speed": 100, "duplex": "half"}, run)
        self.assertEqual(read_calls(self.dir), [["-s", "eth0", "autoneg", "off", "speed", "100",
                                                 "duplex", "half"]])

    def test_restore_failure_raises(self):
        run = self.run_for({})
        with self.assertRaises(ops.RestoreError):
            OPS["pause"].restore("eth0", {"rx": True}, run)

    def test_restore_empty_is_noop(self):
        run = self.run_for({})
        OPS["link"].restore("eth0", {}, run)
        self.assertEqual(read_calls(self.dir), [])

    def test_features_snapshot_uses_long_names_and_refuses_fixed(self):
        run = self.run_for({("-k", "eth0"): (0, fixture("features-e1000e.txt"), "")})
        self.assertEqual(OPS["features"].snapshot("eth0", run, {"tso": False, "gro": False}),
                         {"tcp-segmentation-offload": True, "generic-receive-offload": True})
        self.assertEqual(OPS["features"].snapshot("eth0", run, {"lro": True}), {})

    def test_ring_snapshot(self):
        run = self.run_for({("-g", "eth0"): (0, fixture("ring-e1000e.txt"), "")})
        self.assertEqual(OPS["ring"].snapshot("eth0", run, {"rx": 512}), {"rx": 256})
        self.assertEqual(OPS["ring"].snapshot("eth0", run, {"rx-jumbo": 512}), {})

    def test_eee_snapshot(self):
        run = self.run_for({("--show-eee", "eth0"): (0, fixture("eee-r8169.txt"), "")})
        self.assertEqual(OPS["eee"].snapshot("eth0", run, {"eee": False}), {"eee": True})

    def test_pause_snapshot(self):
        run = self.run_for({("-a", "eth0"): (0, fixture("pause-e1000e.txt"), "")})
        self.assertEqual(OPS["pause"].snapshot("eth0", run, {"rx": False}), {"rx": True})


if __name__ == "__main__":
    unittest.main()
