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

What a pack declares, today, is its behaviours: the manifest's `behaviours`
clause is the only clause a pack has, and each declared behaviour's tunables are
the pack's *settings*. `InstalledPack.behaviours` is that clause as the engine
recorded it, and the unit registered under each name declares its own defaults
(`engine/behaviours/base.py`, `behaviour_defaults`), which is exactly the "high
level" a person configures: the quiet timeout, the lux threshold. A pack that
registered no unit -- a behaviour id this build does not implement, which is
every pack whose behaviours are not one of the shipped three -- contributes
nothing, which is the honest answer and not a failure: there is no declaration
to build a field from.

No pack in this checkout declares an options schema, and the `pack-manifest`
schema has no clause for one. That is a fact about the repository rather than a
gap in this module: the machinery below reads whatever a pack's declared
behaviours expose and builds the schema from that, so the day a pack carries a
behaviour this build registers, its tunables appear on the room's settings page
with no change here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import jsonschema

from engine.binding import RoomScope
from engine.install import InstalledPack
from engine.profiles import Profile, ProfileError, ProfileKind

from .live import LiveSession, LiveSessionError

__all__ = [
    "activate",
    "active_profiles",
    "options",
    "profiles",
    "set_option",
]

#: The title the built schema carries. The panel renders a field's label from
#: the node's own `title` and only falls back to humanising the key, so a schema
#: with no title at all would be a form whose heading is a raw config key.
OPTIONS_TITLE = "Room options"

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
    would be a write the next `options` call forgot. They resolve at the room
    scope through the engine's own resolver, so the answer is the one the
    behaviours will read on the next tick and not a second opinion about it.
    """
    session.require_room(room_id)
    properties = _declarations(session, room_id)
    if not properties:
        return (None, {})
    schema: dict[str, object] = {
        "type": "object",
        "title": OPTIONS_TITLE,
        "properties": properties,
    }
    scope = RoomScope(room_id)
    values = {
        key: session.engine.settings.resolve_or(key, scope, node["default"]).value
        for key, node in properties.items()
    }
    return (schema, values)


def set_option(
    session: LiveSession, *, room_id: str, key: str, value: object
) -> tuple[Mapping[str, object] | None, Mapping[str, object]]:
    """Set one option for a room, or refuse it naming what is wrong.

    The write is an *override* at the room scope, which is the top of the
    resolver's stack and the layer `engine/config.py` reserves for exactly this:
    a setting a person changed that outranks the built-in default and the room's
    own layer without editing either. It is engine state rather than wiring, so
    this does not rebuild -- the next tick reads the new value through the same
    resolver the tick after it will.

    The validation is against the schema this module just produced and not
    against a second table of what is legal: a value the schema rejects is a
    value the panel could not have drawn, so refusing it here is refusing the
    caller that talked past the form. Both refusals name the key, because a
    message that named only the value would leave a reader hunting a form with a
    dozen fields.
    """
    session.require_room(room_id)
    schema, _values = options(session, room_id=room_id)
    node = _property(schema, key)
    if node is None:
        raise LiveSessionError(f"there is no option {key!r} in the room {room_id!r}")
    rejection = _rejects(node, value)
    if rejection is not None:
        raise LiveSessionError(
            f"the value {value!r} is not accepted for the option {key!r}: {rejection}"
        )
    session.engine.settings.set_override(key, RoomScope(room_id), value)
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
            "label": _humanize(profile.name),
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
    session.rebuild()
    for name in session.profiles.modes():
        if name in session.engine.modes.declared:
            session.engine.modes.activate(name)


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
    """
    bound = set(session.engine.house.room(room_id).bindings)
    installed = session.engine.installed
    units = session.engine.behaviours
    declared: dict[str, dict[str, object]] = {}
    for pack in installed.names:
        record = installed.packs[pack]
        if not _reaches(record, bound):
            continue
        for behaviour_id in record.behaviours:
            unit = units.get(behaviour_id)
            if unit is None:
                continue
            for key, default in unit.defaults.items():
                node = _node(key, default, behaviour_id)
                if node is not None:
                    declared.setdefault(key, node)
    return {key: declared[key] for key in sorted(declared)}


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
        "title": _humanize(key.rsplit(".", 1)[-1]),
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


def _humanize(name: str) -> str:
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
