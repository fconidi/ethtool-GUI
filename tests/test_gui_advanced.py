import unittest
from unittest import mock
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ethtool_gui.core.models import DriverInfo, Eee, Feature, FeaturesInfo, Field, LinkInfo, Ring, Wol

HAVE_DISPLAY = Gtk.init_check()[0]

if HAVE_DISPLAY:
    from ethtool_gui.gui.advanced_page import AdvancedPage
    from ethtool_gui.gui.tabs.link import LinkTab
    from ethtool_gui.gui.tabs.offload import OffloadTab
    from ethtool_gui.gui.tabs.persistence import PersistenceTab
    from ethtool_gui.gui.tabs.snapshot import SnapshotTab
    from ethtool_gui.gui.tabs.settings import SettingsTab


@unittest.skipUnless(HAVE_DISPLAY, "no display")
class AdvancedTest(unittest.TestCase):
    def test_all_topics_are_findable(self):
        page = AdvancedPage(lambda *a, **k: None, lambda **k: {"ok": True})
        self.addCleanup(page.destroy)
        self.assertEqual(page.names, ["Link", "Driver", "Statistics", "Pause/Ring/Channels",
                                      "Coalescing", "Offload", "EEE/WoL", "RSS", "SFP",
                                      "Tests", "Dump", "Snapshot", "Persistence"])

    def test_italian_advanced_controls_are_translated(self):
        with mock.patch.dict("os.environ", {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "it_IT.UTF-8"}):
            page = AdvancedPage(lambda *a, **k: None, lambda **k: {"ok": True})
            self.addCleanup(page.destroy)
            self.assertEqual(page.notebook.get_tab_label_text(page.tabs[1]), "Driver")
            self.assertEqual(page.notebook.get_tab_label_text(page.tabs[2]), "Statistiche")
            self.assertEqual(page.tabs[0].apply_button.get_label(), "Applica impostazioni del collegamento")
            self.assertEqual(page.tabs[9].get_children()[1].get_children()[1].get_label(), "Autotest online")

    def test_switching_to_driver_loads_its_content(self):
        page = AdvancedPage(lambda *a, **k: None, lambda **k: {"ok": True})
        self.addCleanup(page.destroy)
        page.tabs[1].reader = lambda _name: DriverInfo(driver=Field("diagnostic-driver"), version=Field("1.2"))
        page.show_all()
        page.show_interface("eth0")
        page.notebook.set_current_page(1)
        end = time.monotonic() + 2
        output = page.tabs[1].output.get_buffer()
        while time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if "diagnostic-driver" in output.get_text(output.get_start_iter(), output.get_end_iter(), True):
                break
            time.sleep(0.01)
        self.assertIn("diagnostic-driver", output.get_text(output.get_start_iter(), output.get_end_iter(), True))
        self.assertEqual(page.tabs[1].reason.get_text(), "")

    def test_link_advertised_modes_form_a_mask(self):
        tab = LinkTab()
        self.addCleanup(tab.destroy)
        tab.render(LinkInfo(autoneg=Field(True), supported_modes=Field(["1000baseT/Full", "100baseT/Full"]),
                            advertised_modes=Field(["1000baseT/Full"])))
        self.assertTrue(tab.mode_checks["1000baseT/Full"].get_active())
        tab.mode_checks["100baseT/Full"].set_active(True)
        self.assertEqual(tab.collect(), {"autoneg": True, "advertise": "0x28"})

    def test_manual_speed_requires_autoneg_off(self):
        tab = LinkTab()
        self.addCleanup(tab.destroy)
        tab.render(LinkInfo(autoneg=Field(True), speed_mbps=Field(1000), duplex=Field("Full"),
                            supported_modes=Field(["100baseT/Full", "1000baseT/Full"]),
                            advertised_modes=Field(["100baseT/Full", "1000baseT/Full"])))
        self.assertFalse(tab.speed.get_sensitive())
        self.assertFalse(tab.duplex.get_sensitive())
        self.assertEqual(tab.collect(), {})
        tab.autoneg.set_active(False)
        self.assertTrue(tab.speed.get_sensitive())
        tab.speed.set_active_id("100")
        self.assertEqual(tab.collect(), {"autoneg": False, "speed": 100, "duplex": "full"})

    def test_fixed_offload_is_disabled(self):
        tab = OffloadTab()
        self.addCleanup(tab.destroy)
        tab.render(FeaturesInfo({"rx-checksumming": Feature(True, fixed=True),
                                 "tcp-segmentation-offload": Feature(True)}))
        self.assertFalse(tab.controls["rx-checksumming"].get_sensitive())
        tab.controls["tcp-segmentation-offload"].set_active(False)
        self.assertEqual(tab.collect(), {"tcp-segmentation-offload": False})

    def test_ring_collects_only_changed_supported_values(self):
        tab = SettingsTab(("ring",), lambda *args: None)
        self.addCleanup(tab.destroy)
        tab.render({"ring": Ring(rx=Field(256), rx_max=Field(512), tx=Field(128), tx_max=Field(512),
                                 rx_mini=Field(None, False, "not supported"))})
        tab.controls["ring"]["rx"].set_value(384)
        self.assertEqual(tab.collect("ring"), {"rx": 384})
        self.assertNotIn("rx-mini", tab.controls["ring"])

    def test_eee_uses_helper_keys(self):
        tab = SettingsTab(("eee",), lambda *args: None)
        self.addCleanup(tab.destroy)
        tab.render({"eee": Eee(enabled=Field(True), tx_lpi_us=Field(24))})
        tab.controls["eee"]["eee"].set_active(False)
        self.assertEqual(tab.collect("eee"), {"eee": False})

    def test_wol_can_be_read_with_authorization(self):
        tab = SettingsTab(("wol",), lambda *args: None,
                          wol_reader=lambda _iface: Wol(None, None, False),
                          authorize_wol=lambda _iface: ("pumbg", "d"))
        self.addCleanup(tab.destroy)
        tab.show_interface("eth0")
        tab.render({"wol": Wol(None, None, False)})
        self.assertFalse(tab.controls["wol"]["wol"].get_sensitive())
        buttons = [child for child in tab.body.get_children()[0].get_children()
                   if isinstance(child, Gtk.Button) and child.get_label() == "Read with authorization"]
        self.assertEqual(len(buttons), 1)
        buttons[0].clicked()
        end = time.monotonic() + 2
        while time.monotonic() < end and not tab.controls["wol"]["wol"].get_sensitive():
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(0.01)
        self.assertTrue(tab.controls["wol"]["wol"].get_sensitive())
        self.assertEqual(tab.controls["wol"]["wol"].get_text(), "d")

    def test_driver_field_names_follow_locale(self):
        from ethtool_gui.gui.tabs.base import format_data
        with mock.patch.dict("os.environ", {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "it_IT.UTF-8"}):
            text = format_data(DriverInfo(driver=Field("r8169"), firmware_version=Field("1.0"),
                                          supports_test=False))
        self.assertIn("Versione firmware: 1.0", text)
        self.assertIn("Autotest supportato: No", text)

    def test_persistence_without_backend_is_disabled(self):
        tab = PersistenceTab(lambda **k: {"ok": True})
        self.addCleanup(tab.destroy)
        tab.render_status({"backend": "none", "reason": "no_permanent_mac", "values": {}},
                          {"link": {"autoneg": True}})
        self.assertFalse(tab.make_button.get_sensitive())
        self.assertIn("no_permanent_mac", tab.reason.get_text())

    def test_snapshot_diff_shows_changed_ring(self):
        tab = SnapshotTab()
        self.addCleanup(tab.destroy)
        old = {"iface": "eth0", "ring": {"rx": 256}, "taken_at": "old"}
        new = {"iface": "eth0", "ring": {"rx": 512}, "taken_at": "new"}
        tab.show_diff(old, new)
        self.assertIn("ring.rx", tab.output.get_buffer().get_text(
            tab.output.get_buffer().get_start_iter(), tab.output.get_buffer().get_end_iter(), True))


if __name__ == "__main__":
    unittest.main()
