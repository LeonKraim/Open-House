"""Slot binding and the declared reduction -- tasks 5.0 and 5.1.

The house documents here are written inline rather than taken from a fixture
house: the fixtures (`simulation`, task 8.0) are four whole houses built to be
driven, and what this module tests is the rule that a slot bound in two rooms
resolves to two entities while a slot bound nowhere resolves to none -- a rule
best tested on the smallest house that can express it.

Each test names the behaviour and says, in its docstring, what a falsifying
implementation would look like, because a test that would pass against any
implementation exercises nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.adapter import ChangeContext, EntityView
from engine.binding import (
    DuplicateRoomError,
    House,
    HouseScope,
    InvalidHouseError,
    Reduction,
    RoomScope,
    SlotRead,
    UnknownRoomError,
    UnknownSlotError,
    resolve_slot,
)
from engine.vocabulary import Vocabulary
from sim.adapter import FakeHouseAdapter
from sim.clock import VirtualClock
from sim.entropy import RandomStream

ROOT = Path(__file__).resolve().parents[1]
_START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog: frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


def _room(
    room_id: str, *, type_: str = "kitchen", bindings: Mapping[str, str] | None = None
) -> dict[str, object]:
    """A room document in the frozen schema's shape.

    `bindings` is written here as slot → entity id for readability, and wrapped
    into the schema's `{"entity_id": ...}` object form on the way out. Passing the
    bare string through would fail schema validation before any resolution ran, so
    every test that binds a slot would fail for a reason it does not name.
    """
    return {
        "id": room_id,
        "name": room_id.replace("_", " ").title(),
        "type": type_,
        "bindings": {
            slot: {"entity_id": entity_id}
            for slot, entity_id in (bindings or {}).items()
        },
    }


def _document(
    *rooms: dict[str, object], house_scope: tuple[str, ...] = ("light_group",)
) -> dict[str, object]:
    return {
        "name": "the test house",
        "rooms": list(rooms),
        "house_scope": {"slots": list(house_scope)},
    }


def _house(
    vocabulary: Vocabulary,
    *rooms: dict[str, object],
    house_scope: tuple[str, ...] = ("light_group",),
) -> House:
    return House.from_document(
        _document(*rooms, house_scope=house_scope), vocabulary=vocabulary
    )


def _two_lights() -> FakeHouseAdapter:
    """A house with two lights, one on and one off, bound nowhere yet."""
    adapter = FakeHouseAdapter(
        clock=VirtualClock.started_at(_START),
        random_stream=RandomStream.from_seed(1),
    )
    adapter.add_entity("light.kitchen", "on", context=ChangeContext.world())
    adapter.add_entity("light.bedroom", "off", context=ChangeContext.world())
    return adapter


def _is_on(view: EntityView) -> bool:
    return view.state == "on"


# --------------------------------------------------------------------------
# The house document, validated
# --------------------------------------------------------------------------


def test_a_house_is_validated_against_the_frozen_schema(vocabulary: Vocabulary) -> None:
    """A document violating the schema fails, naming the pointer it failed at.

    A falsifying implementation that trusted the document would reach a `KeyError`
    on the first missing field and a silent empty binding on the second, so the
    failure would name neither the house nor the field that made it invalid.
    """
    with pytest.raises(InvalidHouseError) as raised:
        House.from_document(
            {"name": "the test house", "rooms": [], "house_scope": {"slots": []}},
            vocabulary=vocabulary,
        )
    assert raised.value.pointer == "/rooms"


def test_a_room_without_bindings_fails_at_that_room(vocabulary: Vocabulary) -> None:
    """The pointer names the room, so a many-room document is diagnosable."""
    with pytest.raises(InvalidHouseError) as raised:
        House.from_document(
            _document({"id": "kitchen", "name": "Kitchen", "type": "kitchen"}),
            vocabulary=vocabulary,
        )
    assert raised.value.pointer == "/rooms/0"


def test_a_binding_naming_an_undefined_slot_fails_naming_room_and_slot(
    vocabulary: Vocabulary,
) -> None:
    """A slot no vocabulary defines is refused, and the room is named with it.

    A falsifying implementation that resolved any name it was handed would turn
    the corpus's controlled vocabulary into a suggestion: the behaviour that
    asked for `light_group` would find nothing and record a skip, and the house
    would look unbound rather than wrong.
    """
    with pytest.raises(UnknownSlotError) as raised:
        _house(vocabulary, _room("kitchen", bindings={"lighting_scene": "light.k"}))
    assert raised.value.slot == "lighting_scene"
    assert raised.value.where == "the room 'kitchen'"


def test_a_house_scope_slot_the_vocabulary_does_not_offer_fails(
    vocabulary: Vocabulary,
) -> None:
    """A house-scope slot outside `house.slots` is refused, naming the scope.

    A falsifying implementation that accepted any controlled slot name at house
    scope would let a house declare a scope the vocabulary does not have, and
    every house-scoped behaviour would resolve against a slot no room binds.
    """
    with pytest.raises(UnknownSlotError) as raised:
        House.from_document(
            _document(_room("kitchen"), house_scope=("ambient_light_sensor",)),
            vocabulary=vocabulary,
        )
    assert raised.value.where == "the house scope"
    assert raised.value.slot == "ambient_light_sensor"


def test_a_house_scope_slot_no_slot_file_defines_fails(vocabulary: Vocabulary) -> None:
    """A name that is not a slot at all is refused for the same reason."""
    with pytest.raises(UnknownSlotError) as raised:
        House.from_document(
            _document(_room("kitchen"), house_scope=("lighting_scene",)),
            vocabulary=vocabulary,
        )
    assert raised.value.slot == "lighting_scene"


def test_two_rooms_sharing_an_id_are_refused(vocabulary: Vocabulary) -> None:
    """A duplicate room id fails rather than resolving to one of the two rooms.

    The schema states a room id's shape but cannot state its uniqueness, so a
    falsifying implementation would accept the document and let `resolve_slot`
    reach whichever room came first -- a house that answers for the wrong room.
    """
    with pytest.raises(DuplicateRoomError) as raised:
        _house(vocabulary, _room("kitchen"), _room("kitchen", type_="bedroom"))
    assert raised.value.room_id == "kitchen"


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_an_unknown_room_scope_fails_naming_the_room(vocabulary: Vocabulary) -> None:
    """A scope naming a room the house lacks is refused rather than empty.

    A falsifying implementation that returned an empty binding would report an
    unbound slot for a room that does not exist, which is the difference between
    "this room binds nothing" and "there is no such room".
    """
    house = _house(vocabulary, _room("kitchen"))
    with pytest.raises(UnknownRoomError) as raised:
        resolve_slot(house, RoomScope("attic"), "light_group")
    assert raised.value.room_id == "attic"


def test_a_room_scope_resolves_that_rooms_own_binding(vocabulary: Vocabulary) -> None:
    """The room's `bindings` entry for the slot is the whole answer."""
    house = _house(vocabulary, _room("kitchen", bindings={"light_group": "light.k"}))
    binding = resolve_slot(house, RoomScope("kitchen"), "light_group")
    assert binding.entities == ("light.k",)
    assert binding.is_empty is False


def test_a_required_slot_with_no_binding_resolves_empty_and_required(
    vocabulary: Vocabulary,
) -> None:
    """An unbound required slot is empty *and* flagged, not an error.

    The skip it produces is the engine's to write as a record (`engine-core`), so
    a falsifying implementation that raised here would leave the scenario with no
    record saying why nothing happened -- and one that reported the slot as
    optional would let the behaviour run on an empty read.
    """
    house = _house(vocabulary, _room("kitchen"))
    binding = resolve_slot(house, RoomScope("kitchen"), "motion_sensor")
    assert binding.entities == ()
    assert binding.is_empty is True
    assert binding.required is True


def test_an_optional_slot_with_no_binding_resolves_empty_and_optional(
    vocabulary: Vocabulary,
) -> None:
    """The optional case differs from the required one only in the flag."""
    house = _house(vocabulary, _room("kitchen"))
    binding = resolve_slot(house, RoomScope("kitchen"), "ambient_light_sensor")
    assert binding.entities == ()
    assert binding.required is False


def test_a_house_scoped_slot_collects_every_rooms_binding_in_room_order(
    vocabulary: Vocabulary,
) -> None:
    """Two rooms binding one slot resolve to two entities, in room order.

    A falsifying implementation that held one entity per slot -- the shape the
    frozen schema binds in a room -- would return whichever room the house
    happened to list first and drop the other, which is a house whose bedroom
    lighting is never switched.
    """
    house = _house(
        vocabulary,
        _room("kitchen", bindings={"light_group": "light.kitchen"}),
        _room("bedroom", type_="bedroom", bindings={"light_group": "light.bedroom"}),
    )
    binding = resolve_slot(house, HouseScope(), "light_group")
    assert binding.entities == ("light.kitchen", "light.bedroom")


def test_a_house_scoped_slot_skips_rooms_that_leave_it_unbound(
    vocabulary: Vocabulary,
) -> None:
    """A room that binds nothing contributes nothing, and is not revised in.

    A falsifying implementation that read every room's slot entry would either
    raise on the unbound room or invent an entity for it.
    """
    house = _house(
        vocabulary,
        _room("kitchen", bindings={"light_group": "light.kitchen"}),
        _room("hallway", type_="hallway"),
        _room("bedroom", type_="bedroom", bindings={"light_group": "light.bedroom"}),
    )
    binding = resolve_slot(house, HouseScope(), "light_group")
    assert binding.entities == ("light.kitchen", "light.bedroom")


def test_a_house_scope_read_of_a_slot_the_house_does_not_make_available_fails(
    vocabulary: Vocabulary,
) -> None:
    """A controlled slot the house did not declare at house scope is refused.

    `ambient_light_sensor` is a slot the vocabulary defines and no house scope offers, so a
    falsifying implementation that checked only the slot name would resolve a
    house-scoped read against a scope the house never declared.
    """
    house = _house(vocabulary, _room("kitchen"))
    with pytest.raises(UnknownSlotError) as raised:
        resolve_slot(house, HouseScope(), "ambient_light_sensor")
    assert raised.value.where == "the house scope"


# --------------------------------------------------------------------------
# The reduction
# --------------------------------------------------------------------------


def test_the_same_two_entities_read_differently_under_each_reduction(
    vocabulary: Vocabulary,
) -> None:
    """One member on and one off is true under `ANY` and false under `ALL`.

    A falsifying implementation that folded the reduction in as an implicit
    default would give one answer for both callers, and the answer would look
    correct for whichever caller it happened to suit.
    """
    house = _house(
        vocabulary,
        _room("kitchen", bindings={"light_group": "light.kitchen"}),
        _room("bedroom", type_="bedroom", bindings={"light_group": "light.bedroom"}),
    )
    adapter = _two_lights()
    binding = resolve_slot(house, HouseScope(), "light_group")

    assert binding.read(Reduction.ANY).holds(adapter, _is_on) is True
    assert binding.read(Reduction.ALL).holds(adapter, _is_on) is False


def test_a_read_that_names_no_reduction_cannot_be_made(vocabulary: Vocabulary) -> None:
    """The reduction has no default, so leaving it out is a failure to construct.

    A falsifying implementation with `reduction: Reduction = Reduction.ANY` would
    answer every multi-entity read as "any" -- silently, and only wrongly for the
    confirmations that need "all".
    """
    house = _house(vocabulary, _room("kitchen", bindings={"light_group": "light.k"}))
    binding = resolve_slot(house, RoomScope("kitchen"), "light_group")
    with pytest.raises(TypeError):
        binding.read()  # type: ignore[call-arg]


def test_an_empty_read_follows_the_reduction(vocabulary: Vocabulary) -> None:
    """No members is false under `ANY` and true under `ALL`, the usual readings.

    A falsifying implementation that special-cased the empty slot to "false" for
    both would make "all the lights are off" false in a house with no lights,
    which contradicts the reduction it was asked for; one that raised would make
    the optional-slot fallback unrunnable.
    """
    house = _house(vocabulary, _room("kitchen"))
    adapter = _two_lights()
    binding = resolve_slot(house, RoomScope("kitchen"), "ambient_light_sensor")
    assert binding.read(Reduction.ANY).holds(adapter, _is_on) is False
    assert binding.read(Reduction.ALL).holds(adapter, _is_on) is True


def test_the_read_carries_the_reduction_it_was_given(vocabulary: Vocabulary) -> None:
    """A read is a value holding its reduction, which is what the log records.

    A falsifying implementation that dropped the reduction on the way into the
    read would leave a record naming a slot with no way to tell an `any` read
    from an `all` one -- the record's whole reason for carrying the read.
    """
    house = _house(vocabulary, _room("kitchen", bindings={"light_group": "light.k"}))
    binding = resolve_slot(house, RoomScope("kitchen"), "light_group")
    read = binding.read(Reduction.ALL)
    assert read == SlotRead(
        slot="light_group", entities=("light.k",), reduction=Reduction.ALL
    )
    assert read.reduction is Reduction.ALL


def test_the_reduction_set_is_closed_at_two() -> None:
    """`ANY` and `ALL` are the whole set, and their spellings are fixed.

    A falsifying implementation that added a third reduction would need every
    caller's two-valued logic revised; one that renamed a member would change
    what a stored record means.
    """
    assert {member.value for member in Reduction} == {"any", "all"}
    assert len(Reduction) == 2
