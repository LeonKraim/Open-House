"""The values beside a clause's kind -- `pack-manifest/1.3.0`'s `match` and `mode`.

`1.2.0` fixed a behaviour's clause to `name, trigger, condition, action,
priority, services, slots` and every one of those named a *kind* with no value
beside it, so a manifest could say `condition: state` and could not say which
state, could say `action: service` and could not say which mode to enter.
`1.3.0` adds the two values, and this module is where they are exercised end to
end -- through `open_session` and `install_pack`, so what is tested is the
decision the engine makes rather than a field the builder stored.

The house is the `minimal` fixture, whose `light_group` is bound in both of its
two rooms, and a declared behaviour is room-scoped unless it says otherwise --
so **one behaviour leaves one record per room**. That is not incidental: the two
properties below are about what a *room*'s evaluation decides, and the fixture's
second room is what turns "the clause is per-device" from a claim into something
a test can fail on.

Three facts, one per thing the clauses have to get right:

- **`match` gates the proposal, not the evaluation.** A behaviour whose clause
  does not match is still evaluated, still reaches the slot, still reads it, and
  declines -- which is `engine/declared_slots.py`'s "the pack reached for
  nothing" against "the pack never reached": the record has to show the device
  that did not match.
- **`mode` reaches the house, and reaches it as an ask.** The engine applies the
  modes its evaluations asked for *after* arbitrating their commands, so a tick
  that turns a lamp off and enters a mode does both, in an order that does not
  depend on which behaviour was evaluated first.
- **A mode the house does not declare is refused, not raised.** A manifest does
  not know which house it lands in, so the name meets a house for the first time
  at evaluation; a tick that raised there would be a hand edit taking the engine
  down.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from engine.behaviours import enable_key, module_enable_key
from engine.behaviours.declared import option_key, reach_key
from engine.binding import RoomScope
from engine.decision_log import DecisionRecord, ModeRequest, Outcome
from engine.vocabulary import Vocabulary
from openhouse.facade import OpenHouse, open_session
from tools.catalog import paths

from .packfactory import SLOT, pack

ROOT = paths.ROOT

#: The one behaviour a generated pack's row is called, from `packfactory`'s
#: `b{index}` naming, and so the half of a unit id the fixture's pack supplies.
#: Named once because the enable key and the record's actor are both built from
#: it, and an actor the enable flag disagrees about is a silent behaviour.
UNIT = "b0"

#: The manifest text the two `match` tests splice in. The value is quoted, and
#: that quotation is load-bearing rather than tidy: YAML 1.1 reads a bare `on` as
#: the boolean `true`, so a pack that wrote `match: [on]` would be a pack whose
#: clause is `[True]` -- and the schema's answer to it ("True is not of type
#: 'string'") names neither the clause nor the file. A pack author's `on`/`off`
#: states are strings and have to be written as ones, which is why the fixture
#: spells the manifest line here rather than hiding it behind a helper.
MATCH_ON = f'    slots: [{SLOT}]\n    match: ["on"]'

#: The `options` block the `for` tests splice in, anchored on the pack
#: description's line. Ninety seconds rather than one minute, so that a tick is
#: shorter than the grace: the point of the clause is that the behaviour waits,
#: and a grace the very first advance already satisfies would test nothing.
OPTIONS_GRACE = (
    "description: a pack for the test\n"
    "options:\n"
    "  - key: grace\n"
    "    type: duration\n"
    "    default: 90\n"
    "    title: Held for\n"
    "    description: How long the reading must hold.\n"
    "    unit: seconds\n"
)

#: The same slot line, plus both values: the reading must be `on` and must have
#: been so for the `grace` option's seconds.
MATCH_ON_FOR_GRACE = f'    slots: [{SLOT}]\n    match: ["on"]\n    for: grace'


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


def _settings(name: str) -> dict[str, object]:
    """The two flags a pack's single behaviour needs before it proposes.

    The module flag is the family gate (`module_enable_key(name)` -- a pack is
    its own family) and the unit's own is `enable_key`, so one pack needs both
    and a session opened with only one would have it silent.
    """
    return {
        module_enable_key(name): True,
        enable_key(f"{name}.{UNIT}"): True,
    }


def _session(
    tmp_path: Path,
    vocabulary: Vocabulary,
    *,
    name: str,
    services: tuple[str, ...],
    edits: tuple[tuple[str, str], ...],
    light: str,
) -> tuple[OpenHouse, tuple[str, ...]]:
    """A session with one pack installed, its lights set to `light`, and its slot.

    Returns the session and the entities the pack's slot resolved to, taken from
    the install's own result, so a test writes to the devices the pack actually
    holds rather than to the ones the fixture happens to name. `light_group` is a
    house-scope slot of `minimal`, so that is both rooms' lamps and every one of
    them is written: the clause under test reads one room's binding at a time, so
    a test that set one lamp would be a test of one room's half of the property.
    """
    session = open_session(
        house="minimal", vocabulary=vocabulary, house_settings=_settings(name)
    )
    result = session.install_pack(
        str(pack(tmp_path, name, services=services, edits=edits))
    )
    entities = tuple(str(entity) for entity in result["slots"][SLOT])
    for entity in entities:
        session.set_state(entity, light)
    return session, entities


def _tick(session: OpenHouse, name: str) -> tuple[DecisionRecord, ...]:
    """One tick's records for the pack's one behaviour, which is one per room."""
    return tuple(
        record
        for record in session.advance_time(minutes=1)
        if record.actor == f"{name}.{UNIT}"
    )


# -- `match`: the readings a behaviour acts on --------------------------------


def test_a_matching_reading_lets_the_behaviour_act(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Both lamps read `on`; a behaviour matching `on` turns both off.

    Falsified by a clause that gates on nothing: the states it proposes are
    `off`, so a tick that ignored the reading would write the same two changes
    to a pair of lamps that were already off -- which is why the *reading* is
    what this sets rather than the outcome.
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="matching",
        services=("light.turn_off",),
        edits=(("    slots: [light_group]", MATCH_ON),),
        light="on",
    )
    records = _tick(session, "matching")

    assert {
        change.entity_id for record in records for change in record.state_delta
    } == set(entities)
    assert {command.action for record in records for command in record.commands} == {
        "off"
    }
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


def test_a_reading_the_clause_does_not_match_declines_and_names_the_read(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Both lamps read `off`; a behaviour matching `on` is left alone and says why.

    The distinction this asserts is the one the clause is easy to get wrong
    about: the behaviour is *evaluated*, reaches its room's slot and reads it,
    and declines -- it is not skipped. A record carrying no read would say the
    pack never reached, which is a different fact about a different defect, and
    the read is asserted by its slot name so the record is shown to be about the
    lamp and not about something else.
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="not_matching",
        services=("light.turn_off",),
        edits=(("    slots: [light_group]", MATCH_ON),),
        light="off",
    )
    records = _tick(session, "not_matching")

    assert {record.outcome for record in records} == {Outcome.DECLINED}
    assert not [change for record in records for change in record.state_delta]
    assert {
        entry.slot
        for record in records
        for entry in record.inputs
        if hasattr(entry, "slot")
    } == {SLOT}
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


def test_a_behaviour_with_no_match_clause_acts_on_any_reading(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Silence is not `match: []` -- a pack that states nothing acts as it always did.

    The regression this pins is the one every 1.2.0 pack would have hit: reading
    an absent `match` as "matches nothing" would make `match` an opt-out of every
    pack that never heard of it, and this behaviour is one of them.
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="unmatched",
        services=("light.turn_off",),
        edits=(),
        light="on",
    )
    records = _tick(session, "unmatched")

    assert {command.action for record in records for command in record.commands} == {
        "off"
    }
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


# -- `mode`: the mode a behaviour enters --------------------------------------


def _modes(records: Sequence[DecisionRecord]) -> set[str]:
    """Every mode request the records carry, `!`-suffixed where the house refused it.

    A set rather than a tuple because the request is made once per room and the
    property is about *which* modes were asked for, not how many times the
    question was asked.
    """
    return {
        entry.mode if entry.taken else f"{entry.mode}!"
        for record in records
        for entry in record.inputs
        if isinstance(entry, ModeRequest)
    }


def test_a_declared_mode_is_entered_when_the_behaviour_acts(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A behaviour carrying `mode: away` puts the house in `away`, and says so.

    The mode is one `DEFAULT_MODES` declares, because the house is the fixture's
    and the fixture carries those two. The request is asserted through the record
    as well as through the mode set: the mode set says the house moved and the
    record says which behaviour asked it to. The legs on the lamps are the other
    half -- a mode is an act *beside* the command, so both happened in the tick.
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="entering",
        services=("light.turn_off",),
        edits=(("    slots: [light_group]", f"    slots: [{SLOT}]\n    mode: away"),),
        light="on",
    )
    records = _tick(session, "entering")

    assert _modes(records) == {"away"}
    assert "away" in session.engine.modes.active
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


def test_a_mode_the_house_does_not_declare_is_refused_and_not_raised(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """`mode: sleep` in a house declaring only `home` and `away` changes no mode.

    A manifest does not know which house it lands in, so the name meets one for
    the first time here; refusing it is what keeps a pack installed into a house
    whose modes changed under it from taking a tick down. The rest of the
    behaviour still runs -- a mode is an ask beside the act, not a precondition
    for it -- which is the second half of what this asserts, and the modes in
    force before the tick are compared rather than assumed to be none, because
    the house is the fixture's and the fixture decides.
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="unknown_mode",
        services=("light.turn_off",),
        edits=(("    slots: [light_group]", f"    slots: [{SLOT}]\n    mode: sleep"),),
        light="on",
    )
    before = session.engine.modes.active
    records = _tick(session, "unknown_mode")

    assert _modes(records) == {"sleep!"}
    assert session.engine.modes.active == before
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


# -- `for`: how long the reading has to have held -----------------------------


def _grace_session(
    tmp_path: Path, vocabulary: Vocabulary, *, name: str
) -> tuple[OpenHouse, tuple[str, ...]]:
    """A session whose one behaviour waits for its declared `grace` option."""
    return _session(
        tmp_path,
        vocabulary,
        name=name,
        services=("light.turn_off",),
        edits=(
            ("description: a pack for the test", OPTIONS_GRACE),
            ("    slots: [light_group]", MATCH_ON_FOR_GRACE),
        ),
        light="on",
    )


def test_a_for_clause_waits_for_the_declared_duration(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The lamps read `on` and the behaviour waits out its ninety-second grace.

    Three advances, and each is a different fact about the clause. The first
    *observes* the reading, which is what starts the clock -- a tick that only
    compared the current reading with the option would have nothing to compare
    the duration against and would either fire at once or never. The second is
    still inside the grace, so it declines, which is the half that makes `for`
    a wait rather than a switch. The third is past it, so the lamps go off.

    The reading is set before the first advance and never touched again, so the
    hold is the only thing the second and third advances change: a test that
    re-wrote the state each tick would reset the duration it meant to measure.
    """
    session, entities = _grace_session(tmp_path, vocabulary, name="waiting")

    first = _tick(session, "waiting")
    assert {record.outcome for record in first} == {Outcome.DECLINED}
    assert {session.read_entity(entity).state for entity in entities} == {"on"}

    second = _tick(session, "waiting")
    assert {record.outcome for record in second} == {Outcome.DECLINED}
    assert {session.read_entity(entity).state for entity in entities} == {"on"}

    third = _tick(session, "waiting")
    assert {command.action for record in third for command in record.commands} == {
        "off"
    }
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


def test_a_change_of_reading_restarts_the_for_clause(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A door that opens, shuts and opens again is not a door that stayed open.

    The clause is about a reading *holding*, so a tick that saw the device change
    away from the matched reading and back has to start over -- otherwise a
    contact that flickered once an hour would accumulate a hold it never had and
    the alarm would fire on a fridge that was never left open. The reset is
    asserted through the record's outcomes rather than through the timer, because
    the timer is this module's internal state and the declining behaviour is what
    a person would see.
    """
    session, entities = _grace_session(tmp_path, vocabulary, name="flickering")

    assert {record.outcome for record in _tick(session, "flickering")} == {
        Outcome.DECLINED
    }
    assert {record.outcome for record in _tick(session, "flickering")} == {
        Outcome.DECLINED
    }

    # Away from the matched reading and straight back: the hold is now zero
    # seconds old, whatever the two advances before it had accumulated. Every
    # entity of the slot is flipped and not just the first, because the slot is
    # house-scope in this fixture and a room-by-room flip would leave the other
    # room's reading holding -- which is a real fact about the engine and not
    # what this test is measuring.
    for entity in entities:
        session.set_state(entity, "off")
    _tick(session, "flickering")
    for entity in entities:
        session.set_state(entity, "on")

    restarted = _tick(session, "flickering")
    assert {record.outcome for record in restarted} == {Outcome.DECLINED}


# -- the role a module addresses ----------------------------------------------


def test_a_role_left_unticked_is_not_addressed(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The module runs, reads its device, and writes nothing to the role.

    The setting the product asks for beside the scope: a bedtime button that
    shuts the house's doors but must leave the lamps alone is one pack with
    `reach.light_group` unticked, and the two halves of what this asserts are
    that it declines rather than silently doing nothing, and that it still
    *reached* -- the record names the slot it would have written to, so a person
    reading the activity log sees "the pack got there and the role was off"
    rather than "the pack never fired".
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="unticking",
        services=("light.turn_off",),
        edits=(),
        light="on",
    )
    # Every room, because the behaviour leaves one record per room and the
    # assertion below is about all of them: one unticked room beside a ticked one
    # is the next test's case, not this one's.
    key = option_key("unticking", reach_key(SLOT))
    for room in session.house.rooms:
        session.engine.settings.set_override(key, RoomScope(room.id), False)
    records = _tick(session, "unticking")

    assert {record.outcome for record in records} == {Outcome.DECLINED}
    assert {session.read_entity(entity).state for entity in entities} == {"on"}


def test_a_role_nobody_has_answered_for_is_still_addressed(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Silence is not a refusal, and that is what keeps old packs behaving.

    Every pack installed before this setting existed has no `reach` value at all,
    and reading that absence as "leave every role alone" would turn a setting
    into an off switch for the whole house. The assertion is on the write
    happening, because "acts as it always did" is only true if the lamps move.
    """
    session, entities = _session(
        tmp_path,
        vocabulary,
        name="unasked",
        services=("light.turn_off",),
        edits=(),
        light="on",
    )
    records = _tick(session, "unasked")

    assert {command.action for record in records for command in record.commands} == {
        "off"
    }
    assert {session.read_entity(entity).state for entity in entities} == {"off"}


def test_a_role_set_for_one_room_leaves_the_other_room_alone(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """One room's answer to "do you address the lights" is that room's alone.

    `light_group` is house-scope in this fixture, so both rooms bind lamps and one
    behaviour leaves one record per room. The option resolves at the room's scope
    (`live_profiles.set_option`), which is what makes the same pack in two rooms
    two households' answers rather than one house-wide switch: the unticked room
    stops writing and its neighbour does not.
    """
    session, _entities = _session(
        tmp_path,
        vocabulary,
        name="per_room",
        services=("light.turn_off",),
        edits=(),
        light="on",
    )
    rooms = tuple(room for room in session.house.rooms)
    assert len(rooms) == 2, "the fixture is the two-room house this asserts about"
    unticked, other = sorted(rooms, key=lambda room: room.id)
    session.engine.settings.set_override(
        option_key("per_room", reach_key(SLOT)), RoomScope(unticked.id), False
    )
    _tick(session, "per_room")

    # The lamps are the observable, not the records: both rooms' records carry
    # the same actor and no room, so the two halves are told apart by which room's
    # device moved -- and that is the fact the setting is about.
    assert session.read_entity(unticked.bindings[SLOT]).state == "on"
    assert session.read_entity(other.bindings[SLOT]).state == "off"
