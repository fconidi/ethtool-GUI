"""Command line entry point: ``python3 -m ethtool_gui``."""

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ethtool-gui", description="Graphical front end for ethtool.")
    parser.add_argument("--version", action="version", version=f"ethtool-gui {__version__}")
    parser.parse_args(argv)

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    from .gui.app import run

    GLib.set_prgname("ethtool-gui")
    GLib.set_application_name("Ethtool-GUI")
    if not Gtk.init_check()[0]:
        print("ethtool-gui: no display available", file=sys.stderr)
        return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
