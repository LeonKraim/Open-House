"""Modules: the packs a live house holds, and the packs a room could hold.

The panel's Modules tab answers "what is in the house, and where"; its "Add
module to room" screen answers "what could this room hold, and would it fight
with something already in". Both are the same two facts read from opposite
directions -- a pack and the room it acts in -- and this module is the live half
of them, the operations a session needs where `openhouse/facade.py`'s
`install_pack` and `uninstall_pack` are the simulator's whole answer.

**A pack lands in the house, and a module belongs to a room.** That asymmetry is
the one thing this module has to say, and it is the engine's shape rather than
this module's choice: `engine/install.py` keys the installed set by pack name,
because `Installing another version of a pack is a change to that pack` and one
house holds one version of one pack. The panel, meanwhile, installs a module
*into a room* -- `open_house/modules/install` carries a `room_id` -- because a
pack's point is the room it acts in. So a module is a pack the house holds plus
the room its bound entities live in, and `_module_room` is that join: the first
room, in the session's order, whose bindings contain *every* entity the pack's
slots reached. A pack whose slots are spread over two rooms belongs to neither
and reads as the empty room id, which is the honest answer rather than picking
one of them.

**Installing is a rebuild and everything else is not.** `install` and
`uninstall` change the installed set, which is wiring -- the engine is a pure
function of the state it starts from (`engine/engine.py`), so the only way to
change it is `session.set_installed`, which builds the next engine.
`set_enabled` changes a *flag*, which is engine state and not wiring, so it
writes an override through `engine.settings` and rebuilds nothing; a rebuild
would throw away the mode, the dwell timers and the override records of a house
that is running, to change one boolean. That is exactly the split
`ha_adapter/composition.py` already draws for a room's auto-lighting switch, and
this module follows it rather than inventing a second convention.

**Nothing here is the engine's decision.** A pack that is installed is a pack
whose behaviours are registered and *disabled* -- installation is not activation
(`product-invariants`) -- so `install` reports `enabled: False` for the pack and
for each of its behaviours, and only `set_enabled` turns one on. The flags are
written under `engine.behaviours.enable_key` at `RoomScope(room_id)`, which is
the same key and the same scope `composition.AUTO_LIGHTING_BEHAVIOURS` writes
for the built-in units, so a pack's behaviour and a shipped one are switched by
one mechanism rather than two.

**The offers come from the checkout's own catalog.** `registry/index.json` names
every pack the repository publishes, by repo-relative path, so `offers` reads
that file and the manifests it names under the session's root. No network call
is made or needed: the index is a committed artifact and the manifests are
committed files, which is what lets the live panel answer "add module to room"
on a house whose owner has no internet. A pack present under `packs/` but absent
from the index is not offered -- the index is the published set, and a directory
listing is not.

**Every verdict is computed here and none is the panel's.** `satisfiable` and
`missing_slots` are the room's bindings against the pack's `requires_slots`, and
`conflicts` is `engine/install.py`'s `_conflict_failures` rule read in *both*
directions -- the arriving pack against each installed one, and each installed
one back -- because a conflict is symmetric and a check that read one direction
would make the panel's answer depend on which pack arrived first. The panel
renders these verdicts and recomputes none of them (`panel/README.md`), so the
one implementation of "can this room satisfy this pack" is this one.

Nothing here imports `homeassistant`: the session is the seam, so a pack install
is driven in a checkout with no Home Assistant installed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from engine import install as pack_install
from engine import manifest as pack_manifest
from engine import sandbox as pack_sandbox
from engine import semver
from engine import vocabulary as engine_vocabulary
from engine.adapter import EntityView, UnknownEntityError
from engine.behaviours import enable_key
from engine.behaviours.declared import behaviour_id
from engine.binding import HouseScope, RoomScope, resolve_slot
from engine.install import InstalledPack, InstalledSet
from engine.vocabulary import SlotDefinition
from openhouse import packs
from tools.registry.errors import RegistryError
from tools.registry.pointer import SELF_REPO
from tools.registry.store import Store as RegistryStore

from .composition import LiveRoom
from .live import LiveSession, LiveSessionError

__all__ = [
    "StorePackMissingError",
    "StorePackRefusedError",
    "install",
    "installed_modules",
    "offers",
    "preload",
    "set_enabled",
    "store_pack",
    "uninstall",
]

#: Where the checkout keeps its registry, relative to a root: the generated
#: index, the pointer files it was generated from, and the revocation list.
#: Named once because four answers read it and they have to agree -- the file the
#: catalog is read from, the path the panel's store tab is generated from, the
#: location a pack's own `provides` entries are resolved against, and the
#: directory `store_pack` opens. A panel offering a pack the installer cannot
#: resolve is a store that sells nothing.
REGISTRY = Path("registry")

#: The generated index inside `REGISTRY`: every published pack, by pointer.
INDEX = REGISTRY / "index.json"

#: The status a binding reads as when the entity the room binds is not one the
#: adapter holds at all. Distinct from `unavailable` (`house-adapter`: a device
#: that dropped out is not a device that never existed) and from `unbound`, which
#: is the slot having no entity rather than its entity having no answer.
MISSING = "missing"

#: How many distinct (file, stamp) pairs the readers below remember. Small on
#: purpose -- a checkout publishes one registry and a handful of manifests -- and
#: bounded only so a process that walked many roots cannot grow without limit.
_CACHE = 256


def _stamp(path: Path) -> tuple[int, int]:
    """A path's modification time and size, or zeros when it cannot be read.

    The stamp is what makes the caches below safe rather than merely fast: the
    key carries the file's identity as well as its name, so a pack republished
    while a process is running is re-read while one nobody touched is not. It is
    a metadata read and not the file read Home Assistant's loop detector reports,
    which is why this is a stamp and not a hash of the contents.
    """
    try:
        info = path.stat()
    except OSError:
        return (0, 0)
    return (info.st_mtime_ns, info.st_size)


def install(
    session: LiveSession, manifest_path: Path, *, root: Path | None = None
) -> Mapping[str, object]:
    """Validate a pack, resolve it against this house, record it, and report it.

    The four checks are `openhouse/facade.py`'s, in its order and for its
    reasons: the manifest schema first (is this a pack at all), the declared
    slots against the house second (is it a pack for *this* house), the sandbox
    third (is what it declares permitted anywhere), and the resolution against
    the installed set last, because it is the only step that needs the packs
    already in and so the only one that can refuse a pack nothing is wrong with.
    What differs is the ending: the facade rebuilds its engine by hand, while
    this hands the new set to `session.set_installed`, which is the one door a
    live session has onto its engine.

    `root` is the checkout the artifacts are read from -- the manifest schema,
    the published behaviour terms, the licence catalog -- and defaults to the
    session's own, because two installs in one session reading two roots would be
    two vocabularies disagreeing inside one house. It is a parameter anyway
    because a caller driving a pack that lives *outside* the checkout (a store
    download, a test) still validates against this checkout's artifacts.

    The result is the protocol's reply for `open_house/modules/install`: the
    panel's `InstalledModule` for the room the pack landed in, and that room's
    detail beside it. Nothing is enabled by arriving, so `enabled` is `False` and
    each behaviour's flag is `False` -- stated rather than left to be assumed.
    """
    where = session.root if root is None else Path(root)
    path = Path(manifest_path)
    loaded = _loaded(path)
    name = loaded.name
    document = loaded.document

    artifacts = engine_vocabulary.load_manifest_artifacts(where)
    published = engine_vocabulary.load_behaviour_vocabulary(where)

    verdict = pack_manifest.validate_manifest(loaded, artifacts, session.vocabulary)
    if not verdict.ok:
        # The reason is carried beside the message for the same purpose the
        # facade's is: a caller branching on *why* a manifest was refused
        # needs the token the schema refused under, not prose to parse.
        raise LiveSessionError(
            _refusal(
                name,
                [
                    f"{failure.reason}: {failure.message}"
                    for failure in verdict.failures
                ],
            )
        )

    try:
        packs.check_slots(document, session.engine.house)
    except packs.PackError as error:
        raise LiveSessionError(_refusal(name, [error.reason])) from error

    projected = _projected(path)
    sandboxed = pack_sandbox.check_pack(projected, where, session.vocabulary, published)
    if not sandboxed.ok:
        raise LiveSessionError(
            _refusal(name, [refusal.message for refusal in sandboxed.refusals])
        )

    arrival = _arrival(loaded)
    try:
        resulting = pack_install.install(
            session.engine.installed,
            arrival,
            slots=_bound(session, projected),
            behaviours=_behaviour_ids(document),
            flags=tuple(flag.message for flag in sandboxed.flags),
        )
    except pack_install.InstallRefusedError as refusal:
        raise LiveSessionError(
            _refusal(name, [failure.message for failure in refusal.failures])
        ) from refusal

    session.set_installed(resulting)
    record = resulting.packs[arrival.name]
    room_id = _module_room(session, record)
    return {
        "installed": _installed_module(session, record, room_id),
        "room": _room_detail(session, room_id),
    }


class StorePackMissingError(LiveSessionError):
    """No published row is called `pack` at `tier`.

    Separate from `StorePackRefusedError` because the two are different sentences to
    a person: this one is "the Store does not sell that", which no retry fixes,
    and the other is "the Store sells it and this copy is not it", which is a
    thing that has gone wrong. The panel shows them under different codes.
    """


class StorePackRefusedError(LiveSessionError):
    """A published row whose file is not the file the registry pinned."""


def store_pack(root: Path, pack: str, tier: str) -> Path:
    """The manifest a published row pins, verified, or a refusal saying why not.

    **The row is a pin, not a note.** `spec.txt` asks for "SHA-256 pinning for
    packs" and a revocation list, and this is the path where both mean something:
    a person opened the Store and clicked a listing, so the file that installs
    has to be the file that listing published, and a pack withdrawn since must
    not install at all.

    The check is `tools.registry.store`'s -- the same one a publisher runs and CI
    runs -- rather than a second digester written here, because two
    implementations of "is this the published pack" is how the two ends come to
    disagree, and the disagreement is silent in the direction that matters.

    `tier` is required rather than looked up. A registry may hold one name under
    two tiers, and the row is what a person clicked: resolving by name alone
    would install whichever the index happened to list first, which is a
    different pack from the one on the screen.

    Both roots are the *session's* checkout and not `tools.registry`'s module
    global, and the pointer's `SELF_REPO` source is resolved against that same
    checkout as an explicit mapping. They are the same directory in every
    deployment this project has, which is exactly why the mapping is written
    out: a `SELF_REPO` that silently resolved elsewhere would install a file from
    a second checkout while the panel showed a listing from this one.

    Blocking -- it reads the index, the revocation list and the pack file -- so a
    caller on an event loop runs it in an executor.
    """
    store = RegistryStore.open(root / REGISTRY)
    entry = next(
        (
            candidate
            for candidate in store.browse()
            if candidate.pointer.name == pack and candidate.pointer.tier == tier
        ),
        None,
    )
    if entry is None:
        raise StorePackMissingError(f"no {tier} pack called {pack!r} in the registry")
    try:
        resolved = store.resolve_verified(entry, {SELF_REPO: root})
    except RegistryError as refusal:
        raise StorePackRefusedError(str(refusal)) from refusal
    return resolved.manifest


def uninstall(session: LiveSession, name: str) -> Mapping[str, object]:
    """Remove a pack from the house, refusing to strand a dependent.

    Removal is not a disable, and the difference is exactly what this undoes: a
    disabled behaviour is a unit that is still registered, still recorded and
    still listed, and uninstalling takes all three away. It is the facade's
    `uninstall_pack` without the facade's second bookkeeping -- a live session's
    behaviours are the engine's, and the composition rebuilds them -- and it
    rides the same refusal, so a pack another installed pack depends on is not
    removed and the refusal names the dependent.

    The reply is the room page of the room the pack belonged to, which is what
    `open_house/modules/uninstall` answers with: the panel has just removed a
    module from a room and is re-rendering that room. A pack that belonged to no
    single room answers with the empty room page, which says so rather than
    naming a room at random.
    """
    held = session.engine.installed
    record = held.get(name)
    try:
        resulting = pack_install.uninstall(held, name)
    except pack_install.InstallRefusedError as refusal:
        raise LiveSessionError(
            _refusal(name, [failure.message for failure in refusal.failures])
        ) from refusal
    # The room is read *before* the set changes, because the record that answers
    # it is the record this call is about to remove.
    room_id = "" if record is None else _module_room(session, record)
    session.set_installed(resulting)
    return _room_detail(session, room_id)


def set_enabled(
    session: LiveSession, *, room_id: str, pack: str, enabled: bool
) -> Mapping[str, object]:
    """Turn a pack's behaviours on or off in one room, and rebuild nothing.

    A permission and not an actuation, exactly as a room's auto-lighting switch
    is: this writes the room layer's enable flag for each unit the pack
    registered and stops there, so the next tick finds nothing to do for a pack
    that was just switched off rather than turning off what it had lit. The key
    is `engine.behaviours.enable_key` over the pack-qualified behaviour id -- the
    same key the engine resolves a shipped unit's flag under -- at
    `RoomScope(room_id)`, so a pack's behaviour and a built-in one are gated by
    one mechanism.

    A flag is engine state and not wiring, so this rebuilds nothing: the engine
    the house is being decided for keeps its modes, its dwell timers and its
    override records. That is the same call `LiveSession.set_room_auto_lighting`
    makes for the built-in units, and the reason it is a call and not a rebuild
    is that a person flipping a switch is not a person re-configuring the house.

    Enabling writes an `True` override; disabling *clears* the override rather
    than writing `False`, so "off" is the absence of a decision rather than a
    second decision that happens to agree -- which is what lets a profile or a
    pack default enable it later without the switch's ghost standing in the way.
    """
    session.require_room(room_id)
    record = session.engine.installed.get(pack)
    if record is None:
        raise LiveSessionError(f"there is no module {pack!r} in this house")
    scope = RoomScope(room_id)
    for unit in record.behaviours:
        if enabled:
            session.engine.settings.set_override(enable_key(unit), scope, True)
        else:
            session.engine.settings.clear_override(enable_key(unit), scope)
    return _installed_module(session, record, room_id)


def installed_modules(
    session: LiveSession,
) -> tuple[Mapping[str, object], ...]:
    """Every pack the house holds, as the panel's `InstalledModule`, by name.

    One entry per pack, and the room is the pack's own -- the room its entities
    live in -- rather than a request's, because this is the Modules tab's answer
    to "what is in the house" and it has no room to be asked about. A pack that
    belongs to no single room is still listed, with the empty room id, because
    hiding it would be the one thing the tab exists to prevent.
    """
    published = _by_name(_published(session.root))
    installed = session.engine.installed
    return tuple(
        _installed_module(
            session,
            record,
            _module_room(session, record),
            published=published,
        )
        for name in installed.names
        if (record := installed.get(name)) is not None
    )


def offers(session: LiveSession, *, room_id: str) -> tuple[Mapping[str, object], ...]:
    """Every pack the house could hold, with its verdict against `room_id`.

    The catalog is `registry/index.json` under the session's root and the
    manifests it names, read here and not fetched: the answer has to be available
    to a house with no network, and it has to be the *published* set rather than
    whatever happens to lie in a directory.

    Each offer carries its whole verdict, because the panel renders one and
    computes none (`panel/README.md`): `satisfiable` and `missing_slots` are the
    room's bindings against the pack's `requires_slots`, `optional_slots_present`
    is the optional half of the same question, and `conflicts` is
    `engine/install.py`'s conflict rule read in both directions so that the
    answer cannot depend on which pack was installed first.

    An entry whose manifest will not load is skipped rather than offered: a
    catalog file edited into nonsense is a fact about the catalog, and an offers
    list is the wrong place to report it -- and the alternative, one bad entry
    failing the whole screen, would hide every other pack from a person who came
    to install one.
    """
    room = session.require_room(room_id)
    bound = frozenset(room.bindings)
    installed = session.engine.installed
    return tuple(
        _offer(session, entry, bound=bound, installed=installed)
        for entry in _published(session.root)
    )


# --------------------------------------------------------------------------
# The panel's two shapes. Both are built here rather than in the websocket
# layer, because a field the layer had to remember to fill is a field that can
# silently go missing -- and the panel reads every one of them.
# --------------------------------------------------------------------------


def _installed_module(
    session: LiveSession,
    record: InstalledPack,
    room_id: str,
    *,
    published: Mapping[str, _Published] | None = None,
) -> Mapping[str, object]:
    """One pack as the panel's `InstalledModule`, for the room it acts in.

    `enabled` is the module's single switch, and it is on when every behaviour
    the pack registered is on in this room: a pack is a unit a person turns on,
    not a bag of independent switches, and reporting "enabled" for a pack with
    half its behaviours running would be a green chip over a half-lit room. A
    pack with no behaviours at all -- a template -- is never "enabled", because
    there is nothing of it to run.
    """
    where = _by_name(_published(session.root)) if published is None else published
    strings = where[record.name].strings if record.name in where else {}
    return {
        "pack": record.name,
        "name": str(strings.get("pack", record.name)),
        "version": record.version,
        "room_id": room_id,
        "enabled": _enabled(session, record, room_id),
        "behaviours": tuple(
            {
                "id": unit,
                "label": str(strings.get(unit.rpartition(".")[2], unit)),
                "enabled": _flag(session, unit, room_id),
            }
            for unit in record.behaviours
        ),
    }


def _offer(
    session: LiveSession,
    entry: _Published,
    *,
    bound: frozenset[str],
    installed: InstalledSet,
) -> Mapping[str, object]:
    """One catalog pack as the panel's `ModuleOffer`, with its whole verdict."""
    document = entry.manifest.document
    required = _slot_names(document.get("requires_slots"))
    optional = _slot_names(document.get("optional_slots"))
    missing = tuple(slot for slot in required if slot not in bound)
    return {
        "pack": entry.name,
        "name": str(entry.strings.get("pack", entry.name)),
        "description": str(document.get("description", "")),
        "version": entry.version,
        "kind": str(document.get("kind", "")),
        "license": str(document.get("license", "")),
        "i18n": dict(entry.strings),
        "requires_slots": required,
        "optional_slots": optional,
        "satisfiable": not missing,
        "missing_slots": missing,
        "optional_slots_present": tuple(slot for slot in optional if slot in bound),
        "conflicts": _conflicts(session, entry, installed=installed),
        "already_installed": entry.name in installed,
        # No pack in the `pack-manifest` schema declares options: the clause does
        # not exist, so there is no declaration to build a schema from and the
        # honest answer is that this pack exposes none. An invented schema here
        # would be the panel rendering a form for a setting nothing reads.
        "options_schema": None,
        "behaviours": tuple(
            {
                "id": row[0],
                "label": str(entry.strings.get(row[0], row[0])),
                "priority": row[1],
            }
            for row in _behaviour_rows(document)
        ),
    }


def _room_detail(session: LiveSession, room_id: str) -> Mapping[str, object]:
    """The panel's `RoomDetail` for `room_id`, as far as the module half reads it.

    `open_house/modules/install` and `open_house/modules/uninstall` both answer
    with a room page, so these two operations owe one. It is built here rather
    than composed by the handler because a handler is text, not a place to keep a
    shape.

    Two halves are deliberately left empty and are not this module's to fill.
    `options_schema`/`options` are the *pack declarations'* reading (no pack in
    the current manifest schema declares an option at all) and `axes` is the
    profile set's; both belong to the operations that own them, and a second
    reader of the same declarations living here would be a second answer that
    could disagree with the first. The bindings half *is* this module's, because
    a module's room id is derived from those bindings and a page that did not
    show them would be a page about a room nobody can see.

    An empty `room_id` means the pack belonged to no single room (`_module_room`),
    and the answer is the empty page: naming a room the pack is not in would be
    worse than naming none.
    """
    room = session.room(room_id)
    selection = {} if room is None else dict(session.profiles.selection(room_id))
    return {
        "id": "" if room is None else room.id,
        "name": "" if room is None else room.name,
        "type": "" if room is None else room.type,
        "type_label": "" if room is None else room.type,
        "bindings": (
            ()
            if room is None
            else tuple(_binding(session, room, slot) for slot in sorted(room.bindings))
        ),
        "options_schema": None,
        "options": {},
        "modules": tuple(
            module
            for module in installed_modules(session)
            if module["room_id"] == room_id and room_id != ""
        ),
        "active_profiles": selection,
        "mode": "" if room is None else room.mode,
        "axes": (),
    }


def _binding(session: LiveSession, room: LiveRoom, slot: str) -> Mapping[str, object]:
    """One slot of a room, with the live status of the entity it is bound to."""
    entity_id = room.bindings[slot]
    definition = session.vocabulary.slots.get(slot)
    view = _view(session, entity_id)
    return {
        "slot": slot,
        # The slot's human name is the catalog's to resolve and the catalog is
        # not this module's to read; the id is the one name this half knows, and
        # a label that is the id is honest where an invented prettier one is not.
        "label": slot,
        "required": _required(definition),
        # `SlotDefinition` deliberately does not carry `accepts_domains` (only
        # the binding layer's rules read domains), so this half states none.
        "accepts_domains": (),
        "entity_id": entity_id,
        "registry_id": None,
        "friendly_name": None if view is None else _friendly_name(view),
        "domain": entity_id.partition(".")[0] or None,
        "state": None if view is None else view.state,
        "status": _status(view),
        "last_changed": None,
    }


# --------------------------------------------------------------------------
# The verdicts. Each is a rule the engine already states, read once here so
# that the panel and the engine cannot disagree about what is installable.
# --------------------------------------------------------------------------


def _conflicts(
    session: LiveSession, entry: _Published, *, installed: InstalledSet
) -> tuple[Mapping[str, object], ...]:
    """Every way `entry` cannot join the house, from both sides of the question.

    The two declared directions are `engine/install.py`'s `_conflict_failures`,
    re-read rather than re-decided: an arriving pack that declares a conflict
    with an installed one *and* an installed pack that declares a conflict with
    the arriving one, because a conflict is symmetric and a check that read one
    direction would let a person install the pair in one order and not the other.
    Both are `blocking`, because that is what the engine does with them -- it
    refuses the install.

    The third is the engine-api range, which the manifest validator refuses
    (`engine_api_mismatch`) and which is a conflict *with the engine* rather than
    with a pack: `installed_version` is nothing and `pack` names no pack, because
    there is none on the other side. It is reported here as a blocking conflict
    rather than left to the install failing later, so the panel can grey the row
    instead of offering a button that always errors.
    """
    document = entry.manifest.document
    version = entry.version
    found: list[Mapping[str, object]] = []
    for reference in _references(document, "conflicts"):
        present = installed.version_of(reference.name)
        if present is not None and reference.admits(present):
            found.append(
                {
                    "kind": "declared",
                    "pack": reference.name,
                    "installed_version": present,
                    "detail": reference.range,
                    "severity": "blocking",
                }
            )
    for other in installed.names:
        if other == entry.name:
            continue
        held = installed.get(other)
        if held is None:  # pragma: no cover - `names` is the same mapping's keys
            continue
        for reference in held.conflicts:
            if reference.name == entry.name and reference.admits(version):
                found.append(
                    {
                        "kind": "declared",
                        "pack": other,
                        "installed_version": held.version,
                        "detail": reference.range,
                        "severity": "blocking",
                    }
                )
    api = document.get("engine_api")
    if isinstance(api, str) and not semver.satisfies(
        session.vocabulary.engine_api_version, api
    ):
        found.append(
            {
                "kind": "engine_api",
                "pack": "",
                "installed_version": None,
                "detail": api,
                "severity": "blocking",
            }
        )
    return tuple(sorted(found, key=lambda row: (str(row["kind"]), str(row["pack"]))))


def _module_room(session: LiveSession, record: InstalledPack) -> str:
    """The room a pack acts in: the one holding every entity its slots reached.

    The join between the engine's house-wide installed set and the panel's
    room-scoped module, and it is made from the entities rather than from the
    slot names: a slot bound in one room and a slot bound in another are one
    pack the house supplies and no room that holds it, and answering "the first
    room that binds something" would tell a person their module is in a room it
    cannot act in. Every entity, therefore, and the empty id when no single room
    holds them -- which `install` and `installed_modules` both report as an
    unplaced pack rather than inventing a room for it.
    """
    reached = tuple(
        (slot, entity) for slot, entities in record.slots for entity in entities
    )
    if not reached:
        return ""
    for room in session.rooms:
        if all(room.bindings.get(slot) == entity for slot, entity in reached):
            return room.id
    return ""


def _enabled(session: LiveSession, record: InstalledPack, room_id: str) -> bool:
    """Whether every behaviour the pack registered is on in `room_id`."""
    if not record.behaviours or not room_id:
        return False
    return all(_flag(session, unit, room_id) for unit in record.behaviours)


def _flag(session: LiveSession, unit: str, room_id: str) -> bool:
    """One unit's enable flag in one room, resolved through the engine.

    Resolved rather than remembered: the flag a module reports has to be the flag
    the engine would decide by, and a field this module kept would be a second
    answer free to drift from the resolver's. `False` is the fallback for a unit
    the engine does not register -- the built-in layer is the *unit's* to state
    (`engine/config.py`), and a pack's unit that this build never registered has
    no default to read.
    """
    if not room_id:
        return False
    return bool(
        session.engine.settings.resolve_or(
            enable_key(unit), RoomScope(room_id), False
        ).value
    )


# --------------------------------------------------------------------------
# The catalog. Read from the checkout, never fetched.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Published:
    """One pack the checkout publishes: its manifest and its resolved strings.

    A value rather than three lookups, because `offers` and `installed_modules`
    both want the same three facts -- the name, the version the index publishes,
    and the i18n block resolved to the default locale -- and reading them twice
    would be two answers about one file.
    """

    name: str
    version: str
    tier: str
    manifest: pack_manifest.Manifest
    strings: Mapping[str, str]


def preload(root: Path) -> None:
    """Read `root`'s catalog once, so that no later reader has to.

    Every reader below is cached on `(path, stamp)` (`_stamp`), so the *second*
    caller reads nothing -- but the first one still reads, and the first caller
    is a websocket command on Home Assistant's event loop, which is precisely the
    blocking call those caches were added to remove. A cache cannot be warmed
    honestly by anything other than the reader that fills it, so this calls the
    one function a request reaches and drops the answer: `_published` is that
    function because it is the only place the index and the manifests it names
    are both read, and every other reader here is a projection of its result.

    Blocking, by definition. The caller is `host._load`, which is already an
    executor job for the same reason -- the catalog is read there too, and the
    two are the same kind of work.
    """
    _published(root)


def _published(root: Path) -> tuple[_Published, ...]:
    """Every pack `registry/index.json` publishes under `root`, in name order.

    Name order rather than the index's own, because the panel shows this list
    and two answers to one question should read the same twice; the index's
    order is the order a *publisher* added entries, which is not a property the
    person browsing a catalog asked for.

    An entry that is not a mapping, names no path, or names a manifest that will
    not load is skipped: the catalog is committed data and this is not the place
    to report a malformed entry, but a listing that failed whole would take every
    other pack down with it.
    """
    document = _index(root)
    found: list[_Published] = []
    for entry in document:
        relative = entry.get("path")
        if not isinstance(relative, str):
            continue
        manifest = _manifest_at(root / relative)
        if manifest is None:
            continue
        name = manifest.name
        found.append(
            _Published(
                name=name,
                version=str(
                    entry.get("version") or manifest.document.get("version", "")
                ),
                tier=str(entry.get("tier", "")),
                manifest=manifest,
                strings=pack_manifest.strings(manifest),
            )
        )
    return tuple(sorted(found, key=lambda entry: entry.name))


def _index(root: Path) -> tuple[Mapping[str, object], ...]:
    """The `entries` of the registry index, or nothing when there is no index.

    A checkout without an index offers nothing rather than failing: the panel is
    usable on a house whose integration was installed without the store's data,
    and "there is nothing to add" is the true answer there.
    """
    return _entries_at(root / INDEX, _stamp(root / INDEX))


@lru_cache(maxsize=_CACHE)
def _entries_at(path: Path, stamp: tuple[int, int]) -> tuple[Mapping[str, object], ...]:
    """A registry index read at `stamp`, already known to be at that stamp.

    **Cached because of who calls it.** `installed_modules` and `offers` are the
    panel's `rooms/get`, `modules/list` and `store/index`, and those run on Home
    Assistant's event loop -- where this read was reported as a blocking call,
    once per screen. The key is the stamp rather than the path alone (`_stamp`),
    so a republished registry is still re-read; the pair is what makes "read once
    per process" safe to say instead of merely convenient.
    """
    if stamp == (0, 0):
        return ()
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    if not isinstance(loaded, Mapping):
        return ()
    entries = loaded.get("entries")
    if not isinstance(entries, list):
        return ()
    return tuple(entry for entry in entries if isinstance(entry, Mapping))


def _manifest_at(path: Path) -> pack_manifest.Manifest | None:
    """A pack manifest read at its current stamp, or `None` when it will not load.

    `None` rather than a raise, matching `_published`'s own tolerance: a catalog
    is committed data and a single malformed entry must not take every other pack
    down with it. The failure is not swallowed where it matters -- `_loaded`
    still raises for a manifest somebody explicitly asked to install.
    """
    return _load_at(path, _stamp(path))


@lru_cache(maxsize=_CACHE)
def _load_at(path: Path, stamp: tuple[int, int]) -> pack_manifest.Manifest | None:
    """`path` read as a manifest at `stamp`. Cached for the reason `_entries_at` gives."""
    if stamp == (0, 0):
        return None
    try:
        return pack_manifest.load_manifest(path)
    except (OSError, pack_manifest.MalformedManifestError):
        return None


def _by_name(entries: Sequence[_Published]) -> dict[str, _Published]:
    """The catalog keyed by pack name, which is what an installed record names."""
    return {entry.name: entry for entry in entries}


# --------------------------------------------------------------------------
# Projections of a manifest, mirroring `openhouse/facade.py`. Each one unwraps
# a clause the validator has already judged, so a row that is not a row is
# dropped here exactly as the validator dropped it, and the two agree.
# --------------------------------------------------------------------------


def _loaded(path: Path) -> pack_manifest.Manifest:
    """`path` read as a manifest, or a failure naming the file."""
    try:
        return pack_manifest.load_manifest(path)
    except (OSError, pack_manifest.MalformedManifestError) as error:
        raise LiveSessionError(
            f"the pack at {path.as_posix()} will not load: {error}"
        ) from error


def _projected(path: Path) -> pack_sandbox.Pack:
    """`path` read as the sandbox reads it, or a failure naming the file."""
    try:
        return pack_sandbox.load_pack(path)
    except (OSError, pack_sandbox.MalformedPackError) as error:
        raise LiveSessionError(
            f"the pack at {path.as_posix()} will not load: {error}"
        ) from error


def _arrival(loaded: pack_manifest.Manifest) -> pack_install.Arrival:
    """What the validated manifest declares, as the installed set reads it.

    The unwrapping lives here rather than in `engine/install.py` for the reason
    the facade gives: that module decides about a set and must never read a
    manifest, so the clauses it needs arrive already unwrapped from whoever had
    the document.
    """
    document = loaded.document
    return pack_install.Arrival(
        name=loaded.name,
        version=str(document.get("version", "")),
        digest=pack_install.digest(document),
        dependencies=_references(document, "dependencies"),
        conflicts=_references(document, "conflicts"),
    )


def _references(
    document: Mapping[str, object], field: str
) -> tuple[pack_install.Reference, ...]:
    """One manifest reference list -- `dependencies` or `conflicts` -- unwrapped.

    Absent, or not a list, is empty; a row that is not a mapping is not a row.
    The same reading `engine/manifest.py` makes of its own rows, because a row
    this dropped and the validator kept would be a constraint nobody checks.
    """
    rows = document.get(field)
    if not isinstance(rows, list):
        return ()
    references: list[pack_install.Reference] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        span = row.get("range")
        if isinstance(name, str) and isinstance(span, str):
            references.append(pack_install.Reference(name=name, range=span))
    return tuple(references)


def _behaviour_rows(
    document: Mapping[str, object],
) -> tuple[tuple[str, int | None], ...]:
    """The manifest's behaviours, each with its name and declared priority.

    The priority is the manifest's or nothing, and nothing is not zero: a pack
    that states no rank is ranked by the published default at registration
    (`catalog/pack-policy.yaml`), and an offer that reported `0` would be stating
    a rank the file does not contain.
    """
    rows = document.get("behaviours")
    if not isinstance(rows, list):
        return ()
    seen: list[tuple[str, int | None]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        if isinstance(name, str):
            seen.append((name, _priority(row.get("priority"))))
    return tuple(seen)


def _behaviour_ids(document: Mapping[str, object]) -> tuple[str, ...]:
    """The pack-qualified unit ids the manifest's behaviours register under.

    Qualified, because that is the identity the engine and the enable key use
    (`engine/behaviours/declared.py`): two packs may each declare a behaviour
    called `motion`, and an unqualified id would give them one switch between
    them.
    """
    name = str(document.get("name", ""))
    return tuple(
        behaviour_id(name, behaviour) for behaviour, _ in _behaviour_rows(document)
    )


def _bound(
    session: LiveSession, projected: pack_sandbox.Pack
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The entities this house supplies for each slot the pack declares.

    Every room's binding and not one room's, because a pack lands in the house:
    which room a behaviour's evaluation is *for* is the engine's to decide each
    tick, and the record's job is to answer "what did this pack get hold of".
    A slot the house binds nowhere is still recorded with an empty tuple, so
    "the house supplied nothing here" is a fact in the record rather than an
    absence from it.
    """
    return tuple(
        (slot, _reached(session, slot)) for slot in sorted(projected.declared_slots)
    )


def _reached(session: LiveSession, slot: str) -> tuple[str, ...]:
    """The entities the house binds `slot` to, whatever scope it is a slot of."""
    house = session.engine.house
    if slot in house.house_scope_slots:
        return resolve_slot(house, HouseScope(), slot).entities
    return tuple(
        entity_id
        for room in house.rooms
        if (entity_id := room.bindings.get(slot)) is not None
    )


def _slot_names(value: object) -> tuple[str, ...]:
    """A clause that is a list of names, or nothing when it is not one."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _priority(value: object) -> int | None:
    """The declared priority, or nothing when none was stated.

    `isinstance(value, int)` alone would admit `True`, which is an `int` and is
    not a rank, and the schema says `integer`.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _required(definition: SlotDefinition | None) -> bool:
    """Whether the vocabulary makes this slot required, a slot it does not hold being not."""
    return definition is not None and definition.required


def _view(session: LiveSession, entity_id: str) -> EntityView | None:
    """The entity's live view, or nothing when the house does not hold it."""
    adapter = session.adapter
    if adapter is None:  # pragma: no cover - `build` always fills it
        return None
    try:
        return adapter.read_entity(entity_id)
    except UnknownEntityError:
        return None


def _friendly_name(view: EntityView) -> str | None:
    """The entity's friendly name, when it reports one."""
    name = view.attributes.get("friendly_name")
    return None if name is None else str(name)


def _status(view: EntityView | None) -> str:
    """The binding's status, in the panel's closed set of kinds.

    Unavailable is not off and is not missing: an entity Home Assistant reports
    as unavailable is a device that dropped out, an entity the house does not
    hold is a device that was never there, and `unknown` is a device that is
    there and answering nothing.
    """
    if view is None:
        return MISSING
    if not view.available:
        return "unavailable"
    if view.state == "unknown":
        return "unknown"
    return "ok"


def _refusal(name: str, reasons: Sequence[str]) -> str:
    """A refusal's message, in the shape every install failure already uses.

    One wording for four layers -- the schema, the slots, the sandbox and the
    resolver -- because a person fixing a pack should read the same sentence
    whatever refused it and only the reason should change.
    """
    return f"the pack {name!r} will not install: {'; '.join(reasons)}"
