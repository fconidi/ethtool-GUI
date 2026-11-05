"""Live NIC counters and a bounded rate graph."""

from __future__ import annotations

import time

from ...core.stats import RateTracker, Stats, read_stats
from ..graph import RateGraph
from .base import TextTab


class StatsTab(TextTab):
    def __init__(self) -> None:
        super().__init__(read_stats)
        self.tracker = RateTracker()
        self.graph = RateGraph()
        self.pack_start(self.graph, False, False, 0)

    def show_interface(self, iface: str | None) -> None:
        if iface != self.iface:
            self.tracker = RateTracker()
            self.graph.history.clear()
            self.graph.queue_draw()
        super().show_interface(iface)

    def show_stats(self, data: Stats) -> None:
        if data.supported:
            self.graph.push(self.tracker.update(data.values, time.monotonic()))

    def render(self, data: Stats) -> None:
        super().render(data)
        self.show_stats(data)
