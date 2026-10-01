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
from typing import Any

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from engine.adapter import domain_of
from ha_adapter.composition import mode_name
from ha_adapter.setup_flow import (
    Candidate,
    load_room_types,
    load_slot_domains,
    rank_candidates,
)

from .const import DOMAIN
from .host import OpenHouseHost, RoomRef
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
    "store_index",
]

#: The severity the engine's own repairs are reported at. A room that cannot be
#: read is a warning and not an error: the house still works, it just cannot see
#: that room, and an error would put it in the same list as a fire.
_REPAIR_SEVERITY = "warning"

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
) -> Mapping[str, object]:
    """Who is looking, and what they may do.

    Every field is passed in rather than read here, because two of the five come
    from the *connection* -- which user authenticated and whether they are an
    admin -- and a projection that reached into `hass` for them would answer for
    the wrong person on a multi-user instance.
    """
    return {
        "admin": admin,
        "user_name": user_name,
        "version": version,
        "engine_api": engine_api,
        "needs_setup": needs_setup,
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
    provided = host.catalog.room_types.get(room.type, ())
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
        "options_schema": schema,
        "options": dict(values),
        "modules": [
            module
            for module in live_modules.installed_modules(host.session)
            if module["room_id"] in ("", room_id)
        ],
        "active_profiles": selection,
        "mode": _room_mode(host.runtime_room(room_id)),
        "axes": _axes(host, selection),
    }


def _room_mode(runtime: RoomRuntime | None) -> str:
    """A room's mode, as the select entity holds it, or empty when it has none."""
    return "" if runtime is None else str(runtime.mode or "")


def binding_statuses(
    hass: HomeAssistant, host: OpenHouseHost, room: RoomRef
) -> tuple[Mapping[str, object], ...]:
    """Every slot the room's type provides, in the catalog's order.

    The slot list is the *type's*, not the room's bindings: a room's settings
    page has to be able to show a slot that is empty, and a list built from the
    bindings would be a list of the slots someone already filled.
    """
    catalog = host.catalog
    entities = er.async_get(hass)
    result: list[Mapping[str, object]] = []
    for slot in catalog.room_types.get(room.type, ()):
        entity_id = room.bindings.get(slot)
        accepted = tuple(catalog.slot_domains.get(slot, ()))
        status: Mapping[str, object] = {
            "slot": slot,
            "label": label(slot),
            "required": catalog.slot_required.get(slot, False),
            "accepts_domains": list(accepted),
            "entity_id": None,
            "registry_id": None,
            "friendly_name": None,
            "domain": None,
            "state": None,
            "status": "unbound",
            "last_changed": None,
        }
        if entity_id is not None:
            status = {
                **status,
                "entity_id": entity_id,
                **_bound_status(hass, entities, entity_id, accepted),
            }
        result.append(status)
    return tuple(result)


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

    Only entities Home Assistant files under the room's *area* are proposed,
    which is the same candidate set the setup flow guesses from: a device that
    exists but is filed elsewhere is a device the room has not been told about,
    and proposing it would quietly move a device into a room by binding it.

    The ordering is `rank_candidates`', the same call the setup flow's guess is
    the winner of, so the picker a person is shown ranks the way the flow would
    have guessed.
    """
    room = host.require_room(room_id)
    domains = host.catalog.slot_domains.get(slot, ())
    ranked: tuple[Candidate, ...] = rank_candidates(
        slot=slot,
        domains=domains,
        entity_ids=_area_entity_ids(hass, room.area_id),
        names=_friendly_names(hass),
        query=query,
        limit=limit,
    )
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


def _area_entity_ids(hass: HomeAssistant, area_id: str) -> tuple[str, ...]:
    """Every entity Home Assistant files under an area, sorted for determinism."""
    entries = er.async_entries_for_area(er.async_get(hass), area_id)
    return tuple(sorted(entry.entity_id for entry in entries))


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
    """Every issue the Health tab shows, from the engine and from Repairs."""
    issues: list[Mapping[str, object]] = []
    for repair in host.session.engine.repairs():
        issues.append(_repair_issue(host, repair))
    for hazard in host.session.engine.hazards():
        issues.append(_hazard_issue(hass, hazard, host))
    issues.extend(_registry_issues(hass))
    return tuple(issues)


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


# -- Store ------------------------------------------------------------------


def store_index(hass: HomeAssistant, host: OpenHouseHost) -> Mapping[str, object]:
    """The Store tab's one answer: every pack the registry knows, with verdicts.

    Built from the catalog the host read at setup, so a Store tab is a join and
    not a disk read. A pack whose manifest could not be read is still listed, with
    its `available` flag false: an entry that vanished from the list would read as
    a pack that was withdrawn, and "the file is not on this machine" is a
    different and much more ordinary thing to be true of a checkout.
    """
    entries = [_store_entry(record, host) for record in host.catalog.records]
    return {
        "entries": entries,
        "generated_at": dt_util.utcnow().isoformat(),
        # Nothing is fetched, so nothing is ever stale in the sense this flag
        # means. It is `False` rather than absent because the panel shows an
        # offline badge from it, and an absent field is a broken template.
        "cached": False,
    }


def pack_path(
    host: OpenHouseHost, pack: str, *, tier: str | None = None
) -> Path | None:
    """The manifest file a registry entry for `pack` names, or `None`.

    Not part of the panel's model: `StoreEntry` carries no path, because a path is
    a fact about this machine and the panel must not depend on one. Installing a
    pack needs it, so it is a second read of the same index the Store tab is built
    from -- one that resolves *the row a person clicked* rather than whatever
    `packs/` happens to hold, which is what makes "you can install what the Store
    lists" true.
    """
    for record in host.catalog.records:
        if record.get("name") != pack:
            continue
        if tier is not None and record.get("tier") != tier:
            continue
        return host.catalog.paths.get(pack)
    return None


def _store_entry(
    record: Mapping[str, Any], host: OpenHouseHost
) -> Mapping[str, object]:
    """One registry record, joined to the pack file and the installed set."""
    name = str(record.get("name", ""))
    version = str(record.get("version", ""))
    installed = host.session.installed.get(name)
    manifest = host.catalog.manifests.get(name, {})
    return {
        "pack": name,
        "name": _localized(manifest, "pack", name),
        "description": _localized(
            manifest, "description", str(manifest.get("description", ""))
        ),
        "version": version,
        "author": str(manifest.get("author", "")),
        "tier": str(record.get("tier", "community")),
        "license": str(manifest.get("license", "")),
        "available": bool(manifest),
        "installed_version": None if installed is None else installed.version,
        "update_available": installed is not None and installed.version != version,
        "update_requires_review": _widens_permissions(installed, manifest),
        "abandoned": False,
        "sha256": str(record.get("sha256", "")),
    }


def _localized(manifest: Mapping[str, Any], key: str, fallback: str) -> str:
    """A manifest's English string for `key`, from its `i18n.default` block."""
    block = manifest.get("i18n")
    if isinstance(block, Mapping):
        default = block.get("default")
        if isinstance(default, Mapping) and isinstance(default.get(key), str):
            return str(default[key])
    return fallback


def _widens_permissions(installed: Any, manifest: Mapping[str, Any]) -> bool:
    """Whether an available update asks for something the installed one did not.

    The spec makes a widening update opt-in, so the panel has to be told before
    it offers one. `installed` is the record the engine holds; its declared
    permissions are what the running pack was allowed, and anything the manifest
    asks for that the running one did not is a new permission.
    """
    if installed is None:
        return False
    held = getattr(installed, "permissions", None)
    if not isinstance(held, (list, tuple, set, frozenset)):
        return False
    declared = manifest.get("permissions")
    if not isinstance(declared, (list, tuple)):
        return False
    return any(item not in held for item in declared)


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
    paths, for the same reason: a `store/index` that re-parsed ten YAML files
    would be ten blocking reads per screen, and the ten files do not change while
    Home Assistant is running -- installing a pack writes the engine's record of
    it, not the registry.

    Empty is a real value and means the checkout has no catalog, which
    `async_setup_host` already treats as "no session": a `Catalog` with nothing in
    it is what a caller sees in the window between the entry loading and the
    catalog being read, and answering emptily is better than raising in a screen.
    """

    room_types: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    slot_domains: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
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
