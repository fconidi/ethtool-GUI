"""Per-pair cable test drawing with measured fault distance."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..core.cable import PairResult  # noqa: E402
from ..i18n import translate as _  # noqa: E402


class CableView(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[PairResult] = []
        self.labels: list[str] = []
        self.set_size_request(280, 100)
        self.connect("draw", self._draw)

    def set_results(self, results: list[PairResult]) -> None:
        self.results = list(results)
        self.labels = [f"{distance:g} m" for pair in results
                       if (distance := pair.fault_at_m if pair.fault_at_m is not None else pair.length_m)
                       is not None]
        self.set_size_request(280, max(100, len(results) * 36 + 18))
        self.queue_draw()

    def _draw(self, _widget, ctx) -> bool:
        width = self.get_allocated_width()
        end = max(150, width - 130)
        max_distance = max((p.fault_at_m or p.length_m or 0 for p in self.results), default=100)
        max_distance = max(100, max_distance)
        ctx.select_font_face("Sans")
        ctx.set_font_size(11)
        for index, pair in enumerate(self.results):
            y = 24 + index * 36
            bad = any(word in pair.status.lower() for word in ("open", "short", "fault", "fail"))
            ctx.set_source_rgb(.74, .19, .24) if bad else ctx.set_source_rgb(.08, .55, .36)
            ctx.set_line_width(3)
            ctx.move_to(70, y)
            ctx.line_to(end, y)
            ctx.stroke()
            ctx.set_source_rgb(.18, .21, .24)
            ctx.move_to(8, y + 4)
            ctx.show_text(_("cable_pair", pair=pair.pair))
            distance = pair.fault_at_m if pair.fault_at_m is not None else pair.length_m
            if distance is not None:
                x = 70 + (end - 70) * max(0, distance) / max_distance
                ctx.set_source_rgb(.74, .19, .24) if bad else ctx.set_source_rgb(.08, .55, .36)
                ctx.arc(x, y, 5, 0, 6.283185307179586)
                ctx.fill()
            ctx.set_source_rgb(.18, .21, .24)
            ctx.move_to(end + 12, y + 4)
            ctx.show_text(pair.status[:15])
            if distance is not None:
                ctx.move_to(end + 12, y + 17)
                ctx.show_text(f"{distance:g} m")
        return False
