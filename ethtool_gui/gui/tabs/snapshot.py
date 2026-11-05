"""Save a snapshot, compare two saved snapshots and export their difference."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ...core import snapshot  # noqa: E402
from ...i18n import translate as _  # noqa: E402
from .base import Topic, scroll  # noqa: E402


class SnapshotTab(Topic):
    def __init__(self) -> None:
        super().__init__()
        self.take_button = Gtk.Button(label=_("take_snapshot"))
        self.compare_button = Gtk.Button(label=_("compare_snapshots"))
        self.export_button = Gtk.Button(label=_("export_comparison"))
        row = Gtk.Box(spacing=8)
        for button in (self.take_button, self.compare_button, self.export_button):
            row.pack_start(button, False, False, 0)
        self.pack_start(row, False, False, 0)
        self.store = Gtk.ListStore(str, str)
        self.list = Gtk.TreeView(model=self.store, headers_visible=False)
        self.list.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.list.append_column(Gtk.TreeViewColumn(_("tab_snapshot"), Gtk.CellRendererText(), text=0))
        chooser = scroll(self.list)
        chooser.set_size_request(-1, 110)
        self.pack_start(chooser, False, False, 0)
        self.output = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.pack_start(scroll(self.output), True, True, 0)
        self._comparison = ""
        self.take_button.connect("clicked", self._take)
        self.compare_button.connect("clicked", self._compare)
        self.export_button.connect("clicked", self._export)

    def show_interface(self, iface: str | None) -> None:
        super().show_interface(iface)
        self.reload()

    def reload(self) -> None:
        self.store.clear()
        if self.iface:
            for path in sorted(snapshot.default_directory().glob(f"{self.iface}-*.json")):
                self.store.append([path.name, str(path)])

    def _take(self, *_args) -> None:
        if not self.iface:
            return
        from ..common import thread_runner
        iface = self.iface
        thread_runner(lambda: snapshot.save(snapshot.take(iface), snapshot.default_directory()),
                      lambda path, error: self.unavailable(str(error)) if error else self.reload())

    def _selected_paths(self) -> list[str]:
        model, paths = self.list.get_selection().get_selected_rows()
        return [model[path][1] for path in paths]

    def _compare(self, *_args) -> None:
        paths = self._selected_paths()
        if len(paths) != 2:
            self.unavailable(_("select_two_snapshots"))
            return
        from pathlib import Path
        try:
            self.show_diff(snapshot.load(Path(paths[0])), snapshot.load(Path(paths[1])))
        except (OSError, ValueError) as exc:
            self.unavailable(str(exc))

    def show_diff(self, old: dict, new: dict) -> None:
        changes = snapshot.diff(old, new)
        self._comparison = "\n".join(f"{c.path}: {c.old!r} → {c.new!r}" for c in changes) or _("no_differences")
        self.output.get_buffer().set_text(self._comparison)
        self.unavailable("")

    def _export(self, *_args) -> None:
        if not self._comparison:
            return
        dialog = Gtk.FileChooserDialog(title=_("export_comparison"), transient_for=self.get_toplevel(),
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dialog.set_current_name("ethtool-comparison.txt")
        try:
            if dialog.run() == Gtk.ResponseType.OK:
                from pathlib import Path
                Path(dialog.get_filename()).write_text(self._comparison + "\n", encoding="utf-8")
        except OSError as exc:
            self.unavailable(str(exc))
        finally:
            dialog.destroy()
