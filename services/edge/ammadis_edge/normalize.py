"""Frigate payload -> TrackUpdate.

This is the only module that knows Frigate's JSON shape. Identity-bearing
fields are dropped here and have no place on TrackUpdate.
"""

from __future__ import annotations

import json
from typing import Any

from ammadis_edge.models import TrackUpdate


def _as_dict(payload: bytes | str | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
    else:
        parsed = payload
    if not isinstance(parsed, dict):
        return None
    return parsed


def _score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _timestamp(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _zones(value: Any, *, ended: bool) -> frozenset[str]:
    if ended or not isinstance(value, list):
        return frozenset()
    return frozenset(str(zone) for zone in value if zone)


def normalize_frigate_mqtt(payload: bytes | str | dict[str, Any]) -> TrackUpdate | None:
    """Normalize a `frigate/events` message (`type` + `before`/`after`)."""

    data = _as_dict(payload)
    if data is None:
        return None
    after = data.get("after") if isinstance(data.get("after"), dict) else {}
    before = data.get("before") if isinstance(data.get("before"), dict) else {}
    body = after or before
    if not body:
        return None
    track_id = str(body.get("id") or "").strip()
    if not track_id:
        return None
    end_time = after.get("end_time") if after else None
    ended = data.get("type") == "end" or end_time not in (None, "")
    score = after.get("score") if after else None
    if score is None:
        score = body.get("score")
    frame_time = None
    if after:
        frame_time = after.get("frame_time") or after.get("start_time")
    if frame_time is None:
        frame_time = before.get("frame_time") or before.get("start_time")
    return TrackUpdate(
        engine="frigate",
        track_id=track_id,
        camera=str(body.get("camera") or ""),
        label=str(body.get("label") or "").strip().lower(),
        score=_score(score),
        current_zones=_zones(after.get("current_zones") if after else body.get("current_zones"), ended=ended),
        ended=ended,
        false_positive=bool((after or body).get("false_positive")),
        timestamp=_timestamp(frame_time),
        transport="mqtt",
    )


def normalize_frigate_rest(event: dict[str, Any]) -> TrackUpdate | None:
    """Normalize one object from `GET /api/events`.

    The REST `zones` list is cumulative (zones entered during the track), not
    the live `current_zones` used on MQTT. HTTP is only a fallback while MQTT
    is down, so occupancy can stay high until the track ends.
    """

    if not isinstance(event, dict):
        return None
    track_id = str(event.get("id") or "").strip()
    if not track_id:
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    ended = event.get("end_time") not in (None, "")
    zones = event.get("zones")
    if not zones:
        zones = data.get("zones") or []
    score = event.get("top_score")
    if score is None:
        score = data.get("top_score", data.get("score"))
    moment = event.get("end_time") if ended else event.get("start_time")
    return TrackUpdate(
        engine="frigate",
        track_id=track_id,
        camera=str(event.get("camera") or ""),
        label=str(event.get("label") or "").strip().lower(),
        score=_score(score),
        current_zones=_zones(zones, ended=ended),
        ended=ended,
        false_positive=bool(event.get("false_positive")),
        timestamp=_timestamp(moment),
        transport="http",
    )
