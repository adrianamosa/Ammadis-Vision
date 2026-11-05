"""HTTP surface: health, occupancy, and an optional demo inject endpoint."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from ammadis_edge import __version__
from ammadis_edge.config import SiteConfig, allow_inject_from_env, load_site_config
from ammadis_edge.models import FORBIDDEN_LABELS, TrackUpdate
from ammadis_edge.normalize import normalize_frigate_mqtt
from ammadis_edge.publisher import WebhookPublisher
from ammadis_edge.service import EdgeService
from ammadis_edge.sources import build_source

logger = logging.getLogger("ammadis.http")


def configure_logging() -> None:
    named = logging.getLogger("ammadis")
    if named.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    named.addHandler(handler)
    named.setLevel(logging.INFO)
    named.propagate = False


def coerce_inject(payload: dict) -> TrackUpdate:
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    track = payload.get("track")
    if isinstance(track, dict):
        label = str(track.get("label") or "").strip().lower()
        if label in FORBIDDEN_LABELS:
            raise ValueError("face and identity tracks are not accepted")
        if "track_id" not in track:
            raise ValueError("track.track_id is required")
        zones = track.get("current_zones") or []
        if not isinstance(zones, list):
            raise ValueError("track.current_zones must be a list")
        timestamp = track.get("timestamp")
        try:
            parsed_ts = None if timestamp in (None, "") else float(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError("track.timestamp must be a unix time") from exc
        return TrackUpdate(
            engine=str(track.get("engine") or "inject"),
            track_id=str(track["track_id"]),
            camera=str(track.get("camera") or ""),
            label=label,
            score=float(track.get("score") or 0),
            current_zones=frozenset(str(zone) for zone in zones if zone),
            ended=bool(track.get("ended")),
            false_positive=bool(track.get("false_positive")),
            timestamp=parsed_ts,
            transport=str(track.get("transport") or "inject"),
        )
    update = normalize_frigate_mqtt(payload.get("frigate", payload))
    if update is None:
        raise ValueError("expected a Frigate MQTT event or a track object")
    if update.label in FORBIDDEN_LABELS:
        raise ValueError("face and identity detections are not accepted")
    return update


def create_app(
    *,
    config_path: str | None = None,
    env: Mapping[str, str] | None = None,
    source=None,
    publisher=None,
    tick_seconds: float = 0.25,
    service: EdgeService | None = None,
) -> FastAPI:
    configure_logging()
    if service is None:
        environ = os.environ if env is None else env
        path = config_path or environ.get("AMMADIS_SITE_CONFIG", "/config/site.yaml")
        site = load_site_config(path, env)
        if source is None:
            source = build_source(site)
        if publisher is None:
            publisher = WebhookPublisher(
                url=site.webhook_url,
                timeout=site.webhook_timeout_seconds,
                retries=site.webhook_retries,
            )
        service = EdgeService(site, source, publisher, tick_seconds=tick_seconds)
    else:
        site = service.site

    allow_inject = allow_inject_from_env(env)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service.start()
        try:
            yield
        finally:
            app.state.service.stop()

    app = FastAPI(title="Ammadis Vision Edge", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.site = site
    app.state.allow_inject = allow_inject

    @app.get("/health")
    def health() -> dict:
        current: EdgeService = app.state.service
        current_site: SiteConfig = app.state.site
        snap = current.snapshot()
        src = current.source.health()
        return {
            "status": "ok",
            "service": "ammadis-edge",
            "version": __version__,
            "site_id": current_site.site_id,
            "vision_engine": current_site.vision_engine,
            "mqtt_connected": bool(src.get("mqtt_connected")),
            "frigate_api": src.get("frigate_api", "unchecked"),
            "webhook_configured": bool(current_site.webhook_url),
            "allow_inject": bool(app.state.allow_inject),
            "last_vision_event_at": snap["last_vision_at"],
            "last_emit_at": snap["last_emit_at"],
            "webhook": current.publisher.stats(),
            "occupancy": snap["occupancy"],
            "zones": [zone.id for zone in current_site.zones],
        }

    @app.get("/ready")
    def ready() -> JSONResponse:
        current: EdgeService = app.state.service
        src = current.source.health()
        mqtt_ok = bool(src.get("mqtt_connected"))
        if current.site.vision_engine == "frigate":
            is_ready = mqtt_ok
        else:
            is_ready = bool(src.get("ready", True))
        body = {
            "ready": is_ready,
            "vision_engine": current.site.vision_engine,
            "mqtt_connected": mqtt_ok,
        }
        return JSONResponse(status_code=200 if is_ready else 503, content=body)

    @app.get("/occupancy")
    def occupancy() -> dict:
        current = app.state.service
        return {"site_id": current.site.site_id, "zones": current.snapshot()["occupancy"]}

    @app.get("/events/recent")
    def recent() -> dict:
        return {"events": app.state.service.snapshot()["recent"]}

    @app.post("/debug/inject")
    def inject(payload: dict = Body(...)) -> dict:
        if not app.state.allow_inject:
            raise HTTPException(status_code=404, detail="inject disabled")
        try:
            update = coerce_inject(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.service.on_track(update)
        return {"accepted": True, "track_id": update.track_id, "label": update.label}

    return app
