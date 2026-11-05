"""Engine-neutral track updates and the outbound event schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

SCHEMA = "ammadis.vision.event.v1"

# Never accept these as object classes. Staff-by-uniform may come later;
# it must not be implemented as face recognition or a face gallery.
FORBIDDEN_LABELS = frozenset(
    {"face", "faces", "face_recognition", "sub_label", "identity", "person_name"}
)


@dataclass(frozen=True)
class TrackUpdate:
    """One observation of a tracked object.

    `current_zones` is where the object is right now, using the vision
    engine's zone names. `ended` means the engine closed the track.
    """

    engine: str
    track_id: str
    camera: str
    label: str
    score: float
    current_zones: frozenset[str]
    ended: bool
    false_positive: bool
    timestamp: float | None
    transport: str = "mqtt"


def utc_iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def unix_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return utc_iso(datetime.fromtimestamp(timestamp, tz=timezone.utc))
