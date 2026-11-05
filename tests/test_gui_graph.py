import unittest

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ethtool_gui.core.cable import PairResult

HAVE_DISPLAY = Gtk.init_check()[0]

if HAVE_DISPLAY:
    from ethtool_gui.gui.graph import RateGraph
    from ethtool_gui.gui.cable_view import CableView
    from ethtool_gui.gui.window import MainWindow


@unittest.skipUnless(HAVE_DISPLAY, "no display")
class DrawingTest(unittest.TestCase):
    def test_history_is_bounded_and_negative_rates_are_rejected(self):
        graph = RateGraph()
        self.addCleanup(graph.destroy)
        for n in range(150):
            graph.push({"rx_crc_errors": float(n), "tx_pause": 2.0})
        self.assertEqual(len(graph.history), 120)
        self.assertEqual(graph.history[-1]["crc"], 149.0)
        self.assertEqual(graph.history[-1]["pause"], 2.0)
        with self.assertRaises(ValueError):
            graph.push({"rx_errors": -0.1})
        window = Gtk.OffscreenWindow()
        self.addCleanup(window.destroy)
        window.add(graph)
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.assertIsNotNone(window.get_pixbuf())

    def test_open_pair_distance_is_drawn(self):
        view = CableView()
        window = Gtk.OffscreenWindow()
        self.addCleanup(window.destroy)
        window.add(view)
        view.set_results([PairResult("A", "Open Circuit", fault_at_m=12.5)])
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.assertIn("12.5 m", view.labels)
        self.assertIsNotNone(window.get_pixbuf())

    def test_refresh_timer_stops_when_window_unmaps(self):
        window = MainWindow(None, timers=True)
        self.addCleanup(window.destroy)
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.assertNotEqual(window._timer, 0)
        window.hide()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.assertEqual(window._timer, 0)


if __name__ == "__main__":
    unittest.main()
