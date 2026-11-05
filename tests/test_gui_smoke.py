import os
import threading
import unittest
from pathlib import Path
import tempfile

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

HAVE_DISPLAY = Gtk.init_check()[0]

if HAVE_DISPLAY:
    from ethtool_gui.core.runner import EthtoolMissing
    from ethtool_gui.gui.window import MainWindow


def make_tree(root: Path) -> Path:
    net = root / "net"
    for name, physical, state, speed in (("eth0", True, "up", "1000"), ("lo", False, "unknown", ""),
                                         ("br0", False, "down", "")):
        d = net / name
        d.mkdir(parents=True)
        (d / "operstate").write_text(state + "\n")
        (d / "address").write_text("aa:bb:cc:dd:ee:ff\n")
        if speed:
            (d / "speed").write_text(speed + "\n")
        if physical:
            (d / "device").mkdir()
    return net


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)


def pump(cond, limit=3.0):
    import time
    end = time.monotonic() + limit
    ctx = GLib.MainContext.default()
    while not cond() and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)
    return cond()


@unittest.skipUnless(HAVE_DISPLAY, "no display")
class WindowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.net = make_tree(Path(self._tmp.name))
        self.reads = []

        def reader(name):
            self.reads.append((name, threading.current_thread()))
            return {"iface": name}

        self.reader = reader
        self.window = MainWindow(None, sys_root=self.net, reader=reader, timers=False)

    def tearDown(self):
        self.window.destroy()
        self._tmp.cleanup()

    def test_frame_belongs_to_the_system_theme(self):
        self.assertIsNone(self.window.get_titlebar())
        self.assertFalse(any(isinstance(w, Gtk.HeaderBar) for w in walk(self.window)))

    def test_default_list_is_physical_only_and_toggle_adds_virtual(self):
        self.assertEqual(self.window.iface_list.names(), ["eth0"])
        self.window.iface_list.show_all_switch.set_active(True)
        self.assertEqual(self.window.iface_list.names(), ["br0", "eth0", "lo"])

    def test_mode_switch_changes_visible_page(self):
        stack = self.window.stack
        self.assertEqual(stack.get_visible_child_name(), "simple")
        self.window.mode_buttons["advanced"].set_active(True)
        self.assertEqual(stack.get_visible_child_name(), "advanced")
        self.window.mode_buttons["simple"].set_active(True)
        self.assertEqual(stack.get_visible_child_name(), "simple")
        self.assertIs(stack.get_child_by_name("simple"), self.window.simple_page)

    def test_selecting_an_interface_notifies_pages(self):
        seen = []
        self.window.simple_page.show_interface = lambda i: seen.append(i.name)
        self.window.iface_list.select("eth0")
        self.assertEqual(self.window.current_iface.name, "eth0")
        self.assertEqual(seen, ["eth0"])

    def test_removed_interface_clears_selection_and_page(self):
        self.window.iface_list.select("eth0")
        (self.net / "eth0").rename(self.net / "eth0-gone")
        self.window._reload_list()
        self.assertIsNone(self.window.current_iface)
        self.assertIsNone(self.window.simple_page.iface)
        self.assertEqual(self.window.iface_list.names(), ["eth0-gone"])

    def test_refresh_reads_off_the_ui_thread(self):
        self.window.iface_list.select("eth0")
        self.window.refresh()
        self.assertTrue(pump(lambda: self.reads))
        self.assertIsNot(self.reads[0][1], threading.main_thread())

    def test_missing_ethtool_shows_info_bar_with_package(self):
        def boom(name):
            raise EthtoolMissing("ethtool: command not found")

        self.window.reader = boom
        self.window.iface_list.select("eth0")
        self.window.refresh()
        self.assertTrue(pump(lambda: self.window.ethtool_bar.get_visible()))
        self.assertIn("apt install ethtool", self.window.ethtool_label.get_text())

    def test_permission_failure_marks_page_as_needing_authorization(self):
        from ethtool_gui.core.models import LinkInfo
        self.window.iface_list.select("eth0")
        self.assertTrue(pump(lambda: not self.window._busy))
        self.window.reader = lambda n: {"link": LinkInfo.unsupported("Operation not permitted")}
        self.window.refresh()
        self.assertTrue(pump(lambda: self.window.simple_page.needs_authorization))

    def test_authorized_wol_read_populates_both_views(self):
        from ethtool_gui.core.models import Field, LinkInfo
        from ethtool_gui.core.parse_settings import Wol

        class Helper:
            def call(self, cmd, **args):
                self.request = (cmd, args)
                return {"data": {"supported": "pumbg", "current": "d", "supported_flag": True}}

            def close(self):
                pass

        helper = Helper()
        self.window._helper = helper
        self.window.iface_list.select("eth0")
        self.assertEqual(self.window.authorized_wol("eth0"), ("pumbg", "d"))
        self.assertEqual(helper.request, ("read", {"iface": "eth0", "what": "wol"}))
        reading = {"link": LinkInfo(wol_supported=Field(None), wol_current=Field(None))}
        iface = self.window.current_iface
        self.window._apply([iface], {"eth0": "1000"}, iface, reading, False)
        self.assertTrue(self.window.simple_page.wol_switch.get_sensitive())
        self.assertFalse(self.window.simple_page.wol_switch.get_active())
        self.assertEqual(self.window.read_wol_cached("eth0"), Wol("pumbg", "d", True))
        self.window.update_wol("eth0", "g")
        self.assertEqual(self.window.read_wol_cached("eth0"), Wol("pumbg", "g", True))

    def test_late_read_result_after_close_is_ignored(self):
        self.window.destroy()
        self.assertFalse(self.window._apply([], {}, None, None, False))


@unittest.skipUnless(HAVE_DISPLAY, "no display")
class IconTest(unittest.TestCase):
    def test_icon_file_loads(self):
        from gi.repository import GdkPixbuf
        path = Path(__file__).resolve().parent.parent / "data" / "ethtool-gui.svg"
        self.assertIsNotNone(GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), 16, 16))


if __name__ == "__main__":
    unittest.main()
