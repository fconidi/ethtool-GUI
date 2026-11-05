"""Editable pause, ring, channel, coalescing, EEE and WoL settings."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ...core.models import Field  # noqa: E402
from ...core import parse_settings  # noqa: E402
from ...helper.operations import OPS  # noqa: E402
from .base import Topic, label, scroll  # noqa: E402


READERS = {
    "pause": parse_settings.read_pause,
    "ring": parse_settings.read_ring,
    "channels": parse_settings.read_channels,
    "coalesce": parse_settings.read_coalesce,
    "eee": parse_settings.read_eee,
    "wol": parse_settings.read_wol,
}


class SettingsTab(Topic):
    def __init__(self, groups: tuple[str, ...], apply) -> None:
        super().__init__()
        self.groups = groups
        self.apply = apply
        self.controls: dict[str, dict[str, Gtk.Widget]] = {}
        self.original: dict[str, dict] = {}
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.pack_start(scroll(self.body), True, True, 0)

    def load(self, iface: str) -> dict:
        return {group: READERS[group](iface) for group in self.groups}

    def render(self, data: dict) -> None:
        for child in self.body.get_children():
            self.body.remove(child)
        self.controls.clear()
        self.original.clear()
        for group in self.groups:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            section.pack_start(label(group.title()), False, False, 0)
            self.body.pack_start(section, False, False, 0)
            obj = data.get(group)
            if obj is None:
                continue
            self.controls[group], self.original[group] = {}, {}
            fields = obj.values if group == "coalesce" else vars(obj)
            if group == "wol":
                entry = Gtk.Entry(text=obj.current or "")
                entry.set_sensitive(obj.supported_flag)
                if not obj.supported_flag:
                    entry.set_tooltip_text("Wake-on-LAN is not reported by this driver")
                section.pack_start(label("Wake-on-LAN modes (p, u, m, b, a, g, s; d disables)"), False, False, 0)
                section.pack_start(entry, False, False, 0)
                self.controls[group]["wol"] = entry
                self.original[group]["wol"] = obj.current
            elif group == "eee":
                for key, raw, value in (("eee", obj.enabled, obj.enabled.value),
                                        ("tx-lpi", obj.tx_lpi_us, obj.tx_lpi_us.value is not None)):
                    control = Gtk.Switch()
                    control.set_active(bool(value))
                    control.set_sensitive(raw.supported)
                    control.set_tooltip_text(raw.reason)
                    row = Gtk.Box(spacing=12)
                    row.pack_start(label(key.upper()), True, True, 0)
                    row.pack_start(control, False, False, 0)
                    section.pack_start(row, False, False, 0)
                    self.controls[group][key] = control
                    self.original[group][key] = bool(value)
            else:
                for key, raw in fields.items():
                    if key.endswith("_max") or key in ("active", "supported_modes", "advertised_modes", "partner_modes"):
                        continue
                    if group == "coalesce" and key in ("supported", "reason"):
                        continue
                    field = raw if isinstance(raw, Field) else Field(raw)
                    if not field.supported:
                        unavailable = label(f"{key}: {field.reason or 'not supported'}")
                        unavailable.set_sensitive(False)
                        section.pack_start(unavailable, False, False, 0)
                        continue
                    if field.value is None and field.supported:
                        continue
                    if isinstance(field.value, bool):
                        control = Gtk.Switch()
                        control.set_active(field.value)
                    elif isinstance(field.value, int):
                        maximum = getattr(obj, key + "_max", None)
                        maximum = maximum.value if isinstance(maximum, Field) else 10_000_000
                        control = Gtk.SpinButton.new_with_range(0, maximum or 10_000_000, 1)
                        control.set_value(field.value)
                    else:
                        continue
                    control.set_sensitive(field.supported)
                    control.set_tooltip_text(field.reason)
                    row = Gtk.Box(spacing=12)
                    row.pack_start(label(key.replace("_", " ").replace("-", " ").title()), True, True, 0)
                    row.pack_start(control, False, False, 0)
                    section.pack_start(row, False, False, 0)
                    self.controls[group][key.replace("_", "-")] = control
                    self.original[group][key.replace("_", "-")] = field.value
            button = Gtk.Button(label=f"Apply {group}")
            button.connect("clicked", self._apply_group, group, False)
            section.pack_start(button, False, False, 0)
            if not OPS[group].is_link_affecting:
                save_button = Gtk.Button(label=f"Apply and save {group} for boot")
                save_button.connect("clicked", self._apply_group, group, True)
                section.pack_start(save_button, False, False, 0)
        self.body.show_all()

    def collect(self, group: str) -> dict:
        result = {}
        for key, control in self.controls.get(group, {}).items():
            if not control.get_sensitive():
                continue
            if isinstance(control, Gtk.Switch):
                value = control.get_active()
            elif isinstance(control, Gtk.SpinButton):
                value = control.get_value_as_int()
            else:
                value = control.get_text().strip()
            if value != self.original[group].get(key):
                result[key] = value
        return result

    def _apply_group(self, _button, group: str, save: bool) -> None:
        values = self.collect(group)
        if values and self.iface:
            self.apply(self.iface, group, values, save=save)
