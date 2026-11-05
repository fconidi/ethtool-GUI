"""Compact live graph of error, CRC, collision and pause rates."""

from __future__ import annotations

import math
from collections import deque

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..core.stats import group_of  # noqa: E402

HISTORY = 120
COLORS = {
    "errors": (0.78, 0.20, 0.27),
    "crc": (0.95, 0.48, 0.13),
    "collisions": (0.66, 0.34, 0.79),
    "pause": (0.08, 0.54, 0.70),
}


class RateGraph(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.history: deque[dict[str, float]] = deque(maxlen=HISTORY)
        self.set_size_request(280, 150)
        self.connect("draw", self._draw)

    def push(self, rates: dict[str, float]) -> None:
        if any(not math.isfinite(value) or value < 0 for value in rates.values()):
            raise ValueError("rates must be finite and non-negative")
        sample = {key: 0.0 for key in COLORS}
        for name, value in rates.items():
            group = group_of(name)
            if group in ("errors", "drops"):
                sample["errors"] += value
                if "crc" in name.lower() or "frame" in name.lower() or "align" in name.lower():
                    sample["crc"] += value
            elif group == "collisions":
                sample["collisions"] += value
            elif group == "pause":
                sample["pause"] += value
        self.history.append(sample)
        self.queue_draw()

    def _draw(self, _widget, ctx) -> bool:
        width, height = self.get_allocated_width(), self.get_allocated_height()
        left, top, bottom = 36, 22, height - 20
        chart_w = max(1, width - left - 12)
        chart_h = max(1, bottom - top)
        ctx.set_source_rgba(0.45, 0.48, 0.50, 0.35)
        ctx.set_line_width(1)
        for fraction in (0, .5, 1):
            y = bottom - fraction * chart_h
            ctx.move_to(left, y)
            ctx.line_to(left + chart_w, y)
        ctx.stroke()
        maximum = max((value for sample in self.history for value in sample.values()), default=1.0)
        maximum = max(1.0, maximum)
        ctx.select_font_face("Sans")
        ctx.set_font_size(10)
        ctx.set_source_rgb(.38, .42, .46)
        ctx.move_to(2, top + 4)
        ctx.show_text(f"{maximum:.0f}/s")
        ctx.move_to(2, bottom)
        ctx.show_text("0")
        for index, (category, color) in enumerate(COLORS.items()):
            x = left + index * min(105, chart_w / 4)
            ctx.set_source_rgb(*color)
            ctx.rectangle(x, 4, 7, 7)
            ctx.fill()
            ctx.move_to(x + 11, 12)
            ctx.show_text(category.title())
        if not self.history:
            return False
        for category, color in COLORS.items():
            ctx.set_source_rgb(*color)
            ctx.set_line_width(2 if category == "crc" else 1.5)
            for index, sample in enumerate(self.history):
                x = left + chart_w * (index / max(1, HISTORY - 1))
                y = bottom - chart_h * sample[category] / maximum
                (ctx.move_to if index == 0 else ctx.line_to)(x, y)
            ctx.stroke()
        return False
