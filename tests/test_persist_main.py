import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ethtool_gui.persist import __main__ as program
from ethtool_gui.persist import state
from ethtool_gui.core.runner import EthtoolTimeout
from tests.fakes import FakeRunner
from tests.test_persist_networkd import DEFAULT, LOG
from tests.test_persist_nm import ETH, UUID, nm_runner


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.link_dir = self.root / "network"
        self.link_dir.mkdir(mode=0o755)
        self.default = self.root / "99-default.link"
        self.default.write_text(DEFAULT, encoding="utf-8")
        self.sys_root = self.root / "sys"
        for name in ("eth0", "eth1"):
            (self.sys_root / name).mkdir(parents=True)
        self.state_file = self.root / "lib" / "managed.json"
        self.ours = self.link_dir / "10-ethtool-gui-eth0.link"
        self.run = None
        self.mac = "aa:bb:cc:00:11:22"
        patches = {"LINK_DIR": self.link_dir, "SYS_ROOT": self.sys_root,
                   "STATE_FILE": self.state_file, "DEFAULT_LINKS": (str(self.default),),
                   "run_tool": lambda argv: self.run(argv)}
        for name, value in patches.items():
            patcher = mock.patch.object(program, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def networkd_runner(self):
        def respond(argv):
            if argv[0] == "nmcli":
                return 0, "\n", ""  # no NetworkManager profile
            if argv == ["ethtool", "-P", "eth0"]:
                return 0, f"Permanent address: {self.mac}\n", ""
            if argv[:3] == ["udevadm", "test-builtin", "net_setup_link"]:
                path = self.ours if self.ours.exists() else self.default
                text = LOG.format(path=path)
                return 0, "ID_NET_LINK_FILE=%s\n" % path, text
            return 99, "", "unexpected\n"
        return FakeRunner(respond)

    def call(self, request, argv=()):
        raw = request if isinstance(request, bytes) else json.dumps(request).encode() + b"\n"
        out, err = io.BytesIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = program.main(io.BytesIO(raw), out, list(argv))
        lines = out.getvalue().splitlines()
        self.assertLessEqual(len(lines), 1)
        return code, (json.loads(lines[0]) if lines else None)


class InputTest(Base):
    def setUp(self):
        super().setUp()
        self.run = FakeRunner(lambda argv: self.fail(f"spawned {argv}"))

    def test_argv_refused_nothing_spawned(self):
        code, reply = self.call({"op": "status", "iface": "eth0"}, argv=["--nmcli=/tmp/x"])
        self.assertNotEqual(code, 0)
        self.assertIsNone(reply)
        self.assertEqual(self.run.calls, [])

    def test_bad_json_and_limits(self):
        for raw, error in ((b"{nope\n", "bad_json"), (b"[1]\n", "bad_json"),
                           (b"", "bad_request"),
                           (b'{"op":"status","iface":"eth0","x":"' + b"a" * (64 * 1024) + b'"}\n',
                            "line_too_long")):
            code, reply = self.call(raw)
            self.assertEqual((code, reply["ok"], reply["error"]), (1, False, error))

    def test_iface_checked_before_anything(self):
        for iface, error in (("-E", "bad_iface"), ("..", "bad_iface"), ("eth0:1", "bad_iface"),
                             (5, "bad_iface"), ("nope", "no_device")):
            for op in ("status", "persist", "remove", "bogus"):
                code, reply = self.call({"op": op, "iface": iface, "values": {"wol": {"wol": "g"}}})
                self.assertEqual((code, reply["error"]), (1, error), (iface, op))
        self.assertEqual(self.run.calls, [])
        self.assertFalse(self.state_file.parent.exists())

    def test_request_shape(self):
        for request in ({"op": "bogus", "iface": "eth0"},
                        {"op": "status", "iface": "eth0", "values": {}},
                        {"op": "persist", "iface": "eth0", "values": {}, "extra": 1},
                        {"op": "status", "iface": "eth0", "permanent_mac": "aa:bb:cc:00:11:22"}):
            code, reply = self.call(request)
            self.assertEqual(reply["error"], "bad_request", request)

    def test_values_outside_validators(self):
        for values in ({"link": {"autoneg": False, "speed": 123, "duplex": "full"}},
                       {"wol": {"wol": "gx"}}, {"ring": {"rx": 1 << 30}}, {"bogus": {}},
                       {"coalesce": {"rx-usecs": "1"}}):
            code, reply = self.call({"op": "persist", "iface": "eth0", "values": values})
            self.assertEqual(reply["error"], "bad_values", values)
        self.assertEqual(self.run.calls, [])


class NmProgramTest(Base):
    def setUp(self):
        super().setUp()
        self.run = nm_runner()

    def test_persist_then_remove(self):
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {
            "link": {"autoneg": False, "speed": 100, "duplex": "full"}, "eee": {"eee": True}}})
        self.assertEqual(code, 0)
        self.assertEqual(reply, {"ok": True, "backend": "nm",
                                 "written": ["link.autoneg", "link.speed", "link.duplex"],
                                 "unsupported": ["eee"]})
        self.assertEqual(stat.S_IMODE(self.state_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state_file.parent.stat().st_mode), 0o700)
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["ifaces"]["eth0"]["previous"][ETH + "speed"], "0")

        code, reply = self.call({"op": "status", "iface": "eth0"})
        self.assertEqual(reply["backend"], "nm")
        self.assertEqual(reply["fields"], ["link.autoneg", "link.speed", "link.duplex"])
        self.assertIn(ETH + "speed", reply["persisted"])

        self.run = nm_runner()
        code, reply = self.call({"op": "remove", "iface": "eth0"})
        self.assertEqual((code, reply["ok"]), (0, True))
        modify = [c for c in self.run.calls if c[:3] == ["nmcli", "connection", "modify"]]
        self.assertEqual(modify, [["nmcli", "connection", "modify", "uuid", UUID,
                                   ETH + "auto-negotiate", "no", ETH + "duplex", "",
                                   ETH + "speed", "0"]])
        self.assertEqual(state.load(self.state_file), {})
        code, reply = self.call({"op": "remove", "iface": "eth0"})
        self.assertEqual(reply["error"], "not_managed")

    def test_tool_error_first_line(self):
        self.run = nm_runner(modify=(4, "", "Error: boom\nsecond\n"))
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.assertEqual(reply, {"ok": False, "error": "nmcli_failed", "reason": "Error: boom"})
        self.assertEqual(state.load(self.state_file), {})  # refused, re-read unchanged: rolled back

    def test_definite_failure_with_changed_profile_keeps_entry(self):
        self.run = nm_runner(modify=(4, "", "Error: boom\n"), after={ETH + "speed": "100"})
        self.call({"op": "persist", "iface": "eth0", "values": {
            "link": {"autoneg": False, "speed": 100, "duplex": "full"}}})
        self.assertEqual(state.load(self.state_file)["eth0"]["previous"][ETH + "speed"], "0")

    def test_timeout_keeps_entry_and_remove_restores(self):
        self.run = nm_runner(modify=EthtoolTimeout("nmcli timed out"))
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {
            "link": {"autoneg": False, "speed": 100, "duplex": "full"}}})
        self.assertEqual(reply["error"], "timeout")
        entry = state.load(self.state_file)["eth0"]
        self.assertEqual(entry["previous"], {ETH + "auto-negotiate": "no", ETH + "speed": "0",
                                             ETH + "duplex": ""})
        self.run = nm_runner()
        code, reply = self.call({"op": "remove", "iface": "eth0"})
        self.assertTrue(reply["ok"])
        modify = [c for c in self.run.calls if c[:3] == ["nmcli", "connection", "modify"]]
        self.assertEqual(modify, [["nmcli", "connection", "modify", "uuid", UUID,
                                   ETH + "auto-negotiate", "no", ETH + "duplex", "",
                                   ETH + "speed", "0"]])
        self.assertEqual(state.load(self.state_file), {})

    def test_state_symlink_refused(self):
        self.state_file.parent.mkdir(mode=0o700)
        target = self.root / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        self.state_file.symlink_to(target)
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.assertEqual(reply["error"], "state_error")
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_tampered_state_refused(self):
        self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        data["ifaces"]["eth0"]["previous"][ETH + "wake-on-lan"] = "magic; reboot"
        self.state_file.write_text(json.dumps(data), encoding="utf-8")
        code, reply = self.call({"op": "remove", "iface": "eth0"})
        self.assertEqual(reply["error"], "state_error")
        os.chmod(self.state_file, 0o644)
        code, reply = self.call({"op": "status", "iface": "eth0"})
        self.assertEqual(reply["error"], "state_error")


class NetworkdProgramTest(Base):
    def setUp(self):
        super().setUp()
        self.run = self.networkd_runner()

    def test_persist_status_remove(self):
        code, reply = self.call({"op": "persist", "iface": "eth0", "permanent_mac": "AA:BB:CC:00:11:22",
                                 "values": {"link": {"autoneg": True}, "pause": {"rx": True}}})
        self.assertEqual(reply, {"ok": True, "backend": "networkd", "written": ["link.autoneg"],
                                 "unsupported": ["pause.rx"]})
        self.assertIn("PermanentMACAddress=aa:bb:cc:00:11:22", self.ours.read_text(encoding="utf-8"))
        code, reply = self.call({"op": "status", "iface": "eth0"})
        self.assertEqual((reply["backend"], reply["file"], reply["values"]),
                         ("networkd", True, {"link": {"autoneg": True}}))
        foreign = self.link_dir / "20-foreign.link"
        foreign.write_text("[Match]\n", encoding="utf-8")
        code, reply = self.call({"op": "remove", "iface": "eth0"})
        self.assertEqual(reply, {"ok": True, "backend": "networkd", "removed": ["link.autoneg"]})
        self.assertEqual(os.listdir(self.link_dir), [foreign.name])

    def test_request_mac_ignored(self):
        code, reply = self.call({"op": "persist", "iface": "eth0", "permanent_mac": "02:00:00:00:00:99",
                                 "values": {"wol": {"wol": "g"}}})
        self.assertTrue(reply["ok"])
        text = self.ours.read_text(encoding="utf-8")
        self.assertIn("PermanentMACAddress=aa:bb:cc:00:11:22", text)
        self.assertNotIn("02:00:00:00:00:99", text)

    def test_zero_mac(self):
        for mac in ("00:00:00:00:00:00", ""):
            self.mac = mac
            code, reply = self.call({"op": "persist", "iface": "eth0", "permanent_mac": "aa:bb:cc:00:11:22",
                                     "values": {"wol": {"wol": "g"}}})
            self.assertEqual(reply["error"], "no_permanent_mac")
            self.assertEqual(os.listdir(self.link_dir), [])
            code, reply = self.call({"op": "status", "iface": "eth0"})
            self.assertEqual((reply["detected"], reply["reason"]), ("none", "no_permanent_mac"))

    def test_status_with_file_missing(self):
        self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.ours.unlink()
        code, reply = self.call({"op": "status", "iface": "eth0"})
        self.assertEqual((reply["ok"], reply["managed"], reply["file"]), (True, True, False))

    def test_remove_without_entry_or_link_dir(self):
        self.link_dir.rmdir()
        code, reply = self.call({"op": "remove", "iface": "eth0"})
        self.assertEqual(reply["error"], "not_managed")

    def test_conflict_reply(self):
        def respond(argv):
            if argv[0] == "nmcli":
                return 0, "", ""
            if argv[0] == "ethtool":
                return 0, f"Permanent address: {self.mac}\n", ""
            path = self.link_dir / "05-other.link" if self.ours.exists() else self.default
            return 0, f"ID_NET_LINK_FILE={path}\n", ""
        self.run = FakeRunner(respond)
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.assertEqual((code, reply), (1, {"ok": False, "error": "conflict",
                                             "reason": "earlier_link_file"}))
        self.assertEqual(os.listdir(self.link_dir), [])
        self.assertEqual(state.load(self.state_file), {})

    def test_backend_changed(self):
        self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.run = nm_runner()
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.assertEqual(reply["error"], "backend_changed")

    def test_no_backend(self):
        self.link_dir.rename(self.root / "gone")
        code, reply = self.call({"op": "persist", "iface": "eth0", "values": {"wol": {"wol": "g"}}})
        self.assertEqual(reply["error"], "no_backend")

    def test_internal_errors_are_replies(self):
        self.run = FakeRunner(lambda argv: 1 / 0)
        code, reply = self.call({"op": "status", "iface": "eth0"})
        self.assertEqual((code, reply["ok"], reply["error"]), (1, False, "internal_error"))


if __name__ == "__main__":
    unittest.main()
