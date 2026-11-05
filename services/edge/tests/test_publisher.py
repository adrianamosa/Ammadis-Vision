import json
from pathlib import Path

import httpx

from ammadis_edge.publisher import WebhookPublisher

ROOT = Path(__file__).resolve().parents[3]


def test_publisher_posts_json_and_retries():
    calls = {"n": 0}
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        seen["body"] = json.loads(request.content)
        seen["header"] = request.headers["x-ammadis-event"]
        return httpx.Response(204)

    publisher = WebhookPublisher(
        "http://stub/webhooks/ammadis",
        timeout=1,
        retries=1,
        backoff_seconds=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    publisher.start()
    try:
        publisher.publish({"event_id": "e1", "event_type": "person_entered_zone"})
        assert publisher.flush(timeout=2)
    finally:
        publisher.stop()
    assert calls["n"] == 2
    assert seen["body"]["event_id"] == "e1"
    assert seen["header"] == "person_entered_zone"
    assert publisher.stats()["sent"] == 1
    assert publisher.stats()["failed"] == 0


def test_empty_webhook_logs_instead_of_posting():
    publisher = WebhookPublisher("")
    publisher.publish({"event_id": "e2", "event_type": "zone_occupancy_changed"})
    assert publisher.stats()["logged"] == 1
    assert publisher.stats()["sent"] == 0


def test_documented_examples_match_the_emitted_shape():
    text = (ROOT / "docs" / "events.md").read_text(encoding="utf-8")
    blocks = []
    fence = "```json\n"
    cursor = 0
    while True:
        start = text.find(fence, cursor)
        if start < 0:
            break
        body_start = start + len(fence)
        end = text.find("```", body_start)
        blocks.append(json.loads(text[body_start:end]))
        cursor = end + 3
    assert [block["event_type"] for block in blocks] == [
        "person_entered_zone",
        "person_left_zone",
        "zone_occupancy_changed",
    ]
    from ammadis_edge.config import DebounceConfig
    from ammadis_edge.rules import RulesEngine
    from conftest import make_site, person

    rules = RulesEngine(make_site(debounce=DebounceConfig(enter_seconds=0, leave_seconds=0)))
    entered = rules.apply(person(track_id="t-a"), now=0)[0]
    rules.apply(person(track_id="t-b"), now=1)
    left = rules.apply(person(track_id="t-a", current_zones=set()), now=2)
    samples = {
        "person_entered_zone": entered,
        "person_left_zone": left[0],
        "zone_occupancy_changed": left[1],
    }
    for documented in blocks:
        emitted = samples[documented["event_type"]]
        assert set(documented) == set(emitted)
        assert set(documented["site"]) == set(emitted["site"])
        assert set(documented["zone"]) == set(emitted["zone"])
        assert set(documented["source"]) == set(emitted["source"])
        blob = json.dumps(documented)
        for banned in ("sub_label", "thumbnail", "snapshot", "embedding", "face", "attributes"):
            assert banned not in blob
