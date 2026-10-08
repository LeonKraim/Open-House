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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from engine.behaviours import enable_key
from engine.binding import HouseScope, RoomScope, UnknownSlotError
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
from .slot_parts import grow as with_slot_parts
from .slot_parts import key_of as part_key
from .slot_parts import record_from as recorded_slot_parts
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
    #: The slots a person has split, by the slot each part belongs to.
    #:
    #: `{"light_group": ("a", "b")}` -- the record `ha_adapter.slot_parts` owns.
    #: It is house-level like `house_bindings` and for a neighbouring reason: the
    #: parts of a slot are a fact about the *role*, not about the room that
    #: happens to bind them, and two rooms that both split their lights are
    #: splitting the same vocabulary word. So it is saved and restored beside
    #: `house_bindings` and absent from a house that has split nothing, which is
    #: every house until somebody asks for a second device on one role.
    #:
    #: It reaches the engine as vocabulary growth (`composition.build_live_house`)
    #: and not as a resolution rule of its own: a part is a slot of its own under
    #: the parent's name, so the binding layer, the engine and the panel's slot
    #: list read it without knowing that parts exist.
    slot_parts: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Room-scope settings a person has set, by room id and resolver key.
    #:
    #: The room layer of the resolver, which `build_live_house` fills from the
    #: rooms' own switches and which this carries the rest of: a pack's option a
    #: person tuned in one room, and the enable flag of a module placed there.
    #:
    #: It has to live here because nothing else can hold it. A room is a config
    #: *subentry* and stores its bindings and nothing else, and the engine's
    #: override layer -- where these wrote before this field existed -- is
    #: in-memory by definition (`engine/config.py`: "the override layer is the one
    #: that changes during a run, because it is temporary"), so every rebuild
    #: dropped it. A rebuild happens on every profile activation, so a person who
    #: turned a module on in the kitchen and then switched a profile on had it
    #: turned off again in silence, and a Home Assistant restart did the same.
    room_settings: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
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
    #: The directory a person's own packs are read from and written to, when the
    #: host has one.
    #:
    #: The checkout is read-only -- it is the integration's own source tree, and
    #: a module a person authors belongs beside their house rather than inside a
    #: program they will update. So authoring has a second root, and this is it:
    #: `<config>/open_house/packs`, handed in by the Home Assistant host, whose
    #: `config` directory is the only writable place in a live instance. It is
    #: `None` offline, where the tests build a session against a fixture tree and
    #: there is nothing of the host's to write into; nothing downstream assumes
    #: otherwise, because every reader treats `None` as "no authored packs".
    user_root: Path | None = None
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
        slot_parts: Mapping[str, tuple[str, ...]] | None = None,
        room_settings: Mapping[str, Mapping[str, object]] | None = None,
        installed: InstalledSet | None = None,
        profiles: ProfileSet | None = None,
        module_rooms: Mapping[str, str] | None = None,
        user_root: Path | None = None,
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
            slot_parts={
                parent: tuple(names) for parent, names in (slot_parts or {}).items()
            },
            room_settings={
                room_id: dict(values)
                for room_id, values in (room_settings or {}).items()
            },
            installed=InstalledSet() if installed is None else installed,
            module_rooms=dict(module_rooms or {}),
            user_root=user_root,
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

    @property
    def revision(self) -> int:
        """How many times the profiles have moved under whoever was reading them.

        The session's, because the profiles are the session's and this is what a
        page is asked to send back with its next write: a page renders the house
        from one revision and a *switch* replaces the house underneath it, so a
        write that names the revision before the switch is a write about a house
        nobody is looking at any more -- see
        `custom_components/open_house/websocket_api.py`, which is where it is
        checked, and `engine/profiles.py` for what counts as a move.
        """
        return self.profiles.revision

    # -- Edits --------------------------------------------------------------

    def rebuild(self) -> None:
        """Build the engine these fields produce, reusing the adapter."""
        if not self.rooms:
            raise LiveSessionError("a house needs at least one room")
        ids = [room.id for room in self.rooms]
        if len(set(ids)) != len(ids):
            raise LiveSessionError(f"two rooms share the id {ids}")
        # A slot the vocabulary does not carry reaches here as `UnknownSlotError`
        # -- a house binding left on a part nobody carries any more, a room still
        # naming a slot a pack's removal took away -- and the command layer above
        # turns exactly one type into a refusal the panel can read
        # (`LiveSessionError`). Left bare, this is a websocket call answering with
        # a traceback instead of an error code, so it is translated here, at the
        # one door every edit and every restart rebuilds through.
        try:
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
                slot_parts=self.slot_parts,
                room_settings=self.room_settings,
                installed=self.installed,
                module_rooms=self.module_rooms,
                profiles=self.profiles,
                adapter=self.adapter,
                user_root=self.user_root,
            )
        except UnknownSlotError as refusal:
            raise LiveSessionError(str(refusal)) from refusal
        self.adapter = house.adapter
        self._engine = house.engine

    def set_rooms(self, rooms: Sequence[LiveRoom]) -> None:
        """Replace the rooms and rebuild. The house document *is* the rooms.

        A room the new list does not hold loses its recorded settings with it.
        Keeping them would leave a map growing a tombstone per room ever removed
        -- including for a room re-added later under a name somebody else had,
        which would come back tuned to a stranger's choices.
        """
        self.rooms = tuple(rooms)
        listed = {room.id for room in self.rooms}
        if any(room_id not in listed for room_id in self.room_settings):
            self.room_settings = {
                room_id: dict(values)
                for room_id, values in self.room_settings.items()
                if room_id in listed
            }
        self.rebuild()

    def set_room_auto_lighting(self, room_id: str, *, on: bool) -> None:
        """Turn a room's lighting behaviours on or off, as the room's switch does.

        A *permission*, not an actuation (`const.ENTITY_AUTO_LIGHTING`): it
        writes the room layer's enable flag and stops there, so the next tick
        finds nothing to do for that room rather than turning its light off now.
        An override is engine state, not wiring, so this does not rebuild -- it
        is the same call the room's switch entity already makes.

        The room's own document is updated beside the override, and it has to be:
        the room's `auto_lighting` is what a *rebuild* reads to fill the room
        layer, so a switch that wrote only the override was a switch whose state
        the next rebuild -- and the next restart -- silently reverted. Any
        remembered enable flag for the lighting behaviours is dropped at the same
        time, for the reason `set_auto_lighting` gives: the switch is the master
        for those two behaviours, and a remembered value laid over it would make
        the off position do nothing.
        """
        self.require_room(room_id)
        self.rooms = tuple(
            _with_auto_lighting(room, on) if room.id == room_id else room
            for room in self.rooms
        )
        self._forget_lighting_flags(room_id)
        scope = RoomScope(room_id)
        for behaviour in AUTO_LIGHTING_BEHAVIOURS:
            key = enable_key(behaviour)
            # The running engine's room layer is set to the switch's own answer,
            # which is what a rebuild would produce (`composition._room_layer`),
            # so the layer under the override does not hold a value the room's
            # switch has since contradicted.
            self.engine.settings.set_room_setting(key, room_id, on)
            self.engine.settings.set_override(key, scope, on)

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
        extended = with_declared_slots(
            self.root, self.installed, self.vocabulary, user_root=self.user_root
        )
        # And the parts, so a room may be given a device for one. The same growth
        # the rebuild applies, in the same order: a part of a pack's own device is
        # a part of a key the *declaration* brought, so the parts are read over
        # the extended vocabulary rather than beside it.
        return frozenset(with_slot_parts(extended, self.slot_parts).slots)

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

    def part_bound_in(self, parent: str, part: str) -> tuple[str, ...]:
        """Everywhere one part of one slot holds a device, by the name a person reads.

        The other half of the sentence a *removal* is refused with. A part's device
        is bound in a room, or by the house -- not on a module -- so dropping the
        part from the record while a binding still names it would leave that room
        naming a slot the vocabulary no longer carries, and the very rebuild this
        removal triggers would refuse the whole house
        (`engine.binding._validate`). The modules half of the refusal
        (`live_modules.modules_on_part`) catches the modules; this catches the
        device, and neither can be skipped: a part nobody's module is on is still
        a part something is bound to.

        The rooms are named rather than counted, and the house's own binding is
        named as itself, because a person reading the refusal has to know which
        page to go to.
        """
        key = part_key(parent, part)
        found = [room.name for room in self.rooms if key in room.bindings]
        if key in self.house_bindings:
            found.append("the house")
        return tuple(found)

    def rebind_slot_part(self, parent: str, was: str, name: str) -> tuple[str, ...]:
        """Move every binding that named one part onto the part's new name.

        **The half of a rename that a person would not think to ask for, and the
        half that makes the other half safe.** A part's name *is* the key it binds
        under (`ha_adapter.slot_parts`), so renaming a part renames a key -- and
        every binding that named it is now naming a word the vocabulary is about
        to stop carrying. Left alone, the next rebuild would refuse the whole house
        (`engine.binding` raises `UnknownSlotError` for a binding the vocabulary
        does not define), which is a rename that takes the house down rather than
        one that tidies a name.

        So the rooms' bindings and the house's are rewritten here, **before** the
        record changes and before the one rebuild the caller then does: the
        session's fields are read by `rebuild`, not held by it, which is what makes
        an atomic swap possible at all -- a `set_rooms` between the two halves
        would rebuild against a record that disagrees with the rooms.

        Returns the rooms whose bindings moved, so the caller can write each of
        them back into its own subentry. The house's own binding is carried in
        `house_bindings`, which the caller saves with the rest of the session
        state, so it needs no name here.
        """
        old = part_key(parent, was)
        new = part_key(parent, name)
        moved: list[str] = []
        rooms: list[LiveRoom] = []
        for room in self.rooms:
            entity_id = room.bindings.get(old)
            if entity_id is None:
                rooms.append(room)
                continue
            bindings = {
                key: value for key, value in room.bindings.items() if key != old
            }
            bindings[new] = entity_id
            rooms.append(replace(room, bindings=bindings))
            moved.append(room.id)
        self.rooms = tuple(rooms)
        if old in self.house_bindings:
            house = {
                key: value for key, value in self.house_bindings.items() if key != old
            }
            house[new] = self.house_bindings[old]
            self.house_bindings = house
        return tuple(moved)

    def set_slot_parts(self, record: Mapping[str, tuple[str, ...]]) -> None:
        """Replace the parts record and rebuild.

        A rebuild and not an override, because a part is a *vocabulary word* and
        not a setting: `engine/binding.py` refuses a binding for a name the
        vocabulary does not carry, and the vocabulary is built by a rebuild. So
        adding a part is the one edit here that can change what the house is able
        to hold, which is exactly why it is this one call and not a write to a
        setting layer somewhere else.

        The caller has already validated the record (`ha_adapter.slot_parts`), so
        there is nothing to refuse here: an unvalidated record would make this a
        second place the rules live, and the rules have one home.
        """
        self.slot_parts = {
            parent: tuple(names) for parent, names in record.items() if names
        }
        self.rebuild()

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

    def room_settings_for(self, room_id: str) -> Mapping[str, object]:
        """The room layer's recorded values for `room_id`, or the empty mapping.

        The room's *own* settings, not the ones it inherits: a caller asking
        whether a module was switched on in the kitchen wants the kitchen's
        answer, and a value that is not here is one the built-in default or the
        pack's own default answers for.
        """
        return dict(self.room_settings.get(room_id, {}))

    def set_room_settings(self, room_id: str, settings: Mapping[str, object]) -> None:
        """Replace one room's recorded layer values and rebuild.

        The room is replaced whole rather than merged key by key, so a caller
        that removes a key can express that: `set_room_settings(room, {})` is
        how a room is returned to its defaults. A room id the house does not
        hold is refused -- silently recording settings for a room that is not
        here would be a value no reader could ever reach.
        """
        self.require_room(room_id)
        recorded = {room: dict(values) for room, values in self.room_settings.items()}
        if settings:
            recorded[room_id] = dict(settings)
        else:
            recorded.pop(room_id, None)
        self.room_settings = recorded
        self.rebuild()

    def set_room_setting(self, room_id: str, key: str, value: object) -> None:
        """Record one value in a room's layer, leaving its other values alone.

        The single-key form of `set_room_settings`, for the callers whose whole
        intent is one switch: a module turned on in a room, a pack's option
        tuned there.
        """
        self.require_room(room_id)
        current = self.room_settings_for(room_id)
        current[key] = value
        self.set_room_settings(room_id, current)

    def forget_room_settings(self, room_id: str) -> None:
        """Drop a room's recorded settings, for a room that has been removed."""
        if room_id not in self.room_settings:
            return
        recorded = {room: dict(values) for room, values in self.room_settings.items()}
        recorded.pop(room_id, None)
        self.room_settings = recorded
        self.rebuild()

    def setting(self, key: str, scope: HouseScope | RoomScope) -> object | None:
        """The value recorded for `key` at `scope`, or `None` when none is.

        The recorded layer and not the resolver, because this answers the
        question a *switch* asks -- "has somebody said something about this, or
        is this the default?" -- and the resolver's answer folds in the built-in
        defaults, the profiles and whatever override is standing at this instant.
        A caller that wants what the engine would decide by resolves it there.
        """
        if isinstance(scope, HouseScope):
            return self.house_settings.get(key)
        return self.room_settings.get(scope.room_id, {}).get(key)

    def remember_setting(
        self, key: str, scope: HouseScope | RoomScope, value: object
    ) -> None:
        """Record a setting in its persistent layer *and* apply it to the engine.

        **Both, or neither works**, and that is the whole reason this exists as
        one call rather than two at each site. As a value in the session's own
        settings it survives a restart and a rebuild, because those are exactly
        what `to_state` carries and `build_live_house` reads back; as an override
        on the running engine it takes effect now. A site that wrote only the
        first would leave the switch looking inert until the next rebuild, and a
        site that wrote only the second would forget the change at the next one
        -- and a rebuild is what every profile activation performs, so the second
        half is the one that was missing whenever a person set a module up in a
        room and then switched a profile on.

        The *layer* a value belongs in follows its scope, and this is the one
        place that mapping is written down: the house scope's values are the
        house's settings (`Layer.HOUSE`, what the House tab edits) and a room
        scope's are that room's (`Layer.ROOM`, which nothing outside this field
        can hold -- see `room_settings`). A room scope naming a room the house
        does not hold is refused rather than recorded, so a settings map cannot
        fill up with values for rooms that are not here.

        Nothing is rebuilt, and *three* writes keep that honest. The engine's
        persistent layer is written too (`ConfigResolver.set_house_setting` /
        `set_room_setting`), because those layers are copies taken when the
        engine was built: a session that recorded a setting without telling the
        running resolver would leave the engine deciding by the value it was
        built with, so a person's switch would appear to do nothing until
        something else happened to rebuild. The override goes on top of it
        because a person's deliberate choice has to outrank the profile layer,
        which sits above both persistent layers and which a rebuild would
        otherwise let reinstate. And the session's own settings are the record a
        restart reads.

        The three agree by construction, which is what makes a rebuild a no-op
        for this setting instead of an amnesia.
        """
        if isinstance(scope, HouseScope):
            self.house_settings = {**self.house_settings, key: value}
            self.engine.settings.set_house_setting(key, value)
        else:
            self.require_room(scope.room_id)
            recorded = {
                room: dict(values) for room, values in self.room_settings.items()
            }
            room = dict(recorded.get(scope.room_id, {}))
            room[key] = value
            recorded[scope.room_id] = room
            self.room_settings = recorded
            self.engine.settings.set_room_setting(key, scope.room_id, value)
        self.engine.settings.set_override(key, scope, value)

    def forget_setting(self, key: str, scope: HouseScope | RoomScope) -> None:
        """Drop a recorded setting and clear its override -- the other half.

        The pair to `remember_setting`, and it removes rather than writing a
        default: "off" is the absence of a decision, not a second decision that
        happens to agree with the one below it, so a person who switches a module
        off leaves nothing behind that would stand in the way of a profile or a
        pack default wanting it on. Clearing an override that is not set is not a
        failure (`ConfigResolver.clear_override`), so this is safe to call for a
        setting that only one of the three layers ever held.

        The engine's persistent layer is cleared beside the record, and that half
        is not optional: those layers are snapshots taken when the engine was
        built, so clearing only the override would leave the snapshot's value
        answering -- a module that stayed on after being switched off, for
        exactly as long as nothing else rebuilt.
        """
        if isinstance(scope, HouseScope):
            self.house_settings = {
                name: held for name, held in self.house_settings.items() if name != key
            }
            self.engine.settings.clear_house_setting(key)
        else:
            recorded = {
                room: dict(values) for room, values in self.room_settings.items()
            }
            room = dict(recorded.get(scope.room_id, {}))
            room.pop(key, None)
            if room:
                recorded[scope.room_id] = room
            else:
                # A room with nothing left said about it is a room with no entry,
                # rather than one holding an empty mapping: the two read the same
                # to every reader here, and the smaller document is the honest one.
                recorded.pop(scope.room_id, None)
            self.room_settings = recorded
            self.engine.settings.clear_room_setting(key, scope.room_id)
        self.engine.settings.clear_override(key, scope)

    def set_auto_lighting(self, *, on: bool) -> None:
        """Set every room's lighting permission, as a house-wide switch would.

        The rooms are replaced rather than the flags set one by one, because a
        rebuilt room carries the flag into the engine's *room settings* at
        construction -- the same path a freshly activated setup takes, so a
        house-wide switch and a fresh setup cannot disagree about what "on" means.

        Any remembered enable flag for the lighting behaviours goes with it. A
        recorded value is laid *over* the room's own switch at construction
        (`composition.build_live_house`), because an edit has to win over a
        default -- but the room's switch is not a default: it is the room's
        permission for those behaviours, and a remembered `True` left behind
        would make turning the permission off a switch that visibly does nothing.
        The switch is the master for exactly these two behaviours, so setting it
        clears their remembered flags rather than recording a second opinion
        beside them.
        """
        self.rooms = tuple(_with_auto_lighting(room, on) for room in self.rooms)
        self._forget_lighting_flags()
        self.rebuild()

    def _forget_lighting_flags(self, room_id: str | None = None) -> None:
        """Drop remembered enable flags for the lighting behaviours.

        Every room's, or one room's when `room_id` is named -- the two callers
        are the house-wide switch and one room's own.
        """
        keys = {enable_key(behaviour) for behaviour in AUTO_LIGHTING_BEHAVIOURS}
        recorded = {
            room: {
                name: value
                for name, value in values.items()
                if name not in keys and (room_id is None or room == room_id)
            }
            if room_id is None or room == room_id
            else dict(values)
            for room, values in self.room_settings.items()
        }
        pruned = {room: values for room, values in recorded.items() if values}
        if pruned != dict(self.room_settings):
            self.room_settings = pruned

    # -- State --------------------------------------------------------------

    def to_state(self) -> Mapping[str, object]:
        """The session as a document, for a restart to rebuild from.

        Configuration only. The engine's own state -- which lights it is holding
        on, how long a room has been quiet -- is deliberately not here: it
        describes a house at an instant, and an instant that crossed a restart
        would describe a house that no longer exists. The engine starts fresh and
        the *configuration* survives, which is the half a person set by hand.

        The house's own bindings and the parts record travel with it, and they
        have to: both are session state that reaches the engine as wiring rather
        than as a setting (`house_bindings`, `slot_parts`), so a document that
        dropped them would rebuild a house whose global slots resolved to nothing
        and whose parts had never been split. `slot_parts` is written as lists
        because a document is JSON-shaped data, and read back through the same
        lenient reader the store uses (`slot_parts.record_from`), so one shape
        means one shape wherever a parts record is written down.
        """
        return {
            "version": SESSION_STATE_VERSION,
            "house_name": self.house_name,
            "modes": list(self.modes),
            "rooms": [_room_document(room) for room in self.rooms],
            "house_settings": dict(self.house_settings),
            "room_settings": {
                room_id: dict(values) for room_id, values in self.room_settings.items()
            },
            "house_bindings": dict(self.house_bindings),
            "slot_parts": {
                parent: list(names) for parent, names in self.slot_parts.items()
            },
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
            # Absent from a document written before the field existed, and the
            # empty mapping is the honest reading: every role resolves from the
            # rooms, exactly as it did then.
            house_bindings=_strings_mapping(
                state.get("house_bindings"), "house_bindings"
            ),
            # Absent from a document written before a slot could be split, and the
            # empty record is the honest reading: no slot has parts and every role
            # resolves as a whole. `slot_parts.record_from` is the reader the store
            # uses for the same record, so a written-down record has one shape.
            slot_parts=recorded_slot_parts(state.get("slot_parts")),
            # Absent from a document written before the field existed, and the
            # empty mapping is the honest reading: no room has been tuned.
            room_settings=_room_settings(state.get("room_settings")),
            installed=InstalledSet.from_document(
                _mapping(state.get("installed"), "installed")
            ),
            # Absent from a document written before the field existed, and the
            # empty mapping is the honest reading: nothing has been placed.
            module_rooms=_strings_mapping(state.get("module_rooms"), "module_rooms"),
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


def _room_settings(value: object) -> Mapping[str, Mapping[str, object]]:
    """`room_settings`: room ids to that room's layer values.

    Absent is the empty mapping and not a failure: a document written before the
    ROOM layer was recorded is still a house whose rooms are simply untuned, and
    the built-in defaults stand in for every room. A room whose entry is not an
    object is a document this build cannot read, so it is refused by name rather
    than dropped -- silently discarding it would turn a room somebody tuned into
    a room that looks untuned, which is a change nobody asked for.

    The values are copied into plain dicts so a later write to the live resolver
    cannot reach back into the document it was read from.
    """
    rooms = _mapping(value, "room_settings")
    settings: dict[str, Mapping[str, object]] = {}
    for room_id, values in rooms.items():
        if not isinstance(values, Mapping):
            raise LiveSessionError(
                f"a session state needs room_settings[{room_id!r}] to be an object"
            )
        settings[str(room_id)] = dict(values)
    return settings


def _strings_mapping(value: object, field_name: str) -> Mapping[str, str]:
    """`field_name` as a mapping of string to string, or a failure naming it.

    Both `module_rooms` (pack names to room ids) and `house_bindings` (slot
    names to entity ids) are this shape, and the two are read the same way.
    Absent is the empty mapping and not a failure, because a document written
    before the field existed has none recorded and is still a valid house --
    `_module_room` falls back to joining the pack's slots for the first, and the
    house scope falls back to collecting the rooms for the second. A value that
    is not a string is a different thing: it is a document that says something
    this build cannot read, and it is refused rather than quietly dropped so a
    module cannot end up in a room nobody chose and a slot cannot resolve to
    something that is not an entity.
    """
    entries = _mapping(value, field_name)
    if not all(
        isinstance(name, str) and isinstance(entry, str)
        for name, entry in entries.items()
    ):
        raise LiveSessionError(
            f"a session state needs {field_name!r} to map names to strings"
        )
    return cast("Mapping[str, str]", entries)


def _labels(value: object) -> tuple[str, ...]:
    """The mode labels a state document holds, which are plain strings."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _not_an_object(field_name: str) -> Mapping[str, object]:
    raise LiveSessionError(f"every entry of {field_name!r} must be an object")
