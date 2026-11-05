"""Explicit hardware tests and raw read-only dumps."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ...core.parse_settings import read_eeprom_dump, read_phy_stats, read_registers_dump  # noqa: E402
from .base import Topic, scroll  # noqa: E402


class TestTab(Topic):
    def __init__(self, call, confirm) -> None:
        super().__init__()
        self.call, self.confirm = call, confirm
        buttons = Gtk.Box(spacing=8)
        for title, command, args, risky in (
            ("Blink LED", "blink", {"seconds": 3}, False),
            ("Online self-test", "selftest", {"mode": "online"}, False),
            ("Offline self-test", "selftest", {"mode": "offline", "confirmed": True}, True),
            ("Cable test", "cable_test", {"confirmed": True}, True),
        ):
            button = Gtk.Button(label=title)
            button.connect("clicked", self._run, command, args, risky)
            buttons.pack_start(button, False, False, 0)
        self.pack_start(buttons, False, False, 0)
        self.output = Gtk.TextView(editable=False, monospace=True)
        self.pack_start(scroll(self.output), True, True, 0)

    def _run(self, _button, cmd: str, args: dict, risky: bool) -> None:
        if not self.iface or (risky and not self.confirm("This test can interrupt the link. Continue?", True)):
            return
        from ..common import thread_runner
        iface = self.iface
        thread_runner(lambda: self.call(cmd, iface=iface, **args),
                      lambda result, error: self.output.get_buffer().set_text(
                          str(error) if error else str(result)))


class DumpTab(Topic):
    def __init__(self, helper_call=None) -> None:
        super().__init__()
        self.helper_call = helper_call
        buttons = Gtk.Box(spacing=8)
        for title, reader, what in (("EEPROM", read_eeprom_dump, "eeprom_dump"),
                                    ("Registers", read_registers_dump, "registers_dump"),
                                    ("PHY statistics", read_phy_stats, "phy_stats")):
            button = Gtk.Button(label=f"Read {title}")
            button.connect("clicked", self._read, reader, what)
            buttons.pack_start(button, False, False, 0)
        self.pack_start(buttons, False, False, 0)
        self.output = Gtk.TextView(editable=False, monospace=True)
        self.pack_start(scroll(self.output), True, True, 0)

    def _read(self, _button, reader, what: str) -> None:
        if not self.iface:
            return
        from ..common import thread_runner
        iface = self.iface
        def work():
            data = reader(iface)
            if (not data.supported and data.reason and
                    any(word in data.reason.lower() for word in ("permission", "not permitted"))
                    and self.helper_call):
                return self.helper_call("read", iface=iface, what=what).get("data")
            return data

        thread_runner(work,
                      lambda data, error: self.output.get_buffer().set_text(
                          str(error) if error else (data.value or data.reason or "No data")
                          if hasattr(data, "value") else str(data)))
