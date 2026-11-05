import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ethtool_gui.core.runner import EthtoolMissing, EthtoolTimeout
from ethtool_gui.persist import common, networkd
from ethtool_gui.persist.common import Env, PersistConflict, PersistError
from tests.fakes import FakeRunner
from tests.test_parse_basic import fixture

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
        self.mac_reply = (0, f"Permanent address: {MAC}\n", "")
        self.udevadm_error = None  # exception raised by the n-th udevadm call: (n, exc)
        self.run = FakeRunner(self.respond)

    def udevadm_calls(self):
        return [c for c in self.run.calls if c[0] == "udevadm"]

    def respond(self, argv):
        if argv == ["ethtool", "-P", "eth0"]:
            return self.mac_reply
        # the current call is already recorded: the n-th (0-based) call sees n + 1
        if self.udevadm_error and len(self.udevadm_calls()) == self.udevadm_error[0] + 1:
            raise self.udevadm_error[1]
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

    def persist(self, values=FORCED, entry=None):
        return networkd.persist("eth0", values, self.env(), entry)

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
                     "WakeOnLan=magic", "RxBufferSize=512", "TxBufferSize=1024",
                     "NamePolicy=keep kernel database onboard slot path",
                     "AlternativeNamesPolicy=database onboard slot path mac",
                     "MACAddressPolicy=persistent"):
            self.assertIn(line, lines)
        for wrong in ("Speed=", "Autonegotiation=", "ReceiveBufferSize", "TransmitBufferSize",
                      "OriginalName"):
            self.assertNotIn(wrong, text)
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

    def test_mac_read_with_ethtool(self):
        self.mac_reply = (0, "Permanent address: AA:BB:CC:DD:EE:01\n", "")
        self.persist()
        self.assertIn("PermanentMACAddress=aa:bb:cc:dd:ee:01", self.ours.read_text(encoding="utf-8"))
        self.assertEqual(self.run.calls[0], ["ethtool", "-P", "eth0"])

    def test_no_permanent_mac_refused(self):
        for reply in ((0, "Permanent address: 00:00:00:00:00:00\n", ""),
                      (0, "Permanent address: \n", ""), (0, "", ""),
                      (1, "", "Operation not supported\n"),
                      (0, "Permanent address: 80:00:02:08:fe:80:00:00:00:00\n", "")):
            self.mac_reply = reply
            self.refused("no_permanent_mac")
            self.assertEqual(os.listdir(self.link_dir), [])
            self.assertEqual(self.udevadm_calls(), [])

    def test_unsupported_fields(self):
        values = {"pause": {"rx": True}, "features": {"gro": True}, "coalesce": {"rx-usecs": 1},
                  "channels": {"rx": 1}, "eee": {"eee": True}, "ring": {"rx-mini": 1, "rx": 0},
                  "link": {"advertise": "0x2f"}, "wol": {"wol": "g"}}
        out = self.persist(values)
        self.assertEqual(out.written, ["wol"])
        self.assertEqual(sorted(out.unsupported), sorted([
            "pause.rx", "features.generic-receive-offload", "coalesce.rx-usecs", "channels.rx",
            "eee", "ring.rx-mini", "ring.rx", "link.advertise"]))
        self.assertNotIn("BufferSize", self.ours.read_text(encoding="utf-8"))

    def test_nothing_supported_writes_nothing(self):
        out = self.persist({"eee": {"eee": True}})
        self.assertEqual(out.written, [])
        self.assertEqual(os.listdir(self.link_dir), [])
        self.assertEqual(self.run.calls, [])

    def test_merges_with_previous_entry(self):
        first = self.persist({"ring": {"rx": 512}}).entry
        out = self.persist({"wol": {"wol": "g"}}, entry=first)
        text = self.ours.read_text(encoding="utf-8")
        self.assertIn("RxBufferSize=512", text)
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
        self.assertEqual(len(self.udevadm_calls()), 1)

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
            if argv[0] == "ethtool":
                return self.mac_reply
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


    def test_udevadm_timeout_or_missing(self):
        for n in (0, 1):  # before writing, and after
            for exc, code in ((EthtoolTimeout("udevadm timed out"), "timeout"),
                              (EthtoolMissing("udevadm: command not found"), "tool_missing")):
                self.run.calls.clear()
                self.udevadm_error = (n, exc)
                self.refused(code)
                self.assertEqual(os.listdir(self.link_dir), [])

    def test_failure_after_rename_rolls_back(self):
        real = os.fsync
        seen = []

        def fsync(fd):
            seen.append(fd)
            if len(seen) == 2:  # the directory, right after the rename
                raise OSError(5, "Input/output error")
            return real(fd)
        with mock.patch("os.fsync", fsync), self.assertRaises(OSError):
            self.persist()
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_failure_after_rename_restores_previous_file(self):
        first = self.persist({"ring": {"rx": 512}}).entry
        old = self.ours.read_bytes()
        real, seen = os.fsync, []

        def fsync(fd):
            seen.append(fd)
            if len(seen) == 2:
                raise OSError(5, "Input/output error")
            return real(fd)
        with mock.patch("os.fsync", fsync), self.assertRaises(OSError):
            self.persist({"wol": {"wol": "g"}}, entry=first)
        self.assertEqual(self.ours.read_bytes(), old)
        self.assertEqual(os.listdir(self.link_dir), [self.ours.name])


class RealOutputTest(Base):
    """The captured output of a desktop with net.ifnames=0 (kernel names kept)."""

    def setUp(self):
        super().setUp()
        self.default.write_text(fixture("default-link.txt"), encoding="utf-8")

        def real(name):
            return fixture(name).replace(
                "/usr/lib/systemd/network/99-default.link", str(self.default)).replace(
                "/etc/systemd/network/10-rename-wlan0.link", str(self.link_dir / "10-rename-wlan0.link"))
        self.real_out = real("udevadm-net-setup-link-ifnames0.stdout.txt")
        self.real_err = real("udevadm-net-setup-link-ifnames0.stderr.txt")
        self.real = self.real_out  # stdout of the before-probe; the base class fakes the rest
        self.before = self.real
        self.after = self.real.replace(f"ID_NET_LINK_FILE={self.default}",
                                       f"ID_NET_LINK_FILE={self.ours}")

    def respond(self, argv):  # stdout as given, the real log on stderr
        code, out, err = super().respond(argv)
        if argv[0] != "udevadm" or code != 0:
            return code, out, err
        text = self.after if self.ours.exists() else self.before
        log = self.real_err.replace(f"Config file {self.default} is applied",
                                    f"Config file {self.ours} is applied") if self.ours.exists() else self.real_err
        return 0, text, log

    def test_parse_real_split(self):
        from ethtool_gui.persist import udev
        probe = udev.parse(self.real_out, self.real_err)
        self.assertEqual((probe.link_file, probe.name, probe.dropins, probe.no_name),
                         (str(self.default), "eth0", False, True))
        fallback = udev.parse("", self.real_out)  # properties on stderr: fallback only
        self.assertEqual((fallback.link_file, fallback.name), (probe.link_file, probe.name))
        self.assertNotIn("ID_NET_", self.real_err)

    def test_success_with_kernel_names(self):
        out = self.persist()
        self.assertIn("ring.rx", out.written)
        self.assertIn("NamePolicy=keep kernel database onboard slot path",
                      self.ours.read_text(encoding="utf-8"))

    def test_name_changed(self):
        self.after = self.after.replace("ID_NET_NAME=eth0", "ID_NET_NAME=enp3s0")
        self.refused("conflict", "naming_changed")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_other_file_applied_after(self):
        self.after = self.real.replace(f"ID_NET_LINK_FILE={self.default}",
                                       f"ID_NET_LINK_FILE={self.link_dir}/05-other.link")
        self.refused("conflict", "earlier_link_file")
        self.assertEqual(os.listdir(self.link_dir), [])

    def test_unrelated_admin_file_does_not_block(self):
        admin = self.link_dir / "10-rename-wlan0.link"
        admin.write_text("[Match]\nwlan0\n[Link]\nName=wlan0\n", encoding="utf-8")
        before = admin.read_bytes()
        self.persist()
        self.assertEqual(admin.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.link_dir)), sorted([admin.name, self.ours.name]))
        self.assertTrue(networkd.remove("eth0", self.env()))
        self.assertEqual(os.listdir(self.link_dir), [admin.name])

    def test_property_lines_are_required(self):
        self.before = "\n".join(l for l in self.real.splitlines() if not l.startswith("ID_NET_LINK_FILE="))
        self.refused("conflict", "cannot_verify")
        self.assertEqual(os.listdir(self.link_dir), [])


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

    def test_missing_link_dir(self):
        self.link_dir.rmdir()
        self.assertFalse(networkd.remove("eth0", self.env()))
        self.assertFalse(networkd.present("eth0", self.env()))

    def test_bad_name(self):
        for bad in ("-E", "..", "../x", "*"):
            with self.assertRaises(PersistError):
                networkd.remove(bad, self.env())


if __name__ == "__main__":
    unittest.main()
