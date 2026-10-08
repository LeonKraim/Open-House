"""Profiles: the five layers, simultaneous axes, house bundles, activation rules.

Phase 3's first three requirements. The checks are grouped the way the phase is:
the layered resolver the profiles fill, the simultaneous axes and house bundles
that make a profile more than a preset, and the activation rules with the three
brakes that stop them flapping.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.binding import HouseScope, RoomScope
from engine.config import LAYER_ORDER, ConfigResolver, Layer, ResolvedSetting
from engine.profiles import (
    ActivationRule,
    BlockedReason,
    InvalidProfileError,
    Profile,
    ProfileActivator,
    ProfileKind,
    ProfileSet,
    RuleKind,
    UnknownProfileError,
    load_profile_schema,
)
from engine.vocabulary import Vocabulary
from openhouse.facade import open_session

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
KEY = "engine.presence.quiet_timeout_seconds"


@pytest.fixture(scope="module")
def schema() -> Mapping[str, object]:
    return load_profile_schema(ROOT)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    return Vocabulary.load(ROOT)


def _room(name: str, axis: str, **deltas: object) -> dict[str, object]:
    return {
        "name": name,
        "kind": "room",
        "axis": axis,
        "description": f"{name} profile",
        "deltas": dict(deltas),
    }


def _house(name: str, selections: Mapping[str, Mapping[str, str]]) -> dict[str, object]:
    return {
        "name": name,
        "kind": "house",
        "description": f"{name} house profile",
        "selections": {room: dict(axes) for room, axes in selections.items()},
    }


# --------------------------------------------------------------------------
# The five layers
# --------------------------------------------------------------------------


def test_the_declared_layer_order_is_the_phases_order() -> None:
    assert [str(layer) for layer in LAYER_ORDER] == [
        "builtin",
        "house",
        "room",
        "profile",
        "override",
    ]


def test_a_room_profile_delta_outranks_the_room_and_the_house() -> None:
    """The profile layer sits above the room, which is what 'delta over the base' means."""
    resolver = ConfigResolver(
        builtin={KEY: "builtin"},
        house={KEY: "house"},
        rooms={"foyer": {KEY: "room"}},
        profile={KEY: "house-profile"},
        profile_rooms={"foyer": {KEY: "room-profile"}},
    )
    resolved = resolver.resolve(KEY, RoomScope("foyer"))
    assert resolved.value == "room-profile"
    assert resolved.layer is Layer.PROFILE


def test_the_house_scope_reads_the_house_global_profile() -> None:
    resolver = ConfigResolver(
        house={KEY: "house"},
        rooms={"foyer": {KEY: "room"}},
        profile={KEY: "house-profile"},
        profile_rooms={"foyer": {KEY: "room-profile"}},
    )
    assert resolver.resolve(KEY, HouseScope()).value == "house-profile"
    # A room with no room-profile of its own falls back to the house-global one.
    assert resolver.resolve(KEY, RoomScope("kitchen")).value == "house-profile"


def test_a_temporary_override_outranks_the_profile_layer() -> None:
    resolver = ConfigResolver(
        profile={KEY: "house-profile"},
        profile_rooms={"foyer": {KEY: "room-profile"}},
    )
    resolver.set_override(KEY, RoomScope("foyer"), "override")
    resolved = resolver.resolve(KEY, RoomScope("foyer"))
    assert resolved.value == "override"
    assert resolved.layer is Layer.OVERRIDE


# --------------------------------------------------------------------------
# The set: axes and house bundles
# --------------------------------------------------------------------------


def _set(schema: Mapping[str, object], *documents: Mapping[str, object]) -> ProfileSet:
    return ProfileSet(list(documents), schema=schema)


def test_two_axes_are_active_at_once(schema: Mapping[str, object]) -> None:
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _room("warm", "climate", **{"engine.rate_limit.bound": 5}),
    )
    profiles.select("foyer", "lighting", "bright")
    profiles.select("foyer", "climate", "warm")
    merged = profiles.effective_rooms()["foyer"]
    assert merged == {KEY: 120.0, "engine.rate_limit.bound": 5}


def test_a_second_profile_on_one_axis_replaces_the_first(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(
        schema,
        _room("dim", "lighting", **{KEY: 10.0}),
        _room("bright", "lighting", **{KEY: 120.0}),
    )
    profiles.select("foyer", "lighting", "dim")
    profiles.select("foyer", "lighting", "bright")
    assert profiles.effective_rooms()["foyer"] == {KEY: 120.0}


def test_selecting_a_profile_on_the_wrong_axis_is_refused(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(schema, _room("bright", "lighting"))
    with pytest.raises(UnknownProfileError):
        profiles.select("foyer", "climate", "bright")


def test_a_room_cannot_be_put_on_a_house_profile(schema: Mapping[str, object]) -> None:
    profiles = _set(schema, _house("vacation", {"foyer": {"lighting": "bright"}}))
    with pytest.raises(UnknownProfileError):
        profiles.select("foyer", "lighting", "vacation")


def test_a_house_profile_bundles_room_selections(schema: Mapping[str, object]) -> None:
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 300.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")
    assert profiles.house_profile == "vacation"
    assert profiles.selection("foyer") == {"lighting": "bright"}
    assert profiles.effective_rooms()["foyer"] == {KEY: 300.0}


def test_a_house_profile_that_names_no_axis_leaves_it_alone(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(
        schema,
        _room("bright", "lighting"),
        _room("warm", "climate"),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.select("foyer", "climate", "warm")
    profiles.activate_house_profile("vacation")
    assert profiles.selection("foyer") == {"lighting": "bright", "climate": "warm"}


def test_a_house_profiles_delta_applies_at_house_scope(
    schema: Mapping[str, object],
) -> None:
    document = _house("guests", {})
    document["deltas"] = {KEY: 900.0}
    document["enabled_behaviours"] = ["motion_lighting"]
    profiles = _set(schema, document)
    profiles.activate_house_profile("guests")
    assert profiles.effective_house() == {
        KEY: 900.0,
        "behaviour.motion_lighting.enabled": True,
    }


def test_a_profile_enabling_a_behaviour_feeds_the_enable_key(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(schema)
    profiles.add(
        {
            "name": "evening",
            "kind": "room",
            "axis": "lighting",
            "description": "evening",
            "enabled_behaviours": ["away_shutdown"],
        }
    )
    profiles.select("foyer", "lighting", "evening")
    assert profiles.enabled_rooms()["foyer"] == frozenset({"away_shutdown"})


def test_a_room_profile_may_not_carry_selections(schema: Mapping[str, object]) -> None:
    with pytest.raises(InvalidProfileError):
        ProfileSet(
            [
                {
                    "name": "bad",
                    "kind": "room",
                    "axis": "lighting",
                    "description": "bad",
                    "selections": {"foyer": {"lighting": "bad"}},
                }
            ],
            schema=schema,
        )


def test_a_duplicate_profile_name_is_refused(schema: Mapping[str, object]) -> None:
    with pytest.raises(UnknownProfileError, match="declared twice"):
        _set(schema, _room("bright", "lighting"), _room("bright", "climate"))


def test_the_revision_survives_a_document_round_trip(
    schema: Mapping[str, object],
) -> None:
    """**The number a page is judged against has to outlive a rebuild.**

    A set is rebuilt far more often than it is moved: every room binding and
    every hosted-module write is a configuration-subentry update, and Home
    Assistant reloads the whole entry for one. The set the reload builds is read
    back out of the store, so a revision that is not written down starts again
    from zero -- and a page holding the number it rendered from is then refused
    for good, because a counter climbing from zero never returns to a number it
    has lost. The page does not recover until somebody reloads it by hand.

    A document from a version that did not write the number reads as a set that
    has never moved, which is what `revision` answers when it is absent rather
    than zero-by-accident.
    """
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")
    assert profiles.revision == 1

    rebuilt = ProfileSet.from_document(profiles.to_document(), schema=schema)
    assert rebuilt.revision == 1
    # And the round trip is a round trip: moving on from the resumed number
    # counts from where the house was rather than from where the rebuild started.
    rebuilt.deactivate_house_profile()
    assert rebuilt.revision == 2

    written = profiles.to_document()
    del written["revision"]
    assert ProfileSet.from_document(written, schema=schema).revision == 0


def test_the_set_round_trips_through_its_document(schema: Mapping[str, object]) -> None:
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")
    rebuilt = ProfileSet.from_document(profiles.to_document(), schema=schema)
    assert rebuilt.to_document() == profiles.to_document()


# --------------------------------------------------------------------------
# Removing a profile, and the two shapes an export takes
# --------------------------------------------------------------------------


def test_removing_a_profile_takes_every_selection_that_named_it(
    schema: Mapping[str, object],
) -> None:
    """A selection names a profile, so the name leaving takes the selection.

    Left behind, the selection would be a room pointing at a name the set no
    longer holds -- which is a `KeyError` waiting in `effective_rooms` rather than
    an answer, so the removal goes through `clear` and the maps stay consistent.
    """
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _room("warm", "climate", **{"engine.rate_limit.bound": 5}),
    )
    profiles.select("foyer", "lighting", "bright")
    profiles.select("foyer", "climate", "warm")

    profiles.remove("bright")

    assert "bright" not in profiles.profiles
    assert profiles.selection("foyer") == {"climate": "warm"}
    assert profiles.effective_rooms()["foyer"] == {"engine.rate_limit.bound": 5}


def test_removing_the_house_profile_in_force_releases_the_house(
    schema: Mapping[str, object],
) -> None:
    """The house profile and the room selections are two facts, not one.

    The bundle it applied stays -- `deactivate_house_profile`'s rule, met from the
    other door -- but the house can no longer be *on* a profile the set does not
    hold, because that is what `house_profile` is read as elsewhere.
    """
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")

    profiles.remove("vacation")

    assert profiles.house_profile is None
    assert profiles.selection("foyer") == {"lighting": "bright"}
    assert profiles.effective_rooms()["foyer"] == {KEY: 120.0}


def test_removing_a_profile_the_set_does_not_hold_is_refused(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(schema, _room("bright", "lighting"))
    with pytest.raises(UnknownProfileError, match="'night' is not held by this set"):
        profiles.remove("night")


def test_exporting_one_profile_writes_that_profile_document(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    assert (
        profiles.export_document("bright") == profiles.profile("bright").to_document()
    )
    assert profiles.export_document("vacation") == _house(
        "vacation", {"foyer": {"lighting": "bright"}}
    )


def test_exporting_the_set_writes_the_profiles_and_not_the_selections(
    schema: Mapping[str, object],
) -> None:
    """The one place the two export shapes differ, and the reason for it.

    A profile is portable and a selection is a fact about this house's rooms, so
    the set form carries the profiles only. `to_document` is the *state* document
    and does carry them; the two are different documents for different jobs, and
    a set form that quietly included the state would be a file that half-applies
    in a house whose rooms are named differently. The revision is state too, for
    the reason `to_document` gives: it is what a page is judged against, and it
    has to outlive the entry reload rather than travel in a file.
    """
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")

    document = profiles.export_document()

    assert set(document) == {"profiles"}
    assert [row["name"] for row in document["profiles"]] == ["bright", "vacation"]
    assert set(profiles.to_document()) == {
        "profiles",
        "selections",
        "house_profile",
        "revision",
    }


def test_an_exported_set_rebuilds_into_a_set_that_holds_the_same_profiles(
    schema: Mapping[str, object],
) -> None:
    """The imported half of the round trip: a document in, the same set out.

    `selections` is the field the export deliberately drops and the rebuild
    deliberately defaults, so the whole starting set has to be rebuilt from that
    same empty starting point for the two documents to be comparable at all.
    """
    source = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    rebuilt = ProfileSet(list(source.export_document()["profiles"]), schema=schema)
    assert rebuilt.export_document() == source.export_document()


# --------------------------------------------------------------------------
# The facade applies the layers
# --------------------------------------------------------------------------


def _inline_house() -> dict[str, object]:
    return {
        "name": "a house",
        "rooms": [
            {
                "id": "foyer",
                "name": "Foyer",
                "type": "foyer",
                "bindings": {
                    "light_group": {"entity_id": "light.foyer"},
                    "motion_sensor": {"entity_id": "binary_sensor.foyer_motion"},
                },
            }
        ],
        "house_scope": {"slots": ["light_group"]},
    }


def test_a_selected_profile_changes_what_the_engine_resolves(
    vocabulary: Vocabulary,
) -> None:
    session = open_session(house=_inline_house(), vocabulary=vocabulary)
    session.add_profiles([_room("quiet", "lighting", **{KEY: 42.0})])
    before = session.engine.resolve(KEY, RoomScope("foyer"))
    session.select_profile(room_id="foyer", axis="lighting", name="quiet")
    after = session.engine.resolve(KEY, RoomScope("foyer"))
    assert before.value != 42.0
    assert after == ResolvedSetting(key=KEY, value=42.0, layer=Layer.PROFILE)


# --------------------------------------------------------------------------
# Activation rules
# --------------------------------------------------------------------------


def _activator(
    schema: Mapping[str, object],
    *rules: ActivationRule,
    **profiles: str,
) -> ProfileActivator:
    documents = [_room(name, "lighting") for name in profiles.values()]
    return ProfileActivator(_set(schema, *documents), list(rules))


def test_a_schedule_rule_fires_inside_its_window(schema: Mapping[str, object]) -> None:
    rule = ActivationRule(
        name="morning",
        room_id="foyer",
        axis="lighting",
        profile="bright",
        kind=RuleKind.SCHEDULE,
        at=time(7, 0),
        until=time(9, 0),
        days=frozenset({0, 1, 2, 3, 4}),
    )
    activator = _activator(schema, rule, bright="bright")
    monday = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # a Monday
    result = activator.step(monday)
    assert [a.after for a in result.activations] == ["bright"]
    # Outside the window it wants nothing, so nothing changes.
    assert activator.step(datetime(2026, 1, 5, 10, 0, tzinfo=UTC)).activations == ()


def test_a_house_mode_rule_fires_on_its_mode(schema: Mapping[str, object]) -> None:
    rule = ActivationRule(
        name="guest",
        room_id="foyer",
        axis="lighting",
        profile="guest",
        kind=RuleKind.HOUSE_MODE,
        mode="guest",
    )
    activator = _activator(schema, rule, guest="guest")
    now = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    assert activator.step(now).activations == ()
    result = activator.step(now, active_modes=["guest"])
    assert [a.after for a in result.activations] == ["guest"]


def test_a_trigger_rule_fires_on_an_entity_state(schema: Mapping[str, object]) -> None:
    rule = ActivationRule(
        name="door",
        room_id="foyer",
        axis="lighting",
        profile="welcome",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.front_door",
        state="on",
    )
    activator = _activator(schema, rule, welcome="welcome")
    now = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    assert (
        activator.step(now, states={"binary_sensor.front_door": "off"}).activations
        == ()
    )
    result = activator.step(now, states={"binary_sensor.front_door": "on"})
    assert [a.after for a in result.activations] == ["welcome"]


def test_a_manual_rule_never_auto_fires(schema: Mapping[str, object]) -> None:
    rule = ActivationRule(
        name="manual",
        room_id="foyer",
        axis="lighting",
        profile="chosen",
        kind=RuleKind.MANUAL,
    )
    activator = _activator(schema, rule, chosen="chosen")
    assert activator.step(datetime(2026, 1, 5, 8, 0, tzinfo=UTC)).activations == ()


def test_hysteresis_delays_a_rule_until_its_condition_has_held(
    schema: Mapping[str, object],
) -> None:
    rule = ActivationRule(
        name="slow",
        room_id="foyer",
        axis="lighting",
        profile="bright",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.x",
        state="on",
        hysteresis=timedelta(minutes=10),
    )
    activator = _activator(schema, rule, bright="bright")
    start = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    states = {"binary_sensor.x": "on"}
    assert activator.step(start, states=states).activations == ()
    assert activator.step(start + timedelta(minutes=5), states=states).activations == ()
    result = activator.step(start + timedelta(minutes=10), states=states)
    assert [a.after for a in result.activations] == ["bright"]


def test_min_dwell_blocks_a_second_change_too_soon(
    schema: Mapping[str, object],
) -> None:
    first = ActivationRule(
        name="a",
        room_id="foyer",
        axis="lighting",
        profile="one",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.a",
        state="on",
        min_dwell=timedelta(minutes=30),
    )
    second = ActivationRule(
        name="b",
        room_id="foyer",
        axis="lighting",
        profile="two",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.b",
        state="on",
    )
    activator = _activator(schema, first, second, one="one", two="two")
    start = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    first_result = activator.step(start, states={"binary_sensor.a": "on"})
    assert [a.after for a in first_result.activations] == ["one"]
    second_result = activator.step(
        start + timedelta(minutes=1), states={"binary_sensor.b": "on"}
    )
    assert second_result.activations == ()
    assert second_result.blocked[0].reason is BlockedReason.MIN_DWELL


def test_cycle_detection_refuses_a_flapping_pair(schema: Mapping[str, object]) -> None:
    left = ActivationRule(
        name="a_left",
        room_id="foyer",
        axis="lighting",
        profile="left",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.left",
        state="on",
    )
    right = ActivationRule(
        name="b_right",
        room_id="foyer",
        axis="lighting",
        profile="right",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.right",
        state="on",
    )
    activator = _activator(schema, left, right, left="left", right="right")
    base = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
    outcomes = []
    for index in range(7):
        states = (
            {"binary_sensor.left": "on"}
            if index % 2 == 0
            else {"binary_sensor.right": "on"}
        )
        outcomes.append(activator.step(base + timedelta(minutes=index), states=states))
    # Six switches settle the pattern; the seventh would take one profile a third
    # time inside the window, which is the flap the brake exists to refuse.
    assert [len(o.activations) for o in outcomes] == [1, 1, 1, 1, 1, 1, 0]
    assert outcomes[-1].blocked[0].reason is BlockedReason.CYCLE


def test_a_change_that_matches_the_current_selection_is_not_a_change(
    schema: Mapping[str, object],
) -> None:
    rule = ActivationRule(
        name="a",
        room_id="foyer",
        axis="lighting",
        profile="one",
        kind=RuleKind.TRIGGER,
        entity_id="binary_sensor.a",
        state="on",
    )
    profiles = _set(schema, _room("one", "lighting"))
    profiles.select("foyer", "lighting", "one")
    activator = ProfileActivator(profiles, [rule])
    result = activator.step(
        datetime(2026, 1, 5, 8, 0, tzinfo=UTC), states={"binary_sensor.a": "on"}
    )
    assert result.activations == ()


def test_the_activator_steps_through_the_facade(vocabulary: Vocabulary) -> None:
    session = open_session(house=_inline_house(), vocabulary=vocabulary)
    session.add_profiles([_room("bright", "lighting", **{KEY: 7.0})])
    session.set_profile_rules(
        [
            ActivationRule(
                name="always",
                room_id="foyer",
                axis="lighting",
                profile="bright",
                kind=RuleKind.TRIGGER,
                entity_id="binary_sensor.foyer_motion",
                state="off",
            )
        ]
    )
    session.set_state("binary_sensor.foyer_motion", "off")
    result = session.run_profile_rules()
    assert [a.after for a in result.activations] == ["bright"]
    assert session.engine.resolve(KEY, RoomScope("foyer")).layer is Layer.PROFILE


# --------------------------------------------------------------------------
# Taking a profile from a house rather than writing one for it
# --------------------------------------------------------------------------


def test_capturing_builds_a_house_profile_the_schema_accepts(
    schema: Mapping[str, object],
) -> None:
    """`capture` is the second way a profile comes to exist, and `add` is the door.

    What it produces is held by the set that made it, which is only possible if
    the document it built validated: `ProfileSet.capture` writes the same
    document `add` would have been handed, so a capture that produced something
    the schema refused would fail here rather than at the next load.
    """
    profiles = _set(schema, _room("bright", "lighting", **{KEY: 120.0}))
    profiles.select("foyer", "lighting", "bright")
    taken = profiles.capture(
        name="last_tuesday",
        description="What this house was on and set to.",
        selections=profiles.selections(),
        setup={
            "installed": {"packs": [{"name": "motion_pack"}]},
            "rooms": [{"id": "foyer", "name": "Foyer", "bindings": {}}],
            "house_settings": {"engine.solar.sun_elevation_threshold": 3.0},
            "room_settings": {"foyer": {KEY: 45.0}},
            "house_bindings": {"lock": "lock.front_door"},
            "module_rooms": {"motion_pack": "foyer"},
            "modules": [{"slug": "foyer_motion", "settings": {"max": 60}}],
        },
    )
    assert taken.kind is ProfileKind.HOUSE
    assert taken.snapshot is True
    assert taken.selections == {"foyer": {"lighting": "bright"}}
    assert taken.setup["house_settings"] == {
        "engine.solar.sun_elevation_threshold": 3.0
    }
    assert taken.setup["room_settings"] == {"foyer": {KEY: 45.0}}
    assert taken.setup["modules"] == [{"slug": "foyer_motion", "settings": {"max": 60}}]
    assert profiles.profile("last_tuesday") is taken


def test_a_snapshot_is_read_from_the_field_being_there_and_not_from_it_being_full(
    schema: Mapping[str, object],
) -> None:
    """A profile taken from a house that had set nothing is a snapshot of nothing.

    The one capture that matters most is the house before anybody touched it, and
    that document carries an empty `setup`. Read by truthiness it would load back
    as a hand-written profile -- one whose (empty) deltas resolve above the house
    for as long as it is in force -- so the presence of the field is what says
    which of the two a profile is.
    """
    empty = Profile.from_document(
        {
            "name": "fresh",
            "kind": "house",
            "description": "Nothing set.",
            "selections": {},
            "setup": {},
        },
        schema=schema,
        index=0,
    )
    assert empty.snapshot is True


def test_a_taken_profile_round_trips_through_its_document(
    schema: Mapping[str, object],
) -> None:
    profiles = _set(schema, _room("bright", "lighting", **{KEY: 120.0}))
    profiles.select("foyer", "lighting", "bright")
    profiles.capture(
        name="evening",
        description="What this house was on and set to.",
        selections=profiles.selections(),
        setup={"room_settings": {"foyer": {KEY: 30.0}}, "modules": []},
    )
    rebuilt = ProfileSet.from_document(profiles.to_document(), schema=schema)
    assert rebuilt.to_document() == profiles.to_document()
    assert rebuilt.profile("evening").snapshot is True
    assert rebuilt.profile("evening").setup == {
        "room_settings": {"foyer": {KEY: 30.0}},
        "modules": [],
    }


def test_renaming_a_profile_takes_everything_that_named_it_with_it(
    schema: Mapping[str, object],
) -> None:
    """A rename is the profile moving, not a second profile beside it.

    The three things that name a profile are the key it is held under, the room
    selections that point at it and the house profile in force; a rename that
    moved only the first would leave two of them naming a profile the set no
    longer holds -- which is the `KeyError` `remove`'s docstring describes
    arriving by another road.
    """
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")

    profiles.rename("bright", "sunny")
    profiles.rename("vacation", "holiday")

    assert sorted(profiles.profiles) == ["holiday", "sunny"]
    assert profiles.selections() == {"foyer": {"lighting": "sunny"}}
    assert profiles.house_profile == "holiday"


def test_a_rename_onto_a_name_already_held_is_refused(
    schema: Mapping[str, object],
) -> None:
    """Two profiles under one name is the state `add` refuses, by either door."""
    profiles = _set(schema, _room("bright", "lighting"), _room("dim", "lighting"))
    with pytest.raises(UnknownProfileError, match="declared twice"):
        profiles.rename("dim", "bright")


def test_a_rename_may_not_take_a_name_the_schema_will_not_have(
    schema: Mapping[str, object],
) -> None:
    """The schema decides what a name may be, here as at every other door."""
    profiles = _set(schema, _room("bright", "lighting"))
    with pytest.raises(InvalidProfileError):
        profiles.rename("bright", "Not A Name")


def test_a_room_profile_may_not_carry_a_snapshot(schema: Mapping[str, object]) -> None:
    """A house is the house kind's, and the schema says so.

    A room profile is a delta on one axis; a room that could carry a whole
    house's `setup` would be a second, contradictory way of saying what a house
    is, applied by a door that reads the house profile and nothing else.
    """
    with pytest.raises(InvalidProfileError):
        _set(
            schema,
            {
                "name": "bright",
                "kind": "room",
                "axis": "lighting",
                "description": "bright",
                "deltas": {},
                "setup": {},
            },
        )


def test_taking_a_name_the_set_already_holds_is_refused(
    schema: Mapping[str, object],
) -> None:
    """One rule for two doors: `capture` adds through `add`, so a clash is `add`'s."""
    profiles = _set(schema, _room("bright", "lighting"))
    with pytest.raises(UnknownProfileError, match="declared twice"):
        profiles.capture(
            name="bright",
            description="What this house was on and set to.",
            selections={},
            setup={},
        )


def test_taking_a_profile_again_moves_only_the_house_it_holds(
    schema: Mapping[str, object],
) -> None:
    """The third door, and the two things `remove`-and-`capture` would get wrong.

    A profile that is taken *again* is the same profile holding a house that has
    moved on -- so the selections that named it stay, and so does the house being
    on it. `remove` followed by `capture` would leave the house with its rooms on
    nothing and its house profile released, which is a house changed by the act
    of learning what it looks like.
    """
    profiles = _set(
        schema,
        _room("bright", "lighting", **{KEY: 120.0}),
        _house("vacation", {"foyer": {"lighting": "bright"}}),
    )
    profiles.activate_house_profile("vacation")
    taken = profiles.capture(
        name="tuesday",
        description="What this house was on and set to.",
        selections=profiles.selections(),
        setup={"room_settings": {"foyer": {KEY: 30.0}}},
    )
    profiles.activate_house_profile("tuesday")

    again = profiles.retake(
        "tuesday",
        selections=profiles.selections(),
        setup={"room_settings": {"foyer": {KEY: 45.0}}},
    )

    assert again.setup == {"room_settings": {"foyer": {KEY: 45.0}}}
    assert again.snapshot is True
    # The name, the kind and the words it was taken with are the profile's own
    # and not the caller's to restate: what a re-take moves is the house.
    assert again.name == "tuesday"
    assert again.kind is ProfileKind.HOUSE
    assert again.description == taken.description
    assert profiles.profile("tuesday") is again
    assert profiles.house_profile == "tuesday"
    assert profiles.selections() == {"foyer": {"lighting": "bright"}}


def test_a_re_take_goes_through_the_schema_like_every_other_door(
    schema: Mapping[str, object],
) -> None:
    """What the house now is has to be a profile document, or it is refused.

    A re-take is the one door where the document is not the caller's to write --
    most of it is the profile already held -- so it is also the door where the
    schema could most easily be skipped and the caller's half believed.
    """
    profiles = _set(schema, _house("vacation", {}))
    with pytest.raises(InvalidProfileError):
        profiles.retake(
            "vacation",
            selections={"foyer": {"lighting": "Not A Name"}},
            setup={},
        )
