import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "ethtool_gui"
BINARIES = {"usr/bin/ethtool-gui", "usr/lib/ethtool-gui/ethtool-gui-helper",
            "usr/lib/ethtool-gui/ethtool-gui-persist", "usr/lib/ethtool-gui/ethtool-gui-restore"}


class PackageTest(unittest.TestCase):
    def test_package_contains_only_program_and_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            built = subprocess.run([sys.executable, str(ROOT / "build-deb.py"), "--output-dir", str(output)],
                                   cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            deb = next(output.glob("ethtool-gui_*_all.deb"))
            listed = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True,
                                    text=True, check=True).stdout
            files = {line.split()[-1].removeprefix("./") for line in listed.splitlines()
                     if line.split()[-1].endswith((".py", ".svg", ".policy", ".desktop", ".menu", ".md", "LICENSE"))
                     or line.split()[-1] in {"./" + name for name in BINARIES}}
            source_modules = {f"usr/lib/python3/dist-packages/{path.relative_to(ROOT)}"
                              for path in (ROOT / PKG).rglob("*.py")}
            expected = source_modules | BINARIES | {
                "usr/share/polkit-1/actions/com.syslinuxos.ethtool-gui.change.policy",
                "usr/share/polkit-1/actions/com.syslinuxos.ethtool-gui.persist.policy",
                "usr/share/applications/ethtool-gui.desktop",
                "etc/xdg/menus/applications-merged/ethtool-gui.menu",
                "usr/share/icons/hicolor/scalable/apps/ethtool-gui.svg",
                "usr/share/doc/ethtool-gui/README.md", "usr/share/doc/ethtool-gui/CHANGELOG.md",
                "usr/share/doc/ethtool-gui/LICENSE",
            }
            self.assertEqual(files, expected)
            self.assertFalse(any(part.lower().startswith(("test", "agents", "claude", "codex", "handoff"))
                                 for name in files for part in name.split("/")))
            control = subprocess.run(["dpkg-deb", "-f", str(deb), "Depends"], capture_output=True,
                                     text=True, check=True).stdout
            self.assertIn("ethtool", control)
            self.assertIn("systemd", control)
            self.assertIn("python3-gi", control)
            for action in ("change", "persist"):
                name = f"com.syslinuxos.ethtool-gui.{action}.policy"
                xml = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True,
                                     check=True).stdout
                import io
                import tarfile
                with tarfile.open(fileobj=io.BytesIO(xml)) as archive:
                    policy = ET.fromstring(archive.extractfile(f"./usr/share/polkit-1/actions/{name}").read())
                path = policy.find(".//annotate[@key='org.freedesktop.policykit.exec.path']").text
                self.assertIn(path.removeprefix("/"), files)
            for name in ("ethtool-gui-helper", "ethtool-gui-persist", "ethtool-gui-restore"):
                text = (ROOT / "packaging" / name).read_text()
                self.assertIn("/usr/bin/python3 -I -P -m", text)
                self.assertNotIn("PYTHONPATH", text)


if __name__ == "__main__":
    unittest.main()
