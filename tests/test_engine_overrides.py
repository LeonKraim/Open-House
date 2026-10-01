"""Manual override and the four reset conditions -- task 6.2.

Three properties carry this module. The first is that an override turns on the
change *context* and not on the fact that a change happened, so a write by the
engine, the world or a fault creates none -- tested by origin rather than by
asserting one negative. The second is that an override ends only through a named
condition, and that the condition is returned rather than merely applied, because
the record that resumes acting has to say which condition released it.

The third is the renewal hole, which has no counterpart in the mechanism's
description and is the one a plausible implementation gets wrong. The port reports
an entity's last *writer* and not when it wrote (`house-adapter`: there is no
`last_changed`), so after an override lapses the entity still reads "a user wrote
this last" -- and a registry keyed only on the origin would re-arm the override on
every tick, moving its expiry forward until the suppression never ended. The memory
of which touches have already been spent is therefore part of this module's state:
it is cleared by the first non-user origin the registry sees, and it is carried in
the snapshot, so both halves of the document are tested for round-tripping and for
the decision they exist to make.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from engine.adapter import ChangeOrigin
from engine.overrides import (
    LAPSE_ORDER,
    InvalidOverrideError,
    OverrideError,
    OverrideRecord,
    OverrideRegistry,
    ResetCondition,
)

_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)


def _overridden(
    *,
    at: datetime = _AT,
    duration: timedelta = timedelta(minutes=30),
    room_id: str | None = "kitchen",
    mode: str | None = None,
) -> OverrideRegistry:
    registry = OverrideRegistry()
    registry.note(
        "light.kitchen",
        origin=ChangeOrigin.USER,
        at=at,
        duration=duration,
        room_id=room_id,
        mode=mode,
    )
    return registry


# --------------------------------------------------------------------------
# What creates an override
# --------------------------------------------------------------------------


def test_a_user_change_overrides_the_entity_it_touched() -> None:
    """A user-origin change registers an override and returns the record.

    A falsifying implementation that suppressed nothing would leave the engine
    free to undo the person's action in the same tick, which is the behaviour the
    override exists to prevent.
    """
    registry = OverrideRegistry()
    record = registry.note(
        "light.kitchen",
        origin=ChangeOrigin.USER,
        at=_AT,
        duration=timedelta(minutes=30),
        room_id="kitchen",
    )
    assert record is not None
    assert registry.is_overridden("light.kitchen") is True
    assert record.expires_at == _AT + timedelta(minutes=30)
    assert record.room_id == "kitchen"


@pytest.mark.parametrize(
    "origin",
    [ChangeOrigin.ENGINE, ChangeOrigin.WORLD, ChangeOrigin.FAULT],
)
def test_only_a_user_change_overrides(origin: ChangeOrigin) -> None:
    """An engine, world or fault write creates no override, and says so.

    This is the requirement's whole hinge: a falsifying implementation that
    overrode on any write would have the engine override itself the first time it
    acted -- every behaviour would fire once and then be suppressed by its own
    actuation -- and a sensor updating would lock the room out of automation.
    """
    registry = OverrideRegistry()
    assert (
        registry.note(
            "light.kitchen", origin=origin, at=_AT, duration=timedelta(minutes=30)
        )
        is None
    )
    assert registry.is_overridden("light.kitchen") is False
    assert len(registry) == 0


def test_a_second_user_touch_replaces_the_first() -> None:
    """One override per entity: the latest user action is the one in force.

    A falsifying implementation that stacked overrides would need an order to
    answer "which condition applies", and would hold a stale record whose expiry
    could release an override the user has since renewed.
    """
    registry = _overridden(duration=timedelta(minutes=5))
    registry.note(
        "light.kitchen",
        origin=ChangeOrigin.USER,
        at=_AT + _MINUTE,
        duration=timedelta(minutes=30),
        room_id="kitchen",
    )
    assert len(registry) == 1
    record = registry.record_for("light.kitchen")
    assert record is not None
    assert record.recorded_at == _AT + _MINUTE
    assert record.expires_at == _AT + _MINUTE + timedelta(minutes=30)


def test_a_non_positive_override_duration_is_refused() -> None:
    """A duration that cannot suppress anything is a mistake, not a no-op.

    A falsifying implementation that accepted zero would record an override that
    expires at the instant it was recorded, so the mechanism would appear to be
    running and never suppress a command -- a configuration error that reads as a
    bug in the override.
    """
    registry = OverrideRegistry()
    with pytest.raises(InvalidOverrideError):
        registry.note(
            "light.kitchen",
            origin=ChangeOrigin.USER,
            at=_AT,
            duration=timedelta(0),
        )


def test_a_registry_can_be_built_from_records() -> None:
    """The records a snapshot carries rebuild the registry that held them.

    A falsifying implementation that only accepted an empty registry would leave
    the override state unrestorable, and a restored house would let the engine
    act on a light the user had taken over -- the one piece of state a restore
    most obviously must keep.
    """
    records = (
        OverrideRecord(
            entity_id="light.kitchen",
            room_id="kitchen",
            recorded_at=_AT,
            expires_at=_AT + timedelta(minutes=30),
            mode=None,
        ),
    )
    registry = OverrideRegistry(records=records)
    assert registry.records() == records
    assert registry.is_overridden("light.kitchen") is True


def test_the_records_come_back_ordered_by_entity_id() -> None:
    """The snapshot form is ordered, so a restore of a replay is identical.

    A falsifying implementation that returned the dict's insertion order would
    make the snapshot's content depend on the order the user happened to touch
    things, and two runs that reached the same state would restore differently.
    """
    registry = OverrideRegistry()
    for entity_id in ("light.hall", "light.bedroom", "light.kitchen"):
        registry.note(
            entity_id, origin=ChangeOrigin.USER, at=_AT, duration=timedelta(minutes=30)
        )
    assert [record.entity_id for record in registry.records()] == [
        "light.bedroom",
        "light.hall",
        "light.kitchen",
    ]


# --------------------------------------------------------------------------
# The reset conditions
# --------------------------------------------------------------------------


def test_override_timeout_lapses_the_override() -> None:
    """The configured duration elapsing on the virtual clock releases it.

    A falsifying implementation that never lapsed would suppress the engine's
    commands to that entity for the rest of the run, and a user who tapped a
    light once would have disabled the room's automation permanently.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    at = _AT + timedelta(minutes=30)
    assert registry.lapse(now=at) == (
        ("light.kitchen", ResetCondition.OVERRIDE_TIMEOUT),
    )
    assert registry.is_overridden("light.kitchen") is False


def test_the_override_does_not_lapse_before_its_duration() -> None:
    """One second short of the duration, the override still holds.

    A falsifying implementation that compared with `>` against the recorded time
    rather than the expiry -- or that used the wall clock -- would release early,
    and the release would be invisible because the log would call it an acted
    evaluation.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    assert registry.lapse(now=_AT + timedelta(minutes=30) - timedelta(seconds=1)) == ()
    assert registry.is_overridden("light.kitchen") is True


def test_room_emptied_lapses_the_override() -> None:
    """A room clear for the quiet timeout releases the override for that room.

    A falsifying implementation that ignored the room would keep the light under
    the user's control after they left, which is the opposite of what a motion
    house is for.
    """
    registry = _overridden(room_id="kitchen")
    assert registry.lapse(now=_AT + _MINUTE, empty_rooms={"kitchen"}) == (
        ("light.kitchen", ResetCondition.ROOM_EMPTIED),
    )


def test_a_house_scoped_entity_never_lapses_by_room_emptied() -> None:
    """An entity in no room cannot empty, so the condition cannot hold for it.

    A falsifying implementation that treated a missing room as an empty one would
    release every house-scoped override on the first tick, and a user's takeover
    of a house-wide light group would last exactly one evaluation.
    """
    registry = _overridden(room_id=None)
    assert registry.lapse(now=_AT + _MINUTE, empty_rooms={"kitchen", ""}) == ()
    assert registry.is_overridden("light.kitchen") is True


def test_mode_changed_lapses_the_override() -> None:
    """Leaving the mode the override was recorded under releases it.

    A falsifying implementation that ignored modes would keep a bedtime override
    in force after the house left away mode, and the room would stay under the
    hand that set it long after the reason for it had gone.
    """
    registry = _overridden(mode="home")
    assert registry.lapse(now=_AT + _MINUTE, active_modes={"away"}) == (
        ("light.kitchen", ResetCondition.MODE_CHANGED),
    )


def test_an_override_recorded_with_no_mode_never_lapses_by_mode_changed() -> None:
    """No mode recorded means the condition has nothing to have left.

    A falsifying implementation that compared `None` against the active set --
    where `None` is never a member -- would release every mode-free override on
    the first tick.
    """
    registry = _overridden(mode=None)
    assert registry.lapse(now=_AT + _MINUTE, active_modes={"away"}) == ()
    assert registry.is_overridden("light.kitchen") is True


def test_the_recorded_mode_still_active_does_not_lapse() -> None:
    """The override holds while its mode is in force."""
    registry = _overridden(mode="home")
    assert registry.lapse(now=_AT + _MINUTE, active_modes={"home"}) == ()
    assert registry.is_overridden("light.kitchen") is True


def test_explicit_clear_releases_an_override_and_reports_whether_one_was() -> None:
    """A caller can clear an override, and the answer says if there was one.

    A falsifying implementation that raised on a clear with nothing to clear
    would make a scenario step that clears twice a failed scenario, and a
    `user_action` that clears an override the timeout already dropped would fail
    for having raced the clock.
    """
    registry = _overridden()
    assert registry.clear("light.kitchen") is True
    assert registry.is_overridden("light.kitchen") is False
    assert registry.clear("light.kitchen") is False


def test_the_first_condition_that_holds_is_the_one_reported() -> None:
    """Two conditions holding at once reports the earlier one in the lapse order.

    A falsifying implementation that reported an arbitrary one of the conditions
    -- or the last it checked -- would make a record that resumes acting say a
    different thing on a replay whose checks were ordered differently, and the
    record is exactly where a user learns why the automation came back.
    """
    registry = _overridden(
        duration=timedelta(minutes=30), room_id="kitchen", mode="home"
    )
    at = _AT + timedelta(minutes=30)
    assert registry.lapse(now=at, empty_rooms={"kitchen"}, active_modes=set()) == (
        ("light.kitchen", ResetCondition.OVERRIDE_TIMEOUT),
    )


def test_explicit_clear_is_not_a_lapse_condition() -> None:
    """The closed set has four members and the lapse order has three.

    A falsifying implementation that folded `explicit_clear` into `lapse` would
    release every override on every tick, because a clear is a caller's statement
    about one entity and not a fact about the house that a tick can observe.
    """
    assert set(ResetCondition) == {
        ResetCondition.OVERRIDE_TIMEOUT,
        ResetCondition.ROOM_EMPTIED,
        ResetCondition.MODE_CHANGED,
        ResetCondition.EXPLICIT_CLEAR,
    }
    assert len(ResetCondition) == 4
    assert ResetCondition.EXPLICIT_CLEAR not in LAPSE_ORDER
    assert LAPSE_ORDER == (
        ResetCondition.OVERRIDE_TIMEOUT,
        ResetCondition.ROOM_EMPTIED,
        ResetCondition.MODE_CHANGED,
    )


def test_lapse_releases_every_qualifying_override_in_entity_order() -> None:
    """One lapse call releases all of them, ordered by entity id.

    A falsifying implementation that stopped at the first would need one call per
    entity, and the caller's loop would decide the order the records were written
    in rather than the registry's fixed order.
    """
    registry = OverrideRegistry()
    for entity_id in ("light.hall", "light.kitchen"):
        registry.note(
            entity_id,
            origin=ChangeOrigin.USER,
            at=_AT,
            duration=timedelta(minutes=1),
            room_id="kitchen",
        )
    released = registry.lapse(now=_AT + _MINUTE)
    assert released == (
        ("light.hall", ResetCondition.OVERRIDE_TIMEOUT),
        ("light.kitchen", ResetCondition.OVERRIDE_TIMEOUT),
    )
    assert len(registry) == 0


# --------------------------------------------------------------------------
# The renewal hole: an ended override does not re-arm on the same touch
# --------------------------------------------------------------------------


def test_a_lapsed_entity_does_not_re_arm_from_the_same_touch() -> None:
    """A user origin after a lapse records nothing, so the timeout stays ended.

    This is the regression the mechanism exists for, and it is the one nothing
    else in the suite would catch. The port carries an entity's *last writer* and
    no time (`house-adapter`: there is no `last_changed`), so after an override
    lapses the entity still reads "a user wrote this last" -- and a tick that
    re-noted it would push the expiry forward once per tick, turning a one-hour
    suppression into one that never ends. A falsifying implementation that keyed
    only on the origin would suppress the room's automation for the rest of the
    run, and every other test here would still pass because each of them notes
    once.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    registry.lapse(now=_AT + timedelta(minutes=30))
    assert registry.has_lapsed("light.kitchen") is True

    assert (
        registry.note(
            "light.kitchen",
            origin=ChangeOrigin.USER,
            at=_AT + timedelta(hours=1),
            duration=timedelta(minutes=30),
            room_id="kitchen",
        )
        is None
    )
    assert registry.is_overridden("light.kitchen") is False
    assert len(registry) == 0


def test_an_explicit_clear_is_remembered_like_a_lapse() -> None:
    """A cleared override is not undone by the next tick's user origin.

    A falsifying implementation that remembered only the lapses would make the
    control surface's clear work for exactly one evaluation: the person clears the
    override, the tick reads the same user origin the clear was about, and the
    override is back -- a control that appears to work and does not.
    """
    registry = _overridden()
    assert registry.clear("light.kitchen") is True
    assert registry.has_lapsed("light.kitchen") is True
    assert (
        registry.note(
            "light.kitchen",
            origin=ChangeOrigin.USER,
            at=_AT + _MINUTE,
            duration=timedelta(minutes=30),
        )
        is None
    )
    assert registry.is_overridden("light.kitchen") is False


@pytest.mark.parametrize(
    "origin", [ChangeOrigin.ENGINE, ChangeOrigin.WORLD, ChangeOrigin.FAULT]
)
def test_a_non_user_origin_clears_the_lapsed_memory(origin: ChangeOrigin) -> None:
    """Somebody other than the user writing is the signal the touch has been superseded.

    The memory has to be clearable or the entity is uncommandable forever: the
    engine's own next actuation is the commonest non-user write there is, and a
    registry that kept the memory across it would never let a second manual touch
    take effect for the life of the run. A falsifying implementation that discarded
    nothing would resolve the ambiguity toward "never suppress again" instead of
    toward "the timeout works", which is the opposite failure and just as wrong.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    registry.lapse(now=_AT + timedelta(minutes=30))
    assert registry.has_lapsed("light.kitchen") is True

    assert (
        registry.note(
            "light.kitchen",
            origin=origin,
            at=_AT + timedelta(minutes=31),
            duration=timedelta(minutes=30),
        )
        is None
    )
    assert registry.has_lapsed("light.kitchen") is False


def test_a_touch_after_a_superseding_write_arms_a_fresh_override() -> None:
    """Once something else wrote, the user's next touch is a new touch again.

    The other half of the test above: clearing the memory is only correct if it
    restores the power to override. A falsifying implementation that cleared the
    record but left the entity permanently uncommandable would pass the test above
    and fail this one -- which is why they are a pair.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    registry.lapse(now=_AT + timedelta(minutes=30))
    registry.note(
        "light.kitchen",
        origin=ChangeOrigin.ENGINE,
        at=_AT + timedelta(minutes=31),
        duration=timedelta(minutes=30),
    )
    record = registry.note(
        "light.kitchen",
        origin=ChangeOrigin.USER,
        at=_AT + timedelta(minutes=32),
        duration=timedelta(minutes=30),
        room_id="kitchen",
    )
    assert record is not None
    assert record.recorded_at == _AT + timedelta(minutes=32)
    assert registry.is_overridden("light.kitchen") is True


def test_the_memory_is_keyed_by_entity_not_held_as_one_flag() -> None:
    """One entity's lapse leaves another's override armable.

    A falsifying implementation that stored the state as a single "the last thing
    was a user touch" boolean would refuse to arm any second override after the
    first lapsed anywhere in the house, so a person could take over one light for
    the whole run and no other.
    """
    registry = OverrideRegistry()
    registry.note(
        "light.hall",
        origin=ChangeOrigin.USER,
        at=_AT,
        duration=timedelta(minutes=1),
        room_id="hall",
    )
    registry.note(
        "light.kitchen",
        origin=ChangeOrigin.USER,
        at=_AT,
        duration=timedelta(minutes=30),
        room_id="kitchen",
    )
    registry.lapse(now=_AT + _MINUTE)

    # Only the hall's override was ended, so only the hall's touch is spent.
    assert registry.has_lapsed("light.hall") is True
    assert registry.has_lapsed("light.kitchen") is False
    assert (
        registry.note(
            "light.hall",
            origin=ChangeOrigin.USER,
            at=_AT + timedelta(minutes=31),
            duration=timedelta(minutes=30),
            room_id="hall",
        )
        is None
    )
    assert (
        registry.note(
            "light.bedroom",
            origin=ChangeOrigin.USER,
            at=_AT + timedelta(minutes=31),
            duration=timedelta(minutes=30),
            room_id="bedroom",
        )
        is not None
    )


def test_a_duration_that_cannot_suppress_is_refused_before_the_memory_is_read() -> None:
    """The duration is checked first, so the mistake is named even for a lapsed entity.

    `note` answers two questions -- is there a record, and was the duration legal
    -- and a falsifying implementation that read the lapsed memory before
    validating the duration would return `None` for a bad duration on a lapsed
    entity and raise for the same duration on a fresh one. The caller would then
    hear about a configuration error only sometimes, which is the shape of a bug
    that reproduces on one house and not another.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    registry.lapse(now=_AT + timedelta(minutes=30))
    with pytest.raises(InvalidOverrideError):
        registry.note(
            "light.kitchen",
            origin=ChangeOrigin.USER,
            at=_AT + timedelta(hours=1),
            duration=timedelta(0),
        )


# --------------------------------------------------------------------------
# The document, which carries both halves of the state
# --------------------------------------------------------------------------


def test_the_document_carries_the_records_and_the_lapsed_memory() -> None:
    """Both halves are in the snapshot, because both decide a resumed run.

    A falsifying implementation that wrote only `records` would restore a run
    missing the memory of which touch it had already spent, and the resumed run
    would re-arm exactly the override the run it resumed had ended -- the renewal
    hole reopened by the restore, and only for runs that had been restored.
    """
    registry = _overridden()
    record = registry.records()[0]
    assert registry.to_document() == {
        "records": [
            {
                "entity_id": "light.kitchen",
                "room_id": "kitchen",
                "recorded_at": record.recorded_at.isoformat(),
                "expires_at": record.expires_at.isoformat(),
                "mode": None,
            }
        ],
        "lapsed": [],
    }


def test_the_lapsed_half_is_written_in_a_fixed_order() -> None:
    """The lapsed entities come back sorted, so two documents of one state are equal.

    A falsifying implementation that wrote the set's own iteration order would make
    the snapshot's bytes depend on the hash seed of the process, and comparing two
    runs' snapshots -- which is how the replay property is stated -- would compare
    two orderings of the same facts.
    """
    registry = OverrideRegistry()
    for entity_id in ("light.study", "light.hall", "light.kitchen"):
        registry.note(
            entity_id, origin=ChangeOrigin.USER, at=_AT, duration=timedelta(minutes=1)
        )
    registry.lapse(now=_AT + _MINUTE)
    assert registry.to_document()["lapsed"] == [
        "light.hall",
        "light.kitchen",
        "light.study",
    ]


def test_a_registry_round_trips_through_its_document() -> None:
    """Both halves survive the round trip, so a restored run decides the same.

    The equal-documents assertion is the weak half; the load-bearing half is that
    the restored registry still refuses to re-arm, because that is the one decision
    the memory exists to make and the one a restore is most likely to drop.
    """
    registry = _overridden(duration=timedelta(minutes=30))
    registry.note(
        "light.hall",
        origin=ChangeOrigin.USER,
        at=_AT,
        duration=timedelta(minutes=1),
        room_id="hall",
    )
    registry.lapse(now=_AT + _MINUTE)
    assert registry.has_lapsed("light.hall") is True

    restored = OverrideRegistry.from_document(registry.to_document())
    assert restored.to_document() == registry.to_document()
    assert restored.records() == registry.records()
    assert restored.has_lapsed("light.hall") is True
    assert restored.has_lapsed("light.kitchen") is False
    assert (
        restored.note(
            "light.hall",
            origin=ChangeOrigin.USER,
            at=_AT + timedelta(hours=1),
            duration=timedelta(minutes=30),
            room_id="hall",
        )
        is None
    )


def test_the_document_is_json_safe() -> None:
    """The snapshot is JSON, so neither half may carry a `datetime` or a set.

    A falsifying implementation that wrote the `OverrideRecord` objects or the raw
    set would fail in `sim/snapshot.py` when the whole run was written, and the
    traceback would name the serialiser rather than this registry.
    """
    registry = _overridden()
    registry.clear("light.kitchen")
    document = registry.to_document()
    assert json.loads(json.dumps(document)) == document


#: Documents that are the wrong shape for a registry. Collected at module level
#: and typed `object` because the cases are deliberately of several shapes -- a
#: string, a list of pairs, a mapping missing a half -- and the point of the test
#: is that none of them is read as a valid document.
_BAD_DOCUMENTS: tuple[object, ...] = (
    "not an object",
    [("records", ())],
    {"lapsed": []},
    {"records": []},
    {"records": "not a list", "lapsed": []},
    {"records": [], "lapsed": "not a list"},
    {"records": {}, "lapsed": []},
    {"records": [], "lapsed": {}},
)

#: Records that cannot be read. The last three are well-shaped but carry a field
#: of the wrong type, which is the case a reader that trusted the file would miss.
_BAD_RECORDS: tuple[object, ...] = (
    "not an object",
    [],
    {},
    {"entity_id": "light.kitchen"},
    {"entity_id": 1, "recorded_at": _AT.isoformat(), "expires_at": _AT.isoformat()},
    {
        "entity_id": "light.kitchen",
        "recorded_at": "not a timestamp",
        "expires_at": _AT.isoformat(),
    },
    {
        "entity_id": "light.kitchen",
        "recorded_at": _AT.isoformat(),
        "expires_at": None,
    },
    {
        "entity_id": "light.kitchen",
        "recorded_at": _AT.isoformat(),
        "expires_at": _AT.isoformat(),
        "room_id": 7,
    },
)


@pytest.mark.parametrize(
    "document",
    _BAD_DOCUMENTS,
)
def test_a_document_of_the_wrong_shape_is_refused(document: object) -> None:
    """A half that is missing or of the wrong type fails rather than defaulting empty.

    A falsifying implementation that read the halves with `.get(..., [])` would
    restore an empty registry for a document that had lost its records, report
    success, and let the engine act on every light the user had taken over -- the
    one failure a restore must not have. A string passed the membership test a bare
    `Sequence` check makes, which is why the shape test is worth writing twice.
    """
    with pytest.raises(OverrideError):
        OverrideRegistry.from_document(document)


@pytest.mark.parametrize(
    "row",
    _BAD_RECORDS,
)
def test_a_malformed_record_is_refused(row: object) -> None:
    """A record with an unreadable field fails, rather than being skipped.

    A falsifying implementation that dropped a row it could not read would restore
    a registry missing an override and report success, and the engine would command
    a light the user had taken over -- with nothing anywhere saying a restore had
    quietly lost a row.
    """
    with pytest.raises(OverrideError):
        OverrideRegistry.from_document({"records": [row], "lapsed": []})


def test_a_lapsed_entry_that_is_not_a_string_is_refused() -> None:
    """The lapsed half is a list of entity ids, not of whatever a file held.

    A falsifying implementation that accepted any iterable would carry a number or
    a nested list into the set, and a user origin for that value would never match
    a real entity -- so the memory would silently never apply.
    """
    with pytest.raises(OverrideError):
        OverrideRegistry.from_document({"records": [], "lapsed": [7]})
