import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ethtool_gui.helper import restore, revert
from tests.fakes import make_fake_ethtool, read_calls

TOKEN = "0123456789abcdef"
BACK = ["-s", "eth0", "autoneg", "on", "advertise", "0x2f"]


class TokenTest(unittest.TestCase):
    def test_regex(self):
        self.assertTrue(revert.valid_token(TOKEN))
        self.assertTrue(revert.valid_token(revert.new_token()))
        for bad in ("../x", "../0123456789abc", TOKEN.upper(), TOKEN[:-1], TOKEN + "0",
                    TOKEN + "\n", None, 12):
            self.assertFalse(revert.valid_token(bad), bad)


class RestoreMainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fake = self.root / "bin"
        self.fake.mkdir()
        self.state = self.root / "state"
        script = make_fake_ethtool(self.fake, {tuple(BACK): (0, "", "")})
        for name, value in (("ETHTOOL_BIN", str(script)), ("STATE_DIR", self.state)):
            patcher = mock.patch.object(restore, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.err = io.StringIO()
        quiet = contextlib.redirect_stderr(self.err)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def write(self, iface="eth0", op="link", saved=None):
        revert.write_state(self.state, TOKEN, iface, op,
                           saved if saved is not None else {"autoneg": True, "advertise": "0x2f"})
        return self.state / f"{TOKEN}.json"

    def test_state_dir_and_file_modes(self):
        path = self.write()
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_state_file_is_exclusive(self):
        self.write()
        with self.assertRaises(FileExistsError):
            self.write()

    def test_restores_and_removes_state(self):
        path = self.write()
        self.assertEqual(restore.main([TOKEN]), 0)
        self.assertEqual(read_calls(self.fake), [BACK])
        self.assertFalse(path.exists())

    def test_unknown_or_bad_token(self):
        self.write()
        for argv in ([], ["fedcba9876543210"], ["../x"], [TOKEN, TOKEN], ["--help"]):
            self.assertEqual(restore.main(argv), 2, argv)
        self.assertEqual(read_calls(self.fake), [])
        self.assertTrue((self.state / f"{TOKEN}.json").exists())

    def test_tampered_state_is_refused(self):
        for iface, op, saved in (("-E", "link", {"autoneg": True}),
                                 ("eth0", "flash", {"autoneg": True}),
                                 ("eth0", "link", {"autoneg": True, "advertise": "0x1 msglvl"})):
            path = self.write(iface, op, saved)
            self.assertEqual(restore.main([TOKEN]), 2)
            self.assertTrue(path.exists())
            path.unlink()
        self.assertEqual(read_calls(self.fake), [])

    def test_loose_permissions_refused(self):
        path = self.write()
        os.chmod(path, 0o644)
        self.assertEqual(restore.main([TOKEN]), 2)
        self.assertEqual(read_calls(self.fake), [])

    def test_symlinked_state_refused(self):
        target = self.root / "elsewhere.json"
        target.write_text(json.dumps({"iface": "eth0", "op": "link",
                                      "saved": {"autoneg": True}, "created_at": 0}))
        os.chmod(target, 0o600)
        self.state.mkdir(mode=0o700)
        os.symlink(target, self.state / f"{TOKEN}.json")
        self.assertEqual(restore.main([TOKEN]), 2)
        self.assertEqual(read_calls(self.fake), [])

    def test_invalid_saved_values_exit_2_and_keep_file(self):
        for saved in ({"autoneg": "on"}, {"autoneg": False, "speed": 100}, {"rx": True}):
            path = self.write(saved=saved)
            self.assertEqual(restore.main([TOKEN]), 2, saved)
            self.assertTrue(path.exists())
            path.unlink()
        self.assertEqual(read_calls(self.fake), [])

    def test_symlinked_state_dir_refused(self):
        real = self.root / "real"
        real.mkdir(mode=0o700)
        os.symlink(real, self.state)
        with self.assertRaises(revert.StateError):
            self.write()
        self.assertEqual(list(real.iterdir()), [])

    def test_state_dir_owned_by_someone_else_refused(self):
        self.state.mkdir(mode=0o700)
        with mock.patch.object(revert.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(revert.StateError):
                self.write()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_ethtool_failure_exit_1_and_state_removed(self):
        path = self.write(saved={"autoneg": False, "speed": 10, "duplex": "full"})
        self.assertEqual(restore.main([TOKEN]), 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
