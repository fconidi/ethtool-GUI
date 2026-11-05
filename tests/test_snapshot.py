import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ethtool_gui.core import snapshot
from ethtool_gui.core.runner import InvalidInterfaceName, NoSuchInterface
from ethtool_gui.core.snapshot import Change
from tests.test_parse_basic import ParseBase, fixture

OK = {
    ("eno1",): "settings-e1000e.txt", ("-i", "eno1"): "driver-e1000e.txt",
    ("-a", "eno1"): "pause-e1000e.txt", ("-g", "eno1"): "ring-e1000e.txt",
    ("-l", "eno1"): "channels-e1000e.txt", ("-c", "eno1"): "coalesce-e1000e.txt",
    ("-k", "eno1"): "features-e1000e.txt", ("-S", "eno1"): "stats-e1000e.txt",
    ("-m", "eno1"): "module-sfp.txt",
}


class TakeTest(ParseBase):
    def responses(self):
        r = {k: (0, fixture(v), "") for k, v in OK.items()}
        r[("-P", "eno1")] = (0, "Permanent address: aa:bb:cc:dd:ee:ff\n", "")
        r[("--show-eee", "eno1")] = (0, fixture("eee-r8169.txt"), "")
        return r

    def test_json_and_sections(self):
        snap = snapshot.take("eno1", run=self.run_for(self.responses()))
        json.dumps(snap)
        for key in ("link", "driver", "permanent_mac", "pause", "ring", "channels", "coalesce",
                    "features", "eee", "wol", "stats", "module"):
            self.assertIn(key, snap)
        self.assertEqual((snap["iface"], snap["version"]), ("eno1", 1))
        self.assertEqual(snap["link"]["speed_mbps"], 1000)
        self.assertEqual(snap["permanent_mac"], "aa:bb:cc:dd:ee:ff")
        self.assertTrue(snap["taken_at"].endswith("Z"))

    def test_unsupported_section_stays_local(self):
        r = self.responses()
        r[("-g", "eno1")] = (95, "", "Cannot get device ring settings: Operation not supported\n")
        snap = snapshot.take("eno1", run=self.run_for(r))
        self.assertIn("unsupported", snap["ring"]["rx"])
        self.assertEqual(snap["link"]["speed_mbps"], 1000)

    def test_failing_reader_becomes_error(self):
        from ethtool_gui.core.runner import EthtoolTimeout
        with mock.patch("ethtool_gui.core.parse_settings.read_ring", side_effect=EthtoolTimeout("slow")):
            snap = snapshot.take("eno1", run=self.run_for(self.responses()))
        self.assertEqual(snap["ring"], {"error": "slow"})
        self.assertEqual(snap["link"]["speed_mbps"], 1000)

    def test_nodevice(self):
        run = self.run_for({("eno1",): (71, "", fixture("nodevice.txt"))})
        with self.assertRaises(NoSuchInterface):
            snapshot.take("eno1", run=run)

    def test_invalid_name(self):
        with self.assertRaises(InvalidInterfaceName):
            snapshot.take("-E", run=self.run_for({}))


class DiffTest(unittest.TestCase):
    def snap(self, rx, **extra):
        return {"iface": "e", "taken_at": "t", "ring": {"rx": rx, "tx": 1}, "stats": {"n": rx}, **extra}

    def test_ring(self):
        self.assertEqual(snapshot.diff(self.snap(256), self.snap(512)), [Change("ring.rx", 256, 512)])

    def test_ignores_taken_at_and_stats(self):
        a, b = self.snap(1), self.snap(1)
        b["taken_at"] = "other"
        b["stats"] = {"n": 99}
        self.assertEqual(snapshot.diff(a, b), [])
        self.assertEqual(snapshot.diff(a, b, include_stats=True), [Change("stats.n", 1, 99)])

    def test_added_removed_sorted(self):
        a = self.snap(1, zz=1)
        b = self.snap(1, aa={"x": 2})
        self.assertEqual(snapshot.diff(a, b), [Change("aa.x", None, 2), Change("zz", 1, None)])

    def test_text(self):
        self.assertEqual(snapshot.to_text([]), "no differences")
        self.assertEqual(snapshot.to_text([Change("ring.rx", 256, 512)]), "ring.rx: 256 -> 512")
        text = snapshot.to_text(self.snap(5))
        self.assertIn("[ring]", text)
        self.assertIn("rx: 5", text)
        self.assertIn("iface: e", text)


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "snaps"
        self.snap = {"iface": "eth0", "version": 1, "ring": {"rx": 1}}

    def test_round_trip_and_mode(self):
        p = snapshot.save(self.snap, self.dir)
        self.assertEqual(snapshot.load(p), self.snap)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertTrue(p.name.startswith("eth0-") and p.name.endswith("Z.json") or "Z-" in p.name)

    def test_no_overwrite(self):
        with mock.patch("ethtool_gui.core.snapshot.datetime") as dt:
            dt.now.return_value = __import__("datetime").datetime(2026, 1, 2, 3, 4, 5)
            p1 = snapshot.save(self.snap, self.dir)
            p2 = snapshot.save(self.snap, self.dir)
            p3 = snapshot.save(self.snap, self.dir)
        self.assertEqual([p.name for p in (p1, p2, p3)], [
            "eth0-20260102T030405Z.json", "eth0-20260102T030405Z-2.json", "eth0-20260102T030405Z-3.json"])

    def test_traversal_rejected(self):
        for bad in ("../x", "a/b", "-E", ""):
            with self.assertRaises(InvalidInterfaceName):
                snapshot.save({"iface": bad}, self.dir)

    def test_default_directory(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg"}):
            self.assertEqual(snapshot.default_directory(), Path("/xdg/ethtool-gui/snapshots"))
        env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(snapshot.default_directory(), Path.home() / ".local/share/ethtool-gui/snapshots")
