"""The decision log: one normative record per evaluation, bounded (tasks 4.0, 4.1).

The log is not logging. It is the artifact a scenario asserts against and an
agent reads, so its field set and its outcome vocabulary are normative in exactly
the way a device's state is (`design.md` D2). A state-only suite cannot tell "off
because the quiet timeout behaved" from "off because the light was never on", and
that difference is what every override, rate-limit and arbitration rule in
`engine-core` is about -- so the record carries the outcome, the rule that was
matched, the inputs that were read and the change that was applied, and a record
that cannot carry them fails rather than being written.

Three consequences of taking the log seriously are visible in this module:

- **`Outcome` is closed at eight values**, including `declined`, and a record
  whose outcome is not one of them cannot be constructed. An outcome outside the
  set is not an unknown state to be tolerated; it is a record the oracle cannot
  read, and the phase's own test asserts every one of the eight is producible by
  some evaluation.
- **`ProposedCommand` lives here.** Its shape is normative because a record's
  `commands` field is where it is named, and the three places that touch a
  command -- the behaviour that proposes it, arbitration, which reduces several
  to one, and this record -- would otherwise each be importing it from one of the
  others.
- **The log is bounded and has no snapshot field.** Records are retained for
  assertion, so an unbounded log is a memory sink on a long run; the bound is
  supplied at construction, and `engine/engine.py` resolves it through the
  `ConfigResolver` under `config.LOG_BOUND_KEY` when it builds the log -- this
  module takes the number and never reads a layer for it. Nothing here appears in
  `sim/snapshot.py`'s document, because state is history-independent and history
  is not: restoring history would make a replayed run's log depend on the run
  that preceded it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from engine.adapter import ChangeContext
from engine.binding import SlotRead
from engine.config import ResolvedSetting
from engine.overrides import ResetCondition


class DecisionLogError(Exception):
    """Base for the failures this module defines."""


class InvalidOutcomeError(DecisionLogError):
    """A record whose outcome is not one of the eight closed values."""

    def __init__(self, outcome: object) -> None:
        super().__init__(
            f"{outcome!r} is not one of the outcomes a record may carry: "
            f"{sorted(str(member) for member in Outcome)}"
        )
        self.outcome = outcome


class Outcome(StrEnum):
    """The closed set of dispositions one evaluation can have.

    The first five are what happened *to* an evaluation: it acted, it declined,
    or it was suppressed by arbitration, by an override, or by a rate limit. The
    last four record an evaluation that never reached a decision -- a skip ahead
    of any rule, or a refusal by the safety gate. They are one enum rather than
    several because a record carries exactly one of them and a closed set is what
    makes "no outcome nothing emits" checkable.

    `SKIPPED_SUPPRESSED` is a skip like the other two and not a suppression like
    the first five: a module another module has switched off never reaches its
    own gate, so its atoms leave a record saying so rather than one saying
    "disabled" -- the person reading the log asked why the lights did not come on,
    and the answer "another module is holding this one off" is a different answer
    from "you switched it off".
    """

    ACTED = "acted"
    DECLINED = "declined"
    LOST_ARBITRATION = "lost arbitration"
    OVERRIDDEN = "overridden"
    RATE_LIMITED = "rate-limited"
    SKIPPED_UNBOUND_SLOT = "skipped: unbound slot"
    SKIPPED_DISABLED = "skipped: disabled"
    SKIPPED_SUPPRESSED = "skipped: suppressed"
    REFUSED_UNSAFE = "refused: unsafe"


@dataclass(frozen=True, slots=True)
class ProposedCommand:
    """A command an evaluation proposed, before arbitration reduces it.

    `action` is what the command asks for -- in this phase, the state to write,
    which is what the port's `actuate` takes -- and `context` is the change
    origin the command would be applied under, so a command a user's action
    outranks is distinguishable in the record from one the engine proposed.

    `safety` marks a command that answers a hazard alert. A safety command is the
    one command the engine refuses to suppress: it outranks every behaviour in
    arbitration and it is admitted whatever an override or a rate limit says
    (`engine/engine.py`). The flag is on the *command* rather than on the
    behaviour that proposed it, so a unit that is ordinarily suppressed can raise
    one urgent command without becoming a unit that can never be suppressed.
    """

    slot: str
    entities: tuple[str, ...]
    action: str
    context: ChangeContext
    #: True for a command answering a smoke, CO or leak alert.
    safety: bool = False


@dataclass(frozen=True, slots=True)
class StateChange:
    """One entity's before and after, as a record's `state_delta` holds them.

    A tuple of these rather than a mapping, so a record is immutable and the
    order an evaluation applied its changes in is preserved.
    """

    entity_id: str
    before: str
    after: str


class DarkSource(StrEnum):
    """Which dark test decided a lighting evaluation.

    The closed set is two because `first-behaviours` names exactly two: the
    room's bound `ambient_light_sensor`, or the computed sun position when no lux sensor is
    bound. It is an enum rather than a bare string so a scenario asserting
    `dark_source: lux` is asserting against a value the engine cannot misspell,
    and so a third branch added later is a change to this set rather than a
    string nobody notices appear.
    """

    LUX = "lux"
    SUN = "sun"


@dataclass(frozen=True, slots=True)
class DarkSourceReading:
    """Which dark test decided, and the number it read.

    Not a `SlotRead`, because the sun branch has no slot behind it -- it is a
    value computed from the virtual clock and the fixture's location, and the
    record has to be able to say so. `first-behaviours` requires this as a named
    input: on the no-lux fixture the same light turns on for a different reason,
    and which reason is what a scenario asserts.

    `value` is lux for the `lux` source and degrees of elevation for `sun`; the
    source says which, so one field carries both rather than two nullable ones
    where exactly one is set.
    """

    source: DarkSource
    value: float


@dataclass(frozen=True, slots=True)
class ModeReading:
    """Whether a mode gated an evaluation, and which way the gate fell.

    `engine-core` requires a behaviour to be able to gate on a mode and requires
    a mode gate that is unmet to produce `declined` rather than a skip, and
    `first-behaviours` requires the shutdown's record to name `house_away` as the
    mode that gated it. Both are satisfied by recording the gate itself, which is
    why the context records this automatically rather than leaving the behaviour
    to remember: a mode gate nobody wrote down is a gate the log cannot explain.
    """

    mode: str
    active: bool


@dataclass(frozen=True, slots=True)
class ModeRequest:
    """A mode a behaviour asked the house to enter, and whether the house took it.

    The request half of what `ModeReading` is the gate half of. A behaviour that
    *writes* a mode -- the bedtime pack's second act is the house entering Sleep,
    which `pack-manifest/1.3.0`'s `mode` clause expresses -- reaches the house
    through the engine rather than through an entity, so no `SlotRead` and no
    `ProposedCommand` can carry it and the record would otherwise say only that
    the lights went off. `taken` is `False` for a mode the house does not declare:
    a manifest does not know which house it lands in, so the check happens when
    the request meets one, and recording the refusal is what makes a pack that
    enters nothing explainable rather than merely silent.
    """

    mode: str
    taken: bool


@dataclass(frozen=True, slots=True)
class HousePresence:
    """Whether the house read as occupied, and the rooms that decided it.

    `presence.house_emptied` is a derived fact rather than a device: nothing in
    the slot vocabulary reports a house's emptiness, so the engine derives it
    from the rooms it observes and this input is how the derivation enters the
    log. `quiet_rooms` is named because "the house emptied" is a claim about
    every room, and a record that said only `empty: true` would not show which
    rooms it was made from -- nor let a surprising shutdown be traced to the one
    sensor that never tripped.
    """

    empty: bool
    quiet_rooms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OverrideNote:
    """An override an evaluation met, in force or just ended.

    One type for both because the two are the same fact at two moments, and a
    record carries one or the other and never both. `released` is `None` while
    the override still holds -- the `overridden` outcome's case, where `awaited`
    names the reset conditions the engine is waiting on, so "we chose not to act
    because the user is in charge" is explainable and not merely visible. Once
    the override ends, `released` names the condition that ended it, which is
    what `engine-core` requires to appear in the record at which acting resumes.
    """

    entity_id: str
    released: ResetCondition | None = None
    awaited: tuple[ResetCondition, ...] = ()


@dataclass(frozen=True, slots=True)
class ModuleSuppression:
    """A module another module is holding off, and the one holding it off.

    One module switching another off is a *temporary* act by construction: the
    suppressor declares the packs it overrides (`pack-manifest`'s `suppresses`),
    the engine derives the suppression from which modules are currently on, and
    nothing is written down -- so the target's own switch is exactly where its
    person left it, and the moment the suppressor goes off the target is back.
    That is the whole difference between this and switching the target off, and
    it is why the input carries *who*: a record that said only "suppressed" would
    leave a person hunting for the switch that did it.
    """

    #: The pack being held off.
    module: str
    #: The pack doing the holding, and the behaviour of it that declared this.
    by: str
    behaviour: str


@dataclass(frozen=True, slots=True)
class HazardReading:
    """One alarming device a safety evaluation found, and which alert it raises.

    A hazard is recognised by what a device *is* -- a `binary_sensor` whose
    `device_class` is an alert class, reading `on` (`engine/safety.py`) -- and not
    by a slot, because the vocabulary has no smoke slot and a house must not be
    able to configure itself out of having a smoke alarm. So the input carries
    the entity and the kind rather than a slot.
    """

    entity_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class Repair:
    """A bound sensor that stopped answering, so a room can no longer be read.

    The engine's half of the HA integration's Repairs: "unavailable is not off".
    A room whose motion sensor has gone silent is neither occupied nor empty, and
    treating its silence as "clear" is the silent "fine" the safety audit forbids.
    The repair is the fact that makes the fallback explainable -- the evaluation
    that met the dead sensor records it, so a light held on is traceable to the
    sensor that could not be read rather than looking like a behaviour that never
    ran.
    """

    room_id: str
    slot: str
    entity_id: str


#: What an evaluation consulted. Each member exists because a requirement names
#: a fact the record has to carry: a read carries its reduction and its entities,
#: a setting carries its deciding layer, a dark-source reading carries which
#: branch decided and the value it read, a mode reading carries the gate, a mode
#: request carries the mode a behaviour asked the house to enter, a presence
#: reading carries the emptying and the rooms behind it, an override note carries
#: the suppression or the release, a module suppression carries the pack holding
#: another one off, a hazard reading carries the alarm a safety evaluation
#: answered, and a repair carries the sensor that could not be read. A record that
#: names them answers "why this value", "why now" and "why not" without the stack
#: that produced any of the three.
Input = (
    SlotRead
    | ResolvedSetting
    | DarkSourceReading
    | ModeReading
    | ModeRequest
    | HousePresence
    | OverrideNote
    | ModuleSuppression
    | HazardReading
    | Repair
)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One evaluation, in the normative field set.

    Every field is required, and none has a default: a record written without an
    outcome is a record the oracle cannot read, so its absence is a construction
    failure rather than an empty field. `rule` is the one field that may be
    `None`, because a skip that precedes any rule has no rule to name and saying
    so is part of what the record is for.
    """

    #: The virtual timestamp of the evaluation, never the wall clock.
    at: datetime
    #: The behaviour unit whose evaluation produced the record.
    actor: str
    #: Every slot read and every resolved setting the evaluation consulted.
    inputs: tuple[Input, ...]
    #: The corpus concept id the evaluation matched, or `None` for a skip.
    rule: str | None
    #: The commands the evaluation proposed, before arbitration.
    commands: tuple[ProposedCommand, ...]
    outcome: Outcome
    #: The change the resolution applied, as entity, before, after.
    state_delta: tuple[StateChange, ...]
    #: The room the evaluation ran for, or `None` for the house scope. Derived
    #: from the evaluation's `Scope`, and defaulting to `None` because a record
    #: is written by more than the engine -- a scenario composes one by hand and
    #: has no room to name. The Activity feed reads it, so a record that carries
    #: its own room needs no second guess (`ha_adapter.live_export`).
    room: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Outcome):
            raise InvalidOutcomeError(self.outcome)

    def to_document(self) -> dict[str, object]:
        """The record as a JSON-serialisable mapping.

        This is the form an agent reads, so it is a flat, tagged projection
        rather than the dataclasses themselves: an input says which kind it is,
        and the timestamp is the ISO string the rest of the system writes times
        as (`sim/snapshot.py`), so a record is comparable across a round trip
        through JSON without the engine being involved in parsing it back.
        """
        return {
            "at": self.at.isoformat(),
            "actor": self.actor,
            "inputs": [_input_document(entry) for entry in self.inputs],
            "rule": self.rule,
            "commands": [_command_document(command) for command in self.commands],
            "outcome": str(self.outcome),
            "room": self.room,
            "state_delta": [
                {
                    "entity_id": change.entity_id,
                    "before": change.before,
                    "after": change.after,
                }
                for change in self.state_delta
            ],
        }


class DecisionLog:
    """The retained records, bounded, newest last.

    `append` is the engine's one write and `window` is the surface's one read;
    everything else about the log is that it does not grow without bound and does
    not survive a restore.
    """

    def __init__(self, *, bound: int) -> None:
        if bound < 1:
            raise ValueError(f"a decision log bound must be at least 1, not {bound}")
        self._bound = bound
        self._records: deque[DecisionRecord] = deque(maxlen=bound)

    @property
    def bound(self) -> int:
        """The most records this log retains."""
        return self._bound

    def __len__(self) -> int:
        return len(self._records)

    def append(self, record: DecisionRecord) -> None:
        """Append a record, dropping the oldest once the bound is reached."""
        self._records.append(record)

    def records(self) -> tuple[DecisionRecord, ...]:
        """Every retained record, oldest first."""
        return tuple(self._records)

    def window(self, size: int) -> tuple[DecisionRecord, ...]:
        """The most recent `size` records, oldest first within the window.

        This is the read the control surface exposes as `get_decision_log`. A
        window larger than the log returns the log; a window of zero returns
        nothing, which is not the same as the whole log.
        """
        if size < 0:
            raise ValueError(f"a decision log window cannot be negative: {size}")
        if size == 0:
            return ()
        return tuple(self._records)[-size:]


def _command_document(command: ProposedCommand) -> dict[str, object]:
    """One proposed command, as a record's `commands` field holds it.

    A command's four ordinary fields are always present; `safety` is added only
    when the command is one, so an ordinary command's document is unchanged by
    the field's existence. That matters because a scenario asserts against the
    document, and a key that appeared on every command would change what an
    already-written assertion matches.
    """
    document: dict[str, object] = {
        "slot": command.slot,
        "entities": list(command.entities),
        "action": command.action,
        "origin": str(command.context.origin),
    }
    if command.safety:
        document["safety"] = True
    return document


def _input_document(entry: Input) -> dict[str, object]:
    """One consulted input, tagged by kind so a reader need not guess.

    A read and a resolved setting are told apart by an explicit `kind` rather
    than by which keys happen to be present, because "a slot read whose slot is
    named `key`" is a shape the presence test would get wrong. The `kind` also
    carries which of the four a row is, so a reader that meets a fifth one added
    later meets a tag it does not know rather than a row it misreads.
    """
    if isinstance(entry, SlotRead):
        return {
            "kind": "read",
            "slot": entry.slot,
            "entities": list(entry.entities),
            "reduction": str(entry.reduction),
        }
    if isinstance(entry, ResolvedSetting):
        return {
            "kind": "setting",
            "key": entry.key,
            "value": entry.value,
            "layer": str(entry.layer),
        }
    if isinstance(entry, DarkSourceReading):
        return {
            "kind": "dark_source",
            "source": str(entry.source),
            "value": entry.value,
        }
    if isinstance(entry, ModeReading):
        return {"kind": "mode", "mode": entry.mode, "active": entry.active}
    if isinstance(entry, ModeRequest):
        return {"kind": "mode_request", "mode": entry.mode, "taken": entry.taken}
    if isinstance(entry, HousePresence):
        return {
            "kind": "presence",
            "empty": entry.empty,
            "quiet_rooms": list(entry.quiet_rooms),
        }
    if isinstance(entry, HazardReading):
        return {"kind": "hazard", "entity_id": entry.entity_id, "hazard": entry.kind}
    if isinstance(entry, ModuleSuppression):
        return {
            "kind": "module_suppression",
            "module": entry.module,
            "by": entry.by,
            "behaviour": entry.behaviour,
        }
    if isinstance(entry, Repair):
        return {
            "kind": "repair",
            "room_id": entry.room_id,
            "slot": entry.slot,
            "entity_id": entry.entity_id,
        }
    return {
        "kind": "override",
        "entity_id": entry.entity_id,
        "released": None if entry.released is None else str(entry.released),
        "awaited": [str(condition) for condition in entry.awaited],
    }
