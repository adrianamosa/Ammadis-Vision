from ammadis_edge.config import DebounceConfig
from ammadis_edge.rules import RulesEngine
from conftest import make_site, person

BANNED = {"sub_label", "thumbnail", "snapshot", "embedding", "attributes", "face", "image"}


def engine(**debounce):
    defaults = dict(enter_seconds=2, leave_seconds=5, track_timeout_seconds=0)
    defaults.update(debounce)
    return RulesEngine(make_site(debounce=DebounceConfig(**defaults)))


def types(events):
    return [event["event_type"] for event in events]


def test_enter_waits_for_debounce_then_emits_occupancy():
    rules = engine()
    assert rules.apply(person(), now=10) == []
    assert rules.tick(now=11.9) == []
    events = rules.tick(now=12)
    assert types(events) == ["person_entered_zone", "zone_occupancy_changed"]
    entered, changed = events
    assert entered["zone"] == {"id": "dining", "name": "Comedor"}
    assert entered["occupancy"] == 1
    assert entered["previous_occupancy"] == 0
    assert entered["track_id"] == "t1"
    assert entered["track_ids"] == ["t1"]
    assert entered["reason"] == "enter"
    assert entered["score"] == 0.9
    assert changed["track_id"] is None
    assert changed["score"] is None
    assert changed["occupancy"] == 1
    assert changed["previous_occupancy"] == 0
    assert changed["source"] == {"engine": "frigate", "transport": "mqtt"}
    assert rules.occupancy()["dining"] == 1


def test_flicker_inside_debounce_emits_nothing():
    rules = engine()
    assert rules.apply(person(), now=0) == []
    assert rules.apply(person(current_zones=set()), now=1) == []
    assert rules.tick(now=30) == []
    assert rules.occupancy()["dining"] == 0
    assert rules.tracks == {}


def test_leave_is_debounced_and_end_is_immediate():
    rules = engine()
    rules.apply(person(), now=0)
    assert types(rules.tick(now=2)) == ["person_entered_zone", "zone_occupancy_changed"]
    assert rules.apply(person(current_zones=set()), now=3) == []
    assert rules.tick(now=7.9) == []
    events = rules.tick(now=8)
    assert types(events) == ["person_left_zone", "zone_occupancy_changed"]
    assert events[0]["occupancy"] == 0
    assert events[0]["previous_occupancy"] == 1
    assert events[1]["reason"] == "leave"
    assert rules.occupancy()["dining"] == 0


def test_return_during_leave_debounce_cancels_the_leave():
    rules = engine()
    rules.apply(person(), now=0)
    rules.tick(now=2)
    rules.apply(person(current_zones=set()), now=3)
    rules.apply(person(), now=4)
    assert rules.tick(now=40) == []
    assert rules.occupancy()["dining"] == 1


def test_track_end_leaves_immediately():
    rules = engine()
    rules.apply(person(), now=0)
    rules.tick(now=2)
    events = rules.apply(person(ended=True, current_zones={"dining"}), now=2.2)
    assert types(events) == ["person_left_zone", "zone_occupancy_changed"]
    assert events[0]["reason"] == "leave"
    assert rules.occupancy()["dining"] == 0


def test_end_before_enter_confirms_emits_nothing():
    rules = engine()
    rules.apply(person(), now=0)
    assert rules.apply(person(ended=True), now=1) == []
    assert rules.tick(now=20) == []


def test_two_people_share_zone_occupancy():
    rules = engine(enter_seconds=0, leave_seconds=0)
    first = rules.apply(person(track_id="a"), now=0)
    second = rules.apply(person(track_id="b"), now=1)
    assert first[0]["occupancy"] == 1
    assert second[0]["occupancy"] == 2
    assert second[1]["track_ids"] == ["a", "b"]
    assert second[1]["previous_occupancy"] == 1
    left = rules.apply(person(track_id="a", current_zones=set()), now=2)
    assert left[0]["event_type"] == "person_left_zone"
    assert left[0]["occupancy"] == 1
    assert left[1]["track_ids"] == ["b"]


def test_wrong_camera_wrong_zone_face_and_false_positive_are_ignored():
    rules = engine(enter_seconds=0)
    assert rules.apply(person(current_zones={"parking"}), now=0) == []
    assert rules.apply(person(camera="cam_bar", current_zones={"dining"}), now=0) == []
    assert rules.apply(person(label="face"), now=0) == []
    assert rules.apply(person(false_positive=True), now=0) == []
    assert rules.tick(now=50) == []
    assert all(count == 0 for count in rules.occupancy().values())


def test_false_positive_after_enter_emits_leave():
    rules = engine(enter_seconds=0)
    rules.apply(person(), now=0)
    events = rules.apply(person(false_positive=True), now=1)
    assert types(events) == ["person_left_zone", "zone_occupancy_changed"]


def test_timeout_can_be_enabled_and_defaults_off():
    quiet = engine(enter_seconds=0, track_timeout_seconds=0)
    quiet.apply(person(), now=0)
    assert quiet.tick(now=10_000) == []
    assert quiet.occupancy()["dining"] == 1

    expiring = engine(enter_seconds=0, track_timeout_seconds=10)
    expiring.apply(person(), now=0)
    assert expiring.tick(now=9) == []
    events = expiring.tick(now=10)
    assert types(events) == ["person_left_zone", "zone_occupancy_changed"]
    assert events[0]["reason"] == "timeout"
    assert expiring.occupancy()["dining"] == 0


def test_emitted_events_have_no_identity_keys():
    rules = engine(enter_seconds=0)
    events = rules.apply(person(), now=0)

    def walk(value):
        if isinstance(value, dict):
            assert BANNED.isdisjoint(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for event in events:
        walk(event)
        assert event["schema"] == "ammadis.vision.event.v1"
        assert event["label"] == "person"


def test_rules_module_does_not_mention_the_vision_engine():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "ammadis_edge" / "rules.py"
    ).read_text(encoding="utf-8").lower()
    assert "frigate" not in source
    assert "mqtt" not in source
    assert "face" not in source
