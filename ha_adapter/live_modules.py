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

**Unsatisfiable is installable; unwired is not enableable.** A pack whose
required slots are bound to nothing installs, and arrives disabled with its
`missing_slots` named -- the same verdict an offer carries, now on the installed
record too -- and `set_enabled` refuses to switch it on until those slots are
bound (`_refuse_unless_wired`). The room's configurable devices are the modules'
own slots (`custom_components/open_house/views.py`), so a person has to be able
to put the module in *before* the slot it wants exists to be filled; refusing
the install would have made the wiring unreachable. Permission to install is not
permission to run, which is why the two verdicts are separate statements rather
than one.

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
from engine.behaviours import enable_key, module_enable_key, scope_key
from engine.behaviours.base import BehaviourScope
from engine.behaviours.declared import behaviour_id, option_rows
from engine.binding import HouseScope, RoomScope, resolve_slot
from engine.declared_slots import optional_keys, required_keys
from engine.install import InstalledPack, InstalledSet
from engine.vocabulary import SlotDefinition
from openhouse import packs
from tools.registry.errors import RegistryError
from tools.registry.pointer import SELF_REPO
from tools.registry.store import Store as RegistryStore

from .composition import LiveRoom
from .live import HOUSE, LiveSession, LiveSessionError
from .live_profiles import (
    OPTIONS_TITLE,
    humanize,
    option_properties,
    pack_option_keys,
    reaches_room,
)
from .live_profiles import options as room_options

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
    session: LiveSession,
    manifest_path: Path,
    *,
    room_id: str | None = None,
    root: Path | None = None,
) -> Mapping[str, object]:
    """Validate a pack, resolve it against this house, record it, and report it.

    The four checks are `openhouse/facade.py`'s, in its order and for its
    reasons: the manifest schema first (is this a pack at all), the declared
    slots against the house second (does it name a slot no vocabulary declares),
    the sandbox third (is what it declares permitted anywhere), and the
    resolution against the installed set last, because it is the only step that
    needs the packs already in and so the only one that can refuse a pack
    nothing is wrong with. What differs is the ending: the facade rebuilds its
    engine by hand, while this hands the new set to `session.set_installed`,
    which is the one door a live session has onto its engine.

    A required slot this house binds nowhere does not refuse the install. The
    module arrives, its record carries that slot against an empty tuple, and
    `set_enabled` refuses to switch it on until the room binds it -- so the
    arrival is reported with `enabled: False` *and* `satisfiable: False`, and the
    two are different statements: nothing is running, and nothing could be.

    `root` is the checkout the artifacts are read from -- the manifest schema,
    the published behaviour terms, the licence catalog -- and defaults to the
    session's own, because two installs in one session reading two roots would be
    two vocabularies disagreeing inside one house. It is a parameter anyway
    because a caller driving a pack that lives *outside* the checkout (a store
    download, a test) still validates against this checkout's artifacts.

    **`room_id` is the room the module is being put in, and it decides which
    entities the pack's slots resolve to.** `open_house/modules/install` asks a
    person which room, and the answer has to be the one recorded: a pack's slot
    resolved house-wide reads as the entities *every* room binds it to, so in a
    house with nine motion sensors the record holds nine, no single room binds
    all nine, and `_module_room` answers "no room" for a module a person just
    placed -- which reads as a module that can never be enabled, because
    `_enabled` returns `False` for the empty room. Resolving against the named
    room makes the record say what the person said.

    `room_id` may name the whole house instead of a room, by being `HOUSE` (the
    empty string). The house is a target in its own right -- its own slots, its
    own modules, its own settings -- so a person may put a module *in the house*
    rather than in a room, and that is recorded as a placement of `HOUSE`: the
    pack's flags and options then resolve at house scope rather than a room's.
    The pack's slots still resolve against the house's bindings, which is what
    `_bound` does with no room, so a house-placed module reaches every room's
    device for a room-scoped role and the house's device for a house-scoped one.

    It is `None` for the one caller that has no room to name: the Store installs
    by pack name from a tab with no room open, so it passes nothing and the pack
    is placed by the entity join -- the room that holds every entity its slots
    reached, or the empty room when no single one does. That is the honest answer
    for a house that has not said where the pack goes, and it is the same answer
    the room dialog would get if the two were ever to agree.

    The result is the protocol's reply for `open_house/modules/install`: the
    panel's `InstalledModule` for the room (or house) the pack landed in, and
    that room's detail beside it. Nothing is enabled by arriving, so `enabled` is
    `False` and each behaviour's flag is `False` -- stated rather than left to be
    assumed.
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
    room = (
        None if room_id is None or room_id == HOUSE else session.require_room(room_id)
    )
    try:
        resulting = pack_install.install(
            session.engine.installed,
            arrival,
            slots=_bound(session, projected, room=room),
            behaviours=_behaviour_ids(document),
            flags=tuple(flag.message for flag in sandboxed.flags),
        )
    except pack_install.InstallRefusedError as refusal:
        raise LiveSessionError(
            _refusal(name, [failure.message for failure in refusal.failures])
        ) from refusal

    session.set_installed(resulting)
    record = resulting.packs[arrival.name]
    # Recorded before the room is read, because the answer being read is this
    # one. `_module_room` prefers the room a person named, and the join it falls
    # back to cannot place a pack whose slots are house-scope (`bedtime` reaches
    # through `light_group` and `lock`, which belong to the whole house and to
    # no room) -- the record said `room_id: ""`, `_enabled` short-circuits on the
    # empty id, and every Enable button on the module did nothing.
    # Any placement the caller named is recorded -- a room *or* the house. The
    # house is `HOUSE`, and recording it is what keeps a house-placed module's
    # flags and options at house scope across a restart instead of falling back
    # to the entity join, which no room answers for a module that reaches the
    # whole house (`bedtime` through `light_group` and `lock`).
    if room_id is not None:
        session.place_module(arrival.name, HOUSE if room is None else room.id)
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
    # The placement goes with the pack. Left behind it would be read by the next
    # install of that name -- `_module_room` answers it before the join -- and
    # put a module the person placed in *this* room into the room the old one
    # was in.
    session.forget_module(name)
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

    Enabling is refused while the pack's required slots are bound to nothing in
    this room (`_unbound_required`), because turning a module on is asking the
    house to act through devices it does not have: the pack's units would be
    evaluated against empty slots and the switch would report "on" over a module
    that cannot do anything. Installing is not refused for the same reason -- the
    module arrives, its slots are the room's configurable devices, and a person
    fills them -- so this is where the ordering is enforced, at the last moment
    before the flag would matter. Disabling is never refused, so a module whose
    devices were unbound *after* it was enabled can always be switched off.
    """
    _require_placement(session, room_id)
    record = session.engine.installed.get(pack)
    if record is None:
        raise LiveSessionError(f"there is no module {pack!r} in this house")
    if enabled:
        _refuse_unless_wired(session, pack, room_id)
    scope = _scope_for(room_id)
    for unit in record.behaviours:
        if enabled:
            session.engine.settings.set_override(enable_key(unit), scope, True)
        else:
            session.engine.settings.clear_override(enable_key(unit), scope)
    _sync_module_flag(session, record)
    return _installed_module(session, record, room_id)


def _require_placement(session: LiveSession, room_id: str) -> None:
    """Refuse a placement that names neither a room the house holds nor itself.

    `HOUSE` is a placement `require_room` would refuse -- it is not a room id --
    so every command that takes one goes through here rather than calling
    `require_room` and special-casing the house at each call site. Any other
    string is a room id and is checked by the session, which is where the
    refusal names it.
    """
    if room_id != HOUSE:
        session.require_room(room_id)


def _scope_for(room_id: str) -> HouseScope | RoomScope:
    """The scope a module's flags and options live at, for its placement.

    A module put in a room is switched at that room's scope, and a module put in
    the house -- `room_id` is `HOUSE` -- is switched at the house's, which is the
    one scope the engine reads for a house-scoped behaviour (`BehaviourContext.
    option` resolves at the scope the behaviour is evaluated at). `room_id` is
    never `None` here: a caller that has no placement at all is asking about a
    module the house could not place, and every caller of this has one.
    """
    return HouseScope() if room_id == HOUSE else RoomScope(room_id)


def set_behaviour_enabled(
    session: LiveSession,
    *,
    room_id: str,
    pack: str,
    behaviour: str,
    enabled: bool,
) -> Mapping[str, object]:
    """Turn one behaviour of a pack on or off, and leave its siblings alone.

    The atom's own switch. A pack is a *bag* of atoms -- "bedtime" is lights off,
    plus the thermostat, plus the locks -- and the whole of the customisation
    spec.txt's pack system promises is that a person may keep the part they want
    and drop the part they do not. Until this existed the only switch was the
    pack's own, so keeping bedtime's lights and dropping its locks meant not
    installing bedtime.

    The same flag the pack-level switch writes, at the same scope, on one unit
    instead of every one: `set_enabled` is this looped over `record.behaviours`,
    and the two cannot disagree because they are one mechanism with one key
    (`engine.behaviours.enable_key`) and one store. Disabling clears the override
    rather than writing `False`, for the reason `set_enabled` gives -- "off" is
    the absence of a decision, and a written `False` is a second decision that
    would stand in the way of a profile that wants the atom on.

    `behaviour` is the pack-qualified unit id the panel already carries in each
    `InstalledModule` behaviour row, and the bare declared name is accepted too
    so a caller that read the manifest rather than the panel is not silently
    refused.

    The wiring gate `set_enabled` applies is applied here too, and for the same
    reason: one atom of a pack that cannot act is no more able to act than the
    whole pack, and letting the per-atom switch through while refusing the
    pack-level one would make the two switches disagree about the same fact.
    """
    _require_placement(session, room_id)
    record = session.engine.installed.get(pack)
    if record is None:
        raise LiveSessionError(f"there is no module {pack!r} in this house")
    if enabled:
        _refuse_unless_wired(session, pack, room_id)
    unit = _unit_of(record, behaviour)
    scope = _scope_for(room_id)
    if enabled:
        session.engine.settings.set_override(enable_key(unit), scope, True)
    else:
        session.engine.settings.clear_override(enable_key(unit), scope)
    _sync_module_flag(session, record)
    return _installed_module(session, record, room_id)


def set_behaviour_scope(
    session: LiveSession,
    *,
    room_id: str,
    pack: str,
    behaviour: str,
    scope: str,
) -> Mapping[str, object]:
    """Say which rooms one atom of a pack runs for: the room it sits in, or all.

    The "reach" control, and the other half of what a module's settings are for.
    A bedtime pack is a button in a bedroom that dims that bedroom and another
    that shuts the whole house down, and which of the two a person wants is not
    something the manifest can know -- so a house-scoped atom can be narrowed to
    the room its module was installed into, and a room-scoped one widened to
    every room, without editing the pack or reinstalling it.

    The choice is written twice, and the two writes are for two different times.
    As a *house setting* (`engine.behaviours.base.scope_key`) it survives a
    restart, because the house settings are what the live path stores and the
    engine's own state is not (`ha_adapter.live.LiveSession.to_state`); as an
    *override* on the running engine it takes effect now, because a setting that
    only arrived at the next rebuild would need one, and a rebuild is what
    discards the in-memory enable flags (`rebuild` passes no `state`). Writing
    only the setting would make the switch look inert; writing only the override
    would forget it on restart. Both, or neither works.

    Setting the atom back to what its pack declared *removes* the setting rather
    than writing the declared word, for the reason `set_enabled` clears rather
    than writes `False`: a house that has not been asked has no opinion, and a
    written opinion that agrees with the default would stand in the way of a
    future default the pack changes.

    Widening is refused when the house scope cannot resolve every slot the atom
    names (`Engine.reaches_house`), which is a fact about the pack rather than
    about this command: an atom reading a device only a room type provides has no
    house-wide reading, and honouring the request would put the atom on every
    tick as a `resolve_slot` refusal. A refusal naming the reason is what the
    panel shows; the engine ignores such a setting if one arrives by file, which
    is the belt to this braces.
    """
    _require_placement(session, room_id)
    record = session.engine.installed.get(pack)
    if record is None:
        raise LiveSessionError(f"there is no module {pack!r} in this house")
    unit = _unit_of(record, behaviour)
    try:
        wanted = BehaviourScope(scope)
    except ValueError:
        raise LiveSessionError(
            f"{scope!r} is not a scope; a behaviour runs in 'room' or 'house'"
        ) from None
    if wanted is BehaviourScope.HOUSE and not session.engine.reaches_house(unit):
        raise LiveSessionError(
            f"{pack!r} cannot run for the whole house: the house scope does not "
            "carry every slot its behaviours reach through"
        )
    key = scope_key(unit)
    if wanted is _declared_scope(session, unit):
        session.house_settings = {
            name: value for name, value in session.house_settings.items() if name != key
        }
        session.engine.settings.clear_override(key, HouseScope())
    else:
        session.house_settings = {**session.house_settings, key: str(wanted)}
        session.engine.settings.set_override(key, HouseScope(), str(wanted))
    return _installed_module(session, record, room_id)


def _sync_module_flag(session: LiveSession, record: InstalledPack) -> None:
    """Open or close a pack's house-scope master flag to match its rooms.

    The engine's module gate resolves `module.<pack>.enabled` at *house* scope
    (`engine/engine.py`'s `_gate`) while a behaviour's own flag resolves at the
    room's scope, and this module writes only the room's. Until this helper
    existed nothing in the live path ever wrote the house-scope key, so the gate
    fell back to its `False` on every tick and a pack the panel reported as "3 of
    3 behaviours now on" was `skipped: disabled` inside the engine -- the panel's
    `enabled` is derived from the per-room unit flags (`_enabled`), so the two
    disagreed in silence and a person's switch changed a flag no unit read.

    The master is *derived* here rather than written beside each toggle, because
    a second writer would leave it open after the last room was switched off and
    the gate would then be a flag the panel can neither show nor clear. Every
    write that changes a per-room unit flag calls this, so the two layers cannot
    drift.
    """
    still_on = any(
        _flag(session, unit, room.id)
        for room in session.rooms
        for unit in record.behaviours
    )
    if still_on:
        session.engine.settings.set_override(
            module_enable_key(record.name), HouseScope(), True
        )
    else:
        session.engine.settings.clear_override(
            module_enable_key(record.name), HouseScope()
        )


def _unit_of(record: InstalledPack, behaviour: str) -> str:
    """The pack-qualified unit id `behaviour` names, or a refusal naming it.

    A refusal rather than a silent no-op, because the caller is a command and a
    command that names an atom the pack does not hold has been given a wrong
    name. Writing a flag for a unit that does not exist would leave the panel
    showing an unchanged row and no reason for it.
    """
    for unit in record.behaviours:
        if unit in (behaviour, behaviour_id(record.name, behaviour)):
            return unit
    raise LiveSessionError(
        f"the module {record.name!r} declares no behaviour called {behaviour!r}"
    )


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


def house_scope(session: LiveSession) -> Mapping[str, object]:
    """The house's own slots, and the whole-house modules.

    The House tab's whole answer, and the mirror of a room's page: a room shows
    the slots *it* provides and binds them one by one, and this shows the slots
    the whole house is asked about, bound the same way. A global slot is one
    entity a person picks -- "the house's lights", one group rather than a pile
    of rooms' groups -- and it is what the house scope resolves the role to and
    what every room that bound none of its own falls back to
    (`engine.binding.resolve_slot`), so the row here is the slot, not a summary
    of the rooms that happen to fill it.

    **A slot is drawn only when a module reaches it.** The catalog's house list
    is a menu of roles the vocabulary can name, most of which nothing in a given
    house uses; a row per name would be a page of controls for automations that
    do not exist. `_house_reach` is the join that says which installed module
    acts through which role, and a role no module reaches is a control with
    nothing behind it, so it is left out -- the page is about the automations a
    person has, not about the vocabulary. The one module-less row that survives
    is none: a slot with no module is exactly the row that cannot do anything.

    `modules` is what acts on the house, and it is deliberately wider than "the
    packs at house scope". A bedtime button sitting in a bedroom is a room-scoped
    pack, and it is still the module whose whole point is the house's doors and
    lights -- a screen headed "whole-house modules" that omitted it would omit
    the one module the person was looking for. So a pack is listed here when it
    reaches any house-eligible role at all (`_house_reach`), which is the pack's
    own answer about what it is for; the *scope* setting beside its name is then
    the narrower, per-behaviour fact of whether the engine decides it once for
    the house or once per room.

    Each slot row carries the same join from the other side -- the modules that
    reach *it* -- because "the House has doors" and "the bedtime button shuts
    them" are two facts a person needs together, and a screen that showed the
    role without the module would leave them to guess which pack put it there.
    """
    reach = _house_reach(session)
    modules = tuple(installed_modules(session))
    schema, values = options_for(session, HOUSE)
    # The pack's *display* name, from the same rows the tab's module list draws
    # (`i18n.default.pack`, falling back to the pack id). A chip reading
    # `cleaning_vacuum_room` would name the pack rather than the module, and the
    # whole point of the chip is to be the name the person installed.
    display = {str(module["pack"]): str(module["name"]) for module in modules}
    by_slot: dict[str, set[str]] = {}
    for name, reached in reach.items():
        for slot in reached:
            by_slot.setdefault(slot, set()).add(display.get(name, name))
    return {
        "name": session.house_name,
        "slots": tuple(
            _house_slot(session, slot, by_slot[slot]) for slot in sorted(by_slot)
        ),
        # The house's own modules: the packs that act on a role the whole house
        # reads, *and* the packs put into the house itself. The first is what the
        # screen was built for -- a bedtime button in a bedroom is the module the
        # house's doors belong to -- and the second is the house being a target in
        # its own right, where a person installed a module into the house rather
        # than a room. A house-placed pack that reaches no house role is still the
        # house's module, and hiding it would make "put it in the house" a choice
        # with no visible consequence.
        "modules": tuple(
            module for module in modules if module["pack"] in reach or module["house"]
        ),
        # The house's own options, drawn exactly as a room's are: the packs put
        # in the house declare settings that resolve at house scope, and this is
        # the form a person sets them on. `None` when nothing in the house
        # declares an option, the same "nothing to configure here" a room answers.
        "options_schema": schema,
        "options": values,
    }


def _house_reach(session: LiveSession) -> dict[str, frozenset[str]]:
    """Which installed module reaches which house-eligible roles, by pack name.

    The join the whole screen is built on, computed once so the two directions --
    "what fills this role" and "which module brought it" -- cannot disagree about
    a pack. Only the catalog's own house roles are counted (`Vocabulary.
    house_slots`): a pack's declared `fridge_contact` is a name for one room's
    appliance, and collecting it across the house would invent a house-wide role
    out of a single fridge, so it is not a role this screen offers.

    A pack that reaches no house role at all is left out of the mapping, which is
    what keeps the room-local packs off a screen that is not about them; the
    slots the *house document* declares stay in regardless, because a row nothing
    acts on yet is exactly the row a person opens this tab to find.
    """
    eligible = session.vocabulary.house_slots
    installed = session.engine.installed
    reach: dict[str, frozenset[str]] = {}
    for name in installed.names:
        record = installed.get(name)
        if record is None:
            continue
        slots = frozenset(slot for slot, _entities in record.slots if slot in eligible)
        if slots:
            reach[name] = slots
    return reach


def _house_slot(
    session: LiveSession, slot: str, modules: set[str]
) -> Mapping[str, object]:
    """One global slot: the role, the entity the house binds to it, its modules.

    The shape a room's binding row has (`views.room_detail`), so a house slot
    and a room slot are the same kind of thing drawn by the same controls: the
    slot's name, its entity, the entity's live status, and what may be bound. The
    entity is the *house's own* binding (`session.house_bindings`), which is the
    one a person sets here -- not a collection of the rooms', which is why
    `rooms` is a footnote rather than the row: it names the rooms whose own
    bindings this global one stands in front of, which is the fact a person needs
    before wondering why binding here changed a room.

    The live status is read the same way a room's page reads it (`_view`,
    `_status`), so a house slot and a room slot cannot disagree about one entity:
    "unavailable" in one list is "unavailable" in the other. An entity the house
    does not hold is still listed, as `missing`, because it is a binding somebody
    made -- and a house that quietly dropped it from "all the lights" would be
    hiding the very broken binding a person opens this screen to find.

    `accepts_domains` is empty here for the reason `_slot_status` states: the
    vocabulary deliberately carries no domains, and the layer that has the
    catalog fills them in (`views.house_scope`).
    """
    entity_id = session.house_bindings.get(slot)
    view = None if entity_id is None else _view(session, entity_id)
    return {
        "slot": slot,
        "label": humanize(slot),
        "required": _required(session.vocabulary.slots.get(slot)),
        "accepts_domains": (),
        "entity_id": entity_id,
        "registry_id": None,
        "friendly_name": None if view is None else _friendly_name(view),
        "domain": None if entity_id is None else entity_id.partition(".")[0] or None,
        "state": None if view is None else view.state,
        "status": "unbound" if entity_id is None else _status(view),
        "rooms": tuple(
            room.name for room in session.rooms if room.bindings.get(slot) is not None
        ),
        "modules": tuple(sorted(modules)),
    }


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

    The house is a placement here too (`HOUSE`), and its verdict is the union of
    every room's bindings rather than a room's own -- the same gather the House
    tab's roles are made of.
    """
    return _offers_for(session, room_id)


def _offers_for(session: LiveSession, room_id: str) -> tuple[Mapping[str, object], ...]:
    """`offers` for a placement, which may be the house (`HOUSE`).

    The house's verdict is read against every room's bindings gathered, which is
    the union `resolve_slot` makes for a house-scoped role -- a pack the house
    can be given is one whose required slots *any* room fills.
    """
    if room_id == HOUSE:
        bound = frozenset(slot for room in session.rooms for slot in room.bindings)
    else:
        bound = frozenset(session.require_room(room_id).bindings)
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


def _reach(
    session: LiveSession, pack: str, unit: str, placement: str
) -> tuple[str, ...]:
    """Where one atom applies, with its module's placement as the default.

    `Engine.active_rooms` is the whole truth once anything has been decided -- a
    room ticked, a pack switched on -- and this returns it unchanged whenever it
    has anything to say. What it cannot answer is the state a pack arrives in:
    installation is not activation (`install` says so, and the tests hold it to
    that), so a fresh pack has an enable flag under none of its units, the engine
    answers "no room", and the reach control beside each atom reads "Nowhere"
    beside a module a person put in the Kitchen moments ago. "Nowhere" is an
    answer to a question nobody asked. The person said where the module goes when
    they chose a room, so that placement is the default the control shows, and
    the first tick or enable replaces it with the engine's own answer.

    A pack that *has* been switched on keeps the engine's answer exactly, which
    is what lets the last room be unticked: the master flag is open, the atom is
    on nowhere, and the control says so rather than falling back to the placement
    and refusing to be cleared.
    """
    decided = session.engine.active_rooms(unit)
    if decided:
        return decided
    master = session.engine.settings.resolve_or(
        module_enable_key(pack), HouseScope(), False
    )
    if master.flag():
        return ()
    return (HOUSE,) if placement == HOUSE else (placement,)


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
    missing = _unbound_required(session, record.name, room_id)
    option_schema, option_values = _module_options(session, record, room_id)
    return {
        "pack": record.name,
        "name": str(strings.get("pack", record.name)),
        "version": record.version,
        "room_id": room_id,
        # Whether the module is in the whole house rather than in one room, which
        # is a placement and not a scope: `room_id` is `HOUSE` (the empty string)
        # for it, and the panel says "the whole house" over that row. Both facts
        # are stated because the panel draws the row's home and its evaluation
        # scope separately -- a module installed into a bedroom with a
        # house-scoped atom is in the room and acts on the house.
        "house": room_id == HOUSE,
        "enabled": _enabled(session, record, room_id),
        # The wiring verdict travels with the module because the panel renders
        # it and computes none (`panel/README.md`): the Enable switch is drawn
        # disabled with these names beside it, and the reasoning that produced
        # them -- room scope for a room's slot, house scope for the house's --
        # lives here and not in TypeScript.
        "satisfiable": not missing,
        "missing_slots": missing,
        # The scope the module is evaluated in, which is the panel's answer to
        # "is this a room's module or the whole house's". `house` only when every
        # behaviour the pack registered is house-scoped: one room-scoped
        # behaviour in the pack makes the pack a room's, because the room is
        # where the rest of it acts. A pack with no behaviours is `room` by the
        # same default the engine reads, and it is never enabled anyway.
        "scope": _module_scope(session, record),
        # The settings this pack owns, so the panel can draw its card as one
        # subject -- the module's behaviours and the module's options together --
        # rather than a flat form of every module's settings with a separate
        # table of switches somewhere else. The keys are the resolver's own
        # (`live_profiles.pack_option_keys`), so the panel groups by them without
        # parsing a key or keeping a second copy of the key space.
        "option_keys": pack_option_keys(session, record),
        # The module's own settings: the slice of its scope's option form that
        # belongs to this pack, and the values those keys hold now. This is what
        # lets a module be drawn as one subject -- its behaviours and its settings
        # together -- instead of the switches living in a table and every module's
        # numbers in a form elsewhere. The panel renders `options_schema` with the
        # same control per type it renders anywhere, so a module's durations,
        # switches, choices and lists are all editable inside the module's own
        # card. `None` when the pack declares no options, which is the panel's
        # "nothing to configure here" rather than a form with no fields in it.
        "options_schema": option_schema,
        "options": option_values,
        "behaviours": tuple(
            {
                "id": unit,
                "label": str(strings.get(unit.rpartition(".")[2], unit)),
                # What the behaviour actually *does* when it is switched on, in a
                # sentence built from its own declaration -- the watched device,
                # the reading that must hold, how long, and what it writes. A chip
                # that carried only a name ("The fridge has been open too long")
                # told a person nothing about the act behind it, which is the one
                # question a switch has to answer before somebody flips it.
                "description": _behaviour_summary(session, unit),
                "enabled": _flag(session, unit, room_id),
                # Where this atom actually runs, which is the reach control's
                # answer and not a second copy of it: the rooms the engine's own
                # enable flags let through, asked of the engine
                # (`Engine.active_rooms`). A pack whose atoms run in every room
                # they are wired for answers every room here, which is the fact a
                # person narrows by unticking the ones they do not want -- and
                # the reason this is not derived from the module's placement: a
                # room behaviour is evaluated in every room, so "its module is
                # somewhere" is not where it runs.
                "active_rooms": _reach(session, record.name, unit, room_id),
                # The scope this atom runs in, and whether the other one is
                # available to it. Both are the engine's answers rather than this
                # module's: `_chosen_scope` reads the resolver the engine decides
                # by, and `reaches_house` is the engine's own verdict on whether
                # widening the atom would resolve. `widenable` being false is
                # what makes the panel draw a room-only atom without a switch it
                # could not honour -- a behaviour whose slots the house scope
                # does not carry, such as one reaching a room type's own device.
                "scope": str(_chosen_scope(session, unit)),
                "declared_scope": str(_declared_scope(session, unit)),
                "widenable": session.engine.reaches_house(unit),
            }
            for unit in record.behaviours
        ),
    }


def _module_options(
    session: LiveSession, record: InstalledPack, room_id: str
) -> tuple[Mapping[str, object] | None, Mapping[str, object]]:
    """The slice of `room_id`'s option form that belongs to one module.

    The scope's whole form is `live_profiles.options`, which is every pack's
    settings at once -- the right shape for a page that sets a room, the wrong
    one for a card that sets one module. This cuts it to the keys the module owns
    (`pack_option_keys`), so a card renders exactly its own settings and a module
    with none has no form rather than an empty one. The two halves are the
    *resolver's* keys on both sides, so the schema a card draws and the values it
    seeds cannot drift from what the engine reads.

    Keys the scope's schema does not declare are dropped rather than invented: a
    field for a setting that does not resolve here is a control that writes
    nothing, and `pack_option_keys` already keeps only keys the pack owns.
    """
    keys = pack_option_keys(session, record)
    if not keys:
        return (None, {})
    schema, values = options_for(session, room_id)
    declared = {} if schema is None else schema.get("properties") or {}
    properties = {key: declared[key] for key in keys if key in declared}
    if not properties:
        return (None, {})
    return (
        {"type": "object", "title": OPTIONS_TITLE, "properties": properties},
        {key: value for key, value in values.items() if key in properties},
    )


def _behaviour_summary(session: LiveSession, unit_id: str) -> str:
    """A plain sentence for what a behaviour does, read off its own declaration.

    The chip a module draws names a behaviour ("The fridge has been open too
    long") and a name is not an answer to the question a switch raises: what
    happens if I turn this on? The facts that answer it are the behaviour's own
    clauses -- the device it watches, the reading it waits for, how long that has
    to hold, and the device it writes to -- and this assembles them into one
    sentence so the person flipping the switch reads the act, not the label.

    Best-effort and never the authority: it describes a *declared* behaviour, and
    an engine unit with a different shape answers the empty string rather than a
    guessed sentence. The panel draws it as help text under the chip; the switch
    it explains is the same switch the engine gates on the behaviour's flag.
    """
    unit = session.engine.behaviours.get(unit_id)
    if unit is None:
        return ""
    watch = getattr(unit, "watch_slot", None)
    action = getattr(unit, "action_slot", None)
    match = tuple(str(part) for part in (getattr(unit, "match", ()) or ()))
    held = getattr(unit, "for_option", None)
    states = tuple(str(part) for part in (getattr(unit, "states", ()) or ()))
    mode = getattr(unit, "mode", None)

    opening = ""
    if watch:
        if match:
            opening = f"When the {humanize(watch)} reads {_or(match)}"
        else:
            opening = f"When the {humanize(watch)} changes"
    if held:
        title = _option_title(unit, str(held))
        clause = f"once it has held for the “{title}” setting"
        opening = f"{opening}, {clause}" if opening else clause.capitalize()
    acts: list[str] = []
    if action and states:
        acts.append(f"sets the {humanize(action)} to {_or(states)}")
    if mode:
        acts.append(f"puts the house in {humanize(str(mode))} mode")
    if not acts:
        acts.append("writes nothing this panel can describe")
    body = " and ".join(acts)
    if not opening:
        return f"It {body}."
    return f"{opening}, it {body}."


def _or(words: tuple[str, ...]) -> str:
    """`('a',)` -> "a"; `('a', 'b')` -> "a or b". A reading list, in words."""
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + f" or {words[-1]}"


def _option_title(unit: object, key: str) -> str:
    """The declared title of option `key` on `unit`, or its humanized name."""
    rows = getattr(unit, "options", ()) or ()
    for row in rows:
        if isinstance(row, Mapping) and row.get("key") == key:
            title = row.get("title")
            return str(title) if title else humanize(key)
    return humanize(key)


def _offer(
    session: LiveSession,
    entry: _Published,
    *,
    bound: frozenset[str],
    installed: InstalledSet,
) -> Mapping[str, object]:
    """One catalog pack as the panel's `ModuleOffer`, with its whole verdict."""
    document = entry.manifest.document
    # The pack's slots in the keys a room binds them under, and including the
    # devices the pack declares itself: a module whose requirement is a
    # `fridge_contact` it brought with it must report that slot as one the room
    # can fill, or the offer would read "this room can satisfy it" while the
    # module sat disabled waiting for a device the panel never showed.
    required = required_keys(entry.name, document)
    optional = optional_keys(entry.name, document)
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
        # The pack's own `options` clause, drawn as the same JSON Schema a room's
        # settings page renders -- so a person choosing a pack can see what it
        # will ask them to set *before* installing it, from the declaration the
        # manifest itself carries. `None` rather than an empty object when the
        # pack declares none, because the two are different answers: "nothing to
        # configure" and "no form to draw" read differently in the panel.
        "options_schema": _options_schema(entry.name, document),
        "behaviours": tuple(
            {
                "id": row[0],
                "label": str(entry.strings.get(row[0], row[0])),
                "priority": row[1],
            }
            for row in _behaviour_rows(document)
        ),
    }


def _options_schema(
    pack: str, document: Mapping[str, object]
) -> Mapping[str, object] | None:
    """A manifest's `options` clause as a JSON Schema, or `None` when it has none.

    The same projection the room's settings page uses, so a pack's settings read
    identically before and after it is installed: `live_profiles` builds it from
    the declaration carried on a registered unit and this builds it from the
    manifest a catalog entry holds, and the two produce one shape because they
    are one function. A pack with no options is `None`, which is the panel's
    "there is nothing to configure here" rather than a form with no fields in it.
    """
    properties = option_properties(pack, option_rows(document))
    if not properties:
        return None
    return {"type": "object", "title": OPTIONS_TITLE, "properties": properties}


def options_for(
    session: LiveSession, room_id: str
) -> tuple[Mapping[str, object] | None, Mapping[str, object]]:
    """A room's options schema and values, or the empty pair for no room.

    `HOUSE` is the house's own options, which the packs put in the house declare
    and which resolve at house scope -- the House tab's settings form, drawn from
    the same declaration a room's form is.

    Any other empty `room_id` is not a failure here. It is what a pack that
    reached no single room answers with (`_module_room`), so the caller is a page
    about a room that does not exist rather than a person asking about one that
    does -- and `live_profiles.options` would raise for it, which would turn
    "this pack belongs to the whole house" into an error message on a page that
    loaded fine.
    """
    if room_id != HOUSE and (not room_id or session.room(room_id) is None):
        return (None, {})
    return room_options(session, room_id=room_id)


def _acts_in(session: LiveSession, module: Mapping[str, object], room_id: str) -> bool:
    """Whether `module` belongs on `room_id`'s page.

    Two facts, either of which puts it there. The pack *placed in this room* is
    the first -- a person put it there, and its switches are the room's. The pack
    whose settings the room's own form carries (`live_profiles.reaches_room`) is
    the second, and it is the one that used to be missing: the form is built from
    the packs that reach the room, so a page that listed only the packs placed in
    it left every other pack's settings with no card to sit under, and the panel
    collected them into one "Other settings" lump.

    The second fact is why a card on a room's page can name another room as where
    the module lives: a bedtime button installed in the bedroom reaches a kitchen
    that binds the same roles, and the kitchen's page is where its lights-off
    setting is read.

    `house_scope` answers for the house, which has no bindings of its own; this
    is only reached for a real room.
    """
    if module["room_id"] == room_id:
        return True
    record = session.engine.installed.get(str(module["pack"]))
    return record is not None and reaches_room(session, record, room_id)


def _room_detail(session: LiveSession, room_id: str) -> Mapping[str, object]:
    """The panel's `RoomDetail` for `room_id`, as far as the module half reads it.

    `open_house/modules/install` and `open_house/modules/uninstall` both answer
    with a room page, so these two operations owe one. It is built here rather
    than composed by the handler because a handler is text, not a place to keep a
    shape.

    The options half is *delegated* rather than rebuilt: `live_profiles.options`
    owns it, and this calls it, so a room page answered here and a room page
    answered by `open_house/rooms/options/get` cannot disagree about what a room
    can be configured with. Only `axes` is still left empty, and it belongs to
    the profile set rather than to this module.

    An empty `room_id` means the pack belonged to no single room (`_module_room`),
    and the answer is the empty page: naming a room the pack is not in would be
    worse than naming none.
    """
    room = session.room(room_id)
    selection = {} if room is None else dict(session.profiles.selection(room_id))
    options_schema, options = options_for(session, room_id)
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
        "options_schema": options_schema,
        "options": options,
        "modules": tuple(
            module
            for module in installed_modules(session)
            if room_id != "" and _acts_in(session, module, room_id)
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


def _module_scope(session: LiveSession, record: InstalledPack) -> str:
    """Whether a pack is the house's or a room's, read off its own behaviours.

    `house` only when there is at least one behaviour and every one of them is
    house-scoped -- one room-scoped behaviour makes the pack a room's, because
    the room is where that behaviour acts and a "house module" label over it
    would be a promise the pack does not keep. A pack with no registered
    behaviour keeps the engine's own default, which is `room`.

    The scope read is the *chosen* one (`_chosen_scope`) and not the declared
    one, because a person who narrowed a house module to a room has made it a
    room's module and the panel should say so. The two agree until somebody
    says otherwise, which is the point of the setting.
    """
    units = [unit for unit in record.behaviours if unit in session.engine.behaviours]
    if not units:
        return str(BehaviourScope.ROOM)
    if all(_chosen_scope(session, unit) is BehaviourScope.HOUSE for unit in units):
        return str(BehaviourScope.HOUSE)
    return str(BehaviourScope.ROOM)


def _chosen_scope(session: LiveSession, unit: str) -> BehaviourScope:
    """The scope a unit is evaluated in, which is its declared one until chosen.

    Read through the resolver under `scope_key` and never off the unit, for the
    reason `_flag` resolves its flag: what the panel shows has to be what the
    engine decides by, and a value this module kept would be a second answer.

    A widening the house cannot carry out is reported as not having happened,
    because it will not have: the engine ignores it (`Engine._scopes`) rather
    than refusing a slot the house scope does not resolve, so a panel that
    echoed the setting back would be showing a scope the next tick would not use.
    """
    engine = session.engine
    chosen = engine.settings.resolve_or(
        scope_key(unit), HouseScope(), str(_declared_scope(session, unit))
    ).value
    if chosen != str(BehaviourScope.HOUSE):
        return BehaviourScope.ROOM
    return BehaviourScope.HOUSE if engine.reaches_house(unit) else BehaviourScope.ROOM


def _declared_scope(session: LiveSession, unit: str) -> BehaviourScope:
    """The scope the pack itself declared for `unit`, or `room` if it is unknown."""
    found = session.engine.behaviours.get(unit)
    if found is None:
        return BehaviourScope.ROOM
    return found.scope


def _module_room(session: LiveSession, record: InstalledPack) -> str:
    """The room a pack belongs to: the one it was installed into, or the join.

    The recorded answer first, because a person who was asked which room gave
    one and the house kept it (`LiveSession.place_module`). It is the only
    answer that works for a pack whose slots are house-scope: `light_group` and
    `lock` belong to the whole house, so `bedtime` reaches through them
    everywhere and no room binds them at all, and a join over those entities
    returns no room -- which reads as a module that can never be enabled.

    The join is the fallback for the two cases that have no recorded room: a
    pack installed from the Store, which has no room open to name, and a house
    that was configured before the placement was stored. It is made from the
    entities rather than from the slot names: a slot bound in one room and a
    slot bound in another are one pack the house supplies and no room that holds
    it, and answering "the first room that binds something" would tell a person
    their module is in a room it cannot act in. Every room-scoped entity,
    therefore, and the empty id when no single room holds them.
    """
    placed = session.module_room(record.name)
    if placed is not None:
        return placed
    reached = tuple(
        (slot, entity)
        for slot, entities in record.slots
        for entity in entities
        # House-scoped slots are the house's and belong to no room, so a pack
        # that reaches through one is not thereby *in* whichever room happens to
        # name it -- `_bound` records them beside the room's own, and matching
        # them here would make the join fail for every pack that uses one.
        if slot not in session.engine.house.house_scope_slots
    )
    if not reached:
        return ""
    for room in session.rooms:
        if all(room.bindings.get(slot) == entity for slot, entity in reached):
            return room.id
    return ""


def _enabled(session: LiveSession, record: InstalledPack, room_id: str) -> bool:
    """Whether every behaviour the pack registered is on where it is placed.

    `room_id` is the pack's placement: a room id, or `HOUSE` for a pack put in
    the whole house, whose flags then resolve at house scope. A pack with no
    behaviours at all is never "enabled", because there is nothing of it to run.
    """
    if not record.behaviours:
        return False
    return all(_flag(session, unit, room_id) for unit in record.behaviours)


def _flag(session: LiveSession, unit: str, room_id: str) -> bool:
    """One unit's enable flag where it is placed, resolved through the engine.

    Resolved rather than remembered: the flag a module reports has to be the flag
    the engine would decide by, and a field this module kept would be a second
    answer free to drift from the resolver's. `False` is the fallback for a unit
    the engine does not register -- the built-in layer is the *unit's* to state
    (`engine/config.py`), and a pack's unit that this build never registered has
    no default to read.

    The scope is the placement's (`_scope_for`): a room's flag for a module put
    in that room, and the house's for one put in the house.
    """
    return bool(
        session.engine.settings.resolve_or(
            enable_key(unit), _scope_for(room_id), False
        ).value
    )


def _declared_required(session: LiveSession, name: str) -> tuple[str, ...]:
    """The slots `name` requires a room to bind, as the keys it binds under.

    Re-read from the index the panel installs *from* rather than remembered on
    the installed record, because the record is deliberately a house's memory of
    what a person chose and not a copy of the manifest:
    `ha_adapter/declared_units.py` resolves the manifest the same way for the
    same reason.

    Both clauses that can require a slot are read -- `requires_slots`, and a
    `slots` declaration written `required: true` -- and both are returned as
    binding *keys* rather than as written names, because this answer is compared
    against a room's bindings and a separate declaration binds under a
    pack-qualified key (`engine/declared_slots.py`). That is one call to
    `required_keys` rather than a list comprehension here, so the pack that
    declares its own required device and the pack that names a catalog one are
    gated by the same reading.

    A name with no row in the index answers with nothing, so a pack whose
    registry was pruned is not thereby un-enableable: the fact this gate is about
    is a *declaration*, and a declaration nobody can read is not one this module
    gets to invent. The visible symptom of a pruned registry is already that the
    pack's behaviours never run.
    """
    entry = _by_name(_published(session.root)).get(name)
    if entry is None:
        return ()
    return required_keys(name, entry.manifest.document)


def _unbound_required(session: LiveSession, name: str, room_id: str) -> tuple[str, ...]:
    """The slots `name` requires that `room_id` binds nothing to.

    Room scope for a room-scoped slot and house scope for a house-scoped one,
    which is the split `_bound` records by and the split `_reached` resolves by:
    `light_group` belongs to a room and is bound there, while a house-scoped slot
    is the house's and belongs to no room, so asking only `room.bindings` would
    report every pack that reaches through one as permanently unwired.

    The house (`room_id` is `HOUSE`) answers against the house's bindings, which
    is what `_reached` gathers for every slot: a room-scoped role is bound when
    *any* room binds it and a house-scoped one when the house resolves it, so a
    module put in the house is enableable exactly when the house has the devices
    it needs.
    """
    required = _declared_required(session, name)
    if not required:
        return ()
    if room_id == HOUSE:
        return tuple(slot for slot in required if not _reached(session, slot))
    room = session.require_room(room_id)
    house_slots = session.engine.house.house_scope_slots
    unbound: list[str] = []
    for slot in required:
        if slot in house_slots:
            bound = bool(_reached(session, slot))
        else:
            bound = room.bindings.get(slot) is not None
        if not bound:
            unbound.append(slot)
    return tuple(unbound)


def _refuse_unless_wired(session: LiveSession, name: str, room_id: str) -> None:
    """Refuse to switch a module on while the devices it needs are not bound.

    The sentence names every missing slot rather than the first, because the
    person reading it is on the room's settings page with all of them to fill.
    """
    missing = _unbound_required(session, name, room_id)
    if not missing:
        return
    named = ", ".join(repr(slot) for slot in missing)
    raise LiveSessionError(
        f"the module {name!r} cannot be enabled yet: it requires {named}, which "
        "nothing in this room is bound to. Configure those devices on the room's "
        "settings and enable it again."
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
    session: LiveSession,
    projected: pack_sandbox.Pack,
    *,
    room: LiveRoom | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The entities the pack's slots reach, in the room it is going into.

    With a `room`, a room-scoped slot reaches that room's binding and nothing
    else, because that is where the pack was told to go: the record is what the
    module is, and a module that recorded every room's motion sensor would
    belong to no room at all (`_module_room`). A house-scoped slot still
    resolves at house scope, because no room owns it -- the house does.

    With no `room` -- the Store's install, which has no room open -- every
    room's binding is read, which is the join `_module_room` needs to place a
    pack nobody has placed. A slot bound nowhere is still recorded with an empty
    tuple, so "nothing supplies this" is a fact in the record rather than an
    absence from it.

    Each name is resolved to the *key* the house binds it under before anything
    is looked up (`engine/declared_slots.py`): a declaration the pack asked to
    hold separately binds under a pack-qualified key, so looking the written name
    up in `room.bindings` would find nothing for a device the room has bound. The
    record is keyed by keys for the same reason -- `_module_room` joins it
    against `room.bindings`, and a record keyed by written names would match no
    room and answer "the module is in no room".
    """
    # Every name resolved to the key the house binds it under before anything is
    # read: a declaration written `separate: true` binds under a pack-qualified
    # key, and looking the written name up in `room.bindings` would find nothing
    # for a device the room has bound. The record is keyed by keys for the same
    # reason -- `_module_room` joins it against `room.bindings`.
    keys = tuple(sorted(projected.bound_key(slot) for slot in projected.declared_slots))
    if room is None:
        return tuple((key, _reached(session, key)) for key in keys)
    house_slots = session.engine.house.house_scope_slots
    return tuple(
        (
            key,
            _reached(session, key)
            if key in house_slots
            else (() if (entity := room.bindings.get(key)) is None else (entity,)),
        )
        for key in keys
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
