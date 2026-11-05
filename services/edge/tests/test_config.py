import json
from pathlib import Path

import yaml
import pytest
from pydantic import ValidationError

from ammadis_edge.config import SiteConfig, load_site_config
from ammadis_edge.sources import FrigateSource, build_source
from conftest import make_site

ROOT = Path(__file__).resolve().parents[3]


def test_temecula_site_yaml_loads_four_zones():
    site = load_site_config(
        ROOT / "config/sites/gaucho-grill-temecula/site.yaml",
        env={},
    )
    assert site.site_id == "gaucho-grill-temecula"
    assert site.vision_engine == "frigate"
    assert [zone.id for zone in site.zones] == ["entrance", "dining", "bar", "kitchen"]
    assert site.labels == ["person"]
    assert site.debounce.enter_seconds == 2
    assert site.debounce.leave_seconds == 5
    assert site.debounce.track_timeout_seconds == 0
    assert site.webhook_url.endswith("/webhooks/ammadis")


def test_env_overrides_webhook_and_mqtt():
    site = load_site_config(
        ROOT / "config/sites/gaucho-grill-temecula/site.yaml",
        env={
            "AMMADIS_WEBHOOK_URL": "https://example.test/hook",
            "AMMADIS_MQTT_HOST": "broker.internal",
            "AMMADIS_FRIGATE_API_URL": "http://vision:5000/",
        },
    )
    assert site.webhook_url == "https://example.test/hook"
    assert site.mqtt_host == "broker.internal"
    assert site.frigate_api_url == "http://vision:5000"


def test_face_label_is_rejected(tmp_path: Path):
    raw = yaml.safe_load(
        (ROOT / "config/sites/gaucho-grill-temecula/site.yaml").read_text()
    )
    raw["labels"] = ["person", "face"]
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="face"):
        load_site_config(path, env={})


def test_unknown_engine_tells_you_frigate_is_replaceable():
    site = make_site(vision_engine="other-detector")
    with pytest.raises(ValueError, match="replaceable"):
        build_source(site)


def test_site_config_rejects_duplicate_zones():
    with pytest.raises(ValidationError):
        make_site(
            zones=[
                {
                    "id": "dining",
                    "name": "Comedor",
                    "cameras": ["cam_dining"],
                    "engine_zones": ["dining"],
                },
                {
                    "id": "dining",
                    "name": "Otra",
                    "cameras": ["cam_bar"],
                    "engine_zones": ["bar"],
                },
            ]
        )


def test_frigate_config_is_person_only_and_uses_substreams():
    raw = yaml.safe_load((ROOT / "config/frigate/config.yml").read_text(encoding="utf-8"))
    assert raw["face_recognition"]["enabled"] is False
    assert raw["lpr"]["enabled"] is False
    assert raw["semantic_search"]["enabled"] is False
    assert raw["record"]["enabled"] is False
    assert raw["snapshots"]["enabled"] is False
    assert raw["objects"]["track"] == ["person"]
    assert "genai" not in raw["objects"]
    assert raw["version"] == "0.16-0"
    assert raw["detect"]["enabled"] is True
    assert raw["detect"]["width"] == 640
    assert raw["detect"]["height"] == 360
    assert raw["mqtt"]["host"] == "mosquitto"
    cameras = raw["cameras"]
    assert set(cameras) == {"cam_entrance", "cam_dining", "cam_bar", "cam_kitchen"}
    for name, camera in cameras.items():
        path = camera["ffmpeg"]["inputs"][0]["path"]
        assert "streamType=sub" in path
        assert "{FRIGATE_RTSP_PASSWORD}" in path
        assert "change-me" not in path
        assert camera["mqtt"]["enabled"] is False
        for zone_name, zone in camera["zones"].items():
            assert zone_name != name
            assert zone["objects"] == ["person"]


def test_http_fallback_emits_end_when_track_disappears():
    seen: list = []

    class Response:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    class Client:
        def __init__(self):
            self.bodies = [
                [
                    {
                        "id": "http-1",
                        "camera": "cam_kitchen",
                        "label": "person",
                        "zones": ["kitchen"],
                        "top_score": 0.8,
                        "start_time": 1,
                        "end_time": None,
                    }
                ],
                [],
            ]

        def get(self, url, params=None):
            assert url.endswith("/api/events")
            assert params["in_progress"] == 1
            return Response(self.bodies.pop(0))

    site = make_site()
    source = FrigateSource(site, http_client=Client())
    source._on_track = seen.append
    source.poll_http_once()
    source.poll_http_once()
    source.stop()
    assert [item.track_id for item in seen] == ["http-1", "http-1"]
    assert seen[0].ended is False
    assert seen[0].current_zones == frozenset({"kitchen"})
    assert seen[1].ended is True
    assert seen[1].transport == "http"


def test_build_source_returns_frigate():
    source = build_source(make_site())
    assert source.engine_name == "frigate"
    source.stop()
