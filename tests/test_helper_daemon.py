import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ethtool_gui.core.protocol import encode_message
from ethtool_gui.core.runner import Result
from ethtool_gui.helper import daemon, restore
from tests.fakes import make_fake_command, make_fake_ethtool, read_calls
from tests.test_parse_basic import fixture

FORCE = ["-s", "eth0", "autoneg", "off", "speed", "100", "duplex", "half"]
BACK = ["-s", "eth0", "autoneg", "on", "advertise", "0x2f"]
LINK_VALUES = {"autoneg": False, "speed": 100, "duplex": "half"}


class Pipe:
    """Blocking in-memory byte stream usable as helper stdin and as stdout."""

    def __init__(self):
        self.cond = threading.Condition()
        self.buf = bytearray()
        self.closed = False

    def write(self, data):
        with self.cond:
            self.buf += data
            self.cond.notify_all()
        return len(data)

    def flush(self):
        pass

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def readline(self, limit=-1, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.cond:
            while True:
                i = self.buf.find(b"\n")
                if i >= 0:
                    n = i + 1
                    if 0 <= limit < n:
                        n = limit
                    line = bytes(self.buf[:n])
                    del self.buf[:n]
                    return line
                if self.closed:
                    line, self.buf = bytes(self.buf), bytearray()
                    return line
                if 0 <= limit <= len(self.buf):
                    line = bytes(self.buf[:limit])
                    del self.buf[:limit]
                    return line
                if deadline is not None and time.monotonic() >= deadline:
                    return b""
                self.cond.wait(0.05)


class HelperBase(unittest.TestCase):
    """A helper wired to a fake ethtool, a fake sysfs and fake systemd tools."""

    revert_seconds = 30

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fake = self.root / "bin"
        self.fake.mkdir()
        self.sys = self.root / "sys"
        (self.sys / "eth0").mkdir(parents=True)
        (self.sys / "eth0" / "operstate").write_text("up\n")
        (self.root / "run").mkdir()
        self.state = self.root / "run" / "ethtool-gui"
        self.ethtool = make_fake_ethtool(self.fake, {
            ("eth0",): (0, fixture("settings-e1000e.txt"), ""),
            tuple(FORCE): (0, "", ""),
            tuple(BACK): (0, "", ""),
            ("-A", "eth0", "rx", "off"): (0, "", ""),
            ("-k", "eth0"): (0, fixture("features-e1000e.txt"), ""),
            ("-p", "eth0", "1"): (0, "", ""),
            ("--cable-test", "eth0"): (0, fixture("cable-test.txt"), ""),
            ("-t", "eth0", "online"): (0, "The test result is PASS\n", ""),
            ("-S", "eth0"): (0, fixture("stats-e1000e.txt"), ""),
        })
        self.systemd_run = make_fake_command(self.fake, "systemd-run")
        self.systemctl = make_fake_command(self.fake, "systemctl")
        for name, value in (("ETHTOOL_BIN", str(self.ethtool)), ("SYS_ROOT", self.sys),
                            ("STATE_DIR", self.state), ("REVERT_SECONDS", self.revert_seconds),
                            ("SYSTEMD_RUN", str(self.systemd_run)),
                            ("SYSTEMCTL", str(self.systemctl)), ("POLL_SECONDS", 0.02)):
            patcher = mock.patch.object(daemon, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("ETHTOOL_BIN", str(self.ethtool)), ("STATE_DIR", self.state)):
            patcher = mock.patch.object(restore, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.stdin, self.stdout = Pipe(), Pipe()
        self.next_id = 0
        self.code = None
        self.thread = threading.Thread(
            target=lambda: setattr(self, "code", daemon.main(self.stdin, self.stdout, [])))
        self.thread.start()
        self.addCleanup(self.end)

    def end(self):
        self.stdin.close()
        self.thread.join(10)
        self.assertFalse(self.thread.is_alive())
        leftovers = [t for t in threading.enumerate() if t.name.startswith("ethtool-gui")]
        self.assertEqual(leftovers, [])

    # -- protocol helpers -------------------------------------------------

    def read(self, timeout=5.0):
        line = self.stdout.readline(timeout=timeout)
        return json.loads(line) if line else None

    def send(self, cmd, **fields):
        self.next_id += 1
        self.stdin.write(encode_message({"id": self.next_id, "cmd": cmd, **fields}))
        return self.next_id

    def call(self, cmd, **fields):
        ident = self.send(cmd, **fields)
        return self.reply_to(ident)

    def reply_to(self, ident, timeout=5.0):
        for i, msg in enumerate(self.others):
            if msg.get("id") == ident:
                return self.others.pop(i)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.read(deadline - time.monotonic())
            if msg is None:
                break
            if msg.get("id") == ident:
                return msg
            self.others.append(msg)
        self.fail(f"no reply to {ident}")

    def event(self, timeout=5.0):
        for i, msg in enumerate(self.others):
            if "event" in msg:
                return self.others.pop(i)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.read(deadline - time.monotonic())
            if msg is None:
                break
            if "event" in msg:
                return msg
            self.others.append(msg)
        return None

    @property
    def others(self):
        if not hasattr(self, "_others"):
            self._others = []
        return self._others

    def calls(self):
        return read_calls(self.fake)

    def close_and_wait(self):
        self.stdin.close()
        self.thread.join(10)
        self.assertFalse(self.thread.is_alive())

    def apply_link(self):
        reply = self.call("apply", iface="eth0", op="link", values=LINK_VALUES)
        self.assertTrue(reply["ok"], reply)
        return reply


class ValidationTest(HelperBase):
    def test_ping(self):
        self.assertEqual(self.call("ping"), {"id": 1, "ok": True})

    def test_bad_interface_names_rejected_and_daemon_keeps_answering(self):
        for name in ("../x", "..", ".", "eth0:1", "-E", "a" * 16, "", "eth 0", 5, None):
            reply = self.call("apply", iface=name, op="pause", values={"rx": False})
            self.assertFalse(reply["ok"], name)
            self.assertEqual(reply["error"], "bad_iface", name)
            self.assertTrue(self.call("ping")["ok"])
        self.assertEqual(self.calls(), [])

    def test_missing_interface(self):
        reply = self.call("apply", iface="eth9", op="pause", values={"rx": False})
        self.assertEqual(reply["error"], "no_device")
        self.assertEqual(self.calls(), [])

    def test_malformed_json_unknown_command_and_op(self):
        self.stdin.write(b"{not json\n")
        self.assertEqual(self.read()["error"], "bad_json")
        self.stdin.write(b"[1, 2]\n")
        self.assertEqual(self.read()["error"], "bad_json")
        self.assertEqual(self.call("format")["error"], "unknown_command")
        self.assertEqual(self.call("apply", iface="eth0", op="eeprom", values={})["error"],
                         "unknown_op")
        self.assertEqual(self.call("apply", iface="eth0", op="flash", values={})["error"],
                         "unknown_op")
        self.assertEqual(self.call("ping", extra=1)["error"], "bad_request")
        self.stdin.write(encode_message({"id": "x", "cmd": "ping"}))
        self.assertEqual(self.read(), {"id": None, "ok": False, "error": "bad_request"})
        self.assertTrue(self.call("ping")["ok"])

    def test_features_outside_allowlist_rejected(self):
        reply = self.call("apply", iface="eth0", op="features", values={"msglvl": True})
        self.assertEqual(reply["error"], "bad_values")
        self.assertEqual(self.calls(), [])

    def test_line_over_64k_rejected_then_serving_continues(self):
        big = b'{"id": 1, "cmd": "ping", "pad": "' + b"a" * (70 * 1024) + b'"}\n'
        self.stdin.write(big)
        self.assertEqual(self.read()["error"], "line_too_long")
        self.next_id = 1
        self.assertTrue(self.call("ping")["ok"])

    def test_cable_test_needs_confirmation(self):
        self.assertEqual(self.call("cable_test", iface="eth0")["error"], "confirmation_required")
        self.assertEqual(self.call("cable_test", iface="eth0", confirmed=1)["error"],
                         "confirmation_required")
        self.assertEqual(self.calls(), [])
        reply = self.call("cable_test", iface="eth0", confirmed=True)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["data"]["pairs"][2]["fault_at_m"], 37.0)

    def test_selftest(self):
        self.assertEqual(self.call("selftest", iface="eth0", mode="offline")["error"],
                         "confirmation_required")
        self.assertEqual(self.call("selftest", iface="eth0", mode="external_lb")["error"],
                         "bad_request")
        reply = self.call("selftest", iface="eth0", mode="online")
        self.assertTrue(reply["ok"], reply)
        self.assertIs(reply["data"]["passed"], True)

    def test_blink_range_and_timeout(self):
        for seconds in (0, 61, "5", True):
            self.assertEqual(self.call("blink", iface="eth0", seconds=seconds)["error"],
                             "bad_request")
        with mock.patch.object(daemon, "run_ethtool", wraps=daemon.run_ethtool) as spy:
            self.assertTrue(self.call("blink", iface="eth0", seconds=1)["ok"])
        self.assertEqual(spy.call_args.kwargs["timeout"], 6)

    def test_read(self):
        reply = self.call("read", iface="eth0", what="link")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["data"]["speed_mbps"], 1000)
        self.assertIn("values", self.call("read", iface="eth0", what="stats")["data"])
        self.assertEqual(self.call("read", iface="eth0", what="../etc")["error"], "bad_request")

    def test_bad_tokens(self):
        for token in ("../x", "0123456789abcde", "0123456789ABCDEF", 7):
            self.assertEqual(self.call("confirm", token=token)["error"], "bad_token")
        self.assertEqual(self.call("cancel", token="0123456789abcdef")["error"], "unknown_token")

    def test_apply_pause_returns_no_token(self):
        reply = self.call("apply", iface="eth0", op="pause", values={"rx": False})
        self.assertEqual(reply, {"id": 1, "ok": True})
        self.assertEqual(self.calls(), [["-A", "eth0", "rx", "off"]])
        self.assertFalse(self.state.exists() and any(self.state.iterdir()))

    def test_ethtool_failure_reported(self):
        reply = self.call("apply", iface="eth0", op="pause", values={"tx": False})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], "ethtool_failed")
        self.assertIn("unexpected", reply["detail"])

    def test_in_flight_limit(self):
        gate = threading.Event()

        def slow(args, **kwargs):
            gate.wait(5)
            return Result(0, "", "")

        with mock.patch.object(daemon, "run_ethtool", side_effect=slow):
            ids = [self.send("blink", iface="eth0", seconds=1) for _ in range(17)]
            self.assertEqual(self.reply_to(ids[-1])["error"], "too_many_requests")
            gate.set()
            for ident in ids[:-1]:
                self.assertTrue(self.reply_to(ident)["ok"])


class ApplyRevertTest(HelperBase):
    def test_apply_link_runs_argv_and_arms_watchdog(self):
        reply = self.apply_link()
        token = reply["token"]
        self.assertRegex(token, r"^[0-9a-f]{16}$")
        self.assertEqual(reply["seconds"], 30)
        self.assertEqual(reply["watchdog"], "systemd")
        self.assertEqual(self.calls(), [["eth0"], FORCE])
        self.assertEqual(read_calls(self.fake, "systemd-run.log"), [[
            "--on-active=30s", f"--unit=ethtool-gui-revert-{token}",
            "/usr/lib/ethtool-gui/ethtool-gui-restore", token]])

    def test_state_file_permissions_and_content(self):
        token = self.apply_link()["token"]
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        path = self.state / f"{token}.json"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        data = json.loads(path.read_text())
        self.assertEqual((data["iface"], data["op"], data["saved"]),
                         ("eth0", "link", {"autoneg": True, "advertise": "0x2f"}))
        self.assertIsInstance(data["created_at"], (int, float))

    def test_confirm_prevents_restore_and_stops_unit(self):
        token = self.apply_link()["token"]
        self.assertEqual(self.call("confirm", token=token), {"id": 2, "ok": True})
        self.assertFalse((self.state / f"{token}.json").exists())
        self.assertEqual(read_calls(self.fake, "systemctl.log"),
                         [["stop", f"ethtool-gui-revert-{token}.timer"]])
        self.close_and_wait()
        self.assertNotIn(BACK, self.calls())
        self.assertEqual(self.call_count_events(), 0)

    def call_count_events(self):
        return sum(1 for m in self.others if "event" in m) + sum(
            1 for _ in iter(lambda: self.read(0.1), None))

    def test_cancel_restores_now(self):
        token = self.apply_link()["token"]
        reply = self.call("cancel", token=token)
        self.assertTrue(reply["ok"])
        self.assertEqual(self.calls()[-1], BACK)
        self.assertEqual(self.event(), {"event": "reverted", "token": token, "reason": "cancel",
                                        "restored": True})

    def test_second_apply_on_same_iface_is_busy(self):
        self.apply_link()
        self.assertEqual(self.call("apply", iface="eth0", op="link", values=LINK_VALUES)["error"],
                         "busy")
        self.assertEqual(self.call("apply", iface="eth0", op="pause", values={"rx": False})["error"],
                         "busy")
        self.assertEqual(self.calls().count(FORCE), 1)

    def test_change_pending_in_another_helper_is_busy(self):
        from ethtool_gui.helper import revert
        revert.write_state(self.state, "fedcba9876543210", "eth0", "link", {"autoneg": True})
        self.assertEqual(self.call("apply", iface="eth0", op="link", values=LINK_VALUES)["error"],
                         "busy")
        self.assertEqual(self.calls(), [])

    def test_stale_state_file_does_not_block(self):
        from ethtool_gui.helper import revert
        path = revert.write_state(self.state, "fedcba9876543210", "eth0", "link", {"autoneg": True})
        data = json.loads(path.read_text())
        data["created_at"] -= daemon.STALE_SECONDS + 1
        path.write_text(json.dumps(data))
        self.apply_link()

    def test_stdin_close_restores_immediately(self):
        token = self.apply_link()["token"]
        started = time.monotonic()
        self.close_and_wait()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.calls()[-1], BACK)
        self.assertEqual(self.event(), {"event": "reverted", "token": token, "reason": "closed",
                                        "restored": True})
        self.assertFalse((self.state / f"{token}.json").exists())
        self.assertEqual(self.code, 0)

    def test_systemd_run_missing_still_applies(self):
        with mock.patch.object(daemon, "SYSTEMD_RUN", str(self.root / "nope")):
            reply = self.apply_link()
        self.assertEqual(reply["watchdog"], "in-process only")
        self.assertIn(FORCE, self.calls())

    def test_snapshot_failure_refuses_apply(self):
        reply = self.call("apply", iface="eth0", op="features", values={"lro": True})
        self.assertEqual(reply["error"], "cannot_snapshot")
        self.assertEqual(self.calls(), [["-k", "eth0"]])
        self.assertEqual(read_calls(self.fake, "systemd-run.log"), [])

    def test_helper_death_then_restore_main(self):
        token = self.apply_link()["token"]
        # Simulate the helper being killed: its countdown never fires.
        self.helper_countdowns_stop()
        self.assertEqual(restore.main([token]), 0)
        self.assertEqual(self.calls()[-1], BACK)
        self.assertFalse((self.state / f"{token}.json").exists())
        self.close_and_wait()
        self.assertEqual(self.calls().count(BACK), 1)   # the closing helper did not restore again

    def helper_countdowns_stop(self):
        for t in threading.enumerate():
            if t.name.startswith("ethtool-gui-revert"):
                t.cancel()


class TimeoutTest(HelperBase):
    revert_seconds = 0.2

    def test_timeout_restores_and_emits_event(self):
        token = self.apply_link()["token"]
        self.assertEqual(self.event(), {"event": "reverted", "token": token, "reason": "timeout",
                                        "restored": True})
        self.assertEqual(self.calls(), [["eth0"], FORCE, BACK])
        self.assertFalse((self.state / f"{token}.json").exists())
        self.assertEqual(read_calls(self.fake, "systemctl.log"),
                         [["stop", f"ethtool-gui-revert-{token}.timer"]])
        # The interface is free again.
        self.assertTrue(self.call("apply", iface="eth0", op="pause", values={"rx": False})["ok"])

    def test_carrier_down_does_not_cancel(self):
        (self.sys / "eth0" / "operstate").write_text("down\n")
        (self.sys / "eth0" / "carrier").write_text("0\n")
        token = self.apply_link()["token"]
        (self.sys / "eth0" / "operstate").write_text("lowerlayerdown\n")
        self.assertEqual(self.event()["reason"], "timeout")
        self.assertEqual(self.calls()[-1], BACK)
        self.assertTrue(token)

    def test_double_restore_is_harmless(self):
        token = self.apply_link()["token"]
        self.assertEqual(restore.main([token]), 0)          # systemd fired first
        event = self.event()
        self.assertEqual(event["reason"], "timeout")
        self.assertIs(event["restored"], False)
        self.assertEqual(self.calls().count(BACK), 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(restore.main([token]), 2)      # and a late second run is a no-op
        self.assertEqual(self.calls().count(BACK), 1)


class GoneTest(HelperBase):
    revert_seconds = 1

    def test_interface_removed_cancels_without_restore(self):
        token = self.apply_link()["token"]
        shutil.rmtree(self.sys / "eth0")
        self.assertEqual(self.event(), {"event": "reverted", "token": token, "reason": "gone",
                                        "restored": False})
        self.assertNotIn(BACK, self.calls())
        self.assertFalse((self.state / f"{token}.json").exists())


class MainTest(unittest.TestCase):
    def test_any_argv_is_refused(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake = Path(tmp.name)
        script = make_fake_ethtool(fake, {})
        out, err = io.BytesIO(), io.StringIO()
        with mock.patch.object(daemon, "ETHTOOL_BIN", str(script)), contextlib.redirect_stderr(err):
            code = daemon.main(io.BytesIO(b'{"id":1,"cmd":"ping"}\n'), out, ["--ethtool=/x"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out.getvalue(), b"")
        self.assertEqual(len(err.getvalue().splitlines()), 1)
        self.assertEqual(read_calls(fake), [])

    def test_module_entry_point_refuses_arguments(self):
        done = subprocess.run([sys.executable, "-m", "ethtool_gui.helper", "--ethtool=/x"],
                              input=b"", capture_output=True, timeout=30,
                              cwd=Path(__file__).resolve().parent.parent)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, b"")

    def test_restore_constants_match(self):
        self.assertEqual(restore.STATE_DIR, daemon.STATE_DIR)
        self.assertEqual(restore.ETHTOOL_BIN, daemon.ETHTOOL_BIN)
        self.assertEqual(daemon.STATE_DIR, Path("/run/ethtool-gui"))
        self.assertEqual(daemon.REVERT_SECONDS, 15)
        self.assertEqual(daemon.SYSTEMD_RUN, "/usr/bin/systemd-run")


if __name__ == "__main__":
    unittest.main()
