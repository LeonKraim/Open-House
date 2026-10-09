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

import contextlib
import logging
from collections.abc import Mapping
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from engine.profiles import ProfileError
from ha_adapter import (
    live_export,
    live_modules,
    live_profiles,
    module_definitions,
    module_host,
    module_records,
    pack_authoring,
    slot_parts,
    store_api,
)
from ha_adapter.live import HOUSE, LiveSessionError
from ha_adapter.module_definitions import ModuleDefinition

from . import dev_authoring, modules, node_red, store, views
from .const import DOMAIN, ENGINE_API_FALLBACK, VERSION
from .host import OpenHouseHost, single_host

__all__ = ["async_register_websocket_api"]

_LOGGER = logging.getLogger(__name__)


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
MODULE_SET_BEHAVIOUR_PRIORITY = "open_house/modules/set_behaviour_priority"
MODULE_SET_SLOT = "open_house/modules/set_slot"
MODULE_SET_SLOT_RULE = "open_house/modules/set_slot_rule"
SLOT_SET_PARTS = "open_house/slots/set_parts"
MODULES_LIST = "open_house/modules/list"
HOUSE_SCOPE = "open_house/house/scope"
PROFILES_LIST = "open_house/profiles/list"
PROFILE_ACTIVATE = "open_house/profiles/activate"
PROFILE_ACTIVATE_HOUSE = "open_house/profiles/activate_house"
PROFILE_CAPTURE = "open_house/profiles/capture"
PROFILE_RENAME = "open_house/profiles/rename"
PROFILE_REMOVE = "open_house/profiles/remove"
PROFILE_DEACTIVATE_HOUSE = "open_house/profiles/deactivate_house"
PROFILE_EXPORT = "open_house/profiles/export"
PROFILE_IMPORT = "open_house/profiles/import"
ACTIVITY_LIST = "open_house/activity/list"
ACTIVITY_SUBSCRIBE = "open_house/activity/subscribe"
HEALTH_LIST = "open_house/health/list"
DASHBOARD_GENERATE = "open_house/dashboard/generate"
DEV_SOURCES = "open_house/dev/sources"
MODULES_HOSTED = "open_house/modules/hosted"
MODULES_READ = "open_house/modules/read"
MODULES_HOST = "open_house/modules/host"
MODULES_SETTINGS = "open_house/modules/settings"
MODULES_EDIT = "open_house/modules/edit"
MODULES_PUBLISH = "open_house/modules/publish"
MODULES_STORE = "open_house/modules/store"
MODULES_DEFINE = "open_house/modules/define"
MODULES_DEPLOY = "open_house/modules/deploy"
MODULES_REMOVE = "open_house/modules/remove"
MODULES_DETACH = "open_house/modules/detach"
MODULES_UNHOST = "open_house/modules/unhost"
MODULES_EXPORT = "open_house/modules/export"
MODULES_IMPORT = "open_house/modules/import"
MODULES_CONFIG_SWITCH = "open_house/modules/configs/switch"
MODULES_CONFIG_ADD = "open_house/modules/configs/add"
MODULES_CONFIG_RENAME = "open_house/modules/configs/rename"
MODULES_CONFIG_REMOVE = "open_house/modules/configs/remove"
PUBLISHED_STATUS = "open_house/published/status"
PUBLISHED_CONFIGURE = "open_house/published/configure"
PUBLISHED_CLAIM = "open_house/published/claim"
PUBLISHED_BROWSE = "open_house/published/browse"
PUBLISHED_PUBLISH = "open_house/published/publish"
PUBLISHED_INSTALL = "open_house/published/install"
PUBLISHED_RATE = "open_house/published/rate"
PUBLISHED_COMMENTS = "open_house/published/comments"
PUBLISHED_COMMENT = "open_house/published/comment"

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
#: The write was made from a page rendered before the house was moved onto
#: another profile. Its own code because the panel does not treat it as a failed
#: edit: nothing was written, nothing is worth retrying, and the page it came from
#: is the thing that is out of date -- see `_stale`.
STALE_PAGE = "stale_page"

#: `hass.data` key holding whether the commands have been registered. They are
#: registered once per process rather than per entry, because a websocket command
#: is global to the instance -- `async_register_command` refuses a duplicate, and
#: a reload would otherwise try to define each one again.
DATA_REGISTERED = f"{__package__}_websocket_registered"

#: The most activity rows one request may ask for. The engine's log is bounded
#: anyway (`engine/decision_log.py`), so this is not protecting memory: it is
#: stopping a request from asking for a window so large that building the JSON is
#: itself the delay a person sees. It is sized against the *engine's own* rate of
#: writing rather than picked round: a tick records one row per behaviour per
#: scope and reaches about three hundred of them in a real house, so a window
#: narrower than a tick or two is a window that cannot be relied on to contain
#: the decision a person opened the tab to read -- see `LOG_BOUND_KEY` in
#: `engine/config.py` for what that cost when the window held under two ticks.
_ACTIVITY_LIMIT = 2_000


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


def _stale(
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    host: OpenHouseHost,
) -> bool:
    """Whether a write came from a page the house has moved out from under.

    A page renders the house from one revision and sends that number back with
    everything it writes. A *profile* is what moves the revision (`engine/profiles.py`
    counts the moves): switching a house profile puts a whole other house in place
    -- other settings, other bindings, other module answers -- and a page that was
    drawn before the switch is drawn from a house that no longer exists. Writing
    what it holds would be the pre-switch answers landing over the profile
    somebody just put on, which is the bug this exists to stop: the page has no
    way to know, so it is told (`stale_page`) and reloads.

    Absent is not stale. A write that carries no revision is a write from
    something that is not a page -- the CLI, an automation, a test -- and it is
    answered rather than refused, because the number is a claim about a *screen*
    and a caller that never rendered one makes no claim to check.

    **Older than, not different from.** The question `revision` answers is whether
    this set has *passed* the number the page is holding, so that is the test:
    a page ahead of this set is not a page describing another house, it is a page
    holding a number this set no longer has -- which is what a rebuild that could
    not resume the count leaves behind. Comparing for equality instead read that
    page as stale, and refused it *for good*: a counter climbing from zero cannot
    come back to a number it has lost, so one profile switch was enough to make
    every open page refuse every write until somebody reloaded it by hand. Judge
    by the rule and the pages that have genuinely been passed are still refused,
    while the ones that merely outlived a rebuild are answered.
    """
    sent = msg.get("revision")
    if sent is None or int(sent) >= host.session.revision:
        return False
    connection.send_error(
        msg["id"],
        STALE_PAGE,
        "this page was written before the house was put on another profile; "
        "reload it and make the change again",
    )
    return True


#: How every refusal that means "the thing you named is not in this house" begins.
#:
#: `LiveSessionError` carries a sentence and nothing else -- no code, no field --
#: so the two kinds of refusal it spans have to be told apart by that sentence,
#: and this is the shape the live layers write the *missing* kind in. Every such
#: refusal, and where it is raised:
#:
#:   * `there is no room <id> in this house`   -- `live.py:322`
#:   * `there is no slot <name> in this house` -- `live.py:456`
#:   * `there is no house slot <name> in this house` -- `live.py:478`
#:   * `there is no module <pack> in this house` -- `live_modules.py`, raised from
#:     `set_enabled`, `set_behaviour_enabled`, `set_behaviour_scope`,
#:     `set_behaviour_priority`, `set_slot`, `set_slot_rule`, `set_option`
#:   * `there is no entity <id> in this house, so ...` -- `live_modules.py`,
#:     from `set_slot` and `set_slot_rule`
#:   * `there is no option <key> in <where>`   -- `live_profiles.py:185`
#:
#: A single leading phrase rather than a growing list of substrings, because the
#: three that were here before (`no room`, `no slot`, `no option`) missed the two
#: commonest ones -- a module and an entity -- and one of them did not even match
#: its own case: `there is no house slot` contains no `no slot`. Matching the
#: phrase the whole family is written with is what makes "a named thing is
#: missing" recognisable by rule rather than by remembering to add a fourth
#: substring the day a fifth producer is written.
_MISSING = "there is no "


def _missing(refusal: Exception) -> bool:
    """Whether a refusal is the live layer saying a thing it was named is not here.

    The distinction is the panel's, not this module's: `not_found` is "that item
    no longer exists; reload the panel" and `invalid_format` is a value refused on
    its merits, kept in the form so a person can fix it. A slot rule set on a pack
    the house no longer holds is the first of those -- the row the person is
    looking at was drawn from a house that has moved on -- so it has to read as
    missing rather than as a bad value. See `_MISSING` for the family.
    """
    return str(refusal).startswith(_MISSING)


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
    connection.send_error(
        msg["id"], NOT_FOUND if _missing(refusal) else INVALID_FORMAT, text
    )


# -- The two things that follow a write that has landed ----------------------
#
# Every mutating handler here changes the session through `ha_adapter.live_*` and
# then does one of two follow-ups: writes the house down (`host.async_save`, for
# the state Home Assistant has no home for), or settles a slot (write down *and*
# build again the modules that reach it, `host.async_slot_changed`). Both are the
# *consequence* of the change, not the change: by the time either runs the session
# already reads back the new value.
#
# So a follow-up that fails must not be reported as a failed write. The panel
# renders `views`'s shapes field by field and reads no warning field, so a note
# threaded through a reply would be a field no screen draws -- which leaves the
# log, the one place that can be said today. These two helpers say it, and the
# handlers call them rather than reaching for `async_save`/`async_slot_changed`
# themselves so the rule is written once.


def _unsaved(refusal: Exception) -> None:
    """Note a house that could not be written down. See the section above."""
    _LOGGER.warning(
        "a change was made to the house and could not be written to the store, "
        "so it is live now but will not survive a restart: %s",
        refusal,
    )


def _unsettled(where: str, refusal: Exception) -> None:
    """Note a rebuild that could not follow a change that landed. See above.

    The rebuild's own failure is the interesting one -- a module whose record has
    lost its document, an automation Home Assistant will not accept -- and it is
    a fact about the *house*, not about the edit: the device the slot names is
    written and the next read shows it, while the automation that should act on
    it was not rewritten. `where` names the slot and room it was about, because
    the refusal's own sentence is about a module and a reader needs both.
    """
    _LOGGER.warning(
        "a change was made to %s and the modules it moves could not be built again: %s",
        where,
        refusal,
    )


async def _saved(host: OpenHouseHost) -> None:
    """Write the house down; a failure to is noted, never raised. See above.

    The save is the host's own (`host.async_save`), and this must not call
    itself. It did: one line reading `await _saved(host)`, so every write went
    ~1000 coroutine frames deep and raised `RecursionError`, which the `except`
    below caught at the frame above and reported as a store that refused the
    house. Every caller believed it had saved, every screen read back the new
    value from the live session, and no mutation in the whole panel reached the
    store -- a house that survived until the next restart and no longer.
    """
    try:
        await host.async_save()
    except Exception as refusal:
        _unsaved(refusal)


async def _rebuilt(host: OpenHouseHost, room_id: str, slot: str) -> None:
    """Settle a slot after it moved; a failure to is noted, never raised.

    The save and the rebuild are one call (`host.async_slot_changed`) because a
    caller that did one without the other would leave the running house and the
    saved one describing two different slots -- so both are guarded here rather
    than each at its own site. Either half failing is a follow-up that could not
    happen, which is why neither is raised. See the section above.
    """
    try:
        await host.async_slot_changed(room_id, slot)
    except Exception as refusal:
        _unsettled(f"{slot!r} in {room_id!r}", refusal)


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
            # **The address a browser opens, not the one Home Assistant pushes
            # to.** They are two strings for one editor and only one of them
            # resolves outside this stack, so a link built from the other is a
            # link that goes nowhere -- `_node_red_url` is the one the rows use,
            # and this is the same answer for everything on the screen that has
            # no row yet.
            node_red_url=await _node_red_url(hass),
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
    # The room is gone from the session, but the state that had no Home Assistant
    # home is not: what a person switched on in that room and how they tuned a pack
    # there live in the store, and the store is only written by this call. Left
    # unsaved, a restart would read the deleted room's settings back out of a file
    # that still names a room nothing answers to.
    await _saved(host)
    connection.send_result(msg["id"], {"room_id": msg["room_id"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_BIND,
        vol.Required("room_id"): str,
        vol.Required("slot"): str,
        vol.Required("entity_id"): str,
        vol.Optional("revision"): int,
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
    if host is None or _stale(connection, msg, host):
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
        vol.Optional("revision"): int,
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
    if host is None or _stale(connection, msg, host):
        return
    await _bind(
        connection, msg, hass, host, msg["room_id"], msg["slot"], msg["entity_id"]
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): ROOM_UNBIND,
        vol.Required("room_id"): str,
        vol.Required("slot"): str,
        vol.Optional("revision"): int,
    }
)
@websocket_api.async_response
@_admin
async def ws_room_unbind(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Leave a slot with nothing in it."""
    host = _host_or_error(connection, msg)
    if host is None or _stale(connection, msg, host):
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

    The list is every device the house holds, for a room's slot as much as for a
    global one: a room's candidates lead with that room's own devices and carry
    the rest of the house behind them, so a person can point a room's slot at
    anything their house has. `room_id` may be `HOUSE`, for a global slot, which
    belongs to no room and so leads with nothing (`views.candidates`).
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
        vol.Optional("revision"): int,
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
    if host is None or _stale(connection, msg, host):
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
    await _saved(host)
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

    Installing reads the manifest, the catalog's slot vocabulary and the house
    schema, so it is an executor job -- the same work `ws_module_uninstall`
    describes, and the same reason. `partial` carries the keywords, because
    `async_add_executor_job` forwards positional arguments only.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    path = _pack_path(hass, host, msg["pack"])
    if path is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    try:
        await hass.async_add_executor_job(
            partial(
                live_modules.install,
                host.session,
                path,
                room_id=msg["room_id"],
                root=host.session.root,
            )
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await _saved(host)
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

    The removal runs in an executor because it is not a list operation: dropping
    a pack makes the engine re-check every remaining pack's dependencies, and
    that reads the catalog -- `catalog/slots.yaml` and the house schema -- which
    is a file read on the event loop, and Home Assistant named it as one.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        await hass.async_add_executor_job(
            live_modules.uninstall, host.session, msg["pack"]
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await _saved(host)
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
    await _saved(host)
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
    await _saved(host)
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
    await _saved(host)
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_SET_BEHAVIOUR_PRIORITY,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
        vol.Required("behaviour"): str,
        # `int` and not `vol.Coerce(int)`: coercing would accept `"7"` and `7.0`
        # and refuse neither, and the file format this mirrors refuses both
        # (`engine/behaviours/declared.py`'s `_priority` takes an `int`). A panel
        # that posts a string is a panel sending the wrong thing.
        vol.Required("priority"): int,
    }
)
@websocket_api.async_response
@_admin
async def ws_module_set_behaviour_priority(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Rank one behaviour of a pack, so two modules can settle a disagreement.

    The number beside each atom, which arbitration sorts by when two behaviours
    propose for one device in one tick. It is a preference about *this* house --
    the pack declares a rank, and its owner is the only one who knows which of
    two rival modules should win here -- so it is stored with the house settings
    and survives a restart (`ha_adapter.live_modules.set_behaviour_priority`).
    Admin-only like the switch beside it: it changes what the house will do next.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_modules.set_behaviour_priority(
            host.session,
            room_id=msg["room_id"],
            pack=msg["pack"],
            behaviour=msg["behaviour"],
            priority=msg["priority"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await _saved(host)
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_SET_SLOT,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
        vol.Required("slot"): str,
        # The entity, or `None` to clear the override and fall back to the room's
        # binding. `vol.Any(str, None)` rather than an optional key, so the two
        # acts -- "point it here" and "put it back" -- are one command with one
        # shape, exactly as `open_house/rooms/unbind` is `rooms/bind` with a
        # nothing in it. The panel sends an explicit `null` for the reset.
        vol.Required("entity_id"): vol.Any(str, None),
        # The name this module calls the slot, or `None` to fall back to the
        # slot's own. Display-only: nothing in the engine reads it.
        vol.Required("label"): vol.Any(str, None),
        # Which part of a split slot this module is on
        # (`ha_adapter.slot_parts`), or `None` for **not touched**: the panel's
        # reset clears the *device* override, and a person who has just put this
        # module on part `a` has not asked for it to be taken off that part by
        # resetting the device. An empty string is the slot itself, which is the
        # answer the "which part" control's blank entry sends.
        vol.Required("part"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@_admin
async def ws_module_set_slot(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Point one of a module's slots at a device of its own, and name it.

    The per-module override: a person may have one module act on a lamp of its
    own while the room's binding stays what every other module acts on. All three
    -- the device, the name, and which *part* of a split slot the module is on --
    are settings, so all three survive a restart and take effect at once
    (`ha_adapter.live_modules.set_slot`). Admin-only like the switches beside it:
    it changes which device the house will write to.

    **The modules reaching the slot are built again, and that is the half that
    used to be missing.** The engine has always honoured the override when it
    evaluates a pack, but a *hosted* module's own automation is built from the
    room's bindings (`host.bound_slots_for`), so without this the row said one
    device and the automation used another -- a control that lies. Any write that
    moves a slot has to be followed by the same rebuild a rebinding does.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    # **Read before the write.** The reply is the module's row, and a `pack` the
    # house does not hold is a `not_found` -- but asking *after* the write means
    # a corner nobody can reach on purpose (the pack gone between the check and
    # the reply) would refuse a slot override that has already landed. Asked
    # first, the refusal precedes the write and the two cannot disagree. The
    # value read is a row of the installed list, which `set_slot` does not add to
    # or take from, so this is the same row the write is about.
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    try:
        live_modules.set_slot(
            host.session,
            room_id=msg["room_id"],
            pack=msg["pack"],
            slot=msg["slot"],
            entity_id=msg["entity_id"],
            label=msg["label"],
            part=msg["part"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await _rebuilt(host, msg["room_id"], msg["slot"])
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULE_SET_SLOT_RULE,
        vol.Required("room_id"): str,
        vol.Required("pack"): str,
        vol.Required("slot"): str,
        # The kind, or `""` to take the rule back off the row. Empty rather than
        # `null` for the same reason the switches use it: the *absence of a kind*
        # is what "no rule" means (`ha_adapter.slot_rules`), so clearing is the
        # same one write as setting, with nothing in it.
        vol.Required("kind"): str,
        # Whatever that kind needs, in the shape it stores: a template's text, a
        # condition's builder config (a mapping or a list of them), a flow's own
        # entity id, a script's id. One key rather than four, because only the
        # kind's own entry is ever read and the four shapes are the four kinds'
        # business -- `slot_rules.rule_from` is where they are told apart.
        vol.Required("value"): vol.Any(str, dict, list, None),
        # What should start a *script* rule: a script runs when something calls
        # it, and nothing here can work out what that should be. Empty for the
        # other three, which find their own events.
        vol.Required("when"): [str],
        # The device a *condition* rule gates. A condition answers yes or no and
        # never an entity, so this is what it is a question *about*, and it is
        # required for that kind alone.
        vol.Required("device"): vol.Any(str, None),
    }
)
@websocket_api.async_response
@_admin
async def ws_module_set_slot_rule(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put logic on one of a module's slots, instead of a device of its own.

    The "Set it to" half of a slot row, and the sibling of `set_slot`: that one
    says *which* device the module reaches through the slot, and this one says the
    device is to be worked out -- by a template, a script, a flow, or a condition
    that decides whether it is used at all (`ha_adapter.slot_rules`).

    **The rule is written here and *decided* elsewhere.** This command records the
    logic and rebuilds the modules reaching the slot; `custom_components
    .open_house.slot_rules` is what listens for the world moving and writes the
    device the logic worked out. The two are separate because a slot is a standing
    fact rather than a run: nothing in this call can know when a template's
    entities will next change, and the watcher is started before any of this can
    be clicked.

    Deliberately *not* carrying a revision, matching `MODULE_SET_SLOT` beside it
    and for the same reason: a slot's device is a setting the running house applies
    at once and the panel reads back, not a document whose staleness has to be
    detected. `/stale-page-revision-guard` is about a page holding a *house* that
    a profile switch replaced, and neither of these two commands can do that.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    # Read before the write, for the reason `ws_module_set_slot` gives: a
    # `not_found` for a pack this house does not hold has to precede the write it
    # would otherwise be refusing after the fact.
    installed = _installed(host, msg["pack"])
    if installed is None:
        connection.send_error(msg["id"], NOT_FOUND, f"no pack called {msg['pack']!r}")
        return
    try:
        live_modules.set_slot_rule(
            host.session,
            room_id=msg["room_id"],
            pack=msg["pack"],
            slot=msg["slot"],
            kind=msg["kind"],
            value=msg["value"],
            when=msg["when"],
            device=msg["device"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    # The save, the rebuild, and the *timing*: writing a rule takes the slot's
    # device override away from the person (`set_slot_rule`), so the modules
    # reaching this slot have to be built again now -- on the room's binding --
    # rather than left acting on a device the rule has not decided about yet. The
    # watcher's first evaluation then moves them, seconds later at the most. The
    # rebuild is a follow-up (`_rebuilt`), so its failure is noted rather than
    # reported as the rule not having been written.
    await _rebuilt(host, msg["room_id"], msg["slot"])
    connection.send_result(msg["id"], installed)


@websocket_api.websocket_command(
    {
        vol.Required("type"): SLOT_SET_PARTS,
        # The slot being split, by its binding key: `light_group`, or
        # `fridge_guard__fridge_contact` for a pack's own device. A key and not a
        # room-scoped name, because the record is the *house's* -- a part is a word
        # the whole house carries, and the same split is what every room's page
        # draws.
        vol.Required("slot"): str,
        # What to do to it. One command rather than three because the three are
        # one control -- a row of parts with an add, a rename and a delete -- and
        # the refusals below are about the part's *use*, which is one question.
        vol.Required("action"): vol.In(["add", "rename", "remove"]),
        # The part's name, which is what the row shows and what a module names.
        vol.Required("name"): str,
        # The new name, for `rename` alone. Empty for the other two, which do not
        # have one.
        vol.Optional("new_name", default=""): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_slot_set_parts(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Split a slot into parts, or rename or rejoin one.

    The control a person uses to make two modules share a role *without* sharing
    the device -- and the one place a part is made at all
    (`ha_adapter.slot_parts` says why a part is a binding key rather than a
    per-module device).

    **A part's name is the key it binds under, so this edits the house's
    vocabulary.** Adding one is a change to what the house can hold, which is why
    it goes through the session (`host.async_set_slot_part` ->
    `LiveSession.set_slot_parts`) and rebuilds; a setting layer alone could not do
    it, because `engine/binding.py` refuses a binding for a name the vocabulary
    does not carry.

    Admin-only like the module commands beside it: a part moves which device
    automations act on. Deliberately *not* carrying a revision, matching
    `MODULE_SET_SLOT` and for the same reason (`ws_module_set_slot_rule` says
    why): a part is a setting the running house applies at once, not a document
    whose staleness has to be detected.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        parts = await host.async_set_slot_part(
            parent=msg["slot"],
            action=msg["action"],
            name=msg["name"],
            new_name=msg["new_name"],
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    connection.send_result(
        msg["id"],
        {
            "slot": msg["slot"],
            "parts": [{"name": name} for name in parts.get(msg["slot"], ())],
        },
    )


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
    """Every profile the house holds, and the revision they were read at.

    The revision comes with the list because this is the screen that switches a
    house profile: it is the one place that knows a switch has happened, and the
    number is what any page it has open in another tab is checked against. See
    `_stale`.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(
        msg["id"],
        {
            "profiles": list(live_profiles.profiles(host.session)),
            "revision": host.session.revision,
        },
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
    await _saved(host)
    _detail(connection, msg, hass, host, msg["room_id"])


@websocket_api.websocket_command(
    {vol.Required("type"): PROFILE_ACTIVATE_HOUSE, vol.Required("profile"): str}
)
@websocket_api.async_response
@_admin
async def ws_profile_activate_house(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put the house on a house profile. A settings change, not an actuation.

    A door of its own rather than a room id the room path would have to invent:
    a house profile is not selected *in* a room, it is the thing that selects a
    profile for each room at once, and `ProfileSet.select` refuses it by design.

    A profile that was *written* by hand is the whole of it: its deltas resolve
    over the house and its selections are made, both inside
    `live_profiles.activate_house`. One that was *taken* from a house carries
    that house -- its rooms, its modules and its stores -- and putting it back is
    the second call, which is the host's because three of the four places a house
    is kept are Home Assistant's rather than the session's.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        live_profiles.activate_house(host.session, profile=msg["profile"])
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    # **The move is written down before the house is put back.** Putting a taken
    # profile back is a config-subentry write per room, and a subentry write is
    # what makes Home Assistant reload the entry -- so the store has to be right
    # *before* the first of them, not after the last. Saved afterwards, a reload
    # that lands in the window reads a store still naming the profile the house
    # was on a moment ago, composes the house from that, and the activation is
    # undone by the act of performing it. This is the same rule, and the same
    # reason, as `async_set_slot_parts`'s "the record is saved before any room is
    # written"; `__init__._async_reload_entry` is the other half of it, which
    # skips the reload altogether while the restore is in flight.
    await _saved(host)
    held = host.session.profiles.profile(msg["profile"])
    if held.snapshot:
        # The profile is on; putting its house back is the follow-up, and its
        # failure is noted rather than raised for the reason `_bind` gives: by
        # the time this runs the session has already been put on the profile, so
        # that has landed and the answer has to say so. It refused the *whole*
        # call before, which meant a panel was told the activation failed while
        # the house was already on the new profile -- and the panel, taking the
        # error at its word, drew no refresh, so its own screen still showed the
        # *old* profile in force. Both halves of that are wrong at once: the
        # person sees an error and an unchanged screen over a house that has
        # changed.
        #
        # The refusal is real and worth reading -- a snapshot names the parts and
        # devices the house had when it was taken, and a part since removed is
        # not in this house to bind -- so it is written to the log with the
        # profile it was about.
        try:
            await host.async_restore_setup(held)
        except Exception as refusal:
            _unsettled(f"the profile {msg['profile']!r}", refusal)
    await _saved(host)
    connection.send_result(
        msg["id"], {"profiles": list(live_profiles.profiles(host.session))}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): PROFILE_CAPTURE, vol.Required("name"): str}
)
@websocket_api.async_response
@_admin
async def ws_profile_capture(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Take a profile from the house: the whole house, named.

    The other way a profile comes to exist, and the half the panel never had: a
    profile could be written as a pack's data or read back out of a file, and
    neither is something a person with a house in front of them can do. This
    makes one out of the house they have -- the packs installed, the rooms and
    what each answers with, every module hosted with the configuration it is on,
    and the settings of the house and of each room.

    The hosted modules are read here and handed in, because they are a file
    rather than anything the session holds: a module is the integration's, and
    the session knows only where its pack was placed.

    The house then goes on it, which for a profile taken from that very house is
    no change at all -- and is the point: "this is what my house is" is a naming
    act, and the name is what they switch away from and back to later.

    Nothing here resolves a profile. What the house is travels to the document
    the way the house keeps it, and it is `activate_house` that writes it back.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        name = _profile_name(msg["name"])
        records = await modules.async_records(hass)
        live_profiles.capture(
            host.session,
            name=name,
            description=_taken_note(),
            modules=[record.as_json() for record in records],
        )
        live_profiles.activate_house(host.session, profile=name)
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await _saved(host)
    connection.send_result(
        msg["id"], {"profiles": list(live_profiles.profiles(host.session))}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PROFILE_RENAME,
        vol.Required("profile"): str,
        vol.Required("to"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_profile_rename(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Rename a profile, keeping every room that is on it on it.

    A rename and not a remove-and-add, which is the difference a person would
    feel: a removal takes every selection that named the profile off with it, so
    the house would come out of a rename with its rooms on nothing.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        host.session.profiles.rename(msg["profile"], _profile_name(msg["to"]))
    except LiveSessionError as refusal:
        # **The name is read inside this call, and it refuses in the session's
        # words, not the profile set's.** `_profile_name` raises `LiveSessionError`
        # -- the *other* sibling `_as_session_error` exists to join to
        # `ProfileError` -- and catching only `ProfileError` let it through: a
        # `to` of `"!!!"` reached the panel as `ERR_UNKNOWN_ERROR` with a stack
        # trace instead of the sentence `_profile_name` wrote for a person to
        # read. Both types are caught now, the name's own refusal reported as it
        # was written and the set's run through `_as_session_error` as before.
        _error(connection, msg, refusal)
        return
    except ProfileError as refusal:
        _error(connection, msg, _as_session_error(refusal))
        return
    host.reload_session()
    await _saved(host)
    connection.send_result(
        msg["id"], {"profiles": list(live_profiles.profiles(host.session))}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): PROFILE_REMOVE, vol.Required("profile"): str}
)
@websocket_api.async_response
@_admin
async def ws_profile_remove(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Drop a profile, and take everything that named it off with it.

    The rule is the engine's (`ProfileSet.remove`): a selection names a profile,
    so removing the profile removes the selections rather than leaving a room
    pointing at a name the set no longer holds. A house profile in force is
    released, and the room selections it set stay -- the same "off is not put
    everything back" the deactivate door spells.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        host.session.profiles.remove(msg["profile"])
    except ProfileError as refusal:
        _error(connection, msg, _as_session_error(refusal))
        return
    host.reload_session()
    await _saved(host)
    connection.send_result(
        msg["id"], {"profiles": list(live_profiles.profiles(host.session))}
    )


def _as_session_error(refusal: ProfileError) -> LiveSessionError:
    """A profile refusal as the session's one failure type. See `live_profiles`."""
    return LiveSessionError(str(refusal))


def _profile_name(raw: str) -> str:
    """A profile name made out of the words a person typed.

    Lossy on purpose and in the open, exactly as a module's name is: the name is
    the identifier every selection is made by and the schema has its own pattern
    for what an identifier may hold, so "Christmas Lights" is `christmas_lights`
    and the label a person reads is that name humanised back.

    The slug rule is `module_records.slug`'s and not a second copy of it -- a
    name that is not a name is refused in the same place by the same rule -- and
    only the refusal is reworded, because a person calling a profile "!!!" should
    be told what is wrong with a *profile* name.
    """
    try:
        return module_records.slug(raw)
    except pack_authoring.AuthoringError as refusal:
        raise LiveSessionError(
            f"{raw!r} is not a name a profile can be called: a profile name is "
            "lower case letters, digits and underscores, beginning with a letter"
        ) from refusal


def _taken_note() -> str:
    """What a taken profile says it is, in the list of them.

    A date and not just a sentence, because the one question a person asks of two
    profiles taken a week apart is which is which.
    """
    return f"What this house was on and set to on {dt_util.now().date().isoformat()}."


@websocket_api.websocket_command({vol.Required("type"): PROFILE_DEACTIVATE_HOUSE})
@websocket_api.async_response
@_admin
async def ws_profile_deactivate_house(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Take the house off its house profile, leaving the room selections it set."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    live_profiles.deactivate_house(host.session)
    await _saved(host)
    connection.send_result(
        msg["id"], {"profiles": list(live_profiles.profiles(host.session))}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): PROFILE_EXPORT, vol.Optional("profile"): str}
)
@websocket_api.async_response
@_admin
async def ws_profile_export(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """One profile, or every profile, as a document a person can keep or share.

    `profile` is absent for the whole set, which is the "export all profiles"
    half of the feature; naming one is "export this profile". Both answer the
    frozen `schemas/profile/` shape -- a set document is a list of them -- so the
    importer has one form to read.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        document = live_profiles.export_document(
            host.session, profile=msg.get("profile")
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    connection.send_result(msg["id"], {"document": document})


@websocket_api.websocket_command(
    {
        vol.Required("type"): PROFILE_IMPORT,
        vol.Required("document"): dict,
        vol.Optional("replace", default=False): bool,
    }
)
@websocket_api.async_response
@_admin
async def ws_profile_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add the profiles a document names, then write down what the session became.

    The store is written *after* the import rather than as part of it, because
    the import is the session's own act and this module's job is to make it
    survive a restart. A refusal is reported before anything is written, and the
    importer validates every profile before applying any of them, so a bad file
    leaves no half-imported set on either side of that seam.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        result = live_profiles.import_document(
            host.session, document=msg["document"], replace=msg["replace"]
        )
    except LiveSessionError as refusal:
        _error(connection, msg, refusal)
        return
    await _saved(host)
    connection.send_result(msg["id"], result)


# -- Activity ---------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): ACTIVITY_LIST,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=_ACTIVITY_LIMIT)),
        vol.Optional("before"): str,
        # The tab's two filters, applied by the server *before* the limit so a
        # filtered page fills. `outcome` is the panel's vocabulary plus
        # `live_export.ACTIONS`; `room` is a room id.
        vol.Optional("outcome"): str,
        vol.Optional("room"): str,
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
        host.session,
        limit=msg.get("limit", 50),
        before=msg.get("before"),
        outcome=msg.get("outcome"),
        room=msg.get("room"),
    )
    connection.send_result(msg["id"], {"entries": list(found)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): ACTIVITY_SUBSCRIBE,
        vol.Optional("limit"): vol.All(int, vol.Range(min=1, max=_ACTIVITY_LIMIT)),
        vol.Optional("outcome"): str,
        vol.Optional("room"): str,
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

    connection.subscriptions[identifier] = host.subscribe(
        _forward, outcome=msg.get("outcome"), room=msg.get("room")
    )
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


# -- Dev: what may be imported ----------------------------------------------
#
# One command: `sources` lists the automations and blueprints a person may
# import. Naming them is all it does -- the import screen reads one of them with
# `modules/read` and hosts it with `modules/host`, because reading a source for
# its inputs is a different act from listing the sources there are.


@websocket_api.websocket_command({vol.Required("type"): DEV_SOURCES})
@websocket_api.async_response
@_admin
async def ws_dev_sources(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What may be imported.

    Deliberately answerable with no house: a person who has automations but has
    not finished the setup flow can still look at what their automations would
    become, and the one screen that explains what is missing is the one this
    command serves. Naming a source needs only Home Assistant and not the
    engine's vocabulary, which is why it needs no house.
    """
    connection.send_result(
        msg["id"],
        {
            "automations": [dict(row) for row in dev_authoring.automations(hass)],
            "blueprints": [dict(row) for row in await dev_authoring.blueprints(hass)],
        },
    )


# -- Hosting a source as a module -------------------------------------------
#
# Three commands, and they are the whole of "turn a blueprint into a module":
# `hosted` lists what the house already runs, `read` answers what one source can
# be made into, and `host` does it. They are separate from `dev/sources` on
# purpose: that one only names the sources there are, and reads no document.


@websocket_api.websocket_command({vol.Required("type"): MODULES_HOSTED})
@websocket_api.async_response
@_admin
async def ws_modules_hosted(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every module this house hosts, what it publishes, and what it last read.

    The last published value is included because it is the question a person
    actually has about a module's outputs: "is it publishing anything, and
    what". An output that has never been written is `null` rather than absent, so
    the panel can show the output and say it is unknown instead of hiding the row
    a person is looking for.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    connection.send_result(msg["id"], await _hosted(hass, host))


async def _dev_text(hass: HomeAssistant, msg: Mapping[str, Any]) -> str:
    """The source a `modules/*` command names, as text.

    `text` is the panel's paste box and is taken as given; the other two kinds are
    read from Home Assistant. Keeping all three behind one function is what makes
    "a paste, a file and a picker are the same thing to the importer" true rather
    than aspirational.
    """
    kind = str(msg.get("kind"))
    if kind == "text":
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            raise pack_authoring.AuthoringError("there is nothing pasted to read")
        return text
    key = msg.get("key")
    if not isinstance(key, str) or not key:
        raise pack_authoring.AuthoringError(f"no {kind} was named to read")
    return await dev_authoring.source_text(hass, kind, key)


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_READ,
        vol.Required("kind"): vol.In(("automation", "blueprint", "text")),
        vol.Optional("key"): str,
        vol.Optional("text"): str,
        # The person's current input choices, so the candidates offered are the
        # ones this house could really fill: an entity input's reading is over
        # the entity they bound, and an unbound one is not offered at all.
        vol.Optional("bindings", default=dict): dict,
        # The rows the screen has answered with *logic* -- a condition, a flow or
        # an automation -- for the same reason the bindings are sent: what a row
        # can publish is asked of the answer it holds, and a cast-answered row is
        # one more thing a person may publish. A **template** cast is not here,
        # because it *is* a binding and arrives in `bindings` above.
        #
        # Sent for all three or for none. A screen that has read once sends its
        # own lists -- which may be empty, because a person who has just switched
        # their only cast row back to a value means exactly that -- and a screen
        # that has not read yet sends nothing, so the module's own record stands
        # (the same fallback `bindings` makes, and for the same reason).
        vol.Optional("casts", default=None): vol.Any(None, [str]),
        vol.Optional("flows", default=None): vol.Any(None, [str]),
        vol.Optional("automations", default=None): vol.Any(None, [str]),
        # **Reading a module this house already runs, rather than a new source.**
        # Naming one here is what opens the import screen *on* a module: the
        # document is the module's own rather than anything sent, and the reply
        # carries back everything that was decided about it, so the same menu a
        # person sees importing a blueprint is drawn over a module that is already
        # installed (`modules._module_to_edit`).
        vol.Optional("module"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_read(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Read one source as something to host, and answer every decision it allows.

    Three answers in one reply, because the import screen is one screen: what the
    document asks for (`inputs`), what it could publish (`candidates`), and what
    the house already has that could fill those inputs (`hosted`). Fetching the
    last of those separately would make the binding list and the module list two
    answers about one house, which is the drift the panel's protocol exists to
    avoid.

    Naming a `module` reads that module instead -- the same screen, opened on
    what is already installed rather than on a blueprint nobody has answered --
    and the reply then also carries the module's own document and every answer
    that was given about it.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    editing: Mapping[str, Any] | None = None
    if msg.get("module"):
        try:
            editing = await _module_to_edit(hass, host, str(msg["module"]))
        except modules.ModuleHostError as refusal:
            connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
            return
    try:
        # A module carries its own document, so the screen is handed *that*
        # rather than anything the panel sent: a copy taken on the way out would
        # be a second version of the module, and the one being edited would be
        # whichever of the two the next read happened to reach.
        text = (
            str(editing["text"]) if editing is not None else await _dev_text(hass, msg)
        )
        source = module_host.read_module_source(text)
    except pack_authoring.AuthoringError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    answers = _bindings(msg.get("bindings")) or (
        _bindings(editing["bindings"]) if editing is not None else {}
    )
    if editing is not None:
        # **A cast and a flow are answers too**, and they are the two that are
        # not rows in `bindings`: a condition travels as the entity this
        # integration makes for it and a flow as the entity Node-RED writes, and
        # that is exactly what `_async_build` binds the input to. Bound here, so
        # the row reads as answered -- which is what it is, and a screen that
        # showed a module's own conditions as unfilled ones would be asking a
        # person to answer what they already had.
        for name in editing["casts"]:
            answers[name] = module_host.InputBinding(
                kind="entity",
                value=module_host.derived_entity_id(str(editing["module"]), name),
            )
        for name in editing["flows"]:
            answers[name] = module_host.InputBinding(
                kind="entity",
                value=module_host.flow_entity_id(str(editing["module"]), name),
            )
    # Slots are resolved to nothing here rather than to a device, because a
    # reading happens before the module has been given a room: what the person
    # has said is which *slot* an input wants, and what that slot answers is not
    # known yet. So the reading is the source with those inputs left as the
    # blueprint defaults them -- which is what the automation will be built with
    # if the slot is never bound, and is therefore honest about the choices made
    # so far. `modules/host` is where a slot becomes a device.
    chosen = module_host.bind_inputs(source, module_host.resolve_slots(answers, {}))
    # Which rows hold logic rather than a value, so each of them can be offered
    # as something the module publishes -- "expose it, and any automation in the
    # house can read it". Read here rather than in the candidate loop below so
    # the two answers about one screen (what the row is, what it may publish)
    # come off one reading of the same question.
    casts = module_host.cast_answers(
        answers,
        conditions=_cast_rows(msg, editing, "casts"),
        flows=_cast_rows(msg, editing, "flows"),
        automations=_cast_rows(msg, editing, "automations"),
    )
    # The roles the house itself answers, which is the set an input may be
    # answered with at *global* scope (`module_host.HOUSE_SCOPE`). Read from the
    # engine rather than from the catalog's house menu: the document's
    # `house_scope` clause is what the engine resolves a house scoped slot
    # against, so it is the clause that decides whether answering globally means
    # anything at all.
    house_scope_slots = frozenset(host.session.engine.house.house_scope_slots)
    connection.send_result(
        msg["id"],
        {
            "source": {
                "title": source.title,
                "description": source.description,
                "blueprint": str(msg.get("key") or "")
                if msg.get("kind") == "blueprint"
                else "",
                "inputs": len(source.inputs),
            },
            # The document itself, and only when a module was named: it is what
            # the screen will send back on save, and it can be a whole blueprint
            # -- so it travels once, on the reading that is *about* a module,
            # rather than riding along with every listing of the house.
            **({"text": text, "editing": editing} if editing is not None else {}),
            "inputs": [
                {
                    **_module_input(name, block, chosen, source),
                    # What the module already answers this input with, when the
                    # screen is open on one. Both are read off the seed rather
                    # than the record so the row and the save agree about the
                    # same input even where the definition is the newer half.
                    **_cast_ids(editing, name, block),
                }
                for name, block in source.inputs.items()
            ],
            "candidates": [
                {
                    "name": candidate.name,
                    "kind": candidate.kind,
                    "value_kind": candidate.value_kind,
                    "expression": candidate.expression,
                    "branch_only": candidate.branch_only,
                    "suggested_key": _suggested_key(candidate.name),
                }
                for candidate in module_host.output_candidates(
                    source, chosen, answers, casts
                )
            ],
            # Each role says whether it is one the *house* answers, because
            # naming a slot in an import is naming where it is looked up and not
            # only what it is called: a role the house document declares at house
            # scope is the house's device in every room, so an input answered
            # with it can be answered with *that* one rather than with whatever
            # the module's room bound for the same name
            # (`module_host.HOUSE_SCOPE`). The engine's own list is the authority
            # -- any other role has no global binding it would ever resolve, so
            # offering one here would be offering a promise the build cannot
            # keep.
            "slots": [
                {
                    "name": name,
                    "label": _slot_label(name),
                    "house_scope": name in house_scope_slots,
                }
                for name in host.known_slots()
            ],
            # The licences a module may carry, for the step that saves one: the
            # screen offers them as a picker rather than restating them, for the
            # reason it offers the slot names -- one vocabulary, one spelling.
            "licences": list(module_definitions.LICENCES),
            "rooms": [
                {"id": room.id, "name": room.name} for room in host.session.rooms
            ],
            "hosted": (await _hosted(hass, host))["modules"],
        },
    )


def _cast_ids(
    editing: Mapping[str, Any] | None, name: str, block: Mapping[str, Any] | None = None
) -> Mapping[str, str]:
    """The flow and the automation a row is answered by, for a screen open on a
    module.

    Both, and not only the flow: the row opens whichever of the two editors the
    answer belongs to, and which one that is is a fact about the answer rather
    than about the input -- so a row answered by an automation has no flow to
    open and a row answered by a flow has no automation. Empty for a fresh
    import, where there is no module yet and nothing to have answered.

    The automation's *helper* rides along with its id, because the row's editor
    says which entity the person's action has to set -- and the helper's id is
    the module's slug plus the input's name in the row's own selector kind, which
    is the row's block to say (`module_host.helper_entity_id`).
    """
    if editing is None:
        return {}
    slug = str(editing.get("module") or "")
    helper = ""
    if slug and block is not None:
        # **Guarded, because an input name is a blueprint's to spell.** A name
        # that is not a module-key shape is a name no helper can be minted for,
        # and that is a refusal the *save* makes for the rows this cast actually
        # answers -- not a reason for the screen to fail to read at all. So a name
        # like that simply has no helper to show, and its row says the automation
        # is pending.
        with contextlib.suppress(pack_authoring.AuthoringError):
            helper = module_host.helper_entity_id(slug, name, dict(block))
    return {
        "flow_id": str((editing.get("flows") or {}).get(name, "")),
        "automation_id": str((editing.get("automations") or {}).get(name, "")),
        "automation_entity": helper,
    }


def _cast_rows(
    msg: Mapping[str, Any], editing: Mapping[str, Any] | None, field: str
) -> tuple[str, ...]:
    """Which inputs a screen has answered with one kind of cast, by kind.

    **The screen where it has said anything, the module's record otherwise.** The
    two are not merged, and that is the point: a person who switches their only
    cast row back to a value means it, and a list unioned with the record would
    keep offering that row's value as something to publish -- a tick that then
    reached a build where the cast is gone, which is a refusal at the far end of
    a screen that looked settled.

    Saying nothing is not the same as saying "none", which is why the fields are
    absent rather than empty on a screen that has not read yet: on the first
    reading of an edit there are no rows to have an opinion about, and the
    module's own answers are the whole truth about what it publishes.
    """
    sent = msg.get(field)
    if sent is not None:
        return tuple(str(name) for name in sent)
    if editing is None:
        return ()
    return tuple(str(name) for name in (editing.get(field) or {}))


def _slot_label(name: str) -> str:
    """A slot name as a person reads it: `ambient_light_sensor` -> `Ambient light sensor`.

    The house's slot names are written the way an engine reads them -- lower case
    with underscores -- and a dropdown offering them that way makes a person
    decode `ambient_light_sensor` to find the lux one. Capitalising the first
    word and opening the underscores out is the whole of the difference, and it
    is done here rather than in the panel so that one list has one spelling.
    """
    words = name.split("_")
    return " ".join([words[0].capitalize(), *words[1:]]) if words else name


async def _module_to_edit(
    hass: HomeAssistant, host: OpenHouseHost, module: str
) -> Mapping[str, Any]:
    """One hosted module as the import screen's own starting point.

    **The screen the card's Edit opens is the import screen**, and the whole of
    what makes it an edit is where it starts from: the module's own document
    rather than a blueprint nobody has answered, and the answers that were given
    about it rather than nothing. Both come from the store row it was made from
    where there is one -- a module *is* what the house offers, and a room's copy
    of it is one installation of it -- and from the record itself for a document
    hosted directly, which is a module with no row behind it.

    `installs` is the half that has to be said out loud: pressing Save changes
    the module, which is every room running it, and a person about to do that is
    owed the count. Each row is a room a screen can name rather than an id,
    because that is how the card that got them here names rooms too.
    """
    records = await modules.async_records(hass)
    record = next((row for row in records if row.slug == module), None)
    if record is None:
        raise modules.ModuleHostError(
            f"this house hosts no module called {module!r}, so there is nothing to edit"
        )
    reaches = (
        [row for row in records if row.definition == record.definition]
        if record.definition
        else [record]
    )
    if record.definition:
        definition = await modules.async_definition(hass, record.definition)
        document: Mapping[str, Any] = {
            "text": definition.source,
            "title": definition.title,
            "description": definition.description,
            "author": definition.author,
            "version": definition.version,
            "licence": definition.licence,
            "blueprint": definition.blueprint,
            "bindings": {name: dict(row) for name, row in definition.bindings.items()},
            "settings": list(definition.settings),
            "casts": dict(definition.derived),
            # *Names* off the definition, because which inputs this module answers
            # by a flow is the module's -- and the *ids* from the installation in
            # front of the person, because the id is the Node-RED that installed
            # it. The screen shows a flow row per name and opens the flow the id
            # names (`module_definitions.follow`).
            "flows": {name: record.flows.get(name, "") for name in definition.flows},
            # The same two halves for an automation: the names are the module's,
            # the id is the house's, and the row opens the person's own
            # automation.
            "automations": {
                name: record.automations.get(name, "")
                for name in definition.automations
            },
            "picks": [{"name": name, "key": key} for name, key in definition.picks],
        }
    else:
        document = {
            "text": record.source,
            "title": record.title,
            "description": "",
            "author": "",
            "version": "1.0.0",
            "licence": "no_licence",
            "blueprint": record.blueprint,
            "bindings": {name: dict(row) for name, row in record.bindings.items()},
            "settings": list(record.settings),
            "casts": dict(record.derived),
            "flows": dict(record.flows),
            "automations": dict(record.automations),
            "picks": [{"name": name, "key": key} for name, key in record.picks],
        }
    return {
        **document,
        "module": record.slug,
        "definition": record.definition,
        "installs": [
            {
                "slug": row.slug,
                "room_id": row.room_id,
                "room_name": _room_name(host, row.room_id),
            }
            for row in reaches
        ],
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_HOST,
        vol.Required("kind"): vol.In(("automation", "blueprint", "text")),
        vol.Required("title"): str,
        vol.Optional("key"): str,
        vol.Optional("text"): str,
        # Where the module sits. Empty is the whole house, which is where a
        # module with no room of its own resolves its slots.
        vol.Optional("room_id", default=""): str,
        vol.Optional("bindings", default=dict): dict,
        # The candidates the person ticked, each with the key they called it.
        vol.Optional("outputs", default=list): list,
        # The inputs they ticked to keep settable on the module.
        vol.Optional("settings", default=list): list,
        # The inputs answered with a *condition* rather than with a value, each
        # as Home Assistant's own condition config. Kept apart from `bindings`
        # because it is a different kind of thing: a binding is a value, and a
        # condition is logic that Open House has to make an entity out of before
        # anything can be bound to it (`modules.async_host`).
        vol.Optional("casts", default=dict): dict,
        # The inputs answered by a *flow of nodes* in Node-RED, by name. The
        # third kind of answer beside a binding and a condition: a flow is not a
        # value and not logic Open House evaluates, it is logic another program
        # runs, writing into an entity this integration makes for it
        # (`modules._async_push_flows`).
        vol.Optional("flows", default=list): list,
        # The inputs answered by a *Home Assistant automation*, by name. The
        # fourth kind of answer: the automation runs on its own and writes a
        # helper the row reads, so it is a flow's twin -- Open House makes the
        # helper and the seeded automation, which is why a name is enough and the
        # id is this house's to fill in (`modules._async_seed_automations`).
        vol.Optional("automations", default=list): list,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_host(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Host one source as a module, and answer with the house it landed in.

    The whole reply rather than the one new module: hosting changes what a
    consumer may bind to, so every screen that lists modules or offers a binding
    is stale the moment this returns, and answering with the full list is what
    lets the panel redraw from the answer it already has instead of asking again.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        text = await _dev_text(hass, msg)
    except pack_authoring.AuthoringError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    room_id = str(msg.get("room_id") or "")
    if room_id and host.session.room(room_id) is None:
        connection.send_error(
            msg["id"], INVALID_FORMAT, f"there is no room {room_id!r} in this house"
        )
        return
    bindings = _bindings(msg.get("bindings"))
    unknown = _unknown_slot(host, bindings)
    if unknown is not None:
        connection.send_error(msg["id"], INVALID_FORMAT, unknown)
        return
    try:
        record = await modules.async_host(
            hass,
            host.entry.entry_id,
            text=text,
            title=str(msg["title"]),
            blueprint=str(msg.get("key") or "")
            if msg.get("kind") == "blueprint"
            else "",
            room_id=room_id,
            bindings=bindings,
            outputs=_picked(msg.get("outputs")),
            settings=_names(msg.get("settings")),
            bound=host.bound_slots(room_id),
            casts=_casts(msg.get("casts")),
            flows=_names(msg.get("flows")),
            automations=_names(msg.get("automations")),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"], {"module": record.slug, "modules": listing["modules"]}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_SETTINGS,
        vol.Required("module"): str,
        # Only the settings the person changed, or all of them: whatever is sent
        # is merged over what the module already answers, so a form that shows a
        # subset of the inputs cannot clear the rest.
        vol.Optional("bindings", default=dict): dict,
        # Which inputs stay settable, when that is what changed.
        vol.Optional("settings"): list,
        # The condition answers, when a cast is what changed -- including an
        # empty one, which is how a cast comes back off (`modules.async_update`).
        vol.Optional("casts"): dict,
        # Which inputs are answered by a Node-RED flow, by name, when that is
        # what changed. Sent as the whole set rather than as a change to it, for
        # the reason the condition answers are: the screen is showing every row,
        # so what it sends is every row's answer (`modules.async_update`).
        vol.Optional("flows"): list,
        # Which inputs are answered by an automation, by name, when a cast is what
        # changed. Sent as the whole set rather than as a change to it, for the
        # flows' reason: Open House makes the automation, so a name that drops off
        # the set is a cast taken away as well as an answer withdrawn
        # (`modules.async_update`).
        vol.Optional("automations"): list,
        # The revision the card was drawn from. The card saves itself on a timer
        # (`panel/src/components/hosted-module.ts`), so without this a card drawn
        # before a profile switch would write the answers of the house that was
        # on then over the profile that is on now -- the bug this is here for.
        vol.Optional("revision"): int,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Change a hosted module's settings, and answer with the house as it now is.

    The same reply as hosting, for the same reason: the module's automation is
    built again, so a consumer bound to one of its outputs is reading an entity
    that was just re-published, and every screen holding the old module is stale.
    """
    host = _host_or_error(connection, msg)
    if host is None or _stale(connection, msg, host):
        return
    sent = _bindings(msg.get("bindings"))
    unknown = _unknown_part(host, sent)
    if unknown is not None:
        connection.send_error(msg["id"], INVALID_FORMAT, unknown)
        return
    try:
        record = await modules.async_update(
            hass,
            host.entry.entry_id,
            module=str(msg["module"]),
            bindings=sent,
            settings=(
                None if msg.get("settings") is None else _names(msg.get("settings"))
            ),
            bound=host.bound_slots(await _room_of(hass, str(msg["module"]))),
            casts=(None if msg.get("casts") is None else _casts(msg.get("casts"))),
            flows=None if msg.get("flows") is None else _names(msg.get("flows")),
            automations=(
                None
                if msg.get("automations") is None
                else _names(msg.get("automations"))
            ),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"], {"module": record.slug, "modules": listing["modules"]}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_DETACH,
        # The module whose row the cast is on, and the row itself. Both are
        # needed and neither is enough: the *module* is where the logic is kept
        # (the record, not the form), and the *input* is which of its rows the
        # person pressed the button beside.
        vol.Required("module"): str,
        # Which row, and the two spellings are two different rows. An `input` is
        # one of the module's inputs, whose cast lives on the record; a `slot` is
        # one of the module's devices, whose rule lives in the session's settings
        # (`ha_adapter.slot_rules`). Exactly one is named, and the command reads
        # whichever it was given rather than a `where` that could disagree with
        # the name beside it.
        vol.Optional("input", default=""): str,
        vol.Optional("slot", default=""): str,
        # What the new module is called. Empty means the module it came from and
        # the row, which is a name that says where the logic is from and is worth
        # offering rather than requiring somebody to invent one.
        vol.Optional("title", default=""): str,
        # Where the new module sits -- the house, or a room. The person's choice,
        # and the whole of what "a house level module or a module in the same
        # room" means: it is the same placement every other way of adding a
        # module asks for, so it is the same field.
        vol.Optional("room_id", default=""): str,
        # What should start the new module, where the cast cannot name it itself
        # (`cast_document.detached_document`). A template says nothing about when
        # it should run, so for it this is not a refinement -- it is the
        # difference between a module and a module that never runs. A condition, a
        # flow and an automation bring their own, and anything named here is
        # watched *beside* them rather than instead.
        vol.Optional("trigger", default=list): list,
        vol.Optional("revision"): int,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_detach(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Make one row's cast a module of its own, and point the row at it.

    The same reply as hosting and setting, because it is both at once: a module
    appears and a module is built again, so every screen holding either is stale.

    `key` is the output the new module publishes under, sent back beside the
    module's name so the panel can point the row at the answer without working
    out what the row's output key was -- the name comes from the input's name by
    a rule (`cast_document.key_for`) that has no business being spelled twice.

    **Two rows, two keepers.** An input's cast is kept on the module's record, so
    `modules.async_detach` reads it, hosts the module *and* rewrites the record --
    the row's answer becomes an `output` binding inside the same function. A
    slot's rule is kept in the session's settings, which this function holds and
    `modules` does not: the module is hosted there and the rule is cleared and the
    slot pointed back here, in one step with no `await` in it. A slot left holding
    its rule *and* a module publishing it would be the same thing worked out twice,
    which is the half-change `async_detach` exists to make impossible.
    """
    host = _host_or_error(connection, msg)
    if host is None or _stale(connection, msg, host):
        return
    room_id = str(msg.get("room_id") or "")
    if room_id and host.session.room(room_id) is None:
        connection.send_error(
            msg["id"], INVALID_FORMAT, f"there is no room {room_id!r} in this house"
        )
        return
    module = str(msg["module"])
    slot = str(msg.get("slot") or "")
    input_name = str(msg.get("input") or "")
    if bool(slot) == bool(input_name):
        connection.send_error(
            msg["id"],
            INVALID_FORMAT,
            "a detach names the row it is detaching from, and exactly one of them: "
            "an input, or a slot",
        )
        return
    try:
        if slot:
            answer = await _detach_slot(hass, host, module, slot, room_id, msg)
        else:
            record, source, watched = await modules.async_detach(
                hass,
                host.entry.entry_id,
                module=module,
                input_name=input_name,
                title=str(msg.get("title") or ""),
                room_id=room_id,
                trigger=_names(msg.get("trigger")),
                bound=host.bound_slots(room_id),
                # The room of the module the row is on, read from the record
                # rather than sent: the module left behind is built again where it
                # *lives*, which is a fact about the house and not something this
                # screen says.
                source_bound=host.bound_slots(await _room_of(hass, module)),
            )
            answer = {
                "module": record.slug,
                "title": record.title,
                "room_id": record.room_id,
                # The output key, read off the record the detach just made rather
                # than re-derived: the record is what the automation publishes
                # under, so a key read from anywhere else could disagree with it.
                "key": record.outputs[0].key if record.outputs else "",
                "watched": list(watched),
                "source": source.slug,
            }
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    except LiveSessionError as refusal:
        # **A slot detach writes through two layers and either can refuse.**
        # `modules.async_detach_slot` raises `ModuleHostError`, but the two
        # session writes that finish the job (`_detach_slot`: the rule cleared,
        # the slot pointed at the new module) are `live_modules.set_slot_rule`
        # and `set_slot`, and those raise `LiveSessionError`. Catching only the
        # first let a half-applied detach -- the module made, the rule still on
        # the row -- leave the handler as a raw 500 with no sentence in it. This
        # one goes through `_error` rather than a fixed code, because a session
        # refusal already says in its own words which of the two kinds it is.
        _error(connection, msg, refusal)
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(msg["id"], {**answer, "modules": listing["modules"]})


async def _detach_slot(
    hass: HomeAssistant,
    host: OpenHouseHost,
    module: str,
    slot: str,
    room_id: str,
    msg: Mapping[str, Any],
) -> dict[str, Any]:
    """Detach a slot's rule, and leave the slot reading what the module publishes.

    The rule is read from the **session**, not from the screen: the session is
    where a slot rule is recorded (`live_modules.set_slot_rule`) and where the
    device the slot acts on is, so a detach can only ever act on logic the house
    actually holds. A rule typed into a row and not yet set is a rule the house
    does not have, which is the same rule the input path follows ("a cast written
    into a form and not yet saved is a cast the module does not hold").

    The room the rule is recorded under is the **module's** room, not the room the
    new module is being placed in: a slot rule is per module per room, and the one
    being detached is on the row the person pressed the button beside. The two
    differ whenever a detach moves the logic somewhere else, which is the whole
    point of asking where it should sit.
    """
    source_room = await _room_of(hass, module)
    rules = live_modules.slot_rules_of(host.session, pack=module, room_id=source_room)
    rule = rules.get(slot)
    if rule is None:
        raise modules.ModuleHostError(
            f"{slot!r} is a device on this module rather than a rule, so there is "
            "nothing to detach: a condition, a template, a flow or an automation on "
            "the slot is what can become a module of its own"
        )
    record, watched = await modules.async_detach_slot(
        hass,
        host.entry.entry_id,
        module=module,
        slot=slot,
        rule=rule,
        title=str(msg.get("title") or ""),
        room_id=room_id,
        trigger=_names(msg.get("trigger")),
        bound=host.bound_slots(room_id or source_room),
    )
    # The two session writes that finish it, and no `await` between them: the rule
    # goes, and the slot reads the new module's output instead. Both are writes to
    # the recorded layer, so both survive a rebuild and both take effect on the
    # next one -- which the rebuild below is what asks for. It is a follow-up
    # (`_rebuilt`), so a house that will not build again is noted, not reported as
    # a detach that did not happen.
    key = record.outputs[0].key if record.outputs else ""
    live_modules.set_slot_rule(
        host.session, room_id=source_room, pack=module, slot=slot
    )
    if key:
        live_modules.set_slot(
            host.session,
            room_id=source_room,
            pack=module,
            slot=slot,
            # The published entity, spelled by `module_host` so this and the
            # consumer's template cannot disagree about where an output lives.
            entity_id=module_host.output_entity_id(record.slug, key),
            label=None,
        )
    await _rebuilt(host, source_room, slot)
    return {
        "module": record.slug,
        "title": record.title,
        "room_id": record.room_id,
        "key": key,
        "watched": list(watched),
        # The module the rule came from, and the slot it left: the same shape the
        # input path answers with, so a screen holding either goes stale the same
        # way.
        "source": module,
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_EDIT,
        # The module being edited, by the name the *house* hosts it under -- the
        # one on the card the Edit was pressed on. Everything below is the
        # module's own answers, and the server works out from this record which
        # store row the module is and which other rooms are running it.
        vol.Required("module"): str,
        # The same three ways of naming a document the rest of the import path
        # takes, because this is the same screen: a blueprint picked, an
        # automation picked, or text pasted in.
        vol.Required("kind"): vol.In(("automation", "blueprint", "text")),
        vol.Required("title"): str,
        vol.Optional("key"): str,
        vol.Optional("text"): str,
        vol.Optional("description", default=""): str,
        vol.Optional("author", default=""): str,
        vol.Optional("version", default=""): str,
        vol.Optional("licence", default=""): str,
        # **Required, unlike the rest of this schema.** The six below are the
        # module's answers, and there is no reading of an absent one that is not
        # a reading of an empty one -- so a caller who leaves one out would
        # silently take every answer away from a module installed in five rooms,
        # and the refusal it would get instead is the one worth having. The
        # fields above are different: a title nobody sent is a title the module
        # already has.
        vol.Required("bindings"): dict,
        vol.Required("outputs"): list,
        vol.Required("settings"): list,
        vol.Required("casts"): dict,
        vol.Required("flows"): list,
        vol.Required("automations"): list,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_edit(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Edit a hosted module, and rebuild every room running it.

    **The one module command whose subject is the module rather than one copy of
    it.** Every other one -- settings, a configuration, unhosting -- names a
    record, which is one installation in one room. This names a record too,
    because that is what a card on a screen knows, and then follows it back to
    the module behind it: the store row is rewritten, and every installation of
    it is built again from the document and the answers that were just decided.

    The answers are the interesting half and they are
    `module_definitions.follow`'s question -- an answer a room never moved follows
    the module, and one it moved is the room's. So this answers with the whole
    house as it now is, like every other module command, because a consumer bound
    to an output of any of those rooms is reading an entity that was just
    re-published.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        text = await _dev_text(hass, msg)
    except pack_authoring.AuthoringError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    bindings = _bindings(msg.get("bindings"))
    unknown = _unknown_slot(host, bindings)
    if unknown is not None:
        connection.send_error(msg["id"], INVALID_FORMAT, unknown)
        return
    try:
        await modules.async_edit(
            hass,
            host.entry.entry_id,
            module=str(msg["module"]),
            text=text,
            title=str(msg["title"]),
            blueprint=str(msg["key"] or "") if msg["kind"] == "blueprint" else "",
            description=str(msg.get("description") or ""),
            author=str(msg.get("author") or ""),
            version=str(msg.get("version") or ""),
            licence=str(msg.get("licence") or ""),
            bindings=bindings,
            outputs=_picked(msg.get("outputs")),
            settings=_names(msg.get("settings")),
            casts=_casts(msg.get("casts")),
            flows=_names(msg.get("flows")),
            automations=_names(msg.get("automations")),
            # **Every room's answers, not the one room's.** An edit reaches the
            # installations in all of them, and a module that reaches through a
            # slot has to be built against the room it sits in -- so the map is
            # built here, where the host that knows every room is.
            bound={
                room_id: host.bound_slots(room_id)
                for room_id in ("", *sorted(host.rooms))
            },
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"],
        {
            "module": str(msg["module"]),
            "modules": listing["modules"],
            # The store too, because a definition was rewritten: a screen listing
            # what the house offers is stale in a way a screen listing what it
            # runs is not, and answering with both is what lets either redraw
            # from the reply it already has.
            "store": await _offered_or_empty(hass, host),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_PUBLISH,
        # The module by the name the house hosts it under, which is what the card
        # knows -- the same subject `modules/edit` takes, for the same reason: a
        # value a module publishes is the module's, in every room running it.
        vol.Required("module"): str,
        # The *input* whose row the toggle sits on, not an output key. A key is
        # what the person called the value and can be anything; the input is what
        # the row is, and it is what finds the pick this acts on.
        vol.Required("setting"): str,
        vol.Required("publish"): bool,
        vol.Optional("revision"): vol.Coerce(int),
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_publish(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Make one row's logic readable by any automation, or stop reading it.

    The whole of demand two as one button: a condition, a flow, an automation or a
    template already holds a value, and publishing it puts that value at
    `sensor.open_house_<module>_<key>` -- an entity like any other, which the rest
    of Home Assistant may then use without knowing Open House exists. On is a
    published entity; off takes it away again.

    Answers with the whole house, like every other module command, because a
    consumer bound to any installation's output is reading an entity that was
    just re-created or removed.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    if _stale(connection, msg, host):
        return
    try:
        await modules.async_publish(
            hass,
            host.entry.entry_id,
            module=str(msg["module"]),
            setting=str(msg["setting"]),
            publish=bool(msg["publish"]),
            # Every room's slots, because an installation being rebuilt is built
            # against the room it sits in -- the same map `modules/edit` sends.
            bound={
                room_id: host.bound_slots(room_id)
                for room_id in ("", *sorted(host.rooms))
            },
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"],
        {
            "module": str(msg["module"]),
            "modules": listing["modules"],
            # The store as well, because a definition may have been rewritten:
            # whether this module is one the house *offers* can change what a
            # screen lists, and answering with both lets either redraw from the
            # reply it already holds.
            "store": await _offered_or_empty(hass, host),
        },
    )


# --------------------------------------------------------------------------
# Configurations: the several sets of answers one placed module can hold
#
# One placed module, set up several ways, switched between in place. All four
# answer with the house as it now is, like every other module command, so the
# panel's reload-on-reply needs to know nothing about them: three of the four
# move the module's answers or its name, and the fourth -- adding one -- is a
# copy of the active configuration, which is the module being built again from
# answers it already had.
# --------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_CONFIG_SWITCH,
        vol.Required("module"): str,
        vol.Required("config"): str,
        vol.Optional("revision"): int,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_config_switch(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Make one of a module's configurations the one it is running."""
    host = _host_or_error(connection, msg)
    if host is None or _stale(connection, msg, host):
        return
    module = str(msg["module"])
    try:
        record = await modules.async_switch_config(
            hass,
            host.entry.entry_id,
            module=module,
            config=str(msg["config"]),
            bound=host.bound_slots(await _room_of(hass, module)),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"], {"module": record.slug, "modules": listing["modules"]}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_CONFIG_ADD,
        vol.Required("module"): str,
        vol.Required("config"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_config_add(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Start a new configuration from the running one, and switch the module to it."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    module = str(msg["module"])
    try:
        record = await modules.async_add_config(
            hass,
            host.entry.entry_id,
            module=module,
            config=str(msg["config"]),
            bound=host.bound_slots(await _room_of(hass, module)),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"], {"module": record.slug, "modules": listing["modules"]}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_CONFIG_RENAME,
        vol.Required("module"): str,
        vol.Required("config"): str,
        vol.Required("to"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_config_rename(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Give one of a module's configurations a different name."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        record = await modules.async_rename_config(
            hass,
            host.entry.entry_id,
            module=str(msg["module"]),
            config=str(msg["config"]),
            to=str(msg["to"]),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"], {"module": record.slug, "modules": listing["modules"]}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_CONFIG_REMOVE,
        vol.Required("module"): str,
        vol.Required("config"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_config_remove(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Drop one of a module's configurations, unless it is the last one."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    module = str(msg["module"])
    try:
        record = await modules.async_remove_config(
            hass,
            host.entry.entry_id,
            module=module,
            config=str(msg["config"]),
            bound=host.bound_slots(await _room_of(hass, module)),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"], {"module": record.slug, "modules": listing["modules"]}
    )


# --------------------------------------------------------------------------
# The store: the modules this house offers
#
# An imported blueprint used to be hosted straight into a room, which is one
# import per room. It is *defined* instead -- the document, the answers it starts
# from, what it publishes -- and then installed from the store into any room or
# into the house. A definition is one file, so the last two commands here are what
# let a person hand a module to somebody else and take one in.
# --------------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_STORE,
        # Which placement each row's verdict is read against. The house by
        # default, which is the empty room id -- the same spelling the rest of
        # the module API uses for it. The Store tab asks about the house (it is
        # the library, not a room); "Add module to room" asks about the room it
        # is over, so its rows can say which devices that room is missing.
        vol.Optional("room_id", default=HOUSE): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_store(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The modules this house offers, and where each of them is installed.

    One answer rather than two, because they are one question on a store row:
    "have I used this, and where". The installations are matched to the offer by
    the name each record kept, which is why they are read from the records file
    rather than counted anywhere -- a module installed from a definition and then
    removed from the store is still a module this house runs.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        store = await _offered(hass, host, msg["room_id"])
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {
            "store": store,
            "rooms": [
                {"id": room.id, "name": room.name} for room in host.session.rooms
            ],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_DEFINE,
        # The same three ways `modules/read` and `modules/host` name a document,
        # because this is the step after those: whatever the person was reading,
        # this is what they decided it is.
        vol.Required("kind"): vol.In(("automation", "blueprint", "text")),
        vol.Required("title"): str,
        vol.Optional("key"): str,
        vol.Optional("text"): str,
        # The prose on the store's row. Empty means the blueprint's own
        # description, which is the sentence the person who wrote it wrote.
        vol.Optional("description", default=""): str,
        vol.Optional("author", default=""): str,
        vol.Optional("version", default="1.0.0"): str,
        vol.Optional("licence", default="no_licence"): str,
        vol.Optional("bindings", default=dict): dict,
        vol.Optional("outputs", default=list): list,
        vol.Optional("settings", default=list): list,
        # Re-importing a blueprint a person has since edited is an update to the
        # module, and overwriting one is a thing to ask for.
        vol.Optional("replace", default=False): bool,
        # The inputs answered with a condition rather than with a value. Travels
        # with the module to every room it is installed in, and to every house
        # the file is sent to, because the condition is what the input is.
        vol.Optional("casts", default=dict): dict,
        # The inputs answered by a flow of nodes, by name. Travels too, and
        # travels as *names*: a flow id belongs to one Node-RED, and installing
        # this file elsewhere pushes a flow for that input in whatever Node-RED
        # is doing the installing (`modules.async_define`).
        vol.Optional("flows", default=list): list,
        # The inputs answered by an automation, by name. Travels as *names*, like
        # the flows -- an automation id belongs to one Home Assistant, so the id
        # is made afresh in whatever house installs this file and kept only in
        # that house's record (`modules.async_define`).
        vol.Optional("automations", default=list): list,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_define(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Save an imported source as a module this house offers, in no room.

    This is where the import screen ends now, and it is deliberately not where an
    automation is created: a definition is what the module *is*, and installing it
    -- into one room, or into another house entirely -- is a separate act with the
    same definition behind it.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        text = await _dev_text(hass, msg)
    except pack_authoring.AuthoringError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    bindings = _bindings(msg.get("bindings"))
    unknown = _unknown_slot(host, bindings)
    if unknown is not None:
        connection.send_error(msg["id"], INVALID_FORMAT, unknown)
        return
    try:
        definition = await modules.async_define(
            hass,
            title=str(msg["title"]),
            text=text,
            blueprint=str(msg["key"] or "") if msg["kind"] == "blueprint" else "",
            description=str(msg.get("description") or ""),
            author=str(msg.get("author") or ""),
            version=str(msg.get("version") or "1.0.0"),
            licence=str(msg.get("licence") or "no_licence"),
            bindings=bindings,
            outputs=_picked(msg.get("outputs")),
            settings=_names(msg.get("settings")),
            replace=bool(msg.get("replace")),
            casts=_casts(msg.get("casts")),
            flows=_names(msg.get("flows")),
            automations=_names(msg.get("automations")),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {"module": definition.slug, "store": await _offered_or_empty(hass, host)},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_DEPLOY,
        vol.Required("module"): str,
        # Where it goes. Empty is the whole house, which is where a module with no
        # room of its own resolves its slots.
        vol.Optional("room_id", default=""): str,
        # Only the answers this room makes differently. Everything else comes from
        # the definition, which is the point of defining it once.
        vol.Optional("bindings", default=dict): dict,
        vol.Optional("settings"): list,
        # The room's own condition answers, when it makes one differently. Empty
        # for a name is how the definition's own cast is taken back off here.
        vol.Optional("casts"): dict,
        # The inputs this room answers with a flow of nodes, by name, when the
        # room differs from the definition about that.
        vol.Optional("flows"): list,
        # The inputs this room answers with an automation, by name, when the room
        # differs from the definition about that. Names only, like the flows: the
        # id is made here (`modules.async_deploy`).
        vol.Optional("automations"): list,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_deploy(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Install a module this house offers into a room, or into the house.

    The answers to `modules/host` and to `modules/settings`: the module list,
    because what a consumer may bind to has changed, and the store, because the row
    that was pressed now has somewhere to say it went.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    room_id = str(msg.get("room_id") or "")
    room = host.session.room(room_id) if room_id else None
    if room_id and room is None:
        connection.send_error(
            msg["id"], INVALID_FORMAT, f"there is no room {room_id!r} in this house"
        )
        return
    try:
        record = await modules.async_deploy(
            hass,
            host.entry.entry_id,
            module=str(msg["module"]),
            room_id=room_id,
            # The room as a person reads it, so the installation's name in Home
            # Assistant's own list says which room it is -- see `async_deploy`.
            room_name=room.name if room is not None else "",
            bindings=_bindings(msg.get("bindings")),
            settings=(
                None if msg.get("settings") is None else _names(msg.get("settings"))
            ),
            bound=host.bound_slots(room_id),
            casts=(None if msg.get("casts") is None else _casts(msg.get("casts"))),
            flows=None if msg.get("flows") is None else _names(msg.get("flows")),
            automations=(
                None
                if msg.get("automations") is None
                else _names(msg.get("automations"))
            ),
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"],
        {
            "module": record.slug,
            "modules": listing["modules"],
            "store": await _offered_or_empty(hass, host),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_REMOVE,
        vol.Required("module"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_remove(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stop offering a module this house defined.

    It is not uninstalled from anywhere: an installation keeps its own copy of the
    document, so the rooms running it keep running it. `installed` is how many do,
    so the screen can say so rather than letting a person discover it room by room.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        definition, still = await modules.async_remove_definition(
            hass, str(msg["module"])
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {
            "removed": definition.slug,
            "installed": len(still),
            "store": await _offered_or_empty(hass, host),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_UNHOST,
        vol.Required("module"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_unhost(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Take one installation out of a room, or out of the house.

    The other half of `modules/deploy`, and a different act from `modules/remove`:
    that one stops the house *offering* a module, which leaves every room running
    the ones already made from it; this one stops one room running its copy, and
    touches neither the offer nor any other room's copy. The panel keeps them
    apart in the same way and for the same reason -- one is about the library and
    one is about a room.

    The whole hosted list comes back rather than the one record, because the
    screen that pressed this is a room's page and the list is what it draws.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        await modules.async_unhost(hass, host.entry.entry_id, str(msg["module"]))
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], NOT_FOUND, str(refusal))
        return
    await _saved(host)
    listing = await _hosted(hass, host)
    connection.send_result(
        msg["id"],
        {"module": str(msg["module"]), "modules": listing["modules"]},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_EXPORT,
        vol.Required("module"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_export(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """One module as a file, with the blueprint inside it.

    The same shape `profiles/export` answers with, so the panel has one way to
    turn a document into a download -- and the file is self-contained, so the house
    that receives it needs neither the blueprint nor anything else from here.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        document = await modules.async_export_definition(hass, str(msg["module"]))
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(msg["id"], {"document": document})


@websocket_api.websocket_command(
    {
        vol.Required("type"): MODULES_IMPORT,
        vol.Required("document"): dict,
        vol.Optional("replace", default=False): bool,
    }
)
@websocket_api.async_response
@_admin
async def ws_modules_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Take a module file from somebody else into this house's store.

    Nothing is installed by this. What arrives is a module this house *offers* --
    a file a person can read in the store, install into a room, and hand on again.
    The file's own answers are read as they were written, which is why the store
    says whether the module names devices of the house it came from: those are
    answers that will have to be changed here, and a module answered with slots
    needs nothing changed at all.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        definition, replaced = await modules.async_import_definition(
            hass, msg["document"], replace=bool(msg.get("replace"))
        )
    except modules.ModuleHostError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {
            "imported": definition.slug,
            "replaced": replaced,
            "store": await _offered_or_empty(hass, host),
        },
    )


async def _offered_or_empty(
    hass: HomeAssistant, host: OpenHouseHost
) -> list[Mapping[str, object]]:
    """The store rows after a change landed; a read that fails is noted, not raised.

    Every handler that calls this has already made its change -- a module removed
    from the store, a definition imported. `_offered` reads the definitions and the
    records back, and a house whose files will not read is a fact about the screen's
    *next* list, not about the edit: a completed removal is not unmade by it, and
    the panel shows "That item no longer exists" for a refusal. So the note goes to
    the log (the section above says why there is no field for it) and the rows
    answer empty, which the next reload fills in.
    """
    try:
        return await _offered(hass, host)
    except modules.ModuleHostError as refusal:
        _LOGGER.warning(
            "a change was made to this house's modules and the store could not be "
            "read back, so this reply lists none of them: %s",
            refusal,
        )
        return []


async def _offered(
    hass: HomeAssistant, host: OpenHouseHost, room_id: str = HOUSE
) -> list[Mapping[str, object]]:
    """The modules this house offers, as the store's rows.

    The definitions and the records are read together and joined here rather than
    by the panel, for the reason `rooms/available_modules` carries an offer's
    satisfiability: whether a store row has been used is a fact about this house,
    and a screen that worked it out would be a second implementation of it.
    """
    definitions = await modules.async_definitions(hass)
    records = await modules.async_records(hass)
    bound = live_modules.bound_slots(host.session, room_id)
    return [
        _definition_json(definition, records, host, bound=bound)
        for definition in definitions
    ]


def _definition_json(
    definition: ModuleDefinition,
    records: tuple[module_records.ModuleRecord, ...],
    host: OpenHouseHost,
    *,
    bound: frozenset[str],
) -> Mapping[str, object]:
    """One module this house offers, as the store's row.

    The document itself is left out: it is the file, and it can be a whole
    blueprint. What is here is what a row draws itself from and what a person
    needs to decide whether to install it or to send it on -- including `pinned`,
    which is the one thing the sender of a file needs to know and the receiver
    cannot see: the module names devices from the house that defined it.

    `missing_slots` is the placement's verdict, the same one a catalog pack's
    offer carries: the slots this module reaches through that the room being
    asked about has no device for. It is answered here rather than worked out by
    the screen for the reason every other verdict is -- "can this room answer
    this module" is a question about the house, and a second implementation of it
    in the panel is one free to disagree with the engine's.
    """
    return {
        "slug": definition.slug,
        "title": definition.title,
        "description": definition.description,
        "author": definition.author,
        "version": definition.version,
        "licence": definition.licence,
        "blueprint": definition.blueprint,
        "pinned": definition.pinned,
        # The inputs this module answers with a Node-RED flow, by name. Names and
        # not ids, and it is the definition that carries them: a flow's id is
        # assigned by the Node-RED of the house that installs the module, so it
        # is minted there and recorded on that house's record rather than here.
        "flows": list(definition.flows),
        # The inputs it answers with an *automation*, by name, for the same reason
        # and by the same split: the name is the module's and travels in the file,
        # and the automation's id -- and the helper it sets -- are the installing
        # house's.
        "automations": list(definition.automations),
        "slots": list(definition.slots),
        "missing_slots": [slot for slot in definition.slots if slot not in bound],
        "deployed": [
            {
                "slug": record.slug,
                "room_id": record.room_id,
                "room_name": _room_name(host, record.room_id),
                "running": bool(record.automation_id),
            }
            for record in records
            if record.definition == definition.slug
        ],
    }


async def _room_of(hass: HomeAssistant, module: str) -> str:
    """The room a hosted module sits in, by the module's own name.

    A settings change rebuilds the automation, and a rebuild has to be given the
    same room the module was built for -- a module whose inputs reach through a
    slot would otherwise be rebuilt against a different room's devices, or none.
    The record is the only place that knows, so it is read here rather than sent
    by the panel: the module's home is a fact about the house, not something a
    settings form should be trusted to restate.

    Empty when there is no such module, which `async_update` refuses in its own
    words one step later.
    """
    for record in await modules.async_records(hass):
        if record.slug == module:
            return record.room_id
    return ""


def _bindings(sent: object) -> dict[str, module_host.InputBinding]:
    """The panel's binding rows as `InputBinding`s, refusing a shape this cannot read.

    A binding arrives as a plain mapping because that is what a websocket message
    is; turning it into the type here, at the one door, is what keeps every
    handler below from re-deciding what a binding is. An unknown `kind` is passed
    through rather than refused here, and refused by `bind_inputs` naming the
    value -- one definition of what a binding may be, in the module that owns it.
    """
    if not isinstance(sent, Mapping):
        return {}
    found: dict[str, module_host.InputBinding] = {}
    for name, row in sent.items():
        if not isinstance(row, Mapping):
            continue
        found[str(name)] = module_host.InputBinding(
            kind=str(row.get("kind") or ""),
            value=row.get("value"),
            module=str(row.get("module") or ""),
            key=str(row.get("key") or ""),
            slot=str(row.get("slot") or ""),
            # Where the slot is looked up, and the whole of what a **global
            # slot** is: `house` means the house's own binding, answered the
            # same in every room even where a room bound the role itself
            # (`module_host.HOUSE_SCOPE`). Absent reads as the room's own, which
            # is what every row written before this field existed meant.
            scope=str(row.get("scope") or module_host.ROOM_SCOPE),
            # Which part of a split slot the row is on
            # (`ha_adapter.slot_parts`), empty for the slot itself. Carried
            # here because this is the one door a binding comes through: a
            # `part` that stopped at this line would be a row the panel drew on
            # a part and the server resolved on the whole slot, which is two
            # different devices under one name and no way to see it from either
            # screen.
            part=str(row.get("part") or ""),
        )
    return found


def _unknown_slot(
    host: OpenHouseHost, bindings: Mapping[str, module_host.InputBinding]
) -> str | None:
    """The first slot a module is answered with that this house has no such role for.

    A slot name typed at the import screen is a shortcut past scrolling the
    dropdown, not a way to invent a word: the vocabulary is the catalog plus what
    the installed packs declare (`OpenHouseHost.known_slots`), and `rooms/bind`
    refuses a name outside it. A module built around a name nothing will ever bind
    is therefore not a module that waits -- it is a module that waits *forever*,
    with no act available that would ever start it. Refusing it here, naming the
    words this house does have, is the difference between a dead end and a typo.

    Only authoring is checked. A file from another house may legitimately name a
    role this one has not installed the pack for yet, and there the waiting state
    is exactly right.
    """
    known = set(host.known_slots())
    for name in sorted(bindings):
        binding = bindings[name]
        if binding.kind == "slot" and binding.slot and binding.slot not in known:
            return (
                f"{binding.slot!r} is not a slot this house has, so {name} would "
                "wait for a device nothing could ever give it. Its slots are: "
                + ", ".join(sorted(known))
            )
    return None


def _casts(sent: object) -> dict[str, Any]:
    """The condition answers off a message, by input name.

    Passed through rather than understood: a condition is Home Assistant's own
    shape -- the one its condition editor builds and its automation schema reads
    -- and this layer has no business holding a second opinion about what one may
    contain. An *empty* answer is passed through too, because it is not missing
    data: it is how a cast comes back off, and dropping it here would make the
    cast box a one-way door.
    """
    if not isinstance(sent, Mapping):
        return {}
    return {
        str(name): value
        for name, value in sent.items()
        if isinstance(name, str) and name
    }


def _names(sent: object) -> tuple[str, ...]:
    """A list of input names off a message, dropping anything that is not one."""
    if not isinstance(sent, list):
        return ()
    return tuple(str(name) for name in sent if isinstance(name, str) and name)


def _picked(sent: object) -> tuple[tuple[str, str], ...]:
    """The ticked candidates as `(candidate name, output key)` pairs."""
    if not isinstance(sent, list):
        return ()
    found: list[tuple[str, str]] = []
    for row in sent:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        key = row.get("key")
        if isinstance(name, str) and isinstance(key, str):
            found.append((name, key))
    return tuple(found)


def _module_input(
    name: str,
    block: object,
    chosen: Mapping[str, Any],
    source: module_host.HostedSource | None = None,
) -> Mapping[str, object]:
    """One blueprint input as the import screen's row.

    `satisfied` is the answer the screen needs and the one a person cannot work
    out from the document: an input with no binding and no default is one this
    module cannot be built without, and the button that builds it has to say so
    before it is pressed rather than after it fails.

    *Which* inputs have a default is not `"default" in block`: an input that
    takes a device has none whatever the author wrote, because the name in it
    belongs to the author's own installation and filling a module with it would
    point the automation at a device nobody here chose. `declares_default` owns
    that rule and the screen reads its answer -- an entity row with a `default:`
    in the document is reported with `has_default` false and no `default` value,
    so its picker starts empty and its row says it still needs an answer.
    """
    declared = block if isinstance(block, Mapping) else {}
    has_default = module_host.declares_default(declared)
    return {
        "name": name,
        "title": str(declared.get("name") or name),
        "description": _one_line(declared.get("description")),
        "default": declared.get("default") if has_default else None,
        "has_default": has_default,
        "multiple": _multiple(declared),
        "bound": name in chosen,
        "value": chosen.get(name),
        # Whether the *trigger* names this input, which is the one place a cast
        # cannot go: a trigger's `entity_id` is matched against the real entities
        # a house has rather than rendered, so text a person wrote there names
        # the entity it compares as text and never matches it -- and the
        # automation installs and simply never fires. The screen offers the cast
        # everywhere else, and says why not here
        # (`module_host.input_in_trigger`).
        "in_trigger": source is not None and module_host.input_in_trigger(source, name),
        "satisfied": name in chosen or has_default,
        "selector": _selector_kind(declared),
        "options": _selector_options(declared),
    }


def _selector_kind(declared: Mapping[str, Any]) -> str:
    """Which selector an input offers, named the way the screen shows it.

    The names are the panel's, not Home Assistant's: the screen asks one question
    of a kind -- is this a device? (`isDeviceInput`, over its `DEVICE_SELECTORS`)
    -- and answers it by exact name, so a kind reported under any other spelling
    reaches the panel as a text box and the input is answered as a plain setting
    instead of with a slot. That is why every selector in the device family is
    named for itself below rather than folded into `entity`: `entity`, `device`,
    `area`, `floor`, `label` and `attribute` all take a device, and each is the
    name the panel already matches. A `target` stays `target` -- it, too, takes a
    device, and it, too, is in that set; the panel names it and so does this.

    The kinds the automation editor has a control for are named here, and
    anything else is `text`. That is a real beginning-of-the-list and not a
    claim: a `select` a person fills from a list, a `boolean` a toggle, an
    `action` an action editor -- and an input whose selector this does not know
    is a text box, which is wrong for some of them and is at least visible, where
    hiding the input would not be.
    """
    selector = declared.get("selector")
    if not isinstance(selector, Mapping):
        return "text"
    for kind in (
        # The device family first, because it is the one the screen answers
        # differently: each of these is a slot on a module, not a setting.
        "target",
        "entity",
        "device",
        "area",
        "floor",
        "label",
        "attribute",
        # Then the plain ones, which a person fills in place.
        "number",
        "boolean",
        "select",
        "action",
        "text",
        "time",
        "date",
    ):
        if kind in selector:
            return kind
    return "text"


def _selector_options(declared: Mapping[str, Any]) -> list[str]:
    """A `select` input's menu, so an unknown token cannot be typed into it.

    The options are the input's own, including when they are given one per line
    as a block scalar -- which is how most of the corpus writes them -- because a
    menu that dropped half of its entries would be worse than no menu: the
    blueprint's comparisons would silently never match what the person chose.
    """
    selector = declared.get("selector")
    block = selector.get("select") if isinstance(selector, Mapping) else None
    if not isinstance(block, Mapping):
        return []
    options = block.get("options")
    if isinstance(options, str):
        return [line.strip() for line in options.splitlines() if line.strip()]
    if isinstance(options, list):
        return [str(option) for option in options]
    return []


def _multiple(declared: Mapping[str, Any]) -> bool:
    """Whether an input's selector takes more than one value."""
    selector = declared.get("selector")
    if not isinstance(selector, Mapping):
        return False
    for value in selector.values():
        if isinstance(value, Mapping) and value.get("multiple") is True:
            return True
    return False


def _suggested_key(name: str) -> str:
    """A name a person could call this output, from the candidate's own name.

    Best-effort: `input:lux_sensor` loses its marker and `min_lux` keeps its
    name, and anything that does not land on a key at all is answered with an
    empty string rather than a guess -- the screen needs *a* starting value, and
    an invented one it cannot validate would be worse than a blank the person
    fills in.
    """
    stripped = name.split(":", 1)[-1]
    try:
        return module_records.slug(stripped)
    except pack_authoring.AuthoringError:
        return ""


def _one_line(value: object) -> str:
    """A description as one line, the way `pack_authoring` reads one."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


async def _node_red_url(hass: HomeAssistant) -> str:
    """Where this house's Node-RED *editor* is, or empty when none is set.

    **The editor's address and not the push address**, which is a distinction
    that only shows up when they differ: Home Assistant reaches Node-RED over the
    docker network by a name that means nothing to a browser, so a link built
    from the address the house itself pushes to is a link that resolves for
    nobody who clicks it. `node_red.async_editor_url` prefers the address a
    person set for the browser, falls back to the push one, and only when neither
    is set reaches for the Node-RED this repository ships as its own add-on --
    whose address is its ingress path, which is exactly what a browser embeds.

    Empty is the ordinary state and the panel is built for it: the cast is still
    offered, and the row says where to set the address rather than showing a link
    to a program this house does not have.
    """
    return await node_red.async_editor_url(hass, node_red.entry_options(hass))


def _flow_href(base: str, flow_id: str) -> str:
    """The Node-RED editor, opened on one flow.

    `#flow/<id>` is Node-RED's own route for a tab -- the editor is a single page
    and the tab is a fragment -- so this is a link to the *flow*, in the program
    that owns it, rather than to a copy Open House would have to keep in step.
    """
    if not base or not flow_id:
        return ""
    return f"{base.rstrip('/')}/#flow/{flow_id}"


def _automation_href(automation_id: str) -> str:
    """Home Assistant's own automation editor, opened on one automation.

    A *relative* address, unlike the flows': Node-RED is another program on
    another port, so its address has to be configured, but the automation editor
    is Home Assistant's own page and the browser reading this is already in Home
    Assistant. `/config/automation/edit/<id>` is that page's route, and building
    it here rather than in the panel keeps the panel from having to know which of
    the two editors is which.
    """
    if not automation_id:
        return ""
    return f"/config/automation/edit/{automation_id}"


async def _hosted(hass: HomeAssistant, host: OpenHouseHost) -> Mapping[str, object]:
    """The house's hosted modules, as the protocol's reply.

    The records are re-read from disk rather than taken from the runtime, because
    the file is the truth about what the house hosts: a record a person removed
    by hand is a module this must stop claiming, and the runtime is only a copy
    of the file taken at setup.
    """
    runtime = hass.data.get(DOMAIN, {}).get(host.entry.entry_id)
    values = {
        slug: dict(module.values)
        for slug, module in (getattr(runtime, "modules", None) or {}).items()
    }
    records = await modules.async_records(hass)
    editor = await _node_red_url(hass)
    helpers = {record.slug: _helpers_of(record) for record in records}
    return {
        "modules": [
            {
                "slug": record.slug,
                "title": record.title,
                "blueprint": record.blueprint,
                # Which store module this was installed from, empty for a document
                # hosted directly. The store matches its installations by this, so
                # the two screens agree about where a module came from.
                "definition": record.definition,
                "room_id": record.room_id,
                "room_name": _room_name(host, record.room_id),
                # Which slots this module reaches through, and what each one
                # currently answers. A module with an unbound slot is one that is
                # imported but not running (`automation_id` is empty) and whose
                # screen has to say which device it is still waiting for --
                # otherwise it reads as a module that silently did nothing.
                "slots": _hosted_slots(host, record),
                "automation_id": record.automation_id,
                # The configurations this module holds, and which of them it is
                # running. Everything else on this record is one set of answers,
                # so the card cannot show a switcher without both -- and the
                # order is the record's own, which is the order they were made
                # in, so a list that is re-read does not shuffle under the hand.
                "config": record.variant,
                "configs": list(record.configurations),
                # The inputs this module answers with a *condition*, each as the
                # person built it, so the card can show the logic a module runs
                # on rather than only the value it ended up bound to.
                "derived": dict(record.derived),
                # The inputs this module answers with a *flow of nodes*, each
                # with the entity the flow writes and the id of the flow itself
                # -- the id so the row can open Node-RED on that flow, and the
                # entity so it can say what the input is actually reading.
                "flows": {
                    name: {
                        "flow_id": flow_id,
                        "entity_id": module_host.flow_entity_id(record.slug, name),
                        # Where the flow is *edited*, so the row can open Node-RED
                        # on this tab. Built here and not in the panel because
                        # only the server knows both halves of it -- the address
                        # the person configured, and the id Node-RED assigned.
                        "url": _flow_href(editor, flow_id),
                    }
                    for name, flow_id in record.flows.items()
                },
                # The inputs this module answers with an *automation*, each with
                # the helper the input is bound to, the automation's own id and
                # where it is edited. The entity is here and that is the point of
                # this cast: what the input reads is the helper's state, written
                # by the person's own automation -- the same shape as a flow, with
                # a Home Assistant automation in place of a Node-RED one.
                "automations": {
                    name: {
                        "automation_id": automation_id,
                        "entity_id": helpers[record.slug].get(name, ""),
                        "url": _automation_href(automation_id),
                    }
                    for name, automation_id in record.automations.items()
                },
                "inputs": [
                    {"name": name, "value": _jsonable(value)}
                    for name, value in record.inputs.items()
                ],
                "settings": _module_settings(record, editor),
                "outputs": [
                    {
                        "key": output.key,
                        "kind": output.kind,
                        "expression": output.expression,
                        "entity_id": module_host.output_entity_id(
                            record.slug, output.key
                        ),
                        "value": _jsonable(values.get(record.slug, {}).get(output.key)),
                    }
                    for output in record.outputs
                ],
            }
            for record in records
        ]
    }


def _unknown_part(
    host: OpenHouseHost, bindings: Mapping[str, module_host.InputBinding]
) -> str | None:
    """The first part named that the house does not carry, as a sentence, or `None`.

    A module put on a part nobody has is a module waiting for a device nothing
    can give it -- the part is a binding key, and a key the vocabulary does not
    carry is one no room may ever bind (`engine.binding`). So the write is refused
    here, where the house's own parts record is to hand, rather than recorded and
    discovered as a module that silently never moves. The same refusal
    `live_modules.set_slot` makes for a pack, at the door a hosted module's
    bindings come through.
    """
    for name, binding in bindings.items():
        if binding.kind != "slot" or not binding.part:
            continue
        carried = slot_parts.parts_of(host.session.slot_parts, binding.slot)
        if binding.part not in carried:
            named = ", ".join(repr(part) for part in carried) or "none"
            return (
                f"the row {name!r} names part {binding.part!r} of the slot "
                f"{binding.slot!r}, and that slot has no such part; its parts are "
                f"{named}"
            )
    return None


def _hosted_slots(
    host: OpenHouseHost, record: module_records.ModuleRecord
) -> list[dict[str, object]]:
    """Which slots one hosted module reaches, and what each of them answers.

    The row the card draws for a device the module does not own: the slot's name,
    the device the module's room resolves it to, and -- where the house has split
    the slot -- **which part of it this module is on**, with the device each part
    is bound to.

    A part is a binding key of its own (`ha_adapter.slot_parts`), so the device
    the *module* acts on is the part's, not the parent's: `slot_key` is what
    decides that, and it is read here rather than the parent's name so the two
    screens cannot disagree about which entity a module is on.

    The parts offered are the house's for this slot and not only the one already
    chosen, because this list is what the row's picker is drawn from -- a list
    holding only the current answer would be a menu with one entry.
    """
    bound = host.bound_slots(record.room_id)
    parts = host.session.slot_parts
    rows: list[dict[str, object]] = []
    for name, row in record.bindings.items():
        binding = module_host.InputBinding(**row)
        if binding.kind != "slot" or not binding.slot:
            continue
        device = str(bound.get(module_host.slot_key(binding), ""))
        rows.append(
            {
                "name": binding.slot,
                # Which input of the module this slot answers, so a row can be
                # written back to the binding it came from.
                "input": name,
                "scope": binding.scope,
                "part": binding.part,
                "bound": device,
                # What Home Assistant calls the device, so the row reads as a
                # lamp rather than as an id. Read here and not in the panel for
                # the reason the room's slot rows do it: the panel's `hass` is a
                # reduced view with no states in it.
                "bound_name": _entity_name(host.hass, device),
                "parts": [
                    {
                        "name": part,
                        "label": part,
                        "bound": str(
                            bound.get(slot_parts.key_of(binding.slot, part), "")
                        ),
                    }
                    for part in slot_parts.parts_of(parts, binding.slot)
                ],
            }
        )
    return rows


def _helpers_of(record: module_records.ModuleRecord) -> Mapping[str, str]:
    """The helper each automation-answered input is bound to, by input name.

    Read out of the record's own stored document, for the reason
    `_module_settings` reads it: which helper Open House made for an input is
    decided by that input's *selector*, and the selector is the document's to
    declare. A document that will not read is a module the house still hosts, so
    the rows lose their entity and keep their automation id rather than the
    listing failing -- the same trade `_module_settings` makes.
    """
    if not record.automations or not record.source:
        return {}
    try:
        source = module_host.read_module_source(record.source)
    except pack_authoring.AuthoringError:
        return {}
    made: dict[str, str] = {}
    for name in record.automations:
        block = source.inputs.get(name)
        if block is None:
            continue
        made[name] = module_host.helper_entity_id(record.slug, name, block)
    return made


def _entity_name(hass: HomeAssistant, entity_id: str) -> str:
    """What Home Assistant calls an entity, or the empty string when it has none."""
    if not entity_id:
        return ""
    state = hass.states.get(entity_id)
    if state is None:
        return ""
    return str(state.attributes.get("friendly_name", entity_id))


def _room_name(host: OpenHouseHost, room_id: str) -> str:
    """The room a module sits in, as a person reads it. Empty id is the house."""
    if not room_id:
        return "the whole house"
    room = host.session.room(room_id)
    return room.name if room is not None else room_id


def _module_settings(
    record: module_records.ModuleRecord, editor: str = ""
) -> list[Mapping[str, object]]:
    """The inputs a module kept settable, as the rows the settings form renders.

    Read out of the module's own stored document rather than kept a second time
    on the record: the declaration is the document's to make, and a copy taken at
    import would be one more thing that could disagree with what the automation
    was actually built from. The rows are `_module_input`'s, the same shape the
    import screen renders, because a setting is one of the import screen's rows
    that the person ticked to keep.
    """
    if not record.settings or not record.source:
        return []
    try:
        source = module_host.read_module_source(record.source)
    except pack_authoring.AuthoringError:
        # A record whose document will not read is still a module the house
        # hosts, with a working automation and outputs to read. Losing its
        # settings rows is an answer the panel can show; refusing to list the
        # module at all would hide the thing that is fine behind the thing that
        # is not.
        return []
    rows: list[Mapping[str, object]] = []
    for name in record.settings:
        if name not in source.inputs:
            continue
        row = dict(_module_input(name, source.inputs[name], record.inputs, source))
        binding = record.bindings.get(name) or {}
        kind = str(binding.get("kind") or "")
        # What the setting is *filled by*, which the panel needs to tell a value
        # it may edit from one another module is publishing. A setting bound to
        # an output is not a number a person owns -- it is a live reading, and a
        # box holding a copy of it would be overwritten by the next publish.
        row["bound_kind"] = kind
        # A setting answered with a condition is bound to the entity Open House
        # made for it, which is not the person's to edit here -- what *is* theirs
        # is the condition, and it is handed back so the card opens on the logic
        # they wrote rather than on an entity id they never chose.
        row["cast"] = record.derived.get(name)
        # A setting answered with a flow is bound to the entity that flow writes,
        # which is not a value the person edits here either -- what is theirs is
        # the flow, and the card opens Node-RED on it.
        row["flow_id"] = record.flows.get(name, "")
        row["flow_url"] = _flow_href(editor, row["flow_id"])
        # A setting answered with an automation is the same shape again: what
        # fills it is not a value the person edits here, it is the helper their
        # own automation writes, so the row carries the automation and where to
        # open it -- and the helper, so the card can say what is being read.
        row["automation_id"] = record.automations.get(name, "")
        row["automation_url"] = _automation_href(row["automation_id"])
        row["automation_entity"] = module_host.helper_entity_id(
            record.slug, name, source.inputs[name]
        )
        # What this row's own logic is published as, or empty. Read off the
        # *picks*, which are the module's answer to "which of my values do I
        # publish" -- and keyed by the candidate name (`cast:<input>`) rather
        # than by the key, because the key is what the person called the value
        # and can be anything at all. The key is handed back as well, so the
        # card can say where the reading lands rather than only that it does.
        row["published_key"] = next(
            (key for candidate, key in record.picks if candidate == f"cast:{name}"),
            "",
        )
        if name in record.flows:
            row["bound_kind"] = "flow"
            row["bound_to"] = module_host.flow_entity_id(record.slug, name)
        elif name in record.automations:
            row["bound_kind"] = "automation"
            row["bound_to"] = module_host.helper_entity_id(
                record.slug, name, source.inputs[name]
            )
        elif name in record.derived:
            row["bound_kind"] = "condition"
            row["bound_to"] = module_host.derived_entity_id(record.slug, name)
        elif kind == "output":
            row["bound_to"] = f"{binding.get('module')}/{binding.get('key')}"
        elif kind == "slot":
            # What fills a setting answered with a slot is the device the
            # module's room binds for it, which is the room's to change and not
            # this screen's -- so the row names the slot rather than a value.
            row["bound_to"] = str(binding.get("slot") or "")
        else:
            row["bound_to"] = ""
        rows.append(row)
    return rows


def _jsonable(value: object) -> object:
    """A published value as something the JSON reply can carry.

    A value that arrives from an automation is whatever its template produced --
    a string, a number, a list, a mapping, or a date. Anything the encoder can
    carry is passed through untouched, and anything it cannot is reported as its
    type name rather than dropped: "there is a value here and it is a datetime"
    is a better answer to a person than a blank row.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


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
    except modules.ModuleHostError as refusal:
        # **The binding landed; only the rebuild behind it did not.** The session
        # edit and the subentry write both happen inside `async_set_binding`
        # before it rebuilds the modules that reach the slot (host.py:494-504),
        # so a `ModuleHostError` here is the follow-up failing, not the write.
        # Reported as a failure it would tell the person the slot was not bound
        # when everything reads back that it was -- so it is noted and the reply
        # is the room as it now stands, exactly as the success path answers.
        _unsettled(f"{slot!r} in {room_id!r}", refusal)
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


# --------------------------------------------------------------------------
# The published Store
#
# The other store: the server somebody else runs, that this house publishes to
# and installs other people's modules from. The commands above are this house's
# own store, which exists whether or not a published one is configured; every
# command here is answered from a server this house was given the address of, and
# answers "set the address" when it was not. See `store.py`.
# --------------------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): PUBLISHED_STATUS})
@websocket_api.async_response
@_admin
async def ws_published_status(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Whether this house has a published Store, and what it calls itself.

    Read off the entry's options and answered with no network at all, because the
    question decides what the tab *is*: with no address it is this house's own
    store of modules, and with one it is that store plus the published one. A
    check that opened a connection here would make a house that has never heard
    of the Store wait for one before it could draw its own list.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    options = host.entry.options
    connection.send_result(
        msg["id"],
        {"url": store.store_url(options), "name": store.publisher_name(options)},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PUBLISHED_CONFIGURE,
        # Required, and an empty string is a real answer rather than a missing
        # one: it is how a house that has been pointed at a Store is pointed back
        # away from it, and it leaves `DEFAULT_URL` as the address when this build
        # ships with one. Required rather than defaulted because a *mutating*
        # command must not read a request that forgot the field as "clear it" --
        # the blank address is the same word as the absent one only by accident,
        # and a caller who sends neither is a caller with a bug.
        vol.Required("url"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_published_configure(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Set which Store this house publishes to, from the panel.

    The address is an option of the integration either way -- this is the second
    writer of it, beside the Configure screen -- and it writes through
    `store.with_url`, so both writers agree about the one part that is easy to get
    wrong: a changed address forgets the publisher name claimed on the old one.

    Answered with the whole new status rather than with the address alone, because
    the reason a person is here is that they were missing one of the two, and the
    claim the address change may just have dropped is the other.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    options = store.with_url(host.entry.options, str(msg["url"]))
    hass.config_entries.async_update_entry(host.entry, options=options)
    connection.send_result(
        msg["id"],
        {"url": store.store_url(options), "name": store.publisher_name(options)},
    )


@websocket_api.websocket_command(
    {vol.Required("type"): PUBLISHED_CLAIM, vol.Required("name"): str}
)
@websocket_api.async_response
@_admin
async def ws_published_claim(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Claim this install's publisher name, once, on the Store.

    Both kinds of refusal -- a name refused on its merits and a name somebody
    else already has -- are answered the same way, `invalid_format` with a
    sentence, so the screen has one thing to render either way: "names are lower
    case" and "that name is taken, pick another" are the same shape of answer to
    a form and different things to a person. `async_claim` does the Store's part
    and stores the pair only when the name was really taken.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    try:
        name = await store.async_claim(hass, host.entry, str(msg["name"]))
    except store.StoreError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(msg["id"], {"name": name})


@websocket_api.websocket_command(
    {
        vol.Required("type"): PUBLISHED_BROWSE,
        vol.Optional("search", default=""): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_published_browse(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The published Store, split into what this house has and what it does not.

    **Split here and not on the screen.** "Do I have this" is a question about
    this house's own definitions, which the server holds and the screen does not;
    the split is by *slug* (`store_api.installed_split`), so a module whose
    publisher corrected a typo in its summary is still the module this house has.
    `mine` is answered from this install's own publisher id, which is why the two
    halves can be drawn with a publish button and an install button respectively.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    client = await store.async_client(hass, host.entry.options)
    if client is None:
        connection.send_error(msg["id"], INVALID_FORMAT, store.no_store_sentence())
        return
    try:
        rows = await client.browse(str(msg.get("search") or ""))
        publisher_id = await client.publisher_id()
    except store.StoreError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    slugs = sorted(
        definition.slug for definition in await modules.async_definitions(hass)
    )
    installed, elsewhere = store_api.installed_split(rows, slugs)
    connection.send_result(
        msg["id"],
        {
            "installed": [
                store_api.module_json(row, publisher_id=publisher_id)
                for row in installed
            ],
            "not_installed": [
                store_api.module_json(row, publisher_id=publisher_id)
                for row in elsewhere
            ],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PUBLISHED_PUBLISH,
        vol.Required("module"): str,
        vol.Optional("summary", default=""): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_published_publish(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Publish one of this house's own modules to the Store, by its name.

    What is published is the *definition* -- the same document an export writes
    and an import reads -- so what a person installs from the Store is exactly
    what they published. The module list is not returned, because publishing
    changes nothing this house runs; the store rows come back because the row
    that was pressed is where the fact "this is now on the Store" belongs.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    client = await store.async_client(hass, host.entry.options)
    if client is None:
        connection.send_error(msg["id"], INVALID_FORMAT, store.no_store_sentence())
        return
    try:
        definition = await modules.async_definition(hass, str(msg["module"]))
        published = await client.publish(definition, str(msg.get("summary") or ""))
    except (modules.ModuleHostError, store.StoreError) as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {
            "published": published,
            "store": await _offered_or_empty(hass, host),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PUBLISHED_INSTALL,
        vol.Required("module_id"): str,
        vol.Optional("replace", default=False): bool,
    }
)
@websocket_api.async_response
@_admin
async def ws_published_install(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Take a published module into this house's own store, where it is offered.

    The Store's record is fetched by id and its document handed to the same
    import a module file from anywhere else goes through, so what lands here is a
    definition like any other and installs into a room the same way. Nothing runs
    yet, which is why `replaced` -- whether a module of that name was already
    here -- is what a person has to know rather than a running module.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    client = await store.async_client(hass, host.entry.options)
    if client is None:
        connection.send_error(msg["id"], INVALID_FORMAT, store.no_store_sentence())
        return
    try:
        published = await client.install(str(msg["module_id"]))
        definition, replaced = await modules.async_import_definition(
            hass, published.document, replace=bool(msg.get("replace"))
        )
    except (modules.ModuleHostError, store.StoreError) as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    # Counted only once the module is actually here, so the number beside a
    # published module is houses that have it rather than houses that asked. This
    # can fail without failing the install -- see `Store.record_install` -- and it
    # is awaited before the reply so the row a person is looking at is re-read
    # after the count moved rather than before.
    await client.record_install(str(msg["module_id"]))
    connection.send_result(
        msg["id"],
        {
            "module": definition.slug,
            "replaced": replaced,
            "store": await _offered_or_empty(hass, host),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PUBLISHED_RATE,
        vol.Required("module_id"): str,
        vol.Required("stars"): int,
    }
)
@websocket_api.async_response
@_admin
async def ws_published_rate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Rate a published module, one to five whole stars.

    The band is checked here (`store_api.stars_refusal`) before a request is
    spent, so a value the Store would only reject after a round trip is refused
    in the same words. A second rating is the same rating changed rather than a
    second opinion, and `Store.rate` does the update rather than showing a person
    pressing a star an error for doing what the screen asked.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    stars = msg["stars"]
    refusal = store_api.stars_refusal(stars)
    if refusal is not None:
        connection.send_error(msg["id"], INVALID_FORMAT, refusal)
        return
    client = await store.async_client(hass, host.entry.options)
    if client is None:
        connection.send_error(msg["id"], INVALID_FORMAT, store.no_store_sentence())
        return
    try:
        answer = await client.rate(str(msg["module_id"]), stars)
    except store.StoreError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(msg["id"], dict(answer))


@websocket_api.websocket_command(
    {vol.Required("type"): PUBLISHED_COMMENTS, vol.Required("module_id"): str}
)
@websocket_api.async_response
@_admin
async def ws_published_comments(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What people have said about one published module."""
    host = _host_or_error(connection, msg)
    if host is None:
        return
    client = await store.async_client(hass, host.entry.options)
    if client is None:
        connection.send_error(msg["id"], INVALID_FORMAT, store.no_store_sentence())
        return
    try:
        comments = await client.comments(str(msg["module_id"]))
    except store.StoreError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {"comments": [store_api.comment_json(comment) for comment in comments]},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): PUBLISHED_COMMENT,
        vol.Required("module_id"): str,
        vol.Required("body"): str,
    }
)
@websocket_api.async_response
@_admin
async def ws_published_comment(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Say something about one published module, and get the list back.

    The whole list, not the one row: the screen it was posted from draws the
    list, and asking for it again the moment the write landed would be a second
    round trip for a fact the Store has already been read for.
    """
    host = _host_or_error(connection, msg)
    if host is None:
        return
    client = await store.async_client(hass, host.entry.options)
    if client is None:
        connection.send_error(msg["id"], INVALID_FORMAT, store.no_store_sentence())
        return
    try:
        comments = await client.comment(str(msg["module_id"]), str(msg["body"]))
    except store.StoreError as refusal:
        connection.send_error(msg["id"], INVALID_FORMAT, str(refusal))
        return
    connection.send_result(
        msg["id"],
        {"comments": [store_api.comment_json(comment) for comment in comments]},
    )


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
    ws_module_set_behaviour_priority,
    ws_module_set_slot,
    ws_module_set_slot_rule,
    ws_slot_set_parts,
    ws_house_scope,
    ws_modules_list,
    ws_profiles_list,
    ws_profile_activate,
    ws_profile_activate_house,
    ws_profile_capture,
    ws_profile_deactivate_house,
    ws_profile_rename,
    ws_profile_remove,
    ws_profile_export,
    ws_profile_import,
    ws_activity_list,
    ws_activity_subscribe,
    ws_health_list,
    ws_dashboard_generate,
    ws_dev_sources,
    ws_modules_hosted,
    ws_modules_read,
    ws_modules_host,
    ws_modules_settings,
    ws_modules_edit,
    ws_modules_publish,
    ws_modules_config_switch,
    ws_modules_config_add,
    ws_modules_config_rename,
    ws_modules_config_remove,
    ws_modules_store,
    ws_modules_define,
    ws_modules_deploy,
    ws_modules_remove,
    ws_modules_detach,
    ws_modules_unhost,
    ws_modules_export,
    ws_modules_import,
    ws_published_status,
    ws_published_configure,
    ws_published_claim,
    ws_published_browse,
    ws_published_publish,
    ws_published_install,
    ws_published_rate,
    ws_published_comments,
    ws_published_comment,
)
