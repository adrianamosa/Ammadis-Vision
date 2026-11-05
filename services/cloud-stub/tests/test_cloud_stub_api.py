from fastapi.testclient import TestClient

from cloud_stub.app import create_app


def test_webhook_is_stored_logged_and_deduped(tmp_path):
    app = create_app(db_path=str(tmp_path / "events.db"))
    event = {
        "schema": "ammadis.vision.event.v1",
        "event_id": "evt-1",
        "event_type": "person_entered_zone",
        "site": {"id": "gaucho-grill-temecula", "name": "Gaucho Grill Temecula"},
        "zone": {"id": "bar", "name": "Barra"},
        "occupancy": 1,
    }
    other = {
        "event_id": "evt-2",
        "event_type": "zone_occupancy_changed",
        "site": {"id": "gaucho-grill-temecula"},
        "zone": {"id": "dining", "name": "Comedor"},
    }
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["service"] == "ammadis-cloud-stub"
        assert health.json()["events_stored"] == 0
        first = client.post("/webhooks/ammadis", json=event)
        assert first.status_code == 200
        assert first.json()["stored"] is True
        second = client.post("/webhooks/ammadis", json=event)
        assert second.json() == {"stored": False, "duplicate": True, "event_id": "evt-1"}
        assert client.post("/webhooks/ammadis", json=other).json()["stored"] is True
        listed = client.get("/events", params={"zone_id": "bar"})
        assert listed.json()["count"] == 1
        assert listed.json()["events"][0]["body"]["event_id"] == "evt-1"
        assert client.get("/events/evt-2").json()["body"]["event_type"] == "zone_occupancy_changed"
        assert client.get("/events/missing").status_code == 404
        assert client.post("/webhooks/ammadis", json={"event_type": "person_left_zone"}).status_code == 400
        assert client.get("/health").json()["events_stored"] == 2
        assert client.delete("/events").json()["deleted"] == 2
        assert client.get("/events").json()["count"] == 0
