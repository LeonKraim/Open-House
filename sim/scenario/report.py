"""What a failed run says: one JSON object, naming the decision it disagreed with.

`scenario-runner` requires a failure to be a *document* rather than prose,
because a red CI job should be an input: the same scenario at the same seed
serialises the same failure, so a reader can replay it, and a machine can refuse
to merge on it. That is the whole reason the report carries the seed *and* the log
slice together -- the seed replays the run, and the slice says which evaluation the
scenario disagreed with, so a failure points at a decision rather than at a line
of a scenario file.

The document is flat and fully spelled out: every key is present, with `null`
where a failure has no step (a failure before the first step) or no verb, so a
consumer never has to distinguish "absent" from "empty". `expected` and `actual`
carry the two values themselves rather than a sentence about them, so a CI job can
diff them, and a human gets the sentence from `summary()`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sized

__all__ = ["FailureReport", "ScenarioFailed", "plain"]


@dataclass(frozen=True, slots=True)
class FailureReport:
    """One failed run, in the terms a CI job and a reader both need.

    `step` and `assertion` are indices into the `when` and `then` blocks, and at
    most one of them is set: a step that could not be performed fails before any
    assertion is evaluated, and an assertion that did not hold fails after every
    step ran. `verb` is the failed step's verb, which is what a reader needs to
    locate the step in a file whose steps are one line each.
    """

    scenario: str
    seed: int
    started_at: str
    instant: str
    expectation: str
    expected: object
    actual: object
    log_slice: tuple[Mapping[str, object], ...]
    step: int | None = None
    verb: str | None = None
    assertion: int | None = None

    def to_document(self) -> dict[str, object]:
        """The report as one JSON-serialisable mapping, built deterministically.

        Sorted-key JSON of this document is the report's canonical form, so two
        runs of the same failing scenario compare equal as strings -- which is
        what makes "the same seed reproduces the same failure" a check rather
        than an impression.
        """
        return {
            "scenario": self.scenario,
            "seed": self.seed,
            "started_at": self.started_at,
            "instant": self.instant,
            "step": self.step,
            "verb": self.verb,
            "assertion": self.assertion,
            "expectation": self.expectation,
            "expected": plain(self.expected),
            "actual": plain(self.actual),
            "log": [plain(record) for record in self.log_slice],
        }

    def to_json(self) -> str:
        """The report as stable JSON text: same failure, same string."""
        return json.dumps(self.to_document(), sort_keys=True)

    def summary(self) -> str:
        """One line a human reads, with the machine-readable form left to JSON."""
        where = (
            f"step {self.step} ({self.verb})"
            if self.step is not None
            else f"assertion {self.assertion}"
        )
        return (
            f"{self.scenario}: {where} failed at {self.instant}: expected "
            f"{self.expectation} to be {_brief(self.expected)}, but it was "
            f"{_brief(self.actual)} (seed {self.seed}, started {self.started_at})"
        )


class ScenarioFailed(Exception):  # noqa: N818  (a run that failed, not an error class)
    """A run that failed, carrying the report rather than only a message.

    The exception's own message is the one-line summary; the report is the
    document. Carrying both keeps a traceback readable while leaving the JSON a
    caller's to emit, and leaves a caller free to catch this and print the
    document without re-deriving anything.
    """

    def __init__(self, report: FailureReport) -> None:
        self.report = report
        super().__init__(report.summary())


def plain(value: object) -> object:
    """A JSON-serialisable form of `value`, without inspecting what it means.

    A report carries values that came from the engine -- a mapping proxy, a
    tuple, an instant -- and a document that JSON cannot write is not a document
    a CI job can consume. Containers are normalised rather than stringified, so
    two runs of the same failure compare equal; a set is sorted by `repr` for the
    same reason, its own order being the hash order.

    Public because a `RunResult` writes the same values a report does -- the
    snapshot's document and the log's records -- and two normalisers would be two
    answers to "is this document JSON-safe", which is exactly the kind of second
    opinion the phase removes everywhere else.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): plain(item)
            for key, item in cast("Mapping[object, object]", value).items()
        }
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str | bool | int | float) or value is None:
        return value
    if isinstance(value, Sequence):
        return [plain(item) for item in cast("Sequence[object]", value)]
    if isinstance(value, set | frozenset):
        members = cast("set[object] | frozenset[object]", value)
        return sorted((plain(item) for item in members), key=repr)
    return str(value)


def _brief(value: object) -> str:
    """A value as a sentence can hold it: a long container is counted, not listed."""
    counted = len(cast("Sized", value)) if _is_container(value) else None
    if counted is not None and counted > 3:
        return f"{counted} values"
    text = repr(value)
    if len(text) > 120:
        return f"{text[:117]}..."
    return text


def _is_container(value: object) -> bool:
    """Whether `value` is one of the three JSON containers a summary can count."""
    return isinstance(value, list | tuple | dict)
