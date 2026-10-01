"""The two halves of a `then` block: what the house reads, and what the log says.

`scenario-runner` requires both, and the reason is `design.md` D2: "the light is
off" is true whether the timeout behaviour turned it off or the light was never
switched on, so a suite that could only assert device state could not tell
*turned it off* from *never ran*, could not measure mutation, and could not
explain a counterexample. So a scenario asserts on the house **and** on the
record an evaluation left.

Neither half predicts. `StateExpectation` compares a read of the entity to the
expected value and reports the mismatch; it does not ask the engine's rules what
the value should have been, because a second derivation is a second
implementation and a disagreement with the engine would be a bug in the
assertion rather than a detection of one (`scenario-runner`: "The runner
predicts instead of reading"). `LogExpectation` reads the records the engine
appended, for the same reason.

The engine's types appear here only structurally. `ReadEntity` is a protocol
with the four fields the port's view carries rather than the view itself, so this
module imports no `engine/` module -- which is the boundary `scenario-runner`
fixes for the whole package, and it is what lets the assertions be a client of
the control surface rather than a client of the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "LogExpectation",
    "Mismatch",
    "ReadEntity",
    "StateExpectation",
    "matches",
]

#: The record fields whose value is a list of documents, and which therefore
#: match by containment rather than by equality. Named once because the matcher
#: and the report's rendering of it have to agree about which fields these are.
LIST_FIELDS: tuple[str, ...] = ("inputs", "commands", "state_delta")

#: The state assertion's field name for an entity's availability. Spelled here
#: rather than at each use, because it is also the key the scenario document uses
#: and the two must be the same word.
AVAILABILITY = "availability"

#: The state assertion's field name for the entity's state itself.
STATE = "state"


class ReadEntity(Protocol):
    """The four fields of a device read, as an assertion needs them.

    A protocol rather than the port's own view type, so this module needs no
    `engine/` import (`scenario-runner`'s boundary). `EntityView` satisfies it
    without being told to: the port's control face already carries exactly these.
    """

    @property
    def entity_id(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def attributes(self) -> Mapping[str, object]: ...

    @property
    def available(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Mismatch:
    """Why an expectation did not hold, in the terms a failure report repeats.

    `expectation` is the expectation's own description rather than a field name,
    because a report a reader has to decode is most of what a report exists to
    save; `expected` and `actual` are the two values that disagreed, left as the
    values themselves so a JSON document carries them rather than a sentence
    about them.
    """

    expectation: str
    expected: object
    actual: object


@dataclass(frozen=True, slots=True)
class StateExpectation:
    """One assertion about what the house reads at the point it is evaluated.

    `field` is one of three things and the closed set is deliberate: the entity's
    `state`, its `availability`, or the name of an attribute. Availability is its
    own case rather than an attribute because `house-adapter` keeps it a field of
    its own -- an entity that is unavailable is assertable *as* unavailable, and a
    scenario for the corpus's "a plug goes unavailable and returns" seed can
    assert the plug was never reported switched off, which is the fact the seed
    exists to protect.
    """

    entity_id: str
    field: str
    expected: object

    def describe(self) -> str:
        """What this asserts, in words a failure report can repeat."""
        if self.field == AVAILABILITY:
            return f"the availability of {self.entity_id}"
        if self.field == STATE:
            return f"the state of {self.entity_id}"
        return f"the attribute {self.field!r} of {self.entity_id}"

    def evaluate(self, entity: ReadEntity) -> Mismatch | None:
        """`None` when the read matches, or the mismatch that says how it does not.

        An attribute the entity does not carry reads as `None` rather than
        raising, because "the assertion asked for an attribute nothing carries"
        is a result a scenario reports and not a failure of the runner. The way to
        tell it from an attribute whose value is `None` is the expectation's own
        description, which names the attribute being asserted.
        """
        if self.field == AVAILABILITY:
            actual: object = entity.available
        elif self.field == STATE:
            actual = entity.state
        else:
            actual = entity.attributes.get(self.field)
        if actual == self.expected:
            return None
        return Mismatch(self.describe(), self.expected, actual)


@dataclass(frozen=True, slots=True)
class LogExpectation:
    """One assertion about the records the engine wrote.

    `must` and `must_not` take any subset of a record's normative fields (D2's
    "the log doubles as the oracle"), and `because` is the named form the phase
    asks for: a record matching a given `rule` *and* `outcome`, which is what
    distinguishes an outcome the engine produced from a state that happens to
    match. `because` is not sugar for a `must` entry -- both are matched the same
    way -- it is the form a reader can see is about a cause, and the loader
    refuses one that names neither a rule nor an outcome rather than letting a
    vacuous citation through.

    `must_not` is what makes an *absence* assertable: "no record says this
    happened" is a claim no state assertion can make, and it is how the corpus's
    unavailable-and-return seed asserts that the plug was never falsely recorded
    as switched off.
    """

    must: tuple[Mapping[str, object], ...] = ()
    must_not: tuple[Mapping[str, object], ...] = ()
    because: Mapping[str, object] | None = None

    def describe(self) -> str:
        """What this asserts, in words a failure report can repeat."""
        parts: list[str] = []
        if self.because is not None:
            parts.append(f"a record because {_render(self.because)}")
        parts.extend(f"a record matching {_render(matcher)}" for matcher in self.must)
        parts.extend(
            f"no record matching {_render(matcher)}" for matcher in self.must_not
        )
        return "; ".join(parts)

    def evaluate(self, records: Sequence[Mapping[str, object]]) -> Mismatch | None:
        """`None` when every part of the expectation holds, or the first miss.

        The first miss rather than all of them: a failure report names one
        expectation, and a report that listed every failing part would be a
        report about the assertion rather than about the run. The log itself
        goes into `actual` when a `must` misses, because the records are what the
        reader needs to see why nothing matched, and they are what the report's
        log slice would have shown anyway.
        """
        if self.because is not None:
            missing = self._missing(self.because, records, because=True)
            if missing is not None:
                return missing
        for matcher in self.must:
            missing = self._missing(matcher, records)
            if missing is not None:
                return missing
        for matcher in self.must_not:
            found = [record for record in records if matches(record, matcher)]
            if found:
                return Mismatch(
                    f"no record matching {_render(matcher)}",
                    None,
                    found[0],
                )
        return None

    def _missing(
        self,
        matcher: Mapping[str, object],
        records: Sequence[Mapping[str, object]],
        *,
        because: bool = False,
    ) -> Mismatch | None:
        """The mismatch for a matcher no record satisfies, or `None`.

        The `because` form is labelled as such in the expectation it reports,
        because it is the assertion a reader is most likely to have to act on --
        "the record I expected is not there" is a different finding from "a
        record with these fields is not there", and `describe()` already
        distinguishes them.
        """
        if any(matches(record, matcher) for record in records):
            return None
        lead = "a record because" if because else "a record matching"
        return Mismatch(f"{lead} {_render(matcher)}", matcher, list(records))


def matches(record: Mapping[str, object], matcher: Mapping[str, object]) -> bool:
    """Whether `record` satisfies every field `matcher` names.

    A matcher is partial: a field it does not name is not constrained, so
    `{"outcome": "acted"}` matches any acted record whatever its rule. The three
    list fields are matched by **containment** and not by position -- a matcher's
    `inputs` entry must match some input of the record's, and the order of an
    evaluation's reads is not part of what a record claims -- while every other
    field is compared by equality, `rule: null` included, which is how a scenario
    asserts a record that reached no rule.
    """
    for field, wanted in matcher.items():
        if field not in LIST_FIELDS:
            if record.get(field) != wanted:
                return False
            continue
        if not isinstance(wanted, list):
            return False
        carried = record.get(field)
        if not isinstance(carried, list):
            return False
        for entry in cast("list[object]", wanted):
            document = _document(entry)
            if document is None:
                return False
            if not any(
                (candidate := _document(item)) is not None
                and matches(candidate, document)
                for item in cast("list[object]", carried)
            ):
                return False
    return True


def _document(value: object) -> Mapping[str, object] | None:
    """`value` as a partial record, or `None` when it is not a JSON object.

    The narrowing the matcher needs and that `isinstance` alone cannot give it:
    a `list`'s members are `object` and a `dict` match narrows to a mapping whose
    two parameters are unknown, so this is where "it is a document" is stated
    once instead of once per field.
    """
    if not isinstance(value, dict):
        return None
    return cast("Mapping[str, object]", value)


def _render(matcher: Mapping[str, object]) -> str:
    """A matcher as one line, field by field, for a report's expectation."""
    return (
        "{" + ", ".join(f"{name}: {value!r}" for name, value in matcher.items()) + "}"
    )
