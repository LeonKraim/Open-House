"""A slot split into parts: two modules sharing one role, each on its own device.

The requirement, in the user's words: see each module reaching a slot, and split a
slot into however many parts so each module can have its own part or two or three
can share one -- **all parts still "the same slot", same name**.

That last clause is the whole design, and it is what makes a part a *binding key*
rather than a per-module device. Two modules naming part `a` of `light_group` act
on one entity, because the part is bound once for the room exactly as the slot is
-- and sharing is only a promise a house can keep if there is one answer to look
up. A part that each module could point somewhere of its own would be a slot per
module, which is `live_modules.slot_overrides` and is a different feature: that
one is *this module only*, and this one is *these modules together*.

So a part is `parent__part`, reusing the `__` joiner `engine.declared_slots`
already uses for a pack's own device (`QUALIFIER`), for the same reason: the
string is a slot name to every reader that has one -- the vocabulary, the binding
layer, the panel's slot list -- and the joiner is what says which slot it is a
part of. Two underscores and not a dot, because a dot is what makes a string an
entity reference (`engine/sandbox.py`), and a binding key that read as an entity
would be a module reaching past the binding layer.

**Why the vocabulary has to grow.** `engine/binding.py` raises when a room binds
a slot the vocabulary does not carry (`grow`), so the parts record is grown into
the vocabulary at live composition exactly as a pack's declarations are
(`engine.declared_slots.grow_vocabulary`). A part is never *required*: the
parent's requirement is about the parent, and a room that binds the parent and no
part is a room that has not split it.

Nothing here talks to Home Assistant, and nothing here writes: the record goes in,
a new record comes out, and the refusal a person reads comes out beside it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace

from engine.declared_slots import QUALIFIER
from engine.vocabulary import SlotDefinition, Vocabulary

__all__ = [
    "PartError",
    "add",
    "grow",
    "key_of",
    "parts_of",
    "record_from",
    "remove",
    "rename",
    "split",
]

#: The parts of every slot, by parent binding key: `{"light_group": ("a", "b")}`.
#:
#: A mapping rather than rows of `{parent, name}` because the two questions asked
#: of it are "which parts does this slot have" and "which slots have parts at all",
#: and a mapping answers both without a grouping pass. Order is the order a person
#: added them, which is the order the row draws them.
SlotParts = Mapping[str, tuple[str, ...]]

#: What a part's name may be, which is what a *slot* name may be
#: (`^[a-z][a-z0-9_]*$`, `engine/declared_slots.py`): a part is joined onto its
#: parent with `__` and the result has to be a name the vocabulary can carry, so a
#: name that would not be one on its own would make a key no binding could fill.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class PartError(ValueError):
    """A part that cannot be made out of what was asked for.

    Its own type and not `LiveSessionError`, for the reason `AuthoringError` is
    the cast vocabulary's own: this layer has no session to refuse about, and the
    caller that does (`live_modules`) turns this into the one error type a
    command path reports.
    """


def key_of(parent: str, part: str) -> str:
    """The binding key `part` of `parent` binds under: `light_group__a`.

    The one function that knows the joiner, so a reader that has a key and a
    writer that makes one cannot disagree about which slot a part is part of.
    """
    return f"{parent}{QUALIFIER}{part}"


def split(record: SlotParts, key: str) -> tuple[str, str] | None:
    """The slot and the part a binding key names, or `None` when it names a slot.

    **The record decides, not the shape of the string.** A pack's own device binds
    under a key with the same joiner in it (`fridge_guard__fridge_contact`), so a
    reader that split on `__` alone would call that a part of `fridge_guard` --
    and it is not: nothing has split `fridge_guard`, and the name after the joiner
    is a pack's, not a person's. So the split is proposed by the string and
    confirmed by the record, which is the only thing that knows which keys are
    parts of which slots.

    Split at the **last** joiner, which is where the string's own rule puts it: a
    part's name may not contain `__` (`_checked`), so the final one is the joiner
    -- including when the parent is itself a pack-qualified key.
    """
    parent, sep, part = key.rpartition(QUALIFIER)
    if not sep or part not in parts_of(record, parent):
        return None
    return parent, part


def parts_of(record: SlotParts, parent: str) -> tuple[str, ...]:
    """The parts `parent` has been split into, in the order they were added."""
    return tuple(record.get(parent, ()))


def record_from(value: object) -> dict[str, tuple[str, ...]]:
    """The parts record a stored mapping spells, read without refusing anything.

    The *reporting* reader, the same one `slot_rules.recorded_rule` is: the state
    file was written by an earlier version, or by hand, or by a run that died
    between two writes, and a house that refused to load because its parts file
    had a row it did not like would be a person locked out of their own panel. A
    row that is not a list of names is dropped -- the whole slot's parts with it,
    because a slot with half a record is worse than one with none: the missing
    half is what a module may be naming.
    """
    if not isinstance(value, Mapping):
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for parent, names in value.items():
        if not isinstance(parent, str) or not parent:
            continue
        found[parent] = _names(names)
    return found


def add(record: SlotParts, parent: str, name: str) -> dict[str, tuple[str, ...]]:
    """`record` with `name` as a new part of `parent`, in the spelling it keeps.

    Adding a part that is already there is **not** a refusal: a person who adds
    `a` twice has asked for the part they already have, and the answer they want
    is the house they were looking at. It is not a rename either -- the name
    stays where it was in the order -- because a second add of an existing name
    says nothing about where it should move to.
    """
    clean = _checked(name)
    parts = list(parts_of(record, parent))
    if clean not in parts:
        parts.append(clean)
    return {**record, parent: tuple(parts)}


def rename(
    record: SlotParts, parent: str, was: str, name: str
) -> dict[str, tuple[str, ...]]:
    """`record` with one part of `parent` renamed, in the position it held.

    The position is kept deliberately: a person reordering a list of parts by
    renaming one would be a surprise, and every other list in the product keeps
    the order it was written in.
    """
    clean = _checked(name)
    parts = list(parts_of(record, parent))
    if was not in parts:
        raise PartError(
            f"the slot {parent!r} has no part {was!r}, so there is nothing to rename"
        )
    if clean in parts and clean != was:
        raise PartError(
            f"the slot {parent!r} already has a part {clean!r}: renaming this "
            "one onto it would leave two parts with one name and one device "
            "between them"
        )
    return {**record, parent: tuple(clean if part == was else part for part in parts)}


def remove(record: SlotParts, parent: str, name: str) -> dict[str, tuple[str, ...]]:
    """`record` with one part of `parent` taken away.

    A slot with no parts left **drops out of the record** rather than keeping an
    empty tuple, so the stored file says the same thing about a slot that was
    never split as about one whose last part was removed -- which is what makes
    "has this slot been split" a question with one answer.
    """
    parts = [part for part in parts_of(record, parent) if part != name]
    if len(parts) == len(parts_of(record, parent)):
        raise PartError(
            f"the slot {parent!r} has no part {name!r}, so there is nothing to "
            "take away"
        )
    if not parts:
        return {key: value for key, value in record.items() if key != parent}
    return {**record, parent: tuple(parts)}


def grow(vocabulary: Vocabulary, record: SlotParts) -> Vocabulary:
    """`vocabulary` plus every part the record names, as bindable slots.

    The same growth a pack's declarations get (`grow_vocabulary`) and for the same
    reason: a room binds a part, and `engine/binding.py` refuses a binding for a
    name the vocabulary does not carry. A part is added **not required**, because
    the parent's requirement is the parent's: a room that bound `light_group` and
    split nothing has not failed to bind anything.

    The vocabulary wins a name it already carries, the same precedence
    `grow_vocabulary` states and the same merge order -- a part key that collides
    with a real slot is a slot, because the catalog's words are the ones the rest
    of the house is already written against.
    """
    declared: dict[str, SlotDefinition] = {}
    for parent, names in record.items():
        for name in names:
            declared.setdefault(key_of(parent, name), SlotDefinition(required=False))
    if not declared:
        return vocabulary
    return replace(vocabulary, slots={**declared, **vocabulary.slots})


def _checked(name: str) -> str:
    """The part name a person asked for, or a refusal saying what a name may be."""
    clean = name.strip() if isinstance(name, str) else ""
    if not clean:
        raise PartError(
            "a part needs a name: it is how the row it belongs to is told apart from the slot's own device"
        )
    if not _NAME.match(clean):
        raise PartError(
            f"{clean!r} is not a name a part can have: a part is joined onto its "
            "slot's name, so it has to read like one -- lower-case letters, "
            "digits and underscores, starting with a letter"
        )
    if QUALIFIER in clean:
        raise PartError(
            f"{clean!r} cannot be a part's name: {QUALIFIER!r} is what joins a "
            "part to its slot, so a name containing it would read as a part of a "
            "part"
        )
    return clean


def _names(value: object) -> tuple[str, ...]:
    """A stored list of part names, keeping only what could be one, in order."""
    if not isinstance(value, (list, tuple)):
        return ()
    found: list[str] = []
    for item in value:
        if isinstance(item, str) and item and _NAME.match(item) and item not in found:
            found.append(item)
    return tuple(found)
