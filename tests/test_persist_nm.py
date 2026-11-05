import unittest
from pathlib import Path

from ethtool_gui.persist import nm
from ethtool_gui.persist.common import Env, PersistError
from tests.fakes import FakeRunner

UUID = "5f3c2a1e-1111-4222-8333-944455556666"
ETH = "802-3-ethernet."
PREVIOUS = {ETH + "auto-negotiate": "no", ETH + "speed": "0", ETH + "duplex": "",
            ETH + "wake-on-lan": "phy, magic"}
FORCED = {"link": {"autoneg": False, "speed": 1000, "duplex": "full"}}


def nm_runner(previous=None, version="1.52.0", modify=(0, "", ""), invalid=()):
    table = dict(PREVIOUS, **(previous or {}))

    def respond(argv):
        if argv == ["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "eth0"]:
            return 0, UUID + "\n", ""
        if argv == ["nmcli", "--version"]:
            return 0, f"nmcli tool, version {version}\n", ""
        if argv[:2] == ["nmcli", "-g"] and argv[3:] == ["connection", "show", UUID]:
            if argv[2] in invalid:
                return 2, "", f"Error: invalid field '{argv[2]}'; allowed fields: ...\n"
            return 0, table.get(argv[2], "") + "\n", ""
        if argv[:4] == ["nmcli", "connection", "modify", UUID]:
            return modify
        return 99, "", "unexpected\n"
    return FakeRunner(respond)


def env(run):
    return Env(run=run, link_dir=Path("/nonexistent"), sys_root=Path("/nonexistent"))


def modifies(run):
    return [c for c in run.calls if c[:3] == ["nmcli", "connection", "modify"]]


def pairs(argv):
    return dict(zip(argv[4::2], argv[5::2]))


class PersistTest(unittest.TestCase):
    def test_profile_by_uuid_not_by_name(self):
        run = nm_runner()
        nm.persist("eth0", FORCED, env(run))
        (cmd,) = modifies(run)
        self.assertEqual(cmd[3], UUID)
        for call in run.calls:
            if "connection" in call:
                self.assertNotIn("eth0", call)

    def test_forced_speed_in_one_command(self):
        run = nm_runner()
        out = nm.persist("eth0", FORCED, env(run))
        (cmd,) = modifies(run)
        self.assertEqual(pairs(cmd), {ETH + "auto-negotiate": "no", ETH + "speed": "1000",
                                      ETH + "duplex": "full"})
        self.assertEqual(out.written, ["link.autoneg", "link.speed", "link.duplex"])

    def test_autoneg_on_clears_speed_and_duplex(self):
        run = nm_runner()
        nm.persist("eth0", {"link": {"autoneg": True}}, env(run))
        (cmd,) = modifies(run)
        self.assertEqual(pairs(cmd), {ETH + "auto-negotiate": "yes", ETH + "speed": "0",
                                      ETH + "duplex": ""})

    def test_never_brings_the_connection_up(self):
        run = nm_runner()
        nm.persist("eth0", dict(FORCED, wol={"wol": "g"}), env(run))
        nm.remove(nm.persist("eth0", FORCED, env(run)).entry, env(run))
        for call in run.calls:
            for word in ("up", "down", "reload", "reapply", "delete"):
                self.assertNotIn(word, call)

    def test_property_mapping(self):
        run = nm_runner()
        values = {"wol": {"wol": "gp"}, "features": {"tso": False, "tx-checksum-ipv4": True},
                  "ring": {"rx": 512, "rx-jumbo": 64}, "pause": {"rx": True, "tx": False},
                  "coalesce": {"rx-usecs": 50, "adaptive-rx": True}}
        nm.persist("eth0", values, env(run))
        (cmd,) = modifies(run)
        self.assertEqual(pairs(cmd), {
            ETH + "wake-on-lan": "magic,phy", "ethtool.feature-tso": "off",
            "ethtool.feature-tx-checksum-ipv4": "on", "ethtool.ring-rx": "512",
            "ethtool.ring-rx-jumbo": "64", "ethtool.pause-rx": "on", "ethtool.pause-tx": "off",
            "ethtool.coalesce-rx-usecs": "50", "ethtool.coalesce-adaptive-rx": "1"})

    def test_wol_disable(self):
        run = nm_runner()
        nm.persist("eth0", {"wol": {"wol": "d"}}, env(run))
        self.assertEqual(pairs(modifies(run)[0]), {ETH + "wake-on-lan": "none"})

    def test_unsupported_fields_are_listed_not_written(self):
        run = nm_runner()
        values = {"channels": {"combined": 4}, "eee": {"eee": True},
                  "link": {"advertise": "0x2f"}, "coalesce": {"cqe-mode-rx": True},
                  "wol": {"wol": "s"}, "ring": {"tx": 256}}
        out = nm.persist("eth0", values, env(run))
        self.assertEqual(sorted(out.unsupported), sorted(
            ["channels.combined", "eee", "link.advertise", "coalesce.cqe-mode-rx", "wol"]))
        self.assertEqual(out.written, ["ring.tx"])
        self.assertEqual(pairs(modifies(run)[0]), {"ethtool.ring-tx": "256"})

    def test_old_networkmanager(self):
        run = nm_runner(version="1.22.0")
        out = nm.persist("eth0", {"pause": {"rx": True}, "ring": {"rx": 1}, "features": {"gro": True}},
                         env(run))
        self.assertEqual(sorted(out.unsupported), ["pause.rx", "ring.rx"])
        self.assertEqual(pairs(modifies(run)[0]), {"ethtool.feature-gro": "on"})

    def test_property_unknown_to_installed_nmcli(self):
        run = nm_runner(invalid=("ethtool.ring-rx",))
        out = nm.persist("eth0", {"ring": {"rx": 1, "tx": 2}}, env(run))
        self.assertEqual(out.unsupported, ["ring.rx"])
        self.assertEqual(pairs(modifies(run)[0]), {"ethtool.ring-tx": "2"})

    def test_nothing_supported_runs_no_modify(self):
        run = nm_runner()
        out = nm.persist("eth0", {"eee": {"eee": True}}, env(run))
        self.assertEqual(modifies(run), [])
        self.assertEqual(out.written, [])
        self.assertIsNone(out.entry)

    def test_previous_values_recorded_once(self):
        run = nm_runner()
        first = nm.persist("eth0", FORCED, env(run)).entry
        self.assertEqual(first["uuid"], UUID)
        self.assertEqual(first["previous"], {ETH + "auto-negotiate": "no", ETH + "speed": "0",
                                             ETH + "duplex": ""})
        run2 = nm_runner(previous={ETH + "auto-negotiate": "no", ETH + "speed": "1000",
                                   ETH + "duplex": "full"})
        second = nm.persist("eth0", {"link": {"autoneg": True}, "wol": {"wol": "g"}},
                            env(run2), first).entry
        self.assertEqual(second["previous"], dict(first["previous"],
                                                  **{ETH + "wake-on-lan": "phy,magic"}))
        self.assertEqual(second["values"], {"link": {"autoneg": True}, "wol": {"wol": "g"}})

    def test_commit_called_before_modify(self):
        run = nm_runner()
        seen = []
        nm.persist("eth0", FORCED, env(run), commit=lambda e: seen.append(len(modifies(run))))
        self.assertEqual(seen, [0])

    def test_unexpected_previous_value_refused(self):
        run = nm_runner(previous={ETH + "speed": "1000; reboot"})
        with self.assertRaises(PersistError) as ctx:
            nm.persist("eth0", FORCED, env(run))
        self.assertEqual(ctx.exception.code, "unexpected_value")
        self.assertEqual(modifies(run), [])

    def test_modify_failure_is_coded(self):
        run = nm_runner(modify=(4, "", "Error: failed to modify 802-3-ethernet.speed: bad\nmore\n"))
        with self.assertRaises(PersistError) as ctx:
            nm.persist("eth0", FORCED, env(run))
        self.assertEqual(ctx.exception.code, "nmcli_failed")
        self.assertEqual(ctx.exception.reason, "Error: failed to modify 802-3-ethernet.speed: bad")

    def test_profile_changed(self):
        entry = nm.persist("eth0", FORCED, env(nm_runner())).entry
        entry = dict(entry, uuid="00000000-0000-4000-8000-000000000000")
        with self.assertRaises(PersistError) as ctx:
            nm.persist("eth0", FORCED, env(nm_runner()), entry)
        self.assertEqual(ctx.exception.code, "profile_changed")


class RemoveTest(unittest.TestCase):
    def test_restores_previous_values(self):
        entry = nm.persist("eth0", dict(FORCED, wol={"wol": "g"}), env(nm_runner())).entry
        run = nm_runner()
        nm.remove(entry, env(run))
        (cmd,) = modifies(run)
        self.assertEqual(cmd[3], UUID)
        self.assertEqual(pairs(cmd), {ETH + "auto-negotiate": "no", ETH + "speed": "0",
                                      ETH + "duplex": "", ETH + "wake-on-lan": "phy,magic"})

    def test_profile_gone_is_not_an_error(self):
        entry = nm.persist("eth0", FORCED, env(nm_runner())).entry
        run = nm_runner(modify=(10, "", f"Error: unknown connection '{UUID}'.\n"))
        nm.remove(entry, env(run))

    def test_other_failure(self):
        entry = nm.persist("eth0", FORCED, env(nm_runner())).entry
        with self.assertRaises(PersistError) as ctx:
            nm.remove(entry, env(nm_runner(modify=(1, "", "Error: permission denied\n"))))
        self.assertEqual(ctx.exception.code, "nmcli_failed")


class PreviousValueTest(unittest.TestCase):
    def test_validation(self):
        good = [(ETH + "auto-negotiate", "yes"), (ETH + "speed", "100"), (ETH + "duplex", ""),
                (ETH + "wake-on-lan", "default"), (ETH + "wake-on-lan", "phy,magic"),
                (ETH + "wake-on-lan", "0x40"), ("ethtool.feature-tso", ""),
                ("ethtool.feature-tso", "on"), ("ethtool.ring-rx", "4096")]
        bad = [(ETH + "auto-negotiate", "maybe"), (ETH + "speed", "-1"), (ETH + "duplex", "x"),
               (ETH + "wake-on-lan", "magic;ls"), ("ethtool.ring-rx", "a b"),
               ("connection.id", "x"), ("ethtool.feature-tso", "on\n")]
        for prop, value in good:
            self.assertTrue(nm.valid_previous(prop, value), (prop, value))
        for prop, value in bad:
            self.assertFalse(nm.valid_previous(prop, value), (prop, value))


if __name__ == "__main__":
    unittest.main()
