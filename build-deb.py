#!/usr/bin/python3
"""Build the ethtool-gui Debian package from an explicit file list."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MODULES = (
    "__init__.py", "__main__.py", "i18n.py", "i18n_gui.py",
    "core/__init__.py", "core/cable.py", "core/diagnosis.py", "core/ifaces.py",
    "core/models.py", "core/module.py", "core/parse_basic.py", "core/parse_settings.py",
    "core/protocol.py", "core/runner.py", "core/snapshot.py", "core/stats.py",
    "gui/__init__.py", "gui/advanced_page.py", "gui/app.py", "gui/cable_view.py",
    "gui/common.py", "gui/countdown.py", "gui/graph.py", "gui/helper_client.py",
    "gui/iface_list.py", "gui/simple_page.py", "gui/style.py", "gui/window.py",
    "gui/tabs/__init__.py", "gui/tabs/base.py", "gui/tabs/link.py",
    "gui/tabs/offload.py", "gui/tabs/persistence.py", "gui/tabs/settings.py",
    "gui/tabs/snapshot.py", "gui/tabs/stats.py", "gui/tabs/tools.py",
    "helper/__init__.py", "helper/__main__.py", "helper/commands.py",
    "helper/daemon.py", "helper/operations.py", "helper/readers.py",
    "helper/restore.py", "helper/revert.py",
    "persist/__init__.py", "persist/__main__.py", "persist/common.py",
    "persist/detect.py", "persist/matrix.py", "persist/networkd.py",
    "persist/nm.py", "persist/request.py", "persist/state.py", "persist/udev.py",
)
LIB = "usr/lib/ethtool-gui"
PYTHON_LIB = "usr/lib/python3/dist-packages/ethtool_gui"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build ethtool-GUI for Debian")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    package = source / "ethtool_gui"
    match = re.search(r'__version__ = "([^"]+)"', (package / "__init__.py").read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("package version is missing")
    version = match.group(1)
    with tempfile.TemporaryDirectory(prefix="package-", dir=output) as tmp:
        root = Path(tmp)

        def install(origin: Path, relative: str, executable: bool = False) -> None:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
            target.chmod(0o755 if executable else 0o644)

        for name in MODULES:
            install(package / name, f"{PYTHON_LIB}/{name}")
        for name in ("ethtool-gui", "ethtool-gui-helper", "ethtool-gui-persist", "ethtool-gui-restore"):
            location = f"usr/bin/{name}" if name == "ethtool-gui" else f"{LIB}/{name}"
            install(source / "packaging" / name, location, True)
        for action in ("change", "persist"):
            policy = f"com.syslinuxos.ethtool-gui.{action}.policy"
            install(source / "packaging" / policy, f"usr/share/polkit-1/actions/{policy}")
        install(source / "packaging/ethtool-gui.desktop", "usr/share/applications/ethtool-gui.desktop")
        install(source / "packaging/ethtool-gui.menu", "etc/xdg/menus/applications-merged/ethtool-gui.menu")
        install(source / "data/ethtool-gui.svg", "usr/share/icons/hicolor/scalable/apps/ethtool-gui.svg")
        for name in ("README.md", "CHANGELOG.md", "LICENSE"):
            install(source / name, f"usr/share/doc/ethtool-gui/{name}")
        control = root / "DEBIAN"
        control.mkdir()
        text = (source / "packaging/control").read_text(encoding="utf-8").replace("@VERSION@", version)
        (control / "control").write_text(text, encoding="utf-8")
        root.chmod(0o755)
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        artifact = output / f"ethtool-gui_{version}_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(artifact)], check=True)
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
