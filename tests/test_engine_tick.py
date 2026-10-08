"""The tick: one record per evaluation, one write per entity, in a fixed order.

This module holds the two claims the phase exists for, together. The **oracle**
claim is that every evaluation leaves exactly one record whichever way it went --
acted, declined, suppressed, or skipped before it reached a rule -- because "off
because the rule behaved" and "off because the light was never on" are different
facts that device state cannot tell apart. The **precedence** claim is that the
tick's stages run in a declared order, so a suppressed command's outcome names
the stage that stopped it rather than whichever check happened to run first.

The two are tested together because they are one mechanism: a record is only
useful if its outcome is a fact about how far the command got, and a stage is only
testable if the record says the command reached it. So the outcome tests each
build a house in which exactly one stage can fire, and the precedence tests build
houses in which two could and assert which one the record names.

Three tests are about the tick as a *function* rather than about a stage:
determinism, the restore, and the single-writer claim. The rest are about what a
behaviour decides, asserted through the record -- which is the only place the
phase's decisions are visible.

Each test says, in its docstring, what a falsifying implementation would look
like.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.adapter import ChangeContext, ChangeOrigin, domain_of
from engine.behaviours import (
    BehaviourScope,
    default_behaviours,
    enable_key,
    module_enable_key,
)
from engine.behaviours.declared import declared_units
from engine.binding import House, SlotRead
from engine.config import RATE_LIMIT_BOUND_KEY, ResolvedSetting
from engine.decision_log import (
    DarkSource,
    DarkSourceReading,
    DecisionRecord,
    HousePresence,
    ModeReading,
    Outcome,
    OverrideNote,
)
from engine.engine import Engine, EngineError
from engine.modes import ModeSet
from engine.overrides import ResetCondition
from engine.solar import Location
from engine.vocabulary import Vocabulary
from sim.adapter import FakeHouseAdapter
from sim.clock import VirtualClock
from sim.entropy import RandomStream
from sim.snapshot import ENGINE_STATE_FIELDS

if TYPE_CHECKING:
    from engine.behaviours import Behaviour, BehaviourContext

ROOT = Path(__file__).resolve().parents[1]

#: A winter night in London: the sun is far below the horizon, so motion
#: lighting's dark test is decided by the sun and its answer is "dark".
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

#: The kitchen every harness starts from. `light_group` is bound in the room
#: because a house-scope slot is aggregated from the rooms (`resolve_slot`), so a
#: house that binds it nowhere cannot resolve the shutdown's required slot.
KITCHEN: Mapping[str, str] = {
    "motion_sensor": "binary_sensor.kitchen_motion",
    "light_group": "light.kitchen",
}

#: The state a fresh entity of each domain is added in: off for anything
#: switchable, home for the mode selector, locked for a lock.
_INITIAL: Mapping[str, str] = {
    "binary_sensor": "off",
    "light": "off",
    "switch": "off",
    "sensor": "5",
    "input_select": "home",
    "lock": "locked",
}

_ALL_ENABLED = (
    "behaviour.motion_lighting.enabled",
    "behaviour.override.enabled",
    "behaviour.away_shutdown.enabled",
)


class _UnlockBehaviour:
    """A unit that proposes an unlock, so the safety veto has something to refuse.

    None of the phase's three units can reach an egress domain -- that is the
    `first-behaviours` check the shutdown's own docstring names -- so
    `refused: unsafe` would be unproducible from the shipped registry alone. This
    unit exists to produce it, which is what makes "every outcome is producible
    by some evaluation" a claim about the engine rather than about the three
    units Phase 1 happens to ship.
    """

    id = "unlocker"
    corpus_rows = ("lighting.room_light_manual_on",)
    scope = BehaviourScope.ROOM
    required_slots = ("lock",)
    optional_slots = ()
    priority = 0
    module: str | None = None
    enabled = True
    defaults: Mapping[str, object] = {}

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose an unlock. The engine, not this unit, is what refuses it."""
        ctx.matched("lighting.room_light_manual_on")
        ctx.propose(
            slot="lock", action="unlocked", rule="lighting.room_light_manual_on"
        )


# --------------------------------------------------------------------------
# Building a harness
# --------------------------------------------------------------------------


def _document(
    layout: Mapping[str, Mapping[str, str]],
    house_scope_slots: Sequence[str],
) -> dict[str, object]:
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
        "house_scope": {"slots": list(house_scope_slots)},
    }


def _build(
    vocabulary: Vocabulary,
    *,
    at: datetime = NIGHT,
    layout: Mapping[str, Mapping[str, str]] | None = None,
    entities: Mapping[str, str] | None = None,
    settings: Mapping[str, object] | None = None,
    room_settings: Mapping[str, Mapping[str, object]] | None = None,
    behaviours: Sequence[Behaviour] | None = None,
    house_scope_slots: Sequence[str] = ("light_group",),
    state: Mapping[str, object] | None = None,
) -> tuple[Engine, FakeHouseAdapter, VirtualClock]:
    """A house, a fake and an engine over them, all three units enabled.

    The default is a single kitchen with its three slots bound and the shipped
    units on, which is the least a tick needs to leave a record of each kind. A
    test that wants one stage to be the only one that can fire narrows this
    rather than building from nothing.
    """
    rooms = dict(layout) if layout is not None else {"kitchen": dict(KITCHEN)}
    house_settings: dict[str, object] = dict.fromkeys(_ALL_ENABLED, True)
    house_settings.update(settings or {})

    clock = VirtualClock.started_at(at)
    adapter = FakeHouseAdapter(clock=clock, random_stream=RandomStream.from_seed(1))
    initial = {
        entity_id: _INITIAL[domain_of(entity_id)]
        for bindings in rooms.values()
        for entity_id in bindings.values()
    }
    initial.update(entities or {})
    for entity_id, value in initial.items():
        adapter.add_entity(entity_id, value, context=ChangeContext.world())

    house = House.from_document(
        _document(rooms, house_scope_slots), vocabulary=vocabulary
    )
    engine = Engine(
        adapter=adapter,
        house=house,
        clock=clock,
        location=LOCATION,
        modes=ModeSet(MODES, vocabulary=vocabulary),
        behaviours=behaviours,
        house_settings=house_settings,
        room_settings=room_settings,
        state=state,
    )
    return engine, adapter, clock


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


def _one(records: Sequence[DecisionRecord], actor: str) -> DecisionRecord:
    """The single record `actor` left this tick."""
    found = _of(records, actor)
    assert len(found) == 1, f"{actor} left {len(found)} records"
    return found[0]


def _of(records: Sequence[DecisionRecord], actor: str) -> list[DecisionRecord]:
    """Every record `actor` left this tick, in order."""
    return [record for record in records if record.actor == actor]


def _readings[Entry](record: DecisionRecord, kind: type[Entry]) -> list[Entry]:
    """Record `record`'s inputs of one kind, in the order it consulted them."""
    return [entry for entry in record.inputs if isinstance(entry, kind)]


# --------------------------------------------------------------------------
# One drive per outcome
# --------------------------------------------------------------------------


def _drive_acted(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """Motion in the dark with the light off: the behaviour acts."""
    engine, adapter, _ = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    return engine.tick()


def _drive_declined(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """Motion in a room that is already lit: the rule is reached and declines."""
    engine, adapter, _ = _build(vocabulary, entities={"light.kitchen": "on"})
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    return engine.tick()


def _drive_overridden(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """A person's hand on the light, and then motion wanting it: suppressed."""
    engine, adapter, _ = _build(vocabulary)
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    return engine.tick()


def _drive_lost_arbitration(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """Away and emptied and lit: the shutdown and motion lighting both want the light."""
    engine, _, clock = _build(vocabulary, entities={"light.kitchen": "on"})
    engine.modes.activate("away")
    engine.tick()
    clock.advance(timedelta(minutes=10))
    return engine.tick()


def _drive_rate_limited(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """A second command inside one window, with the bound at one."""
    engine, adapter, _ = _build(
        vocabulary,
        settings={RATE_LIMIT_BOUND_KEY: 1, "engine.rate_limit.window_seconds": 3600.0},
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    adapter.actuate("light.kitchen", "off", context=ChangeContext.world())
    return engine.tick()


def _drive_skipped_unbound(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """A room that binds no motion sensor: motion lighting never reaches a rule."""
    engine, _, _ = _build(
        vocabulary,
        layout={
            "kitchen": {
                "light_group": "light.kitchen",
            }
        },
    )
    return engine.tick()


def _drive_skipped_disabled(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """A fresh house, nothing its owner enabled: nothing runs."""
    engine, _, _ = _build(vocabulary, settings=dict.fromkeys(_ALL_ENABLED, False))
    return engine.tick()


def _drive_refused_unsafe(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """A behaviour that wants the door unlocked, which no origin but a user's may."""
    engine, _, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "lock": "lock.front_door"}},
        behaviours=(*default_behaviours().values(), _UnlockBehaviour()),
    )
    return engine.tick()


_Drives = Callable[[Vocabulary], Sequence[DecisionRecord]]


def _pack_units(
    *, pack: str, name: str, suppresses: Sequence[str] = ()
) -> tuple[Behaviour, ...]:
    """The units one tiny manifest declares, built by the real builder.

    Both drivers below go through `declared_units` rather than through a
    hand-written double, because the fact under test is a *clause* -- it arrives
    in a document and has to survive the projection into the engine's fact set --
    and a double with the attribute set by hand would exercise the gate while
    proving nothing about the manifest that is supposed to reach it.
    """
    row: dict[str, object] = {
        "name": name,
        "action": "service",
        "services": [],
        "slots": ["light_group"],
    }
    if suppresses:
        row["suppresses"] = list(suppresses)
    return declared_units(
        pack, {"behaviours": [row]}, default_priority=0, service_states={}
    )


def _drive_skipped_suppressed(vocabulary: Vocabulary) -> Sequence[DecisionRecord]:
    """One pack holding another off: the target's atoms never reach their rule."""
    holder = _pack_units(pack="holder", name="hold", suppresses=["target"])
    target = _pack_units(pack="target", name="shine")
    engine, _, _ = _build(
        vocabulary,
        behaviours=(*holder, *target),
        settings={
            enable_key("holder.hold"): True,
            enable_key("target.shine"): True,
            module_enable_key("holder"): True,
            module_enable_key("target"): True,
        },
    )
    return engine.tick()


_Drives = Callable[[Vocabulary], Sequence[DecisionRecord]]

_DRIVES: Mapping[Outcome, _Drives] = {
    Outcome.ACTED: _drive_acted,
    Outcome.DECLINED: _drive_declined,
    Outcome.OVERRIDDEN: _drive_overridden,
    Outcome.LOST_ARBITRATION: _drive_lost_arbitration,
    Outcome.RATE_LIMITED: _drive_rate_limited,
    Outcome.SKIPPED_UNBOUND_SLOT: _drive_skipped_unbound,
    Outcome.SKIPPED_DISABLED: _drive_skipped_disabled,
    Outcome.SKIPPED_SUPPRESSED: _drive_skipped_suppressed,
    Outcome.REFUSED_UNSAFE: _drive_refused_unsafe,
}


@pytest.mark.parametrize("outcome", list(_DRIVES))
def test_every_outcome_is_producible_by_some_evaluation(
    vocabulary: Vocabulary, outcome: Outcome
) -> None:
    """Each of the nine outcomes appears in some record some evaluation leaves.

    A falsifying implementation that dropped a stage -- or that reported a
    suppressed command under another stage's name -- would make one of the nine
    unreachable, and the log would be unable to explain the situation that stage
    exists for. Declaring a closed enum is the vacuous way to satisfy it; the way
    this test demands is to produce it.
    """
    records = _DRIVES[outcome](vocabulary)
    produced = {record.outcome for record in records}
    assert outcome in produced, sorted(str(member) for member in produced)


def test_the_outcome_set_is_closed_at_the_nine_named_values() -> None:
    """The vocabulary the log can express is exactly these nine, by name.

    A falsifying implementation that renamed a member -- or that grew a tenth for
    a stage it added -- would leave a scenario asserting on the old name matching
    nothing, and a bare cardinality check would not notice a rename at all. The
    names are the strings the decision log writes into a record
    (`decision_log.py`'s `str(self.outcome)`), so this asserts the wire form and
    not the Python spelling.
    """
    assert {str(member) for member in Outcome} == {
        "acted",
        "declined",
        "lost arbitration",
        "overridden",
        "rate-limited",
        "skipped: unbound slot",
        "skipped: disabled",
        "skipped: suppressed",
        "refused: unsafe",
    }


# --------------------------------------------------------------------------
# acted and declined, in full
# --------------------------------------------------------------------------


def test_motion_in_the_dark_acts_on_the_room_s_light(vocabulary: Vocabulary) -> None:
    """The acted record names its rule, its command, and the change it applied.

    A falsifying implementation that wrote the light without recording the change
    would leave a scenario asserting "the light is on" unable to tell which
    behaviour turned it on, and the `state_delta` is the field that answers.
    """
    record = _one(_drive_acted(vocabulary), "motion_lighting")
    assert record.outcome is Outcome.ACTED
    assert record.rule == "lighting.motion_light_on"
    assert [
        (change.entity_id, change.before, change.after) for change in record.state_delta
    ] == [("light.kitchen", "off", "on")]
    assert [command.slot for command in record.commands] == ["light_group"]


def test_the_write_actually_reached_the_house(vocabulary: Vocabulary) -> None:
    """The record says `acted` and the port says the light is on.

    A falsifying implementation that recorded `acted` without actuating -- a log
    written from the intention rather than from the result -- would pass every
    assertion above and leave a house that never lights up.
    """
    engine, adapter, _ = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    assert adapter.read_entity("light.kitchen").state == "on"


def test_a_room_already_lit_declines_and_names_its_rule(
    vocabulary: Vocabulary,
) -> None:
    """The commonest decline names the rule it was evaluating rather than nothing.

    A falsifying implementation that left `rule` as `None` for a decline would
    make "motion lighting looked and the room was already visible" and "the
    behaviour never ran" the same record -- which is exactly the difference
    `design.md` D2 turns the whole oracle on.
    """
    record = _one(_drive_declined(vocabulary), "motion_lighting")
    assert record.outcome is Outcome.DECLINED
    assert record.rule == "lighting.motion_light_on"
    assert record.commands == ()
    assert record.state_delta == ()


def test_a_declining_behaviour_leaves_the_house_alone(vocabulary: Vocabulary) -> None:
    """The engine is the only writer, so a decline writes nothing.

    A falsifying implementation that actuated the state it wanted rather than the
    one it proposed would turn the light on in a room that was already lit and
    record a decline, which is why the claim is asserted against the port.
    """
    engine, adapter, _ = _build(vocabulary, entities={"light.kitchen": "on"})
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    entity = adapter.read_entity("light.kitchen")
    assert entity.state == "on"
    assert entity.last_origin is ChangeOrigin.WORLD


# --------------------------------------------------------------------------
# The suppression stages, and which one the record names
# --------------------------------------------------------------------------


def test_a_user_s_touch_suppresses_the_command_it_outranks(
    vocabulary: Vocabulary,
) -> None:
    """An overridden command is recorded as overridden, with what is awaited.

    A falsifying implementation that dropped the command silently would leave a
    record indistinguishable from a decline, so a user told "the behaviour ran
    and was overridden" would instead be told nothing at all. The awaited
    conditions are part of the claim: they are how the log explains what would
    end the suppression.
    """
    record = _one(_drive_overridden(vocabulary), "motion_lighting")
    assert record.outcome is Outcome.OVERRIDDEN
    awaited = sorted(
        condition
        for note in _readings(record, OverrideNote)
        for condition in note.awaited  # type: ignore[attr-defined]
    )
    assert awaited == [
        ResetCondition.EXPLICIT_CLEAR,
        ResetCondition.OVERRIDE_TIMEOUT,
        ResetCondition.ROOM_EMPTIED,
    ]


def test_a_suppressed_command_writes_nothing(vocabulary: Vocabulary) -> None:
    """The override suppresses, so the light is where the person left it."""
    engine, adapter, _ = _build(vocabulary)
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    assert adapter.read_entity("light.kitchen").state == "off"


def test_a_suppressed_command_does_not_spend_the_rate_limit(
    vocabulary: Vocabulary,
) -> None:
    """Override runs before the limit, so a suppressed command costs nothing.

    A falsifying implementation that ran the limit first would report
    `rate-limited` -- a different stage's answer -- and would spend the entity's
    allowance on a command the engine never intended to send, so a room somebody
    is using would exhaust the budget its own automation needs the moment they
    leave. The test is two halves: the outcome, and the allowance still being
    there afterwards.
    """
    engine, adapter, clock = _build(
        vocabulary,
        settings={RATE_LIMIT_BOUND_KEY: 1, "engine.rate_limit.window_seconds": 3600.0},
    )
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.OVERRIDDEN

    engine.overrides.clear("light.kitchen")
    clock.advance(timedelta(seconds=1))
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.ACTED


def test_a_second_command_inside_one_window_is_rate_limited(
    vocabulary: Vocabulary,
) -> None:
    """The engine's own budget, reported under its own outcome.

    A falsifying implementation that folded the limit into arbitration priority
    would report `lost arbitration` here, and an agent could not tell "a
    higher-priority behaviour won" from "the limit was reached" -- the two
    diagnoses `design.md` D6 keeps apart.
    """
    record = _one(_drive_rate_limited(vocabulary), "motion_lighting")
    assert record.outcome is Outcome.RATE_LIMITED
    assert record.state_delta == ()


def test_an_unsafe_command_is_refused_and_named_as_refused(
    vocabulary: Vocabulary,
) -> None:
    """The veto's outcome says the command was the problem, not the priority.

    A falsifying implementation that refused inside the adapter would record
    `acted` -- the refusal would be a runtime surprise instead of a recorded fact
    -- and the product rule would be unexaminable from the log.
    """
    record = _one(_drive_refused_unsafe(vocabulary), "unlocker")
    assert record.outcome is Outcome.REFUSED_UNSAFE
    assert [command.action for command in record.commands] == ["unlocked"]
    assert record.state_delta == ()


def test_a_refused_unlock_leaves_the_door_locked(vocabulary: Vocabulary) -> None:
    """The veto refuses before the port is asked, so nothing was written."""
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "lock": "lock.front_door"}},
        behaviours=(*default_behaviours().values(), _UnlockBehaviour()),
    )
    engine.tick()
    assert adapter.read_entity("lock.front_door").state == "locked"


def test_away_shutdown_wins_the_light_and_motion_lighting_loses(
    vocabulary: Vocabulary,
) -> None:
    """Two propositions for one light resolve to one write and one loss.

    A falsifying implementation that applied both commands would leave the light
    in whichever state was written last, and the outcome would depend on the
    evaluation order -- the instability `design.md` D6 rejected last-writer-wins
    for.
    """
    records = _drive_lost_arbitration(vocabulary)
    shutdown = _one(records, "away_shutdown")
    motion = _one(records, "motion_lighting")
    assert shutdown.outcome is Outcome.ACTED
    assert shutdown.rule == "lighting.away_shutdown"
    assert motion.outcome is Outcome.LOST_ARBITRATION
    assert motion.state_delta == ()
    assert shutdown.state_delta[0].after == "off"


def test_only_one_write_reached_a_contested_entity(vocabulary: Vocabulary) -> None:
    """The light ends where the winner put it, and the loser did not write over it."""
    engine, adapter, clock = _build(vocabulary, entities={"light.kitchen": "on"})
    engine.modes.activate("away")
    engine.tick()
    clock.advance(timedelta(minutes=10))
    engine.tick()
    assert adapter.read_entity("light.kitchen").state == "off"


# --------------------------------------------------------------------------
# The gates, which run before any rule is reached
# --------------------------------------------------------------------------


def test_a_required_slot_left_unbound_skips_before_any_rule(
    vocabulary: Vocabulary,
) -> None:
    """The skip names the slot it could not resolve, and names no rule.

    A falsifying implementation that ran the behaviour anyway would have it read
    an empty slot as "no motion", so a house that had not bound a sensor would
    read as permanently dark and permanently empty -- the two conclusions
    `house-adapter`'s "unavailable is not off" exists to prevent. One that named
    a rule would be recording a decision the behaviour never made.
    """
    record = _one(_drive_skipped_unbound(vocabulary), "motion_lighting")
    assert record.outcome is Outcome.SKIPPED_UNBOUND_SLOT
    assert record.rule is None
    assert [entry.slot for entry in _readings(record, SlotRead)] == ["motion_sensor"]
    assert record.commands == ()


def test_a_fresh_house_runs_nothing(vocabulary: Vocabulary) -> None:
    """Every unit is skipped as disabled, and no record names a rule.

    A falsifying implementation that defaulted a unit on -- or that resolved the
    enable flag from a layer holding `True` for a key nobody set -- would run a
    behaviour on a house whose owner never opted in, which is the product rule
    this phase exists to enforce.
    """
    records = _drive_skipped_disabled(vocabulary)
    assert {record.outcome for record in records} == {Outcome.SKIPPED_DISABLED}
    assert all(record.rule is None for record in records)
    assert {record.actor for record in records} == {
        "away_shutdown",
        "motion_lighting",
        "override",
        "safety_alert",
    }


def test_a_disabled_unit_records_the_flag_it_read(vocabulary: Vocabulary) -> None:
    """The skip carries the resolved setting, so the reason is answerable.

    A falsifying implementation that recorded only the outcome would leave a
    house owner asking "why is this not running" with a record that says it is
    not and not where the answer came from.
    """
    record = _one(_drive_skipped_disabled(vocabulary), "motion_lighting")
    settings = _readings(record, ResolvedSetting)
    assert [entry.key for entry in settings] == [enable_key("motion_lighting")]
    assert settings[0].value is False


def test_the_enable_flag_is_resolved_per_room(vocabulary: Vocabulary) -> None:
    """A room-scoped unit enabled for one room runs there and is skipped elsewhere.

    A falsifying implementation that resolved the flag at house scope for a
    room-scoped unit would either run everywhere or nowhere, and the per-room
    settings page `design.md` describes would have nothing behind it. The state
    is the load-bearing assertion: only one of the two lights moved.
    """
    layout = {
        "kitchen": dict(KITCHEN),
        "hall": {
            "motion_sensor": "binary_sensor.hall_motion",
            "light_group": "light.hall",
        },
    }
    engine, adapter, _ = _build(
        vocabulary,
        settings=dict.fromkeys(_ALL_ENABLED, False),
        room_settings={"kitchen": {enable_key("motion_lighting"): True}},
        layout=layout,
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    adapter.actuate("binary_sensor.hall_motion", "on", context=ChangeContext.world())
    outcomes = [record.outcome for record in _of(engine.tick(), "motion_lighting")]
    assert sorted(str(outcome) for outcome in outcomes) == sorted(
        [str(Outcome.ACTED), str(Outcome.SKIPPED_DISABLED)]
    )
    assert adapter.read_entity("light.kitchen").state == "on"
    assert adapter.read_entity("light.hall").state == "off"


# --------------------------------------------------------------------------
# The tick as a function
# --------------------------------------------------------------------------


def test_one_record_per_evaluation_and_no_more(vocabulary: Vocabulary) -> None:
    """Two rooms and four units give six records, however each one went.

    A falsifying implementation that appended only when something happened would
    return fewer, and the number would depend on the house's state rather than on
    its shape -- so a scenario could not assert a count, and a behaviour that
    silently stopped running would be invisible.
    """
    engine, _, _ = _build(
        vocabulary,
        layout={
            "kitchen": dict(KITCHEN),
            "hall": {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
            },
        },
    )
    records = engine.tick()
    assert len(records) == 2 * 2 + 2
    assert [record.actor for record in records] == [
        "away_shutdown",
        "motion_lighting",
        "motion_lighting",
        "override",
        "override",
        "safety_alert",
    ]


def test_a_room_scoped_unit_visits_the_house_s_rooms_in_order(
    vocabulary: Vocabulary,
) -> None:
    """The records of a room-scoped unit follow the document's room order.

    A falsifying implementation that iterated a mapping keyed by room id -- or a
    set -- would order the records alphabetically whatever the document said, and
    a scenario asserting the log's sequence would be asserting an accident of the
    ids rather than the fixture's order.
    """
    engine, _, _ = _build(
        vocabulary,
        layout={
            "study": {
                "motion_sensor": "binary_sensor.study_motion",
                "light_group": "light.study",
            },
            "attic": {
                "motion_sensor": "binary_sensor.attic_motion",
                "light_group": "light.attic",
            },
        },
    )
    lights = [
        entry.entities[0]
        for record in _of(engine.tick(), "motion_lighting")
        for entry in _readings(record, SlotRead)
        if entry.slot == "light_group"
    ]
    assert lights == ["light.study", "light.attic"]


def test_advancing_by_zero_takes_no_tick(vocabulary: Vocabulary) -> None:
    """A zero advance records nothing and moves nothing.

    A falsifying implementation that ticked anyway would append a record for an
    instant that did not pass, and a scenario's `advance: 0` step would silently
    become an evaluation.
    """
    engine, _, _ = _build(vocabulary)
    before = engine.now()
    assert engine.advance(timedelta(0)) == ()
    assert engine.now() == before
    assert len(engine.log) == 0


def test_advancing_by_a_delta_moves_the_clock_and_times_the_records(
    vocabulary: Vocabulary,
) -> None:
    """The records of a moved tick carry the instant the clock moved to.

    A falsifying implementation that recorded the instant the tick *started*
    would put every record one step behind the state it describes, and a scenario
    asserting a decision log's times would be off by its own advances.
    """
    engine, _, _ = _build(vocabulary)
    records = engine.advance(timedelta(minutes=5))
    assert records
    assert {record.at for record in records} == {NIGHT + timedelta(minutes=5)}


def test_two_runs_of_one_scenario_append_identical_records(
    vocabulary: Vocabulary,
) -> None:
    """The tick is a pure function of the state it starts from and the clock.

    A falsifying implementation that read a wall clock, iterated an unordered
    collection or consulted a module-level accumulator would produce a different
    log for the second run, and nothing else in the suite would notice because
    each run would be internally consistent.
    """

    def run() -> list[dict[str, object]]:
        engine, adapter, clock = _build(vocabulary)
        engine.modes.activate("away")
        documents: list[dict[str, object]] = []
        for step in range(6):
            adapter.actuate(
                "binary_sensor.kitchen_motion",
                "on" if step % 2 else "off",
                context=ChangeContext.world(),
            )
            documents.extend(record.to_document() for record in engine.tick())
            clock.advance(timedelta(minutes=4))
        return documents

    assert run() == run()


# --------------------------------------------------------------------------
# The engine's half of the snapshot
# --------------------------------------------------------------------------


def test_the_snapshot_carries_exactly_the_declared_fields(
    vocabulary: Vocabulary,
) -> None:
    """`state()` names exactly the fields `sim/snapshot.py` enumerates.

    A falsifying implementation that omitted one would make a restore resume a
    run that had forgotten an override, a rate-limit window or a quiet timer, and
    the divergence would appear several ticks later in a decision nobody could
    trace. The expected set is read from the enumeration rather than restated, so
    a field the engine gains and the snapshot is taught to carry cannot leave this
    check behind again.
    """
    engine, _, _ = _build(vocabulary)
    assert set(engine.state()) == set(ENGINE_STATE_FIELDS)


def test_the_snapshot_is_json_safe(vocabulary: Vocabulary) -> None:
    """The engine's state serialises whole, because the snapshot document does."""
    state = _build(vocabulary)[0].state()
    assert json.loads(json.dumps(state)) == state


def test_the_snapshot_records_the_bindings_the_modes_and_the_flags(
    vocabulary: Vocabulary,
) -> None:
    """The snapshot's flags are the resolved answers, per unit and per scope.

    A falsifying implementation that snapshotted the layers instead of the
    answers -- or that answered a unit's rooms map only for room-scoped units --
    would restore a run whose rooms disagreed with the run that was saved, and the
    divergence would appear as a behaviour that stopped firing after a restore.

    The house layer answers at room scope too (`engine/config.py`'s `_value_at`
    returns the house layer's entry whatever the scope), so a room inherits the
    house's `False` unless the room itself says otherwise. That is why the kitchen
    is on for motion lighting, which the room layer enables, and off for the two
    units it does not mention -- and why every unit's rooms map is populated for
    the room the house has, house-scoped units included.
    """
    engine, _, _ = _build(
        vocabulary,
        settings=dict.fromkeys(_ALL_ENABLED, False),
        room_settings={"kitchen": {enable_key("motion_lighting"): True}},
    )
    engine.modes.activate("away")
    state = engine.state()
    assert state["bindings"] == {"kitchen": dict(KITCHEN)}
    assert state["modes"] == ["away"]
    assert state["enable_flags"] == {
        "away_shutdown": {"house": False, "rooms": {"kitchen": False}},
        "motion_lighting": {"house": False, "rooms": {"kitchen": True}},
        "override": {"house": False, "rooms": {"kitchen": False}},
        "safety_alert": {"house": False, "rooms": {"kitchen": False}},
    }


def test_a_restored_engine_decides_what_the_run_it_resumed_decided(
    vocabulary: Vocabulary,
) -> None:
    """The restore-and-replay property, end to end.

    A falsifying implementation that left any of the six fields out of the
    document -- or that rebuilt a registry from the wrong half of one -- would
    have the resumed run take a different branch, and the difference would
    surface as a decision rather than as an error. The fake carries no
    restore-from-snapshot door (`simulation`: the fake implements the port and
    nothing more), so the entities are rebuilt through the port's own
    `add_entity`; the scenario therefore contains no user touch, because
    `last_origin` is the one entity field the port cannot be asked to restore and
    a user origin is the one thing it would decide.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    engine.tick()
    state = engine.state()

    restored, _, _ = _build(
        vocabulary,
        at=clock.now,
        entities={
            entity_id: adapter.read_entity(entity_id).state
            for entity_id in adapter.list_entities()
        },
        state=state,
    )
    first = [record.to_document() for record in engine.advance(timedelta(minutes=10))]
    second = [
        record.to_document() for record in restored.advance(timedelta(minutes=10))
    ]
    assert first == second
    assert first
    assert engine.state() == restored.state()


def test_a_snapshot_restored_onto_a_different_house_is_refused(
    vocabulary: Vocabulary,
) -> None:
    """The bindings are checked, so a mismatched restore fails at the seam.

    A falsifying implementation that read the bindings and ignored them would let
    a snapshot taken from one house resume over another, and the run would quietly
    decide from bindings the snapshot never described.
    """
    state = _build(vocabulary)[0].state()
    with pytest.raises(EngineError):
        _build(
            vocabulary,
            layout={
                "kitchen": {
                    "motion_sensor": "binary_sensor.other_motion",
                    "light_group": "light.other",
                }
            },
            state=state,
        )


def test_a_snapshot_enabling_an_unknown_behaviour_is_refused(
    vocabulary: Vocabulary,
) -> None:
    """A flag for a unit this build does not register fails rather than being ignored.

    A falsifying implementation that skipped the unknown key would restore a run
    whose configuration named a behaviour that is not running, and the house
    owner would be told a unit was enabled that no record ever names.
    """
    state = dict(_build(vocabulary)[0].state())
    state["enable_flags"] = {"ghost": {"house": True, "rooms": {}}}
    with pytest.raises(EngineError):
        _build(vocabulary, state=state)


def test_the_log_is_bounded_and_keeps_the_newest_records(
    vocabulary: Vocabulary,
) -> None:
    """The bound is a setting, and the log drops its oldest records past it.

    A falsifying implementation with an unbounded log would grow for the length
    of a run, and the window the control surface exposes would scan a list whose
    size depends on how long the house has been up.
    """
    engine, _, _ = _build(vocabulary, settings={"engine.decision_log.bound": 3})
    assert engine.log.bound == 3
    for _ in range(5):
        engine.tick()
    assert len(engine.log) == 3
    assert [record.at for record in engine.log.window(1)] == [NIGHT]


# --------------------------------------------------------------------------
# What a behaviour decides, as the record shows it
# --------------------------------------------------------------------------


def test_the_dark_test_falls_back_to_the_sun_when_no_ambient_light_sensor_is_bound(
    vocabulary: Vocabulary,
) -> None:
    """With no lux slot bound, the sun decides and the record says so.

    A falsifying implementation that read the lux slot anyway would take `ANY`
    over no members as false, conclude the room was dark at noon, and light it --
    with no input saying the branch was the sun's.
    """
    record = _one(_drive_acted(vocabulary), "motion_lighting")
    readings = _readings(record, DarkSourceReading)
    assert [reading.source for reading in readings] == [DarkSource.SUN]
    assert readings[0].value < 0.0


def test_a_bound_ambient_light_sensor_decides_the_dark_test(
    vocabulary: Vocabulary,
) -> None:
    """A bound, answering lux sensor is the branch that decides, and it is recorded.

    A falsifying implementation that consulted the sun whenever it could would
    make the optional slot decorative: a house that bound a lux sensor would
    still light its rooms on the sun's schedule, and the record would name the
    branch nobody configured.
    """
    engine, _, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "ambient_light_sensor": "sensor.kitchen_lux"}},
    )
    record = _one(engine.tick(), "motion_lighting")
    readings = _readings(record, DarkSourceReading)
    assert [reading.source for reading in readings] == []

    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "ambient_light_sensor": "sensor.kitchen_lux"}},
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    readings = _readings(_one(engine.tick(), "motion_lighting"), DarkSourceReading)
    assert [reading.source for reading in readings] == [DarkSource.LUX]
    assert readings[0].value == 5.0


def test_a_bright_lux_reading_declines_rather_than_lighting(
    vocabulary: Vocabulary,
) -> None:
    """A lux reading above the threshold is a decline, not a command.

    A falsifying implementation that compared the threshold backwards would light
    a room that is already visible, which is the commonest way for a motion light
    to be wrong and the one the corpus's `room_light_dim` row exists to bound.
    """
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "ambient_light_sensor": "sensor.kitchen_lux"}},
        entities={"sensor.kitchen_lux": "100"},
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    record = _one(engine.tick(), "motion_lighting")
    assert record.outcome is Outcome.DECLINED
    assert record.state_delta == ()


def test_an_unavailable_ambient_light_sensor_falls_back_to_the_sun(
    vocabulary: Vocabulary,
) -> None:
    """A bound sensor that cannot answer does not decide the branch.

    A falsifying implementation that read the state anyway would see an
    `unavailable` reading, fail to parse it as a number, and either raise or
    treat it as dark. `catalog/edge_cases.yaml` names the dead sensor, and the
    readable answer is to take the branch that has a value and say which it was.
    """
    engine, adapter, _ = _build(
        vocabulary,
        layout={"kitchen": {**KITCHEN, "ambient_light_sensor": "sensor.kitchen_lux"}},
    )
    adapter.set_availability(
        "sensor.kitchen_lux", available=False, context=ChangeContext.world()
    )
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    readings = _readings(_one(engine.tick(), "motion_lighting"), DarkSourceReading)
    assert [reading.source for reading in readings] == [DarkSource.SUN]


def test_the_dark_test_follows_the_virtual_clock_across_a_run(
    vocabulary: Vocabulary,
) -> None:
    """One house, unchanged, is dark at night and visible at noon.

    The sun branch is a function of the virtual clock and of nothing else
    (`solar`: the same instant always gives the same elevation), so a run that
    advances from 22:00 to the next noon must change its answer while no device,
    mode or seed changes. A falsifying implementation that read the wall clock --
    or that cached the first elevation and reused it -- would keep the house dark
    at noon, and every other test in this module uses a fixed instant, so nothing
    else here would notice. The light is returned by the *world* and not by a
    person, so the second decision is the sun's and not an override's.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())

    night = _one(engine.tick(), "motion_lighting")
    assert night.outcome is Outcome.ACTED
    assert _readings(night, DarkSourceReading)[0].value < 0.0

    adapter.actuate("light.kitchen", "off", context=ChangeContext.world())
    clock.advance(timedelta(hours=14))
    noon = _one(engine.tick(), "motion_lighting")
    assert noon.outcome is Outcome.DECLINED
    assert noon.state_delta == ()
    assert _readings(noon, DarkSourceReading)[0].value > 0.0


def test_the_light_goes_off_once_the_room_has_been_quiet_for_the_timeout(
    vocabulary: Vocabulary,
) -> None:
    """Motion, then stillness: the off half fires exactly at the quiet timeout.

    A falsifying implementation that owned its own timer -- or that measured the
    quiet period from the first clear observation rather than from the last
    motion -- would turn the light off a tick early or late, and the scenario
    "advance by exactly the timeout" would be the one that almost works.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.ACTED
    adapter.actuate(
        "binary_sensor.kitchen_motion", "off", context=ChangeContext.world()
    )

    clock.advance(timedelta(seconds=299))
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.DECLINED

    clock.advance(timedelta(seconds=1))
    record = _one(engine.tick(), "motion_lighting")
    assert record.outcome is Outcome.ACTED
    assert record.rule == "lighting.motion_light_off"
    assert adapter.read_entity("light.kitchen").state == "off"


def test_the_shutdown_gates_on_away_mode_and_reports_the_gate(
    vocabulary: Vocabulary,
) -> None:
    """Not in away mode, the shutdown declines and the record names the mode gate.

    A falsifying implementation that read the mode off an entity rather than
    from the mode set would let a stale `input_select` disagree with every other
    behaviour's gate, and the record would name an entity rather than the mode
    the house was supposed to be in.
    """
    engine, _, _ = _build(vocabulary, entities={"light.kitchen": "on"})
    record = _one(engine.tick(), "away_shutdown")
    assert record.outcome is Outcome.DECLINED
    assert [(entry.mode, entry.active) for entry in _readings(record, ModeReading)] == [
        ("away", False)
    ]


def test_the_shutdown_declines_a_house_that_still_has_somebody_in_it(
    vocabulary: Vocabulary,
) -> None:
    """Away mode without an empty house is a state the shutdown does not act on.

    A falsifying implementation that gated on the mode alone would darken a house
    the moment away was set, and the first thing the person still inside would
    have to do is undo the automation -- the failure the two-gate rule exists to
    prevent.
    """
    engine, adapter, _ = _build(vocabulary, entities={"light.kitchen": "on"})
    engine.modes.activate("away")
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    record = _one(engine.tick(), "away_shutdown")
    assert record.outcome is Outcome.DECLINED
    assert [entry.empty for entry in _readings(record, HousePresence)] == [False]


def test_the_shutdown_names_the_rooms_its_emptiness_claim_was_made_from(
    vocabulary: Vocabulary,
) -> None:
    """The presence input carries the quiet rooms, so a surprise is traceable.

    A falsifying implementation that recorded only `empty: true` would leave a
    shutdown nobody can explain -- the house emptied, but from which rooms, and
    which sensor never tripped -- and `presence.house_emptied` is a derived fact
    that has to show its work.
    """
    engine, _, clock = _build(vocabulary, entities={"light.kitchen": "on"})
    engine.modes.activate("away")
    engine.tick()
    clock.advance(timedelta(minutes=10))
    record = _one(engine.tick(), "away_shutdown")
    assert [
        (entry.empty, entry.quiet_rooms) for entry in _readings(record, HousePresence)
    ] == [(True, ("kitchen",))]


def test_the_shutdown_declines_a_house_that_is_already_dark(
    vocabulary: Vocabulary,
) -> None:
    """Nothing to shut down is a decline, not an acted record with an empty delta.

    A falsifying implementation that proposed `off` anyway would append an
    `acted` record whose `state_delta` was empty, and a scenario asserting that
    the shutdown did something could not tell it from the shutdown firing on a
    house with nothing to shut down.
    """
    engine, _, clock = _build(vocabulary)
    engine.modes.activate("away")
    engine.tick()
    clock.advance(timedelta(minutes=10))
    record = _one(engine.tick(), "away_shutdown")
    assert record.outcome is Outcome.DECLINED
    assert record.state_delta == ()


def test_a_house_scoped_slot_gathers_every_room_that_binds_it(
    vocabulary: Vocabulary,
) -> None:
    """One command to a house-scope slot writes every room's light, and says so.

    A falsifying implementation that resolved a house-scope slot from the first
    room that bound it would shut down one room and leave the rest lit, and
    because the command's entity list is what arbitration contends over, the
    omission would also make the record name a single light for a house-wide act.
    """
    engine, adapter, clock = _build(
        vocabulary,
        layout={
            "kitchen": dict(KITCHEN),
            "hall": {
                "motion_sensor": "binary_sensor.hall_motion",
                "light_group": "light.hall",
            },
        },
        entities={"light.kitchen": "on", "light.hall": "on"},
    )
    engine.modes.activate("away")
    engine.tick()
    clock.advance(timedelta(minutes=10))
    record = _one(engine.tick(), "away_shutdown")
    assert record.outcome is Outcome.ACTED
    assert [change.entity_id for change in record.state_delta] == [
        "light.hall",
        "light.kitchen",
    ]
    assert adapter.read_entity("light.hall").state == "off"
    assert adapter.read_entity("light.kitchen").state == "off"


def test_the_override_unit_registers_and_proposes_nothing(
    vocabulary: Vocabulary,
) -> None:
    """The unit that stands the engine down never commands anything itself.

    A falsifying implementation that proposed a command from the override unit
    would give it a proposal to arbitrate, and the suppression it exists to
    explain would appear in the log as a competing behaviour rather than as the
    absence of one.
    """
    engine, adapter, _ = _build(vocabulary)
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    assert _one(engine.tick(), "override").commands == ()
    assert engine.overrides.is_overridden("light.kitchen")


def test_only_a_user_origin_arms_an_override(vocabulary: Vocabulary) -> None:
    """An engine, world or fault write to the light leaves the behaviour running.

    A falsifying implementation that overrode on any write would have the engine
    override itself the first time it acted -- every behaviour would fire once
    and then be suppressed by its own actuation -- and a sensor updating would
    lock the room out of automation.
    """
    for context in (
        ChangeContext.engine(),
        ChangeContext.world(),
        ChangeContext.fault(),
    ):
        engine, adapter, _ = _build(vocabulary)
        adapter.actuate("light.kitchen", "off", context=context)
        adapter.actuate(
            "binary_sensor.kitchen_motion", "on", context=ChangeContext.world()
        )
        assert _one(engine.tick(), "motion_lighting").outcome is Outcome.ACTED
        assert not engine.overrides.is_overridden("light.kitchen")


def test_the_condition_that_ended_an_override_appears_where_acting_resumes(
    vocabulary: Vocabulary,
) -> None:
    """A released override is named in the record of the evaluation that resumed acting.

    A falsifying implementation that reported the release only on the override
    unit's own record would tell a reader that a suppression ended without
    telling them what then happened, and `engine-core` requires the two facts in
    one record. The renewal hole is exercised here too: the light's last writer
    is still the user, so a registry that re-armed on the same origin would
    suppress the act this test is looking for.
    """
    engine, adapter, clock = _build(vocabulary)
    adapter.actuate("light.kitchen", "off", context=ChangeContext.user())
    adapter.actuate("binary_sensor.kitchen_motion", "on", context=ChangeContext.world())
    assert _one(engine.tick(), "motion_lighting").outcome is Outcome.OVERRIDDEN

    clock.advance(timedelta(hours=2))
    record = _one(engine.tick(), "motion_lighting")
    assert record.outcome is Outcome.ACTED
    assert [
        entry.released
        for entry in _readings(record, OverrideNote)
        if entry.released is not None
    ] == [ResetCondition.OVERRIDE_TIMEOUT]
    assert engine.overrides.records() == ()
