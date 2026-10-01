"""The live session: the wiring a rebuild needs, and the edits that need one.

Every test here is about the same property. The engine a live house decides for
is a pure function of the state it starts from, so a session is correct exactly
when the engine it currently holds is the one its *fields* describe -- after a
rebinding, after a pack change, after a restart. A test that only checked the
field would miss the whole point, so each one reads back through `.engine`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.binding import House
from engine.install import InstalledSet
from engine.profiles import ProfileSet, load_profile_schema
from engine.solar import Location
from ha_adapter.composition import LiveRoom, mode_name, room_id
from ha_adapter.live import SESSION_STATE_VERSION, LiveSession, LiveSessionError
from ha_adapter.testing import FakeHaTransport

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")


def _transport() -> FakeHaTransport:
    transport = FakeHaTransport()
    transport.set_state("binary_sensor.hall_motion", "off")
    transport.set_state("sensor.hall_lux", "12")
    transport.set_state("light.hall", "off")
    return transport


def _room(name: str = "hall") -> LiveRoom:
    return LiveRoom(
        id=room_id(name),
        name=name.title(),
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "lux_sensor": "sensor.hall_lux",
            "light_group": "light.hall",
        },
    )


def _session(rooms: tuple[LiveRoom, ...] | None = None) -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=rooms if rooms is not None else (_room(),),
        modes=("Home", "Away"),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )


def test_a_built_session_holds_the_house_its_rooms_describe() -> None:
    session = _session()
    house = session.engine.house
    assert isinstance(house, House)
    assert [room.id for room in house.rooms] == ["hall"]
    assert house.room("hall").bindings["light_group"] == "light.hall"


def test_the_modes_are_the_labels_the_session_was_built_with() -> None:
    """A label reaches the engine as the name it gates on, not as the label."""
    session = _session()
    session.set_house_mode("Away")

    # `active` is a set because a mode set may hold several exclusive groups;
    # the presence modes are one group, so choosing Away leaves only Away.
    assert session.engine.modes.active == frozenset({mode_name("Away")})


def test_rebinding_a_slot_rebuilds_the_engine_and_the_house_agrees() -> None:
    session = _session()
    session.bind("hall", "light_group", "light.other")

    # Read back through the *engine*, not the field: a session that changed its
    # own copy and did not rebuild would pass a check on `session.rooms` alone.
    assert session.engine.house.room("hall").bindings["light_group"] == "light.other"
    assert session.room("hall").bindings["light_group"] == "light.other"


def test_unbinding_leaves_the_slot_absent_rather_than_empty() -> None:
    session = _session()
    session.unbind("hall", "light_group")
    bindings = session.engine.house.room("hall").bindings
    assert "light_group" not in bindings


def test_a_rebinding_keeps_the_other_slots() -> None:
    session = _session()
    session.bind("hall", "light_group", "light.other")
    bindings = session.engine.house.room("hall").bindings
    assert bindings["motion_sensor"] == "binary_sensor.hall_motion"
    assert len(bindings) == 3


def test_an_unknown_room_is_refused_by_name() -> None:
    session = _session()
    with pytest.raises(LiveSessionError, match="no room 'kitchen'"):
        session.require_room("kitchen")


def test_an_unknown_slot_is_refused_rather_than_created() -> None:
    session = _session()
    with pytest.raises(LiveSessionError, match="no slot 'flamethrower'"):
        session.bind("hall", "flamethrower", "light.hall")


def test_two_rooms_with_one_id_are_refused_at_construction() -> None:
    with pytest.raises(LiveSessionError, match="two rooms share the id"):
        _session((_room(), _room()))


def test_a_house_with_no_rooms_is_refused() -> None:
    with pytest.raises(LiveSessionError, match="at least one room"):
        _session(())


def test_the_adapter_survives_a_rebuild() -> None:
    """An entity that dropped out keeps its last known state across an edit."""
    session = _session()
    transport = session.transport
    assert isinstance(transport, FakeHaTransport)
    transport.set_state("light.hall", "on")
    assert session.adapter is not None
    first = session.adapter
    session.bind("hall", "light_group", "light.hall")

    assert session.adapter is first


def test_state_round_trips_through_a_document() -> None:
    session = _session()
    session.bind("hall", "light_group", "light.other")

    rebuilt = LiveSession.from_state(
        session.to_state(),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
    )

    assert rebuilt.rooms == session.rooms
    assert rebuilt.house_name == session.house_name
    assert rebuilt.engine.house.room("hall").bindings["light_group"] == "light.other"


def test_state_carries_the_packs_and_the_profiles() -> None:
    session = _session()
    session.set_profiles(ProfileSet([], schema=load_profile_schema(ROOT)))
    document = session.to_state()

    assert document["version"] == SESSION_STATE_VERSION
    assert document["installed"] == InstalledSet().to_document()
    assert document["profiles"]["profiles"] == []


def test_a_state_from_another_version_is_refused_by_name() -> None:
    document = dict(_session().to_state())
    document["version"] = "0.0.1"
    # Escaped rather than left as `"version '0.0.1'"`: the dots would match any
    # character, so the pattern as written would also pass for `0x0y1` -- a
    # refusal that named the wrong version would read as the right one.
    with pytest.raises(LiveSessionError, match=r"version '0\.0\.1'"):
        LiveSession.from_state(
            document, transport=_transport(), root=ROOT, location=LOCATION
        )


def test_turning_auto_lighting_off_is_a_permission_and_not_an_actuation() -> None:
    """The light is still off afterwards, and the engine's flag says why."""
    session = _session()
    session.set_room_auto_lighting("hall", on=False)
    transport = session.transport
    assert isinstance(transport, FakeHaTransport)
    assert transport.state("light.hall") is not None
    assert transport.state("light.hall").state == "off"


def test_a_hallway_is_a_real_room_type_in_the_catalog() -> None:
    """The vocabulary is read from the checkout, not restated by this module.

    A session built over a checkout whose catalog had lost a slot would bind
    nothing and decide nothing, silently. Asserting the slot the fixture room
    binds is *known* is what makes a missing catalog fail here rather than as a
    house that never lights its hall.
    """
    session = _session()
    assert "light_group" in session.vocabulary.slots
    assert "motion_sensor" in session.vocabulary.slots
