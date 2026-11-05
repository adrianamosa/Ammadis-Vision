from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from ammadis_edge.app import create_app
from conftest import FakeSource, MemoryPublisher

ROOT = Path(__file__).resolve().parents[3]


def write_site(tmp_path: Path, *, enter: float) -> Path:
    raw = yaml.safe_load(
        (ROOT / "config/sites/gaucho-grill-temecula/site.yaml").read_text(encoding="utf-8")
    )
    raw["debounce"]["enter_seconds"] = enter
    raw["debounce"]["leave_seconds"] = 0
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_health_ready_and_inject_strips_identity(tmp_path: Path):
    path = write_site(tmp_path, enter=0)
    publisher = MemoryPublisher()
    source = FakeSource(mqtt_connected=True)
    app = create_app(
        config_path=str(path),
        env={"AMMADIS_ALLOW_INJECT": "true"},
        source=source,
        publisher=publisher,
    )
    payload = json_fixture("frigate_enter_dining.json")
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert body["service"] == "ammadis-edge"
        assert body["site_id"] == "gaucho-grill-temecula"
        assert body["vision_engine"] == "frigate"
        assert body["mqtt_connected"] is True
        assert body["zones"] == ["entrance", "dining", "bar", "kitchen"]
        assert client.get("/ready").status_code == 200
        injected = client.post("/debug/inject", json=payload)
        assert injected.status_code == 200
        recent = client.get("/events/recent").json()["events"]
        assert [event["event_type"] for event in recent] == [
            "person_entered_zone",
            "zone_occupancy_changed",
        ]
        encoded = str(recent)
        assert "should-not-leak" not in encoded
        assert "face" not in encoded
        occupancy = client.get("/occupancy").json()
        assert occupancy["zones"]["dining"] == 1
    assert source.started and source.stopped
    assert publisher.events[0]["event_type"] == "person_entered_zone"


def test_inject_disabled_and_face_rejected(tmp_path: Path):
    path = write_site(tmp_path, enter=0)
    app = create_app(
        config_path=str(path),
        env={"AMMADIS_ALLOW_INJECT": "false"},
        source=FakeSource(),
        publisher=MemoryPublisher(),
    )
    with TestClient(app) as client:
        assert client.post("/debug/inject", json={"type": "update"}).status_code == 404
        assert client.get("/ready").status_code == 200

    not_ready = create_app(
        config_path=str(path),
        env={"AMMADIS_ALLOW_INJECT": "true"},
        source=FakeSource(mqtt_connected=False),
        publisher=MemoryPublisher(),
    )
    with TestClient(not_ready) as client:
        assert client.get("/ready").status_code == 503
        rejected = client.post(
            "/debug/inject",
            json={"track": {"track_id": "x", "label": "face", "camera": "cam_dining"}},
        )
        assert rejected.status_code == 400


def json_fixture(name: str) -> dict:
    import json

    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))
