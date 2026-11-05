import unittest

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ethtool_gui.core.models import Eee, Feature, FeaturesInfo, Field, LinkInfo, Ring

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

    def test_link_advertised_modes_form_a_mask(self):
        tab = LinkTab()
        self.addCleanup(tab.destroy)
        tab.render(LinkInfo(autoneg=Field(True), supported_modes=Field(["1000baseT/Full", "100baseT/Full"]),
                            advertised_modes=Field(["1000baseT/Full"])))
        self.assertTrue(tab.mode_checks["1000baseT/Full"].get_active())
        tab.mode_checks["100baseT/Full"].set_active(True)
        self.assertEqual(tab.collect(), {"autoneg": True, "advertise": "0x28"})

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
