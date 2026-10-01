"""The scenario document's types: what a scenario *is*, once it has loaded.

`configuration-schemas` fixes the shape in `schemas/scenario/1.0.0.json`, and the
loader validates against it before anything here is built; these types are the
validated projection, so every one of them exists only for a document that passed
the schema. There are five, and each is a block of the document: `Given` is where
and on what, `Step` is one thing done, `Then` is the assertions, `Expectation` is
one assertion, and `Scenario` is the three together with where it came from.

The run's fixed inputs are resolved here rather than at run time. A `given` block
may omit its seed or its starting instant, and the defaults are the fixture's own;
resolving them at load means `Scenario.given` always carries the concrete inputs a
replay needs, so a failure report can be replayed from the `Scenario` value alone
instead of from the document plus a second set of defaulting rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sim.fixtures import DEFAULT_SEED, DEFAULT_STARTED_AT, FixtureName

from .assertions import LogExpectation, StateExpectation

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

__all__ = ["Expectation", "Given", "Scenario", "Step", "Then"]

#: One assertion of a `then` block. The two kinds are the spec's two halves --
#: what the house reads (`StateExpectation`) and what the log says
#: (`LogExpectation`) -- and a `Then` is a sequence of either, in document order.
Expectation = StateExpectation | LogExpectation


@dataclass(frozen=True, slots=True)
class Step:
    """One entry of a `when` block: a verb, and the parameters it was given.

    The document writes a step as a single-key mapping -- `{advance_time:
    {minutes: 5}}` -- and this is that pair, held apart, because the verb is what
    the DSL dispatches on and the parameters are what it passes through. A step
    has no behaviour of its own: `dsl.compile_step` turns it into a
    control-surface call and nothing else (`scenario-runner`).
    """

    verb: str
    parameters: Mapping[str, object]

    def describe(self) -> str:
        """The step as a failure report names it: `verb(k=v)`, or the verb alone."""
        if not self.parameters:
            return self.verb
        rendered = ", ".join(
            f"{name}={value!r}" for name, value in self.parameters.items()
        )
        return f"{self.verb}({rendered})"


@dataclass(frozen=True, slots=True)
class Given:
    """Where a run happens and on what: the house, and its fixed inputs.

    `house` is either a fixture's name or an inline house document, and the two
    are told apart by type rather than by a flag because the two properties below
    are exactly the projections each caller wants -- the composition root builds a
    fixture, or validates a document into a `House` -- and neither wants both.

    `enable_flags` is the house layer of the resolver, keyed as it keys settings
    (`behaviour.<id>.enabled`). It is here and not a step because no
    control-surface operation enables a behaviour: activation is the composition
    root's act, and a scenario sets it before the first step the way an operator
    would.
    """

    house: str | Mapping[str, object]
    seed: int = DEFAULT_SEED
    started_at: datetime = DEFAULT_STARTED_AT
    #: A `default_factory` rather than a literal, because a shared mutable default
    #: is one scenario's flags appearing in another's. Empty is the honest
    #: default: a `given` block that declares no flag declares no flag.
    enable_flags: Mapping[str, object] = field(default_factory=dict[str, object])

    @property
    def fixture(self) -> FixtureName | None:
        """The fixture this block names, or `None` for an inline house.

        The name is projected through `FixtureName` rather than passed on as a
        string, so a name the registry does not carry fails here -- at load, before
        the run -- naming itself, and so a caller cannot open a fixture whose
        spelling differed only by case.
        """
        if isinstance(self.house, str):
            return FixtureName(self.house)
        return None

    @property
    def inline(self) -> Mapping[str, object] | None:
        """The inline house document, or `None` when a fixture is named."""
        return None if isinstance(self.house, str) else self.house


@dataclass(frozen=True, slots=True)
class Then:
    """The `then` block: the assertions, in the order the document wrote them.

    A wrapper rather than a bare tuple because the block is a thing the spec names
    (`Then`), and because the loader's job of refusing an empty one reads better as
    a property of the block than as a check on a list.
    """

    expectations: tuple[Expectation, ...]

    def describe(self) -> str:
        """Every assertion as one line, in order."""
        return "; ".join(expectation.describe() for expectation in self.expectations)


@dataclass(frozen=True, slots=True)
class Scenario:
    """A loaded scenario: its blocks, and where it came from.

    `path` is the file it was loaded from, or a caller-supplied name for one built
    in memory; it is what a failure report names, because a report a reader cannot
    locate is a report that has to be matched back to a file by hand. `name` is the
    document's own name when it has one and the path's stem otherwise.
    """

    name: str
    path: str
    given: Given
    when: tuple[Step, ...]
    then: Then

    def describe(self) -> str:
        """The scenario's name and path, as a report's opening line."""
        return f"{self.name} ({self.path})"
