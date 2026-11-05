"""Main window: interface list on the left, mode switch and pages on the right."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.ifaces import Iface, list_interfaces  # noqa: E402
from ..core.models import Field, LinkInfo, Wol  # noqa: E402
from ..core.parse_basic import read_driver, read_link  # noqa: E402
from ..core.parse_settings import read_wol  # noqa: E402
from ..core.runner import EthtoolMissing  # noqa: E402
from ..core.stats import read_stats  # noqa: E402
from ..i18n import translate as _  # noqa: E402
from . import style  # noqa: E402
from .common import needs_authorization  # noqa: E402
from .helper_client import HelperClient, HelperError  # noqa: E402
from .iface_list import IfaceList, sysfs_speed  # noqa: E402
from .advanced_page import AdvancedPage  # noqa: E402
from .simple_page import SimplePage  # noqa: E402
from .common import persist_call  # noqa: E402

REFRESH_MS = 2000


def default_reader(name: str) -> dict:
    return {"link": read_link(name), "driver": read_driver(name), "stats": read_stats(name)}


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app=None, *, sys_root: Path = Path("/sys/class/net"), reader=None,
                 helper_factory=HelperClient, timers: bool = True) -> None:
        super().__init__(application=app, title=_("app_title"), default_width=980, default_height=640)
        self.sys_root = Path(sys_root)
        self.reader = reader or default_reader
        self._helper_factory = helper_factory
        self._helper = None
        self._timer = 0
        self._busy = False
        self._refresh_requested = False
        self._destroyed = False
        self._current: Iface | None = None
        self.last_reading: dict | None = None
        self._privileged_wol: dict[str, tuple[str, str]] = {}

        style.apply()
        icon = Path(__file__).resolve().parents[2] / "data" / "ethtool-gui.svg"
        if icon.exists():
            Gtk.Window.set_default_icon_from_file(str(icon))
        self.set_icon_name("ethtool-gui")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        self.ethtool_bar = Gtk.InfoBar(no_show_all=True, message_type=Gtk.MessageType.WARNING)
        self.ethtool_label = Gtk.Label(label=_("ethtool_missing"), xalign=0, wrap=True, selectable=True)
        self.ethtool_bar.get_content_area().add(self.ethtool_label)
        outer.pack_start(self.ethtool_bar, False, False, 0)

        body = Gtk.Box()
        outer.pack_start(body, True, True, 0)
        self.iface_list = IfaceList()
        body.pack_start(self.iface_list, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        body.pack_start(content, True, True, 0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.simple_page = SimplePage(self.helper_call, persist_call,
                                      authorize_wol=self.authorized_wol, update_wol=self.update_wol)
        self.advanced_page = AdvancedPage(self.helper_call, persist_call,
                                          authorize_wol=self.authorized_wol,
                                          read_wol=self.read_wol_cached, update_wol=self.update_wol,
                                          on_change=self.refresh)
        self.stack.add_named(self.simple_page, "simple")
        self.stack.add_named(self.advanced_page, "advanced")
        self.stack.show_all()
        self.stack.set_visible_child_name("simple")
        content.pack_start(self._build_mode_switch(), False, False, 0)
        content.pack_start(self.stack, True, True, 0)

        self.iface_list.connect("iface-selected", self._on_selected)
        self.iface_list.connect("show-all-toggled", lambda *_a: self._reload_list())
        self._reload_list()

        self.connect("destroy", self._on_destroy)
        if timers:
            self.connect("map", lambda *_a: self._start_timer())
            self.connect("unmap", lambda *_a: self._stop_timer())

    # -- construction ----------------------------------------------------

    def _build_mode_switch(self) -> Gtk.Widget:
        box = Gtk.Box(halign=Gtk.Align.START)
        box.get_style_context().add_class("linked")
        style.add_class(box, "mode-switch")
        self.mode_buttons: dict[str, Gtk.RadioButton] = {}
        group = None
        for mode, label in (("simple", _("mode_simple")), ("advanced", _("mode_advanced"))):
            button = Gtk.RadioButton.new_with_label_from_widget(group, label)
            button.set_mode(False)
            group = group or button
            button.connect("toggled", self._on_mode, mode)
            self.mode_buttons[mode] = button
            box.pack_start(button, False, False, 0)
        return box

    def _on_mode(self, button: Gtk.RadioButton, mode: str) -> None:
        if button.get_active():
            if mode == "simple" and self.advanced_page.keep_bar.token:
                self.mode_buttons["advanced"].set_active(True)
                self.advanced_page._message(_("confirm_pending_change"))
                return
            if mode == "advanced" and self.simple_page.keep_bar.token:
                self.mode_buttons["simple"].set_active(True)
                self.simple_page._message(_("confirm_pending_change"))
                return
            self.stack.set_visible_child_name(mode)
            self.refresh()

    # -- interface list and selection -------------------------------------

    @property
    def current_iface(self) -> Iface | None:
        return self._current

    def _reload_list(self) -> None:
        ifaces = list_interfaces(self.sys_root, self.iface_list.include_virtual)
        self.iface_list.set_interfaces(ifaces, {i.name: sysfs_speed(i.name, self.sys_root) for i in ifaces})

    def _on_selected(self, _widget, iface: Iface | None) -> None:
        self._current = iface
        self.last_reading = None
        for page in (self.simple_page, self.advanced_page):
            page.needs_authorization = False
            if hasattr(page, "show_interface"):
                page.show_interface(iface)
        if iface is not None:
            self.refresh()

    # -- periodic refresh (reads never run on the UI thread) ---------------

    def refresh(self) -> None:
        if self._destroyed:
            return
        if self._busy:
            self._refresh_requested = True
            return
        self._busy = True
        iface = self._current
        include_virtual = self.iface_list.include_virtual

        def work() -> None:
            ifaces = list_interfaces(self.sys_root, include_virtual)
            speeds = {i.name: sysfs_speed(i.name, self.sys_root) for i in ifaces}
            reading, missing = None, False
            if iface is not None:
                try:
                    reading = self.reader(iface.name)
                except EthtoolMissing:
                    missing = True
                except Exception:  # the device may have vanished; the next tick retries
                    reading = None
            GLib.idle_add(self._apply, ifaces, speeds, iface, reading, missing)

        threading.Thread(target=work, name="reader", daemon=True).start()

    def _apply(self, ifaces, speeds, iface, reading, missing) -> bool:
        if self._destroyed:
            return False
        self._busy = False
        self.iface_list.set_interfaces(ifaces, speeds)
        if missing:
            self.ethtool_bar.show()
            self.ethtool_label.show()
        if reading is not None and iface is not None and self._current is not None \
                and self._current.name == iface.name:
            link = reading.get("link")
            cached = self._privileged_wol.get(iface.name)
            if isinstance(link, LinkInfo) and cached and link.wol_supported.value is None:
                reading = {**reading, "link": replace(link, wol_supported=Field(cached[0]),
                                                       wol_current=Field(cached[1]))}
            self.last_reading = reading
            authorize = needs_authorization(reading)
            for page in (self.simple_page, self.advanced_page):
                page.needs_authorization = authorize
                if hasattr(page, "show_reading"):
                    page.show_reading(reading)
        if self._refresh_requested:
            self._refresh_requested = False
            GLib.idle_add(self.refresh)
        return False

    def _start_timer(self) -> None:
        if not self._timer:
            self._timer = GLib.timeout_add(REFRESH_MS, self._on_tick)

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_tick(self) -> bool:
        for event in self.poll_helper_events():
            self.simple_page.on_helper_event(event)
            self.advanced_page.on_helper_event(event)
        self.refresh()
        return True

    # -- privileged helper (started on the first privileged call) -----------

    def helper_call(self, cmd: str, **args) -> dict:
        if self._helper is None:
            self._helper = self._helper_factory()
        return self._helper.call(cmd, **args)

    def authorized_wol(self, iface: str) -> tuple[str, str]:
        data = self.helper_call("read", iface=iface, what="wol").get("data") or {}
        supported, current = data.get("supported"), data.get("current")
        if not data.get("supported_flag") or not isinstance(supported, str) or not supported \
                or not isinstance(current, str) or not current:
            raise HelperError("wol_unavailable")
        self._privileged_wol[iface] = (supported, current)
        return supported, current

    def read_wol_cached(self, iface: str) -> Wol:
        if iface in self._privileged_wol:
            supported, current = self._privileged_wol[iface]
            return Wol(supported, current, True)
        return read_wol(iface)

    def update_wol(self, iface: str, current: str) -> None:
        if iface in self._privileged_wol:
            supported, _old = self._privileged_wol[iface]
            self._privileged_wol[iface] = (supported, current)

    def poll_helper_events(self) -> list[dict]:
        return self._helper.poll_events() if self._helper is not None else []

    def _on_destroy(self, *_a) -> None:
        self._destroyed = True
        self._stop_timer()
        if self._helper is not None:
            self._helper.close()
            self._helper = None
