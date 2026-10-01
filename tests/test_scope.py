"""Scope-aware slot availability -- task 6.3.

Two files meet in this check and each supplies one half of the rule: the map
says what a room and the house can offer, and the corpus says what each
behaviour requires. A fixture that got only one of them wrong would not show
which half the check reads, so every case below writes both and states the
scope as well as the slots.

The negative cases are written as assertions that the check fires and names the
row and the slot, because a check proven only to stay silent on a valid tree is
a check that has never been seen to reject anything -- and the acceptance gate
runs exactly this shape of fixture expecting failure. The committed tree is
exercised here for the half of the rule that does hold on it: the map's slot
names are all roles the lexicon fixes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from tools.catalog import scope
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

ROOM_TYPES_PATH = "catalog/room_types.yaml"
BEHAVIORS_PATH = "catalog/behaviors.yaml"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _row(**overrides: object) -> dict[str, object]:
    """A minimal row that satisfies every rule, so one override is one defect.

    Only the three fields this check reads are carried: the rest of a row's
    shape is the schema's, and a row that satisfied it here would pretend this
    fixture had been through the validator.
    """
    row: dict[str, object] = {
        "id": "lighting.example",
        "scope": "room",
        "required_slots": [],
    }
    row.update(overrides)
    return row


def _room_types(
    root: Path,
    types: list[tuple[str, list[str]]],
    house: list[str] | None,
) -> None:
    """Write a map of the named types and, optionally, the `house` scope.

    `house=None` omits the scope entirely, which is not the same as an empty
    list: a scope that was never written and one that supplies nothing are the
    two states `RoomTypeMap.has_house` exists to tell apart.
    """
    document: dict[str, object] = {
        "room_types": [
            {
                "name": name,
                "default": False,
                "source_rooms": [],
                "provides_slots": slots,
            }
            for name, slots in types
        ]
    }
    if house is not None:
        document["house"] = {"slots": house}
    write(root, ROOM_TYPES_PATH, yaml.safe_dump(document, sort_keys=False))


def _behaviors(root: Path, rows: list[dict[str, object]]) -> None:
    write(root, BEHAVIORS_PATH, yaml.safe_dump({"behaviors": rows}, sort_keys=False))


def _messages() -> str:
    """Run the check and return its findings as text."""
    report = Report()
    scope.check_scope(report)
    return "\n".join(f"{d.where}: {d.message}" for d in report.diagnostics)


def _diagnostics() -> list[str]:
    report = Report()
    scope.check_scope(report)
    return [d.where for d in report.diagnostics]


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def test_a_room_row_requiring_a_slot_no_room_type_provides_is_named(
    fake_root: Path,
) -> None:
    """A room is instantiated from a type, so its slots must be that type's."""
    _room_types(fake_root, [("bedroom", ["light_group"])], ["house_mode"])
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.example",
                scope="room",
                required_slots=["light_group", "lock"],
            )
        ],
    )

    messages = _messages()
    assert "lighting.example" in messages
    assert "lock" in messages


def test_a_room_row_may_draw_its_slots_from_different_room_types(
    fake_root: Path,
) -> None:
    """Availability is the union: a behaviour may fit any one room's type."""
    _room_types(
        fake_root,
        [("bedroom", ["light_group"]), ("foyer", ["lock"])],
        ["house_mode"],
    )
    _behaviors(
        fake_root,
        [_row(scope="room", required_slots=["light_group", "lock"])],
    )
    assert _messages() == ""


def test_the_room_scope_does_not_reach_the_house_slots(fake_root: Path) -> None:
    """The two supplies are different sets, or the distinction is decorative."""
    _room_types(fake_root, [("bedroom", ["light_group"])], ["house_mode"])
    _behaviors(
        fake_root,
        [_row(id="modes.room_mode", scope="room", required_slots=["house_mode"])],
    )

    messages = _messages()
    assert "modes.room_mode" in messages
    assert "house_mode" in messages


def test_a_house_row_requiring_only_house_slots_validates(fake_root: Path) -> None:
    """The away shutdown the requirement names: house scope, house slots."""
    _room_types(
        fake_root, [("bedroom", ["light_group"])], ["house_mode", "light_group"]
    )
    _behaviors(
        fake_root,
        [
            _row(
                id="lighting.away_shutdown",
                scope="house",
                required_slots=["light_group", "house_mode"],
            )
        ],
    )
    assert _messages() == ""


def test_a_house_row_requiring_a_slot_the_house_scope_lacks_is_named(
    fake_root: Path,
) -> None:
    """A house behaviour is not forced through a room type, but it is bounded."""
    _room_types(
        fake_root, [("bedroom", ["light_group", "media_player"])], ["house_mode"]
    )
    _behaviors(
        fake_root,
        [
            _row(
                id="media.away_media_off",
                scope="house",
                required_slots=["house_mode", "media_player"],
            )
        ],
    )

    messages = _messages()
    assert "media.away_media_off" in messages
    assert "media_player" in messages


def test_the_supply_is_checked_and_not_the_ready_made_corpus(fake_root: Path) -> None:
    """A row that requires nothing is silent whatever the map supplies.

    The negated control for the cases above: it fails for the reason they fail
    (an unfulfillable requirement) and not because the check rejects every row.
    """
    _room_types(fake_root, [("bedroom", ["light_group"])], None)
    _behaviors(fake_root, [_row(scope="room", required_slots=[])])
    assert _messages() == ""


# --------------------------------------------------------------------------
# The map's names
# --------------------------------------------------------------------------


def test_a_room_type_offering_a_slot_outside_the_vocabulary_is_named(
    fake_root: Path,
) -> None:
    _room_types(fake_root, [("bedroom", ["light_group", "frobnicator"])], None)
    messages = _messages()
    assert "bedroom" in messages
    assert "frobnicator" in messages


def test_the_house_scope_offering_a_slot_outside_the_vocabulary_is_named(
    fake_root: Path,
) -> None:
    _room_types(
        fake_root, [("bedroom", ["light_group"])], ["house_mode", "frobnicator"]
    )
    messages = _messages()
    assert "catalog/room_types.yaml:house" in messages
    assert "frobnicator" in messages


def test_a_map_with_no_types_is_reported_once(fake_root: Path) -> None:
    """One absent map is one finding, not one per row it cannot supply."""
    _room_types(fake_root, [], ["house_mode"])
    _behaviors(fake_root, [_row(scope="room", required_slots=["light_group"])])
    assert len(_diagnostics()) == 1


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def test_the_committed_map_offers_only_controlled_vocabulary_slots(
    real_root: Path,
) -> None:
    """The half of the rule the map can be held to on the committed tree.

    The availability half is a join against the corpus and is asserted per case
    above; this one reads the map alone and is the clause `room_types` names as
    belonging here.
    """
    report = Report()
    scope.check_scope(report)
    offenders = [
        diagnostic
        for diagnostic in report.diagnostics
        if diagnostic.where.startswith("catalog/room_types.yaml")
    ]
    assert offenders == [], report.render()


def test_the_committed_tree_satisfies_the_whole_scope_rule(real_root: Path) -> None:
    """Both halves of task 6.3 on the tree the project ships.

    The fixtures above each break one clause and show the check firing; this is
    the other direction, and it is the one a fixture cannot make. Every row's
    `required_slots` must be reachable from the supply its scope names -- a room
    row from the union of the room types' `provides_slots`, a house row from
    `house.slots` -- or the corpus contains a behaviour no house can host, which
    is exactly the artifact this rule exists to keep installable. The map's names
    are checked in the same pass.
    """
    report = Report()
    scope.check_scope(report)
    assert report.ok, report.render()
