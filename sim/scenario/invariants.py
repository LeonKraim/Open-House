"""The phase's invariants: six claims about a running engine, each falsifiable.

Each is a property over the *whole stack agreeing* -- the engine, the fake and the
log -- rather than over any one of them, because that is the seam a unit test
cannot reach: a behaviour that acts on an unbound slot is a correct behaviour and
a correct resolver and a wrong pair. So every check below opens a session, drives
it through the control surface, and reads what the engine wrote
(`scenario-runner`).

**A claim and its input are separate things, and the split is here.** This module
owns the six claims. It does not own the sessions they are made about, because
the session a claim needs is the one whose seed, enabled behaviours and house the
*falsifying input* is built from -- and building one means choosing a repository
root and reaching into `engine/`, neither of which a `sim/` module may do
(`design.md` D12). So a check takes a `Bench`: a factory that opens sessions, the
script to drive them with, and the one engine-owned fact the claims need and this
package may not import.

**Nothing here imports Hypothesis.** A property is a function that raises when it
does not hold, and a test framework is how a *suite* calls one; importing it here
would make the invariants unreachable from anything that is not a test run, and
would put a testing dependency on the import path of a package the CLI loads. The
suite decorates these with `@given` and, for each, runs the sabotaged `Bench` its
`falsified_by` names -- so a claim that cannot fail is caught rather than
trusted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .report import plain

if TYPE_CHECKING:
    from datetime import datetime

    from .runner import SessionFactory
    from .surface import ScenarioSurface

__all__ = [
    "DETERMINISTIC_REPLAY",
    "INVARIANTS",
    "INVARIANTS_BY_NAME",
    "NO_NON_USER_EGRESS",
    "NO_UNBOUND_REQUIRED_ACTION",
    "RESTORE_REPLAY_IDENTITY",
    "SINGLE_COMMAND_PER_ENTITY",
    "ZERO_ADVANCE_IS_NOOP",
    "Bench",
    "Invariant",
    "InvariantViolated",
    "Script",
    "Step",
]

DETERMINISTIC_REPLAY = "deterministic_replay"
RESTORE_REPLAY_IDENTITY = "restore_replay_identity"
NO_NON_USER_EGRESS = "no_non_user_egress"
NO_UNBOUND_REQUIRED_ACTION = "no_unbound_required_action"
ZERO_ADVANCE_IS_NOOP = "zero_advance_is_noop"
SINGLE_COMMAND_PER_ENTITY = "single_command_per_entity"

#: The record outcomes this module names. Spelled as the strings a record's
#: document carries rather than as `engine.decision_log.Outcome` members, because
#: the members live in `engine/` and this package may not import them -- and
#: because the checks read a record's *document*, so the string is what is there.
ACTED = "acted"
SKIPPED_UNBOUND_SLOT = "skipped: unbound slot"
USER = "user"

#: The two fields a record's document spells with a list of strings.
COMMANDS = "commands"
DELTAS = "state_delta"

#: One step of a drive: a single control-surface call, closed over its arguments.
Step = Callable[["ScenarioSurface"], None]

#: A drive, in order. A sequence rather than one callable because
#: `restore_replay_identity` has to stop halfway, take a snapshot and continue --
#: and a drive that could not be divided could not express the property it is the
#: input to.
Script = Sequence[Step]


class InvariantViolated(Exception):  # noqa: N818  (a claim that failed, not an error class)
    """A claim that did not hold, naming the claim and what disagreed.

    The name travels on the exception as well as in the message, because the
    suite reports which invariant a counterexample belongs to and a report that
    had to parse the sentence for it would be parsing prose to get an identifier.
    """

    def __init__(self, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail
        super().__init__(f"the invariant {name!r} does not hold: {detail}")


@dataclass(frozen=True, slots=True)
class Bench:
    """What a claim is made about: a session, a drive, and one engine fact.

    `egress` is `(entity_id, action) -> whether writing that action to that entity
    is an unlock or an open`. It is handed in rather than restated because the
    rule lives in `engine/safety.py` and this package may not import it
    (`scenario-runner`), and a second copy of the domain-and-action table would be
    a second answer to "is this an egress action" -- which is exactly the kind of
    second opinion the phase removes everywhere else, and worse here than
    elsewhere, because the copy that drifted would be the one guarding the
    product rule.
    """

    session: SessionFactory
    script: Script
    egress: Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class Invariant:
    """One claim, its statement, the input that falsifies it, and its check.

    `falsified_by` is prose rather than a value because the input is a `Bench`
    the suite builds out of `engine/`'s own objects -- a behaviour that proposes
    an unlock, a house with a slot left unbound -- and this module may not
    construct one. Naming it here is what makes the suite's obligation checkable:
    every name in `INVARIANTS` has a falsifier in the suite, and the
    requirement-to-check mapping fails on one that does not.
    """

    name: str
    statement: str
    falsified_by: str
    check: Callable[[Bench], None]

    def __call__(self, bench: Bench) -> None:
        """Evaluate the claim, raising `InvariantViolated` when it does not hold.

        A check that fails for a reason other than the claim -- a fixture that
        will not build, a script that names an entity the house does not hold --
        is reported as this invariant failing rather than as that exception
        escaping. The difference matters to a reader: an escaping traceback says
        "the harness is broken" and stops the suite, while a violation names the
        claim and lets the other five be evaluated.
        """
        try:
            self.check(bench)
        except InvariantViolated:
            raise
        except Exception as error:
            raise InvariantViolated(
                self.name,
                f"its check raised {type(error).__name__}: {error}",
            ) from error


def _deterministic_replay(bench: Bench) -> None:
    """Two runs of one drive at one seed decide the same, device and record.

    Two sessions rather than one session driven twice, because the claim is about
    the *substrate*: a single session carries its own clock and stream state, and
    a second drive over the same object would be a continuation rather than a
    replay. What the factory returns the second time is a fresh session opened at
    the same seed and instant -- which is exactly what a replay is.
    """
    first = _observation(_driven(bench.session, bench.script))
    second = _observation(_driven(bench.session, bench.script))
    _require(DETERMINISTIC_REPLAY, "the device states", first[DEVICES], second[DEVICES])
    _require(
        DETERMINISTIC_REPLAY, "the decision records", first[RECORDS], second[RECORDS]
    )


def _restore_replay_identity(bench: Bench) -> None:
    """A run paused, snapshotted and resumed decides what the run that never stopped did.

    One session runs the whole drive; another runs the first half, is snapshotted,
    and a third is restored from that document and runs the second half. What is
    compared is the two ends: the device states, and the records the resumed run
    decided against the uninterrupted run's records *after the instant the
    snapshot was taken*. The second comparison is the one that catches an
    incomplete snapshot -- a document that lost the random stream's position
    replays its decisions differently and the records diverge even where the
    devices happen to agree.

    The instant is where the pause was, so the half-open comparison is right at
    the seam: the last record of the first half is *at* that instant and belongs
    to the run before the restore, so the tail starts strictly after it.
    """
    whole = _driven(bench.session, bench.script)
    split = len(bench.script) // 2
    paused = _driven(bench.session, bench.script[:split])
    document = paused.snapshot()
    instant = paused.now()
    resumed = bench.session()
    resumed.restore(document)
    _run(resumed, bench.script[split:])
    _require(
        RESTORE_REPLAY_IDENTITY,
        "the device states",
        _device_states(whole),
        _device_states(resumed),
    )
    _require(
        RESTORE_REPLAY_IDENTITY,
        "the decisions after the resume point",
        _after(_records(whole), instant),
        _records(resumed),
    )


def _no_non_user_egress(bench: Bench) -> None:
    """No applied command unlocks a lock or opens a cover under a non-user origin.

    Read off the records rather than off the device, and that is the point: a door
    that stayed shut looks the same whether the engine refused to open it or never
    proposed to, and only the record says which. The claim is about what was
    *applied*, so a `refused: unsafe` record -- the gate doing its job -- is not a
    violation of it; the violation is an `acted` record carrying an egress command
    whose command origin is anything but the user's.
    """
    for record in _records(_driven(bench.session, bench.script)):
        if record.get("outcome") != ACTED:
            continue
        for command in _mappings(record, COMMANDS):
            action = command.get("action")
            if not isinstance(action, str):
                continue
            origin = str(command.get("origin"))
            if origin == USER:
                continue
            for entity_id in _strings(command, "entities"):
                if bench.egress(entity_id, action):
                    raise InvariantViolated(
                        NO_NON_USER_EGRESS,
                        f"the record by {record.get('actor')!r} applied {action!r} to "
                        f"{entity_id!r}, which is an egress action, under the origin "
                        f"{origin!r}; only a change the user made themselves may",
                    )


def _no_unbound_required_action(bench: Bench) -> None:
    """An evaluation skipped for an unbound slot applied nothing.

    The gate that skips a behaviour whose required slot resolves empty runs before
    any rule, so its record carries no command and no state change -- that is the
    representation saying "this did not happen", and a skip that carried either
    would be the engine reporting an action it did not take. A behaviour that
    acted anyway would not record a skip at all, which is why this reads the
    records that *are* skips rather than the houses that are unbound.
    """
    for record in _records(_driven(bench.session, bench.script)):
        if record.get("outcome") != SKIPPED_UNBOUND_SLOT:
            continue
        commands = _mappings(record, COMMANDS)
        deltas = _mappings(record, DELTAS)
        if commands or deltas:
            raise InvariantViolated(
                NO_UNBOUND_REQUIRED_ACTION,
                f"the record by {record.get('actor')!r} skipped for an unbound slot and "
                f"still carries {len(commands)} command(s) and {len(deltas)} state "
                "change(s); a skip is not an act",
            )


def _zero_advance_is_noop(bench: Bench) -> None:
    """Advancing the clock by zero ticks nothing: no command, no decision, no record.

    The drive runs first so that the zero advance is a claim about a *live* house
    with enabled behaviours and pending timers rather than about a house in which
    nothing could have happened anyway. What is compared is the whole observation
    both ways, so a tick that decided only `declined` -- which changes no device --
    is caught by the record comparison and not excused by the device one.
    """
    surface = _driven(bench.session, bench.script)
    before = _observation(surface)
    surface.advance_time(minutes=0)
    after = _observation(surface)
    _require(ZERO_ADVANCE_IS_NOOP, "the device states", before[DEVICES], after[DEVICES])
    _require(
        ZERO_ADVANCE_IS_NOOP, "the decision records", before[RECORDS], after[RECORDS]
    )


def _single_command_per_entity(bench: Bench) -> None:
    """Two behaviours proposing to one entity in one tick apply one command, not two.

    Counted from `state_delta` rather than from `commands`, and the distinction is
    load-bearing: a command is a *proposal*, and a draft that proposed to three
    entities and won two carries all three in its record while its delta names the
    two that were written. The delta is the only field that says what was applied,
    so it is the only field a claim about how many commands were applied can be
    counted from.

    The instant is the tick key: every record an evaluation leaves carries the
    instant it was evaluated at, and two proposals in one tick carry the same one.
    """
    applied: dict[tuple[str, str], int] = {}
    for record in _records(_driven(bench.session, bench.script)):
        instant = str(record.get("at", ""))
        for change in _mappings(record, DELTAS):
            entity_id = change.get("entity_id")
            if isinstance(entity_id, str):
                key = (instant, entity_id)
                applied[key] = applied.get(key, 0) + 1
    for (instant, entity_id), count in sorted(applied.items()):
        if count > 1:
            raise InvariantViolated(
                SINGLE_COMMAND_PER_ENTITY,
                f"{count} commands were applied to {entity_id!r} at {instant}; "
                "arbitration must settle a tick's contention to one",
            )


#: The field a run's devices are compared on, and the field its decisions are.
#: Named rather than written twice, because the two comparisons in a run and the
#: two in a zero advance have to be about the same two things.
DEVICES = "devices"
RECORDS = "records"


def _driven(session: SessionFactory, script: Script) -> ScenarioSurface:
    """A session opened by `session` with `script` run against it."""
    surface = session()
    _run(surface, script)
    return surface


def _run(surface: ScenarioSurface, script: Script) -> None:
    """Every step of `script`, in order. A step is one surface call and nothing else."""
    for step in script:
        step(surface)


def _observation(surface: ScenarioSurface) -> Mapping[str, object]:
    """What two runs are compared on: the device states and the decision records.

    The spec's two, and deliberately only those two. A whole-snapshot comparison
    would also cover the clock, the stream's position and the engine's own
    registries -- all of which should agree for two runs at one seed -- but it
    would report a disagreement in any of them as a disagreement about the
    devices, and a reader of a counterexample needs to be told which thing
    differed.
    """
    return {DEVICES: _device_states(surface), RECORDS: _records(surface)}


def _device_states(surface: ScenarioSurface) -> object:
    """Every device's live state, its attributes and its availability, as a document.

    Read from the snapshot rather than accumulated by reading each entity: the
    snapshot's `entities` slice *is* the port's own account of every device it
    holds (`sim/snapshot.py`), so this needs no listing operation the facade does
    not have and cannot miss a device the drive never touched.
    """
    return plain(surface.snapshot().get("entities"))


def _records(surface: ScenarioSurface) -> list[Mapping[str, object]]:
    """The retained decision log, in the engine's own document form, oldest first."""
    return [record.to_document() for record in surface.get_decision_log()]


def _after(
    records: Sequence[Mapping[str, object]], instant: datetime
) -> list[Mapping[str, object]]:
    """The records written strictly after `instant`.

    Compared as ISO strings rather than parsed back into instants: every record's
    `at` and the snapshot's instant come from the same clock and are written by
    the same ISO 8601 rule, so for one run the lexical order is the chronological
    one -- and parsing here would be a second reading of a format the engine owns.
    """
    moment = instant.isoformat()
    return [record for record in records if str(record.get("at", "")) > moment]


def _mappings(record: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    """A record's list of documents under `field`, or empty when it has none."""
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [
        cast("Mapping[str, object]", item)
        for item in cast("list[object]", value)
        if isinstance(item, dict)
    ]


def _strings(record: Mapping[str, object], field: str) -> list[str]:
    """A record's list of strings under `field`, or empty when it has none."""
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [item for item in cast("list[object]", value) if isinstance(item, str)]


def _require(name: str, what: str, left: object, right: object) -> None:
    """Fail unless the two are equal, naming what they were and how they differed."""
    if left == right:
        return
    raise InvariantViolated(name, f"{what} differ: {_first_difference(left, right)}")


def _first_difference(left: object, right: object) -> str:
    """Where two values first differ, with an index when both are lists.

    An index and not the whole values: a disagreement between two device states is
    usually one device, and a counterexample that printed two full houses would
    bury the one line that matters. The values are still printed for the pair that
    disagreed, because a reader needs to see both sides of it.
    """
    one = _as_list(left)
    other = _as_list(right)
    if one is not None and other is not None:
        for index, (first, second) in enumerate(zip(one, other, strict=False)):
            if first != second:
                return f"at index {index}: {_short(first)} != {_short(second)}"
        return f"the lengths differ: {len(one)} and {len(other)}"
    return f"{_short(left)} != {_short(right)}"


def _as_list(value: object) -> list[object] | None:
    """`value` as a list, or `None` when it is not one."""
    if not isinstance(value, list):
        return None
    return cast("list[object]", value)


def _short(value: object) -> str:
    """A value as a report line can hold it."""
    text = repr(plain(value))
    return text if len(text) <= 160 else f"{text[:157]}..."


#: The six claims, in the order `scenario-runner` lists them. The tuple is the
#: set: the suite checks that every name here has a falsifier and that the
#: requirement-to-check mapping names every one of them, so an invariant cannot be
#: dropped from one place and kept in another.
INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        name=DETERMINISTIC_REPLAY,
        statement=(
            "a run replayed at its seed and starting instant produces identical "
            "device states and identical decision records"
        ),
        falsified_by=(
            "a session factory that opens the second session at a different seed, so "
            "the two runs draw from different streams"
        ),
        check=_deterministic_replay,
    ),
    Invariant(
        name=RESTORE_REPLAY_IDENTITY,
        statement=(
            "a snapshot taken mid-run, restored and continued produces the decisions "
            "the uninterrupted run produced"
        ),
        falsified_by=(
            "a snapshot document with the random stream's position removed, so the "
            "resumed run draws a stream the interrupted one would not have"
        ),
        check=_restore_replay_identity,
    ),
    Invariant(
        name=NO_NON_USER_EGRESS,
        statement=(
            "no applied command unlocks a lock or opens a cover unless its command "
            "origin is the user's own"
        ),
        falsified_by=(
            "an egress oracle that calls an ordinary command one, so a command the "
            "gate allowed under a non-user origin is read as an unlock -- no shipped "
            "unit proposes to a lock or a cover in this phase, and the oracle is what "
            "the claim reads"
        ),
        check=_no_non_user_egress,
    ),
    Invariant(
        name=NO_UNBOUND_REQUIRED_ACTION,
        statement=(
            "an evaluation skipped for an unbound required slot applies no command and "
            "changes no state"
        ),
        falsified_by=(
            "a record that skipped for an unbound slot and still carries a command and "
            "a state change -- the engine will not write one, so the suite hands the "
            "claim a session that answers with it"
        ),
        check=_no_unbound_required_action,
    ),
    Invariant(
        name=ZERO_ADVANCE_IS_NOOP,
        statement=(
            "an advance_time of zero produces no tick, no command and no decision record"
        ),
        falsified_by="a clock that moves on a zero advance, expected to tick",
        check=_zero_advance_is_noop,
    ),
    Invariant(
        name=SINGLE_COMMAND_PER_ENTITY,
        statement=(
            "two evaluations proposing commands to one entity in one tick yield exactly "
            "one applied command"
        ),
        falsified_by=(
            "two records applying a command to one entity at one instant, which is what "
            "two behaviours contending over an entity leave behind when arbitration "
            "does not settle it"
        ),
        check=_single_command_per_entity,
    ),
)

#: The claims by name, so a suite or a mapping document can reach one without
#: depending on the order above.
INVARIANTS_BY_NAME: Mapping[str, Invariant] = {
    invariant.name: invariant for invariant in INVARIANTS
}
