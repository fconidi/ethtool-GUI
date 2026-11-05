"""Stack pages. The simple and advanced pages are filled in by later work."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..core.ifaces import Iface  # noqa: E402
from ..i18n import translate as _  # noqa: E402
from . import style  # noqa: E402


class PlaceholderPage(Gtk.Label):
    """Shows the selected interface name until the real page replaces it."""

    def __init__(self) -> None:
        super().__init__(label=_("select_iface_hint"), wrap=True)
        style.add_class(self, "placeholder")
        self.needs_authorization = False
        self.iface: Iface | None = None

    def show_interface(self, iface: Iface | None) -> None:
        self.iface = iface
        self.set_text(_("select_iface_hint_named", name=iface.name) if iface else _("select_iface_hint"))
