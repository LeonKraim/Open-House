"""The Open House integration: rooms from areas, and the entities that show them.

This is the Home Assistant half of the project -- the part written in Home
Assistant's own types, mounted into a running instance. It is deliberately thin.
The setup flow's decisions are made in `ha_adapter/setup_flow.py` as pure
functions; the engine's decisions are made in the engine, behind the
`HouseAdapter` port; and what is left here is turning rooms into subentries and
showing each room's four entities.

**Rooms are subentries, and areas are their source of truth.** A room is a Home
Assistant area, added to the config entry as a subentry of type `"room"` whose
`unique_id` is the area id -- so one area cannot be added as a room twice, and a
room always names a real area rather than an invented one. When subentries
change, the entry reloads and the rooms are rebuilt from them.

**Setup does not decide anything on its own.** This module reads what the
subentries already say and builds the runtime the entities read; every judgement
about what a room *is* was made in the flow and stored, and every judgement about
what to *do* is the engine's, reached through the adapter. Nothing here actuates.

What *does* actuate is `automation.py`, and this module is where it is started:
`async_setup_automation` ticks the session `host.py` built from Home Assistant's
own loop, so a room's bound sensors drive its bound lights through the engine
rather than through a second set of rules living in the integration. It is
started after the platforms so a house that cannot compose a session still shows
its entities, and it is stopped on unload.

**The panel is the third face of the same session.** `websocket_api.py` answers
the sidebar panel's commands, and every one of them reads and writes the host --
which is why the composition moved out of this module and into `host.py`: the
engine the panel edits has to be the engine the ticker ticks, and the only way to
be sure of that is for one object to own it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components import panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_track_state_change_event

from . import (
    _bootstrap,  # noqa: F401  # imported for its import-path side effect
    modules,
    slot_rules,
)
from .const import (
    DATA_AREA_ID,
    DATA_BINDINGS,
    DATA_ROOM_TYPE,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_ROOM,
    VERSION,
)
from .repairs import async_sync_startup_issues
from .rooms_sync import async_import_areas
from .runtime import HostedModule, OpenHouseRuntime, RoomRuntime
from .transport import HassTransport

if TYPE_CHECKING:
    from .automation import HomeAutomation
    from .host import OpenHouseHost

__all__ = ["async_setup", "async_setup_entry", "async_unload_entry"]

_LOGGER = logging.getLogger(__name__)

#: The sidebar tab. The URL path is the panel's identity in Home Assistant --
#: registering it twice is an error, and a panel is removed by that path -- so it
#: is named once here rather than spelled at each call site.
PANEL_URL_PATH = "open-house"
PANEL_WEBCOMPONENT = "open-house-panel"
#: Where the built bundle is served from. `panel/dist/open-house-panel.js` is
#: mounted into `/config/www/`, which Home Assistant serves at `/local/`, so the
#: panel is a file the instance already knows how to hand to a browser.
PANEL_MODULE_URL = "/local/open-house-panel.js"
#: The path the same file is served *from*, relative to Home Assistant's config
#: directory -- what `_panel_version` stats to build the cache-busting query.
PANEL_BUNDLE_PATH = Path("www") / "open-house-panel.js"
#: `hass.data` key holding the panel paths this component has registered.
DATA_PANELS = f"{DOMAIN}_panels"

#: The `hass.data` key holding each entry's live automation, keyed by entry id.
#: A dict of its own rather than a field on the runtime because the automation is
#: the runtime's *driver* and not a view of it, and because the runtime is what
#: the entity platforms look up under `hass.data[DOMAIN][entry_id]` -- folding the
#: two into one key would make every platform's lookup depend on whether an engine
#: happened to be built.
DATA_AUTOMATION = f"{DOMAIN}_automation"

#: The slot-rule watchers, keyed by entry, for the same reason the automation is:
#: a watcher is the runtime's *listener* rather than a view of it, and
#: `async_unload_entry` has to find the one this entry started to stop it.
DATA_SLOT_RULES = f"{DOMAIN}_slot_rules"


def _engine_modules() -> tuple[Any, Any] | None:
    """The engine-dependent modules, or `None` when they cannot be imported.

    Deferring these two imports to call time is what keeps the integration
    loadable without its engine. `automation` reaches the engine directly, and
    `host` reaches it through `ha_adapter.composition` and `ha_adapter.live`; the
    engine reads the frozen vocabulary through `tools.catalog.schemas`
    (`engine/vocabulary.py`), and that package is mounted beside the checkout
    rather than installed with Home Assistant -- see `docker/docker-compose.yml`
    -- so an environment without the checkout can import this integration and
    must not be taken down by a `ModuleNotFoundError` before a single entity
    exists.

    It is deliberately not a substitute for mounting `tools/`: an instance whose
    engine cannot be imported shows its rooms and explains itself, but it does
    not automate. `None` moves the `async_setup_host` failure one import earlier,
    so the entry survives exactly as it does when a session cannot be composed.
    """
    try:
        from . import automation, host
    except Exception:
        _LOGGER.exception(
            "Open House could not import its engine; rooms and entities will "
            "load, but nothing will be automated and the panel's live views "
            "will not answer"
        )
        return None
    return automation, host


def _import_websocket_api() -> Any:
    """The command module, or `None` when it could not be imported.

    Blocking, and that is the point: the module reaches the engine through
    `ha_adapter.live_export`, `live_modules` and `live_profiles`, which import
    `jsonschema`, whose own import reads its metaschema files off the disk.
    Home Assistant imports a custom integration's *top-level* module in a thread
    but calls `async_setup` on the event loop, so an import written in
    `async_setup` is imported on the loop -- and Home Assistant's loop detector
    reports each of those reads as a blocking call, correctly. So this is an
    executor job and `async_setup` only registers the result.

    Guarded separately from `_engine_modules` because they are reached from
    different call sites and fail to different degrees: a panel that loads and
    cannot answer is still a screen, whereas an `async_setup` that raised would
    take the whole integration with it.
    """
    try:
        from . import websocket_api
    except Exception:
        _LOGGER.exception(
            "Open House could not import its websocket commands; the panel will "
            "load but its live views will not answer"
        )
        return None
    return websocket_api


def _session_data_key() -> str | None:
    """The `hass.data` key the session is stored under, or `None` without a host.

    Read from `host` rather than restated here, because the key belongs to the
    module that writes it; when the engine could not be imported there is no
    session to forget, so `None` is the honest answer rather than a second
    spelling of the key that could drift from the first.
    """
    try:
        from .host import DATA_SESSION
    except Exception:
        return None
    return DATA_SESSION


async def async_setup(hass: HomeAssistant, config: Mapping[str, Any]) -> bool:
    """Register the sidebar tab, and the commands it speaks.

    The panel is registered from `async_setup` rather than from a config entry,
    because the tab is the way into the product: an instance with no rooms
    configured yet still needs the tab that offers to add them, and a panel that
    only appeared after setup would be missing exactly when it is the only thing
    there is to click. `hass.data` holds the paths already registered so a reload
    does not try to add the same panel twice, which Home Assistant refuses.

    The websocket commands are registered here too, and not per entry, because a
    command is global to the instance: `async_register_websocket_api` refuses a
    second registration, and a command that existed only after an entry loaded
    would be one the panel could not call on the very screen that sets it up.

    The import is an executor job and the registration is not: one is disk work
    and the other is a dictionary write that has to happen on the loop
    (`_import_websocket_api` says why the split is not optional).
    """
    websocket_api = await hass.async_add_executor_job(_import_websocket_api)
    if websocket_api is not None:
        websocket_api.async_register_websocket_api(hass)
    # The one service a hosted module's automation calls, registered here rather
    # than per entry for the same reason the commands are: an automation that
    # publishes an output is Home Assistant's own and knows nothing about config
    # entries, so the service has to exist as long as the instance does.
    from .modules import async_register_services

    async_register_services(hass)
    registered: set[str] = hass.data.setdefault(DATA_PANELS, set())
    if PANEL_URL_PATH not in registered:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_WEBCOMPONENT,
            sidebar_title="Open House",
            sidebar_icon="mdi:home-assistant",
            module_url=f"{PANEL_MODULE_URL}?v={_panel_version(hass)}",
            require_admin=True,
            handle_safe_area=True,
        )
        registered.add(PANEL_URL_PATH)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Build the runtime from the entry's rooms, then start their platforms."""
    transport = HassTransport(hass)
    areas = ar.async_get(hass)
    rooms: dict[str, RoomRuntime] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        room = _build_room(subentry.data, transport)
        area = areas.async_get_area(room.area_id)
        if area is not None:
            room.name = area.name
        rooms[subentry_id] = room
    runtime = OpenHouseRuntime(entry_id=entry.entry_id, rooms=rooms)
    # The modules this house hosts, read before the platforms are forwarded to:
    # the sensor platform builds one entity per declared output, so it needs the
    # records to exist by the time it is set up. A records file that will not
    # parse is logged and stepped over rather than allowed to fail the entry --
    # the rooms, the panel and the engine have nothing to do with it -- and the
    # exception names the file, which is what a person needs to fix it.
    try:
        records = await modules.async_records(hass)
    except Exception:
        _LOGGER.exception(
            "Open House could not read this house's module records, so no hosted "
            "module will load and no output will publish; the file is %s",
            modules.records_path(hass),
        )
        records = ()
    runtime.modules = {record.slug: HostedModule(record=record) for record in records}
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Startup safety: a room that cannot read occupancy must say so once, here,
    # rather than letting its silence read as "clear". The repairs are the same
    # ones a later change will update; this is only the first reconciliation.
    async_sync_startup_issues(hass, entry.entry_id, runtime)

    # Nothing polls. A room's occupancy is re-read from the house only when the
    # house says the sensor moved, so each bound sensor is watched, and both the
    # room's entities and its repairs are reconciled on that one event.
    for room in rooms.values():
        _track_occupancy(hass, entry, runtime, room)

    # The session is what the panel reads and what the engine decides with, and
    # it is built here -- after the platforms, so the entities a person sees exist
    # even when there is no session to compose -- because the engine import and
    # the composition from the catalog are work rather than wiring. The import is
    # an executor job for the reason `_import_websocket_api` gives, and it is
    # usually already done by the time this runs; `_engine_modules` answers `None`
    # when the engine cannot be imported at all, and `async_setup_host` answers
    # `None` when it can be imported but a session cannot be composed. Either way
    # the instance is left with its entities, its repairs and its panel rather
    # than a failed entry.
    # Named `engine_modules` rather than `modules`, which is the imported
    # `modules` module: a local of that name anywhere in this function makes
    # every earlier use of the import an `UnboundLocalError`, and the failure
    # arrives as "cannot access local variable 'modules'" pointing at the read
    # of the records file rather than at the line that shadowed it.
    engine_modules = await hass.async_add_executor_job(_engine_modules)
    if engine_modules is not None:
        await _async_start_engine(hass, entry, runtime, *engine_modules)

    # Areas are the source of truth for rooms, so the entry is brought into step
    # with Home Assistant's area registry here -- an area made while the
    # integration was not watching is imported now, and every area made afterwards
    # arrives through the registry event this registers. Last, because an import
    # adds subentries and a subentry change reloads the entry: doing it before the
    # platforms were up would reload a house that had never finished loading.
    await async_import_areas(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_start_engine(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: OpenHouseRuntime,
    automation: Any,
    host: Any,
) -> None:
    """Build the entry's session and start ticking it, or report and move on.

    Split out so the entry keeps its shape when the engine is unavailable: this
    is the only block that touches it, and a failure here must leave the entities
    and the repairs -- already up by the time it runs -- in place. The two module
    objects are the ones `_engine_modules` returned.
    """
    try:
        session_host: OpenHouseHost | None = await host.async_setup_host(
            hass, entry, runtime
        )
        if session_host is not None:
            hass.data.setdefault(DOMAIN, {}).setdefault(host.DATA_SESSION, {})[
                entry.entry_id
            ] = session_host

        # The engine decides for the rooms the flow configured, and this is what
        # makes the house act rather than only show: `automation.py` ticks the
        # session's engine from Home Assistant's loop and publishes what it
        # decided. It is given the same host the panel writes through, so an edit
        # a person makes in the panel is an edit the very next tick decides with.
        started: HomeAutomation | None = await automation.async_setup_automation(
            hass, entry, runtime, session_host
        )
        if started is not None:
            hass.data.setdefault(DATA_AUTOMATION, {})[entry.entry_id] = started
            entry.async_on_unload(started.async_stop)

        # A slot may be decided by *logic* rather than by a device a person
        # picked (`ha_adapter.slot_rules`), and a slot is a standing fact rather
        # than a run -- so something has to be listening for the world moving.
        # This is the only listener of its own in the integration, and it is
        # started beside the automation for that reason: both are "make the house
        # act", they are stopped together, and the watcher needs the host to write
        # the setting an automation is then built from.
        if session_host is not None:
            watcher = await slot_rules.async_setup_slot_rules(hass, entry, session_host)
            hass.data.setdefault(DATA_SLOT_RULES, {})[entry.entry_id] = watcher
            entry.async_on_unload(watcher.async_stop)
    except Exception:
        _LOGGER.exception(
            "Open House could not start its engine for entry %s; the rooms and "
            "their entities are unaffected",
            entry.entry_id,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the platforms and forget the rooms this entry had built."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        session_key = _session_data_key()
        if session_key is not None:
            sessions = hass.data.get(DOMAIN, {}).get(session_key)
            if isinstance(sessions, dict):
                sessions.pop(entry.entry_id, None)
        automation = hass.data.get(DATA_AUTOMATION, {}).pop(entry.entry_id, None)
        if automation is not None:
            automation.async_stop()
        watcher = hass.data.get(DATA_SLOT_RULES, {}).pop(entry.entry_id, None)
        if watcher is not None:
            watcher.async_stop()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the entry or one of its room subentries changes."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _track_occupancy(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: OpenHouseRuntime,
    room: RoomRuntime,
) -> None:
    """Redraw a room and re-judge its repairs when its sensor reports.

    The entities do not poll, so this is what makes a room's occupancy entity
    follow its sensor. The repair is reconciled on the same event because the
    two answers must never disagree: an entity reading unknown while no repair
    says why is the failure this whole path exists to prevent.
    """
    entity_id = room.occupancy_entity_id
    if entity_id is None:
        return

    @callback
    def _sensor_changed(event: Event) -> None:
        room.notify()
        async_sync_startup_issues(hass, runtime.entry_id, runtime)

    entry.async_on_unload(
        async_track_state_change_event(hass, entity_id, _sensor_changed)
    )


def _build_room(data: Mapping[str, Any], transport: HassTransport) -> RoomRuntime:
    """One room's runtime, read from the subentry that describes it.

    `read_state` is bound to the transport's one-entity read so occupancy is
    answered by the house each time it is asked, not cached at setup; a room
    whose sensor is unavailable at startup must read `None` then, exactly as it
    would if the sensor dropped out later.
    """
    return RoomRuntime(
        area_id=str(data.get(DATA_AREA_ID, "")),
        room_type=str(data.get(DATA_ROOM_TYPE, "")),
        bindings=_bindings(data),
        read_state=transport.state,
    )


def _panel_version(hass: HomeAssistant) -> str:
    """A token that changes whenever the served bundle changes.

    Home Assistant serves `/local/` with a 31-day `max-age`, so a browser that has
    loaded the panel once keeps it for a month unless the *URL* changes -- which
    is not a hypothetical: it is why a rebuilt panel kept failing in a browser
    that had the previous build cached, with an error whose text named the code
    the browser was running rather than the code on disk. The query string makes
    the URL change, so the browser fetches the new bundle and the old one is never
    referenced again.

    The token is the served file's size and modification time rather than the
    integration's version: a version moves on release, and during development a
    bundle is rebuilt many times under one version, which is exactly when a stale
    copy is most confusing. Size and mtime change on every build that changes the
    bytes, and they are two fields from one `stat` rather than a hash of a
    megabyte.

    Falls back to the integration's version when the file cannot be read, which
    is the honest answer for an install that ships the integration without the
    bundle: the panel will not work either way, and a constant is better than a
    crash in `async_setup` that would take the config flow down with it.
    """
    try:
        info = Path(hass.config.path(str(PANEL_BUNDLE_PATH))).stat()
    except OSError:
        return VERSION
    return f"{VERSION}-{info.st_size:x}-{info.st_mtime_ns:x}"


def _bindings(data: Mapping[str, Any]) -> Mapping[str, str]:
    """The room's slot bindings, keeping only the slots bound to a string id."""
    raw = data.get(DATA_BINDINGS)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(slot): str(entity_id)
        for slot, entity_id in raw.items()
        if isinstance(entity_id, str) and entity_id
    }
