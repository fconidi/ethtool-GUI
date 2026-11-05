import re
import subprocess
import sys
import unittest
from string import Formatter
from pathlib import Path

from ethtool_gui import __version__
from ethtool_gui.i18n import CATALOG, translate

ROOT = Path(__file__).resolve().parent.parent
USED = re.compile(r"""(?<![\w.])(?:translate|_)\(\s*["']([\w.]+)["'](?!\s*\+)""")


class I18nTest(unittest.TestCase):
    def test_every_key_used_in_gui_exists_in_both_catalogs(self):
        keys = set()
        for path in (ROOT / "ethtool_gui" / "gui").rglob("*.py"):
            keys |= set(USED.findall(path.read_text()))
        self.assertTrue(keys)
        for lang in ("en", "it"):
            self.assertEqual(sorted(k for k in keys if k not in CATALOG[lang]), [], lang)

    def test_diagnosis_keys_present_as_suggestions(self):
        for key in ("diag.low_speed", "diag.duplex_mismatch", "diag.crc_errors", "diag.collisions",
                    "diag.pause_active", "diag.sfp_rx_low", "diag.link_down"):
            for lang in ("en", "it"):
                self.assertTrue(len(CATALOG[lang][key]) > 25, (key, lang))
        self.assertTrue(CATALOG["en"]["diag.low_speed"].startswith("Possible"))
        self.assertTrue(CATALOG["it"]["diag.low_speed"].startswith("Possibile"))

    def test_catalogs_have_same_keys(self):
        for lang in ("it", "fr", "de", "es"):
            self.assertEqual(set(CATALOG["en"]), set(CATALOG[lang]), lang)

    def test_catalog_placeholders_match(self):
        def fields(value):
            return {name for _, name, _, _ in Formatter().parse(value) if name}

        for lang in ("it", "fr", "de", "es"):
            for key, english in CATALOG["en"].items():
                self.assertEqual(fields(english), fields(CATALOG[lang][key]), (lang, key))

    def test_constructed_advanced_keys_exist(self):
        from ethtool_gui.gui.tabs.base import FIELD_LABELS
        keys = {"duplex_half", "duplex_full", "state_up", "state_down"}
        keys.update(FIELD_LABELS.values())
        keys.update("graph_" + name for name in ("errors", "crc", "collisions", "pause"))
        keys.update("group_" + name for name in ("pause", "ring", "channels", "coalesce", "eee", "wol"))
        for lang, catalog in CATALOG.items():
            self.assertFalse(keys - catalog.keys(), lang)

    def test_language_selection_and_unknown_fallback(self):
        from unittest.mock import patch
        from ethtool_gui.i18n import current_language

        for lang in ("fr", "de", "es"):
            with patch.dict("os.environ", {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": f"{lang}_XX.UTF-8"}):
                self.assertEqual(current_language(), lang)
        with patch.dict("os.environ", {"LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": "zz_ZZ.UTF-8"}):
            self.assertEqual(current_language(), "en")

    def test_fallbacks_and_fields(self):
        self.assertEqual(translate("show_all", lang="xx"), CATALOG["en"]["show_all"])
        self.assertEqual(translate("no.such.key", lang="it"), "no.such.key")
        self.assertIn("eth0", translate("select_iface_hint_named", lang="en", name="eth0"))

    def test_cli_version_and_help_need_no_display(self):
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)}
        out = subprocess.run([sys.executable, "-m", "ethtool_gui", "--version"], capture_output=True,
                             text=True, env=env, cwd=ROOT, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(__version__, out.stdout)
        out = subprocess.run([sys.executable, "-m", "ethtool_gui", "--help"], capture_output=True,
                             text=True, env=env, cwd=ROOT, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
