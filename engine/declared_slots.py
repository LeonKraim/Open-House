"""The devices a pack declares of its own: the `slots` clause, read once.

`catalog/slots.yaml` is the house's vocabulary -- the slots a room type provides
and every pack may name -- and it is deliberately closed: a vocabulary that grew
a word every time a pack wanted one would stop being a vocabulary. But a pack's
point is sometimes a device the catalog has no word for. "Warn me when the fridge
has been open longer than ten minutes" needs a contact on the fridge door, and no
room type in the catalog provides one, because a fridge is not a room.

So a manifest may carry a `slots` clause of its own, and three rules decide what
such a declaration *means*. They are the product's rules and not this module's
invention:

  * **A declared name the vocabulary already carries reuses that slot.** A pack
    that declares `door_contact` is asking for the room's own door contact --
    the same device every other pack naming `door_contact` gets. This is the
    default because it is what a person means by "the contact sensor": one room,
    one front door, and a second copy of it would be a device nobody has.
  * **A declared name the vocabulary does not carry joins the house's
    vocabulary**, for the houses that install the pack. `fridge_contact` becomes
    bindable in the room the pack lands in, which is what makes the pack's own
    requirement fillable.
  * **`separate: true` gives the pack a device of its own.** It binds under a
    pack-qualified key, so two packs may each hold their own motion sensor while
    a third shares the room's. A pack asks for this when sharing would be wrong:
    a pack watching one specific appliance does not want the room's front door.

**The key is qualified with `__` and never with a dot.** A slot name is told from
an entity reference by shape -- a name containing a dot *is* a `domain.object_id`
(`engine/sandbox.py`) -- so a dotted slot name would be read as a pack reaching
past the binding layer, which is the one thing the sandbox refuses outright. Two
underscores are inside the slot name pattern (`^[a-z][a-z0-9_]*$`) and outside
the entity reference pattern, which is exactly the gap this needs.

This module is the single reader of the clause. The sandbox asks it which names a
pack declares, `openhouse/packs.py` asks it whether a required slot is one the
pack brought with it, `engine/behaviours/declared.py` asks it what a written slot
name binds under, and `grow_vocabulary` is what the house's vocabulary gains --
and a second reader would be a second answer to "what did this pack declare".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from engine.vocabulary import SlotDefinition, Vocabulary

__all__ = [
    "QUALIFIER",
    "DeclaredSlot",
    "declared_slots",
    "grow_vocabulary",
    "key_of",
    "optional_keys",
    "required_keys",
    "slot_keys",
]

#: What separates a pack's name from a slot's when the slot is separated. Two
#: underscores rather than a dot, for the reason the module docstring gives: a
#: dot is what makes a string an entity reference to the sandbox's shape test.
QUALIFIER = "__"


@dataclass(frozen=True, slots=True)
class DeclaredSlot:
    """One device a pack declares, as the manifest writes it.

    `name` is the *written* name -- the one the manifest's behaviours reach
    through -- and `key_of` is the name it binds under in a house, which differs
    from it exactly when `separate` is true. Keeping the two apart is what lets a
    reader of the manifest (the sandbox, the behaviour builder) work in written
    names while a reader of the house (the binding layer, the panel's slot list)
    works in keys.
    """

    name: str
    accepts_domains: tuple[str, ...]
    required: bool
    separate: bool


def declared_slots(document: Mapping[str, object]) -> tuple[DeclaredSlot, ...]:
    """Every device `document` declares, in the order the manifest lists them.

    The order is the manifest's, because a pack that declares two slots and a
    person reading the diff of two versions should see them in the order they
    were written, and a sort here would hide a reordering that was deliberate.

    A row that is not a mapping, or whose `name` is not a string, is not a row --
    the same filter `engine/manifest.py` and `engine/sandbox.py` apply to their
    own clauses, so a malformed row this reader drops is one the validator has
    already judged and refused.
    """
    rows = document.get("slots")
    if not isinstance(rows, list):
        return ()
    declared: list[DeclaredSlot] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        declared.append(
            DeclaredSlot(
                name=name,
                accepts_domains=_names(row.get("accepts_domains")),
                # `is True` and not a truthiness test: the schema types these as
                # booleans, and a manifest that wrote `required: "yes"` is one the
                # validator refuses -- reading it as true here would act on a
                # declaration the schema rejected.
                required=row.get("required") is True,
                separate=row.get("separate") is True,
            )
        )
    return tuple(declared)


def key_of(pack: str, slot: DeclaredSlot) -> str:
    """The name `slot` binds under in a house holding `pack`.

    The written name for every slot that is shared -- which is every slot by
    default, and every slot the vocabulary already carries -- and the
    pack-qualified key for one the pack asked to hold separately.
    """
    return f"{pack}{QUALIFIER}{slot.name}" if slot.separate else slot.name


def slot_keys(pack: str, document: Mapping[str, object]) -> Mapping[str, str]:
    """Every declared name, mapped to the key it binds under in `pack`'s rooms.

    Only the declared names appear. A caller resolving a slot name that the
    clause does not mention gets the name back unchanged with `.get(name, name)`,
    which is the identity for every slot the catalog carries -- and doing it that
    way rather than seeding the mapping with the whole vocabulary is deliberate:
    a mapping that appeared complete would invite a reader to treat a *missing*
    entry as "not declared", when it means "declared by the catalog, shared".
    """
    return {slot.name: key_of(pack, slot) for slot in declared_slots(document)}


def required_keys(pack: str, document: Mapping[str, object]) -> tuple[str, ...]:
    """Every slot `document` requires a room to bind, as the keys it binds under.

    Two clauses say a slot is required, and both are the pack's own words: the
    `requires_slots` list, which is where a pack names a catalog slot it cannot
    work without, and a `slots` declaration written `required: true`, which is
    where it says the same about a device it brought with it. They are one list
    here because a person configuring a room reads one list -- "these are the
    devices this module needs" -- and splitting them would give the panel two
    questions to ask and two places to be answered wrongly.

    The order is `requires_slots` first and the declarations after, each in the
    manifest's own order, so the list a refusal names is the list the author
    wrote. A duplicate -- a name in both clauses -- appears once, at its first
    position, because a device required twice is still one device.
    """
    written = _names(document.get("requires_slots")) + tuple(
        slot.name for slot in declared_slots(document) if slot.required
    )
    return _unique_keys(pack, document, written)


def optional_keys(pack: str, document: Mapping[str, object]) -> tuple[str, ...]:
    """Every slot `document` may use but does not require, as binding keys.

    `required_keys`' mirror and built the same way, because the two questions are
    one question asked twice on the panel: a room's settings page shows a pack's
    required slots as the ones it is waiting on and its optional ones as the ones
    it could also use. A declared device written `required: false` -- the default
    -- is optional, which is what makes `separate`'s own example work: a fridge
    module may offer `fridge_contact` and still run without one.
    """
    written = _names(document.get("optional_slots")) + tuple(
        slot.name for slot in declared_slots(document) if not slot.required
    )
    return _unique_keys(pack, document, written)


def grow_vocabulary(
    vocabulary: Vocabulary,
    declarations: Iterable[tuple[str, Mapping[str, object]]],
) -> Vocabulary:
    """`vocabulary` plus every device `declarations` bring, read in the order given.

    **Why the vocabulary has to grow at all.** `engine/binding.py` raises when a
    room binds a slot the vocabulary does not carry, and the vocabulary is the
    catalog's `slots.yaml` -- which by construction has no word for
    `fridge_contact`, and none for the button a bedtime pack brings. So a pack
    that brings a device has to *add it to the house's vocabulary* when it
    installs, or the room that binds the fridge's contact cannot be expressed at
    all. The declaration is the pack's, so the extension is the clause applied to
    a base vocabulary, and this is the one place it is applied: the live
    composition root reads its manifests out of the registry and hands the pairs
    here, and the simulator hands the file it just installed.

    **The catalog wins a name it already carries.** A pack declaring
    `door_contact` is reusing the room's own door contact, so the catalog's
    definition of that name is the one that stands; only a name the catalog does
    not have is added, and it is added with the `required`-ness the declaration
    states. The precedence is written as the merge order -- declared first, the
    vocabulary over the top -- rather than as a loop with a condition, because a
    name appearing on both sides is the ordinary case (every reuse) and not an
    exception to be caught.

    Two packs may declare the same *new* name, and then one of them has to be the
    device: the first declaration in the order given is the one that stands, so
    two reads of the same set at the same order add the same slot. That is why
    the order is a parameter's business rather than this function's -- a replay
    has to visit the same house twice, and the order that makes it do so is the
    caller's knowledge (pack-name ascending on the live path, install order in a
    session), not something to be re-decided here.
    """
    declared: dict[str, SlotDefinition] = {}
    for pack, document in declarations:
        for slot in declared_slots(document):
            declared.setdefault(
                key_of(pack, slot), SlotDefinition(required=slot.required)
            )
    if not declared:
        return vocabulary
    return replace(vocabulary, slots={**declared, **vocabulary.slots})


def _unique_keys(
    pack: str, document: Mapping[str, object], written: tuple[str, ...]
) -> tuple[str, ...]:
    """`written` as binding keys, each kept once at its first position.

    A device named twice -- in `requires_slots` and as a declaration, say -- is
    still one device, and the panel renders one chip for it. The first position
    is the one the manifest wrote first, which keeps a refusal's list in the order
    its author is reading.
    """
    keys = slot_keys(pack, document)
    found: list[str] = []
    seen: set[str] = set()
    for name in written:
        key = keys.get(name, name)
        if key not in seen:
            seen.add(key)
            found.append(key)
    return tuple(found)


def _names(value: object) -> tuple[str, ...]:
    """A clause that is a list of strings, or nothing when it is not one."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
