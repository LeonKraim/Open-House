"""`HAAdapter`, its transport seam, and the pure first-run setup flow.

Three groups, and they fail for different reasons:

- **The adapter's own behaviour**, over `FakeHaTransport`: the two things the
  adapter does that no other implementation does -- reading a change's origin out
  of Home Assistant's context when the adapter did not make it, and reconciling
  Home Assistant's single state-or-availability field into the port's two. These
  are the tests the contract suite cannot contain, because they name a fact about
  Home Assistant rather than about the port.
- **`RestTransport`'s wire handling**, over a canned HTTP layer: the same
  reconciliation, exercised through the parsing a live instance would feed it,
  with no network. The `status == 404` paths are here because a 404 is an answer
  ("Home Assistant does not hold this") and every other failure is not.
- **The setup flow**, which is pure and runs with no Home Assistant at all.
- **The live composition** (`ha_adapter.composition`), which is the one engine the
  running integration ticks, driven here over the fake transport and a virtual
  clock: the same composition the container uses, reachable in a checkout where
  `homeassistant` is not installed. What the container adds on top -- Home
  Assistant's interval, its state listeners, its unload -- is the last group.

A final, opt-in group drives a **live** container when one is reachable at the
`HA_BASE_URL` in `.env.local`; it is skipped otherwise, so the suite is green on
a machine with no Docker. What it can and cannot prove is reported, not assumed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from engine.adapter import (
    AdapterError,
    AdapterSnapshot,
    ChangeContext,
    ChangeOrigin,
    EntitySnapshot,
    EntityView,
    Fault,
    InvalidEntityIdError,
    UnknownEntityError,
)
from engine.behaviours import enable_key
from engine.binding import RoomScope
from engine.engine import Clock, Engine, EngineError
from engine.solar import Location
from engine.vocabulary import Vocabulary
from ha_adapter.adapter import HAAdapter, OriginNotAllowedError
from ha_adapter.composition import (
    LiveHouse,
    LiveHouseError,
    LiveRoom,
    SystemClock,
    build_live_house,
    mode_documents,
    mode_name,
    room_id,
)
from ha_adapter.setup_flow import (
    Area,
    BindingGuess,
    RoomSuggestion,
    SetupPlan,
    SetupStep,
    load_room_types,
    load_slot_domains,
    plan_setup,
    setup_steps,
)
from ha_adapter.testing import FakeHaTransport, build_adapter
from ha_adapter.transport import (
    UNAVAILABLE_STATE,
    HaApiError,
    HaState,
    RestTransport,
)
from sim.clock import VirtualClock
from tools.catalog import paths

# --------------------------------------------------------------------------
# Origin: the fact Home Assistant keeps in a context and the port keeps in a
# `ChangeOrigin`. The adapter is the only place the translation happens.
# --------------------------------------------------------------------------


def test_a_persons_change_reads_back_as_user() -> None:
    """A change the adapter did not make, with a `user_id`, is a user's."""
    adapter = build_adapter()
    adapter.add_entity("light.hall", "off", context=ChangeContext.world())
    transport: FakeHaTransport = adapter.transport  # type: ignore[assignment]
    transport.external_change("light.hall", "on", user_id="person-1")
    assert adapter.read_entity("light.hall").last_origin is ChangeOrigin.USER


def test_an_automations_change_reads_back_as_world() -> None:
    """The same change with no `user_id` is the world's, not a person's.

    This is the pair that matters: an adapter that called every external change
    a user would suppress every behaviour the moment an automation touched an
    entity, and one that called none of them a user would never notice a hand at
    the wall.
    """
    adapter = build_adapter()
    adapter.add_entity("light.hall", "off", context=ChangeContext.world())
    transport: FakeHaTransport = adapter.transport  # type: ignore[assignment]
    transport.external_change("light.hall", "on")
    assert adapter.read_entity("light.hall").last_origin is ChangeOrigin.WORLD


def test_the_adapters_own_write_is_not_read_back_as_the_users_who_made_it() -> None:
    """A write through Home Assistant is stamped with the token's user; the port
    must still call it the engine's.

    This is the whole reason the adapter keeps an origin memory. Home Assistamt
    has no field for "the engine wrote this"; its context only carries the user
    behind the authenticated call. Without the memory, every actuation would
    read back as a manual override and permanently suppress the behaviour that
    made it.
    """
    adapter = build_adapter()
    adapter.add_entity("light.hall", "off", context=ChangeContext.world())
    transport: FakeHaTransport = adapter.transport  # type: ignore[assignment]
    # The transport stamps the write with a user, exactly as a real instance does.
    adapter.actuate("light.hall", "on", context=ChangeContext.engine())
    transport.external_change("light.hall", "on", user_id="token-user")
    # The state did not move, so the recorded engine origin stands.
    assert adapter.read_entity("light.hall").last_origin is ChangeOrigin.ENGINE


# --------------------------------------------------------------------------
# Availability: Home Assistant has one field; the port has two.
# --------------------------------------------------------------------------


def test_an_entity_the_house_calls_unavailable_keeps_its_last_known_state() -> None:
    """`"unavailable"` never reaches the engine as a state it could act on."""
    adapter = build_adapter()
    adapter.add_entity("light.hall", "on", context=ChangeContext.world())
    transport: FakeHaTransport = adapter.transport  # type: ignore[assignment]
    transport.make_unavailable("light.hall")

    view = adapter.read_entity("light.hall")
    assert view.available is False
    assert view.state == "on"


def test_the_transport_reconciles_the_wires_one_field_into_the_ports_two() -> None:
    """The fake loses the state on the wire, exactly as a real instance does.

    This is what keeps the seam honest. An entity told to go unavailable really
    carries `"unavailable"` underneath, so the last known state a read returns is
    the transport's memory rather than a fiction -- and `HAAdapter` above it
    never sees the sentinel as a state it could act on.
    """
    transport = FakeHaTransport()
    transport.set_state("light.hall", "on")
    transport.set_state("light.hall", "on", available=False)

    assert transport.wire_state("light.hall") == UNAVAILABLE_STATE

    view = transport.state("light.hall")
    assert view is not None
    assert view.available is False
    assert view.state == "on"


# --------------------------------------------------------------------------
# The rest of the port, where the adapter's behaviour differs from the fake's
# --------------------------------------------------------------------------


def test_restart_resyncs_and_keeps_an_unavailable_entity_unavailable() -> None:
    """Unavailable is not off, through a restart, on the adapter too."""
    adapter = build_adapter()
    adapter.add_entity("light.hall", "on", context=ChangeContext.world())
    transport: FakeHaTransport = adapter.transport  # type: ignore[assignment]
    transport.make_unavailable("light.hall")
    adapter.restart(context=ChangeContext.world())
    view = adapter.read_entity("light.hall")
    assert view.available is False
    assert view.state == "on"


def test_restart_is_never_a_user_origin() -> None:
    adapter = build_adapter()
    adapter.add_entity("light.hall", "on", context=ChangeContext.world())
    adapter.restart(context=ChangeContext.world())
    assert adapter.read_entity("light.hall").last_origin is not ChangeOrigin.USER


def test_only_an_actuation_may_carry_a_user_context() -> None:
    """The fake's rule is the adapter's rule: fail closed on a false `user`."""
    adapter = build_adapter()
    adapter.add_entity("light.hall", "on", context=ChangeContext.world())
    for operation in (
        lambda: adapter.set_availability(
            "light.hall", available=False, context=ChangeContext.user()
        ),
        lambda: adapter.remove_entity("light.hall", context=ChangeContext.user()),
        lambda: adapter.restart(context=ChangeContext.user()),
    ):
        with pytest.raises(OriginNotAllowedError):
            operation()


def test_a_duplicate_add_is_refused() -> None:
    adapter = build_adapter()
    adapter.add_entity("light.hall", "on", context=ChangeContext.world())
    with pytest.raises(AdapterError):
        adapter.add_entity("light.hall", "off", context=ChangeContext.world())


def test_a_read_of_an_unknown_entity_fails_naming_it() -> None:
    with pytest.raises(UnknownEntityError) as raised:
        build_adapter().read_entity("light.ghost")
    assert raised.value.entity_id == "light.ghost"


def test_a_malformed_id_is_rejected_before_the_house_is_asked() -> None:
    with pytest.raises(InvalidEntityIdError):
        build_adapter().read_entity("Light.hall")


def test_the_snapshot_carries_the_startup_condition_beside_the_live_one() -> None:
    """An entity driven after it was added restarts where it was configured."""
    adapter = build_adapter()
    adapter.add_entity(
        "light.hall", "off", attributes={"brightness": 1}, context=ChangeContext.world()
    )
    adapter.actuate("light.hall", "on", context=ChangeContext.engine())

    snapshot = adapter.snapshot()
    assert isinstance(snapshot, AdapterSnapshot)
    entry = _entry(snapshot, "light.hall")
    assert entry.state == "on"
    assert entry.startup_state == "off"
    assert dict(entry.startup_attributes) == {"brightness": 1}


def test_a_fault_is_recorded_as_a_fault() -> None:
    adapter = build_adapter()
    adapter.add_entity("sensor.hall", "42", context=ChangeContext.world())
    adapter.inject_fault(
        "sensor.hall", Fault(available=False), context=ChangeContext.fault()
    )
    view = adapter.read_entity("sensor.hall")
    assert view.last_origin is ChangeOrigin.FAULT
    assert view.available is False
    assert view.state == "42"


def test_the_adapter_exposes_only_the_port_over_the_fake() -> None:
    """`HAAdapter` is a `HouseAdapter`; the smoke the contract suite repeats."""
    adapter: HAAdapter = build_adapter()
    adapter.add_entity("light.hall", "off", context=ChangeContext.world())
    assert isinstance(adapter.read_entity("light.hall"), EntityView)
    assert adapter.list_entities() == ("light.hall",)


def _entry(snapshot: AdapterSnapshot, entity_id: str) -> EntitySnapshot:
    for candidate in snapshot.entities:
        if candidate.entity_id == entity_id:
            return candidate
    raise AssertionError(f"no snapshot entry for {entity_id!r}")


# --------------------------------------------------------------------------
# `RestTransport`'s wire handling, with no network: a canned `_request`.
# --------------------------------------------------------------------------


class _Canned(RestTransport):
    """A `RestTransport` whose HTTP layer is a dict of canned answers.

    Subclasses the real transport so the parsing, the availability memory and
    the 404 handling under test are the shipped ones; only the socket is
    replaced.
    """

    def __init__(self, answers: Mapping[tuple[str, str], object]) -> None:
        super().__init__(base_url="http://ha.invalid", token="t")
        self._answers = answers
        self.calls: list[tuple[str, str, object]] = []

    def _request(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> object:
        self.calls.append((method, path, payload))
        answer = self._answers.get((method, path))
        if isinstance(answer, HaApiError):
            raise answer
        if answer is None:
            raise HaApiError(f"{method} {path} not canned", status=500)
        return answer


def _wire(
    entity_id: str, state: str, *, user_id: str | None = None
) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {"friendly_name": entity_id},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "context": {"id": "c", "user_id": user_id},
    }


def test_the_rest_transport_reads_a_state_object_into_the_ports_terms() -> None:
    transport = _Canned(
        {("GET", "/api/states/light.hall"): _wire("light.hall", "on", user_id="u1")}
    )
    state = transport.state("light.hall")
    assert state == HaState(
        entity_id="light.hall",
        state="on",
        attributes={"friendly_name": "light.hall"},
        available=True,
        user_id="u1",
    )


def test_the_rest_transport_turns_the_wires_unavailable_into_a_last_known_state() -> (
    None
):
    """The wire forgets the prior state; the transport remembers it."""
    transport = _Canned({("GET", "/api/states/light.hall"): _wire("light.hall", "on")})
    assert transport.state("light.hall") is not None
    transport._answers[("GET", "/api/states/light.hall")] = _wire(
        "light.hall", UNAVAILABLE_STATE
    )
    state = transport.state("light.hall")
    assert state is not None
    assert state.available is False
    assert state.state == "on"


def test_the_rest_transport_answers_none_for_a_404_and_raises_for_the_rest() -> None:
    """A 404 is an answer; any other status is a failure."""
    missing = _Canned(
        {("GET", "/api/states/light.ghost"): HaApiError("nope", status=404)}
    )
    assert missing.state("light.ghost") is None

    broken = _Canned(
        {("GET", "/api/states/light.hall"): HaApiError("boom", status=500)}
    )
    with pytest.raises(HaApiError):
        broken.state("light.hall")


def test_the_rest_transport_writes_through_the_state_endpoint() -> None:
    transport = _Canned({("POST", "/api/states/light.hall"): _wire("light.hall", "on")})
    transport.set_state("light.hall", "on", attributes={"brightness": 4})
    method, path, payload = transport.calls[-1]
    assert (method, path) == ("POST", "/api/states/light.hall")
    assert isinstance(payload, dict)
    assert payload["state"] == "on"
    assert payload["attributes"] == {"brightness": 4}


def test_the_rest_transport_writes_unavailable_as_the_wire_state() -> None:
    transport = _Canned(
        {("POST", "/api/states/light.hall"): _wire("light.hall", UNAVAILABLE_STATE)}
    )
    transport.set_state("light.hall", "on")
    transport.set_state("light.hall", "on", available=False)
    payload = transport.calls[-1][2]
    assert isinstance(payload, dict)
    assert payload["state"] == UNAVAILABLE_STATE


def test_the_rest_transport_removes_and_reports_whether_it_was_there() -> None:
    transport = _Canned({("DELETE", "/api/states/light.hall"): {}})
    assert transport.remove("light.hall") is True

    missing = _Canned(
        {("DELETE", "/api/states/light.ghost"): HaApiError("nope", status=404)}
    )
    assert missing.remove("light.ghost") is False


def test_the_rest_transport_lists_states_in_a_stable_order() -> None:
    transport = _Canned(
        {
            ("GET", "/api/states"): [
                _wire("light.z", "on"),
                _wire("light.a", "off"),
            ]
        }
    )
    assert [state.entity_id for state in transport.states()] == ["light.a", "light.z"]


# --------------------------------------------------------------------------
# The setup flow: pure, and the spec's six named steps.
# --------------------------------------------------------------------------


def test_the_flow_has_the_six_steps_the_spec_names() -> None:
    assert setup_steps() == (
        SetupStep.CONFIRM_AREAS,
        SetupStep.PICK_ROOM_TYPES,
        SetupStep.GUESS_BINDINGS,
        SetupStep.CHOOSE_PEOPLE,
        SetupStep.REVIEW,
        SetupStep.ACTIVATE,
    )


def _vocabulary() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    return load_room_types(paths.ROOT), load_slot_domains(paths.ROOT)


def test_an_areas_name_suggests_its_room_type() -> None:
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[
            Area("kitchen", "Kitchen", ("light.kitchen", "sensor.kitchen_temperature"))
        ],
        people=[("p1", "Ada")],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    room = plan.rooms[0]
    assert isinstance(room, RoomSuggestion)
    assert room.room_type == "kitchen"
    assert room.confident is True


def test_an_unknown_area_name_falls_back_and_says_so() -> None:
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[Area("area_1", "Area 1")],
        people=[],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    assert plan.rooms[0].confident is False


def test_a_binding_guess_respects_the_slot_domains() -> None:
    """A light is never guessed for a motion slot, whatever the name."""
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[
            Area(
                "kitchen",
                "Kitchen",
                ("light.kitchen_pendants", "binary_sensor.kitchen_motion"),
            )
        ],
        people=[],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    guesses = {guess.slot: guess.entity_id for guess in plan.bindings_for("kitchen")}
    assert guesses["light_group"] == "light.kitchen_pendants"
    assert guesses["motion_sensor"] == "binary_sensor.kitchen_motion"


def test_an_unbindable_slot_is_left_unbound_rather_than_misbound() -> None:
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[Area("kitchen", "Kitchen", ("light.kitchen",))],
        people=[],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    motion = next(g for g in plan.bindings_for("kitchen") if g.slot == "motion_sensor")
    assert isinstance(motion, BindingGuess)
    assert motion.entity_id is None


def test_the_review_is_plain_language_and_names_the_devices() -> None:
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[Area("kitchen", "Kitchen", ("light.kitchen",))],
        people=[("p1", "Ada"), ("p2", "Guest")],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    assert len(plan.review) == 1
    text = plan.review[0].text
    assert "kitchen" in text
    assert "light.kitchen" in text
    assert "slot" not in text  # the review is prose, not the vocabulary


def test_people_chosen_for_away_detection_default_to_on() -> None:
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[],
        people=[("p1", "Ada"), ("p2", "Guest")],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    assert all(person.for_away_detection for person in plan.people)


def test_the_activated_document_is_json_shaped_and_names_its_rooms() -> None:
    """Activate writes a plain structure Home Assistant can store."""
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[Area("kitchen", "Kitchen", ("light.kitchen",))],
        people=[("p1", "Ada")],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    document = plan.to_document()
    assert json.loads(json.dumps(document)) == document  # JSON round-trips
    rooms = document["rooms"]
    assert isinstance(rooms, list)
    assert rooms[0]["subentry_type"] == "room"
    assert rooms[0]["area_id"] == "kitchen"
    assert rooms[0]["room_type"] == "kitchen"
    assert document["away_people"] == ["p1"]


def test_the_plan_is_a_value_not_a_handle() -> None:
    room_types, slot_domains = _vocabulary()
    plan = plan_setup(
        areas=[Area("kitchen", "Kitchen", ("light.kitchen",))],
        people=[],
        room_types=room_types,
        slot_domains=slot_domains,
    )
    assert isinstance(plan, SetupPlan)
    assert plan.bindings_for("kitchen")


# --------------------------------------------------------------------------
# The live engine: the one engine, over the port, on the clock it is given.
#
# These are the tests the integration's `automation.py` relies on and cannot run
# itself: `ha_adapter.composition` imports no Home Assistant, so the same
# composition the container uses is driven here over `FakeHaTransport` and a
# `VirtualClock`, and the quiet timeout it would otherwise make a live test wait
# five minutes for is reached by advancing the clock. What is *not* tested here
# is the Home Assistant glue -- the interval, the state listeners, the unload --
# because that names `homeassistant`, which is not installed in this checkout.
# --------------------------------------------------------------------------

_LOCATION = Location(latitude=51.5074, longitude=-0.1278, time_zone="Europe/London")
_STARTED_AT = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)
_QUIET_TIMEOUT = timedelta(seconds=301)


def _house(
    *,
    clock: Clock,
    transport: FakeHaTransport,
    auto_lighting: bool = True,
    motion: str = "on",
    lux: str = "5",
    bind_lux: bool = True,
    house_settings: Mapping[str, object] | None = None,
) -> LiveHouse:
    """A one-room live house over a fake transport, for the tests to drive."""
    transport.set_state("binary_sensor.hall_motion", "off")
    transport.set_state("light.hall", "off")
    if bind_lux:
        transport.set_state("sensor.hall_lux", lux)
    bindings = {
        "motion_sensor": "binary_sensor.hall_motion",
        "light_group": "light.hall",
    }
    if bind_lux:
        bindings["lux_sensor"] = "sensor.hall_lux"
    house = build_live_house(
        house_name="Test house",
        rooms=[
            LiveRoom(
                id=room_id("hall"),
                name="Hall",
                type="hallway",
                bindings=bindings,
                auto_lighting=auto_lighting,
            )
        ],
        modes=("Home", "Away", "Sleep", "Guest"),
        transport=transport,
        vocabulary_root=paths.ROOT,
        location=_LOCATION,
        clock=clock,
        house_settings=house_settings,
    )
    transport.external_change("binary_sensor.hall_motion", motion)
    return house


def _adapter_state(transport: FakeHaTransport, entity_id: str) -> str:
    state = transport.state(entity_id)
    assert state is not None
    return state.state


def test_the_composition_builds_the_one_engine() -> None:
    """The live house is the engine the simulator builds, not a second one."""
    clock = VirtualClock.started_at(_STARTED_AT)
    house = _house(clock=clock, transport=FakeHaTransport())
    assert isinstance(house.engine, Engine)
    assert isinstance(house.adapter, HAAdapter)


def test_a_house_with_no_rooms_cannot_be_composed() -> None:
    with pytest.raises(LiveHouseError):
        build_live_house(
            house_name="Empty",
            rooms=[],
            modes=("Home",),
            transport=FakeHaTransport(),
            vocabulary_root=paths.ROOT,
            location=_LOCATION,
            clock=VirtualClock.started_at(_STARTED_AT),
        )


def test_an_area_id_is_projected_to_a_room_the_schema_accepts() -> None:
    """A slug the area name produces still has to fit `^[a-z][a-z0-9_]*$`."""
    assert room_id("living_room") == "living_room"
    assert room_id("2nd floor") == "room_2nd_floor"
    assert room_id("Loft") == "loft"


def test_a_mode_label_projects_to_the_name_the_engine_gates_on() -> None:
    """`away_shutdown` gates on the literal `away`, never on the display label."""
    assert mode_name("Away") == "away"
    documents = mode_documents(["Home", "Away", "home"])
    assert [document["name"] for document in documents] == ["home", "away"]
    assert all(document["exclusive_group"] == "presence" for document in documents)


def test_motion_in_the_dark_lights_the_room_through_the_engine() -> None:
    """The loop the whole phase exists for: a sensor moves, a lamp answers."""
    clock = VirtualClock.started_at(_STARTED_AT)
    transport = FakeHaTransport()
    house = _house(clock=clock, transport=transport)

    assert _adapter_state(transport, "light.hall") == "off"
    clock.advance(timedelta(seconds=30))
    records = house.tick()

    assert _adapter_state(transport, "light.hall") == "on"
    acted = [
        record
        for record in records
        if record.actor == "motion_lighting"
        and record.rule == "lighting.motion_light_on"
    ]
    assert acted, "motion lighting should have acted"


def test_motion_in_a_bright_room_is_left_alone() -> None:
    """The lux reading is the dark test: a daylit room is not lit for a passer-by."""
    clock = VirtualClock.started_at(_STARTED_AT)
    transport = FakeHaTransport()
    house = _house(clock=clock, transport=transport, lux="500")

    clock.advance(timedelta(seconds=30))
    house.tick()

    assert _adapter_state(transport, "light.hall") == "off"


def test_an_unreadable_lux_sensor_falls_back_to_the_sun() -> None:
    """A bound sensor that cannot answer does not decide; the sun branch does."""
    clock = VirtualClock.started_at(_STARTED_AT)
    transport = FakeHaTransport()
    house = _house(clock=clock, transport=transport)
    transport.make_unavailable("sensor.hall_lux")

    clock.advance(timedelta(seconds=30))
    house.tick()

    # The fixture instant is 20:00 UTC in January in London: the sun is below the
    # fog threshold, so the fallback branch lights the room.
    assert _adapter_state(transport, "light.hall") == "on"


def test_the_light_goes_off_after_the_quiet_timeout() -> None:
    """The off half of motion lighting, reached by advancing the clock."""
    clock = VirtualClock.started_at(_STARTED_AT)
    transport = FakeHaTransport()
    house = _house(clock=clock, transport=transport)

    clock.advance(timedelta(seconds=30))
    house.tick()
    assert _adapter_state(transport, "light.hall") == "on"

    transport.external_change("binary_sensor.hall_motion", "off")
    clock.advance(_QUIET_TIMEOUT)
    records = house.tick()

    assert _adapter_state(transport, "light.hall") == "off"
    assert any(
        record.actor == "motion_lighting"
        and record.rule == "lighting.motion_light_off"
        and record.outcome.value == "acted"
        for record in records
    )


def test_the_auto_lighting_switch_silences_the_room_and_lets_it_speak_again() -> None:
    """The switch is the room's permission, wired to the engine's enable flag."""
    clock = VirtualClock.started_at(_STARTED_AT)
    transport = FakeHaTransport()
    house = _house(clock=clock, transport=transport, auto_lighting=False)

    clock.advance(timedelta(seconds=30))
    house.tick()
    assert _adapter_state(transport, "light.hall") == "off"

    house.set_room_auto_lighting(room_id("hall"), on=True)
    clock.advance(timedelta(seconds=30))
    house.tick()
    assert _adapter_state(transport, "light.hall") == "on"


def test_an_unbound_house_scoped_slot_is_skipped_rather_than_fatal() -> None:
    """A house scope that omitted `house_mode` would raise where it should skip.

    `away_shutdown` requires `house_mode`, which no lighting-only room binds, and
    the house-scope resolver raises for a slot the scope does not declare before
    it can notice the binding is empty. The composition declares the vocabulary's
    whole house scope so that this tick records `skipped: unbound slot` instead of
    failing -- the difference between a house that runs and one that does not.
    """
    clock = VirtualClock.started_at(_STARTED_AT)
    transport = FakeHaTransport()
    house = _house(
        clock=clock,
        transport=transport,
        house_settings={enable_key("away_shutdown"): True},
    )

    clock.advance(timedelta(seconds=30))
    records = house.tick()

    away = [record for record in records if record.actor == "away_shutdown"]
    assert away, "the house-scoped behaviour should have been evaluated"
    assert away[0].outcome.value == "skipped: unbound slot"


def test_the_engine_cannot_advance_a_real_house_clock() -> None:
    """Home Assistant has no virtual clock, and the engine must not pretend it does.

    `SystemClock` deliberately has no `advance`, so `Engine.advance` -- the move a
    scenario makes -- fails loudly on a real house rather than silently skipping
    the instant it was told to pass. Time here is the scheduler's, and `tick` is
    the only way it is spent.
    """
    assert SystemClock().now.tzinfo is not None
    assert not hasattr(SystemClock(), "advance")

    house = _house(clock=SystemClock(), transport=FakeHaTransport())
    with pytest.raises(EngineError):
        house.engine.advance(timedelta(seconds=1))


def test_the_house_scope_declares_the_vocabularys_house_slots() -> None:
    """The document's house scope is the vocabulary's, not the rooms' bindings.

    The resolver *raises* for a slot the house scope omits (`resolve_slot`), so
    the scope has to be the whole vocabulary list rather than the slots a room
    binds. The assertion is equality against `Vocabulary.house_slots`, not
    membership of two names: a scope that happened to carry exactly the two slots
    this house binds would pass a membership check and still crash the tick of
    the next house-scoped behaviour the vocabulary gains.
    """
    house = _house(
        clock=VirtualClock.started_at(_STARTED_AT), transport=FakeHaTransport()
    )
    declared = house.engine.house.house_scope_slots
    vocabulary = Vocabulary.load(paths.ROOT)

    assert set(declared) == set(vocabulary.house_slots)
    # `house_mode` is the slot that makes it matter: no room of a lighting-only
    # house binds it, so declaring it is what turns the raise into a
    # `skipped: unbound slot`.
    assert "house_mode" in declared
    bound = {slot for room in house.engine.house.rooms for slot in room.bindings}
    assert "house_mode" not in bound


def test_a_rooms_switch_becomes_the_engines_enable_flag() -> None:
    """What `automation.py` calls is a setting on the engine, and nothing else."""
    house = _house(
        clock=VirtualClock.started_at(_STARTED_AT), transport=FakeHaTransport()
    )
    key = enable_key("motion_lighting")
    scope = RoomScope(room_id("hall"))

    assert house.engine.resolve(key, scope).flag() is True
    house.set_room_auto_lighting(room_id("hall"), on=False)
    assert house.engine.resolve(key, scope).flag() is False


# --------------------------------------------------------------------------
# The live container, when there is one. Skipped otherwise.
# --------------------------------------------------------------------------


def _live_credentials() -> tuple[str, str] | None:
    """The `HA_BASE_URL` and `HA_TOKEN` from `.env.local`, if it has them."""
    env_file = paths.ROOT / ".env.local"
    if not env_file.is_file():
        return None
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in {"HA_BASE_URL", "HA_TOKEN"} and value.strip():
            values[name.strip()] = value.strip()
    if "HA_BASE_URL" not in values or "HA_TOKEN" not in values:
        return None
    return values["HA_BASE_URL"], values["HA_TOKEN"]


def _live_reachable(base_url: str, token: str) -> bool:
    try:
        RestTransport(base_url=base_url, token=token, timeout=3.0).states()
    except HaApiError:
        return False
    return True


@pytest.fixture
def live_transport() -> RestTransport:
    """A transport against the running container, or a skip with a reason."""
    credentials = _live_credentials()
    if credentials is None:
        pytest.skip("no HA_BASE_URL/HA_TOKEN in .env.local")
    base_url, token = credentials
    if not _live_reachable(base_url, token):
        pytest.skip(f"no Home Assistant answering at {base_url}")
    return RestTransport(base_url=base_url, token=token)


def test_live_adapter_reads_actuates_and_removes_a_state(
    live_transport: RestTransport,
) -> None:
    """What the live container can prove about the adapter, end to end.

    The adapter's *control* face is exercised against the instance: an entity is
    created, read, driven, taken unavailable and removed. This is the half a
    harness needs and the half Home Assistant's REST API supports directly. The
    origin and restart behaviour is asserted over the fake, because a live
    instance's origin is the token's user for every write and cannot be forced
    to a fault.
    """
    adapter = HAAdapter(transport=live_transport)
    entity_id = "light.openhouse_adapter_test"
    if live_transport.state(entity_id) is not None:
        live_transport.remove(entity_id)
    try:
        adapter.add_entity(
            entity_id,
            "off",
            attributes={"friendly_name": "Open House probe"},
            context=ChangeContext.world(),
        )
        assert adapter.read_entity(entity_id).state == "off"

        adapter.actuate(entity_id, "on", context=ChangeContext.engine())
        assert adapter.read_entity(entity_id).state == "on"

        adapter.set_availability(
            entity_id, available=False, context=ChangeContext.world()
        )
        view = adapter.read_entity(entity_id)
        assert view.available is False
        assert view.state == "on"
        assert entity_id in adapter.list_entities()
    finally:
        if live_transport.state(entity_id) is not None:
            live_transport.remove(entity_id)


def test_live_house_lists_the_containers_entities(
    live_transport: RestTransport,
) -> None:
    """The adapter reads a real house, whatever is in it."""
    adapter = HAAdapter(transport=live_transport)
    entities = adapter.list_entities()
    assert isinstance(entities, Sequence)
    assert entities, "the container should hold at least one entity"
    # Every listed id is readable and well-formed.
    for entity_id in entities[:5]:
        assert isinstance(adapter.read_entity(entity_id), EntityView)
