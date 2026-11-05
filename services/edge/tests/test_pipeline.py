import json
from datetime import datetime, timezone
from pathlib import Path

from ammadis_edge.normalize import normalize_frigate_mqtt
from ammadis_edge.service import EdgeService
from ammadis_edge.sources import FrigateSource
from conftest import MemoryPublisher, make_site

ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self) -> None:
        self.mono = 1_000.0
        self.wall = datetime(2026, 11, 5, 15, 4, 12, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.mono

    def wall_clock(self) -> datetime:
        return self.wall


def test_mqtt_fixture_becomes_webhook_events_without_identity():
    clock = Clock()
    site = make_site()
    source = FrigateSource(site)
    publisher = MemoryPublisher()
    service = EdgeService(
        site,
        source,
        publisher,
        monotonic=clock.monotonic,
        wall_clock=clock.wall_clock,
    )
    try:
        source._on_track = service.on_track
        payload = (ROOT / "fixtures" / "frigate_enter_dining.json").read_text(encoding="utf-8")
        source.handle_mqtt_payload(payload)
        assert publisher.events == []
        clock.mono = 1_002
        service.tick_once()
        assert [event["event_type"] for event in publisher.events] == [
            "person_entered_zone",
            "zone_occupancy_changed",
        ]
        blob = json.dumps(publisher.events)
        assert "should-not-leak" not in blob
        assert "face" not in blob
        assert publisher.events[0]["occurred_at"] == "2026-11-05T15:04:12.000Z"
        assert publisher.events[0]["camera"] == "cam_dining"
        assert publisher.events[0]["zone"]["name"] == "Comedor"

        leave = normalize_frigate_mqtt(
            (ROOT / "fixtures" / "frigate_leave_dining.json").read_text(encoding="utf-8")
        )
        assert leave is not None
        clock.mono = 1_003
        service.on_track(leave)
        assert len(publisher.events) == 2
        clock.mono = 1_008
        service.tick_once()
        assert [event["event_type"] for event in publisher.events] == [
            "person_entered_zone",
            "zone_occupancy_changed",
            "person_left_zone",
            "zone_occupancy_changed",
        ]
        assert publisher.events[-1]["occupancy"] == 0
    finally:
        source.stop()
