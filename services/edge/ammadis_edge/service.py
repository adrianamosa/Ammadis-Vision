"""Glue: vision source -> zone rules -> webhook, plus a debounce ticker."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

from ammadis_edge.config import SiteConfig
from ammadis_edge.models import TrackUpdate, utc_iso
from ammadis_edge.rules import RulesEngine

logger = logging.getLogger("ammadis.edge")


class EdgeService:
    def __init__(
        self,
        site: SiteConfig,
        source,
        publisher,
        tick_seconds: float = 0.25,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.site = site
        self.source = source
        self.publisher = publisher
        clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self.wall_clock = clock
        self.monotonic = monotonic or time.monotonic
        self.rules = RulesEngine(site, wall_clock=clock)
        self.tick_seconds = tick_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_vision_at: datetime | None = None
        self.last_emit_at: datetime | None = None
        self.recent: deque[dict] = deque(maxlen=50)

    def start(self, *, connect_source: bool = True) -> None:
        self.publisher.start()
        if connect_source:
            self.source.start(self.on_track)
        self._stop.clear()
        self._thread = threading.Thread(target=self._tick_loop, name="ammadis-rules", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.source.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.publisher.stop()

    def on_track(self, update: TrackUpdate) -> None:
        logger.debug(
            "track %s camera=%s label=%s zones=%s ended=%s",
            update.track_id,
            update.camera,
            update.label,
            sorted(update.current_zones),
            update.ended,
        )
        with self._lock:
            self.last_vision_at = self.wall_clock()
            events = self.rules.apply(update, self.monotonic())
        self._emit(events)

    def tick_once(self) -> None:
        with self._lock:
            events = self.rules.tick(self.monotonic())
        self._emit(events)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "occupancy": self.rules.occupancy(),
                "recent": list(self.recent),
                "last_vision_at": utc_iso(self.last_vision_at),
                "last_emit_at": utc_iso(self.last_emit_at),
            }

    def _tick_loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                self.tick_once()
            except Exception:
                logger.exception("rules tick failed")

    def _emit(self, events: list[dict]) -> None:
        if not events:
            return
        with self._lock:
            self.last_emit_at = self.wall_clock()
            for event in events:
                self.recent.append(event)
        for event in events:
            logger.info(
                "emit %s zone=%s occupancy=%s previous=%s track=%s",
                event["event_type"],
                event["zone"]["id"],
                event["occupancy"],
                event["previous_occupancy"],
                event["track_id"],
            )
            self.publisher.publish(event)
