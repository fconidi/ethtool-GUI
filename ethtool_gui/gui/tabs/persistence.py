"""Inspect current and boot settings, and explicitly write or remove a profile."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from .base import Topic, scroll  # noqa: E402
from ...i18n import translate as _  # noqa: E402


class PersistenceTab(Topic):
    def __init__(self, call) -> None:
        super().__init__()
        self.call = call
        self.current: dict = {}
        self.status: dict = {}
        buttons = Gtk.Box(spacing=8)
        self.load_button = Gtk.Button(label=_("read_boot_settings"))
        self.make_button = Gtk.Button(label=_("make_persistent"))
        self.remove_button = Gtk.Button(label=_("remove_persistence"))
        for button in (self.load_button, self.make_button, self.remove_button):
            buttons.pack_start(button, False, False, 0)
        self.pack_start(buttons, False, False, 0)
        self.make_button.set_sensitive(False)
        self.remove_button.set_sensitive(False)
        self.store = Gtk.ListStore(str, str, str, str)
        self.table = Gtk.TreeView(model=self.store)
        for index, title in enumerate((_('setting'), _('current'), _('at_boot'), _('differs'))):
            self.table.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=index))
        self.pack_start(scroll(self.table), True, True, 0)
        self.load_button.connect("clicked", self._load)
        self.make_button.connect("clicked", self._make)
        self.remove_button.connect("clicked", self._remove)

    def show_interface(self, iface: str | None) -> None:
        super().show_interface(iface)
        self.store.clear()
        self.status = {}
        self.current = {}
        self.make_button.set_sensitive(False)
        self.remove_button.set_sensitive(False)

    def render_status(self, status: dict, current: dict) -> None:
        self.status, self.current = status, current
        backend = status.get("backend", "none")
        self.unavailable(status.get("reason") or ("" if backend != "none" else _("no_persistence_backend")))
        self.make_button.set_sensitive(backend != "none" and bool(current))
        self.remove_button.set_sensitive(bool(status.get("managed")))
        self.store.clear()
        saved = status.get("values") or {}
        for group, values in sorted(current.items()):
            if not isinstance(values, dict):
                continue
            for key, value in sorted(values.items()):
                boot = (saved.get(group) or {}).get(key)
                self.store.append([f"{group}.{key}", str(value), "—" if boot is None else str(boot),
                                   _("yes") if boot is not None and boot != value else ""])

    def _run(self, request: dict, done) -> None:
        from ..common import thread_runner
        thread_runner(lambda: self.call(**request),
                      lambda result, error: self.unavailable(str(error)) if error else done(result))

    def _load(self, *_args) -> None:
        if self.iface:
            from ...core.parse_basic import read_link
            from ...core.runner import EthtoolError, run_ethtool
            from ...helper.operations import OPS
            from ..common import thread_runner
            iface = self.iface

            def work():
                current = {}
                try:
                    link = read_link(iface)
                    if link.autoneg.value is True:
                        current["link"] = {"autoneg": True}
                    elif link.autoneg.value is False and link.speed_mbps.value is not None \
                            and link.duplex.value in ("Full", "Half"):
                        current["link"] = {"autoneg": False, "speed": link.speed_mbps.value,
                                           "duplex": link.duplex.value.lower()}
                except EthtoolError:
                    pass
                for group, operation in OPS.items():
                    if group == "link":
                        continue
                    try:
                        values = operation._current(iface, run_ethtool)
                    except (EthtoolError, ValueError):
                        continue
                    if values:
                        current[group] = values
                return self.call(op="status", iface=iface), current

            thread_runner(work, lambda result, error: self.unavailable(str(error)) if error
                          else self.render_status(*result) if iface == self.iface else None)

    def _make(self, *_args) -> None:
        if self.iface and self.current:
            self._run({"op": "persist", "iface": self.iface, "values": self.current},
                      lambda _result: self._load())

    def _remove(self, *_args) -> None:
        if self.iface:
            self._run({"op": "remove", "iface": self.iface},
                      lambda _result: self._load())
