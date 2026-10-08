"""The Activity tab's projection: what the engine decided, in the panel's words.

One of the panel's commands is answered here. A live session
(`ha_adapter/live.py`) owns the wiring an engine is rebuilt from and the decision
log it kept; this module is what turns that log -- the engine's own vocabulary of
dispositions, inputs and results -- into the rows the Activity tab draws.

`openhouse/facade.py` plays the same part for the simulator, and the difference
is the live path's and it is one fact: a live house does not own its devices.
Nothing here asks whether a device exists, because nothing here acts -- the rows
are a reading of what already happened.

**This module hands back documents, not result objects.** The facade's
operations return dataclasses its own callers read; the callers here are
websocket handlers that serialise straight to the panel, so nothing returns a
value the JSON encoder cannot take -- tuples, mappings, strings, numbers and
`None` -- and no engine dataclass crosses the boundary unprojected.

**Nothing here imports `homeassistant`.** The transport is the seam
(`ha_adapter/transport.py`).

The hard part is `activity_entry`, and it is hard for one reason: the engine's
closed set of outcomes has nine members and the panel's has five. The mapping
between them is stated as a table with a rule beside it (`_PANEL_OUTCOME`),
every member of `Outcome` is walked by a test, and a tenth member added later
fails that test rather than disappearing from the Activity tab.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from engine.decision_log import (
    DecisionRecord,
    HazardReading,
    HousePresence,
    ModeReading,
    ModuleSuppression,
    Outcome,
    OverrideNote,
    Repair,
    ResolvedSetting,
    SlotRead,
)

from .live import LiveSessionError

if TYPE_CHECKING:
    from engine.decision_log import Input

    from .live import LiveSession

__all__ = [
    "activity",
    "activity_entry",
]

#: The panel's five outcomes, as the Activity tab's own union spells them
#: (`panel/src/api/models.ts`). Spelled here only so the module can say what it
#: promises to produce; the values themselves come from the table below.
PANEL_OUTCOMES: tuple[str, ...] = (
    "applied",
    "skipped",
    "overridden",
    "blocked",
    "error",
)

#: The engine's disposition to the panel's, as a table with a rule.
#:
#: The rule, in one sentence: **the panel's outcome names the authority that
#: settled the evaluation's command.**
#:
#: | engine outcome            | panel outcome | the authority that settled it     |
#: |---------------------------|---------------|-----------------------------------|
#: | `acted`                   | `applied`     | none -- the command was written   |
#: | `declined`                | `skipped`     | none -- there was no command      |
#: | `skipped: unbound slot`   | `skipped`     | none -- there was no command      |
#: | `skipped: disabled`       | `skipped`     | none -- there was no command      |
#: | `lost arbitration`        | `blocked`     | another behaviour                 |
#: | `rate-limited`            | `blocked`     | the rate limiter                  |
#: | `overridden`              | `overridden`  | a person                          |
#: | `refused: unsafe`         | `error`       | the safety gate                   |
#:
#: Three things about this table are decisions rather than transcription.
#:
#: **The rule is stated, not the rows.** A reader who meets a ninth outcome
#: asks "who settled it", and the answer picks the column; a lookup that had
#: been transcribed from the eight would leave that question unanswerable and
#: the ninth outcome unmapped -- which is exactly the silent disappearance the
#: Activity tab cannot afford.
#:
#: **`error` is the safety gate and not a failure.** The panel has no `refused`
#: outcome, and `error` is its only word for "the engine declined to carry out
#: the command it was given". The refusal is deliberate and correct -- the gate
#: is the product rule working -- and the entry's `reason` says so in words, so
#: the loud chip is not a claim that something broke.
#:
#: **Nothing reaches `error` by default.** The mapping has no fallback branch:
#: `_panel_outcome` looks the outcome up and raises `LiveSessionError` naming it
#: when the row is absent, because a default would be the silent drop this table
#: exists to prevent. `test_live_export.py` walks every member of `Outcome`
#: through `activity_entry`, so a ninth member fails the suite.
_PANEL_OUTCOME: Mapping[Outcome, str] = {
    Outcome.ACTED: "applied",
    Outcome.DECLINED: "skipped",
    Outcome.SKIPPED_UNBOUND_SLOT: "skipped",
    Outcome.SKIPPED_DISABLED: "skipped",
    # `skipped` rather than `blocked`: the panel's `blocked` is a device another
    # behaviour won, and this is a module another module switched off before its
    # atoms were ever evaluated. The row's reason names the holder, which is what
    # the person looking for the culprit reads.
    Outcome.SKIPPED_SUPPRESSED: "skipped",
    Outcome.LOST_ARBITRATION: "blocked",
    Outcome.RATE_LIMITED: "blocked",
    Outcome.OVERRIDDEN: "overridden",
    Outcome.REFUSED_UNSAFE: "error",
}


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------


def activity(
    session: LiveSession, *, limit: int = 50, before: str | None = None
) -> tuple[Mapping[str, object], ...]:
    """The decision log as the Activity tab's rows, newest first.

    `limit` bounds the read and is the panel's `{limit?}`, passed straight to the
    log's own window rather than sliced here, so "the last fifty decisions" is
    one query against the log's bound rather than a copy of the log in this
    module.

    `before` is the opaque cursor the panel pages with: the `id` of the newest
    entry the caller already holds, answered with the entries strictly older
    than it. It is opaque on purpose -- the panel passes back a string this
    module minted and never parses it -- which is what lets the id be a digest
    of the record (`_entry_id`) rather than an index into a log whose bound
    drops its oldest entries.

    A cursor the window does not hold -- because the log's bound has since
    dropped the record it named, or because it came from another house -- is
    answered with the whole window rather than with nothing. "You have fallen
    off the end" and "there is nothing older" are different facts, and only the
    second is worth showing a person as an empty list.
    """
    if limit < 0:
        raise LiveSessionError(f"an activity limit cannot be negative: {limit}")
    entries = [activity_entry(record) for record in session.engine.log.window(limit)]
    if before is not None:
        identifiers = [entry["id"] for entry in entries]
        if before in identifiers:
            entries = entries[: identifiers.index(before)]
    entries.reverse()
    return tuple(entries)


def activity_entry(record: DecisionRecord) -> Mapping[str, object]:
    """One decision record as one `DecisionLogEntry`.

    The single source of the projection, and the reason it is a function of its
    own rather than a step inside `activity`: the panel's Activity tab is fed
    two ways -- the list command and the subscription that pushes one record at
    a time (`custom_components/open_house/host.py`) -- and two ways of building
    the same row would be two answers to the same question, drifting apart at
    the first change to either.

    **What the record can supply, and what it cannot.** A `DecisionRecord` is
    `(at, actor, inputs, rule, commands, outcome, state_delta)` and nothing
    else, so three of the panel's fields are read off it and two are not:

    - `behaviour` is the record's `actor`, the unit whose evaluation it was;
    - `entity_id` and `action` come from what the evaluation wrote, or proposed
      writing, when it did either;
    - `room` is `None` for most records, because a room-scoped evaluation's
      record does not name the room it was scoped to -- only a `Repair` and a
      single-room `HousePresence` name one, and `_room_of` reads those two;
    - `priority` is `None` always, because the engine resolves a unit's
      arbitrated priority at evaluation time and does not record it
      (`engine/engine.py`, `_Draft`). Guessing it from the behaviour's declared
      priority would be wrong for exactly the houses that have tuned it. A
      panel that needs it needs the engine to record it.

    Reporting `None` for what the record does not carry is the honest answer and
    not a gap left open: the alternative -- filling a field from something other
    than the record -- would make two entries that compare equal describe
    different decisions.
    """
    return {
        "id": _entry_id(record),
        "at": record.at.isoformat(),
        "room": _room_of(record),
        "behaviour": record.actor,
        "entity_id": _entity_of(record),
        "action": _action_of(record),
        "reason": _reason(record),
        "priority": None,
        "outcome": _panel_outcome(record.outcome),
    }


def _panel_outcome(outcome: Outcome) -> str:
    """The panel's outcome for an engine outcome, by `_PANEL_OUTCOME`'s rule.

    Raises rather than defaulting. An outcome with no row is an outcome this
    build has met and does not understand, and a record whose disposition the
    panel cannot name is a record that must fail loudly the moment it is read
    rather than appear in the Activity tab as something it is not. That is what
    makes adding a ninth member to `Outcome` a failing test instead of a
    disappearing row.
    """
    try:
        return _PANEL_OUTCOME[outcome]
    except KeyError:
        raise LiveSessionError(
            f"the engine outcome {outcome!r} has no panel outcome; the table in "
            "ha_adapter/live_export.py has to be extended for it"
        ) from None


def _entry_id(record: DecisionRecord) -> str:
    """A stable identifier for one record, opaque to the caller.

    A digest of the record's own document rather than a counter or a timestamp:
    the log is bounded and its oldest records fall out, so a position is not an
    identity, and two records evaluated in the same tick share an instant. The
    digest is of everything the record carries, so it is stable across calls,
    across a rebuild and across a restart, and it is what makes `before` a
    cursor a caller can hold without this module having to keep anything.

    Two records that agree on every field share an id, and that is correct: they
    are the same decision recorded twice, and a caller cannot tell them apart
    because there is nothing to tell apart.
    """
    document = record.to_document()
    encoded = json.dumps(document, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _room_of(record: DecisionRecord) -> str | None:
    """The room the record names, when it names exactly one.

    Two inputs name a room and no others do. `Repair` names the room whose slot
    stopped answering; `HousePresence` names the rooms a house-emptiness was
    decided from, and only when that list is exactly one is it "the room this
    was about" rather than a set of rooms behind a house-level fact. Everything
    else -- a byte-for-byte room-scoped evaluation's own record -- carries the
    slot and the entities it read and not the room, so the answer is `None`
    rather than a guess made from an entity id this function has no house to
    resolve.
    """
    for entry in record.inputs:
        if isinstance(entry, Repair):
            return entry.room_id
        if isinstance(entry, HousePresence) and len(entry.quiet_rooms) == 1:
            return entry.quiet_rooms[0]
    return None


def _entity_of(record: DecisionRecord) -> str | None:
    """The device the evaluation touched, in the order the record can name it.

    What was *written* first, then what was *proposed*, then what was *read* as
    a hazard or a repair. The order is what a person means by "the device" in the
    question the tab answers: an entry that changed a lamp is about the lamp,
    and one that was skipped before proposing anything is about the slot the
    record had to name to be read at all.
    """
    if record.state_delta:
        return record.state_delta[0].entity_id
    for command in record.commands:
        if command.entities:
            return command.entities[0]
    for entry in record.inputs:
        if isinstance(entry, (Repair, HazardReading)):
            return entry.entity_id
    return None


def _action_of(record: DecisionRecord) -> str:
    """What the evaluation asked for, as the panel's `action`.

    The proposed action when there is one, and otherwise the state that was
    written -- which a direct write produces and a behaviour does not. Empty for
    a record that proposed nothing and wrote nothing, which the panel renders as
    an em dash; the field is not nullable in `models.ts`, so the empty string is
    the shape's own way of saying "nothing was asked for".
    """
    if record.commands:
        return record.commands[0].action
    if record.state_delta:
        return record.state_delta[0].after
    return ""


def _reason(record: DecisionRecord) -> str:
    """Why this happened, in plain language, from the record's own fields.

    The point of the tab (`panel/src/tabs/activity.ts`: "the log's reason is the
    product, not debug output"). It is therefore written as a sentence about the
    house rather than as a dump of the record: the disposition first, then what
    the evaluation read, then what it did, each fact a clause of the same
    paragraph.

    Every clause is built from a field of *this* record and from nothing else --
    not from the engine, not from the house, not from the behaviour's source.
    That is what lets the reason be trusted: a record read on its own, months
    later, out of a file, produces the same sentence a live panel showed.
    """
    sentences = [f"{record.actor} {_disposition(record.outcome)}."]
    if record.rule is not None:
        sentences.append(f"It matched the rule {record.rule}.")
    for entry in record.inputs:
        sentences.append(_input_sentence(entry))
    sentences.extend(_result_sentences(record))
    return " ".join(sentence for sentence in sentences if sentence)


#: Which way each outcome fell, in the words a person would use. Written as a
#: clause rather than a sentence because `_reason` completes it with the unit
#: that did it -- "motion_lighting was skipped: it is switched off here." -- so
#: the same phrase reads correctly under every actor.
_DISPOSITION: Mapping[Outcome, str] = {
    Outcome.ACTED: "acted",
    Outcome.DECLINED: "reached its rule and decided that nothing needed doing",
    Outcome.LOST_ARBITRATION: (
        "lost arbitration: another behaviour wanted the same device and outranked it"
    ),
    Outcome.OVERRIDDEN: (
        "stood down, because a person's own change is in charge of the device"
    ),
    Outcome.RATE_LIMITED: (
        "waited, because it had already acted recently and the rate limit held it back"
    ),
    Outcome.SKIPPED_UNBOUND_SLOT: (
        "was skipped, because a slot it needs is bound to nothing"
    ),
    Outcome.SKIPPED_DISABLED: "was skipped, because it is switched off for this house",
    # The holder is named by the record's own `ModuleSuppression` input, so this
    # clause says only which kind of skip it was -- and it has to, because the
    # two skips above would otherwise be indistinguishable to a reader asking why
    # a module they never switched off is not running.
    Outcome.SKIPPED_SUPPRESSED: (
        "was skipped, because another module is holding this one off"
    ),
    Outcome.REFUSED_UNSAFE: (
        "was refused: the safety rule will not carry out a command that would "
        "leave a device unsafe"
    ),
}


def _disposition(outcome: Outcome) -> str:
    """The clause a record's outcome contributes to its reason.

    Guarded for the same reason `_panel_outcome` is, and separately from it:
    this is a second table over the same enum, so a ninth outcome would fail
    here too, and a `KeyError` raised out of a dict literal is a worse answer
    than a failure that names what to do about it.
    """
    try:
        return _DISPOSITION[outcome]
    except KeyError:
        raise LiveSessionError(
            f"the engine outcome {outcome!r} has no disposition sentence; the "
            "table in ha_adapter/live_export.py has to be extended for it"
        ) from None


def _input_sentence(entry: Input) -> str:
    """One consulted input as a clause, or the empty string for a silent kind.

    A `SlotRead` and a `ResolvedSetting` are the two kinds a reader needs in
    order to check the arithmetic of a decision -- what was read and what
    threshold it was compared against -- and each gets a sentence. A
    `DarkSourceReading` does not, because the slot it stands for is already
    named by the read beside it; the remaining kinds each say something the
    disposition alone cannot.
    """
    if isinstance(entry, SlotRead):
        entities = ", ".join(entry.entities) if entry.entities else "nothing bound"
        return (
            f"It read {entry.slot} under the {entry.reduction} reduction ({entities})."
        )
    if isinstance(entry, ResolvedSetting):
        return (
            f"It read the setting {entry.key} as {entry.value!r}, "
            f"decided at the {entry.layer} layer."
        )
    if isinstance(entry, ModeReading):
        state = "active" if entry.active else "not active"
        return f"The {entry.mode} mode was {state}."
    if isinstance(entry, HousePresence):
        rooms = ", ".join(entry.quiet_rooms) if entry.quiet_rooms else "no room"
        return (
            f"The house read as {'empty' if entry.empty else 'occupied'} "
            f"(quiet for long enough: {rooms})."
        )
    if isinstance(entry, OverrideNote):
        return _override_sentence(entry)
    if isinstance(entry, ModuleSuppression):
        return _suppression_sentence(entry)
    if isinstance(entry, HazardReading):
        return f"It answered the {entry.kind} alert raised by {entry.entity_id}."
    if isinstance(entry, Repair):
        return (
            f"It could not read {entry.slot} in {entry.room_id}, because "
            f"{entry.entity_id} stopped answering."
        )
    return ""


def _override_sentence(note: OverrideNote) -> str:
    """An override, in force or just ended, as a clause.

    The two cases are one type at two moments and they read very differently:
    an override still in force explains why nothing was done, and one that has
    just been released explains why something was done *now*. The awaited
    conditions are named in the first case because "we are waiting for the
    timeout" and "we are waiting for the room to empty" are different answers to
    "when will my lights come back".
    """
    if note.released is None:
        waited = (
            ", ".join(_phrase(condition) for condition in note.awaited)
            or "nothing in particular"
        )
        return (
            f"{note.entity_id} is under a person's override, and the engine is "
            f"waiting for {waited}."
        )
    return (
        f"The person's override on {note.entity_id} has ended "
        f"({_phrase(note.released)}), so the engine is acting again."
    )


def _suppression_sentence(entry: ModuleSuppression) -> str:
    """A module held off by another, naming the one to go and switch off.

    The sentence a person needs is the *who*, because the target's own switch is
    exactly where they left it and looking at it would tell them nothing: the
    pack named here is the one whose switch brings the target back.
    """
    return (
        f"The {entry.module} module is being held off by the {entry.by} module "
        f"(its {entry.behaviour} behaviour), which is a temporary override and "
        f"leaves {entry.module}'s own switch alone."
    )


def _result_sentences(record: DecisionRecord) -> list[str]:
    """What the evaluation did, or why nothing observable came of it.

    A record that wrote something names each write; a record that proposed
    something and wrote nothing says so, because "the engine wanted to turn the
    hall light on and did not" is the sentence a person is looking for when they
    open the tab. A record that did neither -- a decline, a skip -- adds nothing
    here, because the disposition has already said it.
    """
    if record.state_delta:
        return [
            f"It changed {change.entity_id} from {change.before!r} to {change.after!r}."
            for change in record.state_delta
        ]
    if record.commands:
        command = record.commands[0]
        devices = ", ".join(command.entities) if command.entities else "no device"
        return [f"It proposed {command.action} on {devices}, which was not applied."]
    return []


def _phrase(condition: object) -> str:
    """An enum value's name as words -- `override_timeout` as "override timeout"."""
    return str(condition).replace("_", " ")


# --------------------------------------------------------------------------
# Small readers over a document that already migrated and validated
# --------------------------------------------------------------------------


def _sequence(value: object) -> tuple[Mapping[str, object], ...]:
    """A migrated document's list field, keeping only the rows that are objects.

    A projection and not a check: the document validated against the frozen
    schema before this ran, so a row here is an object and a non-object would be
    a contradiction rather than a thing to report. Dropping it keeps the caller
    free of a branch no reachable input takes.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        cast("Mapping[str, object]", row) for row in value if isinstance(row, Mapping)
    )
