import tempfile
import time
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ethtool_gui.core.ifaces import Iface
from ethtool_gui.core.models import Field, LinkInfo

HAVE_DISPLAY = Gtk.init_check()[0]

if HAVE_DISPLAY:
    from ethtool_gui.gui.simple_page import SimplePage
    from ethtool_gui.gui.helper_client import HelperError


def pump(until, timeout=2):
    end = time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return until()


@unittest.skipUnless(HAVE_DISPLAY, "no display")
class SimplePageTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.persisted = []
        self.events = []
        self.fail_confirm = False

        def call(cmd, **args):
            self.calls.append((cmd, args))
            self.events.append(cmd)
            if cmd == "confirm" and self.fail_confirm:
                raise HelperError("expired")
            return {"ok": True, "token": "abc", "seconds": 15.0} if cmd == "apply" else {"ok": True}

        def persist(**req):
            self.events.append("persist")
            self.persisted.append(req)

        self.page = SimplePage(call, persist)
        self.page.ask_confirmation = lambda text, danger=False: True
        self.page.show_interface(Iface("eth0", True, "up", "aa:bb:cc:dd:ee:ff"))
        self.page.show_reading({"link": LinkInfo(state=Field("up"), speed_mbps=Field(1000),
                                                  duplex=Field("Full"), autoneg=Field(True),
                                                  wol_supported=Field("g"), wol_current=Field("d"))})

    def tearDown(self):
        self.page.destroy()

    def test_autoneg_change_uses_link_operation_and_shows_countdown(self):
        self.page.autoneg_switch.set_active(False)
        self.assertTrue(pump(lambda: self.page.keep_bar.get_visible()))
        self.assertEqual(self.calls[0], ("apply", {"iface": "eth0", "op": "link",
                                                  "values": {"autoneg": False, "speed": 1000,
                                                             "duplex": "full"}}))
        self.assertGreater(self.page.keep_bar.seconds_left, 0)

    def test_keep_then_persist_has_strict_order(self):
        self.page.autoneg_switch.set_active(False)
        self.assertTrue(pump(lambda: self.page.keep_bar.get_visible()))
        self.page.keep_bar.save_button.clicked()
        self.assertTrue(pump(lambda: bool(self.persisted)))
        self.assertEqual(self.calls[-1], ("confirm", {"token": "abc"}))
        self.assertEqual(self.persisted, [{"op": "persist", "iface": "eth0",
                                           "values": {"link": {"autoneg": False,
                                                               "speed": 1000, "duplex": "full"}}}])
        self.assertEqual(self.events, ["apply", "confirm", "persist"])

    def test_confirm_failure_never_persists(self):
        self.fail_confirm = True
        self.page.autoneg_switch.set_active(False)
        self.assertTrue(pump(lambda: self.page.keep_bar.get_visible()))
        self.page.keep_bar.save_button.clicked()
        self.assertTrue(pump(lambda: self.page.notice.get_visible()))
        self.assertEqual(self.persisted, [])

    def test_pending_change_remembers_original_interface(self):
        self.page.autoneg_switch.set_active(False)
        self.assertTrue(pump(lambda: self.page.keep_bar.get_visible()))
        self.page.show_interface(Iface("eth1", True, "up", "aa:bb:cc:dd:ee:ff"))
        self.page.keep_bar.save_button.clicked()
        self.assertTrue(pump(lambda: bool(self.persisted)))
        self.assertEqual(self.persisted[0]["iface"], "eth0")

    def test_reverted_event_hides_bar_and_shows_notice(self):
        self.page.autoneg_switch.set_active(False)
        self.assertTrue(pump(lambda: self.page.keep_bar.get_visible()))
        self.page.on_helper_event({"event": "reverted", "token": "abc", "reason": "timeout"})
        self.assertFalse(self.page.keep_bar.get_visible())
        self.assertTrue(self.page.notice.get_visible())

    def test_cable_test_decline_does_not_call_helper(self):
        self.page.ask_confirmation = lambda text, danger: False
        self.page.cable_button.clicked()
        self.assertEqual(self.calls, [])

    def test_unsupported_wol_is_disabled_with_reason(self):
        self.page.show_reading({"link": LinkInfo(wol_supported=Field(None, False, "not supported"))})
        self.assertFalse(self.page.wol_switch.get_sensitive())
        self.assertIn("not supported", self.page.wol_switch.get_tooltip_text())


if __name__ == "__main__":
    unittest.main()
