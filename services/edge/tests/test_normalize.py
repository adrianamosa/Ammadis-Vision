import json
from pathlib import Path

from ammadis_edge.models import TrackUpdate
from ammadis_edge.normalize import normalize_frigate_mqtt, normalize_frigate_rest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((ROOT / "fixtures" / "frigate_enter_dining.json").read_text())


def test_mqtt_event_drops_identity_fields():
    update = normalize_frigate_mqtt(FIXTURE)
    assert update is not None
    assert update.track_id == "demo-track-dining-1"
    assert update.camera == "cam_dining"
    assert update.label == "person"
    assert update.score == 0.93
    assert update.current_zones == frozenset({"dining"})
    assert update.ended is False
    assert update.transport == "mqtt"
    assert "sub_label" not in TrackUpdate.__dataclass_fields__
    blob = repr(update)
    assert "should-not-leak" not in blob
    assert "face" not in blob


def test_end_message_clears_zones():
    payload = {
        "type": "end",
        "after": {
            "id": "track-end",
            "camera": "cam_entrance",
            "label": "person",
            "current_zones": ["entrance"],
            "end_time": 1762340099.0,
            "score": 0.8,
            "sub_label": ["Ada", 0.99],
        },
    }
    update = normalize_frigate_mqtt(payload)
    assert update is not None
    assert update.ended is True
    assert update.current_zones == frozenset()
    assert "Ada" not in repr(update)


def test_garbage_payload_is_ignored():
    assert normalize_frigate_mqtt(b"not-json") is None
    assert normalize_frigate_mqtt({"type": "update"}) is None


def test_rest_event_marks_transport_http():
    update = normalize_frigate_rest(
        {
            "id": "rest-1",
            "camera": "cam_bar",
            "label": "person",
            "zones": ["bar"],
            "top_score": 0.77,
            "start_time": 10.0,
            "end_time": None,
            "data": {"score": 0.77, "attributes": [{"label": "face"}]},
        }
    )
    assert update is not None
    assert update.current_zones == frozenset({"bar"})
    assert update.transport == "http"
    assert update.ended is False
    assert "face" not in repr(update)


def test_rest_ended_event_has_no_zones():
    update = normalize_frigate_rest(
        {
            "id": "rest-2",
            "camera": "cam_bar",
            "label": "person",
            "zones": ["bar"],
            "end_time": 20.0,
            "start_time": 10.0,
        }
    )
    assert update is not None
    assert update.ended is True
    assert update.current_zones == frozenset()
