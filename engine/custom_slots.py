"""The devices a *person* declares of their own: the house's own slot words.

`catalog/slots.yaml` is the house's vocabulary and `engine/declared_slots.py` is
how a *pack* adds to it. Between them there was no way for a person to add one,
and the gap showed up exactly where a person meets the vocabulary: importing a
blueprint. A blueprint that toggles a helper asks for a device the catalog has no
word for -- there is no room type whose lighting is a boolean, and no pack was
installed to bring one -- so the import screen offered the house's slots, none of
them fitted, and the row could not be answered at all. The person typed a name,
and the refusal told them the house has no such slot, which is true and is not
something they could do anything about.

So a house may declare slots of its own. Three rules decide what such a
declaration means, and they are the product's rules rather than this module's
invention:

  * **A declared name is the house's, not a room's.** It is bindable in every
    room and, when the house binds it, in the house scope -- because the reason a
    person declares one is a blueprint that follows its room, and a role that
    only one room could fill would be a device wearing a role's name.
  * **A declared name is never required.** `required` is a fact about
    `catalog/slots.yaml` (`engine/vocabulary.py`), and a word a person coined is
    not a word any room type promised. A room that binds nothing for it is a room
    that has not answered, not a room that is incomplete.
  * **A declared name carries the domains it accepts**, and it must carry at
    least one. This is the rule with teeth: what a slot accepts is what the
    panel's picker filters by and what `rank_candidates` skips on, so a slot
    accepting nothing would be a row that offers no device at all -- a role a
    person could declare and then never bind, which is the dead end the
    declaration exists to remove.

**Why the name is derived rather than taken as typed.** A slot name is
`^[a-z][a-z0-9_]*$` (`engine/declared_slots.py`, `ha_adapter/slot_parts.py`), and
a person at an import screen types what they call the thing -- "Toggle Helper",
with the capitals and the space their language uses. `slug` is the one function
that turns one into the other, so the panel, the command layer and any later
reader agree about what the person's words become, and nobody has to be told that
a slot name may not have a space in it.

Nothing here talks to Home Assistant, and nothing here writes: the record goes
in, a new record comes out, and the refusal a person reads comes out beside it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace

from engine.declared_slots import QUALIFIER
from engine.vocabulary import SlotDefinition, Vocabulary

__all__ = [
    "CustomSlots",
    "SlotError",
    "add",
    "domains",
    "grow",
    "names",
    "record_from",
    "remove",
    "slug",
]

#: The slots a person declared, by name, each with the domains it accepts:
#: `{"toggle_helper": ("input_boolean",)}`.
#:
#: A mapping rather than a list of rows because the two questions asked of it are
#: "does this house have this slot" and "what does this slot accept", and a
#: mapping answers both without a scan. The order is the order a person declared
#: them, which is the order the panel lists them in.
CustomSlots = Mapping[str, tuple[str, ...]]

#: What a slot name may be, which is what every other slot name may be: the
#: pattern `ha_adapter/slot_parts.py` holds a part to, and for the same reason --
#: the name is a binding key, and a key the binding layer could not carry would
#: be a slot a room could be shown and never bound.
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: What a domain may be. Home Assistant's own spelling, which is the same shape
#: as a slot name and deliberately not shared with it: two rules that happen to
#: agree today are two rules, and folding them into one would make a change to
#: either a change to both.
_DOMAIN = re.compile(r"^[a-z][a-z0-9_]*$")

#: The separator a slug uses where a person wrote something that is not a letter
#: or a digit. Underscores and not hyphens: the name pattern has no hyphen in it,
#: so a slug that kept one would produce a name `_checked` then refuses.
_SEPARATOR = "_"


class SlotError(ValueError):
    """A slot that cannot be declared out of what was asked for.

    Its own type and not `LiveSessionError`, for the reason `slot_parts.PartError`
    is the parts layer's own: this module has no session to refuse about, and the
    caller that does turns this into the one error type a command path reports.
    """


def slug(text: str) -> str:
    """A person's words as a slot name: `"Toggle Helper"` -> `"toggle_helper"`.

    Every run of characters that is not a letter or a digit becomes one
    underscore, and the whole is lower-cased -- so the name a person reads on the
    screen and the key the house binds under are the same words, which is the
    only thing that makes a name they typed recognisable as the slot they got.

    Runs collapse rather than repeat, because `"Front Door "` and `"Front  Door"`
    are one name to the person who typed them and two keys to a house that kept
    every space. A leading digit is prefixed rather than dropped -- `"3D Printer"`
    is `"s_3d_printer"` and not `"d_printer"`, because a name a person cannot
    recognise is worse than a name with a letter in front of it.

    The empty answer is returned as the empty string and not refused here:
    `_checked` is where a name is judged, and a caller that slugged `"  "` should
    read the same sentence as a caller that typed nothing at all.
    """
    folded = re.sub(r"[^a-z0-9]+", _SEPARATOR, text.lower()).strip(_SEPARATOR)
    if not folded:
        return ""
    return f"s_{folded}" if folded[0].isdigit() else folded


def add(
    record: CustomSlots, name: str, domains: Iterable[str] = ()
) -> dict[str, tuple[str, ...]]:
    """`record` with `name` declared, accepting `domains`.

    Declaring a name the house already declared is **not** a refusal: a person who
    declares `toggle_helper` twice has asked for the slot they already have, and
    the answer they want is the house they were looking at. The second call's
    domains replace the first's, because a declaration's domains are the whole of
    what it says about the slot and a person who narrowed them meant to.

    The name is taken as the caller passes it and *not* slugged here: `slug` is
    the panel's to call, where the person's words are, and a layer that slugged on
    its own would make `"Toggle Helper"` and `"toggle_helper"` two names at one
    end of the call and one name at the other.
    """
    clean = _checked(name)
    accepted = _accepted(domains)
    if not accepted:
        raise SlotError(
            f"the slot {clean!r} needs at least one domain: what a slot accepts is "
            "what a room's picker is filtered by, so a slot that accepts nothing "
            "is a row no device can be chosen for -- a role that could be declared "
            "and never bound"
        )
    return {**record, clean: accepted}


def remove(record: CustomSlots, name: str) -> dict[str, tuple[str, ...]]:
    """`record` without `name`, or a refusal when it declares no such slot.

    A refusal rather than an empty answer, because the caller is a control a
    person pressed and the two situations it could mean -- "I removed it" and "it
    was never there" -- are different things to be told.
    """
    if name not in record:
        raise SlotError(
            f"this house has no slot of its own called {name!r}, so there is "
            "nothing to take away"
        )
    return {key: value for key, value in record.items() if key != name}


def domains(record: CustomSlots) -> dict[str, tuple[str, ...]]:
    """Every declared slot's accepted domains, as the catalog joins them in.

    A copy rather than the record itself, so a reader that merges this into a
    catalog cannot reach back into the session state through the mapping it was
    handed. The *shape* is the catalog's -- `Catalog.slot_domains` is exactly
    this -- because a declared slot has to answer the panel's questions the same
    way a catalog one does, and the join is what makes it.
    """
    return {name: tuple(accepted) for name, accepted in record.items()}


def names(record: CustomSlots) -> tuple[str, ...]:
    """Every declared slot's name, in the order they were declared."""
    return tuple(record)


def record_from(value: object) -> dict[str, tuple[str, ...]]:
    """The record a stored mapping spells, read without refusing anything.

    The *reporting* reader, the same one `slot_parts.record_from` is: the store
    was written by an earlier version, or by hand, or by a run that died between
    two writes, and a house that refused to load because its own slot file carried
    a row it did not like would be a person locked out of their own panel. A row
    whose name is not a name, or whose domains are not a list of domains, is
    dropped -- and dropping it takes the whole slot with it, because a slot with
    half a declaration is worse than none: the half that is missing is what a room
    would be offered a picker for.
    """
    if not isinstance(value, Mapping):
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for name, accepted in value.items():
        if not isinstance(name, str) or not _NAME.match(name):
            continue
        declared = _accepted(accepted if isinstance(accepted, (list, tuple)) else ())
        if declared:
            found[name] = declared
    return found


def grow(vocabulary: Vocabulary, record: CustomSlots) -> Vocabulary:
    """`vocabulary` plus every slot the record declares, as bindable slots.

    The same growth a pack's declarations and a slot's parts get
    (`declared_slots.grow_vocabulary`, `slot_parts.grow`) and for the same reason:
    a room binds the slot, and `engine/binding.py` refuses a binding for a name the
    vocabulary does not carry. A declared slot is added **not required**, which is
    the one place this growth differs from a pack's: a pack says its device is
    required of the rooms it lands in, and a person declaring a slot for a
    blueprint is saying no such thing about any room.

    The vocabulary wins a name it already carries, the same precedence and the
    same merge order the other two state -- a declared name that collides with a
    catalog word is the catalog's word, because the rest of the house is already
    written against it. The command layer refuses that collision before it is ever
    stored (`OpenHouseHost.async_declare_slot`), so this is the belt to that
    braces: a store written by hand cannot make a house read one word two ways.
    """
    declared: dict[str, SlotDefinition] = {}
    for name in record:
        declared.setdefault(name, SlotDefinition(required=False))
    if not declared:
        return vocabulary
    return replace(vocabulary, slots={**declared, **vocabulary.slots})


def _checked(name: str) -> str:
    """The slot name a person asked for, or a refusal saying what a name may be."""
    clean = name.strip() if isinstance(name, str) else ""
    if not clean:
        raise SlotError("a slot needs a name: it is what a room binds a device to")
    if not _NAME.match(clean):
        raise SlotError(
            f"{clean!r} is not a name a slot can have: it is the key a room binds "
            "under, so it has to read like one -- lower-case letters, digits and "
            "underscores, starting with a letter"
        )
    if QUALIFIER in clean:
        raise SlotError(
            f"{clean!r} cannot be a slot's name: {QUALIFIER!r} is what joins a "
            "part to the slot it was split from and a pack to a device it brought, "
            "so a name containing it would read as a part of a slot nobody split"
        )
    return clean


def _accepted(domains: Iterable[str]) -> tuple[str, ...]:
    """The domains a declaration names, each kept once at its first position.

    A domain that is not a domain is dropped rather than refused, and the
    *caller* is where an empty answer is refused (`add`): the reader that restores
    a store has to tolerate a row it cannot use, and a validator here would make
    that reader a second place the rules live.
    """
    found: list[str] = []
    for domain in domains:
        if isinstance(domain, str) and _DOMAIN.match(domain) and domain not in found:
            found.append(domain)
    return tuple(found)
