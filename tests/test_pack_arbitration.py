"""The exit criterion: two installed packs cannot fight over one light.

`engine/arbitration.py`'s unit-level rules are `tests/test_engine_arbitration.py`'s
-- a user outranks a behaviour, a higher priority wins, a tie goes to the
ascending `id`, and none of it depends on the order the proposals arrived in.
What is added here is the *end to end* half, which is the half the phase's exit
criterion is written about: two packs that arrived from two manifests, both
enabled, both proposing for the same entity in the same tick, reduced to one
write.

The engine's contribution is that it fixes the evaluation order to ascending unit
`id` (`_by_id`) and builds the ranked list from the proposals' own fields rather
than from their arrival. That makes **install order** the one remaining way the
two could decide differently -- the packs are merged into the behaviour registry
in the order they were installed -- so the property is stated over it, and stated
as the log being *identical* rather than as the winner being the same, because a
log that differed in anything would be a run that recorded a different house.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import permutations
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.behaviours import enable_key, module_enable_key
from engine.decision_log import DecisionRecord, Outcome
from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from tools.catalog import paths

from .packfactory import SLOT, pack

if TYPE_CHECKING:
    from openhouse.facade import OpenHouse

ROOT = paths.ROOT

#: The pack whose `id` sorts first, and so the one a tie goes to. Named for the
#: order rather than for a role, because "the winner" is what the tests compute
#: and naming it that would make the assertion and the expectation the same word.
FIRST = "alpha"
SECOND = "beta"


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, loaded once for the module."""
    return Vocabulary.load(ROOT)


def _settings(*names: str) -> dict[str, object]:
    """Both flags every named pack's single behaviour needs to propose.

    The module flag is resolved by `module_enable_key(name)`, whose family is the
    pack, and the unit's own is `enable_key(unit)` -- so four keys for two packs,
    and a session opened with only two of them would have one pack silent and the
    other contending with nothing.
    """
    settings: dict[str, object] = {}
    for name in names:
        settings[enable_key(f"{name}.b0")] = True
        settings[module_enable_key(name)] = True
    return settings


def _house(
    tmp_path: Path,
    vocabulary: Vocabulary,
    order: tuple[str, ...],
    *,
    priority: int | None = None,
) -> tuple[OpenHouse, tuple[str, ...]]:
    """A session with each named pack installed in `order`, all of them enabled.

    Returns the session and the entities the packs' one slot resolved to, taken
    from the install's own result rather than from the house -- so the test is
    about the entities the packs actually hold.
    """
    session = open_session(
        house="minimal", vocabulary=vocabulary, house_settings=_settings(*order)
    )
    entities: tuple[str, ...] = ()
    for name in order:
        result = session.install_pack(str(pack(tmp_path, name, priority=priority)))
        entities = tuple(result["slots"][SLOT])
    return session, entities


def _by_actor(
    records: Sequence[DecisionRecord], actor: str
) -> tuple[DecisionRecord, ...]:
    """Every record `actor` left this tick, which is one per room it is evaluated in."""
    return tuple(record for record in records if record.actor == actor)


def _writers(records: Sequence[DecisionRecord]) -> dict[str, list[str]]:
    """Every entity the tick wrote, and the actors whose commands wrote it.

    Read off `state_delta` rather than off the proposals, because a proposal that
    lost arbitration is not a write: the claim under test is about what reached
    the light, not about what was asked of it.
    """
    writers: dict[str, list[str]] = {}
    for record in records:
        for change in record.state_delta:
            writers.setdefault(change.entity_id, []).append(record.actor)
    return writers


def test_two_packs_proposing_for_one_light_reduce_to_one_command(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Two enabled packs wanting one entity leave exactly one command on it.

    Falsified by a tick that applied every proposal: the light would receive both
    `light.turn_on` and whatever the other pack asked for, with the winner decided
    by nothing a person could point at. The loser is asserted as well as the
    winner, because "one command" is a claim about both -- a tick that wrote once
    and recorded no loser would be a tick where the second pack never proposed,
    which is the gate being shut rather than contention being settled.

    The priority is huge rather than merely higher, so the winner is a fact about
    the packs and not a comparison against whatever the fixture's own behaviours
    resolve to.
    """
    session, entities = _house(tmp_path, vocabulary, (FIRST, SECOND), priority=999_999)
    records = session.advance_time(minutes=1)

    assert entities != ()
    writers = _writers(records)
    for entity in entities:
        assert writers.get(entity) == [f"{FIRST}.b0"], entity

    winner = _by_actor(records, f"{FIRST}.b0")
    assert {record.outcome for record in winner} == {Outcome.ACTED}
    assert {
        change.entity_id for record in winner for change in record.state_delta
    } == set(entities)

    loser = _by_actor(records, f"{SECOND}.b0")
    assert loser != ()
    assert {record.outcome for record in loser} == {Outcome.LOST_ARBITRATION}
    assert [change for record in loser for change in record.state_delta] == []


def test_a_higher_priority_wins_over_the_id_that_sorts_first(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The declared priority outranks the tie-break, not the other way round.

    Falsified by an arbitration that ranked by `id` first: the `alpha` pack would
    win every contention whatever either manifest declared, and a pack's own
    `priority` clause would be a comment. The two packs are otherwise identical,
    so the only field that differs is the one under test -- and it differs by
    being stated on the one whose `id` sorts *second*.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings=_settings(FIRST, SECOND),
    )
    session.install_pack(str(pack(tmp_path, FIRST, priority=0)))
    result = session.install_pack(str(pack(tmp_path, SECOND, priority=999_999)))
    entities = tuple(result["slots"][SLOT])

    writers = _writers(session.advance_time(minutes=1))
    assert {tuple(writers.get(entity, ())) for entity in entities} == {
        (f"{SECOND}.b0",)
    }


def test_the_same_two_packs_decide_identically_in_either_install_order(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The install order is not an input: both orders produce the same log.

    Falsified by anything that read the order the packs were merged into the
    behaviour registry -- a registry iterated as built, a loser list assembled in
    arrival order, a draft list that was not sorted. The whole log is compared
    rather than the winner, because a field that differed anywhere in it is a run
    that recorded a different house, and the exit criterion is about the run and
    not about the light.

    Two ticks are driven, so the comparison covers a second evaluation of the same
    house -- where a rate limit or an override left behind by the first would have
    to differ too if it depended on the order.
    """
    logged: list[tuple[DecisionRecord, ...]] = []
    for order in permutations((FIRST, SECOND)):
        session, _ = _house(tmp_path, vocabulary, order)
        session.advance_time(minutes=1)
        session.advance_time(minutes=1)
        logged.append(session.get_decision_log())

    assert logged[0] != ()
    assert logged[0] == logged[1]


def test_a_tie_goes_to_the_id_that_sorts_first_in_either_install_order(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Equal priorities are settled by ascending unit `id`, not by who was there first.

    Falsified by a tie-break reading the installed set, the registry's insertion
    order or the manifest's file name: installing `beta` first would make it the
    winner, so the same two packs would fight differently depending on the order
    their installs happened to run in.

    Evaluation order is not separately tested, and cannot be: the engine fixes it
    to ascending unit `id` (`engine/engine.py`'s `_by_id`), which is the same key
    the tie-break uses, so a run that consulted evaluation order would give the
    answer this asserts. The property is stated over the two orders that *can*
    differ, and the total-order rule itself is
    `tests/test_engine_arbitration.py`'s.
    """
    for order in ((FIRST, SECOND), (SECOND, FIRST)):
        session, entities = _house(tmp_path, vocabulary, order, priority=0)
        writers = _writers(session.advance_time(minutes=1))
        assert {tuple(writers.get(entity, ())) for entity in entities} == {
            (f"{FIRST}.b0",)
        }, order


def test_a_person_outranks_both_packs(tmp_path: Path, vocabulary: Vocabulary) -> None:
    """A user's write stands against two packs that want the same entity.

    Falsified by a tick that let a behaviour write over a person's change: the
    light would go back to what the packs asked for on the next evaluation, and
    "the user is in charge" would last until the next tick.

    The session enables the `override` unit, and that is not incidental: a person's
    touch is *registered* by a behaviour like any other, and every behaviour is off
    in a fixture until it is switched on. A session with the unit off has no
    mechanism that would notice the write at all, so a test that left it off would
    be asserting against a house that had been told nothing about people.

    The user's command is not a proposal and does not arbitrate -- it is written
    when the operation runs -- so what is asserted is the *consequence*: the entity
    still holds the state the person gave it, and no pack's record wrote it.
    Whether a pack was suppressed or merely lost is not this test's claim; the
    outcome precedence in `engine/engine.py` decides which of the two a record
    reports, and both mean the light did not move.
    """
    session = open_session(
        house="minimal",
        vocabulary=vocabulary,
        house_settings={
            **_settings(FIRST, SECOND),
            enable_key("override"): True,
        },
    )
    entities: tuple[str, ...] = ()
    for name in (FIRST, SECOND):
        result = session.install_pack(str(pack(tmp_path, name, priority=999_999)))
        entities = tuple(result["slots"][SLOT])
    chosen = sorted(entities)[0]

    session.user_action(chosen, "off")
    records = session.advance_time(minutes=1)

    assert session.read_entity(chosen).state == "off"
    assert [
        change
        for record in records
        for change in record.state_delta
        if change.entity_id == chosen
    ] == []

    contending = [
        record
        for actor in (f"{FIRST}.b0", f"{SECOND}.b0")
        for record in _by_actor(records, actor)
        if any(chosen in command.entities for command in record.commands)
    ]
    assert contending != [], (
        "the packs still wanted it; the person is what stopped them"
    )
    assert {record.outcome for record in contending} <= {
        Outcome.LOST_ARBITRATION,
        Outcome.OVERRIDDEN,
    }
