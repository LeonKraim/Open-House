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

import re
from pathlib import Path

import pytest

from engine.binding import RoomScope
from engine.config import Layer
from engine.install import InstalledPack, InstalledSet
from engine.profiles import ProfileSet, load_profile_schema
from engine.solar import Location
from ha_adapter.composition import LiveRoom, room_id
from ha_adapter.live import LiveSession, LiveSessionError
from ha_adapter.live_profiles import (
    activate,
    active_profiles,
    options,
    profiles,
    set_option,
)
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


def _house_profile(name: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": "house",
        "description": f"{name} house profile",
        "selections": {},
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
