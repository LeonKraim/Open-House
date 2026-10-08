"""A device that leaves the house must not take the engine down with it.

A binding keeps the *name* of the device a person bound. Remove that device from
Home Assistant -- which the product explicitly supports, and which the mock house
does every time the fleet is restarted -- and the name stays while the device is
gone. The engine's read path used to raise `UnknownEntityError` for exactly that
name, and it raised on the two paths that must never raise: the tick, which reads
every room's `motion_sensor` on every iteration, and `Engine.repairs`, which the
Rooms tab reaches through `views.health_issues`. One deleted motion sensor duly
stopped the house deciding anything and emptied the Rooms tab.

The asymmetry that makes this a defect rather than a design is that the *slot*
layer already has the concept: `ha_adapter.live_modules` answers `MISSING` for a
name the house does not hold, documented as "a device that was never there" and
distinct from `unavailable`. The engine's read path had no such notion.

Each test says in its docstring what a falsifying implementation would look like.
The last one is the one with teeth: `Reduction.ALL` over "nothing is there" must
not read as "everything is fine", so a fix that dropped the absent member from
the read -- leaving an empty tuple that `all` vacuously satisfies -- is failed
here on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.adapter import ChangeContext, EntityView, domain_of
from engine.binding import (
    House,
    Reduction,
    RoomScope,
    SlotRead,
    resolve_slot,
)
from engine.decision_log import Outcome
from engine.engine import Engine
from engine.modes import ModeSet
from engine.solar import Location
from engine.vocabulary import Vocabulary
from sim.adapter import FakeHouseAdapter
from sim.clock import VirtualClock
from sim.entropy import RandomStream

ROOT = Path(__file__).resolve().parents[1]

#: A winter night in London: the sun is below the horizon, so motion lighting's
#: dark test answers "dark" and the only thing that stops it lighting the room is
#: the motion reading itself.
NIGHT = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

MODES: tuple[Mapping[str, object], ...] = (
    {
        "name": "home",
        "description": "Somebody is in.",
        "exclusive_group": "house_presence",
    },
    {
        "name": "away",
        "description": "Nobody is in.",
        "exclusive_group": "house_presence",
    },
)

#: The one room every harness starts from.
KITCHEN: Mapping[str, str] = {
    "motion_sensor": "binary_sensor.kitchen_motion",
    "light_group": "light.kitchen",
}

#: The state a fresh entity of each bound domain is added in.
_INITIAL: Mapping[str, str] = {"binary_sensor": "off", "light": "off"}

#: The two units that read the kitchen's motion sensor, on so they are evaluated
#: rather than gated before they reach the read this defect is about.
_ALL_ENABLED = (
    "behaviour.motion_lighting.enabled",
    "behaviour.override.enabled",
)

#: The name every test removes from the house. Spelled once so a renamed fixture
#: fails at one line rather than scattering.
GONE = "binary_sensor.kitchen_motion"


def _document(layout: Mapping[str, Mapping[str, str]]) -> dict[str, object]:
    """A house document in the frozen schema's shape."""
    return {
        "name": "the test house",
        "rooms": [
            {
                "id": room_id,
                "name": room_id.replace("_", " ").title(),
                "type": "kitchen",
                "bindings": {
                    slot: {"entity_id": entity_id}
                    for slot, entity_id in bindings.items()
                },
            }
            for room_id, bindings in layout.items()
        ],
        "house_scope": {"slots": ["light_group"]},
    }


def _build(
    vocabulary: Vocabulary,
    *,
    entities: Mapping[str, str] | None = None,
) -> tuple[Engine, FakeHouseAdapter, VirtualClock]:
    """A one-room house, a fake and an engine over them, the readers enabled."""
    clock = VirtualClock.started_at(NIGHT)
    adapter = FakeHouseAdapter(clock=clock, random_stream=RandomStream.from_seed(1))
    initial = {
        entity_id: _INITIAL[domain_of(entity_id)] for entity_id in KITCHEN.values()
    }
    initial.update(entities or {})
    for entity_id, value in initial.items():
        adapter.add_entity(entity_id, value, context=ChangeContext.world())

    house = House.from_document(
        _document({"kitchen": dict(KITCHEN)}), vocabulary=vocabulary
    )
    engine = Engine(
        adapter=adapter,
        house=house,
        clock=clock,
        location=LOCATION,
        modes=ModeSet(MODES, vocabulary=vocabulary),
        house_settings=dict.fromkeys(_ALL_ENABLED, True),
    )
    return engine, adapter, clock


def _remove(adapter: FakeHouseAdapter) -> None:
    """Take the bound motion sensor out of the house, as deleting a device does."""
    adapter.remove_entity(GONE, context=ChangeContext.world())


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


def test_a_slot_read_of_a_removed_member_does_not_raise(
    vocabulary: Vocabulary,
) -> None:
    """A read of a slot whose member is gone returns a view, not a traceback.

    Falsified by the pre-fix `SlotRead.views`, which called `read_entity` for each
    member and let `UnknownEntityError` out -- the exception the startup traceback
    shows escaping through `Engine.repairs` and the tick.
    """
    engine, adapter, _ = _build(vocabulary)
    _remove(adapter)

    read = resolve_slot(engine.house, RoomScope("kitchen"), "motion_sensor").read(
        Reduction.ANY
    )
    views = read.views(adapter)

    assert len(views) == 1
    assert views[0].entity_id == GONE
    # Present enough to be named, unreadable enough to be acted on: the reading is
    # "unknown", never a fabricated "off".
    assert views[0].available is False
    assert views[0].state != "off"


def test_repairs_reports_a_motion_sensor_that_is_bound_and_gone(
    vocabulary: Vocabulary,
) -> None:
    """The state Repairs exists to shout about, reached without raising.

    `reviews` reads the same slot the tick does, so before the fix this raised
    before it could report anything; a fix that merely swallowed the exception
    would pass the no-raise test above and fail here, because a sensor that is
    gone is at least as worth a repair as one that is merely unavailable.
    """
    engine, adapter, _ = _build(vocabulary)
    _remove(adapter)

    reported = engine.repairs()

    assert [(repair.room_id, repair.slot, repair.entity_id) for repair in reported] == [
        ("kitchen", "motion_sensor", GONE)
    ]


def test_a_behaviour_gating_on_a_removed_sensor_reads_the_room_as_neither(
    vocabulary: Vocabulary,
) -> None:
    """The tick survives, and the deleted sensor decides nothing either way.

    This is the claim the defect is really about: one deleted motion sensor used
    to stop the house deciding anything. A falsifying implementation that treated
    the silence as a *clear* reading would let motion lighting shut the room down
    (empty); one that treated it as *motion* would light it (occupied). The
    behaviour holds the room as it is and says so, which `_readable` records as a
    decline naming the rule it was evaluating.
    """
    engine, adapter, clock = _build(vocabulary, entities={"light.kitchen": "on"})
    _remove(adapter)

    # Long past the quiet timeout: a room nothing can see must not age into "empty".
    clock.advance(timedelta(minutes=30))
    records = engine.tick()

    assert "kitchen" not in engine.empty_rooms(clock.now)
    held = [record for record in records if record.actor == "motion_lighting"]
    assert len(held) == 1
    assert held[0].outcome is Outcome.DECLINED
    assert held[0].state_delta == ()
    # The light nobody asked about is left exactly as it was.
    assert adapter.read_entity("light.kitchen").state == "on"


def test_holds_over_a_member_that_is_gone_is_false_under_both_reductions(
    vocabulary: Vocabulary,
) -> None:
    """A reduction is never satisfied by a member that is not there.

    The predicate here is `lambda _view: True`, deliberately: a fix that dropped
    the absent member from `views` would leave `ALL` folded over an empty tuple
    and answer `True` -- "everything is fine" -- for a slot whose only member is
    gone. `ANY` is `False` for its own reason, and both are asserted so the two
    reductions cannot be made to agree by accident.
    """
    engine, adapter, _ = _build(vocabulary)
    _remove(adapter)

    binding = resolve_slot(engine.house, RoomScope("kitchen"), "motion_sensor")
    assert binding.read(Reduction.ANY).holds(adapter, lambda _view: True) is False
    assert binding.read(Reduction.ALL).holds(adapter, lambda _view: True) is False


def test_an_available_member_still_satisfies_a_reduction_beside_a_gone_one(
    vocabulary: Vocabulary,
) -> None:
    """The control: a gone member does not poison the members still here.

    A room binding two motion sensors, one of them deleted, still reads motion
    from the other -- `ANY` is `True` -- while `ALL` is `False`, because a member
    that cannot be read may not be claimed to have satisfied the fold.
    """
    _engine, adapter, _ = _build(vocabulary)
    _remove(adapter)
    adapter.add_entity(
        "binary_sensor.kitchen_motion_2", "on", context=ChangeContext.world()
    )

    def moving(view: EntityView) -> bool:
        return view.available and view.state == "on"

    alone = SlotRead("motion_sensor", (GONE,), Reduction.ANY)
    both_any = SlotRead("motion_sensor", (GONE, GONE + "_2"), Reduction.ANY)
    both_all = SlotRead("motion_sensor", (GONE, GONE + "_2"), Reduction.ALL)

    assert alone.holds(adapter, moving) is False
    assert both_any.holds(adapter, moving) is True
    assert both_all.holds(adapter, moving) is False


def test_the_engine_still_lists_the_entities_it_holds(vocabulary: Vocabulary) -> None:
    """A removed entity is absent from `list_entities`, which is not the same
    question as a slot read and must not be papered over: the house no longer
    holds it, and `hazards` scans exactly the listed set."""
    engine, adapter, _ = _build(vocabulary)
    _remove(adapter)

    assert GONE not in engine.house_adapter.list_entities()
    assert "light.kitchen" in engine.house_adapter.list_entities()
    assert engine.hazards() == ()
