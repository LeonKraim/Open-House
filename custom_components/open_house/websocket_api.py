"""The websocket commands the sidebar panel speaks.

`panel/src/api/protocol.ts` *is* the API: it names all 29 commands, and this
module implements them. The panel issues every read and every write through these
and touches no file and no YAML, which is what `spec.txt`'s Phase 5 exit
criterion -- "the whole journey runs with no YAML" -- actually requires of the
server. A command that is missing here is a screen that cannot work, and it fails
as `unknown_command` rather than as an error anyone can act on.

**The reply shape belongs to `views.py` and the mutation to `ha_adapter`.** Almost
every command does the same two things: change the session through one of the
`ha_adapter.live_*` modules, then answer with the projection `views.py` builds
from the changed session. Writing the reply here out of the live module's own
return value would put the panel's field names in two places -- the module's and
the views' -- and the two would drift the first time either was touched, silently,
because the panel reads a missing field as `undefined` and renders a blank row.
So: `live_*` mutates, `views` answers.

**`bind` fills, `replace` displaces.** The protocol names both and the panel
already treats them differently (`panel/src/tabs/room-settings.ts` calls
`replace` when the slot had something and `bind` when it did not). Making the
commands do the same thing would make the distinction decorative, so `bind`
refuses to overwrite a slot that is already bound and names what it would have
displaced. A person who means to swap a device says so.

**Every command but `capabilities` requires an admin.** The panel hides the
admin-only screens from a non-admin, but the panel is not the boundary -- a
websocket command is reachable by anything that can authenticate, so the check is
here. `capabilities` is the exception because it is the one command whose whole
job is to tell a screen whether it is allowed to ask.

**A refusal is an error code the panel knows.** `unauthorized` for a non-admin,
`not_setup` when the instance has never completed the setup flow, `not_found` for
a room, slot or pack that does not exist, and `invalid_format` for a value
refused on its merits. A refusal that arrived as a successful reply carrying an
error field would be one every screen would have to remember to check.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar

from ha_adapter import live_export, live_modules, live_profiles
from ha_adapter.live import HOUSE, LiveSessionError

from . import views
from .const import DOMAIN, ENGINE_API_FALLBACK, VERSION
from .host import OpenHouseHost, single_host

__all__ = ["async_register_websocket_api"]


# -- The command names ------------------------------------------------------
#
# Spelled here as they are spelled in `protocol.ts`, one constant per command, so
# the two files can be read side by side. They are literals rather than built from
# a prefix: a command name assembled from parts is a name no search finds, and the
# panel's own file spells each one out.

CAPABILITIES = "open_house/capabilities"
OVERVIEW = "open_house/overview"
ROOMS_LIST = "open_house/rooms/list"
ROOM_GET = "open_house/rooms/get"
ROOM_CREATE = "open_house/rooms/create"
ROOM_UPDATE = "open_house/rooms/update"
ROOM_DELETE = "open_house/rooms/delete"
ROOM_BIND = "open_house/rooms/bind"
ROOM_REPLACE = "open_house/rooms/replace"
ROOM_UNBIND = "open_house/rooms/unbind"
ROOM_CANDIDATES = "open_house/rooms/candidates"
ROOM_OPTIONS_GET = "open_house/rooms/options/get"
ROOM_OPTIONS_SET = "open_house/rooms/options/set"
ROOM_AVAILABLE_MODULES = "open_house/rooms/available_modules"
MODULE_INSTALL = "open_house/modules/install"
MODULE_UNINSTALL = "open_house/modules/uninstall"
MODULE_SET_ENABLED = "open_house/modules/set_enabled"
MODULE_SET_BEHAVIOUR_ENABLED = "open_house/modules/set_behaviour_enabled"
MODULE_SET_BEHAVIOUR_SCOPE = "open_house/modules/set_behaviour_scope"
MODULES_LIST = "open_house/modules/list"
HOUSE_SCOPE = "open_house/house/scope"
PROFILES_LIST = "open_house/profiles/list"
PROFILE_ACTIVATE = "open_house/profiles/activate"
STORE_INDEX = "open_house/store/index"
STORE_INSTALL = "open_house/store/install"
ACTIVITY_LIST = "open_house/activity/list"
ACTIVITY_SUBSCRIBE = "open_house/activity/subscribe"
HEALTH_LIST = "open_house/health/list"
EXPORT_DOCUMENT = "open_house/import_export/export"
IMPORT_PREVIEW = "open_house/import_export/preview"
IMPORT_APPLY = "open_house/import_export/apply"
DASHBOARD_GENERATE = "open_house/dashboard/generate"

#: The error code a non-admin is refused with. Named here rather than inlined so
#: the panel's own constant (`API_DOMAIN` plus `unauthorized`) has one spelling on
#: this side too.
UNAUTHORIZED = "unauthorized"
#: No config entry has completed the setup flow, so there is no house to speak of.
NOT_SETUP = "not_setup"
#: An entry exists, but it is between loads and there is no host *yet*. Distinct
#: from `not_setup` because the two want opposite things from whoever asked: this
#: one is worth asking again in a moment, and `not_setup` is worth never asking
#: again until someone runs the setup flow. Collapsing them told a person who had
#: just created a room that their house did not exist.
NOT_READY = "not_ready"
#: A room, slot, pack or profile the caller named does not exist.
NOT_FOUND = "not_found"
#: A value the caller sent is wrong on its merits, or the request is malformed.
INVALID_FORMAT = "invalid_format"

#: `hass.data` key holding whether the commands have been registered. They are
#: registered once per process rather than per entry, because a websocket command
#: is global to the instance -- `async_register_command` refuses a duplicate, and
#: a reload would otherwise try to define each one again.
DATA_REGISTERED = f"{__package__}_websocket_registered"

#: The most activity rows one request may ask for. The engine's log is bounded
#: anyway (`engine/decision_log.py`), so this is not protecting memory: it is
#: stopping a request from asking for a window so large that building the JSON is
#: itself the delay a person sees.
_ACTIVITY_LIMIT = 500


def _admin(handler: Any) -> Any:
    """Refuse a command from a connection that is not an administrator.

    Applied under `websocket_command`, so the schema is still attached to the
    function that is actually registered, and over the handler body, so no command
    can reach a session without passing through it. `capabilities` is the one
    command registered bare, and the module docstring says why.
    """

    async def _guard(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        user = connection.user
        if user is None or not user.is_admin:
            connection.send_error(
                msg["id"], UNAUTHORIZED, "Open House's panel is for administrators"
            )
            return
        await handler(hass, connection, msg)

    return _guard


def _house_exists(hass: HomeAssistant) -> bool:
    """Whether an Open House entry has been made, whether or not it is loaded.

    `single_host` answers whether the house can be *read* right now; this answers
    whether it has been *made*. They differ for exactly as long as a config entry
    reload takes, and the difference is what a caller needs to know: a house that
    is reloading is worth asking again, and a house that was never made is not.
    """
    return bool(hass.config_entries.async_entries(DOMAIN))


def _host_or_error(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> OpenHouseHost | None:
    """The one host, sending a refusal and answering `None` when there is none.

    Every command needs the same three lines and every one of them has the same
    two outcomes, so the lookup is one function rather than a block copied into
    twenty-eight handlers -- where a handler that forgot to check would answer a
    reply shaped like a house over a session that does not exist.

    Which refusal it sends matters. Writing a room subentry makes Home Assistant
    reload the config entry (`async_add_subentry` schedules one), and a reload
    takes the host away and puts it back. A read that lands in that gap is not
    early and is not wrong -- it is a question about a house that is momentarily
    unreadable, so it is answered `not_ready`, which the panel asks again.
    `not_setup` is kept for the case it names: no entry, no flow ever run, no
    house to be had until somebody makes one.
    """
    host = single_host(connection.hass)
    if host is None:
        if _house_exists(connection.hass):
            connection.send_error(
                msg["id"],
                NOT_READY,
                "Open House is reloading; ask again in a moment",
            )
        else:
            connection.send_error(
                msg["id"],
                NOT_SETUP,
                "Open House has no house yet; run its setup flow to create one",
            )
    return host


def _error(
    connection: websocket_api.ActiveConnection, msg: dict[str, Any], refusal: Exception
) -> None:
    """Report a refusal from the session as `not_found` or `invalid_format`.

    `LiveSessionError` is one type over two kinds of refusal -- "there is no such
    room" and "that value is not acceptable" -- and the panel acts on them
    differently: an unknown name is a screen that should refresh, and a rejected
    value is a form that should keep what the person typed. So the two are told
    apart by what the message says, and the message is the one the live layer
    already wrote for a person to read.
    """
    text = str(refusal)
    missing = "no room" in text or "no slot" in text or "no option" in text
    connection.send_error(msg["id"], NOT_FOUND if missing else INVALID_FORMAT, text)


# -- Registration -----------------------------------------------------------


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register every command, once per process."""
    if hass.data.get(DATA_REGISTERED):
        return
    hass.data[DATA_REGISTERED] = True
    for handler in _HANDLERS:
        websocket_api.async_register_command(hass, handler)


# -- Capabilities -----------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): CAPABILITIES})
@websocket_api.async_response
async def ws_capabilities(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Who is looking, and what they may do. Answerable by any authenticated user.

    `needs_setup` is the absence of a *house*, not the absence of a host: an entry
    that exists but is reloading has a house, and saying otherwise sent the panel
    to "Open House has not been set up yet. Finish setup" over a house somebody
    had just finished setting up. So the flag reads `_house_exists`, the same
    question `_host_or_error` asks, and the two cannot disagree.
    """
    user = connection.user
    host = single_host(hass)
    connection.send_result(
        msg["id"],
        views.capabilities(
            admin=bool(user is not None and user.is_admin),
            user_name=None if user is None else user.name,
            version=VERSION,
            engine_api=(
                ENGINE_API_FALLBACK
                if host is None
                else host.session.vocabulary.engine_api_version
            ),
            needs_setup=not _house_exists(hass),
        ),
    )


# -- Overview ---------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): OVERVIEW})
@websocket_api.async_response
@_admin
async def ws_overview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The Overview tab's one answer."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(msg["id"], views.overview(hass, host))


# -- Rooms ------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): ROOMS_LIST})
@websocket_api.async_response
@_admin
async def ws_rooms_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every room, as the Rooms tab's rows."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(msg["id"], {"rooms": list(views.room_summaries(hass, host))})


@websocket_api.websocket_command(
    {vol.Required("type"): ROOM_GET, vol.Required("room_id"): str}
)
@websocket_api.async_response
@_admin
async def ws_room_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """One room's settings page."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    _detail(connection, msg, hass, host, msg["room_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_CREATE,
        vol.Required("name"): str,
        # Not `type`. The discriminator is already called `type`, and a Python
        # dict literal that names a key twice keeps only the last one -- so the
        # version of this schema that had `vol.Required("type"): str` here did
        # not narrow the command at all: it replaced the value that names the
        # command with `str` and Home Assistant registered the handler under the
        # command `"str"`. `open_house/rooms/create` was never registered, and
        # the panel's "Create room" answered `unknown_command` for as long as
        # that line was there. `tests/test_ws_contract.py` now reads this file
        # for exactly this shape.
        vol.Required("room_type"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_create(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add a room: a Home Assistant area, then the subentry that makes it one.

    The area is created first because areas are the source of truth for rooms
    (`spec.txt`): a room that named no area would be a room Home Assistant cannot
    show a device in, and the setup flow would have nothing to match against on
    the next run. The panel asks for a name and a type; the area id is Home
    Assistant's to mint.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    areas = ar.async_get(hass)
    # An area that already exists is *adopted*, not refused. Home Assistant areas
    # are the source of truth for rooms, and a real house has areas before Open
    # House does -- an integration files its devices into one, or the person made
    # it in Home Assistant's own settings. Refusing would leave that person with
    # no way to make the room, which is the one thing this command is for. An
    # area that is *already a room* is still refused, but further in, by
    # `OpenHouseHost.async_add_room`.
    area = areas.async_get_area_by_name(msg["name"]) or areas.async_create(msg["name"])
    try:
        room = await host.async_add_room(
            area_id=area.id, room_type=msg["room_type"], bindings={}
        )
    except (KeyError, ValueError) as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    _detail(connection, msg, hass, host, room.id)


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_UPDATE,
        vol.Required("room_id"): str,
        vol.Optional("name"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Rename a room's subentry title.

    Deliberately not a rename of the area: the area is the source of truth and
    Home Assistant has its own screen for it, so this is the panel's title for a
    room whose area a person has not renamed -- and the next reload reads the
    area's name over it, which is the correct outcome.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    if "name" in msg:
        try:
            await host.async_rename_room(msg["room_id"], msg["name"])
        except KeyError:
            connection.send_error(msg["id"], NOT_FOUND, f"no room {msg['room_id']!r}")
            return
    _detail(connection, msg, hass, host, msg["room_id"])


@websocket_api.websocket_command(
    {vol.Required("type"): ROOM_DELETE, vol.Required("room_id"): str}
)
@websocket_api.async_response
@_admin
async def ws_room_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a room's subentry. The area is left alone, and is the reply.

    The area is not deleted with the room, because it is not the integration's to
    delete: it may hold devices a person filed there, and removing a room from the
    panel is a decision about the house's automation rather than about Home
    Assistant's layout.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        await host.async_remove_room(msg["room_id"])
    except KeyError:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {msg['room_id']!r}")
        return
    except ValueError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(msg["id"], {"room_id": msg["room_id"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_BIND,
        vol.Required("room_id"): str,
        vol.Required("slot"): str,
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_bind(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Fill an empty slot. See the module docstring for why this refuses a rebind.

    `room_id` may be `HOUSE`, whose slots are bound on the House tab: the
    occupancy it checks against is the house's own binding for the slot, not any
    room's, because that is the binding this command would displace.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    house_scope = msg["room_id"] == HOUSE
    room = host.room(msg["room_id"])
    if room is None and not house_scope:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {msg['room_id']!r}")
        return
    occupied = (
        host.session.house_bindings.get(msg["slot"])
        if house_scope
        else None
        if room is None
        else room.bindings.get(msg["slot"])
    )
    if occupied is not None:
        connection.send_error(
            msg["id"],
            INVALID_FORMAT,
            f"the slot {msg['slot']!r} already holds {occupied!r}; "
            "use open_house/rooms/replace to swap the device",
        )
        return
    await _bind(
        connection, msg, hass, host, msg["room_id"], msg["slot"], msg["entity_id"]
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_REPLACE,
        vol.Required("room_id"): str,
        vol.Required("slot"): str,
        vol.Required("entity_id"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_replace(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Swap the device in a slot, whether or not it held one.

    The displaced device is named back to the caller in the log line rather than
    in the reply, because the reply is a `RoomDetail` and the panel renders it as
    the room's current state -- a field carrying the *previous* binding would be a
    field every screen has to know to ignore.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    await _bind(
        connection, msg, hass, host, msg["room_id"], msg["slot"], msg["entity_id"]
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_UNBIND,
        vol.Required("room_id"): str,
        vol.Required("slot"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_unbind(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Leave a slot with nothing in it."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        await host.async_set_binding(msg["room_id"], msg["slot"], None)
    except KeyError:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {msg['room_id']!r}")
        return
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    _detail(connection, msg, hass, host, msg["room_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_CANDIDATES,
        vol.Required("room_id"): str,
        vol.Required("slot"): str,
        vol.Optional("query"): str,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1)),
    }
)
@websocket_api.async_response
@_admin
async def ws_room_candidates(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The devices the server proposes for a slot, best first.

    `room_id` may be `HOUSE`, for a global slot: there is no room to file the
    candidates under, so the whole house is the candidate set
    (`views.candidates`).
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        found = views.candidates(
            hass,
            host,
            room_id=msg["room_id"],
            slot=msg["slot"],
            query=msg.get("query"),
            limit=msg.get("limit"),
        )
    except KeyError:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {msg['room_id']!r}")
        return
    connection.send_result(msg["id"], {"candidates": list(found)})


@websocket_api.websocket_command(
    {vol.Required("type"): ROOM_OPTIONS_GET, vol.Required("room_id"): str}
)
@websocket_api.async_response
@_admin
async def ws_room_options_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The room's high-level options, as a schema and the values it holds."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        schema, values = live_profiles.options(host.session, room_id=msg["room_id"])
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    connection.send_result(msg["id"], {"schema": schema, "values": dict(values)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_OPTIONS_SET,
        vol.Required("room_id"): str,
        vol.Required("values"): dict,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_options_set(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Write one or more options, answering with the schema and the values after.

    Every key is written before the reply is built, so a request that names three
    options and fails on the third leaves the first two written and reports the
    failure -- rather than a partial write the caller cannot see. That is the
    honest behaviour for a form: the person's next read shows what actually stuck.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    schema: Mapping[str, object] | None = None
    values: Mapping[str, object] = {}
    try:
        for key, value in msg["values"].items():
            schema, values = live_profiles.set_option(
                host.session, room_id=msg["room_id"], key=str(key), value=value
            )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    connection.send_result(msg["id"], {"schema": schema, "values": dict(values)})


@websocket_api.websocket_command(
    {vol.Required("type"): ROOM_AVAILABLE_MODULES, vol.Required("room_id"): str}
)
@websocket_api.async_response
@_admin
async def ws_room_available_modules(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every pack the room could hold, each with its satisfiability and conflicts."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        found = live_modules.offers(host.session, room_id=msg["room_id"])
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    connection.send_result(msg["id"], {"offers": list(found)})


# -- Modules ----------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_INSTALL,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_module_install(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Install a pack into a room, answering with the module and the room after.

    The module is looked up out of the installed list *after* the install rather
    than taken from the install call's own return, so the reply is built from the
    same projection `modules/list` answers with: two commands that described the
    same module differently would be a screen that changed shape depending on how
    a person got to it.

    `room_id` may be the house (`""`), which puts the module in the whole house
    rather than a room. The reply then carries the house's own page (`house`)
    instead of a room's, because the house is the target the person installed
    into and a room detail for a room they did not choose would be the wrong page
    to hand back.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    path = _pack_path(hass, host, msg["pack"])
    if path is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    try:
        live_modules.install(
            host.session, path, room_id=msg["room_id"], root=host.session.root
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(
            msg["id"], INVALID_FORMAT, f"the pack {msg['pack']!r} did not install"
        )
        return
    if msg["room_id"] == HOUSE:
        connection.send_result(
            msg["id"],
            {"installed": installed, "house": views.house_scope(host)},
        )
        return
    connection.send_result(
        msg["id"],
        {"installed": installed, "room": views.room_detail(hass, host, msg["room_id"])},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_UNINSTALL,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_module_uninstall(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Remove a pack from the house, answering with the room -- or house -- after.

    `room_id` may be the house (`""`), which is where a module put in the whole
    house lives; the reply is then the house's page rather than a room detail,
    for the reason `ws_module_install` gives.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_modules.uninstall(host.session, msg["pack"])
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    if msg["room_id"] == HOUSE:
        connection.send_result(
            msg["id"],
            {
                "room_id": HOUSE,
                "house": views.house_scope(host),
            },
        )
        return
    _detail(connection, msg, hass, host, msg["room_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_SET_ENABLED,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
        vol.Required("enabled"): bool,
    }
)
@websocket_api.async_response
@_admin
async def ws_module_set_enabled(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Turn a pack's behaviours on or off for one room.

    A permission and not an actuation (`ha_adapter.live.set_room_auto_lighting` is
    the precedent): the flag decides whether the next tick may act, and turning it
    off does not undo what was already done.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_modules.set_enabled(
            host.session,
            room_id=msg["room_id"],
            pack=msg["pack"],
            enabled=msg["enabled"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_SET_BEHAVIOUR_ENABLED,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
        vol.Required("behaviour"): str,
        vol.Required("enabled"): bool,
    }
)
@websocket_api.async_response
@_admin
async def ws_module_set_behaviour_enabled(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Turn one behaviour of a pack on or off for one room.

    The same permission the pack-level switch is, one atom down: it writes the
    unit's enable flag and stops there, so nothing that atom already did is
    undone and the next tick is what finds it switched off. `behaviour` is the
    pack-qualified id the module row already carries, so the panel sends back the
    atom it rendered rather than a name it composed.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_modules.set_behaviour_enabled(
            host.session,
            room_id=msg["room_id"],
            pack=msg["pack"],
            behaviour=msg["behaviour"],
            enabled=msg["enabled"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_SET_BEHAVIOUR_SCOPE,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
        vol.Required("behaviour"): str,
        vol.Required("scope"): vol.In(["room", "house"]),
    }
)
@websocket_api.async_response
@_admin
async def ws_module_set_behaviour_scope(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Say which rooms one behaviour of a pack runs for.

    The reach control beside the atom's switch: `room` narrows a house-wide atom
    to the room its module was installed into, and `house` widens a room's atom
    to every room. Configuration rather than state, so this is saved with the
    house settings (`ha_adapter.live_modules.set_behaviour_scope`), and it is
    admin-only for the same reason the switch is: it changes what the house will
    do next, not what it has already done.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_modules.set_behaviour_scope(
            host.session,
            room_id=msg["room_id"],
            pack=msg["pack"],
            behaviour=msg["behaviour"],
            scope=msg["scope"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command({vol.Required("type"): MODULES_LIST})
@websocket_api.async_response
@_admin
async def ws_modules_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every installed pack."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(
        msg["id"], {"modules": list(live_modules.installed_modules(host.session))}
    )


@websocket_api.websocket_command({vol.Required("type"): HOUSE_SCOPE})
@websocket_api.async_response
@_admin
async def ws_house_scope(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The house's own slots, and the whole-house modules.

    The slots are the roles an installed module actually reaches
    (`ha_adapter.live_modules.house_scope`), and each one is bound here rather
    than collected: `open_house/rooms/bind` with `room_id` `HOUSE` writes the
    house's own binding for it, the global entity that fills the role in the
    house scope and in every room that bound none of its own. The modules are
    the ones that act on the house, wherever they sit -- a bedtime button in a
    bedroom is one of them, because the house's doors are what it is for.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(msg["id"], views.house_scope(host))


# -- Profiles ---------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): PROFILES_LIST})
@websocket_api.async_response
@_admin
async def ws_profiles_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every profile the house holds."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(
        msg["id"], {"profiles": list(live_profiles.profiles(host.session))}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PROFILE_ACTIVATE,
        vol.Required("room_id"): str,
        vol.Required("axis"): str,
        vol.Required("profile"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_profile_activate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put a room on a profile for an axis. A settings change, not an actuation."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_profiles.activate(
            host.session,
            room_id=msg["room_id"],
            axis=msg["axis"],
            profile=msg["profile"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    _detail(connection, msg, hass, host, msg["room_id"])


# -- Store ------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): STORE_INDEX})
@websocket_api.async_response
@_admin
async def ws_store_index(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every pack the registry knows, from the checkout's own registry."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(msg["id"], views.store_index(hass, host))


@websocket_api.websocket_command(
    {
        vol.Required("type"): STORE_INSTALL,
        vol.Required("pack"): str,
        vol.Required("tier"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_store_install(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Install a pack by name, as the Store's own button does.

    Two steps and a deliberate split between them. `live_modules.store_pack`
    resolves the published row and verifies the file against its pinned SHA-256
    and the revocation list; `live_modules.install` then puts it in the house.
    The first runs in an executor and the second does not, and that is not an
    oversight: verification only reads, so it is safe off the loop, while the
    install writes the session's installed set and can therefore interleave with
    `automation._tick` if it is moved to a thread. A blocking read on a button
    press is a cost worth paying to keep the engine single-threaded.

    A refusal from either step is sent as an error code the panel can name.
    `missing` -- no row by that name at that tier -- is `not_found`, because the
    thing asked for does not exist; a file that fails its pin is `invalid_format`,
    because the thing asked for exists and this is not it.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    verify = partial(
        live_modules.store_pack, host.session.root, msg["pack"], msg["tier"]
    )
    try:
        path = await hass.async_add_executor_job(verify)
    except live_modules.StorePackMissingError as refusal:
        # "The Store does not sell that" and "the Store sells it and this copy
        # is not it" are different sentences to a person, so they are different
        # codes: one is a stale panel, the other is a corrupted checkout.
        connection.send_error(msg["id"], NOT_FOUND, str(refusal))
        return
    except live_modules.StorePackRefusedError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    try:
        live_modules.install(host.session, path, root=host.session.root)
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(
            msg["id"], INVALID_FORMAT, f"the pack {msg['pack']!r} did not install"
        )
        return
    connection.send_result(msg["id"], {"installed": installed})


# -- Activity ---------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): ACTIVITY_LIST,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=_ACTIVITY_LIMIT)),
        vol.Optional("before"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_activity_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The decision log, newest first, paged by an opaque cursor."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    found = live_export.activity(
        host.session, limit=msg.get("limit", 50), before=msg.get("before")
    )
    connection.send_result(msg["id"], {"entries": list(found)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): ACTIVITY_SUBSCRIBE,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=_ACTIVITY_LIMIT)),
    }
)
@websocket_api.async_response
@_admin
async def ws_activity_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Push new decision records as the house makes them.

    The stream is the *host's*, and the host is told to publish after each tick
    (`automation.py`), because the engine's log notifies nobody: a record exists
    only because a tick produced it, so a subscription that listened anywhere else
    would be guessing at when the log had grown.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    identifier = msg["id"]

    @callback
    def _forward(event: Mapping[str, object]) -> None:
        connection.send_event(identifier, dict(event))

    connection.subscriptions[identifier] = host.subscribe(_forward)
    connection.send_result(identifier)


# -- Health -----------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): HEALTH_LIST})
@websocket_api.async_response
@_admin
async def ws_health_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What the house has found wrong, and what Home Assistant is already saying."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(msg["id"], {"issues": list(views.health_issues(hass, host))})


# -- Import and export ------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): EXPORT_DOCUMENT})
@websocket_api.async_response
@_admin
async def ws_export_document(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The house as a frozen export document, carrying no Home Assistant ids."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(
        msg["id"],
        live_export.export_document(host.session, registry_ids=_registry_ids(host)),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): IMPORT_PREVIEW, vol.Required("document"): dict}
)
@websocket_api.async_response
@_admin
async def ws_import_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A dry run of an import. Changes nothing, which is what makes it a preview."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(
        msg["id"], live_export.preview(host.session, msg["document"])
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): IMPORT_APPLY,
        vol.Required("document"): dict,
        vol.Optional("snapshot", default=True): bool,
    }
)
@websocket_api.async_response
@_admin
async def ws_import_apply(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Apply, then write down what the session became.

    The store is written *after* the apply rather than as part of it, because the
    apply is the session's own act and this module's job is to make it survive a
    restart. A refusal from the import is reported before anything is written, so
    a document that was rejected leaves no half-applied state on disk.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        result = live_export.apply(host.session, msg["document"])
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await host.async_save()
    connection.send_result(msg["id"], result)


# -- Dashboard --------------------------------------------------------------


@websocket_api.websocket_command(
    {vol.Required("type"): DASHBOARD_GENERATE, vol.Required("room_id"): str}
)
@websocket_api.async_response
@_admin
async def ws_dashboard_generate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Offer the room's entities to Home Assistant's own generated dashboard.

    Nothing is written and no dashboard is created: Home Assistant already builds
    an area's dashboard from the area and the registry, and a second dashboard this
    integration wrote would be a copy of a screen Home Assistant maintains better.
    So this answers with the path that already exists -- which is the honest reply
    to "generate", and the reason the panel's button can be a link.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    room = host.room(msg["room_id"])
    if room is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {msg['room_id']!r}")
        return
    connection.send_result(
        msg["id"], {"created": False, "url_path": f"lovelace/{room.area_id}"}
    )


# -- Shared plumbing --------------------------------------------------------


async def _bind(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    hass: HomeAssistant,
    host: OpenHouseHost,
    room_id: str,
    slot: str,
    entity_id: str,
) -> None:
    """Bind one slot and answer with the room, for both bind and replace.

    One body for two commands because the *difference between them is the check
    above it*, not the write: `bind` refuses an occupied slot before reaching
    here and `replace` does not. Duplicating the write would be a second place for
    the subentry and the session to be written in the wrong order.
    """
    try:
        await host.async_set_binding(room_id, slot, entity_id)
    except KeyError:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {room_id!r}")
        return
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    _detail(connection, msg, hass, host, room_id)


def _detail(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    hass: HomeAssistant,
    host: OpenHouseHost,
    room_id: str,
) -> None:
    """Answer with one room's detail, or `not_found` when it is gone.

    Every mutating command answers with the room it changed, so the panel renders
    the state the server just produced rather than what it hoped it produced --
    which is what makes a refused or partly-applied write visible instead of
    silently divergent from the screen.

    `room_id` may be `HOUSE`, in which case the page that changed is the house's
    own -- binding a global slot redraws the House tab, not a room.
    """
    if room_id == HOUSE:
        connection.send_result(msg["id"], views.house_scope(host))
        return
    if host.room(room_id) is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no room {room_id!r}")
        return
    connection.send_result(msg["id"], views.room_detail(hass, host, room_id))


def _installed(host: OpenHouseHost, pack: str) -> Mapping[str, object] | None:
    """The installed module called `pack`, or `None`."""
    for module in live_modules.installed_modules(host.session):
        if module.get("pack") == pack:
            return module
    return None


def _pack_path(
    hass: HomeAssistant, host: OpenHouseHost, pack: str, *, tier: str | None = None
) -> Any:
    """The manifest a registry record for `pack` points at, or `None`.

    Resolved through `views.pack_path`, which reads the same index the Store tab
    is built from, so the file a person installs is the one the row they clicked
    named. A pack that is on disk but not in the registry is not installable,
    which is the rule the registry exists to state.
    """
    return views.pack_path(host, pack, tier=tier)


def _registry_ids(host: OpenHouseHost) -> Mapping[str, str]:
    """Every bound entity's registry id, keyed by entity id.

    The export document is written to be portable, so it names entities by
    registry id where it can: an entity id is a name a person may change, and a
    document that carried one would restore a house whose bindings pointed at
    nothing after a rename. The engine's own export takes this mapping for exactly
    that reason and this is where a live house gets one.
    """
    from homeassistant.helpers import entity_registry as er

    entries = er.async_get(host.hass)
    found: dict[str, str] = {}
    for room in host.rooms.values():
        for entity_id in room.bindings.values():
            entry = entries.async_get(entity_id)
            if entry is not None:
                found[entity_id] = entry.id
    return found


#: Every handler, in the order `protocol.ts` lists them. A tuple rather than a
#: call to `async_register_command` at each definition, so "is every command
#: registered" is a question about one list a test can read.
_HANDLERS: tuple[Any, ...] = (
    ws_capabilities,
    ws_overview,
    ws_rooms_list,
    ws_room_get,
    ws_room_create,
    ws_room_update,
    ws_room_delete,
    ws_room_bind,
    ws_room_replace,
    ws_room_unbind,
    ws_room_candidates,
    ws_room_options_get,
    ws_room_options_set,
    ws_room_available_modules,
    ws_module_install,
    ws_module_uninstall,
    ws_module_set_enabled,
    ws_module_set_behaviour_enabled,
    ws_module_set_behaviour_scope,
    ws_house_scope,
    ws_modules_list,
    ws_profiles_list,
    ws_profile_activate,
    ws_store_index,
    ws_store_install,
    ws_activity_list,
    ws_activity_subscribe,
    ws_health_list,
    ws_export_document,
    ws_import_preview,
    ws_import_apply,
    ws_dashboard_generate,
)
