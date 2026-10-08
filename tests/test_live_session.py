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

from engine.behaviours import enable_key
from engine.binding import House, RoomScope
from engine.config import Layer, UnresolvedSettingError
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
            "ambient_light_sensor": "sensor.hall_lux",
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


def test_a_rooms_settings_survive_a_rebuild_and_a_restart() -> None:
    """The half of the write that is not an override, read the way a restart reads it.

    A rebuild is what every profile activation performs and a restart is a new
    process, and both read the room layer from `room_settings` rather than from
    the in-memory override -- so this asserts through a rebuild, then through a
    document, and neither may lose the value.
    """
    session = _session()
    key = enable_key("motion_lighting")
    scope = RoomScope("hall")
    session.remember_setting(key, scope, False)

    assert session.setting(key, scope) is False
    assert session.engine.settings.resolve(key, scope).value is False

    session.rebuild()
    assert session.engine.settings.resolve(key, scope).value is False

    restarted = LiveSession.from_state(
        session.to_state(), transport=_transport(), root=ROOT, location=LOCATION
    )
    assert restarted.engine.settings.resolve(key, scope).value is False


def test_forgetting_a_setting_removes_it_from_both_layers() -> None:
    """The pair's other half: "off" is an absence, and both layers show it.

    The key is one no layer supplies, which is what makes the assertion exact:
    after the write the ROOM layer answers, and after the forget there is no
    answer at all. A key with a built-in default would resolve to that default
    either way, which is a weaker fact about the same code.
    """
    session = _session()
    key = "behaviour.nothing_declares_this.enabled"
    scope = RoomScope("hall")
    session.remember_setting(key, scope, False)
    # Resolved it is the override that answers, which is the live half; rebuilt,
    # the override is gone and the recorded ROOM value is what still answers --
    # which is the half that survives a restart.
    assert session.engine.settings.resolve(key, scope).layer is Layer.OVERRIDE
    session.rebuild()
    assert session.engine.settings.resolve(key, scope).layer is Layer.ROOM

    session.forget_setting(key, scope)

    assert session.setting(key, scope) is None
    assert session.room_settings_for("hall") == {}
    with pytest.raises(UnresolvedSettingError):
        session.engine.settings.resolve(key, scope)


def test_turning_a_rooms_switch_off_clears_a_remembered_lighting_flag() -> None:
    """The switch is the master for the lighting behaviours, so it wins.

    A remembered flag is laid *over* the room's own switch at construction, so a
    stale `True` left behind would make the off position visibly do nothing.
    """
    session = _session()
    key = enable_key("motion_lighting")
    session.remember_setting(key, RoomScope("hall"), True)

    session.set_room_auto_lighting("hall", on=False)

    assert session.setting(key, RoomScope("hall")) is None
    session.rebuild()
    assert session.engine.settings.resolve(key, RoomScope("hall")).value is False


def test_a_removed_room_takes_its_settings_with_it() -> None:
    session = _session(rooms=(_room("hall"), _room("kitchen")))
    session.remember_setting(
        "behaviour.nothing_declares_this.enabled", RoomScope("hall"), True
    )

    session.set_rooms((_room("kitchen"),))

    assert session.room_settings_for("hall") == {}


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
