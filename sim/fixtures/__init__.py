"""The four fixture houses, built through the adapter's own operations.

`specs/simulation/spec.md` asks for an entry point named `build_fixture` and
exactly four houses, each *constructed* rather than loaded. The distinction is
the whole design: a fixture committed as a document could hold a state the
adapter cannot reach -- a binding to an entity nothing ever added -- and a
scenario passing against it would pass for a reason the running system could
never reproduce (`design.md` D11). So the fixtures are built here by calling
`add_entity` and `set_availability` on the fake, and the four plans in
`sim/fixtures/_plans.py` are read for their contents rather than their calls.

A fixture is a house, its substrate, and where it is -- not a running engine.
The simulation it carries is the adapter, the virtual clock and the single seeded
stream, started at a fixed instant and seeded from the fixture's own seed, so a
scenario's `given` block names a house and gets the same house twice. What the
engine does with it is the runner's business, and the runner is not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from engine.adapter import ChangeContext, domain_of
from engine.binding import House
from sim.snapshot import Simulation

from ._plans import INITIAL, PLANS, FixtureConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

    from engine.solar import Location
    from engine.vocabulary import Vocabulary
    from sim.adapter import FakeHouseAdapter
    from sim.clock import VirtualClock

__all__ = [
    "DEFAULT_SEED",
    "DEFAULT_STARTED_AT",
    "FIXTURE_NAMES",
    "Fixture",
    "FixtureConfig",
    "FixtureError",
    "FixtureName",
    "UnknownFixtureError",
    "build_fixture",
    "check_fixture",
    "materialise",
]

#: The seed every fixture is built with when a scenario names none. One, not
#: zero, so a stream's position after a build is a count that was taken rather
#: than a value that happens to be zero either way.
DEFAULT_SEED = 1

#: Midday UTC on the first of January. A fixture's clock starts here unless the
#: caller fixes it elsewhere: the instant is fixture data like the location is,
#: and a run that must replay supplies its own start rather than inheriting the
#: moment the fixture was written.
DEFAULT_STARTED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FixtureName(StrEnum):
    """The four houses, by the word a scenario's `given` block writes.

    A `StrEnum` rather than four constants because the name is what a YAML block
    carries and what an agent's session is called, and both arrive as strings:
    membership is then a `FixtureName(...)` and an unknown name fails by naming
    itself rather than by comparing unequal to four constants in turn.
    """

    MINIMAL = "minimal"
    MESSY = "messy"
    LARGE = "large"
    NO_LUX = "no_lux"


#: Every fixture name, in the order the spec lists them. The suite asserts this
#: against `sim.fixtures._plans.PLANS`' keys, so a plan without a name or a name
#: without a plan is a failing check rather than a house nothing can select.
FIXTURE_NAMES: tuple[FixtureName, ...] = (
    FixtureName.MINIMAL,
    FixtureName.MESSY,
    FixtureName.LARGE,
    FixtureName.NO_LUX,
)


class UnknownFixtureError(Exception):
    """A name no fixture answers to, reported with the ones that exist."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.known: tuple[str, ...] = tuple(str(item) for item in FIXTURE_NAMES)
        super().__init__(
            f"no fixture is named {name!r}; the fixtures are "
            f"{', '.join(repr(item) for item in self.known)}"
        )


class FixtureError(Exception):
    """A fixture state the adapter's own operations could not have produced.

    It carries the state and the operation apart as well as in the message,
    because a caller that wants to branch on the failure needs the operation
    named without parsing prose, and because the message's job is to say which
    call would have had to happen for the fixture to be honest.
    """

    def __init__(self, state: str, operation: str) -> None:
        self.state = state
        self.operation = operation
        super().__init__(
            f"the fixture holds {state}, which the fake's own operations could "
            f"not have produced: only {operation} would have"
        )


@dataclass(frozen=True, slots=True)
class Fixture:
    """One built house: its document, its substrate, and where it is.

    `document` is the house as the frozen schema describes it and is what a
    scenario's `given` block would carry inline; `house` is that document after
    `House.from_document`, so a fixture that named a slot outside
    `catalog/slots.yaml` fails at the build rather than at the first resolution.
    `simulation` holds the adapter the devices were added to, the clock they were
    added at, and the stream whose draw chose the messy house's unavailable
    device.

    `house_settings` is empty, and that is the OFF-by-default product rule at
    the fixture's boundary: a built house declares no enable flag, so every
    behaviour and every module starts from its builtin default, which is off. A
    fixture that carried `behaviour.motion_lighting.enabled: true` would make "a
    freshly built house runs nothing" false for that fixture alone, and no
    scenario could tell.
    """

    name: FixtureName
    seed: int
    config: FixtureConfig
    document: Mapping[str, object]
    house: House
    simulation: Simulation
    house_settings: Mapping[str, object]

    @property
    def adapter(self) -> FakeHouseAdapter:
        """The fake the fixture's devices live in."""
        return self.simulation.adapter

    @property
    def clock(self) -> VirtualClock:
        """The virtual clock the fixture was built at."""
        return self.simulation.clock

    @property
    def location(self) -> Location:
        """Where the fixture is, as `engine/solar.py` reads it."""
        return self.config.location

    def entities(self) -> tuple[str, ...]:
        """Every entity the fixture holds, sorted as the port enumerates them."""
        return tuple(self.simulation.adapter.list_entities())


def build_fixture(
    name: str | FixtureName,
    *,
    vocabulary: Vocabulary,
    seed: int = DEFAULT_SEED,
    started_at: datetime = DEFAULT_STARTED_AT,
) -> Fixture:
    """Build the fixture `name` through the adapter and return it.

    The vocabulary is required rather than loaded here, because loading it means
    choosing a repository root and `sim/` has no opinion about which tree it is
    running against -- a fixture that read the catalog from its own file position
    would pass against a tree the engine was not built from. The seed and the
    start instant default to the fixture's own constants so that
    `build_fixture("messy", vocabulary=...)` is a complete request, and both are
    parameters so a scenario can pin them explicitly.
    """
    try:
        fixture_name = FixtureName(name)
    except ValueError:
        raise UnknownFixtureError(str(name)) from None
    plan = PLANS[fixture_name]

    simulation = Simulation.start(seed=seed, started_at=started_at)
    document = plan.build(simulation.adapter, simulation.stream, str(fixture_name))
    fixture = Fixture(
        name=fixture_name,
        seed=seed,
        config=plan.config,
        document=document,
        house=House.from_document(document, vocabulary=vocabulary),
        simulation=simulation,
        house_settings={},
    )
    check_fixture(fixture)
    return fixture


def check_fixture(fixture: Fixture) -> None:
    """Fail if the fixture holds a state the fake could not have produced.

    One claim, and it is the one that matters: **every device the house binds is
    a device the adapter holds.** A binding to an entity nothing added is the
    state a fixture loaded from a baked document produces most easily and the one
    a scenario cannot survive, because the first `read` through the port fails
    naming an id the house claims to have -- and the operation that would have
    had to produce it is `add_entity`, called with that id.

    The spec's other two arms are refusals inside the fake rather than checks
    here, which is why they are not repeated: `add_entity` refuses an id the
    house already holds (`test_adding_a_duplicate_entity_is_rejected`) and
    `set_availability` refuses an entity it does not hold, so neither a duplicate
    nor an availability for an unknown device can reach a built fixture at all.
    A check here could only restate a construction that has already failed.
    """
    held = frozenset(fixture.simulation.adapter.list_entities())
    for room in fixture.house.rooms:
        for slot, entity_id in room.bindings.items():
            if entity_id not in held:
                raise FixtureError(
                    f"a binding of {slot!r} in room {room.id!r} to {entity_id!r}, "
                    "an entity the adapter does not hold",
                    "add_entity",
                )


def materialise(house: House, adapter: FakeHouseAdapter) -> tuple[str, ...]:
    """Add every device `house` binds to `adapter`, and return the ids added.

    This is `design.md` D11 stated for *any* house rather than only for the four
    fixtures, because the four are not the only houses a session is opened
    against: a scenario's `given` block may carry an inline house and the
    control surface's `import_config` produces one, so both reach the fake the
    same way a fixture does -- through `add_entity`, at the state a device of
    that domain rests in -- and neither is ever loaded as a baked snapshot.

    The house is taken as a `House` rather than as a document because
    `House.from_document` has already validated the shape and resolved every
    slot name against `catalog/slots.yaml` by the time a caller has one, so
    there is nothing left here to check. A device named by two rooms is wanted
    once: the fake holds one entity per id, and two rooms reading one device is
    a shape the frozen house schema permits. A device the adapter already holds
    is left alone rather than added again, which is what makes applying a house
    to a live adapter -- `import_config` against a session that is already
    running -- an addition rather than a refusal; what is returned is what this
    call added, not the house's whole device list.

    `Plan.build` makes the same additions for the four plans and additionally
    marks the messy plan's unavailable device, so it is not routed through here;
    the two are held together by a check that materialising a plan's own
    document adds exactly what building the plan adds, which is what stops a
    second answer to "where does a device of this domain start".
    """
    wanted = sorted(
        {entity_id for room in house.rooms for entity_id in room.bindings.values()}
    )
    held = frozenset(adapter.list_entities())
    added = [entity_id for entity_id in wanted if entity_id not in held]
    for entity_id in added:
        adapter.add_entity(
            entity_id,
            INITIAL[domain_of(entity_id)],
            context=ChangeContext.world(),
        )
    return tuple(added)
