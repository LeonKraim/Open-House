"""The simulated user journey -- one person, from nothing to a working house.

`spec.txt:95-100` -- the phase's headline deliverable -- asks for the whole
product to be walked end to end in the order a person meets it: setup, the
first-day override, the bed button, a sensor that dies and is replaced, and an
update whose new behaviours wait to be opted into. This module is that walk,
written as one readable sequence so a reader can follow the story down the file
rather than reconstruct it from unit tests that each hold a piece.

Everything is driven through `openhouse.facade.open_session`: the fake house and
the virtual clock, no network and no wall-clock sleep. The oracle is the decision
log (`session.get_decision_log`), which is the record this project already keeps
-- a step asserts *why* something happened by its record's `rule` and `outcome`,
and reaches for a device's literal state only where a person would look at the
house and see it. That the state read off a device is the *state* and not a
service name is the thing `catalog/services.yaml` fixed: a declared service is
projected to what it writes (`light.turn_off` -> `off`) when the unit is built,
so `light.hall` reads `on` or `off` rather than the service a pack named.

One step in the brief was blocked on a clause `pack-manifest/1.3.0` adds, and it
is now written against it rather than left failing: **the bed button entering
Sleep mode**, where `1.3.0`'s `mode` is the value beside a `service` action and
`packs/official/bedtime.yaml` carries `mode: sleep`.

The bed-button steps install a *shipped* pack, and the shipped corpus is not
committed (see `packs/` in `.gitignore`), so they are marked `needs_corpus` and
skip on a checkout that has none. The rest of the walk builds its packs from
`tests/packfactory.py` and runs everywhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.behaviours import enable_key, module_enable_key
from engine.binding import HouseScope
from engine.decision_log import (
    DecisionRecord,
    Outcome,
    OverrideNote,
    Repair,
)
from engine.overrides import ResetCondition
from engine.vocabulary import Vocabulary
from openhouse.facade import OpenHouse, UnknownEntityError, open_session
from tools.catalog import paths

from .conftest import needs_corpus
from .packfactory import pack

ROOT = paths.ROOT
PACKS = ROOT / "packs" / "official"

#: A winter night in London. The sun is far below the horizon, so a room with no
#: lux reading to trust decides "dark" from the sun and motion lighting lights it.
NIGHT = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)

#: The two enable flags a person turns on while setting a room up. They are the
#: only units in Phase 1 that a room can use, and both arrive off: the product
#: rule is that a fresh house runs nothing (`engine/behaviours/base.py`).
ROOM_UNITS = {
    "behaviour.motion_lighting.enabled": True,
    "behaviour.override.enabled": True,
}


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


def _house(bindings: Mapping[str, str]) -> dict[str, object]:
    """A one-room house document, with `bindings` bound in its hall.

    `bindings` maps a slot to the entity id bound to it, which is the shape a
    person configures and the shape the house document carries one level deeper
    (`schemas/house/1.0.0.json`). `house_scope` is present even when it is not
    used, because the frozen schema requires it and an "empty house" is a house
    with an empty room rather than a document that is missing a clause.
    """
    return {
        "name": "the journey house",
        "rooms": [
            {
                "id": "hall",
                "name": "Hall",
                "type": "hallway",
                "bindings": {
                    slot: {"entity_id": entity_id}
                    for slot, entity_id in bindings.items()
                },
            }
        ],
        "house_scope": {"slots": ["light_group"]},
    }


def _session(
    vocabulary: Vocabulary,
    *,
    house: Mapping[str, object] | str,
    settings: Mapping[str, object] | None = None,
) -> OpenHouse:
    """A session on `house` at the fixed night, with the night's tunables set."""
    return open_session(
        house=house,
        vocabulary=vocabulary,
        started_at=NIGHT,
        seed=7,
        house_settings={**ROOM_UNITS, **(settings or {})},
    )


def _since(session: OpenHouse, mark: int) -> tuple[DecisionRecord, ...]:
    """Every record written since `mark` -- the ticks a step is about."""
    return session.get_decision_log()[mark:]


def _of(records: Sequence[DecisionRecord], actor: str) -> list[DecisionRecord]:
    """Every record `actor` left, in order."""
    return [record for record in records if record.actor == actor]


def _one(records: Sequence[DecisionRecord], actor: str) -> DecisionRecord:
    """The single record `actor` left, so a test asserts about one decision."""
    found = _of(records, actor)
    assert len(found) == 1, f"{actor} left {len(found)} records, expected one"
    return found[0]


def _acted(records: Sequence[DecisionRecord]) -> list[DecisionRecord]:
    """Every record that reached the house, which is what "something happened" means."""
    return [record for record in records if record.outcome is Outcome.ACTED]


# --------------------------------------------------------------------------
# SETUP -- the house is empty, then it is not
# --------------------------------------------------------------------------


def test_setup_an_empty_house_runs_nothing(vocabulary: Vocabulary) -> None:
    """A house with no devices bound automates nothing, and says so per unit.

    The product rule is that nothing runs until a person turns it on, and this is
    its first half: the two units are *enabled* and still decide nothing, because
    the slots they reach for are unbound. A falsifying implementation that acted
    on an unbound slot would have to invent an entity to act on; one that stayed
    silent without recording why would leave the log unable to tell "nothing is
    configured" from "the unit never ran" -- which is why the two `skipped:
    unbound slot` records are asserted, not merely the absence of a decision.
    """
    session = _session(vocabulary, house=_house({}))
    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    records = _since(session, mark)
    assert _acted(records) == []
    for actor in ("motion_lighting", "override"):
        record = _one(records, actor)
        assert record.outcome is Outcome.SKIPPED_UNBOUND_SLOT
    assert not session.house.rooms[0].bindings
    with pytest.raises(UnknownEntityError):
        session.read_entity("light.hall")


def test_setup_binding_the_devices_makes_the_house_usable(
    vocabulary: Vocabulary,
) -> None:
    """Binding a room's devices is what turns the house on, and motion lights it.

    Setup's second half: `import_config` is the facade's setup path -- it replaces
    the house and materialises the bound devices at their initial states -- and a
    person walking into the dark hall afterwards gets the light they configured,
    recorded as an action under the corpus rule rather than merely observed as a
    state. A falsifying implementation whose import bound the slots but did not
    materialise the devices would skip the slot it could not read.
    """
    session = _session(vocabulary, house=_house({}))
    session.import_config(
        _house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
                "ambient_light_sensor": "sensor.hall_lux",
            }
        )
    )
    session.set_state("sensor.hall_lux", "5")
    session.set_state("binary_sensor.hall_motion", "on")

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    record = _one(_since(session, mark), "motion_lighting")
    assert record.outcome is Outcome.ACTED
    assert record.rule == "lighting.motion_light_on"
    assert [(command.slot, command.entities) for command in record.commands] == [
        ("light_group", ("light.hall",))
    ]
    assert session.read_entity("light.hall").state == "on"


# --------------------------------------------------------------------------
# FIRST-DAY OVERRIDE -- a hand on the switch beats the automation
# --------------------------------------------------------------------------


def test_first_day_override_a_persons_hand_holds_the_light(
    vocabulary: Vocabulary,
) -> None:
    """A light a person turned on is not turned off by the unit that wanted it off.

    The first day's classic: motion lit the hall, the hall went quiet, and the
    person -- sitting still in it -- put a hand on the switch. The unit's next
    attempt to darken the room must be *overridden* rather than applied, and the
    record must name the conditions that would release the hold, because an
    override nobody can see is an override nobody can explain. A falsifying
    implementation that let the unit win would darken a room somebody is sitting
    in; one that suppressed the unit without recording the outcome would lose the
    fact that the person, not the rule, decided.
    """
    session = _session(vocabulary, house=_house({}))
    session.import_config(
        _house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
                "ambient_light_sensor": "sensor.hall_lux",
            }
        )
    )
    session.set_state("sensor.hall_lux", "5")
    session.set_state("binary_sensor.hall_motion", "on")
    session.advance_time(minutes=1)
    assert session.read_entity("light.hall").state == "on"

    # The hall goes quiet and the person turns the light on under the automation's
    # hand. Then enough time passes for the five-minute quiet timeout to elapse.
    session.set_state("binary_sensor.hall_motion", "off")
    session.user_action("light.hall", "on")
    mark = len(session.get_decision_log())
    session.advance_time(minutes=10)

    off_decisions = [
        rec
        for rec in _since(session, mark)
        if rec.actor == "motion_lighting" and rec.rule == "lighting.motion_light_off"
    ]
    assert len(off_decisions) == 1
    decision = off_decisions[0]
    assert decision.outcome is Outcome.OVERRIDDEN

    notes = [entry for entry in decision.inputs if isinstance(entry, OverrideNote)]
    assert [note.entity_id for note in notes] == ["light.hall"]
    assert ResetCondition.OVERRIDE_TIMEOUT in notes[0].awaited
    assert session.engine.overrides.is_overridden("light.hall")
    assert session.read_entity("light.hall").state == "on"


# --------------------------------------------------------------------------
# BED BUTTON -- lights off, and the locks alone
# --------------------------------------------------------------------------


@needs_corpus
def test_bed_button_the_lights_go_off_and_the_locks_do_not(
    vocabulary: Vocabulary,
) -> None:
    """The pack's lights behaviour acts and its lock behaviour stays off.

    `spec.txt:58` describes the bed button as "lights off, Sleep mode, optional
    thermostat, locks off by default", and the last clause is a *property of
    installation* rather than a flag: every behaviour a pack declares installs
    disabled, so a house that never enables `lock_up` never locks a door. This
    asserts both halves -- `lights_off` reaches the house under its own rule, and
    `lock_up` is `skipped: disabled` with the door still shut -- because a
    falsifying implementation that installed behaviours enabled would lock a door
    the instant the pack arrived.
    """
    house = {
        "name": "the bedtime house",
        "rooms": [
            {
                "id": "bedroom",
                "name": "Bedroom",
                "type": "bedroom",
                "bindings": {
                    "light_group": {"entity_id": "light.bedroom"},
                    "lock": {"entity_id": "lock.front_door"},
                },
            }
        ],
        "house_scope": {"slots": ["light_group", "lock"]},
    }
    session = _session(
        vocabulary,
        house=house,
        settings={
            "module.bedtime.enabled": True,
            "behaviour.bedtime.lights_off.enabled": True,
        },
    )
    session.set_state("light.bedroom", "on")

    mark = len(session.get_decision_log())
    session.install_pack(str(PACKS / "bedtime.yaml"))
    session.advance_time(minutes=1)

    lights = _one(_since(session, mark), "bedtime.lights_off")
    assert lights.outcome is Outcome.ACTED
    assert lights.rule == "lights_off"
    assert [
        (command.slot, command.entities, command.action) for command in lights.commands
    ] == [("light_group", ("light.bedroom",), "off")]
    assert [(change.entity_id, change.before) for change in lights.state_delta] == [
        ("light.bedroom", "on")
    ]

    lock = _one(_since(session, mark), "bedtime.lock_up")
    assert lock.outcome is Outcome.SKIPPED_DISABLED
    assert session.read_entity("lock.front_door").state == "locked"


@needs_corpus
def test_bed_button_the_house_enters_sleep(vocabulary: Vocabulary) -> None:
    """The bed button puts the house in Sleep mode.

    The step that was blocked until `pack-manifest/1.3.0`: a behaviour's `action`
    axis was a block *kind* with no value beside it, so a manifest could say a
    behaviour ends in a service call and could not say it *enters a named mode*.
    `1.3.0`'s `mode` is that value, `packs/official/bedtime.yaml`'s `lights_off`
    carries `mode: sleep`, and the engine applies it after arbitrating the tick's
    commands -- so the record for the press shows the lights going off *and* the
    house entering Sleep, which is the pair `spec.txt:58` describes.
    """
    house = {
        "name": "the bedtime house",
        "rooms": [
            {
                "id": "bedroom",
                "name": "Bedroom",
                "type": "bedroom",
                "bindings": {"light_group": {"entity_id": "light.bedroom"}},
            }
        ],
        "house_scope": {"slots": ["light_group"]},
    }
    session = open_session(
        house=house,
        vocabulary=vocabulary,
        started_at=NIGHT,
        modes=[
            {
                "name": "home",
                "description": "Somebody is in.",
                "exclusive_group": "presence",
            },
            {
                "name": "away",
                "description": "Nobody is in.",
                "exclusive_group": "presence",
            },
            {
                "name": "sleep",
                "description": "The house is asleep.",
                "exclusive_group": "presence",
            },
        ],
        house_settings={
            "module.bedtime.enabled": True,
            "behaviour.bedtime.lights_off.enabled": True,
        },
    )
    session.set_state("light.bedroom", "on")
    session.install_pack(str(PACKS / "bedtime.yaml"))
    session.advance_time(minutes=1)

    assert "sleep" in session.engine.modes.active, (
        "the Bedtime pack does not put the house in Sleep mode, and cannot: "
        "`pack-manifest/1.2.0`'s behaviour clause is "
        "`name, trigger, condition, action, priority, services, slots` and its "
        "`action` axis is a block kind with no value beside it, so no manifest "
        "can say a behaviour enters a named mode. The pack's own docstring "
        "(`packs/official/bedtime.yaml`) records this; the clause that would "
        "express it is the mode a behaviour activates."
    )


# --------------------------------------------------------------------------
# SENSOR DEATH AND REPLACE -- silence is not emptiness
# --------------------------------------------------------------------------


def test_sensor_death_a_dead_motion_sensor_decides_nothing(
    vocabulary: Vocabulary,
) -> None:
    """A motion sensor that stops answering holds the room, it does not empty it.

    `catalog/edge_cases.yaml` names "a motion sensor that stops reporting", and
    the danger is the obvious one: a dead sensor read as "no motion" would darken
    a room somebody is standing in. The unit refuses to decide through a slot it
    cannot read -- the engine's dwell registry refuses to call such a room quiet
    and the unit records a `Repair` naming the dead device -- so the tick's
    motion decision is a decline and the light stays as the person left it. A
    falsifying implementation that read unavailability as "not moving" would turn
    the light off five minutes later, which is what this asserts does not happen.
    """
    session = _session(vocabulary, house=_house({}))
    session.import_config(
        _house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
                "ambient_light_sensor": "sensor.hall_lux",
            }
        )
    )
    session.set_state("sensor.hall_lux", "5")
    session.set_state("binary_sensor.hall_motion", "on")
    session.advance_time(minutes=1)
    assert session.read_entity("light.hall").state == "on"

    session.set_availability("binary_sensor.hall_motion", False)
    assert session.read_entity("binary_sensor.hall_motion").available is False

    mark = len(session.get_decision_log())
    session.advance_time(minutes=10)

    records = _since(session, mark)
    assert _acted(records) == []
    decision = _one(records, "motion_lighting")
    assert decision.outcome is Outcome.DECLINED
    assert decision.rule == "lighting.motion_light_on"
    repairs = [entry for entry in decision.inputs if isinstance(entry, Repair)]
    assert [repair.entity_id for repair in repairs] == ["binary_sensor.hall_motion"]
    assert session.read_entity("light.hall").state == "on"


def test_sensor_death_replacing_the_sensor_restores_the_behaviour(
    vocabulary: Vocabulary,
) -> None:
    """Swapping the dead device for a working one brings the room back.

    The second half of the step, and the half that makes the first useful: a hold
    that never lifts is a house that stops working. The device is removed and a
    fresh one added under the same entity id -- what a person does when they
    replace the hardware -- and the next motion lights the room again, under the
    same rule. A falsifying implementation that cached the dead slot's repair, or
    that keyed its dwell on the entity rather than the slot, would leave the room
    held forever.
    """
    session = _session(vocabulary, house=_house({}))
    session.import_config(
        _house(
            {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
                "ambient_light_sensor": "sensor.hall_lux",
            }
        )
    )
    session.set_state("sensor.hall_lux", "5")
    session.set_state("binary_sensor.hall_motion", "on")
    session.advance_time(minutes=1)
    session.set_availability("binary_sensor.hall_motion", False)
    session.advance_time(minutes=10)
    assert session.read_entity("light.hall").state == "on"

    session.remove_entity("binary_sensor.hall_motion")
    session.add_entity("binary_sensor.hall_motion", "off")
    assert session.read_entity("binary_sensor.hall_motion").available is True
    session.set_state("light.hall", "off")
    session.set_state("binary_sensor.hall_motion", "on")

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    decision = _one(_since(session, mark), "motion_lighting")
    assert decision.outcome is Outcome.ACTED
    assert decision.rule == "lighting.motion_light_on"
    assert session.read_entity("light.hall").state == "on"


# --------------------------------------------------------------------------
# UPDATE WITH OPT-IN BEHAVIOURS -- an upgrade changes nothing until asked
# --------------------------------------------------------------------------


def _nightlight(home: Path, *, version: str, count: int) -> str:
    """A pack `count` behaviours wide, written where a registry would keep it.

    Built with `tests/packfactory.py` rather than a hand-written fixture, because
    what is under test is a *version pair* -- two manifests of one pack name --
    and the builder is the project's one way of writing a generated manifest.
    """
    return str(pack(home, "nightlight", version=version, count=count))


def test_update_a_new_behaviour_arrives_disabled(
    vocabulary: Vocabulary, tmp_path: Path
) -> None:
    """An update installs its behaviour and does not run it.

    "Installation is not activation": the upgrade replaces the pack's old
    behaviour with the new set, and every one of them arrives disabled, so a
    person who updates a pack watches their house behave exactly as it did until
    they say otherwise. A falsifying implementation that activated an update's
    new behaviours would change a running house without asking, which is the
    failure opt-in exists to prevent.
    """
    session = _session(vocabulary, house=_house({"light_group": "light.hall"}))

    first = session.install_pack(_nightlight(tmp_path / "v1", version="1.0.0", count=1))
    assert first["change"] == "install"
    assert first["behaviours"] == ("nightlight.b0",)
    assert first["enabled"] == ()

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)
    assert _acted(_since(session, mark)) == []

    update = session.install_pack(
        _nightlight(tmp_path / "v2", version="1.1.0", count=2)
    )
    assert update["change"] == "upgrade"
    assert update["replaced"] == "1.0.0"
    assert update["behaviours"] == ("nightlight.b0", "nightlight.b1")
    assert update["enabled"] == ()

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)
    assert _acted(_since(session, mark)) == []


def test_update_opting_in_makes_the_new_behaviour_take_effect(
    vocabulary: Vocabulary, tmp_path: Path
) -> None:
    """Turning the module and the new behaviour on is what makes the update real.

    The other half of opt-in: the same update, with the two flags a person would
    set, acts. The check is the *new* behaviour's record and not the old one's,
    so a falsifying implementation that keyed enablement on the pack rather than
    on the behaviour would light the house as soon as the module flag was set and
    never mind which behaviour the person meant.
    """
    session = _session(vocabulary, house=_house({"light_group": "light.hall"}))
    session.install_pack(_nightlight(tmp_path / "v1", version="1.0.0", count=1))
    session.install_pack(_nightlight(tmp_path / "v2", version="1.1.0", count=2))

    session.engine.settings.set_override(
        module_enable_key("nightlight"), HouseScope(), True
    )
    session.engine.settings.set_override(
        enable_key("nightlight.b1"), HouseScope(), True
    )

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    decision = _one(_since(session, mark), "nightlight.b1")
    assert decision.outcome is Outcome.ACTED
    assert [(command.slot, command.entities) for command in decision.commands] == [
        ("light_group", ("light.hall",))
    ]
