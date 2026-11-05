"""Small GTK building blocks for advanced settings."""

from __future__ import annotations

import dataclasses

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ...core.models import Field  # noqa: E402


def scroll(widget: Gtk.Widget) -> Gtk.ScrolledWindow:
    box = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
    box.add(widget)
    return box


def label(text: str, *, selectable: bool = False) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=0, yalign=0, wrap=True, selectable=selectable)


class Topic(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        self.iface: str | None = None
        self.reason = label("")
        self.pack_start(self.reason, False, False, 0)

    def show_interface(self, iface: str | None) -> None:
        self.iface = iface
        self.reason.set_text("")

    def unavailable(self, reason: str | None) -> None:
        self.reason.set_text(reason or "Unavailable")


class TextTab(Topic):
    def __init__(self, reader=None, helper_call=None, what: str | None = None) -> None:
        super().__init__()
        self.reader = reader
        self.helper_call, self.what = helper_call, what
        self.authorize_button = Gtk.Button(label="Read with authorization")
        self.authorize_button.set_no_show_all(True)
        self.authorize_button.connect("clicked", self._authorized_read)
        self.pack_start(self.authorize_button, False, False, 0)
        self.output = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.pack_start(scroll(self.output), True, True, 0)

    def load(self, iface: str):
        return self.reader(iface) if self.reader else None

    def render(self, data) -> None:
        reason = getattr(data, "reason", None) if getattr(data, "supported", True) is False else None
        if isinstance(data, dict):
            reason = next((getattr(value, "reason", None) for key, value in data.items()
                           if key.lower() == self.what and getattr(value, "supported", True) is False and
                           getattr(value, "reason", None) and
                           any(word in value.reason.lower() for word in ("permission", "not permitted"))), None)
        if reason:
            self.unavailable(reason)
        else:
            self.unavailable("")
        self.output.get_buffer().set_text(format_data(data))
        if reason and any(word in reason.lower() for word in ("permission", "not permitted")) \
                and self.helper_call and self.what:
            self.authorize_button.show()
        else:
            self.authorize_button.hide()

    def _authorized_read(self, *_args) -> None:
        if not self.iface or not self.helper_call or not self.what:
            return
        from ..common import thread_runner
        thread_runner(lambda: self.helper_call("read", iface=self.iface, what=self.what),
                      lambda reply, error: self.unavailable(str(error)) if error
                      else self.output.get_buffer().set_text(format_data(reply.get("data"))))


def format_data(data) -> str:
    if isinstance(data, Field):
        return str(data.value) if data.supported else (data.reason or "Unavailable")
    if dataclasses.is_dataclass(data):
        return "\n".join(f"{field.name.replace('_', ' ').title()}: {format_data(getattr(data, field.name))}"
                         for field in dataclasses.fields(data))
    if isinstance(data, dict):
        return "\n".join(f"{key}: {format_data(value)}" for key, value in data.items())
    if isinstance(data, (list, tuple)):
        return "\n".join(format_data(value) for value in data)
    return "" if data is None else str(data)
