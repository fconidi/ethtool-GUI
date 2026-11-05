import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ethtool_gui.persist import common, networkd
from ethtool_gui.persist.common import Env, PersistConflict, PersistError
from tests.fakes import FakeRunner

MAC = "aa:bb:cc:00:11:22"
DEFAULT = """# default
[Match]
OriginalName=*

[Link]
NamePolicy=keep kernel database onboard slot path
AlternativeNamesPolicy=database onboard slot path mac
MACAddressPolicy=persistent
"""
FORCED = {"link": {"autoneg": False, "speed": 1000, "duplex": "full"}, "wol": {"wol": "g"},
          "ring": {"rx": 512, "tx": 1024}}
LOG = """Parsed configuration file "{path}"
eth0: Config file {path} is applied
eth0: Policy *keep*: keeping existing userspace name
ID_NET_LINK_FILE={path}
"""


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.link_dir = root / "network"
        self.link_dir.mkdir(mode=0o755)
        (root / "lib").mkdir(mode=0o755)
        self.default = root / "lib" / "99-default.link"
        self.default.write_text(DEFAULT, encoding="utf-8")
        self.sys_root = root / "sys"
        (self.sys_root / "eth0").mkdir(parents=True)
        self.ours = self.link_dir / "10-ethtool-gui-eth0.link"
        self.after = None  # override: text printed once our file exists
        self.before = None
        self.run = FakeRunner(self.respond)

    def respond(self, argv):
        if argv != ["udevadm", "test-builtin", "net_setup_link", str(self.sys_root / "eth0")]:
            return 99, "", "unexpected\n"
        if self.ours.exists():
            text = self.after if self.after is not None else LOG.format(path=self.ours)
        else:
            text = self.before if self.before is not None else LOG.format(path=self.default)
        return 0, "\n".join(l for l in text.splitlines() if l.startswith("ID_")), text

    def env(self):
        return Env(run=self.run, link_dir=self.link_dir, sys_root=self.sys_root,
                   default_links=(str(self.default),))

    def persist(self, values=FORCED, mac=MAC, entry=None):
        return networkd.persist("eth0", values, self.env(), mac, entry)

    def refused(self, code, reason=None, **kw):
        with self.assertRaises(PersistError) as ctx:
            self.persist(**kw)
        self.assertEqual(ctx.exception.code, code)
        if reason:
            self.assertEqual(ctx.exception.reason, reason)
        return ctx.exception


class WriteTest(Base):
    def test_file_content(self):
        out = self.persist()
        text = self.ours.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(lines[0], "# ethtool-gui managed")
        self.assertIn("[Match]", lines)
        self.assertIn("PermanentMACAddress=aa:bb:cc:00:11:22", lines)
        for line in ("[Link]", "BitsPerSecond=1G", "Duplex=full", "AutoNegotiation=no",
                     "WakeOnLan=magic", "ReceiveBufferSize=512", "TransmitBufferSize=1024",
                     "NamePolicy=keep kernel database onboard slot path",
                     "AlternativeNamesPolicy=database onboard slot path mac",
                     "MACAddressPolicy=persistent"):
            self.assertIn(line, lines)
        self.assertNotIn("Speed=", text)
        self.assertNotIn("Autonegotiation=", text)
        self.assertEqual(stat.S_IMODE(self.ours.stat().st_mode), 0o644)
        self.assertEqual(sorted(out.written), sorted(["link.autoneg", "link.speed", "link.duplex",
                                                      "wol", "ring.rx", "ring.tx"]))
        self.assertEqual(out.entry["values"], FORCED)
        self.assertEqual(os.listdir(self.link_dir), [self.ours.name])  # no temp left

    def test_autoneg_on(self):
        self.persist({"link": {"autoneg": True}})
        text = self.ours.read_text(encoding="utf-8")
        self.assertIn("AutoNegotiation=yes", text)
        self.assertNotIn("Duplex=", text)
        self.assertNotIn("BitsPerSecond=", text)

    def test_wol_letters(self):
        self.persist({"wol": {"wol": "pumbags"}})
        self.assertIn("WakeOnLan=phy unicast multicast broadcast arp magic secureon",
                      self.ours.read_text(encoding="utf-8"))
        self.persist({"wol": {"wol": "d"}})
        self.assertIn("WakeOnLan=off", self.ours.read_text(encoding="utf-8"))

    def test_zero_or_empty_mac_matches_original_name(self):
        for mac in ("", "00:00:00:00:00:00"):
            self.persist(mac=mac)
            text = self.ours.read_text(encoding="utf-8")
            self.assertIn("OriginalName=eth0", text)
            self.assertNotIn("PermanentMACAddress", text)

    def test_unsupported_fields(self):
        values = {"pause": {"rx": True}, "features": {"gro": True}, "coalesce": {"rx-usecs": 1},
                  "channels": {"rx": 1}, "eee": {"eee": True}, "ring": {"rx-mini": 1, "rx": 0},
                  "link": {"advertise": "0x2f"}, "wol": {"wol": "g"}}
        out = self.persist(values)
        self.assertEqual(out.written, ["wol"])
        self.assertEqual(sorted(out.unsupported), sorted([
            "pause.rx", "features.generic-receive-offload", "coalesce.rx-usecs", "channels.rx",
            "eee", "ring.rx-mini", "ring.rx", "link.advertise"]))
        self.assertNotIn("Receive", self.ours.read_text(encoding="utf-8"))

    def test_nothing_supported_writes_nothing(self):
        out = self.persist({"eee": {"eee": True}})
        self.assertEqual(out.written, [])
        self.assertEqual(os.listdir(self.link_dir), [])
        self.assertEqual(self.run.calls, [])

    def test_merges_with_previous_entry(self):
        first = self.persist({"ring": {"rx": 512}}).entry
        out = self.persist({"wol": {"wol": "g"}}, entry=first)
        text = self.ours.read_text(encoding="utf-8")
        self.assertIn("ReceiveBufferSize=512", text)
        self.assertIn("WakeOnLan=magic", text)
        self.assertEqual(out.entry["values"], {"ring": {"rx": 512}, "wol": {"wol": "g"}})
        self.assertEqual(text.count("NamePolicy="), 1)  # naming copied from our own file


class ConflictTest(Base):
    def test_earlier_link_file(self):
        self.after = LOG.format(path=str(self.link_dir / "05-other.link"))
        error = self.refused("conflict", "earlier_link_file")
        self.assertIsInstance(error, PersistConflict)
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_naming_change(self):
        self.before = LOG.format(path=self.default) + "ID_NET_NAME=enp3s0\n"
        self.after = LOG.format(path=self.ours) + "ID_NET_NAME=eth0\n"
        self.refused("conflict", "naming_changed")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_naming_lost(self):
        self.after = (LOG.format(path=self.ours).replace("Policy *keep*: keeping existing userspace name",
                      "Policies didn't yield a name and Name= is not given, not renaming."))
        self.refused("conflict", "naming_lost")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_unknown_output_after_fails_closed(self):
        self.after = "something new\n"
        self.refused("conflict", "cannot_verify")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_unknown_output_before_writes_nothing(self):
        self.before = "ID_NET_LINK_FILE=\n"
        self.refused("conflict", "cannot_verify")
        self.assertEqual(os.listdir(self.link_dir), [])
        self.assertEqual(len(self.run.calls), 1)

    def test_custom_link_file_in_use(self):
        self.before = LOG.format(path=str(self.link_dir / "70-custom.link"))
        self.refused("conflict", "custom_link_file")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_dropins(self):
        self.before = LOG.format(path=self.default) + "ID_NET_LINK_FILE_DROPINS=/etc/x.conf\n"
        self.refused("conflict", "link_dropins")

    def test_default_with_unknown_keys(self):
        self.default.write_text(DEFAULT + "Name=lan0\n", encoding="utf-8")
        self.refused("conflict", "unsupported_default")
        self.default.write_text(DEFAULT.replace("keep kernel", "keep $(reboot)"), encoding="utf-8")
        self.refused("conflict", "unsupported_default")

    def test_udevadm_failure_is_coded_and_rolled_back(self):
        calls = []

        def respond(argv):
            calls.append(argv)
            if len(calls) == 1:
                return 0, f"ID_NET_LINK_FILE={self.default}\n", LOG.format(path=self.default)
            return 1, "", "Failed to open device: No such file\n"
        self.run = FakeRunner(respond)
        self.refused("udevadm_failed", "Failed to open device: No such file")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_conflict_restores_the_old_file(self):
        first = self.persist({"ring": {"rx": 512}}).entry
        old = self.ours.read_bytes()
        self.after = LOG.format(path=str(self.link_dir / "05-other.link"))
        self.before = LOG.format(path=self.ours)
        with self.assertRaises(PersistConflict):
            self.persist({"wol": {"wol": "g"}}, entry=first)
        self.assertEqual(self.ours.read_bytes(), old)
        self.assertEqual(os.listdir(self.link_dir), [self.ours.name])


class UnsafePathTest(Base):
    def test_symlink_at_final_name(self):
        target = self.link_dir.parent / "target"
        target.write_text("keep", encoding="utf-8")
        self.ours.symlink_to(target)
        self.refused("unsafe_path")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertTrue(self.ours.is_symlink())

    def test_symlink_at_temp_name(self):
        target = self.link_dir.parent / "target"
        target.write_text("keep", encoding="utf-8")
        (self.link_dir / f".{self.ours.name}.000000000000.tmp").symlink_to(target)
        with mock.patch.object(common.secrets, "token_hex", return_value="000000000000"):
            self.refused("unsafe_path")
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertFalse(self.ours.exists())

    def test_link_dir_is_a_symlink(self):
        real = self.link_dir
        self.link_dir = real.parent / "alias"
        self.link_dir.symlink_to(real)
        self.refused("unsafe_path")
        self.assertEqual(os.listdir(real), [])

    def test_link_dir_writable_by_others(self):
        self.link_dir.chmod(0o777)
        self.refused("unsafe_path")

    def test_foreign_file_with_our_name(self):
        self.ours.write_text("[Match]\nName=eth0\n", encoding="utf-8")
        self.refused("foreign_file")
        self.assertEqual(self.ours.read_text(encoding="utf-8"), "[Match]\nName=eth0\n")


class RemoveTest(Base):
    def test_removes_only_our_file(self):
        self.persist()
        foreign = self.link_dir / "50-foreign.link"
        foreign.write_text("# ethtool-gui managed\n[Match]\n", encoding="utf-8")
        other = self.link_dir / "10-ethtool-gui-eth1.link"
        other.write_text("# ethtool-gui managed\n", encoding="utf-8")
        self.assertTrue(networkd.remove("eth0", self.env()))
        self.assertEqual(sorted(os.listdir(self.link_dir)), sorted([foreign.name, other.name]))
        self.assertFalse(networkd.remove("eth0", self.env()))

    def test_leaves_file_without_header(self):
        self.ours.write_text("[Match]\nName=eth0\n# ethtool-gui managed\n", encoding="utf-8")
        self.assertFalse(networkd.remove("eth0", self.env()))
        self.assertTrue(self.ours.exists())

    def test_leaves_symlink(self):
        target = self.link_dir.parent / "target"
        target.write_text("# ethtool-gui managed\n", encoding="utf-8")
        self.ours.symlink_to(target)
        with self.assertRaises(PersistError):
            networkd.remove("eth0", self.env())
        self.assertTrue(target.exists())

    def test_bad_name(self):
        for bad in ("-E", "..", "../x", "*"):
            with self.assertRaises(PersistError):
                networkd.remove(bad, self.env())


if __name__ == "__main__":
    unittest.main()
