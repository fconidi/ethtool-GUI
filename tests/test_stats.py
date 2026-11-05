import unittest

from ethtool_gui.core import stats
from ethtool_gui.core.runner import InvalidInterfaceName, NoSuchInterface
from tests.test_parse_basic import ParseBase, fixture


class ReadStatsTest(ParseBase):
    def test_fixture(self):
        run = self.run_for({("-S", "eno1"): (0, fixture("stats-e1000e.txt"), "")})
        s = stats.read_stats("eno1", run=run)
        self.assertTrue(s.supported)
        self.assertEqual(s.values["rx_crc_errors"], 2)
        self.assertEqual(s.values["rx_packets"], 1843921)
        self.assertNotIn("NIC statistics", s.values)

    def test_skips_non_integers(self):
        run = self.run_for({("-S", "eth0"): (0, "NIC statistics:\n     a: 5\n     b: n/a\n     c: -3\n", "")})
        self.assertEqual(stats.read_stats("eth0", run=run).values, {"a": 5})

    def test_unsupported(self):
        run = self.run_for({("-S", "eth0"): (95, "", "netlink error: Operation not supported\n")})
        s = stats.read_stats("eth0", run=run)
        self.assertFalse(s.supported)
        self.assertEqual(s.values, {})
        self.assertEqual(s.reason, "Operation not supported")

    def test_invalid_name_does_not_run(self):
        def boom(args):
            raise AssertionError("runner called")
        with self.assertRaises(InvalidInterfaceName):
            stats.read_stats("-E", run=boom)

    def test_nodevice(self):
        run = self.run_for({("-S", "eth9"): (19, "", fixture("nodevice.txt"))})
        with self.assertRaises(NoSuchInterface):
            stats.read_stats("eth9", run=run)


class RateTrackerTest(unittest.TestCase):
    def test_first_empty_then_rate(self):
        t = stats.RateTracker()
        self.assertEqual(t.update({"a": 100}, 10.0), {})
        self.assertEqual(t.update({"a": 120}, 12.0), {"a": 10.0})

    def test_counter_down_is_zero_and_rebases(self):
        t = stats.RateTracker()
        t.update({"a": 100}, 0.0)
        self.assertEqual(t.update({"a": 5}, 1.0), {"a": 0.0})
        self.assertEqual(t.update({"a": 15}, 2.0), {"a": 10.0})

    def test_time_not_advancing(self):
        t = stats.RateTracker()
        t.update({"a": 100}, 5.0)
        self.assertEqual(t.update({"a": 200}, 5.0), {})
        self.assertEqual(t.update({"a": 200}, 4.0), {})
        self.assertEqual(t.update({"a": 150}, 6.0), {"a": 50.0})

    def test_only_common_names(self):
        t = stats.RateTracker()
        t.update({"a": 1, "b": 1}, 0.0)
        self.assertEqual(t.update({"b": 3, "c": 9}, 1.0), {"b": 2.0})
        self.assertEqual(t.update({"c": 11}, 2.0), {"c": 2.0})


class GroupTest(unittest.TestCase):
    def test_groups(self):
        cases = {
            "rx_crc_errors": "errors", "rx_errors": "errors", "tx_pause": "pause",
            "rx_flow_control_xon": "pause", "tx_flow_control_xoff": "pause",
            "rx_dropped": "drops", "tx_dropped": "drops", "rx_missed_errors": "drops",
            "rx_no_buffer_count": "drops", "collisions": "collisions",
            "tx_late_collisions": "collisions", "rx_packets": "packets",
            "tx_packets": "packets", "rx_bytes": "bytes", "tx_bytes": "bytes",
            "rx_broadcast": "other",
        }
        for name, group in cases.items():
            self.assertEqual(stats.group_of(name), group, name)


if __name__ == "__main__":
    unittest.main()
