"""Shared fixtures for the edge control plane."""

from __future__ import annotations

from ammadis_edge.config import DebounceConfig, SiteConfig, ZoneConfig
from ammadis_edge.models import TrackUpdate


def make_site(**overrides) -> SiteConfig:
    zones = overrides.pop(
        "zones",
        [
            ZoneConfig(
                id="entrance",
                name="Entrada",
                cameras=["cam_entrance"],
                engine_zones=["entrance"],
            ),
            ZoneConfig(
                id="dining",
                name="Comedor",
                cameras=["cam_dining"],
                engine_zones=["dining"],
            ),
            ZoneConfig(
                id="bar",
                name="Barra",
                cameras=["cam_bar"],
                engine_zones=["bar"],
            ),
            ZoneConfig(
                id="kitchen",
                name="Cocina",
                cameras=["cam_kitchen"],
                engine_zones=["kitchen"],
            ),
        ],
    )
    debounce = overrides.pop("debounce", DebounceConfig())
    data = dict(
        site_id="gaucho-grill-temecula",
        site_name="Gaucho Grill Temecula",
        timezone="America/Los_Angeles",
        vision_engine="frigate",
        webhook_url="http://cloud-stub:8080/webhooks/ammadis",
        labels=["person"],
        debounce=debounce,
        zones=zones,
    )
    data.update(overrides)
    return SiteConfig(**data)


def person(**overrides) -> TrackUpdate:
    zones = overrides.pop("current_zones", frozenset({"dining"}))
    if not isinstance(zones, frozenset):
        zones = frozenset(zones)
    data = dict(
        engine="frigate",
        track_id="t1",
        camera="cam_dining",
        label="person",
        score=0.9,
        current_zones=zones,
        ended=False,
        false_positive=False,
        timestamp=1_762_340_001.0,
        transport="mqtt",
    )
    data.update(overrides)
    return TrackUpdate(**data)


class MemoryPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def publish(self, event: dict) -> None:
        self.events.append(event)

    def stats(self) -> dict[str, int]:
        return {
            "queued": 0,
            "sent": len(self.events),
            "failed": 0,
            "dropped": 0,
            "logged": 0,
        }

    def flush(self, timeout: float = 1.0) -> bool:
        return True


class FakeSource:
    engine_name = "fake"

    def __init__(self, mqtt_connected: bool = True) -> None:
        self.mqtt_connected = mqtt_connected
        self.started = False
        self.stopped = False

    def start(self, on_track) -> None:
        self.started = True
        self.on_track = on_track

    def stop(self) -> None:
        self.stopped = True

    def health(self) -> dict:
        return {"mqtt_connected": self.mqtt_connected, "frigate_api": "unused"}
