"""The decision log's normative record and its bound -- tasks 4.0 and 4.1.

The log is the artifact a scenario asserts against, so what these tests hold is
the part of it an oracle depends on: the outcome vocabulary is closed at eight,
a record missing any field cannot be constructed at all, the document an agent
reads is JSON-serialisable and tags each input by kind, and the log retains at
most its bound while `window` reads the newest records. None of this is
bookkeeping -- a record that could be written without an outcome, or with an
outcome outside the eight, is a record the oracle reads as an unknown state and
silently passes.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from engine.adapter import ChangeContext
from engine.binding import Reduction, SlotRead
from engine.config import Layer, ResolvedSetting
from engine.decision_log import (
    DecisionLog,
    DecisionRecord,
    InvalidOutcomeError,
    Outcome,
    ProposedCommand,
    StateChange,
)

_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: The eight dispositions, spelled out rather than derived from the enum: a test
#: that read `Outcome` to check `Outcome` would pass if a member were renamed and
#: every stored record changed meaning with it.
_EIGHT = {
    "acted",
    "declined",
    "lost arbitration",
    "overridden",
    "rate-limited",
    "skipped: unbound slot",
    "skipped: disabled",
    "refused: unsafe",
}


def _read() -> SlotRead:
    return SlotRead(
        slot="light_group", entities=("light.kitchen",), reduction=Reduction.ALL
    )


def _setting() -> ResolvedSetting:
    return ResolvedSetting(key="behaviour.quiet_timeout", value=300, layer=Layer.ROOM)


def _record(**overrides: Any) -> DecisionRecord:
    """A valid record of no particular evaluation, for the tests that vary one field."""
    fields: dict[str, Any] = {
        "at": _AT,
        "actor": "kitchen.motion_lights",
        "inputs": (_read(), _setting()),
        "rule": "motion_lighting",
        "commands": (
            ProposedCommand(
                slot="light_group",
                entities=("light.kitchen",),
                action="on",
                context=ChangeContext.engine(),
            ),
        ),
        "outcome": Outcome.ACTED,
        "state_delta": (
            StateChange(entity_id="light.kitchen", before="off", after="on"),
        ),
    }
    fields.update(overrides)
    return DecisionRecord(**fields)


# --------------------------------------------------------------------------
# The outcome vocabulary
# --------------------------------------------------------------------------


def test_the_outcome_set_is_closed_at_eight() -> None:
    """Exactly these eight spellings, no more and no fewer.

    A falsifying implementation that added an outcome would introduce a state the
    phase's oracle has no case for -- and because a record is only read, not
    validated, the extra value would pass silently. One that renamed a member
    would change what an already-written record means.
    """
    assert {str(member) for member in Outcome} == _EIGHT
    assert len(Outcome) == 8


def test_a_disposition_outside_the_set_cannot_be_recorded() -> None:
    """A raw string that spells an outcome is still refused, because it is not one.

    `Outcome` is a `StrEnum`, so `"acted" == Outcome.ACTED` -- which is why the
    obvious membership test would admit a string. A falsifying implementation that
    compared values instead of checking the type would accept a typo'd or
    phase-local disposition, and the record would carry a state no reader knows.
    """
    with pytest.raises(InvalidOutcomeError) as raised:
        _record(outcome="acted")
    assert raised.value.outcome == "acted"
    assert "acted" in str(raised.value)


def test_an_unknown_disposition_names_the_closed_set() -> None:
    """The failure lists what a record may carry, so the mistake is self-correcting.

    A falsifying implementation raising a bare `ValueError` would leave a
    behaviour author guessing at the vocabulary the log accepts.
    """
    with pytest.raises(InvalidOutcomeError) as raised:
        _record(outcome=Outcome.SKIPPED_DISABLED.value + " forever")
    message = str(raised.value)
    assert all(spelling in message for spelling in _EIGHT)


def test_a_skip_ahead_of_any_rule_carries_no_rule() -> None:
    """`rule` is the one field that may be `None`, and a skip is why.

    A falsifying implementation that required a rule would force a skip to name
    one it never matched, which is exactly the fact -- nothing was evaluated --
    the skip's record exists to state.
    """
    record = _record(outcome=Outcome.SKIPPED_UNBOUND_SLOT, rule=None, commands=())
    assert record.rule is None
    assert record.to_document()["rule"] is None


# --------------------------------------------------------------------------
# The record's field set
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["at", "actor", "inputs", "rule", "commands", "outcome", "state_delta"],
)
def test_every_field_is_required(field: str) -> None:
    """Leaving out any field is a failure to construct, not an empty field.

    A falsifying implementation that defaulted a field -- `outcome=Outcome.ACTED`
    or `commands=()` -- would write a record claiming something the evaluation
    never did, and the defaults would be indistinguishable in the log from the
    recorded facts.
    """
    fields: dict[str, Any] = {
        "at": _AT,
        "actor": "kitchen.motion_lights",
        "inputs": (),
        "rule": None,
        "commands": (),
        "outcome": Outcome.ACTED,
        "state_delta": (),
    }
    del fields[field]
    with pytest.raises(TypeError):
        DecisionRecord(**fields)


def test_the_record_documents_to_a_json_serialisable_mapping() -> None:
    """The document a surface serves survives `json.dumps` without a fallback.

    A falsifying implementation that left its dataclasses in place -- or that
    wrote the `datetime` and the enums raw -- would fail at the boundary, in the
    control surface, where the engine is no longer around to explain it.
    """
    document = _record().to_document()
    encoded = json.dumps(document)
    assert json.loads(encoded) == document
    assert document["at"] == _AT.isoformat()
    assert document["outcome"] == str(Outcome.ACTED)


def test_each_consulted_input_is_tagged_by_kind() -> None:
    """A slot read and a resolved setting are told apart without guessing.

    A falsifying implementation that distinguished them by which keys were present
    would mis-read a read whose slot happened to be named `key` or `value`, and
    `SlotRead`'s field set is not disjoint from `ResolvedSetting`'s.
    """
    document = _record().to_document()
    inputs = document["inputs"]
    assert isinstance(inputs, list)
    assert inputs == [
        {
            "kind": "read",
            "slot": "light_group",
            "entities": ["light.kitchen"],
            "reduction": str(Reduction.ALL),
        },
        {
            "kind": "setting",
            "key": "behaviour.quiet_timeout",
            "value": 300,
            "layer": str(Layer.ROOM),
        },
    ]


def test_a_command_document_names_the_origin_it_would_be_applied_under() -> None:
    """A proposed command carries its origin, so an outranked one is visible.

    A falsifying implementation that dropped the context would leave two records
    -- one of a command the engine proposed and one of the same command a user's
    action outranked -- looking identical, which is the arbitration the record
    exists to explain.
    """
    document = _record().to_document()
    commands = document["commands"]
    assert isinstance(commands, list)
    assert commands == [
        {
            "slot": "light_group",
            "entities": ["light.kitchen"],
            "action": "on",
            "origin": str(ChangeContext.engine().origin),
        }
    ]


def test_a_state_change_document_is_entity_before_and_after() -> None:
    """The delta is the applied change, in the order it was applied.

    A falsifying implementation that stored a mapping would lose the order of two
    changes to the same entity, which is the one case where before/after is not
    determined by the entity's final state.
    """
    record = _record(
        state_delta=(
            StateChange(entity_id="light.kitchen", before="off", after="on"),
            StateChange(entity_id="light.kitchen", before="on", after="dim"),
        )
    )
    assert record.to_document()["state_delta"] == [
        {"entity_id": "light.kitchen", "before": "off", "after": "on"},
        {"entity_id": "light.kitchen", "before": "on", "after": "dim"},
    ]


# --------------------------------------------------------------------------
# The log: bounded, and read as a window
# --------------------------------------------------------------------------


def test_a_log_retains_at_most_its_bound_and_drops_the_oldest() -> None:
    """The third record into a two-record log evicts the first, not the newest.

    A falsifying implementation that dropped the newest -- or that grew without
    bound -- would either report a stale decision as current or make a long run's
    log a memory sink; only the retained set tells the two apart.
    """
    log = DecisionLog(bound=2)
    first = _record(actor="first")
    second = _record(actor="second")
    third = _record(actor="third")
    log.append(first)
    log.append(second)
    log.append(third)
    assert len(log) == 2
    assert log.records() == (second, third)
    assert log.bound == 2


def test_a_log_whose_bound_is_below_one_is_refused() -> None:
    """A bound of zero would be a log that retains nothing, which is not a log.

    A falsifying implementation that passed the bound to a `deque` unchecked would
    accept zero and then make every append a silent no-op, and every scenario
    asserting on the log would fail for a reason the configuration never stated.
    """
    with pytest.raises(ValueError):
        DecisionLog(bound=0)


def test_a_window_returns_the_newest_records_oldest_first_within_the_window() -> None:
    """The read is the tail of the log, in the order the log keeps.

    A falsifying implementation that returned the newest first would reverse the
    order only for windows, so a scenario reading two records would see them in an
    order the whole-log read never produces.
    """
    log = DecisionLog(bound=10)
    records = [_record(actor=f"evaluation {index}") for index in range(4)]
    for record in records:
        log.append(record)
    assert log.window(2) == (records[2], records[3])


def test_a_window_larger_than_the_log_returns_the_log() -> None:
    """Asking for more than is retained is not an error, it is the whole log."""
    log = DecisionLog(bound=10)
    records = [_record(actor=f"evaluation {index}") for index in range(3)]
    for record in records:
        log.append(record)
    assert log.window(50) == tuple(records)


def test_a_window_of_zero_returns_nothing() -> None:
    """Zero is the empty window, which is not the same as the whole log.

    A falsifying implementation that fell back to the whole log on a falsy size
    would answer "the most recent zero records" with every record ever kept.
    """
    log = DecisionLog(bound=10)
    log.append(_record())
    assert log.window(0) == ()


def test_a_negative_window_is_refused() -> None:
    """A negative size is a caller's mistake, not a silently empty read.

    A falsifying implementation that sliced with it would return all but the last
    few records -- a plausible-looking answer to a question nobody asked.
    """
    log = DecisionLog(bound=10)
    with pytest.raises(ValueError):
        log.window(-1)


def test_a_fresh_log_retains_nothing() -> None:
    """A new log is empty, and reading an empty log is not an error.

    This is the log's state before any evaluation. The restore scenario
    (`engine-core`: "a restore does not restore the log") is a claim about the
    engine continuing after a restore, so it lands with `engine/engine.py`; what
    is testable here is that nothing seeds a log and no read of an empty one
    fails, which is what that scenario would be built on.
    """
    log = DecisionLog(bound=10)
    assert len(log) == 0
    assert log.records() == ()
    assert log.window(5) == ()
