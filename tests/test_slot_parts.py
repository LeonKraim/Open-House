"""A slot split into parts: one role, several devices, still one slot's name.

`ha_adapter.slot_parts` is pure -- a record in, a record out, a refusal beside it
-- so what is asserted here is the two claims the rest of the product rests on:

* **A part is a key of its own under the parent's name** (`light_group__a`), which
  is what makes two modules naming one part act on one bound entity rather than
  each on a device of its own.
* **A key is a part only when the record says so.** The joiner is shared with a
  pack's own device (`fridge_guard__fridge_contact`), so a reader that went by the
  shape of the string would call every separated pack slot a part of its pack --
  the case the last test here is about.

The growth into the vocabulary is asserted too, because that is what makes a part
*bindable* at all: `engine/binding.py` refuses a binding for a name the vocabulary
does not carry, so a part that is not grown is a part no room can be given a
device for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import vocabulary
from ha_adapter import slot_parts

ROOT = Path(__file__).resolve().parents[1]


def _record(**parents: list[str]) -> dict[str, tuple[str, ...]]:
    return {
        parent: tuple(names) for parent, names in ((k, v) for k, v in parents.items())
    }


# --------------------------------------------------------------------------
# The key, and how a key is read back
# --------------------------------------------------------------------------


def test_a_part_binds_under_its_slot_s_name_and_its_own() -> None:
    """The whole of what a part is, in one string.

    `__` and not a dot, for the reason `engine/declared_slots` gives: a dotted
    name reads as an entity reference to the sandbox, and a binding key that read
    as an entity would be a module reaching past the binding layer.
    """
    assert slot_parts.key_of("light_group", "a") == "light_group__a"
    # A parent may itself be a pack-qualified key, and the part still hangs off the
    # end of it: a pack's own device is a slot, and a slot can be split.
    assert slot_parts.key_of("fridge_guard__fridge_contact", "left") == (
        "fridge_guard__fridge_contact__left"
    )


def test_the_record_decides_whether_a_key_is_a_part() -> None:
    """**The claim the joiner alone cannot make.**

    `fridge_guard__fridge_contact` is a pack's own device and not a part of
    `fridge_guard`: nothing split `fridge_guard`, and the name after the joiner is
    the pack's. The record is what tells them apart, and it is asked rather than
    the shape of the string.
    """
    record = _record(light_group=["a", "b"])
    assert slot_parts.split(record, "light_group__a") == ("light_group", "a")
    # A slot that has never been split is a slot, however many joiners it carries.
    assert slot_parts.split(record, "fridge_guard__fridge_contact") is None
    assert slot_parts.split(record, "light_group") is None
    # And a part of a split pack slot splits at the *last* joiner, so the parent
    # comes back whole.
    split = _record(**{"fridge_guard__fridge_contact": ["left"]})
    assert slot_parts.split(split, "fridge_guard__fridge_contact__left") == (
        "fridge_guard__fridge_contact",
        "left",
    )
    # A name that is not in the record is not a part, even under a real parent.
    assert slot_parts.split(record, "light_group__c") is None


# --------------------------------------------------------------------------
# Adding, renaming, removing
# --------------------------------------------------------------------------


def test_a_part_is_added_in_the_order_it_was_asked_for() -> None:
    """The order is the person's, because it is the order the row draws them in."""
    record: dict[str, tuple[str, ...]] = {}
    record = slot_parts.add(record, "light_group", "b")
    record = slot_parts.add(record, "light_group", "a")
    assert slot_parts.parts_of(record, "light_group") == ("b", "a")
    # A second add of the same name asks for the part that is already there: the
    # house a person was looking at, not an error and not a move.
    again = slot_parts.add(record, "light_group", " b ")
    assert again == record


def test_a_rename_keeps_the_part_s_place_and_refuses_a_collision() -> None:
    record = slot_parts.add(slot_parts.add({}, "light_group", "a"), "light_group", "b")
    renamed = slot_parts.rename(record, "light_group", "a", "kitchen")
    assert slot_parts.parts_of(renamed, "light_group") == ("kitchen", "b")
    # Renaming onto a name the slot already has would leave two parts with one
    # name and one device between them, so it is refused and not silently merged.
    with pytest.raises(slot_parts.PartError, match="already has a part"):
        slot_parts.rename(record, "light_group", "a", "b")
    with pytest.raises(slot_parts.PartError, match="no part 'c'"):
        slot_parts.rename(record, "light_group", "c", "d")


def test_the_last_part_removed_takes_the_slot_out_of_the_record() -> None:
    """A slot that was never split and one whose parts are gone say the same thing.

    Keeping an empty tuple would give "has this slot been split" two answers, and
    the panel asks it once per row.
    """
    record = slot_parts.add({}, "light_group", "a")
    assert slot_parts.remove(record, "light_group", "a") == {}
    with pytest.raises(slot_parts.PartError, match="nothing to take away"):
        slot_parts.remove(record, "light_group", "b")
    # And removing one of two leaves the other exactly where it was.
    two = slot_parts.add(record, "light_group", "b")
    assert slot_parts.parts_of(
        slot_parts.remove(two, "light_group", "a"), "light_group"
    ) == ("b",)


@pytest.mark.parametrize("name", ["", "   ", "A", "1a", "light group", "a-b", "a.b"])
def test_a_name_a_slot_could_not_be_called_is_refused(name: str) -> None:
    """A part is joined onto its slot's name, so it has to read like one.

    The pattern is the slot name pattern (`engine/declared_slots.py`), because the
    key the part makes has to be a name the vocabulary can carry -- a name that
    would not be one on its own would make a key no binding could fill.
    """
    with pytest.raises(slot_parts.PartError):
        slot_parts.add({}, "light_group", name)


def test_a_name_containing_the_joiner_is_refused() -> None:
    """`a__b` would read as a part of a part, which is one level too many."""
    with pytest.raises(slot_parts.PartError, match="cannot be a part's name"):
        slot_parts.add({}, "light_group", "a__b")


# --------------------------------------------------------------------------
# What a stored record is read as
# --------------------------------------------------------------------------


def test_a_stored_record_is_read_leniently_and_a_slot_with_half_a_record_is_not() -> (
    None
):
    """The state file is a file: a row this version cannot read is dropped.

    A refusal here would be a person locked out of their own panel by a file an
    older version wrote, and there is nothing to report that the row's own
    controls cannot fix. A row that is not a list at all drops with the whole
    slot, because half a record is what a module may be naming.
    """
    assert slot_parts.record_from({"light_group": ["a", "b"]}) == {
        "light_group": ("a", "b")
    }
    assert slot_parts.record_from({"light_group": "a"}) == {"light_group": ()}
    assert slot_parts.record_from({"light_group": ["a", "a", "B", 4, ""]}) == {
        "light_group": ("a",)
    }
    assert slot_parts.record_from(["light_group"]) == {}
    assert slot_parts.record_from(None) == {}
    # A parent that is not a name is not a parent.
    assert slot_parts.record_from({"": ["a"], "light_group": ["a"]}) == {
        "light_group": ("a",)
    }


# --------------------------------------------------------------------------
# The vocabulary a part has to join
# --------------------------------------------------------------------------


def test_parts_are_grown_into_the_vocabulary_and_are_never_required() -> None:
    """**Why a part is bindable at all.**

    `engine/binding.py` refuses a binding for a name the vocabulary does not
    carry, so a part that is not grown is a part no room can be given a device
    for. And it is grown *not required*: the parent's requirement is about the
    parent, and a room that bound the parent and split nothing has not failed to
    bind anything.
    """
    base = vocabulary.Vocabulary.load(ROOT)
    grown = slot_parts.grow(base, {"light_group": ("a", "b")})

    assert "light_group__a" in grown.slots
    assert "light_group__b" in grown.slots
    # The parent keeps the definition it had: the parts are added, not replaced.
    assert grown.slots["light_group"] == base.slots["light_group"]
    assert grown.slots["light_group__a"].required is False
    # No parts, no new vocabulary -- the same object, so a house that has split
    # nothing costs nothing.
    assert slot_parts.grow(base, {}) is base


def test_the_catalog_wins_a_name_it_already_carries() -> None:
    """A part key that collides with a real slot is the slot.

    The same precedence `grow_vocabulary` states and the same merge order: the
    catalog's words are the ones the rest of the house is written against.
    """
    base = vocabulary.Vocabulary.load(ROOT)
    assert "light_group__a" not in base.slots
    # The one name the catalog does carry is the parent's own: a part added under
    # a slot the house already knows leaves that slot exactly as it was.
    grown = slot_parts.grow(base, {"light_group__light_group": ("a",)})
    assert grown.slots["light_group"] == base.slots["light_group"]
