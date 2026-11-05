"""Visible confirmation bar for a link change guarded by the helper."""

from __future__ import annotations

import math
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..i18n import translate as _  # noqa: E402


class KeepBar(Gtk.Box):
    def __init__(self, keep, save, revert) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._deadline = 0.0
        self._timer = 0
        self._token: str | None = None
        self.label = Gtk.Label(xalign=0)
        self.pack_start(self.label, True, True, 0)
        self.keep_button = Gtk.Button(label=_("keep"))
        self.save_button = Gtk.Button(label=_("keep_and_save"))
        self.revert_button = Gtk.Button(label=_("revert"))
        self.keep_button.connect("clicked", lambda *_: keep())
        self.save_button.connect("clicked", lambda *_: save())
        self.revert_button.connect("clicked", lambda *_: revert())
        for button in (self.keep_button, self.save_button, self.revert_button):
            self.pack_start(button, False, False, 0)
        self.set_no_show_all(True)
        self.hide()
        self.connect("destroy", lambda *_: self.end())

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def seconds_left(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def begin(self, token: str, seconds: float) -> None:
        self.end()
        self._token = token
        self._deadline = time.monotonic() + max(0.0, float(seconds))
        self._tick()
        self.show_all()
        self.show()
        self._timer = GLib.timeout_add(100, self._tick)

    def _tick(self) -> bool:
        self.label.set_text(_("keep_countdown", seconds=math.ceil(self.seconds_left)))
        return True

    def end(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._token = None
        self.hide()

