"""The panel's view models, built from the engine and Home Assistant's registries.

`panel/src/api/models.ts` defines the shape of every answer the panel renders,
and this module is where those answers come from. The join is the point: a
`RoomDetail` is not a document at rest but the frozen binding data *joined to*
what Home Assistant knows right now -- whether a bound entity is available, which
entity registry record it has, what else in the room could take the slot. The
panel makes one round trip per screen and does no joining of its own, so every
join has to happen here.

**The panel's field names are normative and this module's job is to match them.**
They are not camel-cased and not abbreviated; a field called `type_label` is
`type_label` here. Renaming one to something more Pythonic would break a screen
silently, because the panel reads `undefined` and renders an empty row rather
than raising.

**A label is derived, never invented.** The catalog carries descriptions and no
display names (`catalog/slots.yaml`), so `label` is the slot's own name with its
separators turned into spaces and its first letter raised: `motion_sensor` is
"Motion sensor". That is a *worse* label than a hand-written one and it is the
honest one, because a hand-written label here would be a second vocabulary that
could drift from the catalog the engine binds against.

**Status is a closed set, and the order of the checks is the meaning.** A slot
with nothing bound is `unbound`; one bound to an entity Home Assistant no longer
holds is `missing`; one bound to an entity of a domain the slot does not accept
is `domain_mismatch`; one whose entity is unavailable is `unavailable`; one whose
entity reads `unknown` is `unknown`; and everything else is `ok`. The order
matters because a binding can be several of these at once, and "the device is
gone" is a more useful thing to say than "it is unavailable".

**Health has two sources and both belong.** The engine's own repairs and hazards
are what the house found while deciding; Home Assistant's issue registry is what
it is currently showing a person on the Repairs screen. Reporting only the first
would leave the panel disagreeing with a badge in the sidebar; reporting only the
second would drop a hazard the engine noticed before a repair existed for it.

**Nothing below the catalog divider reads the disk.** The vocabulary and the pack
registry are files, and every command here runs on Home Assistant's event loop,
where a `read_text` is a blocking call Home Assistant reports and asks a person
to file a bug about -- correctly, because it stalls every other integration for
the length of the read. So the files are read once, when the host is built, into
a `Catalog`, and the projections read that. The divider is deliberate: above it a
function takes a `Catalog` and answers, below it a function touches a path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from engine.adapter import domain_of
from engine.declared_slots import optional_keys, required_keys
from ha_adapter import slot_parts, slot_rules
from ha_adapter.composition import mode_name
from ha_adapter.live import HOUSE
from ha_adapter.setup_flow import (
    load_room_types,
    load_slot_domains,
    rank_candidates,
)

from .const import DOMAIN
from .host import (
    OpenHouseHost,
    RoomRef,
    entity_ids_house,
    entity_ids_in_area,
)
from .repairs import NO_OCCUPANCY_SENSOR, OCCUPANCY_SENSOR_UNAVAILABLE, issue_id
from .runtime import RoomRuntime

__all__ = [
    "Catalog",
    "binding_statuses",
    "candidates",
    "capabilities",
    "health_issues",
    "label",
    "load_catalog",
    "overview",
    "pack_path",
    "people",
    "room_detail",
    "room_summaries",
    "room_summary",
]

#: The severity the engine's own repairs are reported at. A room that cannot be
#: read is a warning and not an error: the house still works, it just cannot see
#: that room, and an error would put it in the same list as a fire.
_REPAIR_SEVERITY = "warning"

#: The code on the row raised when the engine cannot answer at all. It is an
#: error rather than a warning because the whole *picture* is missing, not one
#: room of it, and it has no Repairs flow -- there is nothing for Home
#: Assistant's Repairs screen to offer, since the fix is the engine and not a
#: user's setting -- so `repairs_flow_id` is `None` on that row.
_ENGINE_READ_FAILED = "engine_read_failed"

#: Home Assistant's own severities, mapped onto the panel's three. `critical`
#: becomes `error` rather than `info` because the panel has no fourth word for
#: it, and of the two it could become, the lower is the safer mistranslation: a
#: critical issue shown as an error still reads as urgent, while an error shown
#: as critical is one a person learns to distrust.
_SEVERITIES = {
    "critical": "error",
    "error": "error",
    "warning": "warning",
}

#: The device classes worth a title, as `engine/safety.py` reads them. The engine
#: decides a hazard *is* alarming; this only says what to call it.
_HAZARD_TITLES = {
    "smoke": "Smoke detected",
    "carbon_monoxide": "Carbon monoxide detected",
    "moisture": "Water leak detected",
    "gas": "Gas detected",
}


def label(name: str) -> str:
    """A slot or room type's name as a person reads it.

    See the module docstring: derived from the catalog's own spelling rather
    than looked up, because there is no display name in the catalog to look up
    and inventing one would be a second vocabulary.
    """
    return name.replace("_", " ").replace("-", " ").strip().capitalize()


# -- Capabilities -----------------------------------------------------------


def capabilities(
    *,
    admin: bool,
    user_name: str | None,
    version: str,
    engine_api: str,
    needs_setup: bool,
    node_red_url: str = "",
) -> Mapping[str, object]:
    """Who is looking, and what they may do.

    Every field is passed in rather than read here, because two of the five come
    from the *connection* -- which user authenticated and whether they are an
    admin -- and a projection that reached into `hass` for them would answer for
    the wrong person on a multi-user instance.

    `node_red_url` travels with them because the panel embeds Node-RED's own
    editor at it and has no way to know it otherwise: Node-RED is reached at an
    address the instance's own admin configured, and the panel is served from
    Home Assistant, which may be a different host entirely. Empty means the house
    has neither a configured Node-RED nor the Open House Node-RED add-on running,
    and the row that offers the cast says so.

    **It is the address a browser opens, and not the one flows are pushed to.**
    Those are two addresses for one editor on any stack where the two programs
    are separate containers -- `nodered:1880` resolves between them and nowhere
    else -- and the whole reason a second option exists is that only one of the
    two can be linked to. The caller passes the answer of
    `node_red.async_editor_url`, which prefers the browser's configured address,
    falls back to the push one, and only when neither is set reaches for the
    bundled add-on's ingress path -- so a single-address install configures one
    field and both links work, and a house with nothing configured still gets an
    editor when it runs the add-on Open House ships.
    """
    return {
        "admin": admin,
        "user_name": user_name,
        "version": version,
        "engine_api": engine_api,
        "needs_setup": needs_setup,
        "node_red_url": node_red_url,
    }


# -- Overview ---------------------------------------------------------------


def overview(hass: HomeAssistant, host: OpenHouseHost) -> Mapping[str, object]:
    """The Overview tab's one answer."""
    issues = health_issues(hass, host)
    counts = {"info": 0, "warning": 0, "error": 0}
    for issue in issues:
        severity = str(issue["severity"])
        if severity in counts:
            counts[severity] += 1
    return {
        "name": host.session.house_name,
        "mode": _active_mode_label(host),
        "rooms": [
            room_summary(hass, host, room, issues) for room in host.rooms.values()
        ],
        "people": list(people(hass, host)),
        "modules_installed": len(host.session.installed),
        # The counts are here so the Health badge needs no second call, which is
        # the whole reason the overview carries them instead of a bare list.
        "issues": counts,
        "updated_at": dt_util.utcnow().isoformat(),
    }


def _active_mode_label(host: OpenHouseHost) -> str:
    """The label of the mode in force, or the engine's own name for it.

    The engine holds *names* (`home`, `away`) and the integration offers
    *labels* ("Home", "Away"), so the label is recovered by finding the one that
    projects to an active name. A name with no label came from a mode document
    rather than from this integration's list -- a pack, or a fixture -- and it is
    shown as the engine spells it rather than hidden, because a mode nobody can
    name is still the mode the house is in.
    """
    active = host.session.engine.modes.active
    for candidate in host.session.modes:
        if mode_name(candidate) in active:
            return candidate
    return next(iter(sorted(active)), "")


def people(
    hass: HomeAssistant, host: OpenHouseHost
) -> tuple[Mapping[str, object], ...]:
    """Every person the house follows, with whether they count as home.

    A person counts as home when Home Assistant says they are home *and* the
    setup chose to count them -- the away rule is the engine's, and it was
    written against the people the flow picked. An entry that named nobody
    counts everyone, because a house that asked about nobody has no reason to
    exclude anybody.
    """
    counted = _away_people(host)
    result: list[Mapping[str, object]] = []
    for state in hass.states.async_all("person"):
        home = state.state == "home" and (not counted or state.entity_id in counted)
        result.append(
            {
                "entity_id": state.entity_id,
                "name": str(state.attributes.get("friendly_name", state.entity_id)),
                "state": state.state,
                "home": home,
            }
        )
    return tuple(result)


def _away_people(host: OpenHouseHost) -> frozenset[str]:
    """The people the setup flow chose to count, or none when it chose nobody."""
    chosen = host.entry.data.get("away_people")
    if isinstance(chosen, (list, tuple)):
        return frozenset(str(item) for item in chosen)
    return frozenset()


# -- Rooms ------------------------------------------------------------------


def room_summaries(
    hass: HomeAssistant, host: OpenHouseHost
) -> tuple[Mapping[str, object], ...]:
    """One row per room, in the order the entry holds them."""
    issues = health_issues(hass, host)
    return tuple(room_summary(hass, host, room, issues) for room in host.rooms.values())


def room_summary(
    hass: HomeAssistant,
    host: OpenHouseHost,
    room: RoomRef,
    issues: Sequence[Mapping[str, object]] | None = None,
) -> Mapping[str, object]:
    """One room's row in the Rooms tab.

    `issues` is passed in by the caller that already computed them: the overview
    wants every room's count and the rooms tab wants every room, so computing
    them per row would be the same registry scan a quadratic number of times.
    """
    provided = _configurable_slots(host, room)
    statuses = binding_statuses(hass, host, room)
    runtime = host.runtime_room(room.id)
    room_issues = health_issues(hass, host) if issues is None else issues
    return {
        "id": room.id,
        "name": room.name,
        "type": room.type,
        "type_label": label(room.type),
        "bound_slots": sum(1 for status in statuses if status["entity_id"] is not None),
        "total_slots": len(provided) if provided else len(statuses),
        "required_unbound": [
            str(status["slot"])
            for status in statuses
            if status["required"] and status["entity_id"] is None
        ],
        "mode": _room_mode(runtime),
        "active_profiles": _selections(host, room.id),
        "occupied": bool(runtime is not None and runtime.occupied),
        "auto_lighting": bool(runtime is not None and runtime.auto_lighting),
        "issue_count": sum(1 for issue in room_issues if issue["room_id"] == room.id),
    }


def room_detail(
    hass: HomeAssistant, host: OpenHouseHost, room_id: str
) -> Mapping[str, object]:
    """The room settings page's whole answer.

    The two pack-derived halves -- the installed modules and the high-level
    options -- come from the modules that own them (`ha_adapter.live_modules`,
    `ha_adapter.live_profiles`), because a pack's options and a pack's
    behaviours are one question and answering half of it here would put the two
    halves of a pack in two places.
    """
    from ha_adapter import live_modules, live_profiles

    room = host.require_room(room_id)
    schema, values = live_profiles.options(host.session, room_id=room_id)
    selection = _selections(host, room_id)
    return {
        "id": room.id,
        "name": room.name,
        "type": room.type,
        "type_label": label(room.type),
        "bindings": list(binding_statuses(hass, host, room)),
        # The house's own global slots this room's modules act through, which is
        # the list the room's page draws a "Whole house" section from. They are
        # separately listed rather than merged into `bindings` because they are
        # bound somewhere else: a global slot is written to the house and answers
        # in every room, and a page that drew it as the room's own binding would
        # make "this room" and "every room" the same control.
        "global_bindings": list(global_bindings(host, room_id)),
        "options_schema": schema,
        "options": dict(values),
        # The modules this room's page draws a card for, and the rule is
        # `reaches_room` -- the *same* rule the schema above is built with. The
        # two have to agree: the form carries a key for every pack that reaches
        # the room, and each card claims the keys of its own pack
        # (`pack_option_keys`), so a pack the form carries keys for and the list
        # does not name leaves those keys with no card to sit under, and the
        # panel collects them into one "Other settings" lump. That lump was the
        # bug: another module's switches drawn anonymously at the bottom of the
        # page, unsegregated and unattributable.
        #
        # A pack *placed in this room* is listed whether or not it reaches it --
        # a module installed here with none of its roles bound still has to be
        # switchable and removable -- and a pack placed elsewhere is listed when
        # the room binds the roles it acts through. The card says where such a
        # module lives, so the page never claims a module is in a room it is not.
        "modules": list(
            _with_slot_domains(
                host,
                [
                    module
                    for module in live_modules.installed_modules(host.session)
                    if module["room_id"] == room_id
                    or _reaches_room(host, module, room_id)
                ],
            )
        ),
        "active_profiles": selection,
        "mode": _room_mode(host.runtime_room(room_id)),
        "axes": _axes(host, selection),
        # The revision this page was drawn from, for the page to send back with
        # everything it writes: a house profile switch replaces the house under a
        # page that is still open, and the number is how the page finds out --
        # see `websocket_api._stale`.
        "revision": host.session.revision,
    }


def _reaches_room(
    host: OpenHouseHost, module: Mapping[str, object], room_id: str
) -> bool:
    """Whether `room_id`'s settings form carries `module`'s settings.

    `live_profiles.reaches_room` and nothing restated here: it is the rule the
    room's schema is built with, and the room's module list has to use the same
    one or the two disagree about which packs the room has.
    """
    from ha_adapter.live_profiles import reaches_room

    record = host.session.engine.installed.get(str(module["pack"]))
    return record is not None and reaches_room(host.session, record, room_id)


def _room_mode(runtime: RoomRuntime | None) -> str:
    """A room's mode, as the select entity holds it, or empty when it has none."""
    return "" if runtime is None else str(runtime.mode or "")


def _room_module_slots(
    host: OpenHouseHost, room_id: str
) -> tuple[frozenset[str], frozenset[str]]:
    """The slots the modules installed in this room act through, and the ones they require.

    Under `Modules` on the room's settings page, and nothing else: a device a
    room has no module for is a device nobody has asked the room to hold, and
    listing the whole published catalog -- or the room type's own shape -- put
    fifteen rows of empty slots in front of a person who had installed one
    module. The list is scoped to the room the pack was installed into, and a
    pack installed at house scope carries the empty room id and counts for every
    room, which is the same filter `room_detail` uses to list a room's modules.

    Both halves come out of one pass because they are one question asked twice:
    which slots are on the page, and which of them the room owes a device for. A
    pack's `required: true` declaration is a requirement of the rooms holding the
    pack, so `fridge_contact` is required in the room that installed the fridge
    guard and is not a missing device anywhere else.

    Keys, not written names: a declaration written `separate: true` binds under a
    pack-qualified key (`engine/declared_slots.py`), and a list of written names
    would offer the page a slot no binding can fill.

    **A hosted module counts as a module of the room.** An imported blueprint
    whose input was answered with a slot reaches through that slot exactly as a
    pack's behaviour reaches through one, and the person still has to give the
    room a device for it -- so its slots are unioned in from the host's own
    records, which is the only other place a module can come from. Without this
    the room would offer nothing to point at the module's slot, and the module
    would wait for a device the screen gave no way to bind.
    """
    from ha_adapter import live_modules, module_host

    slots: set[str] = set()
    required: set[str] = set()
    for name, room in live_modules.installed_names_by_room(host.session):
        if room not in ("", room_id):
            continue
        document = host.catalog.manifests.get(name)
        if document is None:
            continue
        slots.update(required_keys(name, document))
        slots.update(optional_keys(name, document))
        required.update(required_keys(name, document))
    for hosted in (getattr(host.runtime, "modules", None) or {}).values():
        record = getattr(hosted, "record", None)
        if record is None or record.room_id != room_id:
            continue
        # Not `required`: a module waiting on a slot is waiting, not owed. The
        # pack's `required: true` is a promise about the room's shape and this is
        # one module's unmet input, and marking it required would put a warning
        # about a missing device on a room whose module was imported a minute ago
        # and never bound on purpose.
        slots.update(
            module_host.slots_reached(
                {
                    name: module_host.InputBinding(**row)
                    for name, row in record.bindings.items()
                }
            )
        )
    return frozenset(slots), frozenset(required)


def _configurable_slots(host: OpenHouseHost, room: RoomRef) -> tuple[str, ...]:
    """The slots this room can be given a device for.

    The *installed modules'* slots and not the room type's, which is the rule the
    whole "add any module to any room" behaviour rests on: a module may be
    installed into any room, so the devices a room can hold are whatever a module
    the room actually holds could act through, and a room type is a label rather
    than a shape. The room's own bindings are unioned in as well, so a device the
    room was given before the module that needed it was removed -- or before this
    rule changed -- still shows rather than hiding behind a rule it predates; a
    binding a person cannot see is a binding a person cannot unbind.

    Sorted, because the list is room-independent and the two sources' orders
    would otherwise be concatenated rather than merged.
    """
    provided, _ = _room_module_slots(host, room.id)
    return tuple(sorted({*provided, *room.bindings}))


def binding_statuses(
    hass: HomeAssistant, host: OpenHouseHost, room: RoomRef
) -> tuple[Mapping[str, object], ...]:
    """Every slot the room may bind, in slot-name order.

    The slot list is the installed modules' and the room's own bindings', not the
    room *type*'s (`_configurable_slots`), and it is not only the bindings
    either: a room's settings page has to be able to show a slot that is empty,
    and a list built from the bindings alone would be a list of the slots someone
    already filled.

    `required` is the module's verdict with the catalog's set behind it: a slot
    an installed pack requires is required, and a slot the catalog marks required
    that happens to be on the page for another reason still reads that way. What
    is gone is the catalog's mark on a slot no installed module needs -- it is
    not on the page at all, and a room cannot be short of a device nothing asked
    it for.

    **A part of a split slot is drawn under its slot, not beside it.** The parts
    are still one role with one name (`ha_adapter.slot_parts`), so a row per
    `light_group__a` next to `light_group` would read as two roles that happen to
    share a prefix; instead the slot's row carries `parts`, each with the device
    this room bound for it. A part key the list happens to hold -- a module acting
    through one reaches it as `light_group__a` and `_room_module_slots` says so --
    is folded into its parent rather than dropped, and the parent is drawn even
    when nothing else put it on the list: a person who bound a part of a role has
    to be able to see and unbind it.
    """
    catalog = host.catalog
    entities = er.async_get(hass)
    record = host.session.slot_parts
    _, module_required = _room_module_slots(host, room.id)
    rules = _slot_rules(host, room.id)
    reach = _room_reach(host, room.id)
    configurable = _configurable_slots(host, room)
    slots = {slot for slot in configurable if slot_parts.split(record, slot) is None}
    for slot in configurable:
        named = slot_parts.split(record, slot)
        if named is not None:
            slots.add(named[0])
    result: list[Mapping[str, object]] = []
    for slot in sorted(slots):
        entity_id = room.bindings.get(slot)
        accepted = _slot_domains(host, slot)
        status: Mapping[str, object] = {
            "slot": slot,
            "label": label(slot),
            "required": slot in module_required
            or catalog.slot_required.get(slot, False),
            "accepts_domains": list(accepted),
            "entity_id": None,
            "registry_id": None,
            "friendly_name": None,
            "domain": None,
            "state": None,
            "status": "unbound",
            "last_changed": None,
            "modules": reach.get(slot, ()),
            "parts": list(_part_statuses(hass, host, room, slot, accepted, entities)),
            **_rule_status(rules.get(slot)),
        }
        if entity_id is not None:
            status = {
                **status,
                "entity_id": entity_id,
                **_bound_status(hass, entities, entity_id, accepted),
            }
        result.append(status)
    return tuple(result)


def _slot_domains(host: OpenHouseHost, slot: str) -> tuple[str, ...]:
    """What a slot accepts, following a *part* back to the slot it was split from.

    The catalog is where a slot's domains come from, and it has no entry for a key
    a person invented -- so a part answered by its own key would accept *anything*,
    which is the one answer that is wrong: a half of the lights is still lights. A
    part is the same role as its parent (`ha_adapter.slot_parts`), and this is the
    one place that follows the name back so every reader agrees.

    The record decides, not the shape of the string (`slot_parts.split`): a pack's
    own device binds under a key with the same joiner in it and is a slot of its
    own, with its own catalog entry.
    """
    named = slot_parts.split(host.session.slot_parts, slot)
    parent = slot if named is None else named[0]
    return tuple(host.catalog.slot_domains.get(parent, ()))


def _part_statuses(
    hass: HomeAssistant,
    host: OpenHouseHost,
    room: RoomRef,
    slot: str,
    accepted: tuple[str, ...],
    entities: er.EntityRegistry,
) -> tuple[Mapping[str, object], ...]:
    """Every part this slot has been split into, each with the room's device for it.

    A part's device is a binding of its own, looked up under the part's key
    (`light_group__a`), which is what makes two modules sharing a part provably act
    on one entity: there is one binding to resolve, not one per module.

    The **domains are the parent's**, passed in rather than looked up: the
    vocabulary deliberately carries none (`this module's docstring`) and the
    catalog has no entry for a key a person invented, so a part of `light_group`
    accepts what `light_group` accepts -- which is the true answer, because a part
    is the same role.

    `status` is HA's reading of whatever is bound, and `"unbound"` when nothing is,
    the same closed set the slot's own row uses. A part with no device is
    deliberately not an error: a person who splits a role and binds one half of it
    has said something true about a house that is half-built.
    """
    rows: list[Mapping[str, object]] = []
    for name in slot_parts.parts_of(host.session.slot_parts, slot):
        key = slot_parts.key_of(slot, name)
        entity_id = room.bindings.get(key)
        row: Mapping[str, object] = {
            "slot": key,
            "name": name,
            "label": label(name),
            "entity_id": entity_id,
            "registry_id": None,
            "friendly_name": None,
            "domain": None,
            "state": None,
            "status": "unbound",
            "last_changed": None,
        }
        if entity_id is not None:
            row = {
                **row,
                **_bound_status(hass, entities, entity_id, accepted),
            }
        rows.append(row)
    return tuple(rows)


def _room_reach(host: OpenHouseHost, room_id: str) -> Mapping[str, tuple[str, ...]]:
    """Which modules act through each of this room's slots, by slot key.

    Drawn as chips under a slot's name, and it answers the question the row
    raises: a person reading "Light group" needs "the bedtime button shuts them"
    beside it, exactly as the House tab's global slots carry theirs.

    **Two sources, because a room's modules are two kinds of thing.** A declared
    pack reaches through the slots its behaviours name (`live_modules
    .slots_reached_by`, which owns every key and display-name rule), and an
    *imported* module reaches through the slots its inputs were answered with
    (`module_host.slots_reached` on its own bindings, the same join
    `_room_module_slots` makes). A room whose module was imported is the common
    case, and a chip list that could only name packs would leave that module's
    slot looking like something nobody acts through.

    Imported modules are taken at the placement `_room_module_slots` takes them
    at -- the module is *in this room* -- for the reason that function gives: an
    imported module belongs to one room and one room has to give it devices. A
    declared pack is taken at `live_modules.slots_reached_by`'s two placements,
    which is the filter the room's slot *list* already uses, so a chip and a row
    cannot appear apart.
    """
    from ha_adapter import live_modules, module_host

    found: dict[str, set[str]] = {}
    for slot, names in live_modules.slots_reached_by(
        host.session, room_id=room_id
    ).items():
        found.setdefault(slot, set()).update(names)
    for hosted in (getattr(host.runtime, "modules", None) or {}).values():
        record = getattr(hosted, "record", None)
        if record is None or record.room_id != room_id:
            continue
        title = str(record.title or record.slug)
        for slot in module_host.slots_reached(
            {
                name: module_host.InputBinding(**row)
                for name, row in record.bindings.items()
            }
        ):
            found.setdefault(slot, set()).add(title)
    return {slot: tuple(sorted(names)) for slot, names in found.items()}


def _slot_rules(host: OpenHouseHost, room_id: str) -> Mapping[str, slot_rules.SlotRule]:
    """The rule each of this room's slots is decided by, by slot key.

    The room's slot *table* draws one row per slot, and a rule is held by a
    *module* (`live_modules.slot_rules_of`), so the two are joined here rather
    than in the row loop: the row asks "am I decided by logic" and this answers
    from whichever installed module of the room holds a rule on that key.

    The packs are taken in name order so a slot two modules both hold a rule on
    reports the same one on two readings -- the room's table can only say one
    sentence about a slot, and a sentence that changes between two draws of the
    same house would be worse than one that names the first module. The first
    module's card is where the other rule is shown, in full.

    The same set of packs the room's slot list is built from: a module installed
    into this room, and one installed at house scope (the empty room id), which
    counts for every room exactly as it does in `_room_module_slots`.
    """
    from ha_adapter import live_modules

    found: dict[str, slot_rules.SlotRule] = {}
    for name, scoped_room in live_modules.installed_names_by_room(host.session):
        if scoped_room not in ("", room_id):
            continue
        for slot, rule in live_modules.slot_rules_of(
            host.session, pack=name, room_id=room_id
        ).items():
            found.setdefault(slot, rule)
    return found


def _rule_status(rule: slot_rules.SlotRule | None) -> Mapping[str, object]:
    """A slot row's four rule keys, empty when the slot is a plain device.

    The same four `live_modules._module_slots` reports on a module's own row, and
    for the same reason: the two tables are the same question asked of the same
    slot, so a slot decided by a template has to read that way whichever page is
    open. `rule_picks_device` is the one a page branches on -- a template or a
    script decides *what* the slot is and the device under it is an answer, while
    a condition decides *whether* and the device under it is the room's.
    """
    if rule is None:
        return {
            "rule_kind": None,
            "rule_summary": None,
            "rule_picks_device": None,
            "rule_device": None,
        }
    device = slot_rules.device_of(rule)
    return {
        "rule_kind": rule.kind,
        "rule_summary": slot_rules.summary(rule),
        "rule_picks_device": slot_rules.picks_the_device(rule),
        "rule_device": device or None,
    }


def _bound_status(
    hass: HomeAssistant,
    entities: er.EntityRegistry,
    entity_id: str,
    accepted: tuple[str, ...],
) -> Mapping[str, object]:
    """How one bound entity stands, in the panel's closed set of statuses."""
    entry = entities.async_get(entity_id)
    state = hass.states.get(entity_id)
    domain = domain_of(entity_id)
    return {
        "registry_id": None if entry is None else entry.id,
        "friendly_name": (
            None
            if state is None
            else str(state.attributes.get("friendly_name", entity_id))
        ),
        "domain": domain,
        "state": None if state is None else state.state,
        "last_changed": None if state is None else state.last_changed.isoformat(),
        "status": _status_of(state, domain, accepted),
    }


def _status_of(state: Any, domain: str, accepted: tuple[str, ...]) -> str:
    """The status a bound entity reports, checked in the order the meaning is.

    See the module docstring: the order is the point, because a binding can be
    several of these at once and the first one that is true is the most useful
    thing to say.
    """
    if state is None:
        return "missing"
    if accepted and domain not in accepted:
        return "domain_mismatch"
    if state.state == "unavailable":
        return "unavailable"
    if state.state == "unknown":
        return "unknown"
    return "ok"


def house_scope(host: OpenHouseHost) -> Mapping[str, object]:
    """The House tab's answer, with the catalog's domains joined in.

    `live_modules.house_scope` builds the rows -- the global binding, its live
    state, the modules that reach the role -- from the engine's own vocabulary,
    which deliberately carries no domains (`engine/vocabulary.py`). The slot
    *rows* need them: the bind picker says what a slot accepts, and a house slot
    drawn without that says "any device" for a role that only takes lights. The
    catalog is Home Assistant's half (`host.catalog`), so the join happens here,
    the same place a room's bindings get theirs.
    """
    from ha_adapter import live_modules

    scope = dict(live_modules.house_scope(host.session))
    catalog = host.catalog
    rows = cast("Sequence[Mapping[str, object]]", scope["slots"])
    scope["slots"] = tuple(
        {
            **row,
            "accepts_domains": list(catalog.slot_domains.get(str(row["slot"]), ())),
            # A part is the *same* role, so it accepts what the role accepts: the
            # catalog has no entry for a key a person invented, and a part drawn
            # as "any device" would offer every entity in the house for a half of
            # the lights. The parent's domains, not a lookup by the part's key.
            "parts": _with_part_domains(host, str(row["slot"]), row.get("parts", ())),
        }
        for row in rows
    )
    # The module rows carry slots too, and the same picker draws them, so they
    # need the same join: a House tab that named the role's domains and a module
    # card beside it that did not would be two answers to one question.
    scope["modules"] = list(
        _with_slot_domains(
            host, cast("Sequence[Mapping[str, object]]", scope["modules"])
        )
    )
    # The revision this page was drawn from, for the page to send back with
    # everything it writes. See `room_detail` and `websocket_api._stale`.
    scope["revision"] = host.session.revision
    return scope


def _with_part_domains(
    host: OpenHouseHost, parent: str, parts: object
) -> tuple[Mapping[str, object], ...]:
    """Each of a slot's parts with the *parent's* accepted domains joined in.

    A part is the same role as the slot it was split from
    (`ha_adapter.slot_parts`), so it accepts what the role accepts -- `_slot_domains`
    is where that is worked out, and it is the same answer a room's part rows and
    the bind picker get, so the three cannot disagree about what a half of the
    lights takes.
    """
    accepted = list(_slot_domains(host, parent))
    return tuple(
        {**cast("Mapping[str, object]", part), "accepts_domains": accepted}
        for part in cast("Sequence[Mapping[str, object]]", parts)
    )


def _with_slot_domains(
    host: OpenHouseHost, modules: Sequence[Mapping[str, object]]
) -> tuple[Mapping[str, object], ...]:
    """Each module's slot rows, with the catalog's accepted domains joined in.

    `live_modules` builds a slot row from the engine's vocabulary, which carries
    no domains on purpose (`engine/vocabulary.py`). The *picker* needs them: what
    a slot accepts is what Home Assistant's own entity selector is filtered by
    (`accepts_domains`), so a row drawn without them would offer every device in
    the house for a role that only takes lights. The catalog is Home Assistant's
    half (`host.catalog`), so the join happens here, where a room's bindings and
    the house's own slots already get theirs, rather than in the adapter that
    reads the engine.
    """
    catalog = host.catalog
    return tuple(
        {
            **module,
            "slots": tuple(
                {
                    **slot,
                    "accepts_domains": list(
                        catalog.slot_domains.get(str(slot["slot"]), ())
                    ),
                }
                for slot in cast("Sequence[Mapping[str, object]]", module["slots"])
            ),
        }
        for module in modules
    )


def global_bindings(
    host: OpenHouseHost, room_id: str
) -> tuple[Mapping[str, object], ...]:
    """The house's own slots, as this room's page draws them.

    A room's page is where a person is standing when they think "this room's
    lights", so it is also where they have to be able to see that the *house* has
    a global `light_group` that every room falls back to -- and to set it, which
    is why the row carries the house's binding and not a summary. The rows are
    `house_scope`'s, both halves: the same role, the same entity and the same live
    status the House tab draws, so a slot cannot read one way on one screen and
    another way on the other.

    Narrowed to the slots *this room's* modules act through, because this page is
    about this room: a global role nothing here reaches is a control with nothing
    behind it, and the House tab is where the whole-house view already lives.

    `room_entity_id` is what the room bound for the same name itself, when it
    bound anything, because that is the one place a global binding does not
    reach: the room's own answer stands in front of it. It is the fact a person
    needs before wondering why the room's lights did not move when they set the
    house's.
    """
    room = host.require_room(room_id)
    provided, _ = _room_module_slots(host, room_id)
    reached = set(provided)
    rows = cast("Sequence[Mapping[str, object]]", house_scope(host)["slots"])
    return tuple(
        {
            **row,
            "room_entity_id": room.bindings.get(str(row["slot"])),
            "overridden": room.bindings.get(str(row["slot"])) is not None,
        }
        for row in rows
        if str(row["slot"]) in reached
    )


def candidates(
    hass: HomeAssistant,
    host: OpenHouseHost,
    *,
    room_id: str,
    slot: str,
    query: str | None = None,
    limit: int | None = None,
) -> tuple[Mapping[str, object], ...]:
    """The devices the server proposes for a slot, best first.

    The set is every entity the *house* holds (`entity_ids_house`), for a room's
    slot as much as for a global one: a room's picker is a starting point, not a
    fence. The devices a person has put in a room and the devices Home Assistant
    has *filed* under that room's area are two different things, and only the
    second is something HA can be asked about -- a room whose area holds nothing
    (a spare room, a house whose devices are filed per device rather than per
    entity) offered no device at all for any slot, which reads as the panel
    having lost devices the person can see in Home Assistant. Narrowing a
    binding to the room it is made from is also not this function's to enforce:
    the binding belongs to the room either way, and a person pointing their
    spare room's lamp at a lamp in the hall is using their own house.

    The room's own devices come *first*, ranked among themselves by the same
    rule as everything else, so the top of the list is still the guess the setup
    flow would have made for that room (`rank_candidates`' "one rule, two
    readers") and the room a person is working in is the group they see first.

    `room_id` may be `HOUSE`, for a global slot, which belongs to no room: its
    list is the house's and there is no room's own devices to put in front,
    because "the house's lights" is a role the whole house fills and putting one
    area first would be picking a room on the person's behalf.

    The ordering is `rank_candidates`', the same call the setup flow's guess is
    the winner of, so the picker a person is shown ranks the way the flow would
    have guessed.
    """
    domains = _slot_domains(host, slot)
    names = _friendly_names(hass)
    house_ids = entity_ids_house(hass)
    if room_id == HOUSE:
        ranked = rank_candidates(
            slot=slot,
            domains=domains,
            entity_ids=house_ids,
            names=names,
            query=query,
            limit=limit,
        )
    else:
        own = set(entity_ids_in_area(hass, host.require_room(room_id).area_id))
        ranked = rank_candidates(
            slot=slot,
            domains=domains,
            entity_ids=house_ids,
            names=names,
            query=query,
        )
        # `sorted` is stable, so each group keeps the ranking it arrived with and
        # `limit` is applied to the room's devices first, the rest after -- which
        # is what makes the truncation cut the tail rather than the room.
        ranked = tuple(
            sorted(ranked, key=lambda candidate: candidate.entity_id not in own)
        )
        if limit is not None:
            ranked = ranked[:limit]
    entities = er.async_get(hass)
    return tuple(
        {
            "entity_id": candidate.entity_id,
            "registry_id": (
                None
                if (entry := entities.async_get(candidate.entity_id)) is None
                else entry.id
            ),
            "friendly_name": candidate.friendly_name,
            "domain": candidate.domain,
            "score": candidate.score,
        }
        for candidate in ranked
    )


def _friendly_names(hass: HomeAssistant) -> Mapping[str, str]:
    """Every entity's friendly name, for the picker's labels."""
    names: dict[str, str] = {}
    for state in hass.states.async_all():
        name = state.attributes.get("friendly_name")
        if isinstance(name, str):
            names[state.entity_id] = name
    return names


# -- Health -----------------------------------------------------------------


def health_issues(
    hass: HomeAssistant, host: OpenHouseHost
) -> tuple[Mapping[str, object], ...]:
    """Every issue the Health tab shows, from the engine and from Repairs.

    **The engine's two reads are wrapped, and that is this function's job and
    not `room_summaries`'.** `room_summaries` computes the issues once and hands
    the same tuple to every row, so a read that raised here would take the whole
    Rooms tab down -- which is exactly what a device leaving the house used to
    do: the engine's repair read raised out of a slot whose member was gone, and
    the tab came back empty. The engine's own read path no longer raises for that
    case (`engine/binding.py` reads a name the house does not hold as
    present-and-unreadable), but the tab must not *depend* on that holding for
    every read a later phase adds. A read that fails unexpectedly is a health
    issue -- that is what the Health tab is for -- and it is reported as one row
    rather than allowed to empty the screen it is meant to fill.
    """
    issues: list[Mapping[str, object]] = []
    try:
        repairs = host.session.engine.repairs()
    except Exception as error:  # a view model reports, it does not raise
        issues.append(_engine_read_failed("repairs", error))
    else:
        issues.extend(_repair_issue(host, repair) for repair in repairs)
    try:
        hazards = host.session.engine.hazards()
    except Exception as error:  # a view model reports, it does not raise
        issues.append(_engine_read_failed("hazards", error))
    else:
        issues.extend(_hazard_issue(hass, hazard, host) for hazard in hazards)
    issues.extend(_registry_issues(hass))
    return tuple(issues)


def _engine_read_failed(read: str, error: Exception) -> Mapping[str, object]:
    """An engine read that raised, as the Health tab's one row.

    A row and not a raise, for the reason `health_issues` gives: the tab exists
    to say what is wrong with the house, and "the engine could not be read" is
    one of those things. The message names the read and the exception rather
    than guessing at a cause, because the person reading it is the one who has
    to act on it and a tidied-up sentence would be a guess dressed as a
    diagnosis. It is attributed to no room -- the failure is the whole read, not
    one room's -- so the Rooms tab shows no room's count moving for it.
    """
    return {
        "severity": "error",
        "code": _ENGINE_READ_FAILED,
        "title": "The house could not be read",
        "detail": f"The engine's {read} read failed: {error}",
        "room_id": None,
        "entity_id": None,
        "repairs_flow_id": None,
    }


def _repair_issue(host: OpenHouseHost, repair: Any) -> Mapping[str, object]:
    """One dead sensor, as the Health tab's row.

    `repairs_flow_id` is the Repairs issue this row is about, named with the same
    helper that raised it (`repairs.issue_id`), so the panel's link opens the
    repair a person is being told to look at rather than a string that merely
    looks similar.
    """
    room = host.room(repair.room_id)
    named = room.name if room is not None else repair.room_id
    return {
        "severity": _REPAIR_SEVERITY,
        "code": OCCUPANCY_SENSOR_UNAVAILABLE,
        "title": "A sensor stopped answering",
        "detail": (
            f"The {label(repair.slot)} in {named} ({repair.entity_id}) is not "
            "reporting, so the room cannot be read."
        ),
        "room_id": repair.room_id,
        "entity_id": repair.entity_id,
        "repairs_flow_id": (
            None
            if room is None
            else issue_id(OCCUPANCY_SENSOR_UNAVAILABLE, room.area_id)
        ),
    }


def _hazard_issue(
    hass: HomeAssistant, hazard: Any, host: OpenHouseHost
) -> Mapping[str, object]:
    """One alarming detector, as the Health tab's row.

    An error and not a warning: a hazard is the one thing in a house worth
    interrupting someone over, and a severity that made it a warning would put it
    beside a dead sensor in the same list.
    """
    state = hass.states.get(hazard.entity_id)
    named = hazard.entity_id
    if state is not None:
        named = str(state.attributes.get("friendly_name", hazard.entity_id))
    return {
        "severity": "error",
        "code": f"hazard_{hazard.kind}",
        "title": _HAZARD_TITLES.get(hazard.kind, f"{label(hazard.kind)} detected"),
        "detail": f"{named} is alarming.",
        "room_id": _room_for_entity(host, hazard.entity_id),
        "entity_id": hazard.entity_id,
        "repairs_flow_id": None,
    }


def _registry_issues(hass: HomeAssistant) -> tuple[Mapping[str, object], ...]:
    """The Repairs this integration has raised, read back from the registry.

    The registry is keyed by `(domain, issue_id)` and not by a dotted string, so
    the filter is on the pair: a key that happened to start with the same letters
    would otherwise be swept in, which is the kind of mistake that shows another
    integration's repairs under this one's heading.
    """
    registry = ir.async_get(hass)
    result: list[Mapping[str, object]] = []
    for (domain, key), issue in registry.issues.items():
        if domain != DOMAIN:
            continue
        placeholders = issue.translation_placeholders or {}
        area_id = str(placeholders.get("room", ""))
        entity_id = placeholders.get("entity_id")
        result.append(
            {
                "severity": _SEVERITIES.get(str(issue.severity), "warning"),
                "code": issue.translation_key or NO_OCCUPANCY_SENSOR,
                "title": _registry_title(issue.translation_key, area_id),
                "detail": str(entity_id or area_id),
                "room_id": None,
                "entity_id": None if entity_id is None else str(entity_id),
                "repairs_flow_id": key,
            }
        )
    return tuple(result)


def _registry_title(translation_key: str | None, area_id: str) -> str:
    """A short headline for a Repairs issue, from its key rather than its prose.

    The issue's own message is Home Assistant's to translate and is not carried
    on the registry entry, so the title is built from the key -- which is why the
    two keys in `repairs.py` read as sentences rather than as ids.
    """
    if translation_key == OCCUPANCY_SENSOR_UNAVAILABLE:
        return f"{area_id} cannot read its sensor"
    if translation_key == NO_OCCUPANCY_SENSOR:
        return f"{area_id} has no occupancy sensor"
    return label(translation_key or "issue")


def _room_for_entity(host: OpenHouseHost, entity_id: str) -> str | None:
    """The room a bound entity belongs to, or `None` when none binds it."""
    for room in host.rooms.values():
        if entity_id in room.bindings.values():
            return room.id
    return None


# -- The pack registry -------------------------------------------------------


def pack_path(
    host: OpenHouseHost, pack: str, *, tier: str | None = None
) -> Path | None:
    """The manifest file a registry entry for `pack` names, or `None`.

    Resolves *the row a person chose* rather than whatever `packs/` happens to
    hold, which is what makes "you can install what was offered" true. A `tier`
    narrows it to one registry entry when a name is listed more than once.
    """
    for record in host.catalog.records:
        if record.get("name") != pack:
            continue
        if tier is not None and record.get("tier") != tier:
            continue
        return host.catalog.paths.get(pack)
    return None


# -- The catalog, read once --------------------------------------------------
#
# Everything below this line touches the disk; everything above it answers a
# request. That is the whole of the split, and it is why `load_catalog` is one
# function rather than a reader per screen: a request path that called any of
# these would be a blocking call on the event loop, which is a bug in a house
# with one light and an outage in a house with a hundred.


@dataclass(frozen=True, slots=True)
class Catalog:
    """The frozen vocabulary and the pack registry, as the panel needs them.

    Read from the checkout at setup and never re-read, which is what makes a
    request pure. The registry is carried as *parsed* manifests rather than as
    paths, for the same reason: a screen that re-parsed ten YAML files would be
    ten blocking reads per request, and the ten files do not change while Home
    Assistant is running -- installing a pack writes the engine's record of it,
    not the registry.

    Empty is a real value and means the checkout has no catalog, which
    `async_setup_host` already treats as "no session": a `Catalog` with nothing in
    it is what a caller sees in the window between the entry loading and the
    catalog being read, and answering emptily is better than raising in a screen.
    """

    room_types: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    slot_domains: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Slot name -> whether `catalog/slots.yaml` marks it required. Read behind
    #: the installed modules rather than in front of them: a room's page lists
    #: the slots its modules act through (`_room_module_slots`) and this only
    #: says which of those the catalog also calls required.
    slot_required: Mapping[str, bool] = field(default_factory=dict)
    #: The registry index's entries, in the file's order.
    records: tuple[Mapping[str, Any], ...] = ()
    #: Pack name -> its manifest document, for the records that point at a file.
    manifests: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Pack name -> the manifest's path on this machine.
    paths: Mapping[str, Path] = field(default_factory=dict)


def load_catalog(root: Path | None) -> Catalog:
    """Read the catalog and the pack registry from `root`. Blocking; run off-loop.

    Every reader here tolerates a missing file by answering emptily rather than
    raising, because the shapes differ: a checkout with no `registry/index.json`
    is a checkout that has not built its registry yet, and that is a Store tab
    with nothing in it rather than a setup failure that would take the whole
    integration down with it.
    """
    if root is None:
        return Catalog()
    records = _registry_records(root)
    manifests: dict[str, Mapping[str, Any]] = {}
    paths: dict[str, Path] = {}
    for record in records:
        name = str(record.get("name", ""))
        relative = record.get("path")
        if not name or not isinstance(relative, str):
            continue
        path = root / relative
        if path.is_file():
            paths[name] = path
            manifests[name] = _document(path)
    return Catalog(
        room_types=load_room_types(root),
        slot_domains=load_slot_domains(root),
        slot_required=_slot_required(root),
        records=records,
        manifests=manifests,
        paths=paths,
    )


def _registry_records(root: Path) -> tuple[Mapping[str, Any], ...]:
    """Every entry of the pack registry's index, in the file's order."""
    document = _document(root / "registry" / "index.json", json.loads)
    entries = document.get("entries", ())
    if not isinstance(entries, (list, tuple)):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, Mapping))


def _document(path: Path, parse: Any = None) -> Mapping[str, Any]:
    """A YAML or JSON document as a mapping, or empty when it is neither.

    One reader for both formats because the two callers differ only in which
    parser they hand it -- and a manifest that is a list rather than a mapping is
    a real thing someone writes, which is the case this refuses rather than a
    reader per format.
    """
    if not path.is_file():
        return {}
    try:
        document = (parse or yaml.safe_load)(path.read_text("utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return document if isinstance(document, Mapping) else {}


def _slot_required(root: Path) -> Mapping[str, bool]:
    """The catalog's `required` flag per slot.

    Read here rather than from `ha_adapter.setup_flow`, which loads the same file
    for the domains and does not carry this field: a required slot the panel
    could not name would be a room that never looks finished, and the alternative
    -- a second `slots.yaml` reader in `ha_adapter` -- would be a loader the flow
    does not use and therefore would not keep honest.
    """
    document = _document(root / "catalog" / "slots.yaml")
    slots = document.get("slots", ())
    if not isinstance(slots, (list, tuple)):
        return {}
    return {
        str(entry["name"]): bool(entry.get("required", False))
        for entry in slots
        if isinstance(entry, Mapping) and "name" in entry
    }


# -- Profiles ---------------------------------------------------------------


def _selections(host: OpenHouseHost, room_id: str) -> Mapping[str, str]:
    """The profiles a room is on, per axis. Empty for a room on none."""
    return dict(host.session.profiles.selection(room_id))


def _axes(
    host: OpenHouseHost, selection: Mapping[str, str]
) -> tuple[Mapping[str, object], ...]:
    """The configurability axes a room's profiles are grouped under.

    Built from the profile set rather than from a constant, so a pack that
    declares an axis the panel has never heard of renders under its own heading
    with no panel change -- which is the property that makes "no pack-supplied
    JS" affordable.

    `active` is recomputed against *this room's* selection rather than taken from
    the profile listing, which answers for the house: a lighting profile can be
    the house's without being the kitchen's, and a panel that showed it as
    selected in both would be wrong in the room a person was looking at.
    """
    from ha_adapter import live_profiles

    axes: dict[str, list[Mapping[str, object]]] = {}
    for profile in live_profiles.profiles(host.session):
        axis = profile.get("axis")
        if not isinstance(axis, str) or profile.get("kind") != "room":
            continue
        axes.setdefault(axis, []).append(
            {**profile, "active": selection.get(axis) == profile.get("name")}
        )
    return tuple(
        {"id": axis, "label": label(axis), "profiles": profiles}
        for axis, profiles in sorted(axes.items())
    )
