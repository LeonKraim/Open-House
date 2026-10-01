"""The room-type map: the generic kinds of space, and the house scope.

`catalog/room_types.yaml` is derived from `catalog/rooms.yaml`, and the two
files answer different questions. `rooms.yaml` records what exists, room by
room, in each repo's own spelling; this file records the *kinds* those rooms
instantiate, so a later phase can say "a bedroom needs a light group" without
naming any one house's bedroom. The derivation runs one way and covers: every
type names the concrete source rooms that produced it, every source names a
room `rooms.yaml` records, and every recorded room is the source of exactly one
type. That is what makes the map a partition of the real rooms rather than a
second list floating above them.

`default` is the one judgement the file carries, and it is a quality gate rather
than a coverage rule -- the distinction the spec draws and the one worth keeping
straight here. The union is *not* truncated by it: a kind that exists in one
repo is recorded and marked `default: false`, because the map has to be able to
express every real room and a room a later phase cannot express is the failure
the union exists to prevent. What `default: true` claims is narrower -- that
Phase 1 may pre-populate the type for a stranger -- so it demands corroboration
from at least two repos, and a type whose rooms all come from one repo says so
by staying `false`.

The `house` scope is separate from the types for the reason the schema records:
a behaviour that belongs to the whole house -- an away shutdown, a mode,
notification routing -- must not be forced through a room type it has nothing to
do with.

Schema validation is deliberately not this module's job. `catalog/
room_types.yaml` is validated against `schemas/catalog/room_types.json`, which
references the runtime `room-type` schema, by the catalog validator; restating
that here would be a second copy of a shape the schema already fixes. What this
module adds is the cross-file half no JSON Schema can state -- that the source
rooms exist, that they are the rooms `rooms.yaml` records, and that a
`default: true` type is corroborated across repos.

`provides_slots` is checked for presence and nothing finer. The slot *vocabulary*
is `catalog/slots.yaml`, which task 5.3 populates after this file, so the
controlled-vocabulary membership rule is not assembled yet; asserting it here
would fail every run against the empty vocabulary the task order guarantees.
The names in the committed file are drawn from the role lexicon's controlled
set (`tools/catalog/lexicon.ROLE_VOCABULARY`), and task 6.3's scope-aware
check is where that membership is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from . import errors, paths, rooms
from .errors import CheckError, Report
from .narrow import as_bool, as_mapping, as_sequence, as_text

ROOM_TYPES_CHECK = "room-types"

ROOM_TYPES_FILE = "room_types.yaml"


@dataclass(frozen=True, slots=True)
class SourceRoom:
    """One concrete room a type was derived from, as `rooms.yaml` names it."""

    repo: str
    room: str


@dataclass(frozen=True, slots=True)
class RoomType:
    """A kind of room, its source rooms, and the slots it offers.

    `default` is `bool | None` rather than `bool`, and the difference is a
    finding rather than a convenience: a type that carries no `default` at all
    has not said whether it may be pre-populated, and reading a missing flag as
    `false` would turn an omission into a decision nobody made.
    """

    name: str
    default: bool | None
    source_rooms: tuple[SourceRoom, ...]
    provides_slots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoomTypeMap:
    """The whole file: the types, and the house scope beside them.

    `has_house` is carried separately from `house_slots` because the two absences
    mean different things. No `house` key is a scope that was never written; a
    `house` with no slots is one that exists and supplies nothing. Collapsing
    them would report the second as the first.
    """

    types: tuple[RoomType, ...]
    has_house: bool
    house_slots: tuple[str, ...]


def load_room_types() -> RoomTypeMap:
    """The committed map, or `CheckError` if the file cannot be read.

    Raising rather than returning an empty map, for the reason
    `rooms.load_rooms` gives: an unreadable file read as an empty one would leave
    every per-type check passing on nothing, and on the committed tree that is
    the difference between twenty types being right and twenty types being gone
    -- a green run with the map's whole content missing. `validate_all` catches
    `CheckError` and nothing else, so an unguarded `yaml.safe_load` would reach
    the pre-commit hook as a traceback instead of naming the file.
    """
    path = paths.CATALOG / ROOM_TYPES_FILE
    relative = f"catalog/{ROOM_TYPES_FILE}"
    if not path.is_file():
        raise CheckError(ROOM_TYPES_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(ROOM_TYPES_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(
            ROOM_TYPES_CHECK, relative, f"cannot be parsed: {exc}"
        ) from exc
    document = as_mapping(loaded)

    types: list[RoomType] = []
    for entry in as_sequence(document.get("room_types")):
        row = as_mapping(entry)
        types.append(
            RoomType(
                name=as_text(row.get("name")) or "",
                default=as_bool(row.get("default")),
                source_rooms=tuple(
                    _source_room(item) for item in as_sequence(row.get("source_rooms"))
                ),
                provides_slots=_strings(row.get("provides_slots")),
            )
        )

    house = document.get("house")
    # Read through `as_mapping` before the `isinstance` narrows `house`, or the
    # narrowed `dict[Unknown, Unknown]` reaches a strictly typed call.
    house_map = as_mapping(house)
    has_house = isinstance(house, dict)
    return RoomTypeMap(
        types=tuple(types),
        has_house=has_house,
        house_slots=_strings(house_map.get("slots")) if has_house else (),
    )


def _source_room(value: object) -> SourceRoom:
    row = as_mapping(value)
    return SourceRoom(
        repo=as_text(row.get("repo")) or "",
        room=as_text(row.get("room")) or "",
    )


def _strings(value: object) -> tuple[str, ...]:
    """The string entries of a list, in order, dropping anything else.

    A non-string is refused by the schema that describes the field, so reporting
    it here as a type error would be this check answering for a shape it does not
    own; what is kept is the names, which are what the checks below join on.
    """
    return tuple(
        text for text in (as_text(item) for item in as_sequence(value)) if text
    )


def check_room_types(report: Report) -> None:
    """The map against the rooms it claims to be derived from, and itself.

    The house scope, each type, then coverage. Coverage is last and is the
    strongest of the three: the per-type checks pin each type from the inside,
    and coverage pins the map from the outside -- every room is a source of a
    type, and no room is a source of two -- so a room cannot be dropped from the
    map and a type cannot double-count one.
    """
    room_map = load_room_types()
    where = f"catalog/{ROOM_TYPES_FILE}"
    if not room_map.types:
        report.add(
            ROOM_TYPES_CHECK,
            where,
            "defines no room types; the map exists to record every real room as a "
            "kind, and one that names none represents no room at all",
        )
        return

    known = {(room.repo, room.name) for room in rooms.load_rooms()}
    _check_house_scope(report, room_map, where)
    for room_type in room_map.types:
        _check_room_type(report, room_type, known, where)
    _check_coverage(report, room_map, known, where)


def _check_house_scope(report: Report, room_map: RoomTypeMap, where: str) -> None:
    """The house scope exists and supplies slots.

    Two findings and not one, because a missing scope and an empty one fail the
    same clause for different reasons and call for different corrections: the
    first is a section to add, the second a list to fill.
    """
    if not room_map.has_house:
        report.add(
            ROOM_TYPES_CHECK,
            where,
            "defines no `house` scope; a behaviour that belongs to the whole house "
            "-- an away shutdown, a mode, notification routing -- must not be "
            "forced through a room type it has nothing to do with",
        )
        return
    if not room_map.house_slots:
        report.add(
            ROOM_TYPES_CHECK,
            f"{where}:house",
            "provides no slots; the house scope exists so a house-scoped behaviour "
            "has slots to require, and one that supplies none supplies nothing",
        )


def _check_room_type(
    report: Report,
    room_type: RoomType,
    known: set[tuple[str, str]],
    where: str,
) -> None:
    """One type: a name, a `default`, corroboration, sources and slots."""
    at = f"{where}:{room_type.name or '<unnamed>'}"

    if not room_type.name:
        report.add(ROOM_TYPES_CHECK, at, "carries no `name`")
    if room_type.default is None:
        report.add(
            ROOM_TYPES_CHECK,
            at,
            "carries no boolean `default`; every type states whether Phase 1 may "
            "pre-populate it, and an absent flag is a decision nobody recorded",
        )
    elif room_type.default:
        _check_corroborated(report, room_type, at)
    if not room_type.source_rooms:
        report.add(
            ROOM_TYPES_CHECK,
            at,
            "names no source room; a type exists because real rooms produced it, "
            "and one that names none is a kind nothing instantiates",
        )
    if not room_type.provides_slots:
        report.add(
            ROOM_TYPES_CHECK,
            at,
            "provides no slots; a room type exists to say what a room of that kind "
            "offers, and one that offers nothing answers nothing",
        )

    _check_sources(report, room_type, known, at)


def _check_corroborated(report: Report, room_type: RoomType, at: str) -> None:
    """A `default: true` type comes from at least two repos.

    The repo set is read off the declared sources rather than off the resolved
    rooms, so a type whose sources all name one repo fails here even when one of
    those sources is misspelled -- the misspelling is its own finding, and this
    one is about the corroboration the flag claims.
    """
    repos = sorted({source.repo for source in room_type.source_rooms if source.repo})
    if len(repos) >= 2:
        return
    if not repos:
        report.add(
            ROOM_TYPES_CHECK,
            at,
            "is `default: true` but traces to no source repo; pre-populating a "
            "type for a new user assumes its shape is shared, so a type no repo "
            "corroborates must be `default: false`",
        )
        return
    report.add(
        ROOM_TYPES_CHECK,
        at,
        f"is `default: true` but its source rooms all come from `{repos[0]}`; "
        "pre-populating a type for a new user assumes its shape is shared, so a "
        "type one repo has is recorded with `default: false` and stays "
        "representable without being assumed",
    )


def _check_sources(
    report: Report,
    room_type: RoomType,
    known: set[tuple[str, str]],
    at: str,
) -> None:
    """Each source names a room `rooms.yaml` records.

    The join is by `(repo, room)` and nothing else, because `rooms.yaml` already
    carries the paths and a second copy here would be free to disagree with the
    file the type is derived from.
    """
    for source in room_type.source_rooms:
        if not source.repo or not source.room:
            report.add(
                ROOM_TYPES_CHECK,
                at,
                "names a source room with no `repo` or no `room`; the source is a "
                "join into `rooms.yaml`, and half a key joins to nothing",
            )
        elif (source.repo, source.room) not in known:
            report.add(
                ROOM_TYPES_CHECK,
                at,
                f"names source room `{source.repo}/{source.room}`, which no record "
                "in `catalog/rooms.yaml` carries; a type is derived from real "
                "rooms, and a source that names none is a kind nothing produced",
            )


def _check_coverage(
    report: Report,
    room_map: RoomTypeMap,
    known: set[tuple[str, str]],
    where: str,
) -> None:
    """Every real room is a source of one type, and no room of two.

    This is the clause that keeps the map a partition. A room in no type is a
    room a later phase cannot express -- the truncation the union is meant to
    prevent. A room in two types is the opposite error and a quieter one: both
    types carry the room, the union counts it twice, and "what rooms does this
    type cover" stops having one answer.

    Sources that do not resolve are skipped rather than counted, so a misspelled
    source produces the per-type finding and not a spurious "covers nothing"
    here as well.
    """
    claimed: dict[tuple[str, str], list[str]] = {}
    for room_type in room_map.types:
        for source in room_type.source_rooms:
            key = (source.repo, source.room)
            if key in known:
                claimed.setdefault(key, []).append(room_type.name)

    for key, type_names in sorted(claimed.items()):
        if len(type_names) > 1:
            report.add(
                ROOM_TYPES_CHECK,
                f"{where}:{key[0]}/{key[1]}",
                f"is a source room of {len(type_names)} types "
                f"({', '.join(sorted(type_names))}); the map partitions the real "
                "rooms, so a room two types both claim is one the union counts "
                "twice",
            )

    for repo, name in sorted(known - set(claimed)):
        report.add(
            ROOM_TYPES_CHECK,
            where,
            f"room `{repo}/{name}` is not a source of any room type; the union is "
            "not truncated by the corroboration rule, so every real room is "
            "representable, and one no type names is a room a later phase cannot "
            "express",
        )
