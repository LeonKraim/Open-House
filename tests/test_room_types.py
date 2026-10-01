"""The room-type map -- section 5, task 5.2.

`catalog/room_types.yaml` makes three claims and each has a clause of its own.
Every type names the source rooms that produced it, so a type cannot float free
of the rooms it is derived from. Every source names a room `rooms.yaml` records,
so the join the corroboration rule reads is real. And every recorded room is a
source of exactly one type, which is what keeps the map a partition rather than
a second list: a room in no type is one a later phase cannot express, and a room
in two is one the union counts twice.

`default` is the fourth claim and the one that needs a join across files. A
`default: true` type says Phase 1 may pre-populate it for a stranger, so its
source rooms must come from at least two repos; a type whose rooms all come from
one repo must stay `default: false`, which is a statement about the *flag* and
not about the type -- the type is still recorded.

A note on how the findings are collected. `room_types.check_room_types` is
registered in `validate._CHECKS`, so its findings do reach the pre-commit hook;
these tests call the check directly rather than routing through `validate_all`
the way `tests/test_golden.py` does, and wrap it in the same `CheckError` catch
the hook uses, so that a finding is attributed to this check alone and a check
that would reach the pre-commit hook as a traceback reaches a test as the
diagnostic it is meant to be instead. The committed-tree tests are the ones that would notice the file
shipping with a defect, so they read the real repository rather than a fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from tools.catalog import paths, room_types, rooms
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

ROOM_TYPES_PATH = "catalog/room_types.yaml"
ROOMS_PATH = "catalog/rooms.yaml"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _rooms_document(entries: list[tuple[str, str]]) -> str:
    """A committed-looking `rooms.yaml`, with only the fields the join reads.

    `rooms.load_rooms` reads a room's `paths` but `room_types` never looks at
    them, so one synthetic path per room keeps the shape honest without building
    evidence no test in this file exercises.
    """
    return yaml.safe_dump(
        {
            "rooms": [
                {"name": name, "repo": repo, "paths": [f"{repo}/{name}/on.yaml"]}
                for name, repo in entries
            ]
        },
        sort_keys=False,
    )


def _room_types_document(
    types: list[dict[str, object]],
    house: bool = True,
    house_slots: tuple[str, ...] = ("house_mode",),
) -> str:
    """A `room_types.yaml` from a terse description of each type.

    `default` is written only when the description supplies one, so a test can
    express "this type carries no `default`" without the builder inventing one.
    """
    document: dict[str, object] = {
        "room_types": [
            {
                "name": entry["name"],
                **({"default": entry["default"]} if "default" in entry else {}),
                "source_rooms": [
                    {"repo": repo, "room": room}
                    for repo, room in entry["sources"]  # type: ignore[union-attr]
                ],
                "provides_slots": entry.get("slots", ["light_group"]),
            }
            for entry in types
        ]
    }
    if house:
        document["house"] = {"slots": list(house_slots)}
    return yaml.safe_dump(document, sort_keys=False)


#: Two repos, three rooms, and a base map that covers all three exactly once. A
#: kitchen is corroborated (two repos); a lounge is not (one room, one repo). A
#: test changes one thing and asserts the one finding it produces.
_ROOMS: list[tuple[str, str]] = [("kitchen", "x"), ("lounge", "x"), ("kitchen", "y")]

_BASE_TYPES: list[dict[str, object]] = [
    {
        "name": "kitchen",
        "default": True,
        "sources": [("x", "kitchen"), ("y", "kitchen")],
    },
    {"name": "lounge", "default": False, "sources": [("x", "lounge")]},
]


def _commit(
    root: Path,
    types: list[dict[str, object]] | None = None,
    room_entries: list[tuple[str, str]] | None = None,
    house: bool = True,
    house_slots: tuple[str, ...] = ("house_mode",),
) -> None:
    write(root, ROOMS_PATH, _rooms_document(room_entries or _ROOMS))
    write(
        root,
        ROOM_TYPES_PATH,
        _room_types_document(
            _BASE_TYPES if types is None else types,
            house=house,
            house_slots=house_slots,
        ),
    )


def _diagnostics() -> list[tuple[str, str]]:
    """The room-type check's findings, collected the way the hook collects them.

    `check_room_types` is registered in `validate._CHECKS`, but this helper calls
    it directly and reproduces the `CheckError` arm here rather than borrowing
    `validate_all`, so the findings are this check's alone. The arm is not
    decoration: the property the
    parse and missing-file tests are about is that a broken file is a
    diagnostic, and without it a `CheckError` would escape as a traceback and the
    test would still pass.
    """
    report = Report()
    try:
        room_types.check_room_types(report)
    except CheckError as exc:
        report.add(exc.check, exc.where, exc.message)
    return [
        (d.where, d.message)
        for d in report.diagnostics
        if d.check == room_types.ROOM_TYPES_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The corroboration rule -- the task's own verify clauses
# --------------------------------------------------------------------------


def test_a_default_type_tracing_to_one_repo_is_named(fake_root: Path) -> None:
    """A `default: true` type says a stranger may be pre-populated with it, so
    its rooms must come from at least two repos. One repo is not corroboration."""
    _commit(
        fake_root,
        types=[
            {"name": "kitchen", "default": True, "sources": [("x", "kitchen")]},
            {"name": "lounge", "default": False, "sources": [("x", "lounge")]},
            {"name": "cook", "default": False, "sources": [("y", "kitchen")]},
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("room_types.yaml:kitchen")
    assert "`default: true`" in message
    assert "`x`" in message


def test_a_single_repo_type_marked_non_default_passes(fake_root: Path) -> None:
    """The union is not truncated by the corroboration rule: a kind one repo has
    is recorded, and saying `default: false` is the whole of its obligation."""
    _commit(
        fake_root,
        types=[
            {
                "name": "kitchen",
                "default": True,
                "sources": [("x", "kitchen"), ("y", "kitchen")],
            },
            {"name": "lounge", "default": False, "sources": [("x", "lounge")]},
        ],
    )

    assert _diagnostics() == [], _messages()


def test_a_default_type_that_traces_to_no_repo_is_named(fake_root: Path) -> None:
    """A `default: true` type with no source repo cannot be corroborated at all;
    the finding names the type and says why the flag is unsupported."""
    _commit(
        fake_root,
        types=[
            *_BASE_TYPES,
            {"name": "garage", "default": True, "sources": []},
        ],
    )

    findings = _diagnostics()
    assert any("traces to no source repo" in message for _, message in findings), (
        _messages()
    )


# --------------------------------------------------------------------------
# The house scope
# --------------------------------------------------------------------------


def test_a_house_scope_with_no_slots_is_named(fake_root: Path) -> None:
    _commit(fake_root, house_slots=())

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "provides no slots" in findings[0][1]


def test_a_missing_house_scope_is_named(fake_root: Path) -> None:
    _commit(fake_root, house=False)

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "defines no `house` scope" in findings[0][1]


# --------------------------------------------------------------------------
# A type against the rooms it names
# --------------------------------------------------------------------------


def test_a_type_that_names_no_source_room_is_named(fake_root: Path) -> None:
    """A type exists because real rooms produced it; one that names none is a
    kind nothing instantiates."""
    _commit(
        fake_root,
        types=[
            *_BASE_TYPES,
            {"name": "garage", "default": False, "sources": []},
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "names no source room" in findings[0][1]


def test_a_source_room_not_recorded_in_rooms_yaml_is_named(fake_root: Path) -> None:
    """The source is a join into `rooms.yaml`; a key that names no room there is
    a type derived from a room that does not exist."""
    _commit(
        fake_root,
        types=[
            *_BASE_TYPES,
            {"name": "garage", "default": False, "sources": [("z", "garage")]},
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "`z/garage`" in findings[0][1]
    assert "no record" in findings[0][1]


def test_a_type_that_provides_no_slots_is_named(fake_root: Path) -> None:
    """A room type exists to say what a room of that kind offers."""
    _commit(
        fake_root,
        types=[
            _BASE_TYPES[0],
            {
                "name": "lounge",
                "default": False,
                "sources": [("x", "lounge")],
                "slots": [],
            },
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "provides no slots" in findings[0][1]


def test_a_type_without_a_default_is_named(fake_root: Path) -> None:
    """An absent flag is a decision nobody recorded, not a `false`."""
    _commit(
        fake_root,
        types=[
            _BASE_TYPES[0],
            {"name": "lounge", "sources": [("x", "lounge")]},
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "carries no boolean `default`" in findings[0][1]


def test_a_type_without_a_name_is_named(fake_root: Path) -> None:
    _commit(
        fake_root,
        types=[
            *_BASE_TYPES,
            {"name": "", "default": False, "sources": []},
        ],
    )

    findings = _diagnostics()
    assert any("carries no `name`" in message for _, message in findings), _messages()


# --------------------------------------------------------------------------
# Coverage: the map partitions the real rooms
# --------------------------------------------------------------------------


def test_a_room_no_type_covers_is_named(fake_root: Path) -> None:
    """The union is not truncated: a room the map cannot express is the failure
    the corroboration rule must not cause."""
    _commit(
        fake_root,
        room_entries=[*_ROOMS, ("garage", "x")],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "`x/garage` is not a source of any room type" in findings[0][1]


def test_a_room_two_types_claim_is_named(fake_root: Path) -> None:
    """A room in two types is counted twice by the union and has no single
    answer to which kind it is."""
    _commit(
        fake_root,
        types=[
            *_BASE_TYPES,
            {"name": "sitting_room", "default": False, "sources": [("x", "lounge")]},
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("room_types.yaml:x/lounge")
    assert "2 types" in message


# --------------------------------------------------------------------------
# A file that cannot be read
# --------------------------------------------------------------------------


def test_a_room_types_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else, so an
    unguarded read would reach it as a traceback."""
    write(fake_root, ROOMS_PATH, _rooms_document(_ROOMS))
    write(fake_root, ROOM_TYPES_PATH, "room_types:\n  - name: kitchen\n   bad indent\n")

    assert "cannot be parsed" in _messages()


def test_a_missing_room_types_file_is_a_diagnostic(fake_root: Path) -> None:
    """Absent is not empty: an empty file would read as "there are no types",
    report nothing, and look like a map that was never written."""
    write(fake_root, ROOMS_PATH, _rooms_document(_ROOMS))

    assert "does not exist" in _messages()


def test_a_map_with_no_types_is_a_diagnostic(fake_root: Path) -> None:
    write(fake_root, ROOMS_PATH, _rooms_document(_ROOMS))
    write(
        fake_root,
        ROOM_TYPES_PATH,
        yaml.safe_dump({"room_types": [], "house": {"slots": []}}),
    )

    assert "defines no room types" in _messages()


# --------------------------------------------------------------------------
# The committed map
# --------------------------------------------------------------------------


def test_the_committed_map_passes_its_own_checks() -> None:
    assert _diagnostics() == []


def test_every_committed_type_names_its_source_rooms() -> None:
    """The clause the task names, read off the committed file directly."""
    for room_type in room_types.load_room_types().types:
        assert room_type.source_rooms, f"{room_type.name} names no source room"
        assert room_type.default is not None, f"{room_type.name} has no default"


def test_the_committed_house_scope_exists() -> None:
    room_map = room_types.load_room_types()
    assert room_map.has_house, "the house scope is missing"
    assert room_map.house_slots, "the house scope provides no slots"


def test_every_committed_default_type_is_corroborated() -> None:
    """The corroboration rule against the real tree, stated as the join rather
    than through the diagnostics."""
    for room_type in room_types.load_room_types().types:
        if not room_type.default:
            continue
        repos = {source.repo for source in room_type.source_rooms}
        assert len(repos) >= 2, f"{room_type.name} defaults on {repos}"


def test_every_committed_room_is_a_source_of_exactly_one_type() -> None:
    """The partition property, against the real tree: the map covers every room
    and double-counts none."""
    recorded = {(room.repo, room.name) for room in rooms.load_rooms()}
    claimed: list[tuple[str, str]] = [
        (source.repo, source.room)
        for room_type in room_types.load_room_types().types
        for source in room_type.source_rooms
    ]
    assert set(claimed) == recorded
    assert len(claimed) == len(set(claimed))


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/` and no `.local/`, and the suite runs in a
    checkout without either. The check reads `room_types.yaml` and `rooms.yaml`,
    both committed, and nothing else."""
    assert not paths.RESSOURCES.exists()
    assert not paths.LOCAL.exists()

    _commit(fake_root, house=False)
    assert "defines no `house` scope" in _messages()
