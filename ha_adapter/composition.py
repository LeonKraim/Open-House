"""Composing a live engine: the same engine, over the same port, on a real clock.

`custom_components/open_house/` shows a person their rooms; this module is what
makes those rooms *do* something. It builds the one `Engine` the whole project
has -- the same class the simulator builds, deciding through the same
`HouseAdapter` port -- and points it at a running Home Assistant through
`HAAdapter`, so that a room's bound `motion_sensor` and `ambient_light_sensor` drive its
bound `light_group` on Home Assistant's own terms rather than through a second,
integration-shaped implementation of the rules. A second implementation is the
failure the port exists to prevent (`design.md` D1): the simulator and the live
house would then disagree, and the disagreement would be invisible until a
person's lights did the wrong thing.

Three things are decided here and each is a decision rather than a projection.

**Time is the wall clock, and the engine is not asked to advance it.** The
simulator owns a `VirtualClock` because a scenario is a replay and a replay needs
an instant it can move by hand (`sim/clock.py`). A real house has no such lever:
time passes on its own, Home Assistant already schedules work on its event loop,
and an engine that tried to `advance()` a clock here would be inventing a second
timeline. So `SystemClock` reads `datetime.now(UTC)` and nothing else, and it
deliberately has no `advance`: `Engine.advance` looks for one and raises
`EngineError` without it, which turns "the integration tried to make time pass"
into a failure at the call site rather than a run that quietly skipped its
prescribed instant. What replaces `advance` is `tick()` on a schedule -- Home
Assistant's, not ours -- and the whole of the difference is that one call.

**The house document declares the vocabulary's house scope in full.** The
engine's house-scope gate resolves a behaviour's required slot by name and
*raises* for a slot the house scope does not declare
(`engine/binding.py`'s `resolve_slot`), before it can notice the slot is merely
unbound. A document that declared only the slots some room happens to bind would
therefore crash the tick of any house-scoped unit whose slot is unbound -- which
is exactly the ordinary case, since a house-scoped unit such as `away_shutdown`
requires `light_group` from the house scope, and a house need not have bound it.
Declaring the vocabulary's `house_slots`
instead makes an unbound house slot resolve *empty*, which is the engine's own
way of saying "skip this behaviour", and it is why the document is built from the
vocabulary rather than from the rooms.

**Every write goes through `HAAdapter`, and therefore through the port.** This
module holds no entity, no service call and no `homeassistant` object; it reads
the transport and writes the transport, and `HAAdapter` is what reconciles Home
Assistant's single state-or-availability field with the port's two. The engine
never learns the difference, which is the property that makes its decisions the
same here as in a scenario.

Nothing in this module imports `homeassistant`. The integration supplies the
transport and the clock's *reading*, so the composition loads and is tested in a
checkout where Home Assistant is not installed -- the phase's defining
constraint, and the reason the engine's port is a `Protocol` rather than an
implementation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from engine.behaviours import default_behaviours, enable_key
from engine.binding import House, RoomScope
from engine.decision_log import DecisionRecord
from engine.engine import Clock, Engine
from engine.install import InstalledSet
from engine.modes import ModeSet
from engine.profiles import ProfileSet
from engine.solar import Location
from engine.vocabulary import Vocabulary

from .adapter import HAAdapter
from .declared_units import installed_units, with_declared_slots
from .transport import HaTransport

__all__ = [
    "AUTO_LIGHTING_BEHAVIOURS",
    "LiveHouse",
    "LiveHouseError",
    "LiveRoom",
    "SystemClock",
    "build_live_house",
    "house_document",
    "mode_documents",
    "mode_name",
    "room_id",
]

#: The behaviours a room's auto-lighting switch turns on together. `motion_lighting`
#: is the rule that lights the room; `override` is the rule that stands it down
#: when a person's own hand touched the light, and the two belong to one switch
#: because a person who wants the room lit for them wants it to stop when they say
#: so. The names are the behaviours' own `id`s (`engine/behaviours/`), read here
#: rather than spelled into the enable key so a renamed unit fails at import.
AUTO_LIGHTING_BEHAVIOURS: tuple[str, ...] = ("motion_lighting", "override")

#: The exclusive group the house's modes share. Every mode the integration offers
#: is a presence mode -- Home, Away, Sleep, Guest -- and they are mutually
#: exclusive by construction, which is what makes choosing one clear the others.
PRESENCE_GROUP = "presence"

#: A run of characters that is not legal in an id, collapsed to one underscore.
#: The frozen house schema's room and mode ids are `^[a-z][a-z0-9_]*$`
#: (`schemas/house/1.0.0.json`, `schemas/mode/1.0.0.json`), and a Home Assistant
#: area id is *usually* already that shape -- but "usually" is not a schema, and
#: an area named "2nd floor" slugs to `2nd_floor`, which the pattern refuses. The
#: substitution is here so that a live house with such an area is configured
#: rather than refused, and `room_id` is exported so the integration derives the
#: same id this module will.
_NOT_ID = re.compile(r"[^a-z0-9]+")


class LiveHouseError(Exception):
    """A live house that cannot be composed.

    Named rather than a bare `ValueError`, because the integration catches it to
    report a config entry that will not start rather than a traceback in the
    middle of the event loop.
    """


def _slug(text: str) -> str:
    """`text` reduced to `[a-z0-9_]`, with no leading or trailing separator."""
    return _NOT_ID.sub("_", text.lower()).strip("_")


def room_id(area_id: str) -> str:
    """The engine's room id for a Home Assistant area id.

    The area id where it already satisfies the frozen schema's room pattern, and
    a slug otherwise, so an area whose name slugs to a leading digit still
    composes. Exported because the integration must name the same room this
    module will when it turns a room's switch or mode into an engine setting, and
    two spellings of that derivation would be two rooms.
    """
    slug = _slug(area_id) or "room"
    if not slug[0].isalpha():
        slug = f"room_{slug}"
    return slug


def mode_name(label: str) -> str:
    """The engine's mode name for a mode the integration offers.

    The integration's `const.MODES` are display names -- "Home", "Away" -- while
    the frozen mode schema lowercases its names and `away_shutdown` gates on the
    literal `away` (`engine/behaviours/away_shutdown.py`). So the label a person
    picks is projected once, here, and the engine only ever sees the name it
    gates on.
    """
    slug = _slug(label) or "mode"
    if not slug[0].isalpha():
        slug = f"mode_{slug}"
    return slug


def mode_documents(labels: Sequence[str]) -> tuple[Mapping[str, object], ...]:
    """One mode document per label, deduplicated and in the order given.

    The documents are the *shape* the frozen mode schema validates
    (`schemas/mode/1.0.0.json`); Phase 1 has no mode catalog, so a mode is
    supplied rather than read from a file (`engine/modes.py`), and supplying them
    here is what lets the engine gate on a mode a person picked from the
    integration's select. Labels that project to one name are collapsed rather
    than passed twice, because `ModeSet` refuses a duplicate name and a UI that
    offered two spellings of "home" would otherwise fail at startup.
    """
    documents: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for label in labels:
        name = mode_name(label)
        if name in seen:
            continue
        seen.add(name)
        documents.append(
            {
                "name": name,
                "description": f"The house is {label.strip().lower() or name}.",
                "exclusive_group": PRESENCE_GROUP,
            }
        )
    return tuple(documents)


@dataclass(frozen=True, slots=True)
class SystemClock:
    """The engine's clock on a real house: the wall clock, read and never moved.

    A property and not a method, matching `sim/clock.py`'s `VirtualClock` and the
    `Clock` protocol (`engine/engine.py`) the engine reads. It has no `advance`
    on purpose: `Engine.advance` looks for that method and raises without it,
    which is how "the integration tried to move time" becomes a loud failure
    rather than a silently skipped instant. Time here comes from the scheduler
    that ticks the engine, not from the engine.
    """

    @property
    def now(self) -> datetime:
        """The current instant, timezone-aware, in UTC."""
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LiveRoom:
    """One configured room, as the engine needs to see it.

    Deliberately not the integration's `RoomRuntime`: that type is a view for
    entities and lives where Home Assistant is importable, while this one is the
    four facts a house document is made of. Keeping them apart is what lets the
    composition -- and therefore the wiring the integration depends on -- be
    tested with no Home Assistant present.

    `auto_lighting` is the room's switch and `mode` its select, carried here
    because they become engine settings rather than engine decisions: the
    switch is the room's `motion_lighting` enable flag and the mode is the house
    mode the select named.
    """

    id: str
    name: str
    type: str
    bindings: Mapping[str, str] = field(default_factory=dict[str, str])
    auto_lighting: bool = True
    mode: str = ""


@dataclass(slots=True)
class LiveHouse:
    """An engine deciding for a live house, through the port.

    It is the composition root's product and the integration's handle on the
    engine: `tick` is the one way time is spent, and the two setters are the two
    controls a person has -- a room's auto-lighting and the house's mode -- turned
    into the engine settings they are. Nothing here decides anything: every one
    of these calls changes a flag the engine reads, and the engine is what acts.
    """

    engine: Engine
    adapter: HAAdapter

    def tick(self) -> tuple[DecisionRecord, ...]:
        """Evaluate every behaviour once, actuate what survives, record it all."""
        return self.engine.tick()

    def set_room_auto_lighting(self, room_id: str, *, on: bool) -> None:
        """Turn a room's lighting behaviours on or off, as the room's switch does.

        The switch is a *permission*, not an actuation (`const.ENTITY_AUTO_LIGHTING`),
        so this writes the room layer's enable flag for the behaviours a room's
        lighting is and stops there: it never turns a light off, and the next tick
        simply finds nothing to do for that room.
        """
        scope = RoomScope(room_id)
        for behaviour in AUTO_LIGHTING_BEHAVIOURS:
            self.engine.settings.set_override(enable_key(behaviour), scope, on)

    def set_house_mode(self, label: str) -> None:
        """Activate the house mode a room's select named.

        The engine's modes are *house-wide* (`engine/modes.py`) while the
        integration offers one select per room, so this is the honest projection
        of a per-room control onto the one mode set there is: whichever room
        wrote last is the house's mode, and a behaviour gated on a mode -- the
        away shutdown -- gates on the house's answer. Phase 3's profiles are
        where a room's own mode will live; until then a per-room mode that meant
        something different per room would be state nothing reads.
        """
        self.engine.modes.activate(mode_name(label))


def build_live_house(
    *,
    house_name: str,
    rooms: Sequence[LiveRoom],
    modes: Sequence[str],
    transport: HaTransport,
    vocabulary_root: Path,
    location: Location,
    clock: Clock | None = None,
    house_settings: Mapping[str, object] | None = None,
    house_bindings: Mapping[str, str] | None = None,
    installed: InstalledSet | None = None,
    module_rooms: Mapping[str, str] | None = None,
    profiles: ProfileSet | None = None,
    state: Mapping[str, object] | None = None,
    adapter: HAAdapter | None = None,
) -> LiveHouse:
    """Build the engine a running Home Assistant is decided for.

    `vocabulary_root` is the checkout the frozen artifacts live in -- the
    vocabulary is read, never restated (`engine/vocabulary.py`), so the live
    engine binds against the same slots and schemas the simulator does. `clock`
    defaults to the wall clock and exists as a parameter for the offline tests,
    which drive the same composition from a `VirtualClock` and reach the quiet
    timeout the wall clock would otherwise make them wait five minutes for.

    Every room's switch is applied as its behaviours' enable flag at construction,
    so a freshly activated setup runs: the product rule is that a fresh *house*
    runs nothing (`behaviour_defaults`), and the switch a person confirmed is what
    says this room is not that house. A room whose type is empty falls back to
    the flow's own fallback room type rather than being refused, because the type
    is a label the engine validates for shape and never reads for meaning.

    **The four optional arguments are what the live path was missing.** `installed`
    and `profiles` are the packs and profile selections a person has configured,
    which the panel edits and a restart must not forget; `state` is what a rebuild
    resumes from; and `adapter` lets a caller keep one `HAAdapter` across rebuilds.
    Reusing the adapter is not an optimisation: it is where the last known state
    of an entity Home Assistant currently reports as unavailable is remembered
    (`ha_adapter.adapter`), and a rebuild that built a fresh one would turn every
    unavailable entity into an entity with no history -- which is a different
    house from the one that was running a moment ago, and would make the engine
    read a device that dropped out as a device that never existed.

    `module_rooms` is the placement of each installed pack, and it is handed to
    the engine because a module narrowed to a room has to know *which* room --
    the one the person put it in. It is configuration the live path already keeps
    (`ha_adapter.live.LiveSession.module_rooms`), and passing it here is what
    makes a rebuild preserve the narrowing rather than resolve every module to
    the house default.

    `house_bindings` is the house's own binding per house-scope slot: the global
    entity a person bound once on the House tab, which fills the slot in the
    house scope and in every room that bound none of its own
    (`engine.binding.resolve_slot`). It is not in the house document the schema
    freezes -- that document binds slots room by room -- so it travels as its own
    argument, from the session that persists it.
    """
    if not rooms:
        raise LiveHouseError("a live house needs at least one room")
    ids = [room.id for room in rooms]
    if len(set(ids)) != len(ids):
        raise LiveHouseError(f"two rooms share the id {ids}")

    # The catalog's vocabulary, and then the devices the installed packs brought
    # with them. `engine/binding.py` refuses a room binding a slot the vocabulary
    # does not carry, so a pack that declares a device of its own -- a fridge's
    # contact, which no room type provides -- is bindable only if its name joins
    # the vocabulary when the pack installs (`declared_units.with_declared_slots`).
    # The extension comes from the same registry the units are built from, so a
    # restart restores the same house: the binds a person made and the words they
    # were allowed to make them under.
    vocabulary = with_declared_slots(
        vocabulary_root, installed, Vocabulary.load(vocabulary_root)
    )
    document = house_document(house_name, rooms, vocabulary)
    house = House.from_document(
        document,
        vocabulary=vocabulary,
        bindings=house_bindings,
    )
    if adapter is None:
        adapter = HAAdapter(transport=transport)
    # The four shipped units, and then the atoms the house actually installed.
    #
    # Until this merge, `behaviours=` was `default_behaviours()` alone and a pack
    # installed through the panel reached the engine only as a *record*: it was
    # stored, listed, flagged and enabled, and evaluated by nothing, so enabling
    # a module in a live house wrote a flag no unit read. The declared units are
    # built from the same registry the panel installs from (`declared_units`),
    # which is what makes a restart restore the atoms a person installed.
    #
    # Both sets are keyed by `id` and merged, because the engine keys units that
    # way and the two cannot collide: a declared unit's id is pack-qualified
    # (`pack.behaviour`, `engine/behaviours/declared.py`), and no shipped unit's
    # id contains a dot.
    behaviours = {
        **default_behaviours(),
        **{
            unit.id: unit
            for unit in installed_units(vocabulary_root, installed, vocabulary)
        },
    }
    engine = Engine(
        adapter=adapter,
        house=house,
        clock=SystemClock() if clock is None else clock,
        location=location,
        modes=ModeSet(mode_documents(modes), vocabulary=vocabulary),
        behaviours=behaviours.values(),
        house_settings=house_settings,
        room_settings={
            room.id: {
                enable_key(behaviour): room.auto_lighting
                for behaviour in AUTO_LIGHTING_BEHAVIOURS
            }
            for room in rooms
        },
        profile_settings=None if profiles is None else profiles.effective_house(),
        profile_room_settings=None if profiles is None else profiles.effective_rooms(),
        installed=installed,
        module_rooms=module_rooms,
        state=state,
    )
    live = LiveHouse(engine=engine, adapter=adapter)
    for room in rooms:
        if room.mode:
            live.set_house_mode(room.mode)
    return live


def house_document(
    house_name: str, rooms: Sequence[LiveRoom], vocabulary: Vocabulary
) -> Mapping[str, object]:
    """The house document the engine validates, from the integration's rooms.

    A room is its area id (projected by `room_id`), the area's name, its type and
    its slot bindings; a binding is `{slot: {"entity_id": ...}}`, which is the
    frozen schema's shape and the shape the setup flow already stores. The house
    scope declares the vocabulary's whole `house_slots` list for the reason the
    module docstring gives: resolution raises for a slot the scope omits, and an
    unbound house-scoped behaviour must be *skipped*, not fatal.
    """
    return {
        "name": house_name or "Open House",
        "house_scope": {"slots": sorted(vocabulary.house_slots)},
        "rooms": [
            {
                "id": room.id,
                "name": room.name or room.id,
                "type": room.type or "living_room",
                "bindings": {
                    slot: {"entity_id": entity_id}
                    for slot, entity_id in sorted(room.bindings.items())
                },
            }
            for room in rooms
        ],
    }
