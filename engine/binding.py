"""Slot binding: a house's slots resolved to the entities bound to them.

Tasks 5.0 and 5.1, `engine-core`'s first two requirements. A house document
names, per room, the entity each of the room's slots is bound to; a behaviour
asks for a slot and gets the entities behind it. Three facts about that are
normative and are the reason this module is more than a dictionary lookup:

- **A binding is a list, and where it is plural is the house scope.** The frozen
  house schema (`schemas/house/1.0.0.json`) binds one `entity_id` per slot in a
  room, so a slot becomes plural exactly by being bound in more than one room and
  read at house scope -- a house-scoped `light_group` collects several rooms'
  light groups behind the one role (`design.md` D4). A house may also bind a slot
  *itself* (`House.bindings`), which makes the list singular again: the global
  entity is the answer wherever the slot is asked for, so "all the lights" can be
  one group a person picked rather than a collection of whatever the rooms
  happened to bind.
- **An unbound slot is a state, not an error.** A slot may be absent from a
  room's `bindings`, which is how it is left unbound, and what that means is the
  slot vocabulary's `required` flag rather than this module's opinion: a required
  slot's absence skips a behaviour, an optional slot's absence degrades it
  (`engine-core`). Resolution therefore reports emptiness and the flag; it does
  not raise, because "why did nothing happen" is answered by a record the engine
  writes, not by an exception nobody catches.
- **A bound name with no device behind it is a state too, and a different one.**
  A binding keeps the *name* of the device a person chose, so removing that
  device from the house -- which the product supports, and which the mock house
  does on every restart -- leaves the name bound while the device is gone. A read
  of such a member does not raise either (`SlotRead.views`): the member reads as
  present-and-unreadable (`_absent`), which is neither unbound nor off, so the
  two paths that read every room's slots -- the tick and `Engine.repairs` --
  survive it, and the reading stays honest about what it could not see. The slot
  layer's `MISSING` (`ha_adapter.live_modules`) is the same distinction from the
  panel's side.
- **A read over a slot carries its reduction.** "Any motion" and "all motion" are
  different questions with no safe default between them, so a read names its
  reduction and a read that names none cannot be constructed (`design.md` D4).

A `House` is the house document *plus* the vocabulary it was validated against,
which is why `House.from_document` takes the vocabulary and `resolve_slot` --
whose signature the spec fixes at `(house, scope, slot)` -- does not: a slot's
`required` flag is a fact about `catalog/slots.yaml`, not about the document, and
the vocabulary travels with the house so the two cannot be paired up wrongly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import jsonschema

from engine.adapter import ChangeOrigin, EntityView, UnknownEntityError
from engine.vocabulary import Vocabulary

if TYPE_CHECKING:
    from engine.adapter import HouseAdapter


class BindingError(Exception):
    """Base for the failures this module defines."""


class InvalidHouseError(BindingError):
    """A house document that does not conform to the frozen house schema."""

    def __init__(self, pointer: str, message: str) -> None:
        super().__init__(f"the house document is invalid at {pointer}: {message}")
        self.pointer = pointer
        self.message = message


class DuplicateRoomError(BindingError):
    """Two rooms of one house share an id, so a room scope is ambiguous.

    The schema states a room id's shape but cannot state its uniqueness, so this
    is the one house-level mistake only the engine can catch. Resolving the id
    would otherwise silently reach one of the two rooms.
    """

    def __init__(self, room_id: str) -> None:
        super().__init__(f"the house has more than one room with the id {room_id!r}")
        self.room_id = room_id


class UnknownRoomError(BindingError):
    """A scope naming a room the house does not have."""

    def __init__(self, room_id: str) -> None:
        super().__init__(f"the house has no room {room_id!r}")
        self.room_id = room_id


class UnknownSlotError(BindingError):
    """A slot name no controlled vocabulary defines, or one out of its scope."""

    def __init__(self, where: str, slot: str, detail: str) -> None:
        super().__init__(f"{where} names the slot {slot!r}, which {detail}")
        self.where = where
        self.slot = slot
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RoomScope:
    """A behaviour's room, or a setting's room: a room id and nothing else."""

    room_id: str


@dataclass(frozen=True, slots=True)
class HouseScope:
    """The house itself, for the slots and settings that are not a room's."""


#: Where a slot read or a setting is addressed. A union rather than a
#: `scope: str | None`, so "the house" and "a room whose id is the empty string"
#: cannot be confused, and so a reader of a signature sees the two cases.
Scope = RoomScope | HouseScope


class Reduction(StrEnum):
    """How a read over several members becomes one answer.

    The closed set is two, and it is closed because both members are meaningful
    and neither is a default: a motion-lighting service asks whether *any* member
    reads motion, while a confirmation that a group is dark asks whether *all*
    of them do (`engine-core`; `design.md` D4).
    """

    ANY = "any"
    ALL = "all"


#: The state a member of a slot reads as when the house does not hold it -- the
#: device a person bound and then removed from Home Assistant, whose *name* the
#: binding keeps while the device is gone. It is not `"off"`, and stating why is
#: the whole of the reading: a member that is not there is not a member that is
#: off, and the two must not collapse, or a gate over a deleted motion sensor
#: would read the room as empty. It is a state no entity reports, which is what
#: lets `holds` recognise it without also reading availability -- availability is
#: the *predicate's* question, because an unavailable member is one the house
#: still holds and may still satisfy a state test (`away_shutdown._is_on` reads
#: the last state a lamp was left in). `ha_adapter.live_modules` carries the same
#: distinction as its `MISSING` status; this is the engine's half of it.
_MISSING = "missing"

#: The attributes an absent member carries. Empty, because there is no device to
#: read one from; frozen, so a reader cannot mistake it for a live attribute set.
_NO_ATTRIBUTES: Mapping[str, object] = MappingProxyType({})


def _absent(entity_id: str) -> EntityView:
    """The view a member the house does not hold reads as.

    Present enough to be *named* -- so a repair can say which device is gone and
    the reduction over the read can see the member it could not read -- and
    unreadable enough that nothing treats its silence as a reading: `available`
    is `False`, and `state` is `_MISSING` rather than a fabricated `"off"`. The
    origin is `world`, because the change that produced this reading was a device
    leaving the house and not the engine's or a user's; override detection reads
    `last_origin`, and a fabricated `user` here would suppress the very behaviour
    that noticed the device was gone.
    """
    return EntityView(
        entity_id=entity_id,
        state=_MISSING,
        attributes=_NO_ATTRIBUTES,
        available=False,
        last_origin=ChangeOrigin.WORLD,
    )


@dataclass(frozen=True, slots=True)
class SlotRead:
    """One read of a slot: the members, and the reduction the read carries.

    This is also the form a read takes in a decision record's `inputs`, which is
    why it holds the reduction rather than the caller's closure: a record naming
    `motion_sensor: any` is what makes a surprising reading inspectable.
    """

    slot: str
    entities: tuple[str, ...]
    reduction: Reduction

    def views(self, adapter: HouseAdapter) -> tuple[EntityView, ...]:
        """Read every member, in binding order.

        An unbound slot reads as an empty tuple rather than raising: the empty
        read is the observable form of an optional slot that was left unbound,
        and the fallback a behaviour builds on it (`first-behaviours`) is written
        against exactly this value.

        A member the house no longer holds is *also* read rather than raised on
        (`_absent`), and that is the shape this method exists to fix: the binding
        keeps the name a person gave it, so a name with no device behind it is an
        ordinary fact of a house somebody edits -- the mock house produces it
        every time the fleet restarts -- and the port refuses a read of an entity
        it does not hold. Raised here, that refusal took the engine's own tick and
        `Engine.repairs` down with it, because both read this over every room's
        `motion_sensor` on every iteration: one deleted device stopped the house
        deciding anything, and emptied the Rooms tab. The absent member reads as
        present-and-unreadable -- named, not available, neither on nor off -- so
        the two paths that must not raise do not, and the reading stays truthful.
        """
        found: list[EntityView] = []
        for entity_id in self.entities:
            try:
                found.append(adapter.read_entity(entity_id))
            except UnknownEntityError:
                found.append(_absent(entity_id))
        return tuple(found)

    def holds(
        self, adapter: HouseAdapter, predicate: Callable[[EntityView], bool]
    ) -> bool:
        """Whether the members satisfy `predicate`, under this read's reduction.

        The empty case follows from the reduction rather than being special-cased:
        `ANY` over no members is false and `ALL` over no members is true, the
        usual readings of "at least one" and "every". A behaviour that must tell
        "unbound" apart from "all satisfied" reads `entities`, which is the
        distinction the optional-slot fallback is built on.

        A member the house does not hold (`_absent`) satisfies nothing, under
        either reduction, and the guarantee is structural rather than left to each
        predicate to remember: `ALL` over a slot whose every member is gone is
        false -- "everything is fine" is exactly what an absent member must not be
        able to say -- while an absent member never makes `ANY` true on its own.
        This is *not* read off availability, which is deliberately the predicate's
        to judge: an unavailable member is one the house still holds and may still
        satisfy a state test, so only a member that is gone is forced false.
        """
        verdicts = tuple(
            False if view.state == _MISSING else predicate(view)
            for view in self.views(adapter)
        )
        if self.reduction is Reduction.ALL:
            return all(verdicts)
        return any(verdicts)


@dataclass(frozen=True, slots=True)
class SlotBinding:
    """A slot resolved in a scope: its members, and whether the slot is required."""

    slot: str
    entities: tuple[str, ...]
    required: bool

    @property
    def is_empty(self) -> bool:
        """True when nothing is bound to the slot in this scope."""
        return not self.entities

    def read(self, reduction: Reduction) -> SlotRead:
        """Open a read of this binding. The reduction has no default on purpose.

        A read that named no reduction would have to be given one, and every
        choice would be wrong for one of the two callers `Reduction` exists to
        serve, so the absence is a failure to construct rather than a value.
        """
        return SlotRead(slot=self.slot, entities=self.entities, reduction=reduction)


@dataclass(frozen=True, slots=True)
class Room:
    """A room, its type, and the entity each of its slots is bound to.

    `bindings` maps a slot name to a `entity_id` string. The document's binding
    object may also carry a `registry_id`, which no rule in this phase reads --
    the port addresses entities -- so it is not carried here.
    """

    id: str
    name: str
    type: str
    bindings: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class House:
    """A validated house document, with the vocabulary it was validated against.

    The only way to build one is `from_document`, so every `House` in existence
    has passed the frozen schema and named only controlled slots: this is what
    makes `resolve_slot` able to resolve a house without re-reading a document.

    `bindings` is the house's *own* binding per house-scope slot, and it is not
    part of the frozen house document: the schema binds slots per room and has no
    top-level `bindings`, and a global slot is a fact the integration holds rather
    than a fact a written-down house carries. It is the answer to "all the lights"
    meaning *one* light group somebody chose, and to a room that binds no
    `light_group` of its own still taking part in the house's automations: an
    entity bound here fills the slot in any room that left it empty, and is what
    the house scope resolves to ahead of the rooms it would otherwise collect.
    """

    name: str
    rooms: tuple[Room, ...]
    house_scope_slots: tuple[str, ...]
    vocabulary: Vocabulary
    bindings: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, object],
        *,
        vocabulary: Vocabulary,
        bindings: Mapping[str, str] | None = None,
    ) -> House:
        """Validate `document` and project it, failing by naming what is wrong."""
        _validate(document, vocabulary)
        rooms = tuple(_room(row, vocabulary) for row in _rows(document, "rooms"))
        _require_distinct(rooms)
        house_scope_slots = tuple(_house_scope_slots(document, vocabulary))
        declared = {} if bindings is None else dict(bindings)
        for slot in declared:
            # The same gate the rooms get, from the other direction: a house
            # binding is a slot name the vocabulary must know, and a name it does
            # not would resolve at house scope forever as an empty list rather
            # than as the refusal a bad binding deserves.
            if slot not in vocabulary.slots:
                raise UnknownSlotError(
                    "the house scope", slot, "no controlled vocabulary defines"
                )
        return cls(
            name=cast("str", document["name"]),
            rooms=rooms,
            house_scope_slots=house_scope_slots,
            vocabulary=vocabulary,
            bindings=declared,
        )

    def room(self, room_id: str) -> Room:
        """The room with this id, or a failure naming the id."""
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise UnknownRoomError(room_id)

    def has_room(self, room_id: str) -> bool:
        """Whether a room with this id is in the house.

        The question a caller holding an id from somewhere else -- a placement
        recorded before the room was deleted, a name a person typed -- has to ask
        before it can use `room`. Not `try: room() except UnknownRoomError`,
        because a caller that expects a miss is not asking for a failure and an
        exception caught to answer `bool` would be control flow dressed as error
        handling.
        """
        return any(room.id == room_id for room in self.rooms)


def resolve_slot(
    house: House, scope: Scope, slot: str, *, own: str | None = None
) -> SlotBinding:
    """Resolve `slot` in `scope` to the entities bound to it.

    A room scope returns that room's binding for the slot, and falls back to the
    house's own binding when the room has none; a house scope returns the house's
    own binding when it has one, and otherwise the slot's binding collected from
    every room of the house, in room order, so a slot bound in two rooms resolves
    to two entities rather than to whichever room the house happened to list
    first.

    The house's own binding therefore wins in both directions, which is what
    makes it *global*: one entity a person bound once, standing in for the slot
    everywhere -- in the house scope, and in every room that did not bind one of
    its own.

    `own` is the other way a slot can resolve, and the narrow one: the entity one
    *module* points this slot at instead of the house's binding, so a pack that
    would otherwise act on the room's `light_group` can be aimed at a lamp of its
    own without rebinding the room. It wins over both the room's binding and the
    house's, because it is the most specific statement of the three and the only
    one that is about a single module rather than about the house. The slot still
    has to be one the vocabulary carries: an override names an entity, never a
    slot, so a name no controlled vocabulary defines is refused here exactly as it
    is without one.

    `required` is the slot definition's own answer where there is one, and
    `False` where the override names a slot the vocabulary does not carry at house
    scope -- the declaration's own `required` is what makes a slot required, and
    an overridden slot nothing declares is nobody's requirement.
    """
    definition = house.vocabulary.slots.get(slot)
    if own is not None:
        if definition is None:
            where = (
                "the house scope"
                if isinstance(scope, HouseScope)
                else f"the room {scope.room_id!r}"
            )
            raise UnknownSlotError(where, slot, "no controlled vocabulary defines")
        return SlotBinding(slot=slot, entities=(own,), required=definition.required)
    if isinstance(scope, HouseScope):
        if definition is None:
            raise UnknownSlotError(
                "the house scope", slot, "no controlled vocabulary defines"
            )
        if slot not in house.house_scope_slots:
            raise UnknownSlotError(
                "the house scope",
                slot,
                "is not among the slots the house makes available at house scope",
            )
        own_binding = house.bindings.get(slot)
        if own_binding is not None:
            return SlotBinding(
                slot=slot, entities=(own_binding,), required=definition.required
            )
        return SlotBinding(
            slot=slot,
            entities=tuple(
                entity_id
                for room in house.rooms
                if (entity_id := room.bindings.get(slot)) is not None
            ),
            required=definition.required,
        )
    if definition is None:
        raise UnknownSlotError(
            f"the room {scope.room_id!r}", slot, "no controlled vocabulary defines"
        )
    room = house.room(scope.room_id)
    entity_id = room.bindings.get(slot)
    if entity_id is None:
        entity_id = house.bindings.get(slot)
    return SlotBinding(
        slot=slot,
        entities=() if entity_id is None else (entity_id,),
        required=definition.required,
    )


# --------------------------------------------------------------------------
# Validation and projection. The document conforms before any of these run, so
# the casts below are the schema's own guarantees rather than hopeful ones.
# --------------------------------------------------------------------------


def _validate(document: Mapping[str, object], vocabulary: Vocabulary) -> None:
    """Fail on a schema violation, then on a slot name the vocabulary lacks.

    Both halves are `engine-core`'s resolution requirement: a house is validated
    against `schemas/house/1.0.0.json`, and every slot name it mentions is
    resolved against `catalog/slots.yaml`. The schema describes a slot name's
    *shape*; only the vocabulary can say whether the name exists.
    """
    validator = jsonschema.Draft202012Validator(vocabulary.house_schema)
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(part) for part in first.absolute_path)
        raise InvalidHouseError(pointer, first.message)
    for room in _rows(document, "rooms"):
        for slot in _bindings(room):
            if slot not in vocabulary.slots:
                raise UnknownSlotError(
                    f"the room {cast('str', room['id'])!r}",
                    slot,
                    "no controlled vocabulary defines",
                )


def _rows(document: Mapping[str, object], field: str) -> Sequence[Mapping[str, object]]:
    return cast("Sequence[Mapping[str, object]]", document[field])


def _bindings(room: Mapping[str, object]) -> Mapping[str, object]:
    return cast("Mapping[str, object]", room["bindings"])


def _room(row: Mapping[str, object], vocabulary: Vocabulary) -> Room:
    bindings = {
        slot: cast("Mapping[str, object]", binding)["entity_id"]
        for slot, binding in _bindings(row).items()
    }
    return Room(
        id=cast("str", row["id"]),
        name=cast("str", row["name"]),
        type=cast("str", row["type"]),
        bindings=cast("Mapping[str, str]", bindings),
    )


def _require_distinct(rooms: Sequence[Room]) -> None:
    seen: set[str] = set()
    for room in rooms:
        if room.id in seen:
            raise DuplicateRoomError(room.id)
        seen.add(room.id)


def _house_scope_slots(
    document: Mapping[str, object], vocabulary: Vocabulary
) -> list[str]:
    house_scope = cast("Mapping[str, object]", document["house_scope"])
    slots = cast("Sequence[str]", house_scope["slots"])
    for slot in slots:
        if slot not in vocabulary.slots:
            raise UnknownSlotError(
                "the house scope", slot, "no controlled vocabulary defines"
            )
        if slot not in vocabulary.house_slots:
            raise UnknownSlotError(
                "the house scope",
                slot,
                "is not among the slots the vocabulary makes available at house scope",
            )
    return list(slots)
