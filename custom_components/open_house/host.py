"""The live session's Home Assistant half: where its state lives between runs.

`ha_adapter/live.py` holds a session -- the engine, the rooms, the installed
packs, the profile selections -- and knows nothing about Home Assistant. This
module is the other side of that seam. It builds a session out of a config
entry, gives it somewhere to be written down, and is the one object the websocket
API reads: a handler asks the *host*, and the host is what knows which subentry a
room came from and which file its packs are recorded in.

**Rooms are the entry's; everything else is the store's.** The spec is explicit
that Home Assistant areas are the source of truth for rooms, so a room's
existence, its type and its bindings are read from the entry's subentries and are
never read back from disk. What has no Home Assistant home -- which packs are
installed, which profiles are declared and selected, the house-scope settings --
is written to `helpers.storage.Store`, one file per entry. Splitting the two this
way is what keeps a restart honest: a room deleted in Home Assistant cannot come
back from a file, and a pack install cannot be lost because a subentry was
rewritten.

**An edit is written twice, on purpose.** A rebinding changes the subentry *and*
the live session. The subentry is the durable fact and the session edit is what
makes the answer the panel gets back correct immediately, rather than after the
reload the subentry change schedules. The two are written from the same argument
in the same call, so they cannot disagree; they are written twice because one is
for the next restart and the other is for this screen.

**Nothing here decides anything.** Every method either reads Home Assistant's
registries or forwards an edit to the session. A judgement about what a house
*should* do belongs to the engine, and a judgement about what a room *is* belongs
to the setup flow (`ha_adapter/setup_flow.py`); this is plumbing between them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from engine.install import InstalledSet
from engine.profiles import Profile, ProfileSet, load_profile_schema
from ha_adapter import (
    live_modules,
    live_profiles,
    module_host,
    module_records,
    slot_parts,
)
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import HOUSE, LiveSession, LiveSessionError

from .const import (
    DATA_AREA_ID,
    DATA_BINDINGS,
    DATA_ROOM_TYPE,
    DOMAIN,
    MODES,
    PACKS_DIRECTORY,
    SUBENTRY_ROOM,
    catalog_root,
)
from .runtime import OpenHouseRuntime, RoomRuntime
from .transport import HassTransport

if TYPE_CHECKING:
    # Only for the annotation: `views` imports this module, so a real import here
    # would be a cycle. The catalog is built by `views.load_catalog` and handed in,
    # which is the direction that keeps a request path from re-reading the disk.
    from .views import Catalog

__all__ = ["DATA_SESSION", "OpenHouseHost", "RoomRef", "async_setup_host", "host_for"]

_LOGGER = logging.getLogger(__name__)

#: `hass.data[DOMAIN]` key holding `{entry_id: OpenHouseHost}`.
DATA_SESSION = "hosts"

#: The store's version. Bumped when the *shape* of the persisted document
#: changes, so an older file is migrated rather than half-read.
STORE_VERSION = 1

#: The room type a room with none is given. Matches `automation._FALLBACK_ROOM_TYPE`
#: and the setup flow's own fallback: the engine validates a type's shape and
#: never reads its meaning, so a missing one is a label to fill.
_FALLBACK_ROOM_TYPE = "living_room"


@dataclass(frozen=True, slots=True)
class RoomRef:
    """One room, as three names for it and the facts that describe it.

    A room genuinely has three ids in a live house and they are not
    interchangeable: the *area* is Home Assistant's, the *subentry* is the config
    entry's, and the *room id* is the engine's. The panel speaks the engine's,
    a subentry change needs the entry's, and the area is what a person edits in
    Home Assistant. Keeping all three in one value is what stops a call site from
    passing one where another was meant -- which is the only way this seam can
    fail silently, because all three are strings.
    """

    subentry_id: str
    area_id: str
    #: The engine's room id (`composition.room_id`).
    id: str
    name: str
    type: str
    bindings: Mapping[str, str]


class OpenHouseHost:
    """One config entry's live session, its rooms, and its stored state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        runtime: OpenHouseRuntime,
        session: LiveSession,
        rooms: Mapping[str, RoomRef],
        store: Store[dict[str, Any]],
        catalog: Catalog,
    ) -> None:
        self.hass = hass
        self.entry = entry
        #: What the per-room entities read. Held so a view answers with the same
        #: values Home Assistant is showing rather than reading the sensor a
        #: second time and being able to disagree with the entity beside it.
        self.runtime = runtime
        self.session = session
        #: Keyed by the engine's room id, because that is what every caller has.
        self.rooms = dict(rooms)
        #: The vocabulary and the pack registry, read once at setup (see
        #: `views.Catalog`). Held on the host rather than re-read per command
        #: because the panel's commands run on the event loop, where a file read
        #: is a blocking call.
        self.catalog = catalog
        self._store = store
        self._activity_listeners: list[Callable[[Mapping[str, object]], None]] = []
        #: The last decision record handed to a subscriber, so the next publish
        #: can tell what is new. Identity is by value: `DecisionRecord` is a
        #: frozen dataclass, and two records that compare equal *are* the same
        #: decision, so re-sending one is the only harm a false match can do.
        self._last_published: object | None = None
        #: Whether a profile is being put back on the house right now. A restore
        #: writes the house through the same doors a person's edits use, so
        #: without this a profile would be learning the half-restored house on
        #: its way back in -- see `async_save`.
        self._restoring = False

    # -- Reads --------------------------------------------------------------

    def room(self, room_id: str) -> RoomRef | None:
        """The room called `room_id`, or `None`. `room_id` is the engine's id."""
        return self.rooms.get(room_id)

    def require_room(self, room_id: str) -> RoomRef:
        """The room called `room_id`, or a `KeyError` a handler turns into a code."""
        found = self.rooms.get(room_id)
        if found is None:
            raise KeyError(room_id)
        return found

    def subentry_for(self, room_id: str) -> ConfigSubentry | None:
        """The subentry that describes a room, found by its area's unique id."""
        room = self.room(room_id)
        if room is None:
            return None
        return self.entry.subentries.get(room.subentry_id)

    def runtime_room(self, room_id: str) -> RoomRuntime | None:
        """The live room the entities read, for the values only it holds.

        Occupancy and a room's mode are *entity* state rather than engine state
        -- the binary sensor reads the bound device and the select holds a
        choice -- so the panel has to read them from the same object the
        entities write. Reading the bound sensor again here would answer a
        second question and could answer it differently.
        """
        room = self.room(room_id)
        return None if room is None else self.runtime.room(room.subentry_id)

    # -- Room edits ---------------------------------------------------------

    def bound_slots(self, room_id: str) -> module_host.BoundSlots:
        """What `room_id` answers with: each slot name, and the entity it names.

        The same two layers `engine.binding.resolve_slot` reads for a room scope
        -- the house's own binding, and the room's over the top of it -- because
        a module reaching through a slot has to be built from what the engine
        would have resolved, or the automation an import writes and the decision
        the engine makes would be about two different devices.

        The empty `room_id` is the whole house: a module that belongs to no room
        resolves against the house's bindings alone, which is what a pack's
        house-scope slots do.

        Both layers are returned, not just the merged one, because a **global**
        slot answers from the house's own binding alone -- in *every* room,
        including one that bound the same name for itself. The merged mapping is
        what a `BoundSlots` is as a mapping, so every caller that only ever meant
        "what does this room answer" reads exactly what it read before.
        """
        house = dict(self.session.house_bindings)
        if not room_id:
            return module_host.BoundSlots(room=house, house=house)
        room = self.session.room(room_id)
        if room is None:
            return module_host.BoundSlots(room=house, house=house)
        merged = dict(house)
        merged.update(room.bindings)
        return module_host.BoundSlots(room=merged, house=house)

    def bound_slots_for(self, module: str, room_id: str) -> module_host.BoundSlots:
        """What one module answers through: the room's bindings, then its own.

        `bound_slots` is the *room's* answer and is what every module in it reads.
        This is the same value with one module's own overrides over the top
        (`live_modules.slot_overrides`), which is the third and most specific of
        the three layers a slot resolves through -- the house's, the room's, and
        the one device this module was pointed at.

        **Needed because a hosted module is not built by the engine.** The engine
        reads the override itself when it evaluates a pack (`engine.engine
        ._slot_override`), so a declared pack acting in a room has always honoured
        it. A module's own automation is built from this map instead, and a map
        without the override produced a module whose card said one device and
        whose automation used another -- see `live_modules.slot_overrides`.

        `module` is the *slug*; the override lives in the pack's namespace
        (`module.<pack>.slot.<slot>.entity`), and a hosted module's record names
        the pack it was made from.

        **A module on a part of a split slot resolves through the part**
        (`ha_adapter.slot_parts`): the part is a binding key of its own under the
        parent's name, so this map is rewritten before the overrides go on --
        `light_group` is pointed at whatever the room bound for `light_group__a`.
        Before, because the two are not competing answers to the same question:
        the part is *where the module lives in the role*, and an override is a
        person pointing this one module at its own lamp, which is the more
        specific statement and stays the last word. A module naming a part the
        house does not carry is reported as on no part at all
        (`slot_parts_of`), so this leaves it on the slot's own device.
        """
        base = self.bound_slots(room_id)
        record = self.session.engine.installed.get(module)
        if record is None:
            return base
        parts = live_modules.slot_parts_of(
            self.session, pack=record.name, room_id=room_id
        )
        overrides = live_modules.slot_overrides(
            self.session, pack=record.name, room_id=room_id
        )
        if not parts and not overrides:
            return base
        room = dict(base.room)
        house = dict(base.house)
        for slot, part in parts.items():
            key = slot_parts.key_of(slot, part)
            device = room.get(key)
            if device is None:
                # The part is carried but nothing bound it: fall back to the
                # slot's own device rather than dropping the name, so the module
                # acts on something the room answered with instead of a slot that
                # resolves to nothing. `house` is checked too, because a global
                # slot's device is the house's in every room.
                device = house.get(key)
            if device is not None:
                room[slot] = device
                house[slot] = device
        room.update(overrides)
        # Both views, because an override is the most specific statement there is:
        # a module pointed at its own lamp means it whether the input was answered
        # with the room's slot or the house's *global* one, and a global slot is
        # global because it is the house's device in every room -- not because a
        # module may not aim it somewhere else for itself.
        house.update(overrides)
        return module_host.BoundSlots(room=room, house=house)

    async def async_slot_changed(self, room_id: str, slot: str) -> None:
        """A slot's device moved without anybody picking one: write it down, rebuild.

        What a *rule* that has worked out a new device asks for, and the reason it
        is one call rather than two lines at the watcher: the answer is a setting
        that has to survive a restart (`async_save`) and a device the modules
        reaching that slot were built from (`_async_rebuild_modules`), and a caller
        that did one without the other would leave the running house and the saved
        one describing two different slots.

        The same pair a rebinding does, minus the subentry: that half exists to
        record what a *person* decided about their room, and nobody decided
        anything here.
        """
        await self.async_save()
        await self._async_rebuild_modules(room_id, slot)

    async def async_set_slot_part(
        self, *, parent: str, action: str, name: str, new_name: str = ""
    ) -> Mapping[str, tuple[str, ...]]:
        """Add, rename or take away a part of one slot, and rebuild what it moved.

        A slot split into parts (`ha_adapter.slot_parts`) is a *house-level* record
        and a vocabulary word at once, so this is one call and not two: the record
        is validated by the pure module, written through the session -- which is
        what grows the parts into the vocabulary and rebuilds the engine
        (`LiveSession.set_slot_parts`) -- saved, and then every module the edit
        moved is built again.

        **The three actions are three different kinds of change**, which is why
        the rebuild differs:

        * *add* moves nobody. A new part has no device bound and no module on it;
          the panel draws one more choice on the row and nothing else happens.
        * *rename* moves every module that was on the old name, because a part's
          name is the key it binds under and a module names that key
          (`live_modules.rename_slot_part`). Those modules have to be built again:
          left as they were, they would name a part the slot no longer has and
          quietly fall back to the slot's own device. It moves the **bindings**
          too, for the same reason one step out (`LiveSession.rebind_slot_part`):
          the room that gave the old half a device keeps that device under the new
          name, and its subentry is written so a restart agrees.
        * *remove* is **refused while a module still names the part**, naming the
          modules. A part is one bound device shared by everything on it, so
          taking it away would move all of them without saying so -- and a control
          that silently relocates three automations is worse than a control that
          says no. The sentence names them so a person knows which rows to move
          first.

        The empty `parent` is refused by `slot_parts.add`/`rename`/`remove`
        themselves where it matters, and the *unknown slot* case is not checked
        here: a part of a slot no room binds is a choice nothing offers yet, and
        the vocabulary grows it the same way -- which is deliberately the same
        path as naming an unknown slot at import.
        """
        record = self.session.slot_parts
        rebound: tuple[str, ...] = ()
        try:
            if action == "add":
                updated = slot_parts.add(record, parent, name)
                moved: tuple[tuple[str, str], ...] = ()
            elif action == "rename":
                updated = slot_parts.rename(record, parent, name, new_name)
                # The settings are moved **before** the session takes the new
                # record: `rename_slot_part` finds who is on the part by reading
                # the part each module names, and every one of them still names the
                # old one. Writing the session first would leave it looking for a
                # part nobody is on.
                moved = live_modules.rename_slot_part(
                    self.session, parent=parent, was=name, name=new_name
                )
                # And the *bindings* that named the old key, before the record
                # stops carrying it: a part's device is bound in a room (or by the
                # house), so a rename that moved only the record would leave the
                # next rebuild refusing the whole house for a binding the
                # vocabulary no longer defines. No rebuild of its own -- the
                # `set_slot_parts` below is the one rebuild, against rooms, parts
                # and settings that already agree.
                rebound = self.session.rebind_slot_part(parent, name, new_name)
            elif action == "remove":
                users = live_modules.modules_on_part(
                    self.session, parent=parent, part=name
                )
                if users:
                    named = ", ".join(repr(module) for module in users)
                    raise LiveSessionError(
                        f"the part {name!r} of {parent!r} is used by {named}, so it "
                        "cannot be taken away: those modules act on the device this "
                        "part is bound to, and removing it would move them all "
                        "without saying so. Put them on another part, or on the "
                        "slot's own device, first"
                    )
                updated = slot_parts.remove(record, parent, name)
                moved = ()
            else:
                raise LiveSessionError(
                    f"{action!r} is not something that can be done to a slot's "
                    "parts; a part can be added, renamed or taken away"
                )
        except slot_parts.PartError as refusal:
            raise LiveSessionError(str(refusal)) from refusal

        self.session.set_slot_parts(updated)
        # Each room that lost its binding to the rename keeps the *same device*
        # under the new name, so its subentry has to be written too -- a session
        # that agreed with the next restart and not with itself would lose the
        # device the moment Home Assistant reloaded.
        for placed in rebound:
            room = self.rooms.get(placed)
            if room is not None:
                await self._async_write_room(room, self.session.require_room(placed))
        await self.async_save()
        for pack, placed in moved:
            await self.async_reapply_module(pack, placed)
        return self.session.slot_parts

    async def async_reapply_module(self, module: str, room_id: str) -> None:
        """Build one module again from where its slots now point.

        What a write to a module's own slot has to be followed by, and the reason
        it is a method here rather than a line at each caller: the answer is
        *which device the automation was built with*, and only this object knows
        how a module is built again (`bound_slots_for`).
        """
        from . import modules as module_hosting

        await module_hosting.async_rebuild(
            self.hass,
            self.entry.entry_id,
            module,
            bound=self.bound_slots_for(module, room_id),
        )

    def known_slots(self) -> tuple[str, ...]:
        """Every slot name this house carries, sorted: what an import may name.

        The catalog's words and whatever the installed packs declare of their own
        (`LiveSession.bindable_slots`), which is exactly the set the bind gate
        accepts -- so a name the panel offers is a name the room can be given a
        device for, and a name it does not offer is one this house has no slot
        called.

        Read through the session rather than the frozen vocabulary, for the
        reason that method gives: a pack may bring a word the catalog does not
        have, and importing a blueprint that needs the pack's slot is the whole
        point of naming one at import.
        """
        return tuple(sorted(self.session.bindable_slots()))

    async def async_set_binding(
        self, room_id: str, slot: str, entity_id: str | None
    ) -> RoomRef | None:
        """Bind or unbind a slot, in the session and in the subentry both.

        The session edit is what the caller's reply is built from, so it happens
        first and its refusal -- an unknown room, an unknown slot -- is what stops
        the subentry being written. A failure after the session edit but before
        the subentry write would leave the running house and the saved one
        disagreeing until the next reload, which is why the session's own
        validation is the gate rather than a second check here.

        `room_id` may be `HOUSE`, whose slots have no subentry to write: the
        binding lives in the session's `house_bindings` and is saved with the rest
        of the session state that Home Assistant has no home for (`async_save`),
        which is what makes a global slot survive a restart. `None` is the answer
        for it, because no room changed.
        """
        if room_id == HOUSE:
            if entity_id is not None:
                self.session.bind(room_id, slot, entity_id)
            else:
                self.session.unbind(room_id, slot)
            await self.async_save()
            await self._async_rebuild_modules(room_id, slot)
            return None
        room = self.require_room(room_id)
        updated = (
            self.session.bind(room_id, slot, entity_id)
            if entity_id is not None
            else self.session.unbind(room_id, slot)
        )
        if updated is None:  # pragma: no cover - the HOUSE branch above returned
            raise LiveSessionError(f"binding {slot!r} in {room_id!r} changed no room")
        await self._async_write_room(room, updated)
        await self._async_rebuild_modules(room_id, slot)
        return await self._async_refresh_room(room_id)

    async def _async_rebuild_modules(self, room_id: str, slot: str) -> None:
        """Build again every hosted module in this room that reaches `slot`.

        A module whose input was answered with a slot has its automation built
        from the device the room binds, so binding that device is a change the
        automation has to be told about -- and so is unbinding it, which is what
        takes a module's automation back out again and leaves it waiting.

        Only the modules that reach *this* slot are rebuilt, and only the ones
        that belong to this room (`or the whole house`, for a module that sits at
        house scope and resolves against the house's bindings). A room may hold a
        dozen modules and a person may bind a dozen slots in it; rebuilding all
        of them on every one would make a rebinding cost a dozen automation
        reloads to change one device.

        Each module is built from *its own* view (`bound_slots_for`), not the
        room's, because a module may have pointed this slot at a device of its
        own -- by picking one, or by putting logic on the row. The room's map is
        the answer for the modules that have not, and `bound_slots_for` returns it
        unchanged for those, so this is one code path for both.
        """
        from . import modules as module_hosting

        for record in await module_hosting.async_records(self.hass):
            if record.room_id != room_id:
                continue
            reached = module_host.slots_reached(
                {
                    name: module_host.InputBinding(**row)
                    for name, row in record.bindings.items()
                }
            )
            if slot not in reached:
                continue
            await module_hosting.async_rebuild(
                self.hass,
                self.entry.entry_id,
                record.slug,
                bound=self.bound_slots_for(record.slug, room_id),
            )

    async def async_add_room(
        self, *, area_id: str, room_type: str, bindings: Mapping[str, str]
    ) -> RoomRef:
        """Add a room for an area, as a new subentry.

        The room type is *not* guessed here. The setup flow guesses, and its guess
        is `ha_adapter/setup_flow.plan_setup`; a caller that has not run it passes
        the type it wants. This is the one place a room comes into being outside
        the flow, and it deliberately does no more than write what it was given.
        """
        areas = ar.async_get(self.hass)
        area = areas.async_get_area(area_id)
        if area is None:
            raise KeyError(area_id)
        if any(room.area_id == area_id for room in self.rooms.values()):
            raise ValueError(f"the area {area_id!r} is already a room")

        subentry = ConfigSubentry(
            data={
                DATA_AREA_ID: area_id,
                DATA_ROOM_TYPE: room_type,
                DATA_BINDINGS: dict(bindings),
            },
            subentry_type=SUBENTRY_ROOM,
            title=area.name,
            unique_id=area_id,
        )
        if not self.hass.config_entries.async_add_subentry(self.entry, subentry):
            raise ValueError(f"Home Assistant refused a room for {area_id!r}")

        # The entry's own subentry map is not updated until the reload lands, so
        # the room is added to the session by hand: the caller gets a correct
        # answer now, and the reload rebuilds the same room from the subentry.
        engine_room = LiveRoom(
            id=room_id(area_id),
            name=area.name,
            type=room_type or _FALLBACK_ROOM_TYPE,
            bindings=dict(bindings),
        )
        self.session.set_rooms((*self.session.rooms, engine_room))
        # `self.rooms` was built once, at setup, out of the entry's subentries,
        # and the subentry added just above is not in it yet -- the entry's own
        # map is only rebuilt when the reload lands. So the room is recorded here
        # by hand for the same reason it is added to the session by hand: the
        # answer this call returns is read out of it. Without this line the
        # subentry is written and the room is then reported as missing, so
        # "Add room" tells the person it failed and the room appears anyway on
        # the next reload.
        self.rooms[engine_room.id] = RoomRef(
            subentry_id=subentry.subentry_id,
            area_id=area_id,
            id=engine_room.id,
            name=area.name,
            type=engine_room.type,
            bindings=dict(bindings),
        )
        return await self._async_refresh_room(engine_room.id)

    async def async_remove_room(self, room_id: str) -> None:
        """Delete a room's subentry and drop it from the session."""
        room = self.require_room(room_id)
        if len(self.session.rooms) <= 1:
            raise ValueError("a house needs at least one room")
        if not self.hass.config_entries.async_remove_subentry(
            self.entry, room.subentry_id
        ):
            raise ValueError(f"Home Assistant refused to remove {room.area_id!r}")
        self.session.set_rooms(
            tuple(other for other in self.session.rooms if other.id != room_id)
        )
        self.rooms.pop(room_id, None)

    async def async_rename_room(self, room_id: str, name: str) -> RoomRef:
        """Rename a room's *subentry title*.

        Deliberately not a rename of the Home Assistant area: the area is the
        source of truth and renaming it is Home Assistant's own screen
        (`config_flow` re-reads the area's name at every setup). This sets the
        panel's title for a room whose area a person has not renamed, and the
        next reload will read the area's name over it -- which is the correct
        outcome, because the area is what a room is.
        """
        # Called for its refusal rather than its answer: an unknown room is a
        # `KeyError` here, not a subentry update against an id no room has.
        self.require_room(room_id)
        subentry = self.subentry_for(room_id)
        if subentry is None:
            raise KeyError(room_id)
        self.hass.config_entries.async_update_subentry(self.entry, subentry, title=name)
        return await self._async_refresh_room(room_id)

    # -- Session state ------------------------------------------------------

    async def async_save(self) -> None:
        """Write the state that has no Home Assistant home to the store.

        Rooms are absent on purpose (see the module docstring). The engine's
        runtime state is absent too: what a light was doing is not what a house
        *is*, and restoring it would describe a house that no longer exists.

        `module_rooms` is here because it is the one thing about a placement that
        Home Assistant does not already hold: the installed set says the pack is
        in the house and the room says which slots it binds, but neither says
        *which room the person put it in* -- and for a pack whose slots are
        house-scope (`light_group`, `lock`) that is the only room there is to
        report. Dropped on restart, the module would come back with no room,
        `_enabled` would answer `False` for the empty id, and a module sitting
        visibly in a room could not be switched on.

        `room_settings` is here for the same reason at room scale: a module
        switched on in the Kitchen, a pack's option tuned there, are values in
        the resolver's ROOM layer, and a room is a config *subentry* that stores
        its bindings and nothing else. Until this key existed, nothing persisted
        them at all -- so both a restart and a profile activation (which rebuilds)
        quietly reverted every one.

        **A house on a taken profile tells it what the house is now.** Every
        deliberate write in this integration ends here -- a binding, a setting, a
        module answer, a pack installed -- and a house that is on a profile which
        was *taken* from it has just stopped agreeing with it. Learning the edit
        is what makes such a profile a house to live on rather than a photograph
        of one to be dragged back to, and it is done from here rather than at
        each caller because a write path that had to remember to do it would be a
        write path free to forget. `live_profiles.remember` is the rule, and what
        is *not* learned (a house on nothing, a hand-written profile, the mode
        the house is on) is stated there.

        A restore is the one save that is not an edit: putting a profile back
        writes the house through these same doors, and a profile learning a
        half-restored house would be a profile destroyed by the act of using it.
        """
        if not self._restoring:
            await self._async_learn_profile()
        await self._store.async_save(
            {
                "installed": self.session.installed.to_document(),
                "profiles": self.session.profiles.to_document(),
                "house_settings": dict(self.session.house_settings),
                "house_bindings": dict(self.session.house_bindings),
                "slot_parts": {
                    parent: list(names)
                    for parent, names in self.session.slot_parts.items()
                },
                "room_settings": {
                    room_id: dict(values)
                    for room_id, values in self.session.room_settings.items()
                },
                "module_rooms": dict(self.session.module_rooms),
            }
        )

    async def _async_learn_profile(self) -> None:
        """Tell the house profile in force what the house is. See `async_save`.

        The module rows are read here because this is the one object that knows
        where they live: a hosted module is a file (`module_records`) and not
        anything the session holds, so `live_profiles.remember` is handed them
        rather than looking them up itself.

        The question is asked twice on purpose -- here and again inside
        `remember`, which is the rule -- because the answer decides whether a file
        is read at all, and the ordinary house (on nothing, or on a profile
        somebody wrote) should not pay a disk read every time a dial moves.
        """
        name = self.session.profiles.house_profile
        if name is None or not self.session.profiles.profile(name).snapshot:
            return
        from . import modules as module_hosting

        records = await module_hosting.async_records(self.hass)
        live_profiles.remember(
            self.session, modules=[record.as_json() for record in records]
        )

    async def async_restore_setup(self, profile: Profile) -> None:
        """Put a taken profile's house back: its rooms, its modules and its stores.

        The half of a restore the session cannot do, in the three places a house
        is kept.

        **Rooms are the entry's.** A room's own slot bindings are a config
        subentry rather than anything in the store (see the module docstring), so
        putting them back is `async_set_binding` per slot -- which writes the
        subentry *and* the session, exactly as a person binding one by hand
        does. A room the house no longer has is skipped, and so is a room the
        snapshot does not name: a snapshot is a statement about the house it was
        taken from.

        **Modules are a file.** A hosted module is a record with its bindings,
        its settings, its conditions, its flows and its named configurations, and
        the snapshot carries those records as they were. A module the house hosts
        and the snapshot does not is unhosted -- its automation stopped, its
        record dropped and its output entities taken away, which is what
        `async_unhost` means -- and every module the snapshot does carry is built
        again, because the automation a house is *running* is not something the
        snapshot can put back on its own.

        **Everything else is the store**, and `async_save` has just written it
        from the session the caller restored.

        Called after `live_profiles.activate_house`, which is what has already
        put the session's own half right. Nothing here decides anything: each
        step is the same door a person's own edit of that part goes through.

        Which is exactly why `_restoring` is set for the duration: those doors
        end in `async_save`, and `async_save` is where a taken profile learns
        what the house is. A snapshot must not learn the house on its way back in
        -- it would be reading a house that is halfway to being itself, and the
        setting it would record is one the restore is about to overwrite.
        """

        self._restoring = True
        try:
            await self._async_restore_rooms(profile)
            await self._async_restore_modules(profile)
            await self.async_save()
        finally:
            self._restoring = False

    async def _async_restore_rooms(self, profile: Profile) -> None:
        """Bind each room's slots as the snapshot had them, in the subentry too."""
        for row in _rows(profile.setup.get("rooms")):
            room_id = str(row.get("id", ""))
            if not room_id or self.room(room_id) is None:
                continue
            wanted = {
                str(slot): str(entity)
                for slot, entity in _objects(row.get("bindings")).items()
            }
            current = self.bound_slots(room_id)
            for slot, entity in wanted.items():
                if current.get(slot) != entity:
                    await self.async_set_binding(room_id, slot, entity)
            for slot in set(current) - set(wanted):
                await self.async_set_binding(room_id, slot, None)

    async def _async_restore_modules(self, profile: Profile) -> None:
        """Write the snapshot's hosted modules, and rebuild what a module is.

        The order is the three things a module is, and it is not the order they
        are written in: a module the restore drops is unhosted *before* the file
        is replaced, because unhosting reads the record it is dropping from that
        file; the file is then written whole, because a half-written module list
        is one a person's outputs would be missing from; and each module is built
        again last, over a file that already holds the right answers.
        """
        from . import modules as module_hosting

        rows = profile.setup.get("modules")
        if not isinstance(rows, list):
            return
        records = module_records.from_documents(
            rows, path=module_hosting.records_path(self.hass)
        )
        kept = {record.slug for record in records}
        for held in await module_hosting.async_records(self.hass):
            if held.slug not in kept:
                await module_hosting.async_unhost(
                    self.hass, self.entry.entry_id, held.slug
                )
        await module_hosting.async_write_records(self.hass, records)
        for record in records:
            await module_hosting.async_rebuild(
                self.hass,
                self.entry.entry_id,
                record.slug,
                bound=self.bound_slots(record.room_id),
            )

    def reload_session(self) -> None:
        """Rebuild the session's engine after a pack or profile change."""
        self.session.rebuild()

    # -- Activity -----------------------------------------------------------

    def subscribe(
        self, listener: Callable[[Mapping[str, object]], None]
    ) -> Callable[[], None]:
        """Add a listener for new decision records; returns the unsubscribe."""
        self._activity_listeners.append(listener)

        def _remove() -> None:
            if listener in self._activity_listeners:
                self._activity_listeners.remove(listener)

        return _remove

    @callback
    def async_publish_activity(self) -> None:
        """Hand every subscriber the records appended since the last publish.

        The engine's log has no listener of its own (`engine/decision_log.py`:
        `append` writes and notifies nobody), so the *tick* is what publishes.
        That is a deliberate pairing rather than a gap: a record only exists
        because a tick produced it, so a publish that ran on any other schedule
        would be guessing at when the log had grown.
        """
        if not self._activity_listeners:
            self._last_published = self._newest()
            return
        records = self.session.engine.log.records()
        fresh = self._records_since(records)
        for record in fresh:
            event = {"kind": "entry", "entry": _entry_document(record)}
            for listener in list(self._activity_listeners):
                listener(event)
        if fresh:
            self._last_published = records[-1]
        else:
            self._last_published = self._newest()

    # -- Internals ----------------------------------------------------------

    def _newest(self) -> object | None:
        records = self.session.engine.log.records()
        return records[-1] if records else None

    def _records_since(self, records: Sequence[object]) -> tuple[object, ...]:
        """The records after the last published one, oldest first.

        A bounded log drops its oldest records, so the marker can fall out of the
        window. When it has, everything retained is new to the subscriber: the
        alternative -- emitting nothing -- would silently swallow a burst longer
        than the log's bound.
        """
        if self._last_published is None:
            return ()
        for index, record in enumerate(records):
            if record == self._last_published:
                return tuple(records[index + 1 :])
        return tuple(records)

    async def _async_write_room(self, room: RoomRef, updated: LiveRoom) -> None:
        """Write a room's bindings back into its subentry."""
        subentry = self.subentry_for(room.id)
        if subentry is None:
            return
        self.hass.config_entries.async_update_subentry(
            self.entry,
            subentry,
            data={
                **dict(subentry.data),
                DATA_BINDINGS: dict(updated.bindings),
            },
        )
        self.rooms[room.id] = _ref(room, updated)

    async def _async_refresh_room(self, room_id: str) -> RoomRef:
        """The room as it stands now, after any edit above."""
        found = self.rooms.get(room_id)
        if found is None:
            raise KeyError(room_id)
        return found


def _ref(previous: RoomRef, room: LiveRoom) -> RoomRef:
    """A `RoomRef` for `room`, keeping the subentry it came from."""
    return RoomRef(
        subentry_id=previous.subentry_id,
        area_id=previous.area_id,
        id=room.id,
        name=room.name,
        type=room.type,
        bindings=dict(room.bindings),
    )


def _entry_document(record: object) -> Mapping[str, object]:
    """One decision record as the Activity tab's row.

    Imported from `ha_adapter.live_export` at call time rather than at module
    scope: that module owns the eight-outcomes-to-five mapping and this one only
    needs to hand its answer to a subscriber, so the import is deferred to keep
    the dependency pointing one way.
    """
    from ha_adapter.live_export import activity_entry

    return activity_entry(record)


async def async_setup_host(
    hass: HomeAssistant, entry: ConfigEntry, runtime: OpenHouseRuntime
) -> OpenHouseHost | None:
    """Build the host for an entry: its session, its rooms, and its store.

    `None` when there is no catalog to read the vocabulary from, matching
    `async_setup_automation`: an instance with entities but no automation is
    still one a person can look at, and refusing the whole entry would take the
    panel away too -- which is the screen that would explain what is missing.
    """
    root = catalog_root()
    if root is None or not runtime.rooms:
        return None

    store: Store[dict[str, Any]] = Store(
        hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}"
    )
    stored = await store.async_load() or {}

    # Building the session imports the engine and `jsonschema`, whose import
    # reads its metaschema files from disk, and composes a house from the
    # catalog -- both blocking, and both reported by Home Assistant when they
    # happen on the event loop. It is one step in an executor rather than
    # several, because the whole build is the same kind of work: nothing in it
    # touches Home Assistant's state machine, only the vocabulary on disk.
    #
    # The whole build, and not just `LiveSession.build`: `_profiles` reads
    # `schemas/profile/` and the arguments to a `partial` are evaluated where the
    # `partial` is *built*, which is here on the loop. Passing the stored document
    # through and reading the schema inside `_session` is what makes the two
    # share one executor job. The catalog is read in that same job (`_load`), so
    # that from the moment the host exists, no command it serves reads a file.
    build = partial(
        _session,
        house_name=entry.title,
        rooms=_live_rooms(runtime),
        modes=MODES,
        transport=HassTransport(hass),
        root=root,
        location=_location(hass),
        stored=stored,
        # Evaluated here, on the loop, and that is fine: `config.path` joins
        # strings and reads nothing. The directory itself is read later, by the
        # readers that are stamped and cached for it.
        user_root=Path(hass.config.path(*PACKS_DIRECTORY)),
    )
    try:
        session, catalog = await hass.async_add_executor_job(_load, root, build)
    except Exception:
        # A session that cannot be composed is reported and skipped rather than
        # failing the entry: the entities and the repairs are already up by the
        # time this runs, and they are what would explain the absence.
        _LOGGER.exception(
            "Open House could not compose a session for entry %s", entry.entry_id
        )
        return None

    host = OpenHouseHost(
        hass, entry, runtime, session, _room_refs(runtime), store, catalog
    )
    return host


def _load(root: Path, build: Callable[[], LiveSession]) -> tuple[LiveSession, Catalog]:
    """The session and the catalog, read in one executor job.

    One job rather than two because they are the same kind of work -- reading the
    checkout's vocabulary from a disk -- and because the catalog is what the
    session was composed from: reading the two at different moments would let the
    panel answer with a vocabulary the engine was not built against, which is the
    one way a room type could be offered that no room can actually be.

    `views` is imported here rather than at module scope because it imports this
    module; importing it inside the job also means the engine-side import cost is
    paid once, off the loop, in the thread that is already paying the rest.

    The module catalog is warmed too, and for the same reason the catalog is read
    here: `views.room_detail` reaches `live_modules.installed_modules`, which
    reads `registry/index.json` and the manifests it names, and a websocket
    command runs on the loop. Its readers cache on the file's stamp, so warming
    them costs one read each here and every later request costs none --
    `live_modules.preload` says why the warm-up cannot be anything but a real
    read.
    """
    from ha_adapter import live_modules

    from . import views

    session = build()
    live_modules.preload(session.root, session.user_root)
    return session, views.load_catalog(root)


def host_for(hass: HomeAssistant, entry_id: str) -> OpenHouseHost | None:
    """The host for one entry, or `None` when it has not been set up."""
    return hass.data.get(DOMAIN, {}).get(DATA_SESSION, {}).get(entry_id)


def single_host(hass: HomeAssistant) -> OpenHouseHost | None:
    """The one host, because this integration has exactly one config entry.

    The flow aborts a second entry (`config_flow.async_step_confirm_areas`), so
    "the" house is well defined, and a websocket command that named an entry
    would be asking the panel to know something it has no way to learn.
    """
    hosts: Mapping[str, OpenHouseHost] = hass.data.get(DOMAIN, {}).get(DATA_SESSION, {})
    if not hosts:
        return None
    return next(iter(hosts.values()))


def _session(
    *,
    house_name: str,
    rooms: tuple[LiveRoom, ...],
    modes: tuple[str, ...],
    transport: HassTransport,
    root: Path,
    location: object,
    stored: Mapping[str, object],
    user_root: Path | None = None,
) -> LiveSession:
    """Build a session, and read everything it needs from disk while doing it.

    A module-level function rather than a `partial` over `LiveSession.build`,
    because two of the arguments -- the installed set and the profile set -- are
    *parsed documents* rather than values: `_profiles` reads
    `schemas/profile/` and `InstalledSet.from_document` reads nothing but is the
    same kind of work. Building them at the call site would evaluate them on the
    event loop before the executor ever saw the job, which is the blocking call
    Home Assistant warns about, reported with a traceback that names the wrong
    line.
    """
    return LiveSession.build(
        house_name=house_name,
        rooms=rooms,
        modes=modes,
        transport=transport,
        root=root,
        location=location,
        house_settings=_mapping(stored.get("house_settings")),
        # Absent from a store written before the field existed, and the empty
        # mapping is the honest reading there: a house that has not bound a
        # global slot resolves each role from rooms, exactly as it did then.
        house_bindings=_strings(stored.get("house_bindings")),
        # Absent from a store written before a slot could be split, and the empty
        # record is the honest reading there: no slot has parts, every role
        # resolves exactly as it did, and `slot_parts.record_from` is the reader
        # that drops a row this version cannot use rather than refusing the house.
        slot_parts=slot_parts.record_from(stored.get("slot_parts")),
        # Absent from a store written before the field existed, and the empty
        # mapping is the honest reading there: the rooms are untuned and the
        # built-in defaults stand in, which is the house that was running then.
        room_settings=_room_settings(stored.get("room_settings")),
        installed=InstalledSet.from_document(_mapping(stored.get("installed"))),
        profiles=_profiles(stored.get("profiles"), root),
        # Absent in a document written before the field existed, and the empty
        # mapping is the right answer there: `_module_room` joins the pack's
        # slots for a house that recorded no placement.
        module_rooms=_strings(stored.get("module_rooms")),
        # Where a person's own packs live. The checkout is read-only in every
        # real deployment, so a module authored in the Dev tab is written beside
        # the house -- and the session has to know, because the catalog it
        # offers and the catalog it installs from are the same catalog.
        user_root=user_root,
    )


def _live_rooms(runtime: OpenHouseRuntime) -> tuple[LiveRoom, ...]:
    """The rooms a runtime holds, projected to what the engine reads."""
    return tuple(
        LiveRoom(
            id=room_id(room.area_id),
            name=room.name or room.area_id,
            type=room.room_type or _FALLBACK_ROOM_TYPE,
            bindings=dict(room.bindings),
            auto_lighting=room.auto_lighting,
            mode=room.mode,
        )
        for room in runtime.rooms.values()
    )


def _room_refs(runtime: OpenHouseRuntime) -> dict[str, RoomRef]:
    """Every room, keyed by the engine id the panel will use."""
    refs: dict[str, RoomRef] = {}
    for subentry_id, room in runtime.rooms.items():
        engine_id = room_id(room.area_id)
        refs[engine_id] = RoomRef(
            subentry_id=subentry_id,
            area_id=room.area_id,
            id=engine_id,
            name=room.name or room.area_id,
            type=room.room_type or _FALLBACK_ROOM_TYPE,
            bindings=dict(room.bindings),
        )
    return refs


def _profiles(stored: object, root: Any) -> ProfileSet:
    """The stored profile set, or an empty one over the schema."""
    schema = load_profile_schema(root)
    if isinstance(stored, Mapping):
        try:
            return ProfileSet.from_document(stored, schema=schema)
        except Exception:
            return ProfileSet([], schema=schema)
    return ProfileSet([], schema=schema)


def entity_ids_in_area(hass: HomeAssistant, area_id: str) -> tuple[str, ...]:
    """Every entity Home Assistant files under an area, sorted for determinism.

    "Files under" is the entity's *effective* area, not its own. An entity with
    no area of its own lives in its device's area, and a device's area is what a
    person sets when they say which room a thing is in -- which is also how an
    integration's `suggested_area` lands. `entity_registry.async_entries_for_area`
    indexes the entity's own `area_id` and nothing else, so it answers with the
    entities somebody filed one at a time: in a house whose devices are all
    neatly in their rooms, and no entity filed individually, that is nothing at
    all. Every slot in the panel answered "No match" for exactly this reason.

    The setup flow's guess reads this and the picker's candidates use it to order
    the room's own devices first, so the two cannot disagree about which room a
    device is in. It is not a fence around what may be bound: `entity_ids_house`
    is every candidate set's set, and this only says which part of it leads.
    """
    registry = er.async_get(hass)
    return tuple(
        sorted(
            entry.entity_id
            for entry in registry.entities.values()
            if er.async_get_effective_area_id(hass, entry) == area_id
        )
    )


def entity_ids_house(hass: HomeAssistant) -> tuple[str, ...]:
    """Every entity Home Assistant holds, sorted for determinism.

    Every candidate set, and the one place "any area" is read. A room's picker
    leads with that room's own devices (`entity_ids_in_area`) and offers the rest
    of this behind them, because a room's picker is a starting point rather than
    a fence -- the binding belongs to the room either way, and the devices a
    person put in a room are not the same set as the devices Home Assistant filed
    under its area. A global slot belongs to no room at all, so this is its whole
    list. Reading the state machine rather than the entity registry is deliberate:
    a slot is proposed from what the house can actually actuate, and an entity
    with no registry entry (a template, a group) is one it can actuate all the
    same.
    """
    return tuple(sorted(state.entity_id for state in hass.states.async_all()))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _objects(value: object) -> Mapping[str, object]:
    """The object at `value`, or the empty one when it is anything else.

    A snapshot's sub-object -- a room's bindings, a module's answers -- is read
    with this rather than validated: a snapshot comes out of a document that
    already migrated and validated, so a row that is not an object is a
    contradiction rather than a thing to report, and a restore that refused the
    whole house over one would be one a person could not get out of.
    """
    return value if isinstance(value, Mapping) else {}


def _rows(value: object) -> tuple[Mapping[str, object], ...]:
    """The list at `value`, keeping only the rows that are objects. See `_objects`."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        cast("Mapping[str, object]", row) for row in value if isinstance(row, Mapping)
    )


def _strings(value: object) -> Mapping[str, str]:
    """A stored mapping whose keys and values are both strings, or a refusal.

    `module_rooms` is pack names to room ids and nothing else, so a document
    holding anything else is not one this build can read -- and it is refused
    rather than filtered, because a dropped entry is a module coming back with
    no room and an Enable button that does nothing, which is exactly the failure
    the field exists to prevent and it would arrive quietly.

    Absent is the empty mapping. A house stored before the field existed has no
    placement recorded and is still a valid house: `_module_room` joins the
    pack's slots for it.
    """
    entries = _mapping(value)
    if not all(
        isinstance(name, str) and isinstance(room, str)
        for name, room in entries.items()
    ):
        raise ValueError("'module_rooms' must map pack names to room ids")
    return cast("Mapping[str, str]", entries)


def _room_settings(value: object) -> Mapping[str, Mapping[str, object]]:
    """A stored mapping of room ids to each room's settings, or a refusal.

    The same reading `ha_adapter.live._room_settings` makes when a session is
    rebuilt from its own document, so a store written by `async_save` and a store
    written by `LiveSession.to_state` are read identically -- this is the path a
    restart takes, and the two must not disagree about what a well-formed
    document is.

    A room whose entry is not an object is refused rather than dropped, for the
    reason `_strings` gives: a dropped entry is a room somebody tuned coming back
    untuned, arriving quietly. Absent is the empty mapping and a valid house --
    one stored before the field existed simply has no room settings recorded.

    The values are copied into plain dicts, so what the session holds cannot be
    changed by a later write to whatever document this read.
    """
    entries = _mapping(value)
    settings: dict[str, Mapping[str, object]] = {}
    for held, values in entries.items():
        if not isinstance(values, Mapping):
            raise ValueError("'room_settings' must map room ids to objects")
        settings[str(held)] = dict(values)
    return settings


def _location(hass: HomeAssistant) -> Any:
    """The house's place, from the instance's own core configuration."""
    from engine.solar import Location

    return Location(
        latitude=hass.config.latitude,
        longitude=hass.config.longitude,
        time_zone=hass.config.time_zone,
    )
