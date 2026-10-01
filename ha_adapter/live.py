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
from .transport import HaTransport

__all__ = ["SESSION_STATE_VERSION", "LiveSession", "LiveSessionError"]

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
    #: The packs this house holds.
    installed: InstalledSet = field(default_factory=InstalledSet)
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
        installed: InstalledSet | None = None,
        profiles: ProfileSet | None = None,
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
            installed=InstalledSet() if installed is None else installed,
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
            installed=self.installed,
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

    def bind(self, room_id: str, slot: str, entity_id: str) -> LiveRoom:
        """Point `slot` in `room_id` at `entity_id`, replacing whatever was there.

        The old binding is not recoverable from here: the *caller* records what
        it replaced, because only the caller has a decision log to record it in,
        and a session that kept a history of bindings would be a second place the
        house's past is written down.
        """
        room = self.require_room(room_id)
        if slot not in room.bindings and slot not in self.vocabulary.slots:
            raise LiveSessionError(f"there is no slot {slot!r} in this house")
        updated = _rebind(room, slot, entity_id)
        self.set_rooms(
            tuple(updated if other.id == room_id else other for other in self.rooms)
        )
        return updated

    def unbind(self, room_id: str, slot: str) -> LiveRoom:
        """Leave `slot` in `room_id` unbound."""
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


def _labels(value: object) -> tuple[str, ...]:
    """The mode labels a state document holds, which are plain strings."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _not_an_object(field_name: str) -> Mapping[str, object]:
    raise LiveSessionError(f"every entry of {field_name!r} must be an object")
