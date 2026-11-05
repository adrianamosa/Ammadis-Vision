"""Vision sources.

Frigate is the default engine and a replaceable dependency. To swap it:

1. Emit `TrackUpdate` (person/object only — no face embeddings or names).
2. Implement `start`, `stop`, and `health`.
3. Register the engine name in `build_source`.
4. Set `vision.engine` in the site YAML and map `engine_zones`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

import httpx
import paho.mqtt.client as mqtt

from ammadis_edge.config import SiteConfig
from ammadis_edge.models import TrackUpdate
from ammadis_edge.normalize import normalize_frigate_mqtt, normalize_frigate_rest

logger = logging.getLogger("ammadis.vision")


class VisionSource(Protocol):
    engine_name: str

    def start(self, on_track: Callable[[TrackUpdate], None]) -> None: ...

    def stop(self) -> None: ...

    def health(self) -> dict: ...


class FrigateSource:
    """Live path: MQTT `frigate/events`. HTTP `/api/events` is the fallback."""

    engine_name = "frigate"

    def __init__(self, site: SiteConfig, http_client: httpx.Client | None = None) -> None:
        self.site = site
        self._on_track: Callable[[TrackUpdate], None] | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._mqtt_connected = False
        self._frigate_api = "unchecked"
        self._client: mqtt.Client | None = None
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=3.0)
        self._http_tracks: set[str] = set()
        self._threads: list[threading.Thread] = []

    def health(self) -> dict:
        with self._lock:
            return {
                "mqtt_connected": self._mqtt_connected,
                "frigate_api": self._frigate_api,
            }

    def start(self, on_track: Callable[[TrackUpdate], None]) -> None:
        self._on_track = on_track
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="ammadis-edge",
            protocol=mqtt.MQTTv311,
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(self.site.mqtt_host, self.site.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client
        self._threads = [
            threading.Thread(target=self._probe_loop, name="ammadis-frigate-probe", daemon=True)
        ]
        if self.site.http_fallback_seconds > 0:
            self._threads.append(
                threading.Thread(
                    target=self._fallback_loop, name="ammadis-frigate-http", daemon=True
                )
            )
        for thread in self._threads:
            thread.start()
        logger.info(
            "frigate source mqtt=%s:%s topic=%s api=%s",
            self.site.mqtt_host,
            self.site.mqtt_port,
            self.site.mqtt_topic,
            self.site.frigate_api_url,
        )

    def stop(self) -> None:
        self._stop.set()
        client = self._client
        if client is not None:
            client.loop_stop()
            try:
                client.disconnect()
            except Exception:
                logger.debug("mqtt disconnect", exc_info=True)
        if self._owns_http:
            self._http.close()

    def handle_mqtt_payload(self, payload: bytes | str) -> None:
        update = normalize_frigate_mqtt(payload)
        if update is None or self._on_track is None:
            return
        self._on_track(update)

    def poll_http_once(self) -> None:
        url = self.site.frigate_api_url.rstrip("/") + "/api/events"
        response = self._http.get(url, params={"limit": 50, "in_progress": 1})
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list):
            return
        seen: set[str] = set()
        for item in body:
            if not isinstance(item, dict):
                continue
            update = normalize_frigate_rest(item)
            if update is None or update.ended:
                continue
            seen.add(update.track_id)
            if self._on_track is not None:
                self._on_track(update)
        missing = self._http_tracks - seen
        self._http_tracks = seen
        for track_id in sorted(missing):
            if self._on_track is None:
                continue
            self._on_track(
                TrackUpdate(
                    engine="frigate",
                    track_id=track_id,
                    camera="",
                    label="person",
                    score=0.0,
                    current_zones=frozenset(),
                    ended=True,
                    false_positive=False,
                    timestamp=None,
                    transport="http",
                )
            )

    def _on_connect(self, client, userdata, *args) -> None:
        reason_code = args[1] if len(args) >= 2 else (args[0] if args else 0)
        is_failure = getattr(reason_code, "is_failure", None)
        if is_failure is None:
            try:
                is_failure = int(reason_code) != 0
            except (TypeError, ValueError):
                is_failure = True
        if is_failure:
            logger.warning("mqtt connect failed: %s", reason_code)
            with self._lock:
                self._mqtt_connected = False
            return
        with self._lock:
            self._mqtt_connected = True
        client.subscribe(self.site.mqtt_topic)
        logger.info("mqtt connected; subscribed to %s", self.site.mqtt_topic)

    def _on_disconnect(self, client, userdata, *args) -> None:
        with self._lock:
            self._mqtt_connected = False

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = message.payload if hasattr(message, "payload") else message
            self.handle_mqtt_payload(payload)
        except Exception:
            logger.exception("failed to handle mqtt message")

    def _probe_loop(self) -> None:
        while not self._stop.is_set():
            self._probe_once()
            self._stop.wait(self.site.http_probe_seconds or 15)

    def _probe_once(self) -> None:
        url = self.site.frigate_api_url.rstrip("/") + "/api/version"
        try:
            response = self._http.get(url)
            state = "ok" if response.status_code == 200 else f"http_{response.status_code}"
        except httpx.HTTPError:
            state = "down"
        with self._lock:
            self._frigate_api = state

    def _fallback_loop(self) -> None:
        while not self._stop.wait(self.site.http_fallback_seconds):
            with self._lock:
                connected = self._mqtt_connected
                api_down = self._frigate_api != "ok"
            if connected or api_down:
                continue
            try:
                self.poll_http_once()
            except Exception as exc:
                logger.warning("frigate http fallback failed: %s", exc)


def build_source(site: SiteConfig) -> VisionSource:
    """Registry of vision engines. Frigate is the only built-in engine."""

    if site.vision_engine == "frigate":
        return FrigateSource(site)
    raise ValueError(
        f"Vision engine {site.vision_engine!r} is not registered. "
        "Frigate is a replaceable dependency: add a VisionSource that emits "
        "TrackUpdate values (person/object only) and register it in "
        "ammadis_edge.sources.build_source. Registered engines: frigate."
    )
