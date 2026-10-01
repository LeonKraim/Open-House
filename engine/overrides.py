"""Manual override: the user was last to touch it, so the engine stands down.

Task 6.2. An entity whose **last writer was a user** is overridden, and while it
is, the engine suppresses its own behaviour commands to it. "Last writer was a
user" is read from the change context the port attaches to every change
(`house-adapter`), never from a timestamp or a heuristic: an `engine`-, `world`-
or `fault`-origin change does not create an override, which is what makes the
rule about *who* acted rather than about the fact that something did.

An override ends only through a named `ResetCondition`, and the condition that
ended it is part of the record that resumes acting -- because "the behaviour ran
and was overridden" and "the behaviour did not run" are different facts, and a
user who is told the second when the first happened has been told nothing about
what the engine did.

The four conditions are a closed set. Three are observable to this module from
the arguments `lapse` is handed -- the virtual clock, which rooms are empty, and
which modes are active -- and they are evaluated in the module's declared order,
so an override that two conditions would release reports the same one on every
replay. The fourth, `explicit_clear`, is not a lapse at all: it is a caller
saying so through `clear`, which is why it is not in the lapse order.

**An override that has ended does not re-arm on the same touch.** The port
carries an entity's *last writer* and not when it wrote (`house-adapter`: there
is no `last_changed`), so "the last writer is a user" stays true after the
override it created has lapsed -- and a registry that re-recorded it each tick
would move its expiry forward every tick, turning the one-hour timeout into a
suppression that never ends. Two mechanisms together keep that from happening,
and they are both needed: the calling unit declines to re-note an entity already
in force (`BehaviourContext.is_overridden`), and an entity whose override *has*
ended is remembered here as *lapsed*, so a user origin for it is read as the same
touch already accounted for rather than as a new one. The memory is cleared by
the first non-user origin the registry sees for that entity, because that is the
signal the touch is no longer the last thing that happened to it. It is engine
state and it is snapshotted (`to_document`), because a resumed run that lost it
would re-arm an override the run it resumed had ended.

That leaves one limitation worth stating rather than discovering: a **second**
manual touch of the same entity, arriving after its override has ended and with
no intervening non-user write, does not re-arm the override. The port reports one
origin and no time, so that touch is indistinguishable from the first, and the
registry deliberately resolves the ambiguity toward the timeout working. The
remedy is a port that reports *when*, which Phase 2's real adapter can and this
phase's fake cannot.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from engine.adapter import ChangeOrigin


class OverrideError(Exception):
    """Base for the failures this module defines."""


class InvalidOverrideError(OverrideError):
    """An override duration that is not a positive interval.

    A zero or negative duration would expire the instant it was recorded, so the
    override would exist and never suppress anything -- a configuration mistake
    that would read as the override mechanism being broken.
    """

    def __init__(self, duration: timedelta) -> None:
        super().__init__(f"an override duration must be positive, not {duration!r}")
        self.duration = duration


class ResetCondition(StrEnum):
    """The closed set of conditions that end an override."""

    OVERRIDE_TIMEOUT = "override_timeout"
    ROOM_EMPTIED = "room_emptied"
    MODE_CHANGED = "mode_changed"
    EXPLICIT_CLEAR = "explicit_clear"


#: The conditions `lapse` evaluates, in the order it evaluates them, lowest first.
#: `explicit_clear` is absent because it is not a lapse: it is a caller saying so.
LAPSE_ORDER: tuple[ResetCondition, ...] = (
    ResetCondition.OVERRIDE_TIMEOUT,
    ResetCondition.ROOM_EMPTIED,
    ResetCondition.MODE_CHANGED,
)


@dataclass(frozen=True, slots=True)
class OverrideRecord:
    """One entity's override, and the facts its conditions are decided from."""

    entity_id: str
    #: The room the entity belongs to, when it belongs to one. A house-scoped
    #: entity has none, and so cannot lapse by `room_emptied`.
    room_id: str | None
    #: When the override was recorded, on the virtual clock.
    recorded_at: datetime
    #: When `override_timeout` expires, computed from the duration in force at
    #: the time of recording -- so a later change to the configured duration does
    #: not move an override that is already running.
    expires_at: datetime
    #: The mode in force when the override was recorded, or `None` if no mode
    #: was. `mode_changed` means this mode is no longer active.
    mode: str | None


class OverrideRegistry:
    """The overrides currently in force, keyed by entity.

    One override per entity: a second user touch of the same entity replaces the
    first rather than stacking, because the question the registry answers is
    "when did the user last have this?" and two answers would need an order the
    entity's state does not carry.

    A second *touch* and the *same* touch read identically from the port, so the
    difference is carried here: `note` records a user origin unless the entity is
    one whose override has already ended, and every non-user origin clears that
    memory. See the module docstring for why the distinction has to exist.
    """

    def __init__(
        self,
        records: Iterable[OverrideRecord] = (),
        lapsed: Iterable[str] = (),
    ) -> None:
        self._records: dict[str, OverrideRecord] = {
            record.entity_id: record for record in records
        }
        self._lapsed: set[str] = set(lapsed)

    def __len__(self) -> int:
        return len(self._records)

    def has_lapsed(self, entity_id: str) -> bool:
        """Whether `entity_id`'s override has ended and its touch still stands.

        True means the entity's last writer is still the user but the override
        that writer created is over -- so a user origin for it is not a new
        touch. Read by `note`, and public because a record needs it to explain
        why a later user origin recorded nothing.
        """
        return entity_id in self._lapsed

    def note(
        self,
        entity_id: str,
        *,
        origin: ChangeOrigin,
        at: datetime,
        duration: timedelta,
        room_id: str | None = None,
        mode: str | None = None,
    ) -> OverrideRecord | None:
        """Record an override if `origin` is a user's, and return what was recorded.

        Returning `None` rather than silently doing nothing is the point: a
        caller that believed it had overridden something can see that it had not.
        Two things return `None` -- an origin that is not the user's, and a user
        origin for an entity whose override has already ended -- and they are the
        same answer to the caller's question, "is there now an override this call
        created?". A non-user origin also clears the lapsed memory, which is the
        only thing that ends an entity's spell of being uncommandable.

        The duration is checked before either, so a caller passing a duration
        that cannot suppress anything hears about it whether or not the note
        would have recorded.
        """
        if duration <= timedelta(0):
            raise InvalidOverrideError(duration)
        if origin is not ChangeOrigin.USER:
            self._lapsed.discard(entity_id)
            return None
        if entity_id in self._lapsed:
            return None
        record = OverrideRecord(
            entity_id=entity_id,
            room_id=room_id,
            recorded_at=at,
            expires_at=at + duration,
            mode=mode,
        )
        self._records[entity_id] = record
        return record

    def is_overridden(self, entity_id: str) -> bool:
        """Whether the engine's commands to `entity_id` are suppressed.

        This answers "is a record in force", not "is it still current": an
        override whose `expires_at` has passed is still overridden until `lapse`
        is called, and the two are deliberately not the same question. Reading
        the clock here would make a *read* the thing that changes state, so an
        engine that took a snapshot between two reads would record an override
        the run had already ended -- and the engine is required to call `lapse`
        once per tick, which is where an elapsed override is ended and where the
        condition that ended it is decided and returned.
        """
        return entity_id in self._records

    def record_for(self, entity_id: str) -> OverrideRecord | None:
        """`entity_id`'s override, or `None`."""
        return self._records.get(entity_id)

    def awaited_for(self, entity_id: str) -> tuple[ResetCondition, ...]:
        """The conditions that would end `entity_id`'s override, or none if there is none.

        The three lapse conditions that can apply to *this* record, in
        `LAPSE_ORDER`, followed by `explicit_clear` -- which is a caller saying
        so rather than a condition that comes true, and so is always available.
        A house-scoped entity has no room and an override recorded under no mode
        has no mode to leave, so those two conditions are left out rather than
        listed as unreachable; a record naming a condition that cannot fire would
        be a record that cannot explain why nothing happened.
        """
        record = self._records.get(entity_id)
        if record is None:
            return ()
        applicable = [
            condition
            for condition in LAPSE_ORDER
            if not (
                (condition is ResetCondition.ROOM_EMPTIED and record.room_id is None)
                or (condition is ResetCondition.MODE_CHANGED and record.mode is None)
            )
        ]
        return (*applicable, ResetCondition.EXPLICIT_CLEAR)

    def records(self) -> tuple[OverrideRecord, ...]:
        """Every override in force, ordered by entity id.

        Also the form the snapshot carries (`simulation` lists "override
        records" among the state a restore resumes), so the order is fixed here
        rather than left to the dict's insertion order.
        """
        return tuple(self._records[entity_id] for entity_id in sorted(self._records))

    def clear(self, entity_id: str) -> bool:
        """Release an override through `explicit_clear`; whether one was in force.

        A clear of an entity that is not overridden reports `False` rather than
        raising, because a scenario step that clears an override twice is a
        scenario saying the same thing twice, not a broken one.

        An explicit clear is remembered like a lapse, and for the same reason: a
        clear that the next tick's user origin immediately undid would be a
        control that appears to work and does not.
        """
        if self._records.pop(entity_id, None) is None:
            return False
        self._lapsed.add(entity_id)
        return True

    def lapse(
        self,
        *,
        now: datetime,
        empty_rooms: Collection[str] = (),
        active_modes: Collection[str] = (),
    ) -> tuple[tuple[str, ResetCondition], ...]:
        """Release every override whose condition now holds.

        Each released override is returned with the condition that released it,
        ordered by entity id, so a caller writing a record per release writes
        them in the same sequence on every replay. The conditions are evaluated
        in `LAPSE_ORDER`'s order and the first that holds is the one reported.
        """
        released: list[tuple[str, ResetCondition]] = []
        for entity_id in sorted(self._records):
            record = self._records[entity_id]
            condition = _lapse_condition(
                record, now=now, empty_rooms=empty_rooms, active_modes=active_modes
            )
            if condition is not None:
                released.append((entity_id, condition))
        for entity_id, _ in released:
            del self._records[entity_id]
            self._lapsed.add(entity_id)
        return tuple(released)

    def to_document(self) -> Mapping[str, object]:
        """The registry's whole state, as the snapshot carries it.

        Both halves, because both are engine state a resumed run decides from: a
        restore that kept the records and lost the lapsed set would re-arm the
        overrides the run had ended, which is a divergence the replay property
        would catch and nothing here would explain.
        """
        return {
            "records": [
                {
                    "entity_id": record.entity_id,
                    "room_id": record.room_id,
                    "recorded_at": record.recorded_at.isoformat(),
                    "expires_at": record.expires_at.isoformat(),
                    "mode": record.mode,
                }
                for record in self.records()
            ],
            "lapsed": sorted(self._lapsed),
        }

    @classmethod
    def from_document(cls, document: object) -> OverrideRegistry:
        """Rebuild the registry from `to_document`'s form, naming what is wrong."""
        if not isinstance(document, Mapping):
            raise OverrideError(
                f"an override document must be an object, not {type(document).__name__}"
            )
        raw_records = document.get("records")
        if not isinstance(raw_records, Sequence) or isinstance(
            raw_records, (str, bytes)
        ):
            raise OverrideError("an override document must hold a 'records' list")
        raw_lapsed = document.get("lapsed")
        if not isinstance(raw_lapsed, Sequence) or isinstance(raw_lapsed, (str, bytes)):
            raise OverrideError("an override document must hold a 'lapsed' list")
        return cls(
            (_record_from_document(item) for item in raw_records),
            (_text(item, "lapsed") for item in raw_lapsed),
        )


def _record_from_document(item: object) -> OverrideRecord:
    """One record, rebuilt field by field with each field named on failure."""
    if not isinstance(item, Mapping):
        raise OverrideError(f"an override record must be an object, not {item!r}")
    room_id = item.get("room_id")
    mode = item.get("mode")
    return OverrideRecord(
        entity_id=_text(item.get("entity_id"), "entity_id"),
        room_id=None if room_id is None else _text(room_id, "room_id"),
        recorded_at=_instant(item.get("recorded_at"), "recorded_at"),
        expires_at=_instant(item.get("expires_at"), "expires_at"),
        mode=None if mode is None else _text(mode, "mode"),
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise OverrideError(f"an override {field} must be a string, not {value!r}")
    return value


def _instant(value: object, field: str) -> datetime:
    try:
        return datetime.fromisoformat(_text(value, field))
    except ValueError as error:
        raise OverrideError(f"an override {field} must be a timestamp") from error


def _lapse_condition(
    record: OverrideRecord,
    *,
    now: datetime,
    empty_rooms: Collection[str],
    active_modes: Collection[str],
) -> ResetCondition | None:
    """Which of the lapse conditions holds for `record`, or `None`.

    The order is `LAPSE_ORDER`'s and the first match wins, so an override that a
    timeout and an emptying would both release reports the timeout -- the
    condition that was being awaited when the lapse was evaluated, rather than
    whichever the caller happened to check first.
    """
    for condition in LAPSE_ORDER:
        if condition is ResetCondition.OVERRIDE_TIMEOUT and now >= record.expires_at:
            return condition
        if (
            condition is ResetCondition.ROOM_EMPTIED
            and record.room_id is not None
            and record.room_id in empty_rooms
        ):
            return condition
        if (
            condition is ResetCondition.MODE_CHANGED
            and record.mode is not None
            and record.mode not in active_modes
        ):
            return condition
    return None
