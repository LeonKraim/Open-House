"""The real rooms -- section 5, task 5.1.

`catalog/rooms.yaml` says two things at once and both have to hold. A repo the
project draws from has rooms -- so the file cannot quietly forget one of the
four setups -- and every path written under a room is a real source of that
room, which is the same as saying the path is a selected file and it names the
room. The two together are the task's own verify clauses.

The third thing, and the reason the first two are worth checking rather than
asserting, is that the list of paths is *complete*: a selected file that names
the room and is not written down is a finding. Without it the file could keep
only the paths somebody happened to type, look fully evidenced while half a
room's configuration went unrecorded, and nothing would say so -- the failure
mode this whole file exists to remove, arriving through the door held open for
it. That is why the fixtures below pin the rule against the *committed* file as
well: a check proven only on trees it built itself is a check whose own data it
never looked at.

A note on how the findings are collected. `rooms.check_rooms` is not yet in
`validate._CHECKS`, so these tests cannot route through `validate_all` the way
`tests/test_golden.py` does; they call the check directly and wrap it the same
way, in the same `CheckError` catch, so that a check that would reach the
pre-commit hook as a traceback reaches a test as the diagnostic it is meant to
be instead.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from tools.catalog import licenses, paths, rooms
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

ROOMS_PATH = "catalog/rooms.yaml"
INVENTORY_PATH = "catalog/inventory.json"
LICENCES_PATH = "catalog/licenses.yaml"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _inventory_document(files: dict[str, dict[str, bool]]) -> str:
    """A committed-looking inventory, with only the two fields the check reads.

    `selected` is the only per-file fact `rooms` looks at; a real inventory
    carries a class, a rule order and a parse outcome beside it, and building
    those here would be a fixture agreeing with itself about fields no test in
    this file exercises.
    """
    return json.dumps(
        {
            "repos": [
                {
                    "repo": repo,
                    "files": [
                        {"path": path, "selected": selected}
                        for path, selected in entries.items()
                    ],
                }
                for repo, entries in files.items()
            ]
        },
        indent=2,
    )


def _licences_document(repos: list[str]) -> str:
    return yaml.safe_dump(
        {"repos": [{"repo": name} for name in repos]}, sort_keys=False
    )


def _rooms_document(entries: list[tuple[str, str, list[str]]]) -> str:
    return yaml.safe_dump(
        {
            "rooms": [
                {"name": name, "repo": repo, "paths": list(path_list)}
                for name, repo, path_list in entries
            ]
        },
        sort_keys=False,
    )


#: A closed fixture: two repos, each with rooms, and every room's `paths` exactly
#: the selected files that name it. One entry per room, so a test can change one
#: thing and assert the *one* finding it produces.
_SELECTED: dict[str, dict[str, bool]] = {
    "x": {"kitchen/on.yaml": True, "kitchen/off.yaml": True, "lounge/on.yaml": True},
    "y": {"kitchen/main.yaml": True},
}

_ROOMS: list[tuple[str, str, list[str]]] = [
    ("kitchen", "x", ["kitchen/off.yaml", "kitchen/on.yaml"]),
    ("lounge", "x", ["lounge/on.yaml"]),
    ("kitchen", "y", ["kitchen/main.yaml"]),
]


def _commit_rooms(
    root: Path,
    entries: list[tuple[str, str, list[str]]] | None = None,
    selected: dict[str, dict[str, bool]] | None = None,
    repos: list[str] | None = None,
) -> None:
    write(root, LICENCES_PATH, _licences_document(repos or ["x", "y"]))
    write(root, INVENTORY_PATH, _inventory_document(selected or _SELECTED))
    write(root, ROOMS_PATH, _rooms_document(_ROOMS if entries is None else entries))


def _diagnostics() -> list[tuple[str, str]]:
    """The room check's findings, collected the way the hook collects them.

    `rooms.check_rooms` is not registered in `validate._CHECKS`, so the check is
    called directly and the `CheckError` arm is reproduced here rather than
    borrowed from `validate_all`. The arm is not decoration: the property the
    parse and missing-file tests are about is that a broken `rooms.yaml` is a
    diagnostic, and without it a `CheckError` would escape as a traceback and the
    test would still pass.
    """
    report = Report()
    try:
        rooms.check_rooms(report)
    except CheckError as exc:
        report.add(exc.check, exc.where, exc.message)
    return [
        (d.where, d.message) for d in report.diagnostics if d.check == rooms.ROOMS_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The task's two verify clauses
# --------------------------------------------------------------------------


def test_a_repo_that_contributes_no_room_is_named(fake_root: Path) -> None:
    """Each repo contributes at least one room. A setup whose rooms all go
    missing looks, in a file this one, exactly like a setup that never had any."""
    _commit_rooms(
        fake_root,
        entries=[entry for entry in _ROOMS if entry[1] != "y"],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "records no room in `y`" in findings[0][1]


def test_a_room_whose_source_path_is_not_a_selected_file_is_named(
    fake_root: Path,
) -> None:
    """Each room's source path names a file, and the file has to be one the
    inventory selected -- a path is a fact about the repo, not a label."""
    _commit_rooms(
        fake_root,
        entries=[
            (
                "kitchen",
                "x",
                ["kitchen/off.yaml", "kitchen/on.yaml", "kitchen/gone.yaml"],
            ),
            ("lounge", "x", ["lounge/on.yaml"]),
            ("kitchen", "y", ["kitchen/main.yaml"]),
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("x/kitchen")
    assert "is not a selected file" in message
    assert "kitchen/gone.yaml" in message


# --------------------------------------------------------------------------
# The rest of what a room record claims
# --------------------------------------------------------------------------


def test_a_room_that_names_no_source_path(fake_root: Path) -> None:
    """A record whose whole point is to say where the room was read from, and
    that says nowhere, is a room with no evidence at all."""
    _commit_rooms(
        fake_root,
        entries=[
            ("kitchen", "x", []),
            ("lounge", "x", ["lounge/on.yaml"]),
            ("kitchen", "y", ["kitchen/main.yaml"]),
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "names no source path" in findings[0][1]


def test_a_path_that_does_not_name_the_room(fake_root: Path) -> None:
    """The list is the room's evidence, so a selected file that is evidence for
    a different room cannot be filed under this one."""
    _commit_rooms(
        fake_root,
        entries=[
            ("kitchen", "x", ["kitchen/off.yaml", "kitchen/on.yaml", "lounge/on.yaml"]),
            ("lounge", "x", ["lounge/on.yaml"]),
            ("kitchen", "y", ["kitchen/main.yaml"]),
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("x/kitchen")
    assert "does not name the room `kitchen`" in message
    assert "lounge/on.yaml" in message


def test_a_room_that_omits_a_selected_file_that_names_it(fake_root: Path) -> None:
    """The completeness half. A list that stops short is a room the room-type
    map derived from it would under-count, and nothing else would notice."""
    _commit_rooms(
        fake_root,
        entries=[
            ("kitchen", "x", ["kitchen/on.yaml"]),
            ("lounge", "x", ["lounge/on.yaml"]),
            ("kitchen", "y", ["kitchen/main.yaml"]),
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("x/kitchen")
    assert "omits 1 selected file" in message
    assert "kitchen/off.yaml" in message


def test_the_repo_is_checked_against_the_licence_records(fake_root: Path) -> None:
    """A room's `repo` has to be a repository the project records, or the join
    the room-type map does -- through this file to `licenses.yaml` -- resolves
    to nothing and the corroboration rule cannot be evaluated."""
    _commit_rooms(
        fake_root,
        entries=[
            *_ROOMS,
            ("garage", "z", ["garage/on.yaml"]),
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "names `z`, which is no repository" in findings[0][1]


def test_a_room_recorded_twice_is_named(fake_root: Path) -> None:
    """Two records for one room each carry its evidence, so both pass the path
    checks while the map counts the room twice."""
    _commit_rooms(
        fake_root,
        entries=[*_ROOMS, ("kitchen", "x", ["kitchen/off.yaml", "kitchen/on.yaml"])],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "is recorded twice" in findings[0][1]


def test_a_room_without_a_name(fake_root: Path) -> None:
    _commit_rooms(
        fake_root,
        entries=[*_ROOMS, ("", "x", ["kitchen/on.yaml"])],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "carries no `name`" in findings[0][1]


def test_a_room_without_a_repo(fake_root: Path) -> None:
    _commit_rooms(
        fake_root,
        entries=[*_ROOMS, ("garage", "", ["garage/on.yaml"])],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "carries no `repo`" in findings[0][1]


def test_a_rooms_file_with_no_rooms_records_nothing(fake_root: Path) -> None:
    _commit_rooms(fake_root, entries=[])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "records no rooms" in findings[0][1]


# --------------------------------------------------------------------------
# A file that cannot be read
# --------------------------------------------------------------------------


def test_a_rooms_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else, so an
    unguarded read here would reach it as a traceback and take the findings of
    the other checks with it."""
    write(fake_root, LICENCES_PATH, _licences_document(["x", "y"]))
    write(fake_root, INVENTORY_PATH, _inventory_document(_SELECTED))
    write(fake_root, ROOMS_PATH, "rooms:\n  - name: kitchen\n   bad indent\n")

    assert "cannot be parsed" in _messages()


def test_a_missing_rooms_file_is_a_diagnostic(fake_root: Path) -> None:
    """Absent is not empty: an empty file would read as "there are no rooms",
    report nothing, and look like a repository that never had any."""
    write(fake_root, LICENCES_PATH, _licences_document(["x", "y"]))
    write(fake_root, INVENTORY_PATH, _inventory_document(_SELECTED))

    assert "does not exist" in _messages()


# --------------------------------------------------------------------------
# The matcher, on its own
# --------------------------------------------------------------------------


def test_a_name_matches_a_whole_segment_and_a_run_within_one() -> None:
    """The two shapes the four repos actually use: a room that is its own
    directory, and a room that leads a file named for an action on it."""
    assert rooms.names_room("office", "packages/office/lights/off.yaml")
    assert rooms.names_room("kitchen", "automations/areas/kitchen_on.yaml")
    assert rooms.names_room("wohnzimmer", "esphome/bt-proxy-wohnzimmer.yaml")


def test_a_name_does_not_match_a_longer_word_that_contains_it() -> None:
    """Contiguity is what keeps a short name from swallowing the tree: `room`
    must not match `bedroom`, and `main` must not match `maintenance`."""
    assert not rooms.names_room("room", "dashboards/bedroom/on.yaml")
    assert not rooms.names_room("main", "packages/maintenance/on.yaml")


def test_the_words_of_a_name_must_be_adjacent_in_one_segment() -> None:
    """A room written `main_bedroom` is one segment or one run inside a
    segment, never the word `main` here and `bedroom` a directory away."""
    assert not rooms.names_room("main_bedroom", "main/bedroom/on.yaml")
    assert rooms.names_room("main_bedroom", "upper/main_bedroom/on.yaml")


def test_naming_paths_is_sorted_and_whole() -> None:
    """Sorted so the committed list is a function of the inventory and not of
    iteration order, and whole so the extraction that wrote the file and the
    check that re-derives it compute the same thing."""
    assert rooms.naming_paths(
        "kitchen", ["kitchen/off.yaml", "lounge/on.yaml", "kitchen/on.yaml"]
    ) == ("kitchen/off.yaml", "kitchen/on.yaml")


# --------------------------------------------------------------------------
# The committed rooms
# --------------------------------------------------------------------------


def test_the_committed_rooms_pass_their_own_checks() -> None:
    assert _diagnostics() == []


def test_every_repo_the_project_draws_from_contributes_a_room() -> None:
    """The clause the task names, against the real tree rather than a fixture."""
    recorded = {record.repo for record in licenses.load_licences() if record.repo}
    present = {room.repo for room in rooms.load_rooms()}
    assert recorded <= present


def test_every_committed_room_names_a_path_that_is_selected_and_names_it() -> None:
    """The other clause the task names, stated against the committed artifacts
    directly rather than through the diagnostics."""
    selected = rooms.selected_paths()
    for room in rooms.load_rooms():
        assert room.paths, f"{room.repo}/{room.name} has no source path"
        for path in room.paths:
            assert path in selected[room.repo], f"{room.repo}: {path} not selected"
            assert rooms.names_room(room.name, path), (
                f"{room.repo}: {path} is not {room.name}"
            )


def test_the_committed_paths_are_every_file_that_names_the_room() -> None:
    """The completeness property the check enforces, read off the file."""
    selected = rooms.selected_paths()
    for room in rooms.load_rooms():
        assert room.paths == rooms.naming_paths(room.name, selected[room.repo])


def test_no_committed_room_is_recorded_twice() -> None:
    seen = [(room.repo, room.name) for room in rooms.load_rooms()]
    assert len(seen) == len(set(seen))


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/` and no `.local/`, and the suite runs in a
    checkout without either. The check reads `rooms.yaml` and `inventory.json`,
    both committed, and nothing else."""
    assert not paths.RESSOURCES.exists()
    assert not paths.LOCAL.exists()

    _commit_rooms(fake_root, entries=[entry for entry in _ROOMS if entry[1] != "y"])
    assert "records no room in `y`" in _messages()
