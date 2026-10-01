"""`FakeHouseAdapter` -- task 3.2.

The fake is the house every engine test drives, so the tests here hold it to the
three claims its own module makes and the contract suite leaves to the spec:
that it implements the whole port and adds nothing to it, that its restart
returns the house to a defined startup condition in which an unavailable device
is not off, and that only an actuation may carry a `user` origin.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation is worse than none: the phase's whole method is that a feature is
exercised through a mock, and a mock that answers every question the same way
exercises nothing.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from engine.adapter import (
    PORT_OPERATIONS,
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
from sim.adapter import FakeHouseAdapter, OriginNotAllowedError
from sim.clock import VirtualClock
from sim.entropy import RandomStream

if TYPE_CHECKING:
    from collections.abc import Callable


def _adapter() -> FakeHouseAdapter:
    """A fresh fake with a fixed clock and seed, so a failure is reproducible."""
    return FakeHouseAdapter(
        clock=VirtualClock.started_at(datetime(2026, 1, 1, tzinfo=UTC)),
        random_stream=RandomStream.from_seed(0),
    )


# --------------------------------------------------------------------------
# The fake's surface -- it is the port and nothing beside it
# --------------------------------------------------------------------------


def test_the_fake_is_constructed_with_its_substrate() -> None:
    """The clock and stream a run is built with are the ones the fake holds.

    A falsifying implementation would build its own `VirtualClock` or generator
    internally, so two fixtures given the same run's substrate would draw from
    different sources and a replay would diverge while every operation still
    looked correct.
    """
    clock = VirtualClock.started_at(datetime(2026, 6, 1, tzinfo=UTC))
    stream = RandomStream.from_seed(42)
    adapter = FakeHouseAdapter(clock=clock, random_stream=stream)
    assert adapter.clock is clock
    assert adapter.random_stream is stream


def test_the_fake_implements_the_whole_port_and_adds_nothing() -> None:
    """Its public methods are exactly the port's operations.

    A falsifying implementation with a `set_mode` or a `make_user_action` helper
    would let the engine bind to the fake's shape, and Phase 4's adapter would
    then fail to satisfy a port the engine only appeared to use. A missing
    operation fails the other way and is named here.
    """
    public = {
        name
        for name, _ in inspect.getmembers(FakeHouseAdapter, callable)
        if not name.startswith("_")
    }
    assert public == set(PORT_OPERATIONS)


# --------------------------------------------------------------------------
# The registry: add, remove, enumerate
# --------------------------------------------------------------------------


def test_an_entity_is_added_enumerated_and_removed() -> None:
    """Removal means absent, so a read fails rather than answering `off`."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    assert "light.kitchen" in set(adapter.list_entities())
    adapter.remove_entity("light.kitchen", context=ChangeContext.world())
    assert "light.kitchen" not in set(adapter.list_entities())
    with pytest.raises(UnknownEntityError):
        adapter.read_entity("light.kitchen")


def test_a_read_of_an_unknown_entity_fails_naming_it() -> None:
    adapter = _adapter()
    with pytest.raises(UnknownEntityError) as raised:
        adapter.read_entity("light.ghost")
    assert raised.value.entity_id == "light.ghost"


@pytest.mark.parametrize("bad", ["Light.kitchen", "kitchen", "light.", "light-kitchen"])
def test_a_read_rejects_an_id_outside_the_schema_shape(bad: str) -> None:
    """The id shape guards reads, so a fixture cannot name an unbindable entity."""
    adapter = _adapter()
    with pytest.raises(InvalidEntityIdError) as raised:
        adapter.read_entity(bad)
    assert raised.value.entity_id == bad


def test_adding_a_duplicate_entity_is_rejected() -> None:
    """A second `add_entity` for a live id is an error, not a silent clobber.

    A falsifying implementation would overwrite, hiding a fixture that adds the
    same device twice behind a scenario that still passes.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    with pytest.raises(AdapterError):
        adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())


# --------------------------------------------------------------------------
# Availability stands apart from state
# --------------------------------------------------------------------------


def test_marking_a_device_unavailable_is_not_off() -> None:
    """`available: false` and a state of `on` coexist, and the read says so.

    A falsifying implementation that reported an unavailable entity as `off`
    would let a behaviour read a dead sensor as "no motion" -- the mistake the
    corpus calls out most often.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())
    adapter.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    view = adapter.read_entity("light.kitchen")
    assert view.available is False
    assert view.state == "on"
    assert view.state != "off"


def test_a_device_returns_from_unavailable_without_an_actuation() -> None:
    """Returning to available moves availability only, leaving the state alone."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())
    adapter.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    adapter.set_availability(
        "light.kitchen", available=True, context=ChangeContext.world()
    )
    view = adapter.read_entity("light.kitchen")
    assert view.available is True
    assert view.state == "on"


def test_an_actuation_does_not_change_availability() -> None:
    """Writing a state to an unavailable entity leaves it unavailable."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())
    adapter.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    adapter.actuate("light.kitchen", "off", context=ChangeContext.engine())
    view = adapter.read_entity("light.kitchen")
    assert view.available is False
    assert view.state == "off"


# --------------------------------------------------------------------------
# Change context: the four origins and the one that may be a user
# --------------------------------------------------------------------------


def test_the_four_origins_are_distinguishable() -> None:
    """User, engine, world and fault are told apart by context alone."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    assert adapter.read_entity("light.kitchen").last_origin is ChangeOrigin.WORLD

    adapter.actuate("light.kitchen", "on", context=ChangeContext.user())
    assert adapter.read_entity("light.kitchen").last_origin is ChangeOrigin.USER

    adapter.actuate("light.kitchen", "on", context=ChangeContext.engine())
    assert adapter.read_entity("light.kitchen").last_origin is ChangeOrigin.ENGINE

    adapter.inject_fault(
        "light.kitchen", Fault(state="off"), context=ChangeContext.fault()
    )
    assert adapter.read_entity("light.kitchen").last_origin is ChangeOrigin.FAULT


def test_a_fault_is_injected_as_a_fault_origin() -> None:
    """A fault is its own origin, distinguishable from an engine or user write."""
    adapter = _adapter()
    adapter.add_entity("sensor.hall", "42", context=ChangeContext.world())
    adapter.inject_fault(
        "sensor.hall", Fault(available=False), context=ChangeContext.fault()
    )
    view = adapter.read_entity("sensor.hall")
    assert view.last_origin is ChangeOrigin.FAULT
    assert view.available is False
    assert view.state == "42"


def test_a_write_without_a_context_is_rejected_by_construction() -> None:
    """The argument has no default, so the omission is a `TypeError` at the call.

    A falsifying implementation that defaulted the context would let an
    engine-origin write present itself as manual three ticks later.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    with pytest.raises(TypeError):
        adapter.actuate("light.kitchen", "on")  # type: ignore[call-arg]


def _user_origin_calls() -> list[tuple[str, Callable[[FakeHouseAdapter], object]]]:
    return [
        (
            "add_entity",
            lambda a: a.add_entity("light.new", "on", context=ChangeContext.user()),
        ),
        (
            "remove_entity",
            lambda a: a.remove_entity("light.kitchen", context=ChangeContext.user()),
        ),
        (
            "set_availability",
            lambda a: a.set_availability(
                "light.kitchen", available=False, context=ChangeContext.user()
            ),
        ),
        (
            "inject_fault",
            lambda a: a.inject_fault(
                "light.kitchen", Fault(state="off"), context=ChangeContext.user()
            ),
        ),
        ("restart", lambda a: a.restart(context=ChangeContext.user())),
    ]


@pytest.mark.parametrize(
    ("operation", "call"),
    _user_origin_calls(),
    ids=[name for name, _ in _user_origin_calls()],
)
def test_only_an_actuation_may_carry_a_user_origin(
    operation: str, call: Callable[[FakeHouseAdapter], object]
) -> None:
    """Every non-actuation mutation refuses a `user` context, and changes nothing.

    A falsifying implementation would record the `user` origin it was handed, and
    a scenario asserting a behaviour fired would find it suppressed instead --
    the false `user` the house-adapter spec states the rule against.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    with pytest.raises(OriginNotAllowedError) as raised:
        call(adapter)
    assert raised.value.operation == operation
    # The refused call left the registry untouched.
    assert "light.new" not in set(adapter.list_entities())
    assert "light.kitchen" in set(adapter.list_entities())


# --------------------------------------------------------------------------
# Restart: a defined startup condition, in which unavailable is not off
# --------------------------------------------------------------------------


def test_restart_returns_the_house_to_its_startup_condition() -> None:
    """Each entity returns to the state it was added with.

    A falsifying implementation that left the live state in place -- restart as a
    no-op -- would make a restart-mid-absence scenario assert nothing.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    adapter.actuate("light.kitchen", "on", context=ChangeContext.engine())
    adapter.restart(context=ChangeContext.world())
    assert adapter.read_entity("light.kitchen").state == "off"


def test_restart_returns_a_stateless_light_to_its_unconfirmed_condition() -> None:
    """A light added in an unconfirmed state restarts unconfirmed, not `off`.

    A falsifying implementation that reset every light to `off` would report a
    state the hardware cannot confirm, which is the fiction the corpus's
    stateless-light edge case is about.
    """
    adapter = _adapter()
    adapter.add_entity("light.stair", "unknown", context=ChangeContext.world())
    adapter.actuate("light.stair", "on", context=ChangeContext.engine())
    adapter.restart(context=ChangeContext.world())
    assert adapter.read_entity("light.stair").state == "unknown"


def test_restart_preserves_an_unavailable_device() -> None:
    """An unavailable device crosses a restart still unavailable, not off.

    A falsifying implementation that reset availability would make a device whose
    integration has not reconnected read as switched off -- the
    unavailable-is-not-off rule in the place a restart would break it.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())
    adapter.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    adapter.restart(context=ChangeContext.world())
    view = adapter.read_entity("light.kitchen")
    assert view.available is False
    assert view.state != "off"


def test_restart_is_not_a_user_action() -> None:
    """No entity's last-writer origin is `user` after a restart."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    adapter.actuate("light.kitchen", "on", context=ChangeContext.user())
    adapter.restart(context=ChangeContext.world())
    for entity_id in adapter.list_entities():
        assert adapter.read_entity(entity_id).last_origin is not ChangeOrigin.USER


def test_restart_is_observable_as_the_restart_origin() -> None:
    """The restart's own context becomes the last writer, not silence.

    A falsifying implementation that left the previous origin in place would hide
    from the engine that a restart happened at all.
    """
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    adapter.actuate("light.kitchen", "on", context=ChangeContext.engine())
    adapter.restart(context=ChangeContext.world())
    assert adapter.read_entity("light.kitchen").last_origin is ChangeOrigin.WORLD


def test_restart_is_reproducible() -> None:
    """Two restarts from one configuration yield one startup condition."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())
    adapter.restart(context=ChangeContext.world())
    first = adapter.snapshot()
    adapter.restart(context=ChangeContext.world())
    assert adapter.snapshot() == first


# --------------------------------------------------------------------------
# The adapter's own snapshot
# --------------------------------------------------------------------------


def test_the_snapshot_carries_the_entities_and_only_those() -> None:
    """The port's snapshot holds the registry, sorted by id, and nothing else."""
    adapter = _adapter()
    adapter.add_entity("light.kitchen", "off", context=ChangeContext.world())
    adapter.add_entity(
        "sensor.hall",
        "12",
        attributes={"unit": "c"},
        context=ChangeContext.world(),
    )
    adapter.set_availability(
        "light.kitchen", available=False, context=ChangeContext.world()
    )
    snapshot = adapter.snapshot()
    assert isinstance(snapshot, AdapterSnapshot)
    assert all(isinstance(entry, EntitySnapshot) for entry in snapshot.entities)
    assert [entry.entity_id for entry in snapshot.entities] == [
        "light.kitchen",
        "sensor.hall",
    ]
    by_id = {entry.entity_id: entry for entry in snapshot.entities}
    assert set(by_id) == set(adapter.list_entities())
    assert by_id["light.kitchen"].available is False
    assert by_id["sensor.hall"].state == "12"
    assert dict(by_id["sensor.hall"].attributes) == {"unit": "c"}
    assert isinstance(adapter.read_entity("sensor.hall"), EntityView)
