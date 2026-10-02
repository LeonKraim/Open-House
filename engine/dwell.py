"""How long a room has been quiet, which is a duration nothing else can answer.

Two requirements need the same number and neither can derive it from the port.
`first-behaviours` says motion lighting turns a room's light off "once the room
has been clear of motion for the configured quiet timeout"; `engine-core` says an
override lapses when "the room's motion is clear for the quiet timeout". Both are
statements about how long a *reading has held*, and the `HouseAdapter` port
carries no time source (`house-adapter`) -- an entity has a state, not a
`last_changed` -- so the instant a room last read as occupied is a fact the
engine observed and has to keep.

Three consequences follow, and each is why this module is a registry rather than
a dictionary of timestamps:

- **The clock is an argument, never read.** `observe` is handed the tick's
  instant, so the whole module is a pure function of its inputs and a replay at
  the same clock reaches the same quiet rooms. Nothing here can drift with the
  wall clock because nothing here can see one.
- **A room that has never read as occupied is quiet from the run's start, not
  from the first tick.** `observe(active=False)` starts the clock the first time
  it sees a clear reading; a room no sensor covers is absent from the registry
  entirely, which is *not* the same fact -- `quiet` is false for an unobserved
  slot, because "we do not know" must not read as "empty" and let a shutdown fire
  on a room nothing is watching.
- **It is engine state, and it is snapshotted.** `sim/snapshot.py` enumerates the
  runtime state a run resumes from; a quiet timer left out of that document would
  make a restored run's first tick differ from the run that was never
  interrupted, which is exactly what the restore-and-replay property forbids.

The registry's document form is its own (`to_document`/`from_document`) rather
than the raw records, because the snapshot is JSON and a `datetime` is not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast


class DwellError(Exception):
    """Base for the failures this module defines."""


@dataclass(frozen=True, slots=True)
class Dwell:
    """Since when a slot in a room has been reading as not occupied.

    `since` is the last instant the slot read as *active*. It therefore moves
    forward every time motion is seen and stands still while the room is clear,
    so `now - since` is the length of the current quiet period and the tick that
    sees motion again resets it to zero.

    `known` is whether the last reading could be trusted at all. A sensor that
    has gone unavailable reads neither active nor clear -- it is *unreadable* --
    and the two mistakes a house makes with such a reading are opposite and both
    wrong: calling it clear empties a room nobody can see, and calling it active
    holds a room lit forever. `known=False` is the third answer, and it makes
    `quiet` refuse to answer rather than guess, which is `house-adapter`'s
    "unavailable is not off" reaching the one duration the port cannot report.
    """

    room_id: str
    slot: str
    since: datetime
    #: False when the last reading was unreadable. Defaults True so a record
    #: written before this field existed, and every ordinary observation, reads
    #: as a reading that could be trusted.
    known: bool = True
    #: When the reading last *changed value*. The second duration this registry
    #: answers, and a different one from `since`: `since` moves whenever the slot
    #: reads active, while `held` stands still until the reading becomes a
    #: *different* reading. `pack-manifest/1.3.0`'s `for` clause asks this
    #: question -- "the fridge has read open for a quarter of an hour" -- and
    #: `since` cannot answer it, because a contact that has read `on` for an hour
    #: and one that flicked to `on` a second ago have the same `since` only when
    #: neither has changed, which is exactly the case the clause is not about.
    #: `None` for a record observed before this field existed, or by a caller
    #: that named no reading (the presence path, which asks a boolean question
    #: and has no state string to hold).
    held: datetime | None = None
    #: The reading that `held` is the age of, in the one string form a caller
    #: supplies (`engine/engine.py`'s `observe`). Kept beside `held` because a
    #: duration with no value beside it could not tell "the door has been open
    #: for an hour" from "the door has been shut for an hour".
    reading: str | None = None


class DwellRegistry:
    """The quiet period of every (room, slot) the engine has observed.

    One entry per room and slot rather than per room, because the two callers
    read different things -- motion lighting watches the room's `motion_sensor`,
    a presence-driven rule might watch a `door_contact` -- and a registry keyed
    by room alone would silently make the second caller's observations overwrite
    the first's.
    """

    def __init__(self, records: Iterable[Dwell] = ()) -> None:
        self._records: dict[tuple[str, str], Dwell] = {
            (record.room_id, record.slot): record for record in records
        }

    def __len__(self) -> int:
        """How many (room, slot) pairs have been observed."""
        return len(self._records)

    def observe(
        self,
        room_id: str,
        slot: str,
        *,
        active: bool,
        at: datetime,
        known: bool = True,
        reading: str | None = None,
    ) -> Dwell:
        """Record this tick's reading, and return the quiet period it defines.

        An active reading restarts the period at `at`; a clear reading leaves an
        existing period alone and starts one at `at` if there is none. The second
        half is what makes a room nobody has walked into yet eventually go quiet
        rather than staying unobserved forever.

        An unreadable reading (`known=False`) neither restarts nor advances the
        period: the instant the last *known* reading stood at is kept and the
        record is marked unreadable, so `quiet` answers "we do not know" until a
        readable reading arrives. Keeping `since` rather than resetting it means
        the quiet time is never restarted by a fault, so a sensor that comes back
        after a long outage does not hand the room a fresh occupancy it never had.

        `reading` is the optional second question -- which value the slot holds
        and since when -- and it is supplied only by callers that have a state
        string to give. A reading equal to the last one leaves `held` where it
        was; a *different* one moves it to `at`; the first one a slot is ever
        observed holding sets `held` to `at` too, because the engine does not know
        how long it had been so before it looked. An unreadable observation
        advances neither, on the same reasoning that keeps `since` still: a
        sensor that cannot be read is not a sensor reporting a change.
        """
        key = (room_id, slot)
        existing = self._records.get(key)
        held, held_reading = _held(existing, at=at, known=known, reading=reading)
        if not known:
            since = at if existing is None else existing.since
            record = Dwell(
                room_id=room_id,
                slot=slot,
                since=since,
                known=False,
                held=held,
                reading=held_reading,
            )
        elif active or existing is None:
            record = Dwell(
                room_id=room_id,
                slot=slot,
                since=at,
                known=True,
                held=held,
                reading=held_reading,
            )
        else:
            record = Dwell(
                room_id=room_id,
                slot=slot,
                since=existing.since,
                known=True,
                held=held,
                reading=held_reading,
            )
        self._records[key] = record
        return record

    def held_for(self, room_id: str, slot: str, *, at: datetime) -> timedelta | None:
        """How long the slot has held its current reading, or `None` if unknown.

        `None` rather than zero for a slot nothing has observed with a reading,
        for the reason `quiet` answers false for one nothing has observed at all:
        "the door has been open for a while" must not be manufactured out of a
        reading nobody took. A duration of zero is a real answer -- the reading
        changed on this very tick -- and it is the one that stops a `for` clause
        firing the instant a door opens.
        """
        record = self._records.get((room_id, slot))
        if record is None or record.held is None:
            return None
        return at - record.held

    def since(self, room_id: str, slot: str) -> datetime | None:
        """When the slot last read as active, or `None` if never observed."""
        record = self._records.get((room_id, slot))
        return None if record is None else record.since

    def quiet(
        self, room_id: str, slot: str, *, at: datetime, timeout: timedelta
    ) -> bool:
        """Whether the slot has been clear for at least `timeout`.

        `at` is inclusive of the boundary: a room observed clear at `t` is quiet
        at `t + timeout`, which is what makes "advance by exactly the timeout"
        the scenario that turns the light off rather than one that almost does.
        An unobserved slot is *not* quiet -- see the module docstring -- and
        neither is one whose last reading was unreadable: a dead sensor is not a
        clear room, so the answer is "no" rather than "yes" until a reading
        arrives that can be trusted.
        """
        record = self._records.get((room_id, slot))
        if record is None or not record.known:
            return False
        return at - record.since >= timeout

    def quiet_rooms(
        self, *, slot: str, at: datetime, timeout: timedelta
    ) -> tuple[str, ...]:
        """Every room whose `slot` has been clear for `timeout`, ordered by id.

        Ordered rather than set-like because the caller writes records from it,
        and two runs of one scenario must write them in the same sequence.
        """
        return tuple(
            sorted(
                room_id
                for room_id, observed_slot in self._records
                if observed_slot == slot
                and self.quiet(room_id, slot, at=at, timeout=timeout)
            )
        )

    def records(self) -> tuple[Dwell, ...]:
        """Every observed period, ordered by room id then slot."""
        return tuple(self._records[key] for key in sorted(self._records))

    def to_document(self) -> Sequence[Mapping[str, object]]:
        """The JSON-serialisable form the snapshot carries."""
        return [
            {
                "room_id": record.room_id,
                "slot": record.slot,
                "since": record.since.isoformat(),
                "known": record.known,
                # The duration half, written even when unknown: a key that
                # appeared only sometimes would make the document's shape depend
                # on the run, and a snapshot reader compares documents.
                "held": None if record.held is None else record.held.isoformat(),
                "reading": record.reading,
            }
            for record in self.records()
        ]

    @classmethod
    def from_document(cls, document: object) -> DwellRegistry:
        """Rebuild from `to_document`'s form, failing by naming a bad row."""
        if not isinstance(document, list):
            raise DwellError(f"a dwell document must be a list, not {document!r}")
        rows = cast("list[object]", document)
        records: list[Dwell] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise DwellError(f"dwell row {index} is not an object: {row!r}")
            fields = cast("Mapping[str, object]", row)
            records.append(
                Dwell(
                    room_id=_str(fields, "room_id", index),
                    slot=_str(fields, "slot", index),
                    since=_timestamp(fields, "since", index),
                    known=_bool(fields, "known", index),
                    held=_optional_timestamp(fields, "held", index),
                    reading=_optional_str(fields, "reading", index),
                )
            )
        return cls(records)


def _held(
    existing: Dwell | None, *, at: datetime, known: bool, reading: str | None
) -> tuple[datetime | None, str | None]:
    """The `held` instant and reading an observation defines.

    Four cases, and the two that are easy to get wrong are named here rather than
    left to the reader of `observe`:

    - **Nothing to report** (`reading is None`) keeps whatever the record already
      held, so a caller asking the boolean question does not erase the answer to
      the duration one.
    - **Unreadable** keeps it too: a dead sensor reports no change, and a `for`
      clause must not fire because a contact flicked to unavailable.
    - **The same reading** stands still, which is the whole point.
    - **A different reading**, and the first one ever seen, both set `at`.
    """
    if reading is None or not known:
        return (None, None) if existing is None else (existing.held, existing.reading)
    if existing is not None and existing.reading == reading:
        return (existing.held if existing.held is not None else at, reading)
    return (at, reading)


def _str(fields: Mapping[str, object], key: str, index: int) -> str:
    value = fields.get(key)
    if not isinstance(value, str):
        raise DwellError(f"dwell row {index} has no string {key!r}: {value!r}")
    return value


def _bool(fields: Mapping[str, object], key: str, index: int) -> bool:
    """A row's `known` flag, defaulting to `True` when the document omits it.

    Defaulting rather than requiring is what keeps a snapshot written before this
    field existed restorable: a record with no `known` is the ordinary reading,
    and refusing it would make a version bump of the dwell document a version bump
    of every snapshot. A present but non-boolean value is still refused, because
    that is a corrupt row and not an old one.
    """
    value = fields.get(key, True)
    if not isinstance(value, bool):
        raise DwellError(f"dwell row {index} has a non-boolean {key!r}: {value!r}")
    return value


def _optional_str(fields: Mapping[str, object], key: str, index: int) -> str | None:
    """A row's optional string, where absent and null both mean "not reported".

    Absent is what a snapshot written before the field existed has, and null is
    what the current writer emits for a record it observed no reading for. The
    two mean the same thing to every reader here, so they are collapsed rather
    than distinguished -- unlike a present-but-wrong value, which is refused.
    """
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DwellError(f"dwell row {index} has a non-string {key!r}: {value!r}")
    return value


def _optional_timestamp(
    fields: Mapping[str, object], key: str, index: int
) -> datetime | None:
    """A row's optional instant, absent or null both reading as unknown."""
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DwellError(f"dwell row {index} has a non-string {key!r}: {value!r}")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise DwellError(
            f"dwell row {index} has an unreadable {key!r}: {value!r}"
        ) from error


def _timestamp(fields: Mapping[str, object], key: str, index: int) -> datetime:
    value = fields.get(key)
    if not isinstance(value, str):
        raise DwellError(f"dwell row {index} has no string {key!r}: {value!r}")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise DwellError(
            f"dwell row {index} has an unreadable {key!r}: {value!r}"
        ) from error
