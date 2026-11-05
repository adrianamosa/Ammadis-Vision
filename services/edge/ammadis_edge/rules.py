"""Zone rules. No vision-engine types: only TrackUpdate in, event dicts out."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ammadis_edge.config import SiteConfig
from ammadis_edge.models import SCHEMA, TrackUpdate, unix_iso, utc_iso


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _track_event_type(label: str, *, entered: bool) -> str:
    """Person events keep the stable names the webhook contract documents."""

    if label == "person":
        return "person_entered_zone" if entered else "person_left_zone"
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in label) or "object"
    verb = "entered" if entered else "left"
    return f"{safe}_{verb}_zone"


@dataclass
class _ZoneTrack:
    inside: bool = False
    pending_enter_since: float | None = None
    pending_leave_since: float | None = None


@dataclass
class _Track:
    engine: str
    transport: str
    label: str
    camera: str
    score: float
    detected_at: float | None
    observed: set[str] = field(default_factory=set)
    zones: dict[str, _ZoneTrack] = field(default_factory=dict)
    last_seen: float = 0.0


class RulesEngine:
    def __init__(
        self,
        site: SiteConfig,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.site = site
        self.wall_clock = wall_clock or _now
        self.tracks: dict[str, _Track] = {}
        self.members: dict[str, set[str]] = {zone.id: set() for zone in site.zones}
        self._zones = {zone.id: zone for zone in site.zones}
        self._labels = set(site.labels)

    def occupancy(self) -> dict[str, int]:
        return {zone_id: len(members) for zone_id, members in self.members.items()}

    def apply(self, update: TrackUpdate, now: float) -> list[dict]:
        if update.label not in self._labels or update.false_positive:
            if update.track_id in self.tracks:
                return self._force_leave_all(update.track_id, now, reason="leave")
            return []
        if update.ended:
            if update.track_id not in self.tracks:
                return []
            track = self.tracks[update.track_id]
            self._touch(track, update, now)
            return self._force_leave_all(update.track_id, now, reason="leave")

        observed = self._match_zones(update)
        track = self.tracks.get(update.track_id)
        if track is None:
            if not observed:
                return []
            track = _Track(
                engine=update.engine,
                transport=update.transport,
                label=update.label,
                camera=update.camera,
                score=update.score,
                detected_at=update.timestamp,
                last_seen=now,
            )
            self.tracks[update.track_id] = track
        self._touch(track, update, now)
        track.observed = observed
        return self._evaluate(update.track_id, track, now)

    def tick(self, now: float) -> list[dict]:
        events: list[dict] = []
        timeout = self.site.debounce.track_timeout_seconds
        for track_id, track in list(self.tracks.items()):
            if timeout > 0 and now - track.last_seen >= timeout:
                events.extend(self._force_leave_all(track_id, now, reason="timeout"))
            else:
                events.extend(self._evaluate(track_id, track, now))
        return events

    def _touch(self, track: _Track, update: TrackUpdate, now: float) -> None:
        track.engine = update.engine or track.engine
        track.transport = update.transport or track.transport
        track.label = update.label or track.label
        if update.camera:
            track.camera = update.camera
        if update.score or not update.ended:
            track.score = update.score
        if update.timestamp is not None:
            track.detected_at = update.timestamp
        track.last_seen = now

    def _match_zones(self, update: TrackUpdate) -> set[str]:
        found: set[str] = set()
        for zone in self.site.zones:
            if update.camera not in zone.cameras:
                continue
            if update.current_zones.intersection(zone.engine_zones):
                found.add(zone.id)
        return found

    def _evaluate(self, track_id: str, track: _Track, now: float) -> list[dict]:
        events: list[dict] = []
        enter_after = self.site.debounce.enter_seconds
        leave_after = self.site.debounce.leave_seconds
        for zone_id in self._zones:
            state = track.zones.setdefault(zone_id, _ZoneTrack())
            if zone_id in track.observed:
                state.pending_leave_since = None
                if state.inside:
                    state.pending_enter_since = None
                    continue
                if state.pending_enter_since is None:
                    state.pending_enter_since = now
                if now - state.pending_enter_since >= enter_after:
                    state.inside = True
                    state.pending_enter_since = None
                    events.extend(self._commit_enter(track, track_id, zone_id, reason="enter"))
                continue
            state.pending_enter_since = None
            if not state.inside:
                state.pending_leave_since = None
                continue
            if state.pending_leave_since is None:
                state.pending_leave_since = now
            if now - state.pending_leave_since >= leave_after:
                state.inside = False
                state.pending_leave_since = None
                events.extend(self._commit_leave(track, track_id, zone_id, reason="leave"))
        self._drop_if_idle(track_id, track)
        return events

    def _drop_if_idle(self, track_id: str, track: _Track) -> None:
        if any(
            state.inside or state.pending_enter_since is not None or state.pending_leave_since is not None
            for state in track.zones.values()
        ):
            return
        self.tracks.pop(track_id, None)

    def _force_leave_all(self, track_id: str, now: float, reason: str) -> list[dict]:
        track = self.tracks.pop(track_id, None)
        if track is None:
            return []
        events: list[dict] = []
        for zone_id, state in track.zones.items():
            if not state.inside:
                continue
            state.inside = False
            events.extend(self._commit_leave(track, track_id, zone_id, reason=reason))
        return events

    def _commit_enter(self, track: _Track, track_id: str, zone_id: str, reason: str) -> list[dict]:
        before = len(self.members[zone_id])
        self.members[zone_id].add(track_id)
        occupancy = len(self.members[zone_id])
        if occupancy == before:
            return []
        return [
            self._event(
                _track_event_type(track.label, entered=True),
                track,
                track_id,
                zone_id,
                occupancy=occupancy,
                previous=before,
                reason=reason,
                include_track=True,
            ),
            self._event(
                "zone_occupancy_changed",
                track,
                None,
                zone_id,
                occupancy=occupancy,
                previous=before,
                reason=reason,
                include_track=False,
            ),
        ]

    def _commit_leave(self, track: _Track, track_id: str, zone_id: str, reason: str) -> list[dict]:
        before = len(self.members[zone_id])
        self.members[zone_id].discard(track_id)
        occupancy = len(self.members[zone_id])
        if occupancy == before:
            return []
        return [
            self._event(
                _track_event_type(track.label, entered=False),
                track,
                track_id,
                zone_id,
                occupancy=occupancy,
                previous=before,
                reason=reason,
                include_track=True,
            ),
            self._event(
                "zone_occupancy_changed",
                track,
                None,
                zone_id,
                occupancy=occupancy,
                previous=before,
                reason=reason,
                include_track=False,
            ),
        ]

    def _event(
        self,
        event_type: str,
        track: _Track,
        track_id: str | None,
        zone_id: str,
        occupancy: int,
        previous: int,
        reason: str,
        include_track: bool,
    ) -> dict:
        zone = self._zones[zone_id]
        return {
            "schema": SCHEMA,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "occurred_at": utc_iso(self.wall_clock()),
            "detected_at": unix_iso(track.detected_at),
            "site": {
                "id": self.site.site_id,
                "name": self.site.site_name,
                "timezone": self.site.timezone,
            },
            "zone": {"id": zone.id, "name": zone.name},
            "camera": track.camera or None,
            "label": track.label,
            "track_id": track_id if include_track else None,
            "score": round(track.score, 4) if include_track else None,
            "occupancy": occupancy,
            "previous_occupancy": previous,
            "track_ids": sorted(self.members[zone_id]),
            "reason": reason,
            "source": {"engine": track.engine, "transport": track.transport},
        }
