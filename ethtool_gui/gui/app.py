"""Application object."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from .window import MainWindow  # noqa: E402


class EthtoolApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.syslinuxos.EthtoolGui")

    def do_activate(self) -> None:
        window = self.get_active_window() or MainWindow(self)
        window.show_all()
        window.present()


def run() -> int:
    Gtk.Window.set_default_icon_name("ethtool-gui")
    return EthtoolApp().run([])
