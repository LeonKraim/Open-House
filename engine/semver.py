"""Semver ranges, evaluated the way a version *is*: an interval over integers.

A pack says which engine it is written for with `engine_api`, and which version of
another pack it needs with a `reference`'s `range`. Both are the same grammar -- a
run of comparators over dotted versions, joined by whitespace for *and* and by
`||` for *or* -- and the grammar is the **schema's**: `schemas/pack-manifest/
1.2.0.json` publishes it as a `pattern` on `engine_api` and on
`$defs.reference.range`, and `reference`'s own description says why the two share
it ("so a pack author has one range syntax and not two"). This module evaluates
that grammar rather than restating it, and it is a module of its own because two
capabilities read it: the manifest validator checks a pack's `engine_api` against
the engine's published version, and the dependency resolver checks a `range`
against an installed pack's version.

Every comparator is read as a half-open interval over three-component integer
versions, `[lower, upper)`, with `None` meaning unbounded on that side. That is
the whole of the model, and it is chosen because the alternative -- comparing
components one at a time against an operator -- has to decide, at every index,
which of the range's components the operator was talking about, and gets `~1.2`,
`^0.0.3` and `>=1.2` wrong in three different ways. An interval has no such index:
`~1.2.3` is `[1.2.3, 1.3.0)` and there is nothing left to interpret.

**The readings this fixes**, all of them conventional and none of them derivable
from the pattern alone:

- a bare partial version is a range, not an exact match: `1.2` is `[1.2.0, 1.3.0)`
  and `1` is `[1.0.0, 2.0.0)`. A fully written version names a single version:
  `1.2.3` is `[1.2.3, 1.2.4)`, which in a version space of three components
  admits `1.2.3` alone. So every written version has a span, and a range that
  named fewer components left the rest open.
- the operator is applied to that span and never to the digits, so `>1.2` is
  `[1.3.0, ...)` -- above *all* of 1.2 -- while `>=1.2` is `[1.2.0, ...)`. On the
  other side `<=1.2` is `[..., 1.3.0)` and `<1.2` is `[..., 1.2.0)`, and `=1.2` is
  the span itself. Applying the operator to the digits instead would make `>1.2`
  mean `[1.2.1, ...)`, which excludes nothing from 1.2 and so says "above 1.2"
  rather than "above the versions that are 1.2".
- `^` widens to the leftmost component the range left open, or, when it left none
  open, to the leftmost non-zero one: `^1.2.3` is `[1.2.3, 2.0.0)`, `^0.2.3` is
  `[0.2.3, 0.3.0)`, `^0.0.3` is `[0.0.3, 0.0.4)`, `^0.0` is `[0.0.0, 0.1.0)`
  because the patch was open, and `^0` is `[0.0.0, 1.0.0)` because the minor was.
- `~` widens to the minor, or to the major when the range named no minor: `~1.2.3`
  is `[1.2.3, 1.3.0)` and `~1.2` is `[1.2.0, 1.3.0)`, both ending at the same
  minor, while `~1` is `[1.0.0, 2.0.0)`.
- a wildcard names the whole span of the component it stands in: `1.x` is
  `[1.0.0, 2.0.0)` and `1.x.x` is the same range. The schema's `pattern` admits a
  wildcard as the minor and, after it, as the patch, so those are the two shapes
  a clause can write -- and both are just the span rule above, with the wildcard
  marking where the version stops being written.
- a wildcard standing where the major would be names every version, so `*` is
  every version and an operator on it narrows only when it excludes something:
  `<=x`, `>=x` and `=x` are still every version, while `>x` and `<x` are **empty**,
  because "above every version" and "below every version" are sets with no
  members. The pattern admits all of them, so all of them have to parse -- a pack
  that wrote an excluding one is refused by the `engine_api` check, naming the
  version it excluded, rather than crashing the validator on a range the schema
  said was well formed.
- `>` and `<` are strict over integers, so `>1.2.3` is `[1.2.4, ...)`: the version
  space is discrete, and a bound one patch above the version named is exactly
  "strictly greater than it".

A range the `pattern` admits parses. A range it does not admit raises
`MalformedRangeError` naming the text, and that exception is what a caller sees
only when it read a document the schema never saw: a malformed `engine_api` fails
*schema* validation, and the range check is never reached. Nothing here decides
whether a range is well formed for a *manifest*; the schema does, and a second
answer in code would be the drift `pack-manifest` is written against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import cast

#: A component that varies, in the three spellings the schema's pattern admits.
_WILDCARDS: frozenset[str] = frozenset({"x", "X", "*"})

#: The operators, longest first so that `>=` is never read as `>` and then a
#: stray `=`. The single mistake a prefix scan over this table can make is the
#: reason the table is ordered rather than folded into one pattern.
_OPERATORS: tuple[tuple[str, str], ...] = (
    (">=", "at_least"),
    ("<=", "at_most"),
    (">", "above"),
    ("<", "below"),
    ("=", "equal"),
    ("^", "caret"),
    ("~", "tilde"),
)

#: One numeric component. Anchored, so `1a` is malformed rather than read as `1`.
_NUMBER = re.compile(r"^[0-9]+$")

#: A version, padded: the form every comparison in this module is made in.
_Concrete = tuple[int, int, int]


class SemverError(Exception):
    """Something this module was asked to read and could not."""


class MalformedRangeError(SemverError):
    """A range outside the grammar the manifest schema publishes.

    It carries the text rather than a position: the ranges in this project are
    clause-sized, and a reader fixing one wants to see it whole.
    """

    def __init__(self, text: str, reason: str) -> None:
        super().__init__(f"{text!r} is not a semver range: {reason}")
        self.text = text
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Comparator:
    """One comparator, read as a half-open interval over concrete versions.

    `lower` is inclusive and `upper` is exclusive, and `None` on either side is
    unbounded. Both are stored padded, so a comparison is a tuple comparison and
    nothing about the range's *shape* survives into the evaluation.
    """

    lower: _Concrete | None
    upper: _Concrete | None

    def admits(self, version: _Concrete) -> bool:
        """Whether a concrete version falls inside the interval."""
        if self.lower is not None and version < self.lower:
            return False
        return self.upper is None or version < self.upper


#: The interval with no members, which is what "above every version" and "below
#: every version" both denote. Spelled as `[0.0.0, 0.0.0)` rather than as a flag on
#: the comparator, because a version is never negative: every version this project
#: has is at or above `0.0.0`, so the half-open interval between it and itself is
#: empty by arithmetic and not by a special case a caller has to know about.
_EMPTY = Comparator(lower=(0, 0, 0), upper=(0, 0, 0))


@dataclass(frozen=True, slots=True)
class Range:
    """A parsed range: alternatives joined by `||`, comparators joined by *and*.

    A range with no comparators in an alternative is *any* version, which is what
    the bare wildcard `*` parses to -- spelled as an empty alternative rather than
    as a comparator that always matches, so that a caller counting comparators
    cannot confuse the two.
    """

    alternatives: tuple[tuple[Comparator, ...], ...]

    def admits(self, version: str) -> bool:
        """Whether a concrete version satisfies the range.

        At least one alternative must admit it, and within an alternative every
        comparator must.
        """
        parts = parse_version(version)
        return any(
            all(comparator.admits(parts) for comparator in alternative)
            for alternative in self.alternatives
        )


def satisfies(version: str, text: str) -> bool:
    """Whether a concrete version satisfies a range, in one call.

    The convenience both callers share: a validator and a resolver each hold a
    version and a range text, and neither has a use for the parsed form beyond
    asking this question.
    """
    return parse_range(text).admits(version)


def parse_range(text: str) -> Range:
    """The range a clause spells, or a `MalformedRangeError` naming it.

    The split on `||` is outermost and the split on whitespace is inner, because
    `||` binds looser: `a b || c d` is "both a and b, or both c and d".
    """
    if not isinstance(text, str) or not text.strip():
        raise MalformedRangeError(str(text), "empty")
    alternatives: list[tuple[Comparator, ...]] = []
    for alternative in text.split("||"):
        alternatives.append(tuple(_comparator(token) for token in alternative.split()))
    return Range(alternatives=tuple(alternatives))


def parse_version(text: str) -> _Concrete:
    """A concrete version's three components.

    A version and a range are different things and this is where that shows: `1.2`
    as a *range* admits every patch, and `1.2` as a *version* has no patch and is
    padded to `1.2.0`, because a comparison that kept the lengths apart would make
    `1.2` satisfy `=1.2` and not `=1.2.0`.
    """
    if not isinstance(text, str):
        raise MalformedRangeError(str(text), "not a version string")
    parts = text.split(".")
    if not 1 <= len(parts) <= 3:
        raise MalformedRangeError(text, "a version has one to three components")
    numbers: list[int] = []
    for part in parts:
        if _NUMBER.match(part) is None:
            raise MalformedRangeError(text, f"`{part}` is not a number")
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def _comparator(token: str) -> Comparator:
    """One whitespace-separated token as an interval.

    Every operator takes the same two steps: read the version as written into the
    span it denotes, then apply the operator to that span. No operator reaches
    past the span into the digits, which is what keeps `>1.2` from meaning
    `[1.2.1, ...)` and `~1.2.3` from admitting `1.2.0`.
    """
    operator, body = _split(token)
    written = _components(token, body)
    floor, roof = _span(written)
    if floor is None:
        # The version as written is every version, so an operator can only narrow
        # it downward -- and only `>` and `<` do, by excluding everything.
        return _EMPTY if operator in ("above", "below") else Comparator(None, None)
    if operator == "equal":
        return Comparator(floor, roof)
    if operator == "at_least":
        return Comparator(floor, None)
    if operator == "at_most":
        return Comparator(None, roof)
    if operator == "above":
        return Comparator(roof, None)
    if operator == "below":
        return Comparator(None, floor)
    return Comparator(floor, _ceiling(operator, floor, _open_index(written)))


def _split(token: str) -> tuple[str, str]:
    """A token's operator and the version it is applied to."""
    for symbol, name in _OPERATORS:
        if token.startswith(symbol):
            body = token[len(symbol) :]
            if not body:
                raise MalformedRangeError(token, "no version after the operator")
            return name, body
    return "equal", token


def _components(token: str, body: str) -> tuple[int | None, ...]:
    """A comparator's components, with a wildcard kept as `None`."""
    parts = body.split(".")
    if not 1 <= len(parts) <= 3:
        raise MalformedRangeError(token, "a version has one to three components")
    out: list[int | None] = []
    wildcard_seen = False
    for part in parts:
        if part in _WILDCARDS:
            wildcard_seen = True
            out.append(None)
            continue
        if wildcard_seen:
            # `1.x.3` fixes a component after one the range left open, which has
            # no reading: the wildcard already admits every patch of `1`, and the
            # `3` would be a constraint on a component the range disclaimed.
            raise MalformedRangeError(
                token, "a wildcard may not be followed by a number"
            )
        if _NUMBER.match(part) is None:
            raise MalformedRangeError(token, f"`{part}` is not a number or a wildcard")
        out.append(int(part))
    return tuple(out)


def _open_index(written: tuple[int | None, ...]) -> int:
    """The first component the version left open, or three if it left none.

    Three is the sentinel rather than a fourth component, because a version has
    three: "nothing is open" is a real case and not a missing one, and it is the
    case `^` falls back to the leftmost non-zero component for.

    A version pads out with wildcards rather than with zeros -- `1` is `1.x.x`,
    not `1.0.0` -- and that is the whole of the partial-version rule.
    """
    padded: tuple[int | None, ...] = (*written, *(None,) * (3 - len(written)))
    return next((index for index, part in enumerate(padded) if part is None), 3)


def _span(
    written: tuple[int | None, ...],
) -> tuple[_Concrete | None, _Concrete | None]:
    """The interval the version denotes as written, before the operator.

    `1` denotes `[1.0.0, 2.0.0)`, `1.2` denotes `[1.2.0, 1.3.0)` and `1.2.3`
    denotes `[1.2.3, 1.2.4)`: the written prefix is the floor, and the component
    just left of where it stopped is the one incremented for the ceiling. A
    version that was never written -- the bare wildcard -- has no prefix and so
    spans everything, which `None` on both sides says.
    """
    first = _open_index(written)
    if first == 0:
        return None, None
    floor = _padded(cast("tuple[int, ...]", written[:first]))
    return floor, _roof(floor, first - 1)


def _padded(written: tuple[int, ...]) -> _Concrete:
    """A written version padded to three components with zeros.

    Only ever called on components with no wildcard among them, which the type
    says rather than a check: padding a wildcard to zero is exactly the reading
    `_span` exists to avoid making by accident, so the wildcard case is kept out
    of this function's reach rather than handled inside it.
    """
    numbers = [*written]
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def _roof(version: _Concrete, index: int) -> _Concrete:
    """The version above one, bumped at `index` and zeroed after it.

    The exclusive bound of a span that stopped just after `index`: a span open at
    the minor admits every patch, so `1.x` ends at `2.0.0` and not at `1.0.1`.
    """
    if index == 0:
        return (version[0] + 1, 0, 0)
    if index == 1:
        return (version[0], version[1] + 1, 0)
    return (version[0], version[1], version[2] + 1)


def _ceiling(operator: str, floor: _Concrete, open_index: int) -> _Concrete:
    """The exclusive bound `^` or `~` sets above a version's floor.

    `open_index` is where the range stopped being written and is what decides how
    far the bound reaches: `^0.0.3` stops at the patch, `^0.0` at the minor and
    `^0` at the major, and the three differ only in how much was left open. When
    nothing was left open the bound has to be derived from the digits instead,
    which is the leftmost non-zero component for `^` and the minor for `~`.
    """
    if open_index < 3:
        reach = open_index - 1 if operator == "caret" else min(open_index - 1, 1)
        return _roof(floor, reach)
    major, minor, patch = floor
    if operator == "tilde":
        return (major, minor + 1, 0)
    if major > 0:
        return (major + 1, 0, 0)
    return (0, minor + 1, 0) if minor > 0 else (0, 0, patch + 1)
