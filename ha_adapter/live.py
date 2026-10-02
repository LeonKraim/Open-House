"""A live house as a session: the engine, and everything a person configures.

`composition.build_live_house` builds *an* engine -- one house, one set of rooms,
one construction -- and stops. That was enough while the integration only ever
showed a house and turned one switch. It is not enough for a panel that edits
the house while it runs: a pack install, a rebinding, a profile change and an
import all mean the same thing -- the document the engine decides for has
changed -- and the engine the project has is a pure function of the state it
starts from (`engine/engine.py`), so "changed" is only ever spelled by building
the next engine.

**A session is the wiring a rebuild needs.** `LiveSession` holds what does not
change when the engine is rebuilt (the checkout, the transport, the vocabulary,
the place, the clock, the adapter) and what does (the rooms, the installed
packs, the profile selections, the house settings, the mode labels). Every edit
is a small change to one of the second group followed by a rebuild, and no edit
reaches into the engine: the engine is a *product* of the session, never a thing
the session mutates in place. That is why `engine` is a property over a private
field rather than a field -- there is no supported way to hold an engine across
an edit, and the type says so.

**Why this is not `openhouse.facade.OpenHouse`.** The facade is the same idea
over a `Simulation`: a virtual clock, a virtual adapter, `advance_time`,
scenarios. A live house has none of those and must not pretend to -- time is the
wall clock and the engine is ticked by Home Assistant's scheduler, so a session
that could `advance()` would be inventing a second timeline. The two share the
engine, the port, the installed set and the profile set, and share no clock.
This module is the live half of that pair, and it is deliberately the smaller
one, because everything a live session can do is something a person asked for
through the panel.

**Nothing here imports `homeassistant`.** The transport is the seam
(`ha_adapter/transport.py`), so a session is built and edited in a checkout with
no Home Assistant installed, and the integration is the only thing that knows
one is running. This is the same constraint `composition.py` is written under,
and for the same reason: the wiring the live product depends on has to be
testable without the product.

**The room ids are the engine's.** A room in a live house is a Home Assistant
area, whose id is not necessarily a legal engine id (`room_id` slugs it), and a
session speaks only the engine's spelling. Translating an area id is the
integration's job and happens once, at the edge, so nothing below this line has
two names for one room.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from engine.behaviours import enable_key
from engine.binding import RoomScope
from engine.decision_log import DecisionRecord
from engine.engine import Clock, Engine
from engine.install import InstalledSet
from engine.profiles import ProfileSet, load_profile_schema
from engine.solar import Location
from engine.vocabulary import Vocabulary

from .adapter import HAAdapter
from .composition import (
    AUTO_LIGHTING_BEHAVIOURS,
    LiveHouse,
    LiveRoom,
    build_live_house,
    mode_name,
)
from .declared_units import with_declared_slots
from .transport import HaTransport

__all__ = [
    "HOUSE",
    "SESSION_STATE_VERSION",
    "LiveSession",
    "LiveSessionError",
]

#: The placement that means *the whole house* rather than a room.
#:
#: The house is a target a module can be put in, exactly like a room -- its own
#: slots, its own modules, its own settings -- and a placement has to be able to
#: say so. A room placement is the room's engine id; this is the other one, and
#: it is spelled the empty string because that is already what a room id is never
#: allowed to be (`rebuild` refuses duplicate ids and every room has one) and
#: what a module that belongs to no single room already read as. `module_rooms`
#: carrying `pack: ""` is therefore unambiguous: the key is *present*, so the
#: house was chosen, where an absent key still means "never placed" and falls
#: back to joining the pack's slots (`live_modules._module_room`).
HOUSE = ""

#: The version the session writes into the state it hands out. Bumped when the
#: shape of that state changes, so a state written by an older build is refused
#: by name rather than half-read.
SESSION_STATE_VERSION = "1.0.0"


class LiveSessionError(Exception):
    """An edit a session cannot make.

    Named rather than a bare `ValueError` because the caller is a websocket
    handler that turns it into an error *code* -- `unknown_room`, `unknown_slot`
    -- that the panel renders, and a failure the panel cannot name is a failure
    it can only show as a stack trace.
    """


@dataclass(slots=True)
class LiveSession:
    """The engine a live house is decided for, and the state that produces it.

    Constructed through `build`, never by hand: every field is something the
    constructor derives, and a half-filled session would be one whose next
    `rebuild` produced an engine for a house that is not the one on screen.
    """

    #: What the house is called. Comes from the config entry's title.
    house_name: str
    #: The rooms, in the engine's id space, in the order they are shown.
    rooms: tuple[LiveRoom, ...]
    #: The mode *labels* the house offers -- "Home", "Away". The documents the
    #: engine validates are derived from these on every rebuild, so a label and
    #: the mode it produces cannot drift apart.
    modes: tuple[str, ...]
    #: The seam to Home Assistant. Never rebuilt.
    transport: HaTransport
    #: The checkout the frozen artifacts are read from. Never rebuilt.
    root: Path
    #: The frozen vocabulary every room binds against. Never rebuilt.
    vocabulary: Vocabulary
    #: Where the house is, for the sun branch of motion lighting.
    location: Location
    #: The clock. The wall clock live; a virtual one in the offline tests.
    clock: Clock
    #: The schema a profile document is validated against.
    profile_schema: Mapping[str, object]
    #: The profiles the house holds and the ones each room is on. Not defaulted:
    #: a `ProfileSet` is built against a schema, and a default one would be a set
    #: validated against nothing.
    profiles: ProfileSet
    #: House-scope settings a person has set, above the builtin defaults.
    house_settings: Mapping[str, object] = field(default_factory=dict)
    #: The house's own binding per house-scope slot, by slot name.
    #:
    #: One global entity per role -- "the house's lights" -- bound once on the
    #: House tab. It is what the house scope resolves the slot to, ahead of the
    #: rooms it would otherwise collect, and what a room that bound no slot of
    #: its own falls back to (`engine.binding.resolve_slot`). Kept here rather
    #: than in a room because a room is not where it lives: the frozen house
    #: schema has no house-level `bindings`, so this is session state, saved and
    #: restored beside `house_settings` and absent from a house that never set
    #: one.
    house_bindings: Mapping[str, str] = field(default_factory=dict)
    #: The packs this house holds.
    installed: InstalledSet = field(default_factory=InstalledSet)
    #: The room each installed pack was *put in*, by pack name.
    #:
    #: A pack lands in the house -- the engine keys the installed set by name
    #: and knows nothing of rooms -- but a person installs a module *into a
    #: room*, and which room has to be remembered rather than worked out. The
    #: obvious inference, "the room that binds every entity the pack's slots
    #: reached", cannot answer for a pack whose slots are mostly house-scope:
    #: `light_group` and `lock` are the whole house's, so `bedtime` reaches
    #: through them in every room at once and no room binds them at all. It got
    #: no room, `_enabled` answered `False` for the empty id, and the module a
    #: person had just placed could never be switched on.
    module_rooms: Mapping[str, str] = field(default_factory=dict)
    #: The adapter, kept across rebuilds so an entity Home Assistant currently
    #: reports as unavailable keeps the last state it was known to hold.
    adapter: HAAdapter | None = None
    #: The engine. A product of the fields above; read it, never hold it.
    _engine: Engine | None = field(default=None, repr=False)

    # -- Construction -------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        house_name: str,
        rooms: Sequence[LiveRoom],
        modes: Sequence[str],
        transport: HaTransport,
        root: Path,
        location: Location,
        clock: Clock | None = None,
        house_settings: Mapping[str, object] | None = None,
        house_bindings: Mapping[str, str] | None = None,
        installed: InstalledSet | None = None,
        profiles: ProfileSet | None = None,
        module_rooms: Mapping[str, str] | None = None,
        adapter: HAAdapter | None = None,
    ) -> LiveSession:
        """A session over `rooms`, with its first engine built.

        The vocabulary and the profile schema are read here rather than by each
        caller because every rebuild needs them and reading them twice would be
        two vocabularies that could disagree. A checkout whose catalog is missing
        raises out of `Vocabulary.load` naming the file it wanted, which is the
        honest failure for a session that cannot be built at all.
        """
        from .composition import SystemClock

        vocabulary = Vocabulary.load(root)
        session = cls(
            house_name=house_name,
            rooms=tuple(rooms),
            modes=tuple(modes),
            transport=transport,
            root=root,
            vocabulary=vocabulary,
            location=location,
            clock=SystemClock() if clock is None else clock,
            profile_schema=load_profile_schema(root),
            profiles=ProfileSet([], schema=load_profile_schema(root))
            if profiles is None
            else profiles,
            house_settings=dict(house_settings or {}),
            house_bindings=dict(house_bindings or {}),
            installed=InstalledSet() if installed is None else installed,
            module_rooms=dict(module_rooms or {}),
            adapter=adapter,
        )
        session.rebuild()
        return session

    # -- Reads --------------------------------------------------------------

    @property
    def engine(self) -> Engine:
        """The engine this session's wiring currently produces.

        A property and not a field: an edit replaces the engine, and a caller
        that kept the old one would be deciding for the house as it was. The
        churn is deliberate -- the engine is a pure function of its start state,
        so the only way to change it is to build the next one.
        """
        if self._engine is None:  # pragma: no cover - `build` always fills it
            raise LiveSessionError("this session has no engine yet")
        return self._engine

    @property
    def house(self) -> LiveHouse:
        """The engine and its adapter, for the calls that are the port's."""
        if self.adapter is None:  # pragma: no cover - `build` always fills it
            raise LiveSessionError("this session has no adapter yet")
        return LiveHouse(engine=self.engine, adapter=self.adapter)

    def room(self, room_id: str) -> LiveRoom | None:
        """The room called `room_id`, or `None`. `room_id` is the engine's id."""
        for room in self.rooms:
            if room.id == room_id:
                return room
        return None

    def require_room(self, room_id: str) -> LiveRoom:
        """The room called `room_id`, or a failure naming it.

        A handler wants the refusal, not the `None`: a command that named a room
        the house does not hold is a bad request the panel should see, and an
        empty answer for it would read as a room with nothing in it.
        """
        found = self.room(room_id)
        if found is None:
            raise LiveSessionError(f"there is no room {room_id!r} in this house")
        return found

    def tick(self) -> tuple[DecisionRecord, ...]:
        """Evaluate every behaviour once and actuate what survives."""
        return self.engine.tick()

    # -- Edits --------------------------------------------------------------

    def rebuild(self) -> None:
        """Build the engine these fields produce, reusing the adapter."""
        if not self.rooms:
            raise LiveSessionError("a house needs at least one room")
        ids = [room.id for room in self.rooms]
        if len(set(ids)) != len(ids):
            raise LiveSessionError(f"two rooms share the id {ids}")
        house = build_live_house(
            house_name=self.house_name,
            rooms=self.rooms,
            modes=self.modes,
            transport=self.transport,
            vocabulary_root=self.root,
            location=self.location,
            clock=self.clock,
            house_settings=self.house_settings,
            house_bindings=self.house_bindings,
            installed=self.installed,
            module_rooms=self.module_rooms,
            profiles=self.profiles,
            adapter=self.adapter,
        )
        self.adapter = house.adapter
        self._engine = house.engine

    def set_rooms(self, rooms: Sequence[LiveRoom]) -> None:
        """Replace the rooms and rebuild. The house document *is* the rooms."""
        self.rooms = tuple(rooms)
        self.rebuild()

    def set_room_auto_lighting(self, room_id: str, *, on: bool) -> None:
        """Turn a room's lighting behaviours on or off, as the room's switch does.

        A *permission*, not an actuation (`const.ENTITY_AUTO_LIGHTING`): it
        writes the room layer's enable flag and stops there, so the next tick
        finds nothing to do for that room rather than turning its light off now.
        An override is engine state, not wiring, so this does not rebuild -- it
        is the same call the room's switch entity already makes.
        """
        self.require_room(room_id)
        scope = RoomScope(room_id)
        for behaviour in AUTO_LIGHTING_BEHAVIOURS:
            self.engine.settings.set_override(enable_key(behaviour), scope, on)

    def set_house_mode(self, label: str) -> None:
        """Activate the house mode a label names, projecting the label once."""
        self.engine.modes.activate(mode_name(label))

    def bind(self, room_id: str, slot: str, entity_id: str) -> LiveRoom | None:
        """Point `slot` in `room_id` at `entity_id`, replacing whatever was there.

        The old binding is not recoverable from here: the *caller* records what
        it replaced, because only the caller has a decision log to record it in,
        and a session that kept a history of bindings would be a second place the
        house's past is written down.

        `room_id` may be `HOUSE`, which binds the slot for the whole house
        instead of for a room (`set_house_binding`) and answers `None`, because
        there is no room that changed.
        """
        if room_id == HOUSE:
            self.set_house_binding(slot, entity_id)
            return None
        room = self.require_room(room_id)
        if slot not in room.bindings and slot not in self.bindable_slots():
            raise LiveSessionError(f"there is no slot {slot!r} in this house")
        updated = _rebind(room, slot, entity_id)
        self.set_rooms(
            tuple(updated if other.id == room_id else other for other in self.rooms)
        )
        return updated

    def set_house_binding(self, slot: str, entity_id: str | None) -> None:
        """Bind `slot` for the whole house to `entity_id`, or clear it for `None`.

        The global half of binding: one entity standing in for a role everywhere
        -- the house scope resolves the slot to it, and every room that bound
        none of its own falls back to it (`engine.binding.resolve_slot`). It
        rebuilds, because the house the engine decides for is a different house
        once a global slot points somewhere new.

        Gated on the house's own slot list first, which is the list the house
        scope will resolve against: a binding the engine would refuse to resolve
        is refused here, where the person can still be told why.
        """
        house = self.engine.house
        if slot not in house.house_scope_slots and slot not in self.bindable_slots():
            raise LiveSessionError(f"there is no house slot {slot!r} in this house")
        bindings = dict(self.house_bindings)
        if entity_id is None:
            bindings.pop(slot, None)
        else:
            bindings[slot] = entity_id
        self.house_bindings = bindings
        self.rebuild()

    def bindable_slots(self) -> frozenset[str]:
        """Every slot a room in this house may bind, by the name it binds under.

        The catalog's words *and* the devices the installed packs declare of
        their own. The frozen vocabulary carries only the first: a pack that
        brings `fridge_contact` has that word added to the house's vocabulary by
        the install rather than by the catalog, so a gate that checked
        `self.vocabulary.slots` alone would refuse the very binding that makes
        the pack's own requirement fillable -- the room's page would offer the
        device and the write behind it would answer "no such slot".

        The extension is read through `with_declared_slots`, the one function
        that grows the vocabulary, so this gate and the engine's vocabulary
        cannot disagree about which names the house carries. A declared name
        arrives under the key it binds with (`key_of`), which is the name the
        room's page lists and the name this check is asked about.
        """
        extended = with_declared_slots(self.root, self.installed, self.vocabulary)
        return frozenset(extended.slots)

    def unbind(self, room_id: str, slot: str) -> LiveRoom | None:
        """Leave `slot` in `room_id` unbound, or the house's own binding for `HOUSE`."""
        if room_id == HOUSE:
            self.set_house_binding(slot, None)
            return None
        room = self.require_room(room_id)
        updated = _rebind(room, slot, None)
        self.set_rooms(
            tuple(updated if other.id == room_id else other for other in self.rooms)
        )
        return updated

    def set_installed(self, installed: InstalledSet) -> None:
        """Replace the installed packs and rebuild."""
        self.installed = installed
        self.rebuild()

    def place_module(self, pack: str, room_id: str) -> None:
        """Record where `pack` was installed: a room id, or `HOUSE` for the house.

        Rebound rather than mutated so a reader holding the previous mapping
        keeps the previous answer, which is the same discipline the installed set
        is kept under. No rebuild: where a module belongs is how the panel groups
        it and where its enable flag is written, and neither is engine wiring.

        `room_id` is `HOUSE` when a module was put in the whole house rather than
        in a room, which is a real choice and not the absence of one -- the house
        has its own modules (`live_modules.install`) and this is what records it.
        """
        self.module_rooms = {**self.module_rooms, pack: room_id}

    def forget_module(self, pack: str) -> None:
        """Drop `pack`'s room, because the module is no longer in the house."""
        self.module_rooms = {
            name: room for name, room in self.module_rooms.items() if name != pack
        }

    def module_room(self, pack: str) -> str | None:
        """Where `pack` was installed: a room id, `HOUSE`, or `None` if unnamed.

        The three answers are three different facts and none is a spelling of
        another: a room id is a room, `HOUSE` is the whole house, and `None` is a
        pack nobody placed -- a Store install, or a house configured before the
        placement was kept.
        """
        return self.module_rooms.get(pack)

    def set_profiles(self, profiles: ProfileSet) -> None:
        """Replace the profile set -- declarations and selections -- and rebuild."""
        self.profiles = profiles
        self.rebuild()

    def set_house_settings(self, settings: Mapping[str, object]) -> None:
        """Replace the house-scope settings and rebuild."""
        self.house_settings = dict(settings)
        self.rebuild()

    def set_auto_lighting(self, *, on: bool) -> None:
        """Set every room's lighting permission, as a house-wide switch would.

        The rooms are replaced rather than the flags set one by one, because a
        rebuilt room carries the flag into the engine's *room settings* at
        construction -- the same path a freshly activated setup takes, so a
        house-wide switch and a fresh setup cannot disagree about what "on" means.
        """
        self.rooms = tuple(_with_auto_lighting(room, on) for room in self.rooms)
        self.rebuild()

    # -- State --------------------------------------------------------------

    def to_state(self) -> Mapping[str, object]:
        """The session as a document, for a restart to rebuild from.

        Configuration only. The engine's own state -- which lights it is holding
        on, how long a room has been quiet -- is deliberately not here: it
        describes a house at an instant, and an instant that crossed a restart
        would describe a house that no longer exists. The engine starts fresh and
        the *configuration* survives, which is the half a person set by hand.
        """
        return {
            "version": SESSION_STATE_VERSION,
            "house_name": self.house_name,
            "modes": list(self.modes),
            "rooms": [_room_document(room) for room in self.rooms],
            "house_settings": dict(self.house_settings),
            "installed": self.installed.to_document(),
            "module_rooms": dict(self.module_rooms),
            "profiles": self.profiles.to_document(),
        }

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, object],
        *,
        transport: HaTransport,
        root: Path,
        location: Location,
        clock: Clock | None = None,
        house_name: str | None = None,
    ) -> LiveSession:
        """Rebuild a session from `to_state`'s document.

        The transport, the checkout and the place are *not* in the document: they
        are facts about where the house is rather than about what it is
        configured to do, and a document that carried a path would be one a
        restart could use to load a different checkout than the one it is
        running from.
        """
        version = str(state.get("version", ""))
        if version != SESSION_STATE_VERSION:
            raise LiveSessionError(
                f"this session state is version {version!r}, and this build writes "
                f"{SESSION_STATE_VERSION!r}"
            )
        rooms = tuple(
            _room_from_document(document)
            for document in _documents(state.get("rooms"), "rooms")
        )
        schema = load_profile_schema(root)
        profiles = state.get("profiles")
        return cls.build(
            house_name=house_name or str(state.get("house_name", "")) or "Open House",
            rooms=rooms,
            modes=tuple(_labels(state.get("modes"))),
            transport=transport,
            root=root,
            location=location,
            clock=clock,
            house_settings=_mapping(state.get("house_settings"), "house_settings"),
            installed=InstalledSet.from_document(
                _mapping(state.get("installed"), "installed")
            ),
            # Absent from a document written before the field existed, and the
            # empty mapping is the honest reading: nothing has been placed.
            module_rooms=_strings_mapping(state.get("module_rooms")),
            profiles=ProfileSet.from_document(
                _mapping(profiles, "profiles"), schema=schema
            )
            if profiles is not None
            else ProfileSet([], schema=schema),
            adapter=None,
        )


# --------------------------------------------------------------------------
# Projections. Each is a round trip through the one shape that is written
# down, so a room that survives a restart is the room that went in.
# --------------------------------------------------------------------------


def _rebind(room: LiveRoom, slot: str, entity_id: str | None) -> LiveRoom:
    """A copy of `room` with `slot` bound to `entity_id`, or unbound for `None`."""
    bindings = dict(room.bindings)
    if entity_id is None:
        bindings.pop(slot, None)
    else:
        bindings[slot] = entity_id
    return LiveRoom(
        id=room.id,
        name=room.name,
        type=room.type,
        bindings=bindings,
        auto_lighting=room.auto_lighting,
        mode=room.mode,
    )


def _with_auto_lighting(room: LiveRoom, on: bool) -> LiveRoom:
    """A copy of `room` with its lighting permission set to `on`."""
    return LiveRoom(
        id=room.id,
        name=room.name,
        type=room.type,
        bindings=dict(room.bindings),
        auto_lighting=on,
        mode=room.mode,
    )


def _room_document(room: LiveRoom) -> Mapping[str, object]:
    """One room as the document `_room_from_document` reads back."""
    return {
        "id": room.id,
        "name": room.name,
        "type": room.type,
        "bindings": dict(room.bindings),
        "auto_lighting": room.auto_lighting,
        "mode": room.mode,
    }


def _room_from_document(document: Mapping[str, object]) -> LiveRoom:
    """One room, rebuilt from `_room_document`'s shape."""
    raw = document.get("bindings")
    bindings = (
        {str(slot): str(entity) for slot, entity in raw.items()}
        if isinstance(raw, Mapping)
        else {}
    )
    return LiveRoom(
        id=str(document.get("id", "")),
        name=str(document.get("name", "")),
        type=str(document.get("type", "")),
        bindings=bindings,
        auto_lighting=bool(document.get("auto_lighting", True)),
        mode=str(document.get("mode", "")),
    )


def _documents(value: object, field_name: str) -> tuple[Mapping[str, object], ...]:
    """`value` as a tuple of mappings, or a failure naming the field."""
    if not isinstance(value, (list, tuple)):
        raise LiveSessionError(f"a session state needs a {field_name!r} list")
    return tuple(
        item if isinstance(item, Mapping) else _not_an_object(field_name)
        for item in value
    )


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    """`value` as a mapping, or a failure naming the field."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise LiveSessionError(f"a session state needs {field_name!r} to be an object")
    return value


def _strings_mapping(value: object) -> Mapping[str, str]:
    """`module_rooms`: pack names to room ids, both strings.

    Absent is the empty mapping and not a failure, because a document written
    before the field existed has none recorded and is still a valid house --
    `_module_room` falls back to joining the pack's slots for those. A value
    that is not a string is a different thing: it is a document that says
    something this build cannot read, and it is refused rather than quietly
    dropped so a module cannot end up in a room nobody chose.
    """
    entries = _mapping(value, "module_rooms")
    if not all(
        isinstance(name, str) and isinstance(room, str)
        for name, room in entries.items()
    ):
        raise LiveSessionError(
            "a session state needs 'module_rooms' to map pack names to room ids"
        )
    return cast("Mapping[str, str]", entries)


def _labels(value: object) -> tuple[str, ...]:
    """The mode labels a state document holds, which are plain strings."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _not_an_object(field_name: str) -> Mapping[str, object]:
    raise LiveSessionError(f"every entry of {field_name!r} must be an object")
