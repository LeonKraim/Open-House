"""Every Home Assistant area is an Open House room, now and as they appear.

**Areas are the source of truth, so this module is the door they come through.**
The setup flow asks a person which areas become rooms *once*, and an area made
afterwards -- a new office, a hallway added while the lights are being wired --
had no door at all: it existed in Home Assistant and nowhere in Open House, and
the only way in was to re-run a flow that is written to run once. This closes
that: on setup, and on every change to the area registry, an area the entry does
not have a room for is added as one.

**A room is filled in the same way the flow fills one.** The room type and the
slot bindings come from `ha_adapter.setup_flow.plan_setup`, the same pure
function the first-run flow reads its plan from, so an imported room is guessed
exactly as a confirmed one was and the guess is the one the person would have
seen. Nothing here invents a type or a binding: the engine's vocabulary is read
from the catalog and a checkout where the catalog is missing imports nothing
rather than importing rooms the engine cannot describe.

**Removal is deliberately not mirrored.** Deleting an area in Home Assistant
leaves its room alone. A room holds a person's bindings, its settings and the
modules installed into it, and deleting all of that because an area was tidied
away in another program is an edit nobody asked this module to make -- the room
keeps working, its entities stay, and the person who wants it gone removes it
where they can see what they are removing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers.area_registry import (
    EVENT_AREA_REGISTRY_UPDATED,
    EventAreaRegistryUpdatedData,
)

from ha_adapter.setup_flow import (
    Area,
    load_module_slots,
    load_room_types,
    load_slot_domains,
    plan_setup,
)

from .const import SUBENTRY_ROOM, catalog_root
from .host import entity_ids_in_area

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Vocabulary:
    """The catalog facts an imported room is guessed from, read off disk.

    Read as one value by one executor job rather than three reads inside the
    import loop: a manifest, a slot list and a room-type table that were read at
    three instants could disagree with each other, and Home Assistant is right to
    refuse the disk on its event loop.
    """

    root: Path
    room_types: Mapping[str, tuple[str, ...]]
    slot_domains: Mapping[str, tuple[str, ...]]
    module_slots: tuple[str, ...]


def _read_vocabulary() -> _Vocabulary | None:
    """The catalog a room is guessed from, or `None` when it is not there.

    An executor job (`hass.async_add_executor_job`): `catalog_root` stats its way
    up the tree looking for the checkout and the three loaders open files under
    it, which is the disk, and the import runs on the event loop.
    """
    root = catalog_root()
    if root is None:
        return None
    return _Vocabulary(
        root=root,
        room_types=load_room_types(root),
        slot_domains=load_slot_domains(root),
        module_slots=load_module_slots(root),
    )


def _read_area(hass: HomeAssistant, area: ar.AreaEntry) -> Area:
    """One registry area as the planner's own `Area`.

    The same construction `config_flow._read_areas` makes, and the entity list is
    the *effective* area's (`host.entity_ids_in_area`), so a device assigned to
    the area brings its entities with it and the binding guess reads exactly the
    candidates the first-run flow would have read. The floor's name is carried
    because the planner's type guess reads it: an area called "Loft" on a floor
    called "Upstairs" is guessed from both words, and dropping the floor here
    would make an imported room's type depend on which door it came through.
    """
    floor = fr.async_get(hass).async_get_floor(area.floor_id) if area.floor_id else None
    return Area(
        area_id=area.id,
        name=area.name,
        entity_ids=entity_ids_in_area(hass, area.id),
        floor_name=floor.name if floor is not None else None,
    )


def _room_document(area: Area, vocabulary: _Vocabulary) -> dict[str, object]:
    """The subentry data for one area, guessed from the vocabulary.

    The shape is `SetupPlan.to_document`'s room exactly, because a room a person
    confirmed and a room this imported have to be the same kind of thing: the
    engine reads one shape, and a second writer that spelled it differently would
    be a room the reload could not read. Pure: the vocabulary was read off the
    loop by `_read_vocabulary`, and `plan_setup` is the planner the flow uses.
    """
    plan = plan_setup(
        areas=(area,),
        people=(),
        room_types=vocabulary.room_types,
        slot_domains=vocabulary.slot_domains,
        slots=vocabulary.module_slots,
    )
    room = plan.to_document()["rooms"][0]
    assert isinstance(room, dict)
    return room


def _existing_rooms(entry: ConfigEntry) -> dict[str, ConfigSubentry]:
    """The entry's room subentries, keyed by the area id each one names."""
    return {
        str(subentry.unique_id): subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_ROOM
    }


@callback
def _import_areas(
    hass: HomeAssistant, entry: ConfigEntry, vocabulary: _Vocabulary
) -> int:
    """Add a room for every area the entry has none for, and answer how many.

    Names are kept in step too: an area renamed in Home Assistant renames its
    room, which is the same edit a person made, one program over. The write goes
    through `hass.config_entries`, so the entry's update listeners fire and the
    house is rebuilt from the rooms as they now stand -- the same reload a
    subentry edited by hand would schedule.

    Nothing is added when there is nothing to add, which is what keeps this from
    reloading the entry on every area event.

    The catalog arrives as an argument because reading it is the disk and this
    runs on the event loop; the caller read it in an executor job. Everything
    else here is the registries and the config entry, which are in memory.
    """
    existing = _existing_rooms(entry)
    added = 0
    for registered in ar.async_get(hass).async_list_areas():
        subentry = existing.get(registered.id)
        if subentry is not None:
            if subentry.title != registered.name:
                hass.config_entries.async_update_subentry(
                    entry, subentry, title=registered.name
                )
            continue
        document = _room_document(_read_area(hass, registered), vocabulary)
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=document,
                subentry_type=SUBENTRY_ROOM,
                title=registered.name,
                unique_id=registered.id,
            ),
        )
        added += 1
        _LOGGER.info(
            "Open House imported the area %r as a %s room",
            registered.name,
            document.get("room_type"),
        )
    return added


async def async_import_areas(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Import the areas the entry is missing, and watch for the next one.

    Called once per setup, after the entry is loaded: an area added while the
    integration was not watching -- before it was set up, or during a reload --
    is found here, and every area added afterwards arrives through the registry
    event.

    The listener answers `create`, `update` and `reorder` alike, because the
    question it asks is not what changed but whether the entry and the registry
    agree, and that question has one answer whichever event raised it. The work
    is deferred to a task rather than done inside the event callback: adding a
    room schedules the entry's reload, and a reload started from inside the
    registry's own dispatch would be re-entering the loop that fired it.

    The catalog is read off the loop by `_read_vocabulary`; an instance that has
    none imports nothing and says so once per pass, because an area that silently
    did not become a room is the failure this module exists to close.
    """
    await _sync(hass, entry)

    @callback
    def _changed(event: Event[EventAreaRegistryUpdatedData]) -> None:
        if event.data.get("action") == "remove":
            # A removal is not mirrored (see the module docstring), and it cannot
            # have left the two disagreeing about a room that should exist.
            return
        hass.async_create_task(_sync(hass, entry))

    entry.async_on_unload(hass.bus.async_listen(EVENT_AREA_REGISTRY_UPDATED, _changed))


async def _sync(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """One pass: read the catalog off the loop, then import what is missing.

    The one path both the setup call and the registry event take, so an area
    that arrives through the event is imported by exactly the code that imported
    the areas already there -- there is no second importer to keep in step.
    """
    vocabulary = await hass.async_add_executor_job(_read_vocabulary)
    if vocabulary is None:
        _LOGGER.warning(
            "Open House found no catalog beside it, so the areas it does not "
            "know are not imported as rooms"
        )
        return
    _import_areas(hass, entry, vocabulary)


__all__ = ["async_import_areas"]
