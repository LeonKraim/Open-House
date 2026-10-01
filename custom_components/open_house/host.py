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
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store

from engine.install import InstalledSet
from engine.profiles import ProfileSet, load_profile_schema
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession

from .const import (
    DATA_AREA_ID,
    DATA_BINDINGS,
    DATA_ROOM_TYPE,
    DOMAIN,
    MODES,
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

    async def async_set_binding(
        self, room_id: str, slot: str, entity_id: str | None
    ) -> RoomRef:
        """Bind or unbind a slot, in the session and in the subentry both.

        The session edit is what the caller's reply is built from, so it happens
        first and its refusal -- an unknown room, an unknown slot -- is what stops
        the subentry being written. A failure after the session edit but before
        the subentry write would leave the running house and the saved one
        disagreeing until the next reload, which is why the session's own
        validation is the gate rather than a second check here.
        """
        room = self.require_room(room_id)
        updated = (
            self.session.bind(room_id, slot, entity_id)
            if entity_id is not None
            else self.session.unbind(room_id, slot)
        )
        await self._async_write_room(room, updated)
        return await self._async_refresh_room(room_id)

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
        """
        await self._store.async_save(
            {
                "installed": self.session.installed.to_document(),
                "profiles": self.session.profiles.to_document(),
                "house_settings": dict(self.session.house_settings),
            }
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
    live_modules.preload(session.root)
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
        installed=InstalledSet.from_document(_mapping(stored.get("installed"))),
        profiles=_profiles(stored.get("profiles"), root),
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


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _location(hass: HomeAssistant) -> Any:
    """The house's place, from the instance's own core configuration."""
    from engine.solar import Location

    return Location(
        latitude=hass.config.latitude,
        longitude=hass.config.longitude,
        time_zone=hass.config.time_zone,
    )
