"""Receive Ammadis webhooks and keep them for a demo."""

from __future__ import annotations

import logging
import os

from fastapi import Body, FastAPI, HTTPException, Query

from cloud_stub import __version__
from cloud_stub.store import EventStore

logger = logging.getLogger("ammadis.cloud_stub")


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


def create_app(db_path: str | None = None) -> FastAPI:
    configure_logging()
    path = db_path or os.environ.get("CLOUD_STUB_DB", "/data/events.db")
    store = EventStore(path)
    app = FastAPI(title="Ammadis Vision Cloud Stub", version=__version__)
    app.state.store = store

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "ammadis-cloud-stub",
            "version": __version__,
            "events_stored": store.count(),
        }

    @app.post("/webhooks/ammadis")
    def webhook(payload: dict = Body(...)) -> dict:
        try:
            stored = store.add(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        zone = payload.get("zone") if isinstance(payload.get("zone"), dict) else {}
        logger.info(
            "webhook %s %s zone=%s stored=%s",
            payload.get("event_type"),
            payload.get("event_id"),
            zone.get("id"),
            stored,
        )
        return {
            "stored": stored,
            "duplicate": not stored,
            "event_id": payload.get("event_id"),
        }

    @app.get("/events")
    def events(
        limit: int = Query(default=50, ge=1, le=500),
        event_type: str | None = None,
        zone_id: str | None = None,
    ) -> dict:
        items = store.list_events(limit=limit, event_type=event_type, zone_id=zone_id)
        return {"count": len(items), "events": items}

    @app.get("/events/{event_id}")
    def one(event_id: str) -> dict:
        item = store.get(event_id)
        if item is None:
            raise HTTPException(status_code=404, detail="event not found")
        return item

    @app.delete("/events")
    def clear() -> dict:
        return {"deleted": store.clear()}

    return app
