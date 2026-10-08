"""Room options and profiles over a live session: the pure half of two tabs.

`ha_adapter/live.py` builds the session -- one engine, its rooms, its packs and
its profile selections -- and the websocket layer in `custom_components/` is
what a panel command calls. This module is the operation between them: the reads
and the one write a room's settings page and the profiles screen need, in the
form the panel's view models take (`panel/src/api/models.ts`), so that the
websocket handler is a call and a `return` rather than a place two edits can
disagree about what a profile or an option is.

**Nothing here is a second copy of the engine.** A selection lives in the
`ProfileSet`, an option's value lives in the `ConfigResolver`'s override layer,
and both are read *through* the session's current engine rather than cached
here. That is the same rule the facade is written under and for the same reason:
a module that shadowed one of them would be a third place for it to disagree
with the two that own it, and the disagreement would show up as a panel that
says a profile is on while the engine decides it is off.

**Every operation is a projection, not a policy.** `options` and `profiles` read
what the engine holds and reshape it; `set_option` writes one override and
`activate` moves one selection, each leaving every decision about what the house
does to the next tick. Neither rebuilds the world: an override is engine state
(`live.py`'s `set_room_auto_lighting` is the precedent) and a profile is resolved
at construction, so a selection does rebuild -- once -- because the resolver's
layers are fixed when the engine is built and a profile that changed a setting
has to be visible to the very next tick.

**The high-level options are the one part the panel cannot get anywhere else.**
A room's settings page shows "only high-level options, rendered from pack schemas
with no pack-supplied JS" (`spec.txt`), which is two requirements in one: the
schema has to be *data* the panel's own form renderer can draw, and it has to
come from the packs installed in that room rather than from a table written by
hand. So the schema built here is a plain JSON Schema -- the subset
`panel/src/components/schema-spec.ts` declares, and nothing outside it, because a
keyword the renderer does not know is a field it draws as `unsupported` -- and
its declarations are gathered from the packs this house holds, filtered to the
ones that reach the room being asked about.

A pack declares two kinds of setting, and they arrive by different roads.
`pack-manifest/1.3.0`'s top-level `options` clause is the author's own form --
one typed entry per tunable, with a title, a description, bounds and members --
and it is carried on every unit the pack registered
(`engine/behaviours/declared.py`), because an installed record keeps a digest
rather than a document and the build is the last place the two can be seen
together. A *unit's* `defaults` mapping is the other road, and it is what the
three hand-written behaviours use; a declared behaviour contributes none, since
its manifest states its settings rather than its code.

Both land under one key space and the same JSON Schema, which is the point:
`option_properties` draws a declared option as `schema-spec.ts`'s checkbox, number
field or select, and `_node` guesses the same three from a Python default. A pack
that registered no unit at all contributes nothing, which is the honest answer and
not a failure -- the record names behaviour ids, and a manifest's clause is read
through the unit those ids resolve to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import cast

import jsonschema

from engine.behaviours import scope_key
from engine.behaviours.base import BehaviourScope
from engine.behaviours.declared import option_key, reach_key, slot_label_key
from engine.binding import HouseScope, RoomScope
from engine.install import InstalledPack, InstalledSet
from engine.profiles import Profile, ProfileError, ProfileKind

from .live import HOUSE, LiveSession, LiveSessionError

__all__ = [
    "activate",
    "activate_house",
    "active_profiles",
    "capture",
    "deactivate_house",
    "export_document",
    "humanize",
    "import_document",
    "option_properties",
    "options",
    "profiles",
    "reach_properties",
    "remember",
    "set_option",
]

#: The title the built schema carries. The panel renders a field's label from
#: the node's own `title` and only falls back to humanising the key, so a schema
#: with no title at all would be a form whose heading is a raw config key.
OPTIONS_TITLE = "Room options"

#: The same heading for the house's own form. The house is a target like a room,
#: so its options are drawn by the same code and differ only in the word: a form
#: headed "Room options" on the House tab would name a room the person is not on.
HOUSE_OPTIONS_TITLE = "House options"

#: The JSON Schema type each declared default projects to, in the order the test
#: has to run: `bool` first because it is an `int` in Python, and a flag read as
#: a number would render a checkbox as a spinner. A behaviour's default that is
#: none of these -- a nested object, a `None` -- has no type the panel can draw,
#: so it is dropped rather than emitted as a node the renderer calls
#: `unsupported`. The mapping is here rather than inline because it is the one
#: place a Python value becomes a schema type, and the check that every emitted
#: node is renderable reads the same table.
_TYPES: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
)


def options(
    session: LiveSession, *, room_id: str
) -> tuple[Mapping[str, object] | None, Mapping[str, object]]:
    """The room's high-level options: the schema to draw, and the values it holds.

    `None` for the schema when nothing installed in this room declares an option,
    which is not the same as an empty object: the panel's `RoomDetail` carries
    `options_schema: JsonSchema | null`, and "there is nothing to configure here"
    is the null case rather than a form with no fields in it. The values are still
    returned -- empty -- so a caller never has to branch on which half it got.

    The values are *resolved*, not read from the schema's defaults: an option a
    person has overridden has to read back as what they set, or `set_option`
    would be a write the next `options` call forgot. They resolve at the scope
    the module is placed in -- the room's for a room module, the house's for one
    put in the house (`HOUSE`) -- through the engine's own resolver, so the answer
    is the one the behaviours will read on the next tick and not a second opinion
    about it.
    """
    if room_id != HOUSE:
        session.require_room(room_id)
    properties = _declarations(session, room_id)
    if not properties:
        return (None, {})
    schema: dict[str, object] = {
        "type": "object",
        "title": HOUSE_OPTIONS_TITLE if room_id == HOUSE else OPTIONS_TITLE,
        "properties": properties,
    }
    scope = HouseScope() if room_id == HOUSE else RoomScope(room_id)
    values = {
        key: session.engine.settings.resolve_or(key, scope, node["default"]).value
        for key, node in properties.items()
    }
    return (schema, values)


def set_option(
    session: LiveSession, *, room_id: str, key: str, value: object
) -> tuple[Mapping[str, object] | None, Mapping[str, object]]:
    """Set one option for a room, or refuse it naming what is wrong.

    The value is written twice and the two writes are for two different times,
    which is `LiveSession.remember_setting`'s whole contract. As an *override* on
    the running engine it is the top of the resolver's stack and takes effect on
    the next tick; as a value in the session's own settings -- the room's or the
    house's, following `room_id` -- it survives a rebuild and a restart. Until
    this was one call it wrote only the override, and the override layer is
    in-memory by definition (`engine/config.py`), so a person who tuned a pack's
    duration in the Kitchen lost it the moment any profile was activated, because
    activating one rebuilds.

    It is engine state rather than wiring, so nothing here rebuilds -- the next
    tick reads the new value through the same resolver the tick after it will.

    The validation is against the schema this module just produced and not
    against a second table of what is legal: a value the schema rejects is a
    value the panel could not have drawn, so refusing it here is refusing the
    caller that talked past the form. Both refusals name the key, because a
    message that named only the value would leave a reader hunting a form with a
    dozen fields.
    """
    if room_id != HOUSE:
        session.require_room(room_id)
    schema, _values = options(session, room_id=room_id)
    node = _property(schema, key)
    if node is None:
        where = "the house" if room_id == HOUSE else f"the room {room_id!r}"
        raise LiveSessionError(f"there is no option {key!r} in {where}")
    rejection = _rejects(node, value)
    if rejection is not None:
        raise LiveSessionError(
            f"the value {value!r} is not accepted for the option {key!r}: {rejection}"
        )
    scope = HouseScope() if room_id == HOUSE else RoomScope(room_id)
    session.remember_setting(key, scope, value)
    return options(session, room_id=room_id)


def profiles(session: LiveSession) -> tuple[Mapping[str, object], ...]:
    """Every profile the house holds, as the panel's `ProfileRef` rows.

    Ordered by name so two calls answer the same list, and carrying `label`
    because `models.ts` requires one: the frozen `schemas/profile/` document has
    no display name, so the label is the profile's own name humanised -- the same
    derivation `schema-spec.ts`'s `humanizeKey` makes for a field it has no title
    for, which is what keeps the panel from hard-coding a name per profile.

    `active` answers "is this profile in force somewhere", which is the question
    the screen asks: a room profile is active when a room is on it, and a house
    profile when the house is. It is deliberately not "is it on in *that* room" --
    the room a profile is on is `RoomDetail.active_profiles`, and a house profile
    is on no room at all.
    """
    selected = {
        name
        for axes in session.profiles.selections().values()
        for name in axes.values()
    }
    house_profile = session.profiles.house_profile
    held = session.profiles.profiles
    return tuple(
        {
            "name": profile.name,
            "label": humanize(profile.name),
            "description": profile.description,
            "kind": str(profile.kind),
            "axis": profile.axis,
            "active": _active(profile, selected, house_profile),
        }
        for _name, profile in sorted(held.items())
    )


def active_profiles(session: LiveSession) -> Mapping[str, str]:
    """The profile in force for each axis, house-wide, as `{axis: profile_name}`.

    The panel's `RoomDetail.active_profiles` is exactly this shape, one room at a
    time; a house's rooms may hold different profiles on the same axis -- that is
    what simultaneity by axis buys -- and this is the house's single answer for
    each one. Rooms are read in the set's own order (ascending room id, ascending
    axis) and the first room that names an axis decides it, so the map is a
    function of the profile set rather than of a dictionary's insertion order.

    A caller that needs a *room's* map reads `ProfileSet.selection(room_id)`,
    which is the same shape and is the one the room detail is built from: this
    answer is a summary and not a replacement for it.
    """
    merged: dict[str, str] = {}
    for _room_id, axes in session.profiles.selections().items():
        for axis, name in axes.items():
            merged.setdefault(axis, name)
    return merged


def activate(session: LiveSession, *, room_id: str, axis: str, profile: str) -> None:
    """Put a room on a profile for an axis, mirroring `OpenHouse.select_profile`.

    The selection is made on the session's own `ProfileSet` and then the engine is
    rebuilt, because the profile layer is supplied when the engine is constructed
    (`build_live_house`'s `profile_settings` / `profile_room_settings`) and a
    profile that changed a setting has to be visible to the very next tick. That
    is the same act the facade spells as `select_profile` followed by
    `_apply_profiles`, with one difference the live path forces: a live session
    rebuilds from configuration and not from a carried engine state, so the modes
    a profile carries are activated on the engine the rebuild produced rather than
    before it.

    A refusal is a `LiveSessionError` and not the `ProfileError` underneath it.
    `live.py` fixes the session's one failure type as the thing a websocket
    handler turns into an error *code*, and a second exception type crossing that
    seam would be a handler that has to catch two; the original message is kept
    verbatim so the code's detail still names the profile and the axis.
    """
    session.require_room(room_id)
    try:
        session.profiles.select(room_id, axis, profile)
    except ProfileError as refusal:
        raise LiveSessionError(str(refusal)) from refusal
    _rebuilt(session)


def activate_house(session: LiveSession, *, profile: str) -> None:
    """Put the *house* on a house profile, applying the room selections it bundles.

    A house profile is not selected in a room -- it is the thing that selects a
    profile for each room at once -- so it has a door of its own rather than a
    room id this path would have to invent. `ProfileSet.select` refuses a house
    profile by design, and the panel used to reach this by sending the literal
    room id `"house"`, which is the one room no house has.

    A profile that was *taken* from a house carries that house's settings as well
    as its selections, and those are written back where a person's own edits are
    written -- see `_restored`. A profile written by hand carries deltas instead
    and is applied the way it always was: its deltas resolve above the base for
    as long as it is in force.
    """
    try:
        held = session.profiles.profile(profile)
        session.profiles.activate_house_profile(profile)
    except ProfileError as refusal:
        raise LiveSessionError(str(refusal)) from refusal
    if held.snapshot:
        _restored(session, held)
    _rebuilt(session)


def _restored(session: LiveSession, profile: Profile) -> None:
    """Put a taken profile's house back, through the doors a person's edits use.

    Every setting here lands where a person's own edit of that part would have
    landed -- which is the whole difference between a profile that was written
    and one that was taken. A hand-written profile's deltas are resolved *over*
    the house's settings, so a key it names is a key nobody can set until the
    profile comes off; a house put back on a taken profile has to be a house
    somebody can carry on setting, or the next edit they make would be silently
    outranked by the profile they took a week ago.

    **The parts are restored whole, not merged.** A pack the house has installed
    and the snapshot does not is *removed*, a module placed in a room the
    snapshot does not place it in is moved, a binding the snapshot does not name
    is taken off. Restoring is putting the house back rather than adding what is
    missing: a merge would leave everything done since the profile was taken
    still in force, which is the state somebody takes a profile in order to
    leave.

    **Rooms are the one thing that cannot be restored from here.** A room is a
    configuration subentry and this is the session, so the room *list* and each
    room's own slot bindings are the caller's (see `restore_rooms`) -- what this
    restores is a room's settings, and only for the rooms that are still here. A
    room the house has since gained is left alone rather than given settings
    nobody ever chose for it.

    This rebuilds once per call it makes and `_rebuilt` rebuilds again, which is
    the price of using the doors a person's edits use rather than writing the
    fields behind them: a rule stated in two places is a rule that can be stated
    two ways.
    """
    setup = profile.setup
    session.set_installed(
        InstalledSet.from_document(_mapping(setup.get("installed"), "installed"))
    )
    session.set_house_settings(_mapping(setup.get("house_settings"), "house_settings"))
    recorded = _nested(setup.get("room_settings"), "room_settings")
    for room in session.rooms:
        session.set_room_settings(room.id, recorded.get(room.id, {}))

    bound = _mapping(setup.get("house_bindings"), "house_bindings")
    for slot in set(session.house_bindings) - set(bound):
        session.set_house_binding(slot, None)
    for slot, entity in bound.items():
        session.set_house_binding(slot, None if entity is None else str(entity))

    placed = _mapping(setup.get("module_rooms"), "module_rooms")
    for pack in set(session.module_rooms) - set(placed):
        session.forget_module(pack)
    for pack, room_id in placed.items():
        session.place_module(pack, str(room_id))


def capture(
    session: LiveSession,
    *,
    name: str,
    description: str,
    modules: Sequence[Mapping[str, object]] = (),
) -> Profile:
    """Take a profile from the house: the whole house, named.

    The other way a profile comes to exist -- written *for* a house by hand, or
    read *off* one. Everything a house is configured to be is here: the packs
    installed into it, its rooms and what each of them answers with, which room
    profile each room is running on each axis, the house's own settings and each
    room's, where each module was placed, and every module hosted in it with the
    configuration that module is on.

    `modules` is the one part the session cannot supply. A hosted module is the
    integration's -- a record in `modules.json` with its bindings, settings,
    picks, conditions, flows and its named configurations -- and the session
    knows only the placement of the *pack* it was made from. So the caller reads
    the records and hands them in, and they are carried through as they arrived:
    see `ProfileSet.capture` for why nothing here reshapes them.

    What is deliberately *not* captured is the mode the house is on, and the
    library of profiles itself. A mode is the one part of a house that is a
    *moment* rather than a setting -- the session does not persist them and the
    engine starts fresh on every restart -- so a profile that carried one would
    put a house back into an instant rather than into a way of being; a
    profile's `modes` are a hand-written profile's own business and are left
    empty here. The library is left out because a profile that held the library
    would hold itself: what a house is *on* is the selections, and those are
    captured.

    Nothing about the house changes: taking a profile is naming what it is, and
    putting it back on it is `activate_house`.
    """
    setup = _snapshot(session, modules)
    try:
        return session.profiles.capture(
            name=name,
            description=description,
            selections=session.profiles.selections(),
            setup=setup,
        )
    except ProfileError as refusal:
        raise LiveSessionError(str(refusal)) from refusal


def remember(
    session: LiveSession, *, modules: Sequence[Mapping[str, object]]
) -> Profile | None:
    """Let the house profile in force learn the house as it now is, when it can.

    A taken profile *is* a house, so a house on one is a house that agrees with
    it -- until somebody changes something. This is the change being written to
    the profile as well as to the house, which is what makes a profile something
    a house can live on rather than a photograph of a house it will be dragged
    back to: bound a slot, turned a module down, set a room's quiet timeout, and
    the profile is still the house you are looking at, so switching away and back
    brings *this* house back and not the one from before the edits.

    **Only what was taken is learned, and only a house that is on it.** There may
    be no house profile at all, and then there is nothing to tell. A *written*
    profile with deltas is left alone deliberately: its deltas are an instruction
    -- "keep the house quieter than it says" -- and an instruction that quietly
    rewrote itself into the house it was written over would be a rule its author
    can no longer edit. So this answers `None` for both, and a `Profile` when
    something was learned.

    **The caller says what the modules are, and this reads nothing.** A hosted
    module is a file rather than anything the session holds (see `capture`), and
    the rows go in rather than being looked up here because the one thing that
    knows where that file lives is the integration. They are the module rows *as
    they now are* -- a module's own settings can be what changed -- so a caller
    that has just written a module hands in what it wrote.

    Learning is not saving: this moves the profile in the set the session holds,
    and `Host.async_save` is what writes the set down. See `_snapshot` for the
    parts, and `_restored` for the reverse journey.
    """
    name = session.profiles.house_profile
    if name is None:
        return None
    held = session.profiles.profile(name)
    if not held.snapshot:
        return None
    try:
        return session.profiles.retake(
            name,
            selections=session.profiles.selections(),
            setup=_snapshot(session, modules),
        )
    except ProfileError as refusal:
        raise LiveSessionError(str(refusal)) from refusal


def _snapshot(
    session: LiveSession, modules: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    """The parts of a house a taken profile is made of, read off the session.

    One function for the two ways a profile comes to hold a house -- `capture`,
    which takes one, and `remember`, which takes one again -- because the two
    differ in *when* they are called and not in what a house is. Written twice,
    they would be two answers to "which parts is a house", free to disagree
    exactly at the part somebody had just changed.

    The modules are the one part the session cannot supply and the caller does:
    see `capture`.
    """
    state = session.to_state()
    return {
        "installed": state["installed"],
        "rooms": state["rooms"],
        "house_settings": state["house_settings"],
        "room_settings": state["room_settings"],
        "module_rooms": state["module_rooms"],
        "house_bindings": dict(session.house_bindings),
        "modules": [dict(row) for row in modules],
    }


def deactivate_house(session: LiveSession) -> None:
    """Take the house off its house profile. The selections it set stay.

    "The selections stay" is the engine's own rule and not a second one here
    (`ProfileSet.deactivate_house_profile`): a house profile is a bundle of room
    profiles, and taking the bundle off is not the same act as moving every room
    back to whatever it was on before -- a fact this house no longer holds.
    """
    session.profiles.deactivate_house_profile()
    _rebuilt(session)


def _rebuilt(session: LiveSession) -> None:
    """Rebuild, then activate the modes the new selections carry.

    One function because the two steps are one act and three callers perform it:
    the profile layer is supplied when the engine is built
    (`build_live_house`'s `profile_settings`), so a profile that changed a
    setting has to be visible to the very next tick, and a mode a profile
    activates has to be turned on on the engine the rebuild produced rather than
    on the one it replaced.
    """
    session.rebuild()
    for name in session.profiles.modes():
        if name in session.engine.modes.declared:
            session.engine.modes.activate(name)


# -- Export and import -------------------------------------------------------


def export_document(
    session: LiveSession, *, profile: str | None = None
) -> Mapping[str, object]:
    """One profile, or every profile, as the document the importer reads back.

    The two halves of the feature are one operation with two answers, because
    both produce *a profile document* -- the frozen `schemas/profile/` shape --
    and a second function for "all" would be a second place deciding what a
    profile document is. Without a name the answer is a set document
    (`{"profiles": [...]}`, `Profile.export`), which is the form `import_document`
    reads back unchanged.

    Nothing here reads a room: a profile names settings, not this house's
    hardware, which is what makes an export portable.
    """
    try:
        return session.profiles.export_document(profile)
    except ProfileError as refusal:
        raise LiveSessionError(str(refusal)) from refusal


def import_document(
    session: LiveSession, *, document: Mapping[str, object], replace: bool = False
) -> Mapping[str, object]:
    """Add the profiles a document names, answering what was added and replaced.

    **Every document is validated before any of them is applied.** The set is
    changed in two passes -- parse them all, then write them all -- because the
    one-pass version has a failure mode a person would meet exactly once and
    never trust again: a file whose third profile is malformed would leave the
    first two imported and report a failure, so "the import was refused" and "the
    house is now half of that file" would both be true.

    A name the house already holds is refused unless `replace` is set, and the
    refusal names every conflict rather than the first. A *replacement* goes
    through `ProfileSet.remove`, which takes any selection that named the old
    profile off with it, so a room is never left pointing at a profile whose axis
    has changed underneath it.
    """
    documents = _profile_documents(document)
    parsed = _parsed(session, documents)
    names = [profile.name for profile in parsed]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise LiveSessionError(
            f"the document names {_names(duplicated)} more than once"
        )
    held = session.profiles.profiles
    conflicts = sorted(name for name in names if name in held)
    if conflicts and not replace:
        raise LiveSessionError(
            f"this house already holds {_names(conflicts)}; "
            "import again with replace set to overwrite them"
        )
    for name in conflicts:
        session.profiles.remove(name)
    for entry in documents:
        session.profiles.add(entry)
    if names:
        _rebuilt(session)
    return {
        "imported": [name for name in names if name not in conflicts],
        "replaced": conflicts,
        "profiles": list(profiles(session)),
    }


def _profile_documents(
    document: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    """The profile documents a file holds, in either accepted form.

    A file is either one profile -- what "export this profile" writes, and the
    shape a person is most likely to be handed by somebody else -- or a set of
    them, what "export all profiles" writes. The two are told apart by the set's
    own key, which is also the key `ProfileSet.to_document` persists under, so a
    state file is importable as-is.
    """
    held = document.get("profiles")
    if held is None:
        return (document,)
    if not isinstance(held, list):
        raise LiveSessionError("the document's 'profiles' is not a list")
    for entry in held:
        if not isinstance(entry, Mapping):
            raise LiveSessionError("a profile in the document is not an object")
    return tuple(cast("list[Mapping[str, object]]", held))


def _parsed(
    session: LiveSession, documents: Sequence[Mapping[str, object]]
) -> tuple[Profile, ...]:
    """Every document parsed against the frozen schema, or a refusal naming one.

    Parsing is the same act `ProfileSet.add` performs, done here first so that a
    refusal happens before anything in the set has changed.
    """
    parsed: list[Profile] = []
    for index, entry in enumerate(documents):
        try:
            parsed.append(
                Profile.from_document(entry, schema=session.profile_schema, index=index)
            )
        except ProfileError as refusal:
            raise LiveSessionError(str(refusal)) from refusal
    return tuple(parsed)


def _names(names: Sequence[str]) -> str:
    """The names as a sentence reads them, because a refusal is read by a person."""
    return ", ".join(repr(name) for name in names)


# --------------------------------------------------------------------------
# Building the schema. Each function is one step and none reads a document the
# session does not hold, because a pack's manifest is not reachable from here --
# only the record the engine kept of it.
# --------------------------------------------------------------------------


def _declarations(session: LiveSession, room_id: str) -> dict[str, dict[str, object]]:
    """One schema property per tunable the room's installed packs declare.

    Ordered by key so the schema a caller receives is the same twice: a form
    whose fields reorder between reads is a form a person cannot use.

    The packs considered are the ones that *reach* this room, which is the
    filter the requirement names -- "only options the room's installed modules
    actually expose". A behaviour id no registered unit answers to contributes
    nothing, and that is not an error: a pack's manifest declares behaviour names
    the engine is free not to implement, and inventing a field for one would put
    a control on the screen for a setting nothing reads.

    Membership is `reaches_room` and not a test written here, because the page
    must list a card for every pack this form carries settings for: the same rule
    answers both, so a key can never be left with no card to sit under.

    For the house (`HOUSE`) the bound set is every room's, because the house's
    roles are the rooms' roles gathered (`engine.binding.resolve_slot`): a module
    put in the house reaches a role when *any* room binds it, so its options are
    shown against the union rather than a room the house does not have.
    """
    installed = session.engine.installed
    units = session.engine.behaviours
    declared: dict[str, dict[str, object]] = {}
    for pack in installed.names:
        record = installed.packs[pack]
        if not reaches_room(session, record, room_id):
            continue
        for behaviour_id in record.behaviours:
            unit = units.get(behaviour_id)
            if unit is None:
                continue
            # The pack's own declarations first, because they are the ones a
            # person is meant to set: a manifest's `options` clause states its
            # type, its bounds, its members and its words, where a unit's
            # `defaults` states only a value whose kind has to be guessed from
            # Python. Both land under one key space, so a later declaration for a
            # key an earlier one already named is dropped rather than duplicated.
            for key, node in option_properties(
                pack, getattr(unit, "options", ())
            ).items():
                declared.setdefault(key, node)
            for key, default in unit.defaults.items():
                node = _node(key, default, behaviour_id)
                if node is not None:
                    declared.setdefault(key, node)
        # The roles, last, so a pack that declared a `reach.<slot>` option of its
        # own keeps its own words: the derived control is the fallback every pack
        # gets, not a field that overwrites one an author wrote.
        acting = tuple(
            slot
            for behaviour_id in record.behaviours
            if (unit := units.get(behaviour_id)) is not None
            and (slot := getattr(unit, "action_slot", None)) is not None
        )
        for key, node in reach_properties(
            pack, acting, name_of=slot_names(session, pack)
        ).items():
            declared.setdefault(key, node)
    return {key: declared[key] for key in sorted(declared)}


def reaches_room(session: LiveSession, record: InstalledPack, room_id: str) -> bool:
    """Whether a page's form carries `record`'s settings.

    The *one* rule, and the reason it is a function rather than a line inside
    `_declarations`: a room's page draws a card per module and asks each card for
    the settings it owns (`pack_option_keys`), so the packs the form carries
    settings for and the packs the page draws cards for have to be the same set.
    When they were two rules, every key the form carried for a pack the page did
    not list fell through to the panel's leftover bucket -- one undifferentiated
    "Other settings" card holding another module's switches, which is a form a
    person cannot read and cannot tell apart from the room's own.

    A room's answer is membership of the room's bound slots (`_reaches`), not the
    room the pack was installed into: the form is the room's configurability, and
    a pack whose roles this room binds is one it can be configured against. The
    house's answer is the house's own rule (`_reaches_house`), because the house
    has no bindings of its own to gather -- it reads the rooms'.
    """
    if room_id == HOUSE:
        return _reaches_house(session, record)
    return _reaches(record, _bound_slots(session, room_id))


def _reaches_house(session: LiveSession, record: InstalledPack) -> bool:
    """Whether the house's own form should carry `record`'s options.

    A pack belongs on the House tab's form when its settings are read at house
    scope, which is one of two facts and not a guess. A pack *placed in the
    house* is the first: a person put it there, and its settings are the house's.
    A pack with at least one atom that resolves to house scope is the second, and
    it is why a bedtime button sitting in a bedroom still appears on the House
    tab -- its lights-off atom is house-scoped, reads its options at house scope,
    and a form that omitted the pack would leave that setting with nowhere to be
    set.

    The scope read is the *resolved* one (the engine's `scope_key` over the house
    setting, defaulting to the unit's declared scope), so a person who widened an
    atom to the house moves its pack onto this form and one who narrowed it takes
    the pack off. A pack with no registered behaviour and no house placement is
    not shown, which is the honest answer for a pack nothing evaluates.
    """
    if session.module_room(record.name) == HOUSE:
        return True
    return any(
        _resolved_scope(session, unit) is BehaviourScope.HOUSE
        for unit in record.behaviours
        if unit in session.engine.behaviours
    )


def _resolved_scope(session: LiveSession, unit: str) -> BehaviourScope:
    """The scope a unit is evaluated in, read the way the engine reads it."""
    found = session.engine.behaviours[unit]
    chosen = session.engine.settings.resolve_or(
        scope_key(unit), HouseScope(), str(found.scope)
    ).value
    if chosen != str(BehaviourScope.HOUSE):
        return BehaviourScope.ROOM
    # A widening the house scope cannot resolve is reported as not in force, the
    # same way `live_modules._chosen_scope` reads it: the engine ignores such a
    # setting (`Engine._scopes`), so a form built from it would carry options for
    # an atom that is still evaluated per room.
    return (
        BehaviourScope.HOUSE
        if session.engine.reaches_house(unit)
        else BehaviourScope.ROOM
    )


def _bound_slots(session: LiveSession, room_id: str) -> set[str]:
    """The slots the form is filtered against: one room's, or the whole house's.

    A room's are exactly the room's own bindings; the house's are every room's
    gathered, which is the same union `resolve_slot` makes for a house-scoped
    role. The house is `HOUSE` and has no room of its own, so asking the house
    for `room("")` would answer `None` and the caller would read an attribute off
    it.
    """
    if room_id == HOUSE:
        return {slot for room in session.engine.house.rooms for slot in room.bindings}
    return set(session.engine.house.room(room_id).bindings)


def pack_option_keys(session: LiveSession, record: InstalledPack) -> tuple[str, ...]:
    """Every setting key `record` owns, in the order its behaviours declare them.

    The join that lets a screen draw a module rather than a form: `_declarations`
    merges every pack's properties into one flat object, which is right for a
    schema and wrong for a person -- a card listing "Lux threshold" and "Door
    left open for" side by side, with nothing saying which module either belongs
    to, is a form nobody can reason about. This is the same computation the
    declarations make, kept per pack, so the panel can put each setting under the
    module that declares it without parsing keys.

    Membership is the resolver's own namespace -- a key belongs to `record` when
    it is spelled `module.<record>.<...>`, which is the prefix `option_key`
    writes -- and never a substring match, so `module.fan` does not claim
    `module.fan_boost`'s settings. The three sources are the pack's declared
    `options`, its units' own defaults, and the `reach.<slot>` checkboxes derived
    from the roles its behaviours act through; all three land in the form, so all
    three belong to the card.

    A **slot rule** (`ha_adapter.slot_rules`) is deliberately *not* here, and it
    is the one setting a module holds that is not: this is the list of keys a form
    draws, and a rule is drawn by the slot row that owns it rather than as a field
    of its own. Its keys are still settings, so a profile captures and restores
    them with the rest of the room's (`_snapshot` reads `LiveSession.to_state`,
    which carries the layers whole) -- which is the property that matters, and the
    one a list of *form* keys was never what provided.
    """
    prefix = option_key(record.name, "")
    units = session.engine.behaviours
    keys: list[str] = []
    for behaviour_id in record.behaviours:
        unit = units.get(behaviour_id)
        if unit is None:
            continue
        candidates = list(
            option_properties(record.name, getattr(unit, "options", ()))
        ) + list(unit.defaults)
        acting = getattr(unit, "action_slot", None)
        if acting is not None:
            candidates += list(reach_properties(record.name, (acting,)))
        for key in candidates:
            if key.startswith(prefix) and key not in keys:
                keys.append(key)
    return tuple(keys)


def slot_names(session: LiveSession, pack: str) -> Callable[[str], str | None]:
    """A reader for the names a person has given one module's slots.

    The label half of the per-module, per-slot override (`ha_adapter.live_modules
    .set_slot`), which is why this returns a *reader* rather than a mapping: the
    names live in the resolver's own namespace (`module.<pack>.slot.<slot>.label`)
    and the caller has the slot names, so one callable answers for whichever slot
    it is asked about.

    Read from the session's recorded settings rather than from the live resolver,
    because the question is "what did a person call this" and only the recorded
    layer holds a decision -- the resolver would answer with the slot's derivation
    for a slot nobody has named, which is the fallback and not a name. The scope
    is where `set_slot` records it: the module's own placement, the house's for a
    module put in the house.

    A module with no recorded placement yet is searched at house scope and then
    every room's, in room order, because a label is display-only: a name written
    before the placement was stored still reads back, and the search is
    deterministic, so a second answer would be a curiosity rather than a wrong
    name on a checkbox.
    """
    placed = session.module_room(pack)

    def name_of(slot: str) -> str | None:
        key = option_key(pack, slot_label_key(slot))
        if placed is not None:
            scope: HouseScope | RoomScope = (
                HouseScope() if placed == HOUSE else RoomScope(placed)
            )
            return _recorded_name(session, key, scope)
        named = _recorded_name(session, key, HouseScope())
        if named is not None:
            return named
        for room in session.rooms:
            named = _recorded_name(session, key, RoomScope(room.id))
            if named is not None:
                return named
        return None

    return name_of


def _recorded_name(
    session: LiveSession, key: str, scope: HouseScope | RoomScope
) -> str | None:
    """The string recorded for `key` at `scope`, or nothing when none is."""
    value = session.setting(key, scope)
    return value if isinstance(value, str) and value else None


def reach_properties(
    pack: str,
    slots: Iterable[str],
    *,
    name_of: Callable[[str], str | None] | None = None,
) -> dict[str, dict[str, object]]:
    """One checkbox per role a pack's behaviours act through, keyed and typed.

    The control the phrase "the user could also set ... that he only want ... the
    current room only gets addressed by lights or thermostat or doors" asks for,
    and the second half of a module's reach beside its scope: the scope decides
    *where* the module acts, and these decide *on what*. A bedtime button that
    should shut the house's doors and dim its lights but leave the thermostats
    alone is one pack with one role unticked, which is a setting and not a
    different manifest.

    Derived rather than declared, because the roles are already facts of the pack:
    each `action_slot` is a role some behaviour writes through, and asking an
    author to restate them as options would be asking for a list that can be
    wrong -- a declared `reach.door_contact` for a pack that acts on no doors is
    a checkbox that silently does nothing. The manifest's own `options` are read
    first (`_declarations`), so a pack that wants to say more about one of these
    -- a different title, a description naming the cost -- can still declare it.

    **The pack's name goes on the description**, through the same suffix a
    declared option's description carries. Two installed modules that both act on
    the room's lights are two checkboxes a person has to tell apart, and "Act on
    light group" with the same sentence under it twice is exactly the pair the
    suffix exists to break (`_attributed`).

    `boolean` and defaulting true, which is what makes this additive: every pack
    installed before this existed reads every role as reached and behaves exactly
    as it did.

    `name_of` is the per-module label override, when the caller has a session to
    read one from (`slot_names`): a module whose person has renamed the role it
    acts through shows that name -- "Act on Reading lamp" beside a checkbox that
    would otherwise say "Act on light group" -- because the name a person gave is
    the name the control about that role should carry. It is *not* lowercased the
    way the derived label is: a name a person typed is written the way they wrote
    it, and `humanize`'s lowering is a property of this fallback rather than of a
    name.
    """
    properties: dict[str, dict[str, object]] = {}
    for slot in sorted(set(slots)):
        named = None if name_of is None else name_of(slot)
        label = named if named is not None else humanize(slot).lower()
        properties[option_key(pack, reach_key(slot))] = {
            "type": "boolean",
            "title": f"Act on {label}",
            "description": _attributed(
                f"Untick to leave {label} alone: the module keeps running "
                "but stops writing to this role",
                pack,
            ),
            "default": True,
        }
    return properties


#: The JSON Schema node each declared option `type` becomes. Spelled out rather
#: than derived from the manifest's `default`, because the declaration says more
#: than a value can: `duration` and `integer` are the same JSON type and
#: different questions, and a `number` option whose default happens to be `2`
#: must still be a number field rather than a whole-number one.
_OPTION_NODES: Mapping[str, Mapping[str, object]] = {
    "boolean": {"type": "boolean"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "string": {"type": "string"},
    "enum": {"type": "string"},
    # A duration is whole seconds, and the floor is what makes the panel's number
    # input refuse a negative one before the engine ever sees it. The ceiling is
    # deliberately absent: an author who wants one declares `maximum`.
    "duration": {"type": "integer", "minimum": 0},
}


def option_properties(
    pack: str, rows: Iterable[Mapping[str, object]]
) -> dict[str, dict[str, object]]:
    """One schema property per option `pack` declares, keyed by its setting key.

    The key is the resolver's own (`engine/behaviours/declared.py`'s
    `option_key`), so the property name a form sends back is the name a value is
    written under and the name a behaviour reads -- one spelling, three uses.

    The declaration is honoured literally: `title` and `description` are the
    author's words rather than a humanised key, `minimum`/`maximum` bound the
    field, `enum` becomes a select, and `unit` is appended to the description
    because a number with no unit beside it is a number a person guesses at. The
    manifest's own headings are what make this a form an author can design rather
    than a table of engine internals.
    """
    properties: dict[str, dict[str, object]] = {}
    for row in rows:
        key = row.get("key")
        kind = row.get("type")
        if not isinstance(key, str) or not isinstance(kind, str):
            continue
        node = dict(_OPTION_NODES.get(kind, {}))
        if not node:
            continue
        node["title"] = _option_title(row, key)
        node["description"] = _option_description(row, pack)
        node["default"] = _option_default(row, kind)
        for bound in ("minimum", "maximum"):
            value = row.get(bound)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                node[bound] = value
        members = row.get("enum")
        if kind == "enum" and isinstance(members, list):
            node["enum"] = list(members)
        properties[option_key(pack, key)] = node
    return properties


def _option_title(row: Mapping[str, object], key: str) -> str:
    """The label: the author's `title`, or the key humanised as a fallback."""
    title = row.get("title")
    return title if isinstance(title, str) and title else humanize(key)


def _attributed(said: str, pack: str) -> str:
    """`said` with the pack that declared it named after it.

    Appended rather than substituted, because the panel shows every room's
    options in one form and two packs may each declare the same thing: a `grace`,
    or a role both of them act on. Without the name a person would be looking at
    two identical labels, and one of them under two different settings.
    """
    return f"{said} -- from the pack {pack}."


def _option_description(row: Mapping[str, object], pack: str) -> str:
    """What the option does, the author's words plus the pack that set it."""
    description = row.get("description")
    said = description.strip() if isinstance(description, str) else ""
    unit = row.get("unit")
    measured = f" (in {unit})" if isinstance(unit, str) and unit else ""
    prefix = f"{said}{measured}" if said else f"Declared by the {pack} module"
    return _attributed(prefix, pack)


def _option_default(row: Mapping[str, object], kind: str) -> object:
    """The declared default, as JSON-shaped data the form can start from.

    The validator guarantees the value is of the declared type, so this does not
    re-check it; the one thing it does is give an `enum` with no default the
    first member, because a select whose initial value is `undefined` renders
    empty and a person cannot tell an unset option from an empty one.
    """
    default = row.get("default")
    if default is None and kind == "enum":
        members = row.get("enum")
        if isinstance(members, list) and members:
            return members[0]
    return default


def _reaches(record: InstalledPack, bound: set[str]) -> bool:
    """Whether a room that binds `bound` is a room this pack was installed into.

    Slot names and not entity ids, because the record's two halves answer
    different questions: `slots` is the pack's declaration (the manifest's
    `requires_slots` and `optional_slots`, recorded at install time) and its
    entities are the ones *the house* happened to bind, collected across every
    room. A room that binds a slot the pack declares is a room the pack reaches,
    whether or not the entity the record happens to name is that room's.

    A pack declaring no slot at all reaches no room: with nothing to bind, there
    is no room it can be said to be installed *into*, and treating it as present
    everywhere would put its options on every room's page.
    """
    return any(slot in bound for slot, _entities in record.slots)


def _node(key: str, default: object, behaviour_id: str) -> dict[str, object] | None:
    """One option's schema node, or `None` when its default has no drawable type.

    The `type` is projected from the declared default and never invented: the
    records a live session holds carry no schema, so the value a unit declares is
    the only statement of the setting's kind that exists. The description names
    the behaviour, because an option key is dotted and a form that showed
    `behaviour.motion_lighting.lux_threshold` raw would be a screen full of
    engine internals.
    """
    kind = _json_type(default)
    if kind is None:
        return None
    node: dict[str, object] = {
        "type": kind,
        "title": humanize(key.rsplit(".", 1)[-1]),
        "description": f"Set by the {behaviour_id} behaviour.",
        "default": _plain(default),
    }
    if kind == "array":
        node["items"] = {"type": "string"}
    return node


def _json_type(value: object) -> str | None:
    """The JSON Schema type a declared default projects to, if it has one.

    `bool` is tested before `int` because Python's `bool` *is* an `int`, and the
    order of the two branches is the whole of the difference between a checkbox
    and a number field. A list is an array only when every member is a string;
    anything else has no type this subset can express, and answering `None` drops
    it from the form rather than emitting a node the renderer cannot draw.
    """
    if isinstance(value, (list, tuple)):
        return "array" if all(isinstance(item, str) for item in value) else None
    for python_type, json_type in _TYPES:
        if isinstance(value, python_type):
            return json_type
    return None


def _plain(value: object) -> object:
    """A default as JSON-shaped data: a tuple becomes a list, everything else stays."""
    return list(value) if isinstance(value, (list, tuple)) else value


def _property(
    schema: Mapping[str, object] | None, key: str
) -> Mapping[str, object] | None:
    """One option's node out of a schema this module produced, or `None`.

    The lookups are guarded rather than asserted because the schema is a
    `Mapping[str, object]`: nothing but this module's own builder ever writes one,
    so a shape that is not what `options` returns means the caller passed a
    schema from somewhere else, and answering "no such option" is the failure a
    caller can act on.
    """
    if schema is None:
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None
    node = cast("Mapping[str, object]", properties).get(key)
    return node if isinstance(node, Mapping) else None


def _rejects(node: Mapping[str, object], value: object) -> str | None:
    """Why `value` is not acceptable for `node`, or `None` when it is.

    `jsonschema` rather than a hand-written type test, for the reason
    `engine/config.py` gives about `bool` being an `int`: the library's type
    checker already knows a boolean is not an integer and not a number, and a
    second implementation of that rule would be one free to disagree with it at
    exactly the value that matters. The errors are sorted the way
    `engine/profiles.py` sorts its own, so the message a caller sees is the first
    one by path and not the first one the validator happened to build.
    """
    validator = jsonschema.Draft202012Validator(cast("dict[str, object]", dict(node)))
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    return errors[0].message if errors else None


def _active(profile: Profile, selected: set[str], house_profile: str | None) -> bool:
    """Whether a profile is in force: a room is on it, or the house is."""
    if profile.kind is ProfileKind.ROOM:
        return profile.name in selected
    return profile.name == house_profile


def _mapping(value: object, name: str) -> Mapping[str, object]:
    """One of a snapshot's own documents, or the empty mapping when it has none.

    A part a house did not have is absent rather than empty, and absent reads as
    empty here: a house that hosted no modules, had nothing installed or had set
    nothing had exactly that, and restoring it is restoring nothing. A value that
    is there and is not an object is a snapshot that was not written by this
    build, and `LiveSessionError` is the type a websocket handler already turns
    into an error code.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise LiveSessionError(f"a taken profile's {name!r} is not an object")
    return cast("Mapping[str, object]", value)


def _nested(value: object, name: str) -> Mapping[str, Mapping[str, object]]:
    """`_mapping` one level deeper: room ids to that room's recorded values."""
    return {
        str(room): _mapping(values, name)
        for room, values in _mapping(value, name).items()
    }


def humanize(name: str) -> str:
    """`quiet_timeout_seconds` -> `Quiet timeout seconds`.

    The same shape `schema-spec.ts`'s `humanizeKey` produces, kept here rather
    than invented per caller: a profile or an option whose only name is a
    snake-case identifier needs *some* display name, and the panel's own
    derivation is the one a person has already seen on every other field.
    """
    spaced = name.replace("_", " ").replace("-", " ").strip()
    if not spaced:
        return name
    return spaced[0].upper() + spaced[1:]
