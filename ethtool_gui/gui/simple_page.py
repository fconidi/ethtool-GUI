"""Simple link status and the few common ethtool actions."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..core.diagnosis import diagnose  # noqa: E402
from ..core.ifaces import Iface  # noqa: E402
from ..core.models import LinkInfo  # noqa: E402
from ..core.stats import RateTracker, Stats, group_of  # noqa: E402
from ..i18n import translate as _  # noqa: E402
from .common import confirm_dialog, error_text, thread_runner  # noqa: E402
from .countdown import KeepBar  # noqa: E402


def _default_route(iface: str) -> bool:
    try:
        result = subprocess.run(["ip", "route", "show", "default"], capture_output=True,
                                text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(parts[i:i + 2] == ["dev", iface]
               for line in result.stdout.splitlines() for parts in [line.split()]
               for i in range(len(parts) - 1))


class SimplePage(Gtk.Box):
    def __init__(self, helper_call: Callable, persist_call: Callable) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=16)
        self._helper_call = helper_call
        self._persist_call = persist_call
        self._iface: str | None = None
        self._link: LinkInfo | None = None
        self._rates = RateTracker()
        self._updating = False
        self._busy = False
        self._pending_values: dict | None = None
        self._pending_iface: str | None = None
        self.needs_authorization = False
        self.ask_confirmation = lambda text, danger=False: confirm_dialog(self.get_toplevel(), text, danger)

        self.title = Gtk.Label(label=_("select_iface_hint"), xalign=0)
        self.title.get_style_context().add_class("section-title")
        self.pack_start(self.title, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.speed = Gtk.Label(xalign=0)
        self.duplex = Gtk.Label(xalign=0)
        self.errors = Gtk.Label(xalign=0)
        self.hint = Gtk.Label(xalign=0, wrap=True)
        for label in (self.status, self.speed, self.duplex, self.errors, self.hint):
            self.pack_start(label, False, False, 0)

        controls = Gtk.Grid(row_spacing=12, column_spacing=18)
        self.autoneg_switch = Gtk.Switch()
        self.wol_switch = Gtk.Switch()
        self.autoneg_switch.connect("notify::active", self._on_autoneg)
        self.wol_switch.connect("notify::active", self._on_wol)
        controls.attach(Gtk.Label(label=_("autoneg"), xalign=0), 0, 0, 1, 1)
        controls.attach(self.autoneg_switch, 1, 0, 1, 1)
        controls.attach(Gtk.Label(label=_("wol"), xalign=0), 0, 1, 1, 1)
        controls.attach(self.wol_switch, 1, 1, 1, 1)
        self.pack_start(controls, False, False, 0)

        actions = Gtk.Box(spacing=8)
        self.blink_button = Gtk.Button(label=_("blink_led"))
        self.cable_button = Gtk.Button(label=_("check_cable"))
        self.blink_button.connect("clicked", self._on_blink)
        self.cable_button.connect("clicked", self._on_cable)
        actions.pack_start(self.blink_button, False, False, 0)
        actions.pack_start(self.cable_button, False, False, 0)
        self.pack_start(actions, False, False, 0)

        self.keep_bar = KeepBar(lambda: self._keep(False), lambda: self._keep(True), self._revert)
        self.pack_start(self.keep_bar, False, False, 0)
        self.notice = Gtk.Label(xalign=0, wrap=True)
        self.notice.set_no_show_all(True)
        self.pack_start(self.notice, False, False, 0)
        self.show_interface(None)

    @property
    def iface(self) -> str | None:
        return self._iface

    def show_interface(self, iface: Iface | str | None) -> None:
        name = iface.name if isinstance(iface, Iface) else iface
        if name != self._iface:
            self._rates = RateTracker()
            self._link = None
        self._iface = name
        self.title.set_text(_("select_iface_hint_named", name=name) if name else _("select_iface_hint"))
        self._updating = True
        self.autoneg_switch.set_sensitive(False)
        self.wol_switch.set_sensitive(False)
        self.blink_button.set_sensitive(bool(name))
        self.cable_button.set_sensitive(bool(name))
        self._updating = False

    def show_reading(self, reading: dict) -> None:
        link = reading.get("link")
        if not isinstance(link, LinkInfo):
            return
        self._link = link
        state = link.state.value if link.state.supported else None
        self.status.set_text(_("link_up") if state == "up" else _("link_down") if state == "down" else _("unknown"))
        self.speed.set_text(_("speed_label", value=_("speed_mbps", speed=link.speed_mbps.value))
                            if link.speed_mbps.value else _("speed_label", value=_("unknown")))
        self.duplex.set_text(_("duplex_label", value=link.duplex.value or _("unknown")))
        stats = reading.get("stats")
        rates = self._rates.update(stats.values, time.monotonic()) if isinstance(stats, Stats) and stats.supported else {}
        error_rate = sum(value for key, value in rates.items() if group_of(key) in ("errors", "drops"))
        self.errors.set_text(_("error_rate", value=f"{error_rate:.1f}"))
        hints = diagnose(link, rates, None, None)
        self.hint.set_text(_("suggestion", text=_(hints[0].key)) if hints else "")
        self._updating = True
        try:
            autoneg = link.autoneg
            can_off = link.speed_mbps.value is not None and link.duplex.value in ("Full", "Half")
            self.autoneg_switch.set_active(bool(autoneg.value))
            self.autoneg_switch.set_sensitive(bool(self._iface) and autoneg.supported and autoneg.value is not None
                                               and (not autoneg.value or can_off) and not self._busy)
            self.autoneg_switch.set_tooltip_text(autoneg.reason or (_("unknown_link_settings") if not can_off else None))
            wol = link.wol_supported
            self.wol_switch.set_active(link.wol_current.value not in (None, "d"))
            self.wol_switch.set_sensitive(bool(self._iface) and wol.supported and bool(wol.value)
                                          and "g" in wol.value and not self._busy)
            self.wol_switch.set_tooltip_text(wol.reason or (_("wol_unsupported") if not wol.value or "g" not in wol.value else None))
        finally:
            self._updating = False

    def _run(self, cmd: str, done: Callable, **args) -> None:
        if self._busy:
            return
        self._busy = True

        def finished(result, error):
            self._busy = False
            if error:
                self._message(error_text(error))
                if self._link:
                    self.show_reading({"link": self._link})
            else:
                done(result)

        thread_runner(lambda: self._helper_call(cmd, **args), finished)

    def _message(self, text: str) -> None:
        self.notice.set_text(text)
        self.notice.show()

    def _on_autoneg(self, switch, _param) -> None:
        if self._updating or not self._iface or not self._link:
            return
        enabled = switch.get_active()
        if self._busy or (not enabled and (self._link.speed_mbps.value is None
                                           or self._link.duplex.value not in ("Full", "Half"))):
            self.show_reading({"link": self._link})
            return
        values = {"autoneg": enabled}
        if not enabled:
            values.update(speed=self._link.speed_mbps.value, duplex=self._link.duplex.value.lower())
        warning = _("change_link_warning")
        if os.environ.get("SSH_CONNECTION"):
            warning += "\n" + _("ssh_warning")
        if _default_route(self._iface):
            warning += "\n" + _("default_route_warning")
        if not self.ask_confirmation(warning, True):
            self.show_reading({"link": self._link})
            return
        iface = self._iface
        self._run("apply", lambda reply: self._show_keep(reply, iface, {"link": values}),
                  iface=iface, op="link", values=values)

    def _show_keep(self, reply: dict, iface: str, values: dict) -> None:
        self._pending_values = values
        self._pending_iface = iface
        self.keep_bar.begin(reply["token"], reply["seconds"])

    def _on_wol(self, switch, _param) -> None:
        if self._updating or not self._iface:
            return
        self._run("apply", lambda _reply: self._message(_("change_applied")), iface=self._iface,
                  op="wol", values={"wol": "g" if switch.get_active() else "d"})

    def _on_blink(self, *_args) -> None:
        if self._iface:
            self._run("blink", lambda _reply: self._message(_("blink_done")), iface=self._iface, seconds=3)

    def _on_cable(self, *_args) -> None:
        if self._iface and self.ask_confirmation(_("cable_warning"), True):
            self._run("cable_test", lambda _reply: self._message(_("cable_done")),
                      iface=self._iface, confirmed=True)

    def _keep(self, save: bool) -> None:
        token, iface, values = self.keep_bar.token, self._pending_iface, self._pending_values
        if token is None or iface is None or values is None:
            return

        def confirmed(_reply):
            self.keep_bar.end()
            self._pending_values = None
            self._pending_iface = None
            self._message(_("change_kept"))
            if save:
                thread_runner(lambda: self._persist_call(op="persist", iface=iface, values=values),
                              lambda _result, error: self._message(error_text(error) if error else _("saved_for_boot")))

        self._run("confirm", confirmed, token=token)

    def _revert(self) -> None:
        token = self.keep_bar.token
        if token:
            self._run("cancel", lambda _reply: self._message(_("change_reverted")), token=token)

    def on_helper_event(self, event: dict) -> None:
        if event.get("event") == "reverted" and event.get("token") == self.keep_bar.token:
            self.keep_bar.end()
            self._pending_values = None
            self._pending_iface = None
            self._message(_("change_reverted"))
