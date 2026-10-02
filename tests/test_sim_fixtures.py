"""The four fixture houses -- task 8.0.

`specs/simulation/spec.md` asks for two things that pull in opposite directions,
and the tension is what these tests are about. A fixture must be *reachable* --
every device in it arrived through a port operation, so a scenario written
against it describes something the running system could also be in -- and it
must be *reproducible* -- the same name and seed give the same house twice, so a
scenario's failure is a failure a second run can be shown. A fixture loaded from
a baked document is perfectly reproducible and can be unreachable; one built from
unseeded randomness is perfectly reachable and can be unreproducible.

Three tests carry the reachability half.
`test_every_device_the_document_binds_is_held_by_the_adapter` asserts agreement
in both directions at once, which is the state a baked document fails -- a
binding with no entity, or an entity no binding claims.
`test_the_fixture_check_names_the_state_and_the_operation` runs the check
against a deliberately corrupted house and asserts it fails naming both, because
a check never seen to fail is a check that may never run; and
`test_build_fixture_runs_that_check` asserts the build path actually calls it,
since a check that exists and is never invoked enforces nothing on the path a
scenario takes.

A fourth reads `catalog/room_types.yaml` and asserts the invariant that makes a
fixture a *house* rather than a bag of devices: every slot a room binds is one
its type provides, or a slot no type provides that the house declares at house
scope. The plans copy the catalog's `provides_slots` rather
than reading it, so the copy is checked rather than trusted.

The reproducibility half is a property rather than an example: the same seed is
built twice and the two snapshots are compared as documents. The messy house is
the fixture that makes this non-trivial, since its unavailable device is drawn
from the run's stream; `test_the_messy_house_varies_with_its_seed` asserts the
draw is real, so the equality test above is not passing because the seed is
inert.

Each test says, in its docstring, what a falsifying implementation would look
like.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

import sim.fixtures
from engine.adapter import ChangeContext
from engine.behaviours import behaviour_defaults, default_behaviours, enable_key
from engine.binding import House, HouseScope, Reduction, resolve_slot
from engine.decision_log import Outcome
from engine.engine import Engine
from engine.modes import ModeSet
from engine.solar import sun_elevation
from engine.vocabulary import Vocabulary
from sim.fixtures import (
    DEFAULT_STARTED_AT,
    FIXTURE_NAMES,
    Fixture,
    FixtureConfig,
    FixtureError,
    FixtureName,
    UnknownFixtureError,
    build_fixture,
    check_fixture,
)
from sim.fixtures._plans import PLANS, PROVIDES, Plan, RoomPlan
from tools.netguard import no_sockets

ROOT = Path(__file__).resolve().parents[1]

#: Two modes, exclusive, so a mode set exists. No fixture carries modes -- they
#: are engine state a scenario's `given` block supplies -- but an `Engine` needs
#: one to be constructed, and the away-shutdown unit reads the house's presence
#: from it even when the unit is disabled and never gets as far as the reading.
MODES = (
    {"name": "home", "description": "Somebody is in.", "exclusive_group": "presence"},
    {"name": "away", "description": "Nobody is in.", "exclusive_group": "presence"},
)

#: The light of each fixture: the entity a motion event could plausibly switch,
#: and therefore the entity the disablement test watches. Named per fixture
#: rather than discovered, because "no light changed" is only meaningful against
#: a light the enabled behaviour would have changed.
_LIGHTS: dict[FixtureName, str] = {
    FixtureName.MINIMAL: "light.living_room",
    FixtureName.MESSY: "light.kitchen",
    FixtureName.LARGE: "light.basement_01_light",
    FixtureName.NO_LUX: "light.living_room",
}

#: The motion sensor paired with each light above, for the same reason.
_MOTIONS: dict[FixtureName, str] = {
    FixtureName.MINIMAL: "binary_sensor.living_room_motion",
    FixtureName.MESSY: "binary_sensor.kitchen_motion",
    FixtureName.LARGE: "binary_sensor.basement_01_motion",
    FixtureName.NO_LUX: "binary_sensor.living_room_motion",
}


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def fixtures(vocabulary: Vocabulary) -> dict[FixtureName, Fixture]:
    """All four, built once, since a build is a whole house and four is enough."""
    return {name: build_fixture(name, vocabulary=vocabulary) for name in FIXTURE_NAMES}


def _bound(fixture: Fixture) -> set[str]:
    """Every entity id the fixture's document claims, across both scopes."""
    return {
        entity_id
        for room in fixture.house.rooms
        for entity_id in room.bindings.values()
    }


def _unavailable(fixture: Fixture) -> list[str]:
    """Every entity the fixture reports unavailable, in the port's own order."""
    return [
        entity_id
        for entity_id in fixture.entities()
        if not fixture.adapter.read_entity(entity_id).available
    ]


# --------------------------------------------------------------------------
# Exactly four, each selectable by name
# --------------------------------------------------------------------------


def test_there_are_exactly_four_fixtures_and_each_has_a_name() -> None:
    """The enum, the name list and the plan table describe the same four houses.

    The enum is the source of truth and both other collections are measured
    against it, because the three can disagree in ways a pair-wise comparison
    misses: a `FixtureName` member with no `FIXTURE_NAMES` entry slips past a
    list-to-plans comparison and fails only when someone selects it.

    Falsified by a fifth plan added without a `FixtureName` member -- a house no
    scenario's `given` block could ever select, silently present and unreachable
    -- by a name with no plan behind it, which fails at the first build with a
    `KeyError` rather than a message, and by a member added to the enum alone,
    which passes every check but this one.
    """
    assert len(FixtureName) == 4
    assert set(FIXTURE_NAMES) == set(FixtureName)
    assert set(PLANS) == {str(name) for name in FixtureName}
    assert [str(name) for name in FIXTURE_NAMES] == [
        "minimal",
        "messy",
        "large",
        "no_lux",
    ]


def test_a_fixture_is_selected_by_the_word_a_scenario_writes(
    vocabulary: Vocabulary,
) -> None:
    """The string and the enum member name the same house.

    Falsified by a `build_fixture` that took only the enum: a scenario's `given`
    block carries a string, and the runner would have to translate before every
    lookup, which is the coupling the string-valued name exists to avoid.
    """
    by_string = build_fixture("no_lux", vocabulary=vocabulary)
    by_member = build_fixture(FixtureName.NO_LUX, vocabulary=vocabulary)
    assert by_string.name is FixtureName.NO_LUX
    assert by_string.document == by_member.document
    assert by_string.simulation.snapshot().to_json() == (
        by_member.simulation.snapshot().to_json()
    )


def test_an_unknown_fixture_name_fails_naming_itself_and_the_known_ones(
    vocabulary: Vocabulary,
) -> None:
    """A typo is reported as a typo, with the alternatives.

    Falsified by a bare `KeyError`, which names the missing key but not the set
    of keys that exist -- and the set is what a user with a misspelled scenario
    needs, since the fixtures are the vocabulary of every `given` block.
    """
    with pytest.raises(UnknownFixtureError) as caught:
        build_fixture("minmal", vocabulary=vocabulary)

    assert caught.value.name == "minmal"
    assert caught.value.known == ("minimal", "messy", "large", "no_lux")
    for known in caught.value.known:
        assert repr(known) in str(caught.value)


def test_the_document_is_named_for_the_selector(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """The house's own name carries the word the scenario selected it by.

    Falsified by a document named from the plan's contents rather than the
    selector: a scenario failing on the third fixture would log a house whose
    name appears in no `given` block, and the reader could not find it.
    """
    for name, fixture in fixtures.items():
        assert fixture.document["name"] == f"the {name} fixture"


# --------------------------------------------------------------------------
# Reachable: every device arrived through the port
# --------------------------------------------------------------------------


def test_every_device_the_document_binds_is_held_by_the_adapter(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """The house and the fake agree, in both directions, for all four fixtures.

    The forward direction is what `check_fixture` enforces at the build: a
    binding to an entity nothing added is the failure a baked document makes
    easy, and it surfaces at the first `read` as an unknown entity rather than
    as a fixture that was never really built. The reverse direction is the one
    a check cannot see, because a stray entity is *legal* -- `add_entity` has no
    binding requirement -- so only the fixture's own construction is wrong: a
    device the document does not mention is a device no scenario can address,
    which makes it dead weight that changes every house-scoped count.

    Falsified by a fixture that wrote its document first and added entities
    after, in either direction: a binding with no entity, or an entity with no
    binding.
    """
    for name, fixture in fixtures.items():
        assert _bound(fixture) == set(fixture.entities()), f"{name} disagrees"


def test_the_shipped_fixtures_pass_the_check(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """The four houses as shipped hold nothing the fake could not have produced.

    The check's own behaviour is `test_the_fixture_check_names_the_state_and_the_operation`
    below; this is the other direction, and a check that rejected the shipped
    fixtures would be worse than none, because it would fail every build.

    Falsified by a change to `Plan.build` that added entities from somewhere
    other than `Plan.entities` -- the reachable version of "the house binds
    something nothing added", since a plan's bindings and its `add_entity` calls
    are derived from one table and an edit to that table moves both.
    """
    for name, fixture in fixtures.items():
        assert check_fixture(fixture) is None, f"{name} does not pass its own check"


def test_build_fixture_runs_that_check(
    vocabulary: Vocabulary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_fixture` calls `check_fixture` on the fixture it built.

    A separate test from the two around it, because they test the check and this
    one tests the *call*: `check_fixture` can be correct, can reject a corrupted
    house, and can be invoked by nobody. The spec's "a fixture is inspected and
    contains a binding the fake could not have produced" is a claim about the
    build path, so the call is what has to be asserted, and a spy is the only way
    to see it -- the shipped plans cannot produce a corrupted house, which is the
    point of building them through the adapter.

    Falsified by deleting the `check_fixture(fixture)` call in `build_fixture`:
    every other test in this module still passes, because they call the check
    themselves, and the spy below records nothing.
    """
    seen: list[Fixture] = []
    real = sim.fixtures.check_fixture

    def spy(fixture: Fixture) -> None:
        seen.append(fixture)
        real(fixture)

    monkeypatch.setattr(sim.fixtures, "check_fixture", spy)
    built = build_fixture("minimal", vocabulary=vocabulary)

    assert [fixture.name for fixture in seen] == [FixtureName.MINIMAL]
    assert seen[0] is built


def test_the_fixture_check_names_the_state_and_the_operation(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """A binding to an entity nothing added fails, naming both halves.

    The corrupted house is built the honest way -- a document with an extra
    binding, through `House.from_document`, which admits it because
    `house/1.0.0.json` constrains a binding's *shape* and only the adapter can
    say whether the entity exists. The fixture then holds a state the fake's
    operations could not have produced, which is exactly the state the check
    exists for.

    Falsified by a check that raised a bare `AssertionError` or a message naming
    only the entity: the spec asks for the state *and* the operation that would
    have had to produce it, because the operation is the fix.
    """
    good = fixtures[FixtureName.MINIMAL]
    document: dict[str, object] = {
        "name": good.document["name"],
        "rooms": [
            *(
                {
                    "id": room.id,
                    "name": room.name,
                    "type": room.type,
                    "bindings": {
                        slot: {"entity_id": entity_id}
                        for slot, entity_id in room.bindings.items()
                    },
                }
                for room in good.house.rooms
            ),
            {
                "id": "cellar",
                "name": "Cellar",
                "type": "basement",
                "bindings": {"light_group": {"entity_id": "light.cellar"}},
            },
        ],
        "house_scope": {"slots": list(good.house.house_scope_slots)},
    }
    corrupted = replace(
        good,
        document=document,
        house=House.from_document(document, vocabulary=good.house.vocabulary),
    )

    with pytest.raises(FixtureError) as caught:
        check_fixture(corrupted)

    assert "light.cellar" in caught.value.state
    assert "cellar" in caught.value.state
    assert caught.value.operation == "add_entity"
    assert "light.cellar" in str(caught.value)
    assert "add_entity" in str(caught.value)


# --------------------------------------------------------------------------
# A house, not a bag of devices
# --------------------------------------------------------------------------


def _catalog_types() -> list[dict[str, object]]:
    """`catalog/room_types.yaml`'s rows, read the way the catalog writes them."""
    document = yaml.safe_load(
        (ROOT / "catalog" / "room_types.yaml").read_text(encoding="utf-8")
    )
    return document["room_types"]


def test_the_plans_copy_of_the_catalog_is_the_catalog() -> None:
    """The slot table the large fixture is generated from matches the artifact.

    The copy exists because `sim/` reading the repository at import would make a
    fixture depend on the tree it was imported from; the price of copying is
    drift, and this is what makes drift a failed check. Both halves are asserted:
    the same 20 types, and the same slots under each. A type whose slots were
    changed in the catalog and not here would build a house the vocabulary calls
    malformed, and every scenario written against it would be rewritten.

    Falsified by a slot added to `provides_slots` in `room_types.yaml` without a
    matching entry in `PROVIDES`, and by a type renamed in one and not the
    other. It fails in the direction that matters: the copy is checked against
    the artifact, never the artifact against the copy.
    """
    rows = _catalog_types()
    assert set(PROVIDES) == {row["name"] for row in rows}
    for row in rows:
        assert set(PROVIDES[str(row["name"])]) == set(row["provides_slots"]), (  # type: ignore[arg-type]
            f"{row['name']} disagrees with the catalog"
        )


def test_every_room_type_a_fixture_uses_is_one_the_catalog_defines(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """No fixture invents a room type, and between them they use every one.

    A room's `type` is shape-checked by `house/1.0.0.json` and nothing else, so a
    typo -- `livingroom`, `bedrooom` -- validates, builds, and produces a house
    whose kind the catalog does not know. The reverse direction is what makes the
    fixtures coverage rather than four anecdotes: 60 rooms cycling 20 types means
    every kind has a house behind it.

    Falsified by a misspelled type in any plan, which the schema admits, and by a
    large fixture whose cycle skipped a type.
    """
    defined = {row["name"] for row in _catalog_types()}
    used = {room.type for fixture in fixtures.values() for room in fixture.house.rooms}
    assert used - defined == set(), "a fixture names a type the catalog does not"
    assert used == defined, "a catalog type no fixture builds"


def test_a_room_binds_only_slots_its_type_provides_or_the_house_declares(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """Every binding is a slot the type provides, or a slot only the house holds.

    The invariant that makes a fixture a house the catalog would recognise, and
    the escape clause is what makes it load-bearing rather than decorative.

    A room may bind a slot its type provides. It may also bind a slot that **no
    room type provides at all** and that the house declares at house scope: a
    house-scope slot is aggregated from the rooms, so a house that bound it at no
    room could not resolve it, and a type that does not offer it cannot be what
    supplies it. No shipped fixture needs the escape -- the home's state is the
    engine's own and is no slot, so the only house slot every fixture declares,
    `light_group`, is also provided by most types -- and the clause is kept
    because it states the rule rather than the fixtures' current use of it.

    What the escape must *not* be is "any house-scope slot", and this is worth
    being explicit about because the wider reading is the natural one to write
    and it is nearly vacuous: `light_group` is declared at house scope by every
    fixture, and it is also provided by most types -- so under the wide reading a
    driveway could carry a light, which is the example this invariant exists to
    forbid. The two clauses are therefore about two different facts: what a type
    offers, and what the house holds that no type can offer.

    Nothing shipped rejects a light in a driveway today. `tools/catalog/scope.py`
    checks a *behaviour row* against the catalog's supply, not a house's room
    bindings, so the fixtures are the only place the rule is held -- which is
    why it is held here rather than left to a later task.

    Falsified by a binding added to a room for a slot its type does not offer and
    no type offers -- a lock in a gazebo -- and by a binding of a house-scope
    slot the room's type does not provide but some type does, which is the light
    in the driveway.
    """
    offered_somewhere = {slot for slots in PROVIDES.values() for slot in slots}
    for name, fixture in fixtures.items():
        house_only = {
            slot
            for slot in fixture.house.house_scope_slots
            if slot not in offered_somewhere
        }
        for room in fixture.house.rooms:
            allowed = set(PROVIDES[room.type]) | house_only
            assert set(room.bindings) - allowed == set(), (
                f"{name}: {room.id} ({room.type}) binds "
                f"{sorted(set(room.bindings) - allowed)}"
            )


def test_a_plan_that_binds_one_entity_twice_is_refused() -> None:
    """Two rooms claiming one entity id fails at the plan, naming the id.

    The fake holds one entity per id, so the second binding would address the
    first room's device while the document claimed two -- a house where turning
    off the kitchen light also turns off the dining room's, silently. The plan
    refuses it where it is written rather than letting the build add the entity
    once and bind it twice.

    Falsified by a `Plan.entities` that built a plain dict comprehension: the
    second binding would overwrite the first and the collision would be
    invisible.
    """
    plan = Plan(
        config=FixtureConfig(latitude=0.0, longitude=0.0, time_zone="UTC"),
        house_scope_slots=("light_group",),
        rooms=(
            RoomPlan(
                id="kitchen",
                name="Kitchen",
                type="kitchen",
                bindings={"light_group": "light.shared"},
            ),
            RoomPlan(
                id="dining_room",
                name="Dining Room",
                type="dining_room",
                bindings={"light_group": "light.shared"},
            ),
        ),
    )

    with pytest.raises(ValueError, match=r"light\.shared"):
        plan.entities()


# --------------------------------------------------------------------------
# Reproducible from its seed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_a_fixture_is_the_same_house_twice_at_one_seed(
    name: FixtureName, vocabulary: Vocabulary
) -> None:
    """Two builds at one seed are equal as documents and as snapshots.

    Asserted as document equality rather than as a spot-check of a few entities,
    because the property the runner needs is that a failing scenario replays: a
    field that differed between two builds at one seed would make the second run
    of a failure a different run.

    What the comparison covers, stated exactly, because "reproducible" is easy to
    overclaim. The snapshot carries the entities, their attributes and
    availability, the engine's state, the clock, the seed and the stream's
    *position* (`sim/snapshot.py`) -- so a build that drew from the stream a
    different number of times is caught even when the drawn value was discarded.
    Two things it cannot catch, and does not pretend to: a draw whose value is
    discarded changes neither document nor snapshot, so equality holds; and a
    nondeterminism that changed nothing observable is by definition invisible
    here. What it does catch is a draw whose *result* varies, which is the messy
    house's unavailable device -- and `test_the_messy_house_varies_with_its_seed`
    below is what proves that draw is real rather than constant, so this test is
    not passing because the seed is inert.

    Falsified by an unavailable device drawn from the module `random` rather than
    from the run's stream (caught when the two draws differ, which is the usual
    case and not every case), and by any per-build state the snapshot enumerates
    -- an entity count, an availability, a clock -- that varied with anything but
    the seed.
    """
    first = build_fixture(name, vocabulary=vocabulary)
    second = build_fixture(name, vocabulary=vocabulary)
    assert first.document == second.document
    assert first.simulation.snapshot().to_json() == (
        second.simulation.snapshot().to_json()
    )


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_seed_a_fixture_was_built_at_is_the_seed_it_reports(
    name: FixtureName, vocabulary: Vocabulary
) -> None:
    """The fixture carries the seed, and the stream is seeded from it.

    Falsified by a `build_fixture` that seeded the simulation from a constant:
    the seed would be recorded and inert, and a scenario pinning one would be
    pinning nothing.
    """
    fixture = build_fixture(name, vocabulary=vocabulary, seed=99)
    assert fixture.seed == 99
    assert fixture.simulation.stream.seed == 99


def test_the_messy_house_varies_with_its_seed(vocabulary: Vocabulary) -> None:
    """The messy house's unavailable device is a draw, and the draw is real.

    This is what makes the same-seed equality above more than a tautology: if
    the unavailable device were a constant, the fixture would be reproducible
    for a reason that has nothing to do with the seed, and a scenario that
    wanted the other device unavailable could not ask for it.

    Falsified by a plan that listed the unavailable device as a constant, in
    which case every seed would give one value and the set below would have one
    element. The seeds are a fixed range rather than a random sample, so the
    claim is deterministic: it is a fact about the shipped stream, not a
    probability.
    """
    picks = {
        _unavailable(build_fixture("messy", vocabulary=vocabulary, seed=seed))[0]
        for seed in range(16)
    }
    assert picks == {"light.dining_room", "light.kitchen"}


# --------------------------------------------------------------------------
# What each fixture is for
# --------------------------------------------------------------------------


def test_the_large_fixture_is_large_enough(vocabulary: Vocabulary) -> None:
    """At least 150 entities, built with no socket opened.

    The count is what makes "the engine is not quadratic" a claim with something
    behind it; the socket guard is the spec's other half of the same sentence,
    and it is here rather than in the hermeticity scan because a socket could be
    opened by a dependency of the build without any module in `sim/` importing a
    networking package.

    Falsified by a fixture of 60 rooms that bound only a light each -- 60
    entities, a house that would make any traversal look fast -- and by a build
    that resolved a hostname, which `no_sockets` refuses and names.
    """
    with no_sockets():
        large = build_fixture(FixtureName.LARGE, vocabulary=vocabulary)

    assert len(large.entities()) >= 150
    assert len(large.house.rooms) >= 20


def test_the_messy_fixture_strains_binding(vocabulary: Vocabulary) -> None:
    """One slot in two rooms, a device unavailable, and no lux anywhere.

    The three strains the spec names, asserted together because they are one
    house and a fixture that had two of the three would be one the suite claimed
    to cover. `light_group` is house-scoped, so the two rooms' bindings
    aggregate: that aggregation is the only place list-capable binding arises in
    this phase, and it is what a house-scoped behaviour acts on.

    Falsified by a messy house with one room -- the house-scoped slot would
    resolve to one entity and the aggregation path would never run -- and by a
    house whose second light was simply absent, which would leave the same
    footprint as an unbound slot.
    """
    messy = build_fixture(FixtureName.MESSY, vocabulary=vocabulary)
    lights = resolve_slot(messy.house, HouseScope(), "light_group").read(Reduction.ANY)

    assert len(lights.views(messy.adapter)) == 2
    assert len(messy.house.rooms) == 2
    assert len(_unavailable(messy)) == 1
    assert not any(
        "ambient_light_sensor" in room.bindings for room in messy.house.rooms
    )
    assert "ambient_light_sensor" not in messy.house.house_scope_slots


def test_the_no_lux_fixture_binds_no_ambient_light_sensor(
    vocabulary: Vocabulary,
) -> None:
    """Nothing in the house measures light, at either scope.

    This is the sun fallback's own subject: a behaviour that would rather read a
    lux sensor than compute an elevation has no reading available here, and the
    fixture is where that is a property of the house rather than of the run.

    Falsified by any room binding `ambient_light_sensor`, and by the slot appearing at
    house scope -- which the second assertion catches even though
    `house_scope.slots` is drawn from a list that does not contain it today.
    """
    no_lux = build_fixture(FixtureName.NO_LUX, vocabulary=vocabulary)
    assert no_lux.house.rooms
    assert not any(
        "ambient_light_sensor" in room.bindings for room in no_lux.house.rooms
    )
    assert "ambient_light_sensor" not in no_lux.house.house_scope_slots


# --------------------------------------------------------------------------
# Where the house is
# --------------------------------------------------------------------------


def test_the_location_travels_with_the_fixture(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """Every fixture carries a latitude, a longitude and a zone, and two differ.

    The spec records the location as fixture data because the corpus supplies
    none: the engine has no opinion about where a house is, and a scenario that
    needed one would otherwise have to invent it in YAML, which would make "the
    same house at the same time decides the same" true only as long as the YAML
    did not change.

    Falsified by a single shared location constant -- the divergence test below
    would then find nothing to diverge -- and by a fixture that carried no zone,
    which would leave a scenario written in local times unable to say what
    "08:00" means.

    This test does not reach the engine, and so says nothing about where the
    location is *held*; `test_the_location_is_not_engine_state` below is the half
    that does.
    """
    for fixture in fixtures.values():
        assert fixture.location.latitude == fixture.config.latitude
        assert fixture.location.longitude == fixture.config.longitude
        assert fixture.location.time_zone == fixture.config.time_zone

    zones = {fixture.location.time_zone for fixture in fixtures.values()}
    assert len(zones) >= 2


def test_two_fixtures_at_different_locations_diverge_at_one_instant(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """Every pair: equal elevations where the locations are equal, unequal where not.

    All six pairs rather than three, because the pairs left out are the ones a
    reader would most want to know about. `minimal` and `messy` are both London
    at 51.5074, -0.1278, and their elevations at one instant are *equal* -- which
    is the claim that makes the other five meaningful, since a function that
    returned a constant would also make them equal. Asserting both directions
    means the test cannot pass by way of a sun that ignores the location, which
    an all-pairs inequality test could not distinguish from a correct one.

    The comparison is against the pure `sun_elevation` helper rather than through
    a behaviour, because what is under test here is that the fixture's location
    is what reaches the formula; `test_engine_solar.py` owns the formula, and the
    sun-following branch that consumes it is `first-behaviours`'.

    "Same place" means same coordinates and not same zone, deliberately: the zone
    is carried because a scenario written in local times needs it, and
    `engine/solar.py`'s own docstring records that no arithmetic below reads it,
    so two fixtures at one point with different zone names *should* agree. A
    test that demanded they differ would be asserting a bug.

    Falsified by a plan whose latitude or longitude was never wired to its
    config, which would make five pairs equal, and by two fixtures whose
    coordinates were copied from each other, which would turn an inequality into
    an equality.
    """
    at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    elevations = {
        name: sun_elevation(at=at, location=fixture.location)
        for name, fixture in fixtures.items()
    }
    for left, right in itertools.combinations(FIXTURE_NAMES, 2):
        same_place = (
            fixtures[left].config.latitude == fixtures[right].config.latitude
            and fixtures[left].config.longitude == fixtures[right].config.longitude
        )
        if same_place:
            assert elevations[left] == elevations[right], f"{left} and {right} differ"
        else:
            assert elevations[left] != elevations[right], f"{left} equals {right}"

    assert elevations[FixtureName.MINIMAL] == elevations[FixtureName.MESSY]


def test_the_location_is_not_engine_state(fixtures: dict[FixtureName, Fixture]) -> None:
    """A snapshot carries no coordinate and no zone.

    The spec's separation, asserted on the document a restore reads: making the
    location engine state would put a constant the user never chose into the
    same snapshot as the enable flags and the bindings, where it would be
    restorable, overridable and auditable as though a decision depended on it.

    Falsified by a fixture that seeded its location into `engine_state`, and by
    a snapshot that serialised the whole fixture rather than the enumerated
    fields.
    """
    for name, fixture in fixtures.items():
        text = fixture.simulation.snapshot().to_json()
        for value in (
            f"{fixture.config.latitude}",
            f"{fixture.config.longitude}",
            fixture.config.time_zone,
        ):
            assert value not in text, f"{name} leaks {value} into its snapshot"
        assert "latitude" not in text
        assert "longitude" not in text


# --------------------------------------------------------------------------
# A fresh fixture runs nothing
# --------------------------------------------------------------------------


def _engine_over(fixture: Fixture, vocabulary: Vocabulary) -> Engine:
    """An engine on the fixture's own substrate, with the fixture's own settings."""
    return Engine(
        adapter=fixture.adapter,
        house=fixture.house,
        clock=fixture.clock,
        location=fixture.location,
        modes=ModeSet(MODES, vocabulary=vocabulary),
        house_settings=fixture.house_settings,
    )


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_a_freshly_built_fixture_runs_nothing(
    name: FixtureName, vocabulary: Vocabulary
) -> None:
    """A motion event on a built house moves no light, and every record says why.

    The product rule at the fixture's boundary: OFF by default means a house
    nobody has configured does nothing, and "nothing happened" is asserted
    through the decision log rather than through the light alone -- a light that
    did not move because no behaviour was *offered* the event is a different
    fact from one that did not move because every behaviour declined, and only
    the record tells them apart.

    Falsified by a fixture that shipped an enable flag true, and by an engine
    whose builtin default for a behaviour's enable flag was anything but off.
    """
    fixture = build_fixture(name, vocabulary=vocabulary)
    engine = _engine_over(fixture, vocabulary)

    fixture.adapter.actuate(_MOTIONS[name], "on", context=ChangeContext.world())
    records = engine.tick()

    assert records, f"{name} left no record, so nothing was evaluated"
    assert {record.outcome for record in records} == {Outcome.SKIPPED_DISABLED}
    assert {record.actor for record in records} == set(default_behaviours())
    assert fixture.adapter.read_entity(_LIGHTS[name]).state == "off"


def test_nothing_between_the_fixture_and_the_engine_enables_a_behaviour(
    fixtures: dict[FixtureName, Fixture],
) -> None:
    """Both halves of OFF by default: the house layer is empty and the builtin is off.

    Kept apart from the end-to-end test above because the two fail for different
    reasons: this one fails if a *fixture* starts carrying an enable, or if a
    unit's declared default stops being off, and the end-to-end test cannot say
    which of the two changed. It is the pair of assertions that makes the rule
    true rather than either one alone -- an empty house layer over a builtin that
    defaulted `True` would enable everything.

    Falsified by a fixture that set `behaviour.motion_lighting.enabled: true`
    for the room it considers its own, and by a unit whose `defaults` declared
    `enabled: True`, which would make the OFF-by-default rule false for that
    unit alone and invisible to every other.
    """
    declared = behaviour_defaults(default_behaviours().values())
    for name, fixture in fixtures.items():
        assert dict(fixture.house_settings) == {}, f"{name} declares settings"
        for behaviour_id in default_behaviours():
            key = enable_key(behaviour_id)
            assert declared[key] is False, f"{behaviour_id} defaults to on"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_clock_is_the_instant_the_fixture_was_given(
    name: FixtureName, vocabulary: Vocabulary
) -> None:
    """The fixture's clock reads its start instant, not the moment it was built.

    The wall-clock half of the spec's "building it does not open a network
    connection or read the wall clock", asserted as an equality rather than as a
    scan: `tools.catalog.substrate.check_wall_clock` proves no module under
    `sim/` *names* the wall clock, and this proves the value a built fixture
    reports is the one it was handed. A build that read `datetime.now` anywhere
    would give an instant a few microseconds after the constant, and the
    assertion would fail on the difference rather than on a substring.

    Falsified by `Simulation.start` taking its instant from `from_wall_clock`,
    which is the live-run entry point the spec keeps for the CLI and the MCP
    server and out of every fixture.
    """
    fixture = build_fixture(name, vocabulary=vocabulary, started_at=DEFAULT_STARTED_AT)
    assert fixture.clock.now == DEFAULT_STARTED_AT

    elsewhere = datetime(2030, 6, 15, 3, 30, tzinfo=UTC)
    moved = build_fixture(name, vocabulary=vocabulary, started_at=elsewhere)
    assert moved.clock.now == elsewhere
