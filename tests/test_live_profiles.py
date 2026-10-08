"""Room options and profiles over a live session.

Every test here is about the same property: the operation must be a projection
of what the *engine* holds and not a copy kept beside it. So the assertions read
back through `session.engine` -- a resolved setting's layer and value, the mode
set, the house's bindings -- rather than only through the mapping the operation
returned, because a module that answered the right dictionary and wrote nothing
would pass a check on its own return value.

The shapes are checked against `panel/src/api/models.ts` by field *name*, since
those names are the contract the websocket layer is written against and a
renamed key is a screen that renders nothing rather than a test that fails.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engine.behaviours.declared import option_key, slot_rule_key
from engine.binding import RoomScope
from engine.config import Layer
from engine.install import InstalledPack, InstalledSet
from engine.profiles import ProfileKind, ProfileSet, load_profile_schema
from engine.solar import Location
from ha_adapter.composition import LiveRoom, mode_name, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.live_profiles import (
    activate,
    activate_house,
    active_profiles,
    capture,
    deactivate_house,
    export_document,
    import_document,
    options,
    profiles,
    remember,
    set_option,
)
from ha_adapter.module_records import ModuleRecord, Variant, from_documents
from ha_adapter.testing import FakeHaTransport

ROOT = Path(__file__).resolve().parents[1]

LOCATION = Location(latitude=51.5, longitude=-0.1, time_zone="Europe/London")

#: The unit the test pack declares, and the tunables it declares. Named here
#: rather than spelled at each call site so a renamed behaviour id fails at
#: import instead of as a form that quietly lost its fields.
MOTION = "motion_lighting"
QUIET = "behaviour.motion_lighting.quiet_timeout_seconds"
LUX = "behaviour.motion_lighting.lux_threshold"
SUN = "behaviour.motion_lighting.sun_elevation_threshold"

#: A tunable of a unit the test pack does *not* declare: the check that the
#: schema is built from the installed packs and not from every registered unit.
OVERRIDE_KEY = "behaviour.override.duration_seconds"

#: The keywords `panel/src/components/schema-spec.ts` declares. A schema that
#: carried anything else -- a `$ref`, a script, a keyword the renderer has never
#: heard of -- would draw as `unsupported`, which is the "no pack-supplied JS"
#: requirement read from the other side.
PANEL_SUBSET = frozenset(
    {
        "type",
        "title",
        "description",
        "properties",
        "required",
        "items",
        "enum",
        "default",
        "format",
        "minimum",
        "maximum",
        "additionalProperties",
    }
)

#: The field names `ProfileRef` declares in `models.ts`.
PROFILE_REF_FIELDS = frozenset(
    {"name", "label", "description", "kind", "axis", "active"}
)


def _transport() -> FakeHaTransport:
    transport = FakeHaTransport()
    transport.set_state("binary_sensor.hall_motion", "off")
    transport.set_state("sensor.hall_lux", "12")
    transport.set_state("light.hall", "off")
    return transport


def _room(name: str = "hall") -> LiveRoom:
    return LiveRoom(
        id=room_id(name),
        name=name.title(),
        type="hallway",
        bindings={
            "motion_sensor": "binary_sensor.hall_motion",
            "ambient_light_sensor": "sensor.hall_lux",
            "light_group": "light.hall",
        },
    )


def _pack(
    name: str = "motion_pack",
    *,
    behaviours: tuple[str, ...] = (MOTION,),
    slots: tuple[str, ...] = ("light_group",),
) -> InstalledPack:
    """One installed pack, as `engine/install.py` records one.

    The record and not a manifest: a session holds an `InstalledSet` and never a
    document, so this is the only form of a pack `live_profiles` can read and the
    only one a test should hand it.
    """
    return InstalledPack(
        name=name,
        version="1.0.0",
        digest=f"sha256:{name}",
        slots=tuple((slot, ("light.hall",)) for slot in slots),
        behaviours=behaviours,
    )


def _session(
    *,
    installed: InstalledSet | None = None,
    profiles_: ProfileSet | None = None,
    rooms: tuple[LiveRoom, ...] | None = None,
) -> LiveSession:
    return LiveSession.build(
        house_name="Test House",
        rooms=rooms if rooms is not None else (_room(),),
        modes=("Home", "Away"),
        transport=_transport(),
        root=ROOT,
        location=LOCATION,
        installed=installed,
        profiles=profiles_,
    )


def _profile_set(documents: tuple[dict[str, object], ...]) -> ProfileSet:
    return ProfileSet(list(documents), schema=load_profile_schema(ROOT))


def _room_profile(name: str, axis: str, **deltas: object) -> dict[str, object]:
    return {
        "name": name,
        "kind": "room",
        "axis": axis,
        "description": f"{name} profile",
        "deltas": dict(deltas),
    }


def _house_profile(name: str, **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "kind": "house",
        "description": f"{name} house profile",
        "selections": {},
        **extra,
    }


def _installed_with_motion() -> InstalledSet:
    return InstalledSet({_pack().name: _pack()})


# --------------------------------------------------------------------------
# The schema is built from the installed packs, and nothing else
# --------------------------------------------------------------------------


def test_a_room_with_nothing_installed_has_no_schema_and_no_values() -> None:
    """The "nothing installed" case: a null schema, not an empty form."""
    schema, values = options(_session(), room_id="hall")
    assert schema is None
    assert values == {}


def test_a_pack_that_does_not_reach_the_room_declares_nothing() -> None:
    """A pack installed for another room's slots exposes nothing here."""
    elsewhere = InstalledSet(
        {_pack(slots=("fan_switch",)).name: _pack(slots=("fan_switch",))}
    )
    schema, values = options(_session(installed=elsewhere), room_id="hall")
    assert schema is None
    assert values == {}


def test_the_schema_holds_the_tunables_the_installed_pack_declares() -> None:
    schema, values = options(
        _session(installed=_installed_with_motion()), room_id="hall"
    )

    assert schema is not None
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {QUIET, LUX, SUN}
    # The pack declares `motion_lighting` and not `override`, so the override
    # unit's tunable must be absent: a schema built from the registry rather than
    # from the installed packs would carry it and this is what catches that.
    assert OVERRIDE_KEY not in schema["properties"]
    assert values[QUIET] == 300.0


def test_an_option_node_is_typed_from_the_value_the_unit_declares() -> None:
    schema, _values = options(
        _session(installed=_installed_with_motion()), room_id="hall"
    )
    assert schema is not None
    node = schema["properties"][QUIET]
    assert node["type"] == "number"
    assert node["default"] == 300.0
    assert node["title"] == "Quiet timeout seconds"


def test_the_schema_carries_only_keywords_the_panel_can_render() -> None:
    """ "Rendered from pack schemas with no pack-supplied JS" is a subset check."""
    schema, _values = options(
        _session(installed=_installed_with_motion()), room_id="hall"
    )
    assert schema is not None
    # The names `RoomDetail.options_schema` is drawn from, and nothing else.
    assert set(schema) == {"type", "title", "properties"}
    assert set(schema) <= PANEL_SUBSET
    for node in schema["properties"].values():
        assert set(node) == {"type", "title", "description", "default"}
        assert set(node) <= PANEL_SUBSET


def test_a_room_with_nothing_bound_has_no_schema() -> None:
    """An installed pack whose slots this room binds none of exposes nothing."""
    bare = LiveRoom(id=room_id("hall"), name="Hall", type="hallway", bindings={})
    schema, values = options(
        _session(installed=_installed_with_motion(), rooms=(bare,)), room_id="hall"
    )
    assert schema is None
    assert values == {}


def test_options_are_refused_for_a_room_the_house_does_not_hold() -> None:
    with pytest.raises(LiveSessionError, match="no room 'kitchen'"):
        options(_session(), room_id="kitchen")


# --------------------------------------------------------------------------
# set_option writes through the engine's resolver
# --------------------------------------------------------------------------


def test_setting_an_option_writes_the_override_layer_and_reads_back() -> None:
    session = _session(installed=_installed_with_motion())
    before = session.engine
    assert session.engine.seconds(QUIET, RoomScope("hall")) == 300.0

    schema, values = set_option(session, room_id="hall", key=QUIET, value=90.0)

    resolved = session.engine.settings.resolve(QUIET, RoomScope("hall"))
    assert resolved.value == 90.0
    assert resolved.layer is Layer.OVERRIDE
    # Through the engine's own reader, which is what the next tick consults.
    assert session.engine.seconds(QUIET, RoomScope("hall")) == 90.0
    # An override is engine state and not wiring: a rebuild here would be a
    # session that forgot every other override when one was set.
    assert session.engine is before
    assert schema is not None
    assert values[QUIET] == 90.0


def test_an_unknown_option_is_refused_by_name() -> None:
    session = _session(installed=_installed_with_motion())
    with pytest.raises(
        LiveSessionError, match=re.escape("no option 'behaviour.motion_lighting.nope'")
    ):
        set_option(
            session, room_id="hall", key="behaviour.motion_lighting.nope", value=1
        )


def test_an_option_is_refused_when_nothing_declares_it() -> None:
    """The "nothing installed" case for the write half, not only the read."""
    with pytest.raises(LiveSessionError, match="no option"):
        set_option(_session(), room_id="hall", key=QUIET, value=90.0)


def test_a_value_the_schema_rejects_is_refused_naming_the_key() -> None:
    session = _session(installed=_installed_with_motion())
    with pytest.raises(
        LiveSessionError, match=f"is not accepted for the option {QUIET!r}"
    ):
        set_option(session, room_id="hall", key=QUIET, value="ninety")
    # A boolean is not a number, which is `engine/config.py`'s own rule and not a
    # second one written here.
    with pytest.raises(LiveSessionError, match="not accepted"):
        set_option(session, room_id="hall", key=QUIET, value=True)
    assert session.engine.settings.resolve(QUIET, RoomScope("hall")).value == 300.0


def test_an_option_is_refused_for_a_room_the_house_does_not_hold() -> None:
    with pytest.raises(LiveSessionError, match="no room 'kitchen'"):
        set_option(_session(), room_id="kitchen", key=QUIET, value=90.0)


# --------------------------------------------------------------------------
# profiles, active_profiles and activate
# --------------------------------------------------------------------------


def test_a_house_with_no_profiles_lists_none() -> None:
    assert profiles(_session()) == ()
    assert active_profiles(_session()) == {}


def test_profiles_are_the_panels_profile_refs_by_field_name() -> None:
    session = _session(
        profiles_=_profile_set(
            (_room_profile("evening", "lighting"), _house_profile("vacation"))
        )
    )
    rows = profiles(session)

    assert [row["name"] for row in rows] == ["evening", "vacation"]
    for row in rows:
        assert set(row) == PROFILE_REF_FIELDS
    evening = rows[0]
    assert evening["label"] == "Evening"
    assert evening["kind"] == "room"
    assert evening["axis"] == "lighting"
    assert evening["active"] is False
    assert rows[1]["kind"] == "house"
    assert rows[1]["axis"] is None


def test_a_selected_profile_reads_as_active() -> None:
    session = _session(
        profiles_=_profile_set(
            (_room_profile("evening", "lighting"), _house_profile("vacation"))
        )
    )
    activate(session, room_id="hall", axis="lighting", profile="evening")

    by_name = {row["name"]: row for row in profiles(session)}
    assert by_name["evening"]["active"] is True
    assert by_name["vacation"]["active"] is False
    assert active_profiles(session) == {"lighting": "evening"}


def test_activating_a_profile_reaches_the_engines_resolver() -> None:
    """A selection the engine does not carry is a panel that lies."""
    session = _session(
        profiles_=_profile_set((_room_profile("evening", "lighting", **{QUIET: 60.0}),))
    )
    assert (
        session.engine.settings.resolve(QUIET, RoomScope("hall")).layer is Layer.BUILTIN
    )

    activate(session, room_id="hall", axis="lighting", profile="evening")

    resolved = session.engine.settings.resolve(QUIET, RoomScope("hall"))
    assert resolved.value == 60.0
    assert resolved.layer is Layer.PROFILE
    assert session.profiles.selection("hall") == {"lighting": "evening"}


def test_activating_a_house_profile_is_refused_in_a_room() -> None:
    session = _session(profiles_=_profile_set((_house_profile("vacation"),)))
    with pytest.raises(LiveSessionError, match="is a house profile"):
        activate(session, room_id="hall", axis="lighting", profile="vacation")


def test_activating_a_profile_on_the_wrong_axis_is_refused() -> None:
    session = _session(profiles_=_profile_set((_room_profile("evening", "lighting"),)))
    with pytest.raises(
        LiveSessionError, match="is on the axis 'lighting', not 'climate'"
    ):
        activate(session, room_id="hall", axis="climate", profile="evening")


def test_activating_an_unknown_profile_is_refused_by_name() -> None:
    session = _session(profiles_=_profile_set((_room_profile("evening", "lighting"),)))
    with pytest.raises(LiveSessionError, match="'night' is not held by this set"):
        activate(session, room_id="hall", axis="lighting", profile="night")


def test_activating_a_profile_for_an_unknown_room_is_refused() -> None:
    session = _session(profiles_=_profile_set((_room_profile("evening", "lighting"),)))
    with pytest.raises(LiveSessionError, match="no room 'kitchen'"):
        activate(session, room_id="kitchen", axis="lighting", profile="evening")


# --------------------------------------------------------------------------
# activate_house and deactivate_house: the house's own switch
# --------------------------------------------------------------------------


def test_a_house_profile_is_selected_for_every_room_it_bundles() -> None:
    """The bundle is applied room by room, and the engine sees each one.

    A house profile is the one profile kind that is not selected *in* a room, so
    the chain worth pinning is the whole one: the activation reaches each room's
    selection, the selection reaches the engine's resolver (a bundled delta that
    never resolved would be a house profile that reads as applied and changes
    nothing), and the house profile reads back as the one in force.
    """
    session = _session(
        profiles_=_profile_set(
            (
                _room_profile("evening", "lighting", **{QUIET: 60.0}),
                _house_profile(
                    "vacation", selections={"hall": {"lighting": "evening"}}
                ),
            )
        )
    )

    activate_house(session, profile="vacation")

    assert session.profiles.selection("hall") == {"lighting": "evening"}
    resolved = session.engine.settings.resolve(QUIET, RoomScope("hall"))
    assert resolved.value == 60.0
    assert resolved.layer is Layer.PROFILE
    assert session.profiles.house_profile == "vacation"


def test_a_house_profiles_modes_are_activated_on_the_rebuilt_engine() -> None:
    """A mode a profile carries is activated on the engine the rebuild produced.

    The house profile here bundles no room at all, so the *only* thing this
    activation can put into force is the mode -- which makes the mode itself the
    assertion rather than a coincidence of some room's selection. The document
    names the mode the way the engine knows it (`mode_name`), because the
    integration's display labels are projected once at the outer edge and a
    profile is already inside it.
    """
    session = _session(
        profiles_=_profile_set((_house_profile("vacation", modes=["away"]),))
    )
    assert session.engine.modes.active == frozenset()

    activate_house(session, profile="vacation")

    assert mode_name("Away") in session.engine.modes.active


def test_activating_a_house_profile_is_refused_for_a_room_profile() -> None:
    session = _session(profiles_=_profile_set((_room_profile("evening", "lighting"),)))
    with pytest.raises(LiveSessionError, match="is a room profile, not a house one"):
        activate_house(session, profile="evening")


def test_activating_an_unknown_house_profile_is_refused_by_name() -> None:
    session = _session(profiles_=_profile_set((_house_profile("vacation"),)))
    with pytest.raises(LiveSessionError, match="'guests' is not held by this set"):
        activate_house(session, profile="guests")


def test_taking_the_house_off_its_profile_leaves_the_selections_alone() -> None:
    """ "Off" is not "put everything back": this house does not remember before.

    The engine's rule and not a second one here, so the assertion is on both
    halves at once -- the house profile is released *and* the room selections it
    set are still in force, down to the resolver's layer.
    """
    session = _session(
        profiles_=_profile_set(
            (
                _room_profile("evening", "lighting", **{QUIET: 60.0}),
                _house_profile(
                    "vacation", selections={"hall": {"lighting": "evening"}}
                ),
            )
        )
    )
    activate_house(session, profile="vacation")

    deactivate_house(session)

    assert session.profiles.house_profile is None
    assert session.profiles.selection("hall") == {"lighting": "evening"}
    assert (
        session.engine.settings.resolve(QUIET, RoomScope("hall")).layer is Layer.PROFILE
    )


# --------------------------------------------------------------------------
# capture: a profile taken *from* the house rather than written for it
# --------------------------------------------------------------------------


def test_capture_names_the_whole_house_and_every_part_of_it() -> None:
    """One name for everything a house is configured to be.

    The parts the session holds are read off it -- the packs installed, the
    rooms and what each answers with, the house's settings and each room's, the
    placements, whether a module is bound at house scope, and which room profile
    each room is on -- and the hosted modules, which the session cannot supply,
    are handed in and carried through unreshaped.
    """
    session = _session(
        installed=_installed_with_motion(),
        profiles_=_profile_set(
            (_room_profile("evening", "lighting", **{QUIET: 60.0}),)
        ),
    )
    activate(session, room_id="hall", axis="lighting", profile="evening")
    session.set_house_settings({SUN: 4.0})
    session.set_room_settings("hall", {LUX: 30.0})
    session.set_house_binding("lock", "lock.front_door")
    session.place_module("motion_pack", "hall")

    taken = capture(
        session,
        name="tuesday",
        description="Tuesday morning.",
        modules=[{"slug": "hall_motion", "settings": {"max_brightness_percent": 60}}],
    )

    assert taken.kind is ProfileKind.HOUSE
    assert taken.snapshot is True
    assert taken.selections == {"hall": {"lighting": "evening"}}
    assert taken.setup["house_settings"] == {SUN: 4.0}
    assert taken.setup["room_settings"] == {"hall": {LUX: 30.0}}
    assert taken.setup["house_bindings"] == {"lock": "lock.front_door"}
    assert taken.setup["module_rooms"] == {"motion_pack": "hall"}
    assert taken.setup["modules"] == [
        {"slug": "hall_motion", "settings": {"max_brightness_percent": 60}}
    ]
    # The rooms travel as the session writes them, so what a restore reads back
    # is the shape `from_state` reads rather than a projection of it.
    rooms = taken.setup["rooms"]
    assert [row["id"] for row in rooms] == [room_id("hall")]  # type: ignore[union-attr]
    assert taken.setup["installed"] != {}
    # Taking is naming and not putting: the house is where it was, and the
    # profile is a thing it *could* be put back on.
    assert session.profiles.house_profile is None


def test_a_house_put_back_on_a_taken_profile_is_set_where_a_person_would_set_it() -> (
    None
):
    """The one thing that makes "taken" different from "written".

    A written profile's deltas resolve *above* the house, so a key it names is a
    key nobody can set while it is on. A taken profile is the house's own
    settings put back, so the proof is the layer and not only the value: the
    resolver answers with `ROOM`, which is the layer a person's own edit would
    have written, and the settings maps read back as the captured ones.
    """
    session = _session(
        profiles_=_profile_set((_room_profile("evening", "lighting", **{QUIET: 60.0}),))
    )
    session.set_house_settings({SUN: 4.0})
    session.set_room_settings("hall", {LUX: 30.0})
    capture(session, name="tuesday", description="Tuesday morning.")

    # The house moves on, and then goes back.
    session.set_house_settings({SUN: 20.0})
    session.set_room_settings("hall", {LUX: 5.0})

    activate_house(session, profile="tuesday")

    assert dict(session.house_settings) == {SUN: 4.0}
    assert session.room_settings_for("hall") == {LUX: 30.0}
    resolved = session.engine.settings.resolve(LUX, RoomScope("hall"))
    assert resolved.value == 30.0
    assert resolved.layer is Layer.ROOM
    assert session.profiles.house_profile == "tuesday"


def test_a_taken_profile_puts_a_room_s_lighting_permission_back() -> None:
    """A room's auto-lighting is a setting, so a taken profile restores it.

    Falsified by the pair that banked it and then dropped it: the snapshot wrote
    each room whole -- `auto_lighting` and all -- and the restore read only the
    settings map and the bindings, so a house put back on its own profile kept
    whatever the switch had since been moved to. "The house as it was", with one
    switch still on a position nobody chose, is the quiet gap a person finds by
    staring at the switch; it comes back through `set_room_auto_lighting`, the
    same door the switch uses, so the restored house is one a person can go on
    setting.
    """
    session = _session()
    session.set_room_auto_lighting(room_id("hall"), on=False)
    capture(session, name="tuesday", description="Tuesday morning.")

    # The switch is moved while the profile is off the house.
    session.set_room_auto_lighting(room_id("hall"), on=True)

    activate_house(session, profile="tuesday")

    restored = {room.id: room for room in session.rooms}[room_id("hall")]
    assert restored.auto_lighting is False


def test_a_taken_profile_does_not_bank_a_room_s_mode() -> None:
    """A mode is a moment, so it is not written into a room's row either.

    The house's own mode is already deliberately left out of a snapshot
    (`capture` says why), and a room's row is that same one house mode -- a
    room's mode select sets the house's, not its own. So the field is dropped
    rather than banked and ignored: a profile that carried a mode it would never
    put back would be one whose contents disagreed with its own restore, which is
    exactly the half-captured state a person cannot see from either end.
    """
    session = _session()
    taken = capture(session, name="tuesday", description="Tuesday morning.")

    rows = taken.setup["rooms"]
    assert isinstance(rows, list)
    assert rows, "the snapshot carries no rooms, so this check reads nothing"
    assert all("mode" not in row for row in rows)


def test_a_slot_rule_is_captured_and_put_back_with_the_room_it_was_set_in() -> None:
    """**A rule is a room setting, so a profile that drops it is a silent un-ruling.**

    A slot rule is recorded per module per slot in the room's own layer
    (`ha_adapter.live_modules.set_slot_rule`, whose own write is proved in
    `tests/test_module_slot_override.py`), and restoring a house profile is
    *putting the house back* -- everything done since the profile was taken goes,
    including settings maps that are written whole (`_snapshot` reads
    `LiveSession.to_state`, which carries the layers as they are). So a rule a
    profile did not carry is a rule a profile switch quietly takes away, and the
    slot it was deciding silently back on the room's binding. That is the failure
    this asserts against, in both directions.

    Recorded through `remember_setting` rather than through `set_slot_rule`
    because this is a test about the *profile* layer and not about the writer: the
    writer reads the module's own reach, and the only pack this file installs
    carries one of the engine's built-in behaviours, which has no declared `slots`
    for that read to walk.
    """
    session = _session(installed=_installed_with_motion())
    session.place_module("motion_pack", "hall")
    _record_rule(session, "hall", kind="template", value="{{ 'light.hall' }}")

    capture(session, name="tuesday", description="Tuesday morning.")

    # The house moves on: the rule is taken back off.
    _clear_rule(session, "hall")
    assert _rule_kind(session, "hall") is None

    activate_house(session, profile="tuesday")

    assert _rule_kind(session, "hall") == "template"
    assert _rule_value(session, "hall") == "{{ 'light.hall' }}"

    # And a rule set *after* the profile was taken is put away by restoring it,
    # because restoring is putting the house back rather than adding what it
    # never had (`test_putting_a_house_back_takes_off_what_it_did_not_have`).
    _clear_rule(session, "hall")
    _record_rule(session, "hall", kind="template", value="{{ 'light.study' }}")
    activate_house(session, profile="tuesday")
    assert _rule_value(session, "hall") == "{{ 'light.hall' }}"


def _rule_fact(room: str, fact: str) -> str:
    return option_key("motion_pack", slot_rule_key("light_group", fact))


def _record_rule(session: LiveSession, room: str, *, kind: str, value: object) -> None:
    """A rule written the way `set_slot_rule` writes one: two keys, one scope."""
    scope = RoomScope(room)
    session.remember_setting(_rule_fact(room, "kind"), scope, kind)
    session.remember_setting(_rule_fact(room, "rule"), scope, value)


def _clear_rule(session: LiveSession, room: str) -> None:
    """...and cleared the way it clears one: every fact the last rule may have."""
    scope = RoomScope(room)
    for fact in ("kind", "rule", "when"):
        session.forget_setting(_rule_fact(room, fact), scope)


def _rule_kind(session: LiveSession, room: str) -> object:
    return session.setting(_rule_fact(room, "kind"), RoomScope(room))


def _rule_value(session: LiveSession, room: str) -> object:
    return session.setting(_rule_fact(room, "rule"), RoomScope(room))


def test_a_written_house_profiles_deltas_stay_deltas() -> None:
    """The other kind is untouched by any of this: a delta is an instruction.

    Restoring is what a *snapshot* does, so a house profile with deltas and no
    settings must write nothing into the house's own layer -- otherwise every
    hand-written profile would silently become a setting its author can no
    longer edit.
    """
    session = _session(
        profiles_=_profile_set((_house_profile("vacation", deltas={QUIET: 90.0}),))
    )

    activate_house(session, profile="vacation")

    assert dict(session.house_settings) == {}
    resolved = session.engine.settings.resolve(QUIET, RoomScope("hall"))
    assert resolved.value == 90.0
    assert resolved.layer is Layer.PROFILE


def test_putting_a_house_back_takes_off_what_it_did_not_have() -> None:
    """Restoring is putting the house back, not adding what is missing.

    Everything done since the profile was taken has to go, or the state somebody
    took the profile in order to leave would still be half in force: a pack
    installed since, a module placed since, a slot bound since, a room setting
    since. The snapshot here holds none of them.
    """
    session = _session(
        installed=_installed_with_motion(),
        profiles_=_profile_set((_house_profile("tuesday", setup={}),)),
    )
    session.set_house_settings({SUN: 20.0})
    session.set_room_settings("hall", {LUX: 5.0})
    session.set_house_binding("lock", "lock.front_door")
    session.place_module("motion_pack", "hall")

    activate_house(session, profile="tuesday")

    assert dict(session.house_settings) == {}
    assert session.room_settings_for("hall") == {}
    assert dict(session.house_bindings) == {}
    assert dict(session.module_rooms) == {}
    assert dict(session.installed.packs) == {}


def test_a_snapshot_puts_back_the_packs_the_rooms_and_the_placements() -> None:
    """The half a house is *made of*: what is installed, and where it was put."""
    taken = _session(installed=_installed_with_motion())
    taken.place_module("motion_pack", "hall")
    taken.set_house_binding("lock", "lock.front_door")
    profile = capture(taken, name="tuesday", description="Tuesday morning.")

    session = _session(profiles_=_profile_set((profile.to_document(),)))

    activate_house(session, profile="tuesday")

    assert sorted(session.installed.packs) == ["motion_pack"]
    assert dict(session.module_rooms) == {"motion_pack": "hall"}
    assert dict(session.house_bindings) == {"lock": "lock.front_door"}


def test_a_split_slot_and_a_house_binding_on_a_part_are_put_back() -> None:
    """**A part is a vocabulary word, so its binding needs the record restored first.**

    A snapshot carries the house's bindings, and one of them may be a *part* of a
    split slot -- the global device for a half of a role. Restoring that binding
    against the session's current record is what the two orders decide: with the
    parts record still a version behind, `set_house_binding` refuses a part the
    vocabulary does not carry, and the refusal lands *partway through* the
    restore, after the packs and settings have already been put back. So the parts
    come back before the bindings, and this asserts the whole restore lands.
    """
    session = _session()
    session.set_slot_parts({"light_group": ("a", "b")})
    session.set_house_binding("light_group__a", "light.lamp")
    capture(session, name="tuesday", description="Tuesday morning.")

    # The house moves on: the part comes off. The binding must be taken off first,
    # because a record cannot drop a part something still names (`part_bound_in`),
    # which leaves exactly the state a restore has to survive.
    session.set_house_binding("light_group__a", None)
    session.set_slot_parts({})
    assert dict(session.slot_parts) == {}
    assert dict(session.house_bindings) == {}

    activate_house(session, profile="tuesday")

    assert dict(session.slot_parts) == {"light_group": ("a", "b")}
    assert dict(session.house_bindings) == {"light_group__a": "light.lamp"}
    # The part is a vocabulary word the restore grew, so the engine carries it too.
    assert "light_group__a" in session.engine.house.vocabulary.slots


def test_a_snapshot_that_predates_parts_does_not_take_them_off_the_house() -> None:
    """**An absent `slot_parts` is not an empty one.**

    Every capture this build makes writes the key, empty or not, because a split
    slot is a question this build knows how to ask -- so a snapshot with no
    `slot_parts` at all is one written before the house could split a slot, the
    same "predates the field" tolerance `_room_rows` shows for a profile that
    names no rooms. Read through the lenient reader, which answers `{}` for a
    missing key because a *store* whose parts have gone really has none, it took
    every part off the house for the offence of switching to an older profile --
    and nothing said so. Every module sitting on a part then falls back to the
    slot's whole device (`slot_parts_of` reports a part the house does not carry
    as no part at all), so half a room's lights move in place of the half that
    was asked for.

    The second half is what keeps the tolerance narrow: a snapshot that *does*
    carry the key is a statement about parts, and an empty one means the house
    had none.

    The bindings are left out of the claims here because they behave as
    documented and not as the parts do: a restore is putting the house back and
    not merging into it, so a snapshot naming no bindings takes the house's off,
    and the part this test's binding named goes with them. What the fix buys is
    that the *record* stays, so a module on a part is still on that part.
    """
    session = _session(
        profiles_=_profile_set(
            (
                _house_profile(
                    "monday", setup={"house_settings": {SUN: 4.0}, "modules": []}
                ),
            )
        )
    )
    session.set_slot_parts({"light_group": ("a", "b")})

    activate_house(session, profile="monday")

    assert dict(session.slot_parts) == {"light_group": ("a", "b")}
    # The part is a vocabulary word the restore kept, so the engine carries it.
    assert "light_group__a" in session.engine.house.vocabulary.slots

    # A snapshot that speaks about parts still puts the whole record back: an
    # empty `slot_parts` is the house saying it had none.
    splitting = _session(
        profiles_=_profile_set(
            (
                _house_profile(
                    "tuesday",
                    setup={"house_settings": {SUN: 4.0}, "slot_parts": {}},
                ),
            )
        )
    )
    splitting.set_slot_parts({"light_group": ("a", "b")})

    activate_house(splitting, profile="tuesday")

    assert dict(splitting.slot_parts) == {}
    assert "light_group__a" not in splitting.engine.house.vocabulary.slots


def test_a_snapshot_of_a_room_this_house_no_longer_has_is_skipped() -> None:
    """A room the house has since removed is a room it cannot be set back to.

    `set_room_settings` refuses a room that is not here -- silently recording a
    value no reader could reach -- so the restore skips it rather than failing
    the whole activation on a room that has been knocked through into the
    kitchen.
    """
    session = _session(
        profiles_=_profile_set(
            (
                _house_profile(
                    "tuesday",
                    setup={
                        "house_settings": {SUN: 4.0},
                        "room_settings": {"attic": {LUX: 30.0}, "hall": {LUX: 8.0}},
                    },
                ),
            )
        )
    )

    activate_house(session, profile="tuesday")

    assert dict(session.house_settings) == {SUN: 4.0}
    assert session.room_settings_for("hall") == {LUX: 8.0}
    assert session.room_settings_for("attic") == {}


def test_a_taken_module_keeps_the_configuration_it_was_on() -> None:
    """A hosted module travels whole, down to which of its answers are in force.

    A module can hold several configurations and be switched between them, and
    the one it is *on* is the module's own flat answers while `variants` keeps
    the rest. A snapshot that carried the name and not those answers would put
    the module back on whatever configuration it was on when the profile came
    off, so somebody who set it to Evening, took a profile and then set it to
    Morning would get Morning back.

    The row survives a file's worth of packaging -- a snapshot is stored,
    exported and read back -- so this reads it through `from_documents`, the one
    door a restore reads it by, rather than off the record that wrote it.
    """
    session = _session()
    taken = capture(
        session,
        name="tuesday",
        description="Tuesday morning.",
        modules=[
            ModuleRecord(
                slug="hall_motion",
                title="Hall motion",
                source="{}",
                bindings={"brightness": {"kind": "value", "value": 40}},
                settings=("brightness",),
                variant="Morning",
                variants={
                    "Evening": Variant(
                        bindings={"brightness": {"kind": "value", "value": 10}},
                        settings=("dim_level",),
                    )
                },
            ).as_json()
        ],
    )

    document = json.loads(json.dumps(taken.to_document()))
    rows = document["setup"]["modules"]
    (record,) = from_documents(rows)

    assert record.variant == "Morning"
    assert record.settings == ("brightness",)
    assert record.bindings == {"brightness": {"kind": "value", "value": 40}}
    # The configuration it was *not* on is still there to switch back to, which
    # is the half a snapshot holding only the answers in force would drop.
    assert set(record.configurations) == {"Morning", "Evening"}
    assert record.held_configurations["Evening"].settings == ("dim_level",)


def test_a_taken_profile_learns_an_edit_made_while_the_house_is_on_it() -> None:
    """The edit lands in the profile as well as in the house.

    Which is the difference between a profile a house lives on and a photograph
    of one it keeps being dragged back to: a setting changed while the house is
    on a taken profile is the house it now is, so switching away and back brings
    *this* house back. The proof is the round trip, not the capture: what is
    asserted is what the house reads as after leaving the profile and returning.
    """
    session = _session()
    session.set_house_settings({SUN: 4.0})
    capture(session, name="tuesday", description="Tuesday morning.")
    activate_house(session, profile="tuesday")

    session.set_house_settings({SUN: 20.0})
    session.set_room_settings("hall", {LUX: 5.0})
    learned = remember(session, modules=[])

    assert learned is not None
    assert learned.setup["house_settings"] == {SUN: 20.0}
    assert learned.setup["room_settings"] == {"hall": {LUX: 5.0}}

    session.set_house_settings({SUN: 1.0})
    session.set_room_settings("hall", {LUX: 99.0})
    activate_house(session, profile="tuesday")

    assert dict(session.house_settings) == {SUN: 20.0}
    assert session.room_settings_for("hall") == {LUX: 5.0}


def test_a_written_profiles_deltas_are_left_alone() -> None:
    """A hand-written profile is an instruction, and an instruction is not learned.

    Its deltas resolve *above* the house, so writing the house's own settings
    into it would be turning "keep the house quieter than it says" into a value
    the author can no longer edit -- and the deltas it does carry stay exactly
    where they were.
    """
    session = _session(
        profiles_=_profile_set((_house_profile("vacation", deltas={QUIET: 90.0}),))
    )
    activate_house(session, profile="vacation")
    session.set_house_settings({SUN: 4.0})

    assert remember(session, modules=[]) is None
    held = session.profiles.profile("vacation")
    assert held.setup == {}
    assert held.deltas == {QUIET: 90.0}


def test_a_house_on_no_profile_has_nothing_to_tell() -> None:
    """The ordinary case, and the one that must not cost anything."""
    session = _session()
    session.set_house_settings({SUN: 4.0})

    assert remember(session, modules=[]) is None


def test_what_a_profile_learns_carries_the_modules_as_they_now_are() -> None:
    """A module is the one part the session cannot supply, so it is handed in.

    And it is handed in *as it now is*: a module's own settings can be what a
    person changed, so the rows a caller passes are the ones the profile ends up
    holding rather than the ones it was taken with.
    """
    session = _session()
    capture(session, name="tuesday", description="Tuesday morning.")
    activate_house(session, profile="tuesday")

    learned = remember(
        session,
        modules=[{"slug": "hall_motion", "variant": "Evening"}],
    )

    assert learned is not None
    assert learned.setup["modules"] == [{"slug": "hall_motion", "variant": "Evening"}]


def test_taking_a_name_the_house_already_holds_is_refused_as_a_session_error() -> None:
    """A refusal crosses the seam as the session's one failure type.

    `live.py` fixes `LiveSessionError` as the thing a websocket handler turns
    into an error code, so the `ProfileError` underneath must not be the one a
    caller has to catch; the message is kept verbatim so the code's detail still
    names what clashed.
    """
    session = _session(profiles_=_profile_set((_house_profile("tuesday"),)))
    with pytest.raises(LiveSessionError, match="'tuesday' is declared twice"):
        capture(session, name="tuesday", description="Tuesday morning.")


# --------------------------------------------------------------------------
# export_document and import_document: the file
# --------------------------------------------------------------------------


def test_exporting_one_profile_is_that_profiles_own_document() -> None:
    """No envelope: one profile out is one profile document, byte for byte.

    The document is the frozen `schemas/profile/` shape and not a wrapper around
    it, so it must be identical to what the profile itself writes -- a wrapper
    would be a second definition of a profile document, and the two would drift.
    """
    held = _profile_set(
        (
            _room_profile("evening", "lighting", **{QUIET: 60.0}),
            _house_profile("vacation"),
        )
    )
    session = _session(profiles_=held)

    document = export_document(session, profile="evening")

    assert document == held.profile("evening").to_document()
    assert "profiles" not in document


def test_exporting_every_profile_carries_the_profiles_and_nothing_house_specific() -> (
    None
):
    """The set form drops `selections` and `house_profile` on purpose.

    A profile names settings and is portable; a selection names *this house's*
    rooms. A file carrying them would half-apply in a house whose rooms are named
    differently -- which is the failure `ProfileSet.export_document` refuses by
    not writing the fields at all, so their absence is the assertion.
    """
    session = _session(
        profiles_=_profile_set(
            (_room_profile("evening", "lighting"), _house_profile("vacation"))
        )
    )

    document = export_document(session)

    assert set(document) == {"profiles"}
    assert [row["name"] for row in document["profiles"]] == ["evening", "vacation"]


def test_exporting_an_unknown_profile_is_refused_by_name() -> None:
    session = _session(profiles_=_profile_set((_room_profile("evening", "lighting"),)))
    with pytest.raises(LiveSessionError, match="'night' is not held by this set"):
        export_document(session, profile="night")


def test_an_exported_set_imports_into_a_house_that_holds_nothing() -> None:
    """The round trip the feature exists for: out of one house, into another."""
    source = _session(
        profiles_=_profile_set(
            (
                _room_profile("evening", "lighting", **{QUIET: 60.0}),
                _house_profile("vacation"),
            )
        )
    )
    target = _session()

    answer = import_document(target, document=export_document(source))

    assert answer["imported"] == ["evening", "vacation"]
    assert answer["replaced"] == []
    assert [row["name"] for row in answer["profiles"]] == ["evening", "vacation"]
    assert target.profiles.profile("evening").deltas == {QUIET: 60.0}


def test_a_single_profile_document_imports_as_one_profile() -> None:
    """The other accepted form: a file that is one profile, not a set of them."""
    session = _session()

    answer = import_document(
        session, document=_room_profile("evening", "lighting", **{QUIET: 60.0})
    )

    assert answer["imported"] == ["evening"]
    assert answer["profiles"][0]["axis"] == "lighting"


def test_importing_refuses_every_name_the_house_already_holds() -> None:
    """The refusal names all of them, not the first: one rerun fixes the file.

    A message that stopped at the first conflict would turn a file with three
    clashes into three refusals and three round trips.
    """
    session = _session(
        profiles_=_profile_set(
            (_room_profile("evening", "lighting"), _room_profile("night", "lighting"))
        )
    )

    with pytest.raises(
        LiveSessionError, match="already holds 'evening', 'night'"
    ) as refusal:
        import_document(
            session,
            document={
                "profiles": [
                    _room_profile("evening", "lighting"),
                    _room_profile("night", "lighting"),
                ]
            },
        )
    assert "replace" in str(refusal.value)


def test_importing_with_replace_reports_which_ones_it_overwrote() -> None:
    session = _session(
        profiles_=_profile_set(
            (_room_profile("evening", "lighting", **{QUIET: 300.0}),)
        )
    )

    answer = import_document(
        session,
        document={
            "profiles": [
                _room_profile("evening", "lighting", **{QUIET: 60.0}),
                _room_profile("night", "lighting"),
            ]
        },
        replace=True,
    )

    assert answer["imported"] == ["night"]
    assert answer["replaced"] == ["evening"]
    assert session.profiles.profile("evening").deltas == {QUIET: 60.0}


def test_replacing_a_profile_takes_the_selection_that_named_it_off() -> None:
    """A replaced profile is a *different* profile that reuses the name.

    The axis, the deltas and the kind may all have changed, so a room left
    pointing at the name would be on a profile whose meaning moved underneath it
    with nothing on the screen saying so. `ProfileSet.remove` clears the
    selection, and the assertion is on the engine -- the resolver has to fall
    back off the profile layer, or the clearing never happened.
    """
    session = _session(
        profiles_=_profile_set((_room_profile("evening", "lighting", **{QUIET: 60.0}),))
    )
    activate(session, room_id="hall", axis="lighting", profile="evening")
    assert (
        session.engine.settings.resolve(QUIET, RoomScope("hall")).layer is Layer.PROFILE
    )

    import_document(
        session,
        document=_room_profile("evening", "lighting", **{QUIET: 900.0}),
        replace=True,
    )

    assert session.profiles.selection("hall") == {}
    resolved = session.engine.settings.resolve(QUIET, RoomScope("hall"))
    assert resolved.layer is Layer.BUILTIN


def test_a_refused_import_leaves_the_set_exactly_as_it_was() -> None:
    """The two-pass property: one bad profile refuses the whole file.

    The one-pass version has an outcome that is both "the import was refused" and
    "the house now holds half of that file", which is the state a person meets
    once and never trusts again. So the good profile of this pair must be absent
    afterwards -- and the assertion names it, because a check on the *count* would
    pass for a set that had swapped one profile for another.
    """
    session = _session(profiles_=_profile_set((_room_profile("night", "lighting"),)))

    with pytest.raises(LiveSessionError, match="axis"):
        import_document(
            session,
            document={
                "profiles": [
                    _room_profile("evening", "lighting"),
                    # A room profile with no axis: the schema's own refusal.
                    {"name": "broken", "kind": "room", "description": "no axis"},
                ]
            },
        )

    assert sorted(session.profiles.profiles) == ["night"]


def test_a_document_that_names_one_profile_twice_is_refused() -> None:
    """Two profiles, one name: which of them the house ends up with is a race."""
    session = _session()

    with pytest.raises(LiveSessionError, match="names 'evening' more than once"):
        import_document(
            session,
            document={
                "profiles": [
                    _room_profile("evening", "lighting", **{QUIET: 60.0}),
                    _room_profile("evening", "climate"),
                ]
            },
        )
    assert session.profiles.profiles == {}


def test_a_document_whose_profiles_is_not_a_list_is_refused() -> None:
    session = _session()
    with pytest.raises(LiveSessionError, match="'profiles' is not a list"):
        import_document(session, document={"profiles": {"name": "evening"}})


def test_a_profile_in_the_set_that_is_not_an_object_is_refused() -> None:
    session = _session()
    with pytest.raises(LiveSessionError, match="is not an object"):
        import_document(session, document={"profiles": ["evening"]})


def test_importing_nothing_changes_nothing() -> None:
    """An empty set file is a no-op, not a rebuild: nothing was added to see."""
    session = _session(profiles_=_profile_set((_room_profile("night", "lighting"),)))

    answer = import_document(session, document={"profiles": []})

    assert answer == {
        "imported": [],
        "replaced": [],
        "profiles": list(profiles(session)),
    }


# --------------------------------------------------------------------------
# The roles a module addresses
# --------------------------------------------------------------------------


def _declared_installed() -> InstalledSet:
    """The committed example pack, as an installed record names it.

    The real published manifest rather than a fixture: `installed_units` reads
    the pack's text through `registry/index.json`, so a hand-written dictionary
    here would test a document no live house ever builds a unit from. Its one
    behaviour declares `slots: [motion_sensor, light_group]`, and the action slot
    -- the last -- is what the reach controls are derived from.
    """
    record = InstalledPack(
        name="example_pack",
        version="1.0.0",
        digest="sha256:example",
        slots=(
            ("light_group", ("light.hall",)),
            ("motion_sensor", ("binary_sensor.hall_motion",)),
        ),
        # The unit id, not the manifest's bare behaviour name: a declared unit is
        # keyed by the pack and the name together (`behaviour_id`), which is why
        # the record's `behaviours` and the engine's registry are one spelling.
        behaviours=("example_pack.motion_turns_on_light",),
    )
    return InstalledSet({record.name: record})


def test_a_declared_packs_action_role_is_a_checkbox_in_the_rooms_options() -> None:
    """The chain from a manifest's `slots` to the field a person unticks.

    Three links have to hold at once and each has its own way of failing
    silently: the unit has to be built from the *published* manifest (a registry
    root that cannot load the pack contributes nothing, and the symptom is an
    empty form), the action slot has to be the one the services write through
    (the last declared slot, so `light_group` and not `motion_sensor`), and the
    key has to be the one the engine resolves under, because a checkbox whose
    name the behaviour never reads is a control that changes nothing.
    """
    schema, values = options(_session(installed=_declared_installed()), room_id="hall")

    assert schema is not None
    key = "module.example_pack.reach.light_group"
    node = schema["properties"][key]
    assert node["type"] == "boolean"
    assert node["default"] is True
    assert node["title"] == "Act on light group"
    assert values[key] is True
    # The watched slot is not a role the module addresses, so it draws nothing:
    # a control per *slot* rather than per action slot would offer a checkbox that
    # no behaviour reads.
    assert "module.example_pack.reach.motion_sensor" not in schema["properties"]


def test_unticking_a_role_writes_the_room_override_the_engine_reads() -> None:
    """The field is a setting, not an announcement: the value resolves back.

    `set_option` is the same one write every other field on the page makes, so
    the assertion is on the *layer* -- the override, which outranks the pack's
    default and the room's own -- because a checkbox that appeared to save and
    wrote nothing would read back as `True` and look correct.
    """
    session = _session(installed=_declared_installed())
    key = "module.example_pack.reach.light_group"

    _schema, values = set_option(session, room_id="hall", key=key, value=False)

    assert values[key] is False
    resolved = session.engine.settings.resolve(key, RoomScope("hall"))
    assert resolved.value is False
    assert resolved.layer is Layer.OVERRIDE
