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
    ProfileActivator,
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
