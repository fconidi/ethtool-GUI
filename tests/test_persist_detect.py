import tempfile
import unittest
from pathlib import Path

from ethtool_gui.core.runner import EthtoolMissing
from ethtool_gui.persist import detect, matrix, request
from ethtool_gui.persist.common import PersistError
from tests.fakes import FakeRunner

UUID = "5f3c2a1e-1111-4222-8333-944455556666"
UUID_ARGV = ["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "eth0"]


def answer(table):
    def respond(argv):
        value = table.get(tuple(argv), (99, "", "unexpected\n"))
        if isinstance(value, Exception):
            raise value
        return value
    return FakeRunner(respond)


class BackendTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.link_dir = Path(tmp.name)

    def test_nm_when_the_device_has_a_profile(self):
        run = answer({tuple(UUID_ARGV): (0, UUID + "\n", "")})
        self.assertIs(detect.backend_for("eth0", run, self.link_dir), detect.Backend.NM)
        self.assertEqual(run.calls, [UUID_ARGV])

    def test_networkd_without_profile(self):
        for reply in ((0, "\n", ""), (10, "", "Error: Device 'eth0' not found.\n"),
                      EthtoolMissing("nmcli: command not found")):
            run = answer({tuple(UUID_ARGV): reply})
            self.assertIs(detect.backend_for("eth0", run, self.link_dir), detect.Backend.NETWORKD)

    def test_none_without_link_directory(self):
        run = answer({tuple(UUID_ARGV): (0, "", "")})
        self.assertIs(detect.backend_for("eth0", run, self.link_dir / "missing"), detect.Backend.NONE)

    def test_garbage_profile_id_fails_closed(self):
        run = answer({tuple(UUID_ARGV): (0, "eth0; rm -rf /\n", "")})
        with self.assertRaises(PersistError) as ctx:
            detect.backend_for("eth0", run, self.link_dir)
        self.assertEqual(ctx.exception.code, "cannot_verify")

    def test_bad_name_never_reaches_nmcli(self):
        run = answer({})
        for bad in ("-E", "..", "a:b", ""):
            with self.assertRaises(PersistError):
                detect.backend_for(bad, run, self.link_dir)
        self.assertEqual(run.calls, [])


class NmSupportsTest(unittest.TestCase):
    def run_for(self, version):
        return answer({("nmcli", "--version"): (0, f"nmcli tool, version {version}\n", "")})

    def test_versions(self):
        new, old = self.run_for("1.52.0"), self.run_for("1.20.4")
        for prop in ("802-3-ethernet.speed", "ethtool.feature-tso", "ethtool.pause-rx",
                     "ethtool.ring-rx", "ethtool.coalesce-rx-usecs"):
            self.assertTrue(detect.nm_supports(prop, new), prop)
        self.assertTrue(detect.nm_supports("ethtool.feature-tso", old))
        self.assertTrue(detect.nm_supports("802-3-ethernet.wake-on-lan", old))
        for prop in ("ethtool.pause-rx", "ethtool.ring-rx", "ethtool.coalesce-rx-usecs",
                     "ethtool.feature-rx-gro-list"):
            self.assertFalse(detect.nm_supports(prop, old), prop)

    def test_unknown_property_or_version(self):
        self.assertFalse(detect.nm_supports("ethtool.channels-rx", self.run_for("1.52.0")))
        self.assertFalse(detect.nm_supports("connection.id", self.run_for("1.52.0")))
        self.assertFalse(detect.nm_supports("ethtool.feature-tso", self.run_for("x")))
        missing = answer({("nmcli", "--version"): EthtoolMissing("no nmcli")})
        self.assertFalse(detect.nm_supports("802-3-ethernet.speed", missing))


class MatrixTest(unittest.TestCase):
    def test_every_field_has_both_backends(self):
        for field in matrix.FIELDS:
            for backend in (matrix.NM, matrix.NETWORKD):
                self.assertIn((field, backend), matrix.SUPPORT)

    def test_rules(self):
        support = matrix.SUPPORT
        for field in ("link.autoneg", "link.speed", "link.duplex", "wol", "pause.rx",
                      "ring.rx", "coalesce.rx-usecs", "features.tcp-segmentation-offload"):
            self.assertTrue(support[(field, matrix.NM)], field)
        for field in ("channels.combined", "eee", "eee.tx-lpi", "link.advertise"):
            self.assertFalse(support[(field, matrix.NM)], field)
        for field in ("link.autoneg", "link.speed", "link.duplex", "wol", "ring.rx", "ring.tx"):
            self.assertTrue(support[(field, matrix.NETWORKD)], field)
        for field in ("ring.rx-mini", "pause.rx", "features.generic-receive-offload",
                      "coalesce.rx-usecs", "channels.rx", "eee", "link.advertise"):
            self.assertFalse(support[(field, matrix.NETWORKD)], field)

    def test_merge_replaces_link_and_wol(self):
        old = {"link": {"autoneg": False, "speed": 100, "duplex": "full"}, "ring": {"rx": 1}}
        new = {"link": {"autoneg": True}, "ring": {"tx": 2}}
        self.assertEqual(matrix.merge(old, new),
                         {"link": {"autoneg": True}, "ring": {"rx": 1, "tx": 2}})


class ValuesTest(unittest.TestCase):
    def test_reuses_helper_validators(self):
        for bad in ({"link": {"autoneg": False, "speed": 123, "duplex": "full"}},
                    {"wol": {"wol": "x"}}, {"ring": {"rx": -1}}, {"ring": {"rx": True}},
                    {"unknown": {}}, {"features": {"tso": "on"}}, {}, [], None,
                    {"link": {"autoneg": False, "speed": "1000; reboot", "duplex": "full"}}):
            with self.assertRaises(PersistError) as ctx:
                request.validate_values(bad)
            self.assertEqual(ctx.exception.code, "bad_values")

    def test_feature_names_become_long_names(self):
        self.assertEqual(request.validate_values({"features": {"tso": True}}),
                         {"features": {"tcp-segmentation-offload": True}})

    def test_mac(self):
        self.assertEqual(request.validate_mac("AA:bb:cc:00:11:22"), "aa:bb:cc:00:11:22")
        self.assertEqual(request.validate_mac("00:00:00:00:00:00"), "")
        self.assertEqual(request.validate_mac(None), "")
        for bad in ("aa:bb", "aa:bb:cc:dd:ee:ff\n", "x" * 17, 5):
            with self.assertRaises(PersistError):
                request.validate_mac(bad)


if __name__ == "__main__":
    unittest.main()
