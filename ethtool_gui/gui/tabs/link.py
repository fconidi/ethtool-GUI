"""Link state and editable advertised modes."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ...core.models import LinkInfo  # noqa: E402
from ...core.parse_basic import read_link  # noqa: E402
from ...helper.operations import MODE_BITS, SPEEDS  # noqa: E402
from ...i18n import translate as _  # noqa: E402
from .base import Topic, label, scroll  # noqa: E402


class LinkTab(Topic):
    def __init__(self, apply=None) -> None:
        super().__init__()
        self.reader = read_link
        self.current = LinkInfo()
        self.summary = label("")
        self.pack_start(self.summary, False, False, 0)
        self.autoneg = Gtk.Switch()
        self.autoneg.set_halign(Gtk.Align.START)
        self.pack_start(label(_("autoneg")), False, False, 0)
        self.pack_start(self.autoneg, False, False, 0)
        self.speed = Gtk.ComboBoxText()
        self.speed.set_halign(Gtk.Align.START)
        for n in SPEEDS:
            self.speed.append(str(n), f"{n} Mb/s")
        self.duplex = Gtk.ComboBoxText()
        self.duplex.set_halign(Gtk.Align.START)
        for value in ("half", "full"):
            self.duplex.append(value, _("duplex_" + value))
        self.pack_start(label(_("speed_duplex_manual")), False, False, 0)
        self.pack_start(self.speed, False, False, 0)
        self.pack_start(self.duplex, False, False, 0)
        self.modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.pack_start(label(_("advertised_modes")), False, False, 0)
        self.pack_start(scroll(self.modes), True, True, 0)
        self.mode_checks: dict[str, Gtk.CheckButton] = {}
        self.apply_button = Gtk.Button(label=_("apply_link_settings"))
        self.apply_button.connect("clicked", lambda *_: apply(self.iface, "link", self.collect())
                                  if apply and self.iface else None)
        self.pack_start(self.apply_button, False, False, 0)

    def load(self, iface: str) -> LinkInfo:
        return self.reader(iface)

    def render(self, data: LinkInfo) -> None:
        self.current = data
        state = _("state_" + data.state.value) if data.state.value in ("up", "down") else _("unknown")
        speed = _("speed_mbps", speed=data.speed_mbps.value) if data.speed_mbps.value else _("unknown")
        duplex = _("duplex_" + data.duplex.value.lower()) if data.duplex.value in ("Full", "Half") else _("unknown")
        self.summary.set_text(_("link_summary", state=state, speed=speed, duplex=duplex))
        self.autoneg.set_active(bool(data.autoneg.value))
        self.autoneg.set_sensitive(data.autoneg.supported and data.autoneg.value is not None)
        self.autoneg.set_tooltip_text(data.autoneg.reason)
        self.apply_button.set_sensitive(data.autoneg.supported and data.autoneg.value is not None)
        if data.speed_mbps.value in SPEEDS:
            self.speed.set_active_id(str(data.speed_mbps.value))
        self.duplex.set_active_id((data.duplex.value or "").lower())
        for child in self.modes.get_children():
            self.modes.remove(child)
        self.mode_checks = {}
        supported = data.supported_modes.value or []
        advertised = set(data.advertised_modes.value or [])
        for mode in supported:
            check = Gtk.CheckButton(label=mode)
            check.set_active(mode in advertised)
            check.set_sensitive(mode in MODE_BITS)
            if mode not in MODE_BITS:
                check.set_tooltip_text(_("unsupported_link_mode"))
            self.modes.pack_start(check, False, False, 0)
            self.mode_checks[mode] = check
        self.modes.show_all()

    def collect(self) -> dict:
        enabled = self.autoneg.get_active()
        if not enabled:
            speed, duplex = self.speed.get_active_id(), self.duplex.get_active_id()
            return {"autoneg": False, "speed": int(speed), "duplex": duplex} if speed and duplex else {}
        selected = [mode for mode, check in self.mode_checks.items() if check.get_active() and mode in MODE_BITS]
        if selected:
            return {"autoneg": True, "advertise": "0x%x" % sum(1 << MODE_BITS[m] for m in selected)}
        return {"autoneg": True}
