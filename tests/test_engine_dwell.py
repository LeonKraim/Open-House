"""How long a room has been quiet -- the duration the port cannot report.

`house-adapter` carries no `last_changed`: an entity has a state and not the
instant it entered it. Two requirements need the missing number anyway --
motion lighting turning a light off "once the room has been clear of motion for
the configured quiet timeout", and `room_emptied` releasing an override after
the same period -- so the engine observes readings and keeps the instant a room
last read as occupied. This module tests that registry.

Three properties are the ones a plausible implementation gets wrong, and each
has its own test below. The **boundary** is inclusive, because "advance by
exactly the timeout" is the scenario a scenario runner will write and it has to
be the one that fires. A slot **never observed** is not quiet, because "we do
not know" must not read as "empty" and let a shutdown fire on a room nothing is
watching. And a room **never seen active** still goes quiet, because a room
nobody has walked into yet is the normal case at the start of a run.

Each test says, in its docstring, what a falsifying implementation would look
like.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from engine.dwell import Dwell, DwellError, DwellRegistry

_AT = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)
_TIMEOUT = timedelta(minutes=5)
_MOTION = "motion_sensor"


def _observed(
    at: datetime = _AT, *, room_id: str = "kitchen", slot: str = _MOTION
) -> DwellRegistry:
    """A registry that has seen one clear reading, which starts a period."""
    registry = DwellRegistry()
    registry.observe(room_id, slot, active=False, at=at)
    return registry


# --------------------------------------------------------------------------
# What an observation does to a period
# --------------------------------------------------------------------------


def test_an_active_reading_starts_the_period_at_that_instant() -> None:
    """Motion at `t` makes the quiet period begin at `t`.

    A falsifying implementation that started the period at the *first* clear
    reading instead would measure the quiet time from whenever the observer
    happened to look, and a room vacated ten minutes before the next tick would
    read as freshly occupied.
    """
    registry = DwellRegistry()
    record = registry.observe("kitchen", _MOTION, active=True, at=_AT)
    assert record.since == _AT
    assert registry.since("kitchen", _MOTION) == _AT


def test_a_clear_reading_leaves_an_existing_period_where_it_was() -> None:
    """Every subsequent clear reading does not move the start of the period.

    A falsifying implementation that reset `since` on every observation would
    make a room that keeps reporting "no motion" never go quiet, so motion
    lighting's off half and `room_emptied` would both be unreachable.
    """
    registry = DwellRegistry()
    registry.observe("kitchen", _MOTION, active=True, at=_AT)
    registry.observe("kitchen", _MOTION, active=False, at=_AT + _MINUTE)
    registry.observe("kitchen", _MOTION, active=False, at=_AT + 2 * _MINUTE)
    assert registry.since("kitchen", _MOTION) == _AT


def test_a_room_never_seen_active_is_quiet_from_its_first_clear_reading() -> None:
    """A clear reading on a fresh room starts a period rather than doing nothing.

    A falsifying implementation that only started a period on an active reading
    would leave every room nobody had walked into unobserved for the whole run,
    and a house that emptied before anyone moved would never shut down. It is the
    same reading a real sensor gives, and the intent is "this room has been clear
    since I first looked".
    """
    registry = DwellRegistry()
    registry.observe("kitchen", _MOTION, active=False, at=_AT)
    assert registry.since("kitchen", _MOTION) == _AT
    assert registry.quiet("kitchen", _MOTION, at=_AT + _TIMEOUT, timeout=_TIMEOUT)


def test_motion_again_restarts_the_period_at_the_new_instant() -> None:
    """A second active reading moves `since` forward to it.

    A falsifying implementation that kept the first `since` after later motion
    would turn the light off while somebody was still in the room, which is the
    failure mode the whole quiet mechanism exists to avoid.
    """
    registry = DwellRegistry()
    registry.observe("kitchen", _MOTION, active=True, at=_AT)
    registry.observe("kitchen", _MOTION, active=False, at=_AT + _MINUTE)
    later = _AT + timedelta(minutes=30)
    registry.observe("kitchen", _MOTION, active=True, at=later)
    assert registry.since("kitchen", _MOTION) == later
    assert not registry.quiet(
        "kitchen", _MOTION, at=later + _TIMEOUT - timedelta(seconds=1), timeout=_TIMEOUT
    )


# --------------------------------------------------------------------------
# Quiet, and its boundary
# --------------------------------------------------------------------------


def test_the_quiet_boundary_is_inclusive() -> None:
    """Exactly one timeout after the last motion, the room is quiet.

    A falsifying implementation that compared with a strict `>` would make
    "advance by exactly the timeout" the scenario that *almost* turns the light
    off, and every scenario author would have to add a second to work around a
    detail the spec does not state.
    """
    registry = _observed()
    assert registry.quiet("kitchen", _MOTION, at=_AT + _TIMEOUT, timeout=_TIMEOUT)
    assert not registry.quiet(
        "kitchen", _MOTION, at=_AT + _TIMEOUT - timedelta(seconds=1), timeout=_TIMEOUT
    )


def test_an_unobserved_slot_is_not_quiet_at_any_timeout() -> None:
    """A room nothing watches never reads as quiet, even at a zero timeout.

    A falsifying implementation that treated a missing record as "quiet since the
    beginning of time" would let the away shutdown fire on a house whose only
    rooms have no motion sensors, and the decision log would show an empty house
    nothing had ever observed.
    """
    registry = DwellRegistry()
    assert not registry.quiet("kitchen", _MOTION, at=_AT, timeout=timedelta(0))
    assert registry.since("kitchen", _MOTION) is None


def test_a_zero_timeout_is_quiet_immediately_after_an_observation() -> None:
    """With no timeout configured, a clear reading is quiet at once.

    The mirror of the test above: an observed slot is judged by the timeout it is
    given, so the boundary case of zero is a real answer rather than a refusal.
    This is what stops the unobserved case passing for the wrong reason.
    """
    registry = _observed()
    assert registry.quiet("kitchen", _MOTION, at=_AT, timeout=timedelta(0))


def test_the_slot_is_part_of_the_key() -> None:
    """One room may be observed on two slots without one answer overwriting the other.

    A falsifying implementation keyed by room alone would let a `contact_sensor`
    observation silently replace a `motion_sensor` one, so a room whose door
    closed a minute ago would read as freshly occupied and a rule watching motion
    would decide from a reading nobody made.
    """
    registry = DwellRegistry()
    registry.observe("kitchen", _MOTION, active=True, at=_AT)
    registry.observe(
        "kitchen", "contact_sensor", active=False, at=_AT + timedelta(hours=1)
    )
    assert registry.since("kitchen", _MOTION) == _AT
    assert registry.since("kitchen", "contact_sensor") == _AT + timedelta(hours=1)
    assert len(registry) == 2


# --------------------------------------------------------------------------
# Every quiet room at once
# --------------------------------------------------------------------------


def test_quiet_rooms_names_every_room_whose_slot_has_been_clear() -> None:
    """Both of two clear rooms are returned, and a third, active one is not.

    A falsifying implementation that returned the first match -- or that folded a
    read's reduction over rooms into a single boolean -- would give a house that
    shuts down one room and leaves the rest lit, with nothing in the log to say
    which rooms it had looked at.
    """
    registry = DwellRegistry()
    for room_id in ("kitchen", "hall", "study"):
        registry.observe(room_id, _MOTION, active=False, at=_AT)
    registry.observe("study", _MOTION, active=True, at=_AT + _MINUTE)
    assert registry.quiet_rooms(slot=_MOTION, at=_AT + _TIMEOUT, timeout=_TIMEOUT) == (
        "hall",
        "kitchen",
    )


def test_quiet_rooms_is_ordered_by_room_id_however_they_were_observed() -> None:
    """The rooms come back sorted, so a caller writing records writes them in order.

    A falsifying implementation that returned the registry's insertion order
    would make the decision log depend on the order a fixture happened to bind
    its rooms, and two runs of one scenario would append different records.
    """
    registry = DwellRegistry()
    for room_id in ("study", "hall", "kitchen"):
        registry.observe(room_id, _MOTION, active=False, at=_AT)
    assert registry.quiet_rooms(slot=_MOTION, at=_AT + _TIMEOUT, timeout=_TIMEOUT) == (
        "hall",
        "kitchen",
        "study",
    )


def test_quiet_rooms_ignores_rooms_observed_on_a_different_slot() -> None:
    """Only the rooms with *this* slot are considered.

    A falsifying implementation that ignored the slot filter would report a room
    quiet because its door sensor settled, even though its motion sensor is
    unbound and the room has never been watched for motion.
    """
    registry = DwellRegistry()
    registry.observe("kitchen", _MOTION, active=False, at=_AT)
    registry.observe("hall", "contact_sensor", active=False, at=_AT)
    assert registry.quiet_rooms(slot=_MOTION, at=_AT + _TIMEOUT, timeout=_TIMEOUT) == (
        "kitchen",
    )


def test_quiet_rooms_is_empty_when_nothing_has_been_observed() -> None:
    """No observations, no quiet rooms -- not every room in the house."""
    registry = DwellRegistry()
    assert registry.quiet_rooms(slot=_MOTION, at=_AT, timeout=timedelta(0)) == ()


# --------------------------------------------------------------------------
# The records and the document
# --------------------------------------------------------------------------


def test_the_periods_come_back_ordered_by_room_then_slot() -> None:
    """The record list has a fixed order, so a snapshot of a run is stable.

    A falsifying implementation that returned the dict's insertion order would
    make two runs that reached the same state write different documents, and the
    restore-and-replay property is stated over the document.
    """
    registry = DwellRegistry()
    for room_id, slot in (
        ("study", _MOTION),
        ("kitchen", "contact_sensor"),
        ("kitchen", _MOTION),
    ):
        registry.observe(room_id, slot, active=False, at=_AT)
    assert [(record.room_id, record.slot) for record in registry.records()] == [
        ("kitchen", "contact_sensor"),
        ("kitchen", _MOTION),
        ("study", _MOTION),
    ]


def test_a_registry_round_trips_through_its_document() -> None:
    """`from_document(to_document())` answers every quiet question the same way.

    A falsifying implementation that carried only the room ids would lose the
    instant the quiet period began, and a restored run would measure every quiet
    time from the restore rather than from the last motion -- a divergence the
    replay property would catch and nothing here would explain.
    """
    registry = _observed()
    restored = DwellRegistry.from_document(registry.to_document())
    assert restored.records() == registry.records()
    assert restored.quiet("kitchen", _MOTION, at=_AT + _TIMEOUT, timeout=_TIMEOUT)
    assert not restored.quiet("kitchen", _MOTION, at=_AT + _MINUTE, timeout=_TIMEOUT)


def test_the_document_is_json_safe() -> None:
    """The snapshot is JSON, so the document must serialise without a converter.

    A falsifying implementation that wrote the `datetime` objects themselves
    would fail in `sim/snapshot.py` at the point the whole run is written, and
    the failure would name the serialiser rather than this registry.
    """
    document = _observed().to_document()
    assert json.loads(json.dumps(document)) == document


def test_the_since_instant_survives_the_round_trip_with_its_zone() -> None:
    """An aware instant comes back aware, and equal to the one that went in.

    A falsifying implementation that dropped the offset would restore a naive
    instant, and the first subtraction against the clock would raise -- or worse,
    compare a naive instant to an aware one and give an answer that depends on
    the machine's zone.
    """
    registry = _observed(at=_AT)
    restored = DwellRegistry.from_document(registry.to_document())
    since = restored.since("kitchen", _MOTION)
    assert since is not None
    assert since.tzinfo is not None
    assert since == _AT


def test_a_registry_can_be_built_from_records() -> None:
    """The records a snapshot carries rebuild the registry that held them."""
    records = (Dwell(room_id="kitchen", slot=_MOTION, since=_AT),)
    registry = DwellRegistry(records)
    assert registry.records() == records
    assert registry.since("kitchen", _MOTION) == _AT


# --------------------------------------------------------------------------
# What is refused
# --------------------------------------------------------------------------


@pytest.mark.parametrize("document", ["not a list", {"room_id": "kitchen"}, None, 42])
def test_a_document_that_is_not_a_list_is_refused(document: object) -> None:
    """A document of the wrong shape fails rather than restoring an empty registry.

    A falsifying implementation that returned an empty registry for anything it
    did not recognise would silently lose every quiet timer in a snapshot, and a
    resumed run would shut down a house it had decided was occupied.
    """
    with pytest.raises(DwellError):
        DwellRegistry.from_document(document)


@pytest.mark.parametrize(
    "row",
    [
        "not an object",
        {"room_id": "kitchen", "slot": _MOTION},
        {"room_id": "kitchen", "slot": _MOTION, "since": 12},
        {"room_id": "kitchen", "slot": _MOTION, "since": "not a timestamp"},
        {"room_id": 1, "slot": _MOTION, "since": _AT.isoformat()},
    ],
)
def test_a_malformed_row_is_refused_naming_the_row(row: object) -> None:
    """A row with a missing or unreadable field fails, and the message names it.

    A falsifying implementation that skipped a row it could not read would
    restore a registry missing a room and report success, which is the one
    failure mode a restore must not have.
    """
    with pytest.raises(DwellError) as raised:
        DwellRegistry.from_document([row])
    assert "0" in str(raised.value)
