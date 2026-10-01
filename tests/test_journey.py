"""The simulated user journey -- one person, from nothing to a working house.

`spec.txt:95-100` -- the phase's headline deliverable -- asks for the whole
product to be walked end to end in the order a person meets it: setup, the
first-day override, the bed button, the Roomba, a sensor that dies and is
replaced, and an update whose new behaviours wait to be opted into. This module
is that walk, written as one readable sequence so a reader can follow the story
down the file rather than reconstruct it from unit tests that each hold a piece.

Everything is driven through `openhouse.facade.open_session`: the fake house and
the virtual clock, no network and no wall-clock sleep. The oracle is the decision
log (`session.get_decision_log`), which is the record this project already keeps
-- a step asserts *why* something happened by its record's `rule` and `outcome`,
and reaches for a device's literal state only where a person would look at the
house and see it. That is deliberate: the fake port writes a proposed service as
though it were a state (`engine/behaviours/declared.py`), so `light.hall` reading
`light.turn_on` is the port's documented gap and not the light's colour.

Two steps in the brief cannot pass against the tree as it stands, and they are
left failing loudly with a message naming the missing mechanism rather than
weakened into a test of what happens to exist:

- **the bed button entering Sleep mode** -- `pack-manifest/1.2.0`'s behaviour
  clause has no value beside its `action` kind, so a manifest can say a behaviour
  ends in a service call and cannot say it enters a named mode;
- **the Roomba dispatching on its state** -- the same clause carries no `choose`,
  so the four behaviours fire together with the vacuum's state meaning nothing.

Both are the pack format's boundaries, recorded in the packs' own docstrings, and
each failing test below quotes the file and clause that would have to change.
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
        "house_scope": {"slots": ["house_mode", "light_group"]},
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
                "lux_sensor": "sensor.hall_lux",
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
                "lux_sensor": "sensor.hall_lux",
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
        "house_scope": {"slots": ["house_mode", "light_group", "lock"]},
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
    ] == [("light_group", ("light.bedroom",), "light.turn_off")]
    assert [(change.entity_id, change.before) for change in lights.state_delta] == [
        ("light.bedroom", "on")
    ]

    lock = _one(_since(session, mark), "bedtime.lock_up")
    assert lock.outcome is Outcome.SKIPPED_DISABLED
    assert session.read_entity("lock.front_door").state == "locked"


def test_bed_button_the_house_enters_sleep(vocabulary: Vocabulary) -> None:
    """The bed button puts the house in Sleep mode -- and today it cannot.

    Red on purpose, and the message is the point. `packs/official/bedtime.yaml`
    says in its own docstring that this clause is "deliberately absent, and
    cannot be, against `pack-manifest/1.2.0`": a behaviour's `action` axis is a
    block *kind* with no value beside it, so a manifest can say a behaviour ends
    in a service call and cannot say it *enters a named mode*. The whole pack
    therefore fires with the house in whatever mode it was already in. The gap is
    the clause `packs/official/bedtime.yaml:9-19` names, and it closes when the
    manifest carries the mode a behaviour activates -- the clause
    `schemas/mode/1.0.0.json` fixes the shape of.
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
        "house_scope": {"slots": ["house_mode", "light_group"]},
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
# ROOMBA -- four acts, and no state to choose between them
# --------------------------------------------------------------------------


def _roomba_session(vocabulary: Vocabulary) -> OpenHouse:
    """A foyer with a vacuum, the Roomba pack installed as a person installs it.

    A person installs the pack and turns its module on, which enables all four
    behaviours at once -- the state the pack is *for*, and the one in which the
    missing dispatch matters.
    """
    house = {
        "name": "the roomba house",
        "rooms": [
            {
                "id": "foyer",
                "name": "Foyer",
                "type": "foyer",
                "bindings": {"vacuum": {"entity_id": "vacuum.roomba"}},
            }
        ],
        "house_scope": {"slots": ["house_mode"]},
    }
    session = open_session(
        house=house,
        vocabulary=vocabulary,
        started_at=NIGHT,
        house_settings={
            "module.roomba.enabled": True,
            "behaviour.roomba.start_cleaning.enabled": True,
            "behaviour.roomba.send_home.enabled": True,
            "behaviour.roomba.resume_cleaning.enabled": True,
            "behaviour.roomba.notify_stuck.enabled": True,
        },
    )
    session.install_pack(str(PACKS / "roomba.yaml"))
    return session


def test_roomba_an_idle_vacuum_starts_cleaning(vocabulary: Vocabulary) -> None:
    """An idle Roomba starts a clean -- and today it is sent home instead.

    Red on purpose. `spec.txt:58` describes a dispatch: which of the four acts
    follows depends on the vacuum's current state. `pack-manifest/1.2.0` has no
    `choose` -- `catalog/pack-policy.yaml` forbids it -- and a behaviour's
    `condition` axis is a block kind with no value beside it, so no behaviour can
    say *which* state it waits for. The four therefore fire together and
    arbitration decides: `send_home` and `start_cleaning` share priority 30 and
    the tie-break picks `send_home`, so a docked vacuum is sent back to the dock
    it is already sitting on. The gap is the one
    `packs/official/roomba.yaml` records; it closes when a manifest can dispatch
    on a state.
    """
    session = _roomba_session(vocabulary)
    assert session.read_entity("vacuum.roomba").state == "docked"

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    actions = [
        (record.actor, command.action)
        for record in _since(session, mark)
        if record.outcome is Outcome.ACTED
        for command in record.commands
    ]
    assert actions == [("roomba.start_cleaning", "vacuum.start")], (
        f"an idle (docked) vacuum must start cleaning, but the pack proposed "
        f"{actions!r}: with all four behaviours enabled there is no state "
        "dispatch, so `send_home` wins arbitration whatever the vacuum is doing. "
        "`pack-manifest/1.2.0` has no `choose` and no value beside a behaviour's "
        "`condition` kind, which is the gap `packs/official/roomba.yaml` names."
    )


def test_roomba_a_stuck_vacuum_is_notified(vocabulary: Vocabulary) -> None:
    """A stuck Roomba raises a notification rather than being silently retried.

    Red on purpose, and for the same missing clause as the idle case. The pack
    declares `notify_stuck`, but with no way to say "when the vacuum is stuck" it
    declines on every tick, and the two `vacuum.start` behaviours propose into
    the void -- so a robot erroring in the corner is neither reported nor
    understood as anything different from a robot cleaning the floor.
    """
    session = _roomba_session(vocabulary)
    session.set_state("vacuum.roomba", "error")

    mark = len(session.get_decision_log())
    session.advance_time(minutes=1)

    notified = [
        (record.actor, command.action)
        for record in _since(session, mark)
        if record.outcome is Outcome.ACTED
        for command in record.commands
        if command.action.startswith("notify.")
    ]
    assert notified == [("roomba.notify_stuck", "notify.send_message")], (
        "a stuck vacuum must raise a notification, but the pack proposed "
        f"{notified!r}: `notify_stuck` has no way to say which state it responds "
        "to, because `pack-manifest/1.2.0` carries no value beside a behaviour's "
        "`condition` kind. The gap is `packs/official/roomba.yaml`'s."
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
                "lux_sensor": "sensor.hall_lux",
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
                "lux_sensor": "sensor.hall_lux",
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
