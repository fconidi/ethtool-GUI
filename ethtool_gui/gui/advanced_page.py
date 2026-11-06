"""Advanced ethtool topics with explicit reads and guarded changes."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.ifaces import Iface  # noqa: E402
from ..core.models import LinkInfo  # noqa: E402
from ..core.module import read_module  # noqa: E402
from ..core.parse_basic import read_driver  # noqa: E402
from ..core.parse_settings import read_ntuple, read_rss  # noqa: E402
from ..core.stats import read_stats  # noqa: E402
from ..helper.operations import OPS  # noqa: E402
from ..i18n import translate as _  # noqa: E402
from .common import confirm_dialog, error_text, thread_runner  # noqa: E402
from .countdown import KeepBar  # noqa: E402
from .simple_page import _default_route  # noqa: E402
from .tabs.base import TextTab  # noqa: E402
from .tabs.link import LinkTab  # noqa: E402
from .tabs.offload import OffloadTab  # noqa: E402
from .tabs.persistence import PersistenceTab  # noqa: E402
from .tabs.settings import SettingsTab  # noqa: E402
from .tabs.stats import StatsTab  # noqa: E402
from .tabs.snapshot import SnapshotTab  # noqa: E402
from .tabs.tools import DumpTab, TestTab  # noqa: E402


class AdvancedPage(Gtk.Box):
    names = ["Link", "Driver", "Statistics", "Pause/Ring/Channels", "Coalescing", "Offload",
             "EEE/WoL", "RSS", "SFP", "Tests", "Dump", "Snapshot", "Persistence"]
    tab_keys = ["tab_link", "tab_driver", "tab_statistics", "tab_pause_ring_channels",
                "tab_coalescing", "tab_offload", "tab_eee_wol", "tab_rss", "tab_sfp",
                "tab_tests", "tab_dump", "tab_snapshot", "tab_persistence"]

    def __init__(self, helper_call, persist_call, *, authorize_wol=None, read_wol=None,
                 update_wol=None, on_change=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._helper_call, self._persist_call = helper_call, persist_call
        self._update_wol = update_wol
        self._on_change = on_change
        self._iface: str | None = None
        self._refresh_serial = 0
        self._busy = False
        self._pending: tuple[str, dict] | None = None
        self.needs_authorization = False
        self.notebook = Gtk.Notebook(scrollable=True)
        self.tabs = [
            LinkTab(self.apply_change), TextTab(read_driver), StatsTab(),
            SettingsTab(("pause", "ring", "channels"), self.apply_change),
            SettingsTab(("coalesce",), self.apply_change), OffloadTab(self.apply_change),
            SettingsTab(("eee", "wol"), self.apply_change,
                        wol_reader=read_wol, authorize_wol=authorize_wol),
            TextTab(lambda iface: {_("tab_rss"): read_rss(iface), _("rx_classification_rules"): read_ntuple(iface)},
                    helper_call, "rss"),
            TextTab(read_module, helper_call, "module"), TestTab(helper_call, self._confirm), DumpTab(helper_call),
            SnapshotTab(), PersistenceTab(persist_call),
        ]
        for key, tab in zip(self.tab_keys, self.tabs):
            self.notebook.append_page(tab, Gtk.Label(label=_(key)))
        self.pack_start(self.notebook, True, True, 0)
        self.keep_bar = KeepBar(lambda: self._keep(False), lambda: self._keep(True), self._revert)
        self.pack_start(self.keep_bar, False, False, 0)
        self.notice = Gtk.Label(xalign=0, wrap=True)
        self.notice.set_no_show_all(True)
        self.pack_start(self.notice, False, False, 0)
        self.notebook.connect("switch-page", lambda *_: GLib.idle_add(self.refresh))

    def _confirm(self, text: str, danger: bool = False) -> bool:
        return confirm_dialog(self.get_toplevel(), text, danger)

    def show_interface(self, iface: Iface | str | None) -> None:
        self._iface = iface.name if isinstance(iface, Iface) else iface
        for tab in self.tabs:
            tab.show_interface(self._iface)
        self.refresh()

    def show_reading(self, reading: dict) -> None:
        link = reading.get("link")
        if not isinstance(link, LinkInfo):
            return
        values = {"link": {}}
        if link.autoneg.supported and link.autoneg.value is not None:
            values["link"]["autoneg"] = link.autoneg.value
            if link.autoneg.value is False and link.speed_mbps.value is not None \
                    and link.duplex.value in ("Full", "Half"):
                values["link"].update(speed=link.speed_mbps.value, duplex=link.duplex.value.lower())
        if not values["link"] or (values["link"].get("autoneg") is False
                                   and "speed" not in values["link"]):
            values = {}
        if values:
            self.tabs[12].current["link"] = values["link"]
        else:
            self.tabs[12].current.pop("link", None)
        stats = reading.get("stats")
        if stats is not None and self.notebook.get_current_page() == 2:
            self.tabs[2].show_stats(stats)

    def refresh(self) -> None:
        if not self._iface:
            return
        self._refresh_serial += 1
        serial = self._refresh_serial
        tab = self.tabs[self.notebook.get_current_page()]
        if not hasattr(tab, "load"):
            return
        iface = self._iface

        def done(data, error):
            if (serial != self._refresh_serial or iface != self._iface
                    or tab is not self.tabs[self.notebook.get_current_page()]):
                return
            if error:
                tab.unavailable(str(error))
            else:
                tab.render(data)

        thread_runner(lambda: tab.load(iface), done)

    def apply_change(self, iface: str, op: str, values: dict, *, save: bool = False) -> None:
        if self._busy or iface != self._iface:
            return
        if not values:
            self._message(_("no_changes"))
            return
        risky = OPS[op].is_link_affecting
        if risky:
            warning = _("change_link_warning")
            if os.environ.get("SSH_CONNECTION"):
                warning += "\n" + _("ssh_warning")
            if _default_route(iface):
                warning += "\n" + _("default_route_warning")
            if not self._confirm(warning, True):
                return
        self._busy = True

        def done(reply, error):
            self._busy = False
            if error:
                self._message(error_text(error))
            elif risky:
                self._pending = (iface, {op: values})
                self.keep_bar.begin(reply["token"], reply["seconds"])
            else:
                if op == "wol" and self._update_wol:
                    self._update_wol(iface, values["wol"])
                self._message(_("change_applied"))
                if save:
                    thread_runner(lambda: self._persist_call(op="persist", iface=iface, values={op: values}),
                                  lambda _result, exc: self._message(error_text(exc) if exc else _("saved_for_boot")))
            if not error and self._on_change:
                self._on_change()
            self.refresh()

        thread_runner(lambda: self._helper_call("apply", iface=iface, op=op, values=values), done)

    def _message(self, message: str) -> None:
        self.notice.set_text(message)
        self.notice.show()

    def _keep(self, save: bool) -> None:
        token, pending = self.keep_bar.token, self._pending
        if not token or not pending:
            return
        iface, values = pending

        def done(_reply, error):
            if error:
                self._message(error_text(error))
                return
            self.keep_bar.end()
            self._pending = None
            self._message(_("change_kept"))
            if save:
                thread_runner(lambda: self._persist_call(op="persist", iface=iface, values=values),
                              lambda _result, exc: self._message(error_text(exc) if exc else _("saved_for_boot")))

        thread_runner(lambda: self._helper_call("confirm", token=token), done)

    def _revert(self) -> None:
        token = self.keep_bar.token
        if token:
            thread_runner(lambda: self._helper_call("cancel", token=token),
                          self._reverted)

    def _reverted(self, _result, error) -> None:
        self._message(error_text(error) if error else _("change_reverted"))
        if not error:
            self.refresh()

    def on_helper_event(self, event: dict) -> None:
        if event.get("event") == "reverted" and event.get("token") == self.keep_bar.token:
            self.keep_bar.end()
            self._pending = None
            self._message(_("change_reverted"))
            self.refresh()
