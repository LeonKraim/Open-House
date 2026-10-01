"""House modes and exclusive groups -- task 6.0.

The definitions here are written as documents rather than taken from a file,
because that is what a mode is: `schemas/mode/1.0.0.json` is the only mode
artifact in the repository, so a `ModeSet` is constructed from documents it
validates, and the interesting behaviour is what happens between two modes that
share an `exclusive_group`.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from engine.modes import (
    DuplicateModeError,
    InvalidModeError,
    ModeSet,
    UnknownModeError,
)
from engine.vocabulary import Vocabulary

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog: frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


def _mode(name: str, group: str | None = None) -> dict[str, object]:
    document: dict[str, object] = {"name": name, "description": f"the {name} mode"}
    if group is not None:
        document["exclusive_group"] = group
    return document


def _set(vocabulary: Vocabulary, *definitions: Mapping[str, object]) -> ModeSet:
    return ModeSet(list(definitions), vocabulary=vocabulary)


def _occupancy(vocabulary: Vocabulary) -> ModeSet:
    """A set with a three-member group, a one-member group, and two ungrouped modes.

    Two ungrouped modes rather than one, because "an ungrouped mode excludes
    nothing and is excluded by nothing" is a claim about ungrouped modes *with
    each other*: with only one such mode, an implementation that made every
    ungrouped mode exclusive with every other is indistinguishable from the
    correct one, because there is never a second to exclude.
    """
    return _set(
        vocabulary,
        _mode("home", "occupancy"),
        _mode("away", "occupancy"),
        _mode("sleep", "sleep_state"),
        _mode("holiday", "occupancy"),
        _mode("guest"),
        _mode("pet"),
    )


# --------------------------------------------------------------------------
# Declaring modes
# --------------------------------------------------------------------------


def test_a_mode_set_is_validated_against_the_frozen_schema(
    vocabulary: Vocabulary,
) -> None:
    """A document violating the mode schema fails, naming the row it failed at.

    A falsifying implementation that read `name` and `exclusive_group` directly
    would accept a document the schema forbids -- a name with capitals or a
    hyphen, an extra field a later phase renames -- and the mistake would surface
    far away, as a mode no behaviour can gate on.
    """
    with pytest.raises(InvalidModeError) as raised:
        _set(vocabulary, {"name": "Not A Mode"})
    assert raised.value.index == 0


def test_the_failing_row_is_the_one_named(vocabulary: Vocabulary) -> None:
    """The index points at the invalid document, not at the first one.

    A falsifying implementation that reported a constant index, or that reported
    the count, would name a row that is fine and leave the broken one to be found
    by hand.
    """
    with pytest.raises(InvalidModeError) as raised:
        _set(vocabulary, _mode("home"), _mode("away"), {"name": "broken"})
    assert raised.value.index == 2


def test_two_documents_declaring_one_name_are_refused(vocabulary: Vocabulary) -> None:
    """A duplicate name fails rather than silently replacing the first.

    The schema states a name's shape but cannot state its uniqueness within a
    set, so a falsifying implementation would keep whichever came last and the
    set's declared groups would depend on the order the documents were listed in.
    """
    with pytest.raises(DuplicateModeError) as raised:
        _set(vocabulary, _mode("home", "occupancy"), _mode("home", "sleep_state"))
    assert raised.value.name == "home"


def test_a_declared_mode_carries_its_group(vocabulary: Vocabulary) -> None:
    """`declared` projects the name and the group, and nothing else."""
    modes = _occupancy(vocabulary)
    assert sorted(modes.declared) == [
        "away",
        "guest",
        "holiday",
        "home",
        "pet",
        "sleep",
    ]
    assert modes.group_of("away") == "occupancy"
    assert modes.group_of("guest") is None


def test_a_mode_the_set_does_not_declare_is_refused(vocabulary: Vocabulary) -> None:
    """An undeclared name fails rather than reading as "not active".

    A falsifying implementation that answered `False` for any unknown name would
    let a typo gate a behaviour off forever, silently, with the log showing a
    behaviour that declined rather than a name that does not exist.
    """
    modes = _occupancy(vocabulary)
    with pytest.raises(UnknownModeError) as raised:
        modes.activate("vacation")
    assert raised.value.name == "vacation"
    with pytest.raises(UnknownModeError):
        modes.is_active("vacation")
    with pytest.raises(UnknownModeError):
        modes.deactivate("vacation")
    with pytest.raises(UnknownModeError):
        modes.group_of("vacation")


# --------------------------------------------------------------------------
# Exclusivity
# --------------------------------------------------------------------------


def test_activating_a_mode_clears_its_group_sibling(vocabulary: Vocabulary) -> None:
    """The second mode of a group replaces the first, and says which it cleared.

    A falsifying implementation that only added to the active set would leave
    `home` and `away` active together -- a house both occupied and empty -- and
    every behaviour gated on either would act on a contradiction.
    """
    modes = _occupancy(vocabulary)
    assert modes.activate("home") == ()
    assert modes.activate("away") == ("home",)
    assert modes.active == {"away"}


def test_a_mode_in_another_group_is_untouched(vocabulary: Vocabulary) -> None:
    """Exclusivity is per group, so clearing one group's siblings clears no other.

    A falsifying implementation that cleared every other active mode would make
    `sleep` unreachable alongside an occupancy mode, and a house that is both away
    and asleep -- which is what a bedtime scene sets -- could not be expressed.
    """
    modes = _occupancy(vocabulary)
    modes.activate("away")
    modes.activate("sleep")
    assert modes.active == {"away", "sleep"}


def test_a_mode_in_no_group_clears_nothing_and_is_cleared_by_nothing(
    vocabulary: Vocabulary,
) -> None:
    """An ungrouped mode is mutually exclusive with nothing, both ways.

    A falsifying implementation that treated a missing group as a group of its
    own -- say, by keying on `None` -- would make every ungrouped mode exclusive
    with every other, which is the opposite of what leaving the group out means.
    """
    modes = _occupancy(vocabulary)
    modes.activate("guest")
    assert modes.activate("pet") == ()
    assert modes.active == {"guest", "pet"}
    assert modes.activate("home") == ()
    assert modes.active == {"guest", "pet", "home"}
    modes.activate("sleep")
    assert modes.active == {"guest", "pet", "home", "sleep"}


def test_a_group_of_three_never_holds_more_than_one_member(
    vocabulary: Vocabulary,
) -> None:
    """Setting every member of a three-member group in turn leaves exactly one.

    A falsifying implementation that paired modes rather than grouping them --
    `home` clearing `away` but not `holiday` -- would end this with two active,
    and which two would depend on the order they were set in, so the house's mode
    would be a function of the scenario's script rather than of its last step.

    What this does *not* catch is an implementation that clears the previously
    activated mode rather than every group sibling: `activate` is the only writer,
    so at most one sibling is ever active for it to clear, and the two rules are
    behaviourally identical through this surface. The claim in the docstring is
    the one the fixture can actually support.
    """
    modes = _occupancy(vocabulary)
    for name in ("home", "holiday", "away"):
        modes.activate(name)
        assert modes.active == {name}
    assert modes.activate("away") == ()


def test_reactivating_an_active_mode_clears_nothing_and_keeps_it_active(
    vocabulary: Vocabulary,
) -> None:
    """Activating a mode that is already active is not a change.

    A falsifying implementation that cleared the group before adding the name
    would report the mode as having cleared itself, and a caller recording the
    cleared names would write a record saying a mode it just set had been
    unset.
    """
    modes = _occupancy(vocabulary)
    modes.activate("home")
    assert modes.activate("home") == ()
    assert modes.active == {"home"}


def test_deactivating_an_inactive_mode_does_nothing(vocabulary: Vocabulary) -> None:
    """Deactivation is idempotent, because a scenario step may repeat it."""
    modes = _occupancy(vocabulary)
    modes.deactivate("home")
    modes.activate("home")
    modes.deactivate("home")
    assert modes.active == set()


def test_the_active_set_is_empty_until_a_mode_is_set(vocabulary: Vocabulary) -> None:
    """A fresh mode set is empty, and its emptiness is not a mode.

    A falsifying implementation that defaulted to `home` -- the obvious
    convenience -- would make a freshly built house already occupied, and every
    away-gated behaviour would need the default to be reasoned about rather than
    the house's own modes to be.
    """
    modes = _occupancy(vocabulary)
    assert modes.active == frozenset()
    assert modes.is_active("home") is False


def test_the_active_set_is_a_set_and_not_a_sequence(vocabulary: Vocabulary) -> None:
    """`active` has no order, so nothing can come to depend on one.

    A falsifying implementation returning a list would give callers a stable
    iteration order that happens to hold today, and a later change to the
    declaration order would silently reorder every record written from it.
    """
    modes = _occupancy(vocabulary)
    modes.activate("away")
    modes.activate("sleep")
    active = modes.active
    assert active == {"away", "sleep"}
    assert isinstance(active, frozenset)
