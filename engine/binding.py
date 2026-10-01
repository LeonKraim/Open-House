"""Slot binding: a house's slots resolved to the entities bound to them.

Tasks 5.0 and 5.1, `engine-core`'s first two requirements. A house document
names, per room, the entity each of the room's slots is bound to; a behaviour
asks for a slot and gets the entities behind it. Three facts about that are
normative and are the reason this module is more than a dictionary lookup:

- **A binding is a list, and where it is plural is the house scope.** The frozen
  house schema (`schemas/house/1.0.0.json`) binds one `entity_id` per slot in a
  room, so a slot becomes plural exactly by being bound in more than one room and
  read at house scope -- a house-scoped `light_group` collects several rooms'
  light groups behind the one role (`design.md` D4).
- **An unbound slot is a state, not an error.** A slot may be absent from a
  room's `bindings`, which is how it is left unbound, and what that means is the
  slot vocabulary's `required` flag rather than this module's opinion: a required
  slot's absence skips a behaviour, an optional slot's absence degrades it
  (`engine-core`). Resolution therefore reports emptiness and the flag; it does
  not raise, because "why did nothing happen" is answered by a record the engine
  writes, not by an exception nobody catches.
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
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import jsonschema

from engine.vocabulary import Vocabulary

if TYPE_CHECKING:
    from engine.adapter import EntityView, HouseAdapter


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
        """
        return tuple(adapter.read_entity(entity_id) for entity_id in self.entities)

    def holds(
        self, adapter: HouseAdapter, predicate: Callable[[EntityView], bool]
    ) -> bool:
        """Whether the members satisfy `predicate`, under this read's reduction.

        The empty case follows from the reduction rather than being special-cased:
        `ANY` over no members is false and `ALL` over no members is true, the
        usual readings of "at least one" and "every". A behaviour that must tell
        "unbound" apart from "all satisfied" reads `entities`, which is the
        distinction the optional-slot fallback is built on.
        """
        verdicts = tuple(predicate(view) for view in self.views(adapter))
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
    """

    name: str
    rooms: tuple[Room, ...]
    house_scope_slots: tuple[str, ...]
    vocabulary: Vocabulary

    @classmethod
    def from_document(
        cls, document: Mapping[str, object], *, vocabulary: Vocabulary
    ) -> House:
        """Validate `document` and project it, failing by naming what is wrong."""
        _validate(document, vocabulary)
        rooms = tuple(_room(row, vocabulary) for row in _rows(document, "rooms"))
        _require_distinct(rooms)
        house_scope_slots = tuple(_house_scope_slots(document, vocabulary))
        return cls(
            name=cast("str", document["name"]),
            rooms=rooms,
            house_scope_slots=house_scope_slots,
            vocabulary=vocabulary,
        )

    def room(self, room_id: str) -> Room:
        """The room with this id, or a failure naming the id."""
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise UnknownRoomError(room_id)


def resolve_slot(house: House, scope: Scope, slot: str) -> SlotBinding:
    """Resolve `slot` in `scope` to the entities bound to it.

    A room scope returns that room's binding for the slot; a house scope returns
    the slot's binding collected from every room of the house, in room order, so
    a slot bound in two rooms resolves to two entities rather than to whichever
    room the house happened to list first.
    """
    definition = house.vocabulary.slots.get(slot)
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
