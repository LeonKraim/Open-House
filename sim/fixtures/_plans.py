"""The four fixture houses as data: their rooms, their bindings, and where they are.

A plan is what a fixture *is*, kept apart from the act of building it. Nothing
here touches an adapter, a clock or a stream except through the `build` method
`sim.fixtures.build_fixture` calls, so the four houses can be read against
`specs/simulation/spec.md`'s requirement the way the requirement is written --
as a table of rooms and devices -- without tracing a single call.

Every binding a room carries is a slot that room's type provides
(`catalog/room_types.yaml`'s `provides_slots`), or `house_mode` -- the one slot
no type provides that a house holds at house scope, and which must be bound in a
room because a house-scope slot is aggregated from the rooms. That is what the
catalog means by a type, and a light in a driveway is a house the catalog would
not recognise. Nothing shipped rejects it: `tools/catalog/scope.py` checks a
behaviour row against the catalog's supply, not a house's room bindings, so the
fixtures are where the rule is held, and the suite reads the catalog and asserts
it rather than trusting the copies.

The plans are data rather than four functions because the fixtures differ only
in their contents, and the one thing that varies by seed -- which device the
messy house reports as unavailable -- is a choice over a list the plan names
rather than four branches of code. `Plan.build` is where that choice is taken,
from the run's own stream, so a fixture is reproducible from its seed for the
same reason a run is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.adapter import ChangeContext, domain_of
from engine.solar import Location

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sim.adapter import FakeHouseAdapter
    from sim.entropy import RandomStream


@dataclass(frozen=True, slots=True)
class FixtureConfig:
    """Where a fixture house is: the location the sun branch reads, and its zone.

    This is fixture data and not engine state, which is the distinction
    `specs/simulation/spec.md` draws: the corpus supplies no location, so the
    fixture invents one, and it belongs to the house a scenario builds rather
    than to the decisions the engine takes about it. A snapshot therefore does
    not carry it -- a restored run reads it from the fixture it was built from --
    and two fixtures at different coordinates diverge at the same virtual instant
    without either of them having decided anything.
    """

    latitude: float
    longitude: float
    time_zone: str

    @property
    def location(self) -> Location:
        """The config as the value `engine/solar.py` computes an elevation from."""
        return Location(
            latitude=self.latitude,
            longitude=self.longitude,
            time_zone=self.time_zone,
        )


@dataclass(frozen=True, slots=True)
class RoomPlan:
    """One room: its identity, which of the 20 types it is, and what it binds."""

    id: str
    name: str
    type: str
    bindings: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Plan:
    """A whole fixture house, in the form the builder walks.

    `unavailable_candidates` is empty for every fixture but the messy one. When
    it is not, the build takes exactly one of them from the run's stream, which
    is what makes the messy house's availability a function of the seed rather
    than a constant: the spec asks it for "at least one device reporting
    unavailable", and a choice is the honest way to hold that, because the number
    of unavailable devices is then one rather than however many the list has.
    """

    config: FixtureConfig
    house_scope_slots: tuple[str, ...]
    rooms: tuple[RoomPlan, ...]
    unavailable_candidates: tuple[str, ...] = ()

    def entities(self) -> dict[str, str]:
        """Every entity the plan adds, mapped to the state it is added in.

        Sorted by id, so a fixture's entity list is a property of the house and
        not of the order the rooms happen to be written in. A collision -- the
        same id reached from two rooms -- is refused rather than collapsed,
        because the dict would otherwise keep one of the two silently and the
        fixture would bind a device it never added.
        """
        states: dict[str, str] = {}
        for room in self.rooms:
            for entity_id in room.bindings.values():
                if entity_id in states:
                    raise ValueError(
                        f"the plan binds {entity_id!r} twice; the fake holds one "
                        "entity per id, so the second binding would address the "
                        "first room's device"
                    )
                states[entity_id] = INITIAL[domain_of(entity_id)]
        return dict(sorted(states.items()))

    def attributes(self) -> dict[str, dict[str, object]]:
        """The attributes each added entity carries, by the slot that binds it.

        Only a measuring sensor carries anything: the unit it reports in is the
        one attribute the corpus reads a number out of, and a fixture whose
        sensors had none would make "the reading was a number in the unit the
        slot declares" untestable against the fixtures the scenarios use.

        Spelled by slot rather than by entity id because the unit is a property
        of what the slot measures -- every `lux_sensor` anywhere reads in lux --
        so a room that binds a new one does not have to be taught the unit too.
        """
        units = {
            "lux_sensor": "lx",
            "temperature_sensor": "°C",
            "humidity_sensor": "%",
        }
        attributes: dict[str, dict[str, object]] = {
            entity_id: {} for entity_id in self.entities()
        }
        for room in self.rooms:
            for slot, entity_id in room.bindings.items():
                unit = units.get(slot)
                if unit is not None:
                    attributes[entity_id] = {"unit_of_measurement": unit}
        return attributes

    def document(self, name: str) -> dict[str, object]:
        """The house document, in the frozen schema's shape.

        The name comes from the caller because it is the fixture's *selector* --
        the word a scenario's `given` block and an agent's session both write --
        and not a property of the rooms. A plan reached by any other route would
        otherwise be able to name itself something no `given` block can select.
        """
        return {
            "name": f"the {name} fixture",
            "rooms": [
                {
                    "id": room.id,
                    "name": room.name,
                    "type": room.type,
                    "bindings": {
                        slot: {"entity_id": entity_id}
                        for slot, entity_id in room.bindings.items()
                    },
                }
                for room in self.rooms
            ],
            "house_scope": {"slots": list(self.house_scope_slots)},
        }

    def build(
        self, adapter: FakeHouseAdapter, stream: RandomStream, name: str
    ) -> dict[str, object]:
        """Add every entity, mark what is unavailable, and return the document.

        Every device the fixture holds arrives through `add_entity` and every
        availability through `set_availability`, which is the whole point of
        building a fixture rather than loading one: a state the fake's own
        operations cannot reach is a state a scenario could pass against and the
        running system could never reproduce (`design.md` D11). Entities are
        added before any availability is set, because the fake refuses to mark an
        entity it does not hold.
        """
        states = self.entities()
        attributes = self.attributes()
        for entity_id, state in states.items():
            supplied = attributes[entity_id]
            adapter.add_entity(
                entity_id,
                state,
                attributes=supplied if supplied else None,
                context=ChangeContext.world(),
            )
        for entity_id in self.unavailable(stream):
            adapter.set_availability(
                entity_id, available=False, context=ChangeContext.world()
            )
        return self.document(name)

    def unavailable(self, stream: RandomStream) -> tuple[str, ...]:
        """The devices the fixture reports unavailable, drawn from the stream."""
        if not self.unavailable_candidates:
            return ()
        return (stream.choice(self.unavailable_candidates),)


#: The state a fresh entity of each domain is added in. A fixture device starts
#: where a real integration would report it at rest: off for anything
#: switchable, locked for a lock, docked for a vacuum. A sensor gets a number
#: rather than a word because a reading that is not a number is the one thing
#: every consumer of a sensor would have to special-case.
#:
#: Public because `sim/fixtures/__init__.py`'s `materialise` applies the same
#: table to any house document, not only to the four plans here: two copies of
#: "where a device of this domain starts" would be two answers to one question,
#: and an inline house and a fixture that started their devices differently
#: would be a difference no reader could account for.
INITIAL: Mapping[str, str] = {
    "binary_sensor": "off",
    "climate": "off",
    "cover": "closed",
    "input_select": "home",
    "light": "off",
    "lock": "locked",
    "media_player": "off",
    "sensor": "5",
    "switch": "off",
    "vacuum": "docked",
}

#: The entity domain each controlled slot addresses. Fixed by the slot's role
#: rather than by the room: every `light_group` anywhere is a `light.*` entity,
#: which is what makes a generated house readable.
_DOMAINS: Mapping[str, str] = {
    "climate_zone": "climate",
    "contact_sensor": "binary_sensor",
    "cover": "cover",
    "house_mode": "input_select",
    "humidity_sensor": "sensor",
    "leak_sensor": "binary_sensor",
    "light_group": "light",
    "lock": "lock",
    "lux_sensor": "sensor",
    "media_player": "media_player",
    "motion_sensor": "binary_sensor",
    "scene_selector": "input_select",
    "temperature_sensor": "sensor",
    "vacuum": "vacuum",
}

#: A short suffix per slot, so a generated entity id says which of a room's
#: devices it is: a room binding both a motion and a contact sensor would
#: otherwise name two `binary_sensor.*` entities the same way.
_SLOT_SUFFIX: Mapping[str, str] = {
    "climate_zone": "climate",
    "contact_sensor": "contact",
    "cover": "cover",
    "house_mode": "mode",
    "humidity_sensor": "humidity",
    "leak_sensor": "leak",
    "light_group": "light",
    "lock": "lock",
    "lux_sensor": "lux",
    "media_player": "media",
    "motion_sensor": "motion",
    "scene_selector": "scene",
    "temperature_sensor": "temperature",
    "vacuum": "vacuum",
}

#: The slots each room type provides, copied from `catalog/room_types.yaml` in
#: the catalog's own order. Copied rather than read, because `sim/` reading the
#: repository at import would make a fixture depend on the tree it was imported
#: from, and because the fixture that uses this is generated rather than
#: written out. The copy is not trusted: the suite reads the catalog and fails
#: when the two disagree, so drift is a failed check rather than a quiet
#: difference between the house a scenario gets and the vocabulary it was
#: validated against.
PROVIDES: Mapping[str, tuple[str, ...]] = {
    "basement": ("light_group", "motion_sensor"),
    "bathroom": ("humidity_sensor", "light_group", "motion_sensor"),
    "bedroom": (
        "climate_zone",
        "light_group",
        "lux_sensor",
        "media_player",
        "motion_sensor",
        "scene_selector",
        "temperature_sensor",
    ),
    "dining_room": ("light_group", "media_player", "motion_sensor"),
    "driveway": ("motion_sensor",),
    "family_room": ("light_group", "media_player", "motion_sensor"),
    "foyer": ("contact_sensor", "light_group", "lock", "motion_sensor"),
    "garage": ("cover", "light_group", "motion_sensor"),
    "gazebo": ("motion_sensor",),
    "hallway": ("light_group", "motion_sensor"),
    "kitchen": (
        "climate_zone",
        "contact_sensor",
        "light_group",
        "lux_sensor",
        "media_player",
        "motion_sensor",
        "temperature_sensor",
    ),
    "laundry": ("light_group", "motion_sensor"),
    "living_room": (
        "light_group",
        "lux_sensor",
        "media_player",
        "motion_sensor",
        "temperature_sensor",
    ),
    "mudroom": ("contact_sensor", "light_group", "lock", "motion_sensor"),
    "office": (
        "climate_zone",
        "contact_sensor",
        "light_group",
        "lux_sensor",
        "media_player",
        "motion_sensor",
        "scene_selector",
        "temperature_sensor",
    ),
    "playroom": ("climate_zone", "light_group", "media_player", "motion_sensor"),
    "pool": ("motion_sensor",),
    "porch": ("motion_sensor",),
    "stairs": ("light_group", "motion_sensor"),
    "sunroom": ("light_group", "motion_sensor"),
}

#: One or two rooms and a handful of devices: the least a tick needs. The first
#: room is a foyer rather than a hallway because it binds a contact sensor and
#: `catalog/room_types.yaml` gives `contact_sensor` to a foyer; a hallway
#: provides lighting and occupancy only. `house_mode` is bound in a room rather
#: than at house scope because a house-scope slot is aggregated from the rooms,
#: so a house that binds it nowhere cannot resolve the shutdown's required slot
#: -- the same reason every fixture binds it somewhere.
_MINIMAL = Plan(
    config=FixtureConfig(
        latitude=51.5074, longitude=-0.1278, time_zone="Europe/London"
    ),
    house_scope_slots=("house_mode", "light_group"),
    rooms=(
        RoomPlan(
            id="foyer",
            name="Foyer",
            type="foyer",
            bindings={
                "contact_sensor": "binary_sensor.front_door",
                "light_group": "light.foyer",
                "motion_sensor": "binary_sensor.foyer_motion",
            },
        ),
        RoomPlan(
            id="living_room",
            name="Living Room",
            type="living_room",
            bindings={
                "light_group": "light.living_room",
                "lux_sensor": "sensor.living_room_lux",
                "media_player": "media_player.living_room",
                "motion_sensor": "binary_sensor.living_room_motion",
                "house_mode": "input_select.house_mode",
            },
        ),
    ),
)

#: The house that strains binding: `light_group` is bound in two rooms, so the
#: house-scoped slot resolves to two entities, and no room binds a `lux_sensor`
#: anywhere -- a kitchen and a dining room both provide one, so the absence is a
#: choice the fixture makes rather than a slot no type offers. Which of the two
#: lights is unavailable is the stream's choice, so the house a scenario is
#: written against is reproducible from its seed.
_MESSY = Plan(
    config=FixtureConfig(
        latitude=51.5074, longitude=-0.1278, time_zone="Europe/London"
    ),
    house_scope_slots=("house_mode", "light_group"),
    rooms=(
        RoomPlan(
            id="dining_room",
            name="Dining Room",
            type="dining_room",
            bindings={
                "light_group": "light.dining_room",
                "motion_sensor": "binary_sensor.dining_room_motion",
                "house_mode": "input_select.house_mode",
            },
        ),
        RoomPlan(
            id="kitchen",
            name="Kitchen",
            type="kitchen",
            bindings={
                "light_group": "light.kitchen",
                "motion_sensor": "binary_sensor.kitchen_motion",
            },
        ),
    ),
    unavailable_candidates=("light.dining_room", "light.kitchen"),
)

#: How many rooms the large fixture has. Sixty rather than forty because every
#: room binds every slot its type provides, and the 20 types average 3.25 slots
#: between them: sixty rooms is 195 slot bindings plus the `house_mode` binding
#: the first room carries, so 196 entities, comfortably over the spec's 150, and
#: three passes over the catalog's types rather than two.
_LARGE_ROOM_COUNT = 60

#: The 20 type names, derived from `PROVIDES` so the cycle and the slot table
#: cannot fall out of step: a type added to one and not the other would give a
#: room a light it does not provide, or a type no room is ever built from.
_ROOM_TYPES: tuple[str, ...] = tuple(PROVIDES)


def _large_rooms() -> tuple[RoomPlan, ...]:
    """The large fixture's rooms, generated rather than written out sixty times.

    The type cycles through the catalog's 20 so every one appears three times and
    none is invented, and the index is zero-padded into both the room id and
    every entity id, because `house/1.0.0.json`'s id patterns admit digits but
    not the punctuation an unpadded list would need to stay sortable.
    """
    rooms: list[RoomPlan] = []
    for index in range(1, _LARGE_ROOM_COUNT + 1):
        room_type = _ROOM_TYPES[(index - 1) % len(_ROOM_TYPES)]
        slug = f"{room_type}_{index:02d}"
        bindings = {
            slot: f"{_DOMAINS[slot]}.{slug}_{_SLOT_SUFFIX[slot]}"
            for slot in PROVIDES[room_type]
        }
        if index == 1:
            bindings["house_mode"] = "input_select.house_mode"
        rooms.append(
            RoomPlan(
                id=slug,
                name=f"{room_type.replace('_', ' ').title()} {index}",
                type=room_type,
                bindings=bindings,
            )
        )
    return tuple(rooms)


_LARGE = Plan(
    config=FixtureConfig(
        latitude=40.7128, longitude=-74.0060, time_zone="America/New_York"
    ),
    house_scope_slots=("house_mode", "light_group"),
    rooms=_large_rooms(),
)

#: No `lux_sensor` bound anywhere, which is the sun fallback's own subject: a
#: behaviour taking the sun branch is never offered a lux reading here, and the
#: southern-hemisphere location makes the fallback's answer differ from the
#: London fixtures' at the same instant. Three of the four types here provide a
#: lux sensor and the fixture declines to bind it, so the absence is the
#: fixture's decision rather than a gap in the vocabulary.
_NO_LUX = Plan(
    config=FixtureConfig(
        latitude=-33.8688, longitude=151.2093, time_zone="Australia/Sydney"
    ),
    house_scope_slots=("house_mode", "light_group"),
    rooms=(
        RoomPlan(
            id="living_room",
            name="Living Room",
            type="living_room",
            bindings={
                "light_group": "light.living_room",
                "media_player": "media_player.living_room",
                "motion_sensor": "binary_sensor.living_room_motion",
                "house_mode": "input_select.house_mode",
            },
        ),
        RoomPlan(
            id="bedroom",
            name="Bedroom",
            type="bedroom",
            bindings={
                "light_group": "light.bedroom",
                "motion_sensor": "binary_sensor.bedroom_motion",
                "temperature_sensor": "sensor.bedroom_temperature",
            },
        ),
        RoomPlan(
            id="garage",
            name="Garage",
            type="garage",
            bindings={
                "cover": "cover.garage_door",
                "light_group": "light.garage",
                "motion_sensor": "binary_sensor.garage_motion",
            },
        ),
        RoomPlan(
            id="hallway",
            name="Hallway",
            type="hallway",
            bindings={
                "light_group": "light.hallway",
                "motion_sensor": "binary_sensor.hallway_motion",
            },
        ),
    ),
)

#: The four fixtures, by name. The key set is asserted against `FixtureName` by
#: the suite, so a fixture added here without a name -- or a name without a
#: fixture -- is a failing check rather than a house no scenario can select.
PLANS: Mapping[str, Plan] = {
    "minimal": _MINIMAL,
    "messy": _MESSY,
    "large": _LARGE,
    "no_lux": _NO_LUX,
}
