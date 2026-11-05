import unittest

from ethtool_gui.core import cable
from ethtool_gui.core.runner import InvalidInterfaceName, NoSuchInterface
from tests.test_parse_basic import ParseBase, fixture


class CableTestTest(ParseBase):
    def test_open_circuit(self):
        run = self.run_for({("--cable-test", "eth0"): (0, fixture("cable-test.txt"), "")})
        r = cable.run_cable_test("eth0", run=run)
        self.assertTrue(r.supported)
        self.assertEqual([p.pair for p in r.pairs], ["A", "B", "C", "D"])
        c = r.pairs[2]
        self.assertEqual(c.status, "Open Circuit")
        self.assertEqual(c.fault_at_m, 37.0)
        self.assertIsNone(c.length_m)
        for p in (r.pairs[0], r.pairs[1], r.pairs[3]):
            self.assertEqual(p.status, "OK")
            self.assertIsNone(p.fault_at_m)

    def test_length_line(self):
        pairs = cable.parse_cable_test("Pair A code OK\nPair A, length: 12.50m\n")
        self.assertEqual((pairs[0].status, pairs[0].length_m), ("OK", 12.5))

    def test_multiword_status(self):
        pairs = cable.parse_cable_test("Pair B code Short to another pair\n")
        self.assertEqual(pairs[0].status, "Short to another pair")

    def test_no_pairs(self):
        run = self.run_for({("--cable-test", "eth0"): (0, "Cable test started for device eth0.\n", "")})
        r = cable.run_cable_test("eth0", run=run)
        self.assertTrue(r.supported)
        self.assertEqual(r.pairs, [])

    def test_unsupported(self):
        run = self.run_for({("--cable-test", "eth0"): (95, "", "netlink error: Operation not supported\n")})
        r = cable.run_cable_test("eth0", run=run)
        self.assertFalse(r.supported)
        self.assertEqual(r.pairs, [])
        self.assertEqual(r.reason, "Operation not supported")

    def test_invalid_name_does_not_run(self):
        def boom(args):
            raise AssertionError("runner called")
        with self.assertRaises(InvalidInterfaceName):
            cable.run_cable_test("-E", run=boom)

    def test_nodevice(self):
        run = self.run_for({("--cable-test", "eth9"): (19, "", fixture("nodevice.txt"))})
        with self.assertRaises(NoSuchInterface):
            cable.run_cable_test("eth9", run=run)


    def test_unknown_status(self):
        self.assertEqual(cable.parse_cable_test("Pair A code Unknown\n")[0].status, "Unknown")

    def test_garbage(self):
        self.assertEqual(cable.parse_cable_test("\x00 junk\nPair\nPair Z code\n"), [])
        run = self.run_for({("--cable-test", "eth0"): (0, "garbage\n", "")})
        self.assertEqual(cable.run_cable_test("eth0", run=run).pairs, [])


if __name__ == "__main__":
    unittest.main()
