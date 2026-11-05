"""Offload feature switches; fixed features stay visible and insensitive."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ...core.models import FeaturesInfo  # noqa: E402
from ...core.parse_settings import read_features  # noqa: E402
from ...helper.operations import FEATURES  # noqa: E402
from .base import Topic, scroll  # noqa: E402


class OffloadTab(Topic):
    def __init__(self, apply=None) -> None:
        super().__init__()
        self.reader = read_features
        self.controls: dict[str, Gtk.CheckButton] = {}
        self.original: dict[str, bool] = {}
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.pack_start(scroll(self.rows), True, True, 0)
        self.apply_button = Gtk.Button(label="Apply changed offloads")
        self.apply_button.connect("clicked", lambda *_: apply(self.iface, "features", self.collect())
                                  if apply and self.iface and self.collect() else None)
        self.pack_start(self.apply_button, False, False, 0)

    def load(self, iface: str) -> FeaturesInfo:
        return self.reader(iface)

    def render(self, data: FeaturesInfo) -> None:
        for child in self.rows.get_children():
            self.rows.remove(child)
        self.controls.clear()
        self.original.clear()
        if not data.supported:
            self.unavailable(data.reason)
            return
        self.unavailable("")
        for name, feature in sorted(data.features.items()):
            check = Gtk.CheckButton(label=name)
            check.set_active(feature.on)
            check.set_sensitive(not feature.fixed and name in FEATURES)
            if feature.fixed:
                check.set_tooltip_text("Fixed by the driver")
            elif name not in FEATURES:
                check.set_tooltip_text("This feature is not in the supported change list")
            self.rows.pack_start(check, False, False, 0)
            self.controls[name] = check
            self.original[name] = feature.on
        self.rows.show_all()

    def collect(self) -> dict:
        return {name: check.get_active() for name, check in self.controls.items()
                if check.get_sensitive() and check.get_active() != self.original[name]}
