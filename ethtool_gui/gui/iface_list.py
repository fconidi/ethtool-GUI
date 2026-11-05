"""Left pane: the interface list with link dot, speed and a "show all" switch."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk  # noqa: E402

from ..core.ifaces import Iface  # noqa: E402
from ..i18n import translate as _  # noqa: E402
from . import style  # noqa: E402

_DOT = {"up": "#1fb870", "down": "#c0283c"}
_DOT_OTHER = "#8a8a8a"


def sysfs_speed(name: str, sys_root: Path) -> str:
    """Link speed from sysfs ("1000 Mb/s"), or an empty string when unknown."""
    try:
        value = int((Path(sys_root) / name / "speed").read_text().strip())
    except (OSError, ValueError):
        return ""
    return _("speed_mbps", speed=value) if value > 0 else ""


class IfaceList(Gtk.Box):
    __gsignals__ = {"iface-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
                    "show-all-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,))}

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        style.add_class(self, "iface-pane")
        self.set_size_request(210, -1)
        self._ifaces: dict[str, Iface] = {}
        self._updating = False

        title = Gtk.Label(label=_("interfaces"), xalign=0, margin_start=12, margin_top=10)
        style.add_class(title, "section-title")
        self.pack_start(title, False, False, 0)

        self.store = Gtk.ListStore(str, str, str)  # name, dot colour, speed
        self.view = Gtk.TreeView(model=self.store, headers_visible=False)
        dot = Gtk.CellRendererText(text="●", xpad=8)
        self.view.append_column(Gtk.TreeViewColumn("", dot, foreground=1))
        self.view.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0))
        speed = Gtk.CellRendererText(xalign=1.0, xpad=8)
        col = Gtk.TreeViewColumn("", speed, text=2)
        col.set_expand(True)
        self.view.append_column(col)
        self.view.get_selection().connect("changed", self._on_changed)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add(self.view)
        self.pack_start(scroll, True, True, 0)

        row = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_bottom=10)
        row.pack_start(Gtk.Label(label=_("show_all"), xalign=0, wrap=True), True, True, 0)
        self.show_all_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.show_all_switch.connect("notify::active", self._on_toggle)
        row.pack_end(self.show_all_switch, False, False, 0)
        self.pack_start(row, False, False, 0)

    @property
    def include_virtual(self) -> bool:
        return self.show_all_switch.get_active()

    def names(self) -> list[str]:
        return [row[0] for row in self.store]

    def selected(self) -> Iface | None:
        model, it = self.view.get_selection().get_selected()
        return self._ifaces.get(model[it][0]) if it else None

    def select(self, name: str) -> None:
        for row in self.store:
            if row[0] == name:
                self.view.get_selection().select_iter(row.iter)
                return

    def set_interfaces(self, ifaces: list[Iface], speeds: dict[str, str] | None = None) -> None:
        """Replace the rows, keeping the selection when the interface still exists."""
        speeds = speeds or {}
        current = self.selected()
        self._updating = True
        self.store.clear()
        self._ifaces = {i.name: i for i in ifaces}
        for i in ifaces:
            self.store.append([i.name, _DOT.get(i.operstate, _DOT_OTHER), speeds.get(i.name, "")])
        if current and current.name in self._ifaces:
            self.select(current.name)
        self._updating = False
        if current and current.name not in self._ifaces:
            self.emit("iface-selected", None)

    def _on_changed(self, selection) -> None:
        if not self._updating:
            self.emit("iface-selected", self.selected())

    def _on_toggle(self, switch, _param) -> None:
        self.emit("show-all-toggled", switch.get_active())
