"""Scope-aware slot availability: the half of 6.3 that joins two files.

`catalog/room_types.yaml` answers a different question at each scope, and the
two answers are different sets. A `room` behaviour is instantiated once per
room, so the slots it requires have to be ones some room type can supply; a
`house` behaviour is instantiated once for the whole house, so it may require
only what the `house` scope provides. Reading both scopes off one supply would
make the distinction decorative and would force a house behaviour -- an away
shutdown, a mode -- through a room type it has nothing to do with, which is
exactly the rejection design D6 exists to prevent.

The map also owns the *names* its supply is made of, and the membership rule
belongs here rather than in `room_types`: that module checks `provides_slots`
for presence and says so, and defers the controlled-vocabulary half to this
check because the vocabulary (`catalog/slots.yaml`, task 5.3) is populated
after the map (task 5.2), so asserting it there would run against an empty set
the task order guarantees. A name in the supply that is not a role
`lexicon.ROLE_VOCABULARY` fixes is one no behaviour may require by a rule the
schema already enforces, so it would sit in the map supplying nothing.

Every finding names the row and the slot it cannot bind, because the reader's
next move is to open `behaviors.yaml` at that row and change what it requires --
or to open the map and add the type that supplies it -- and a diagnostic that
named only the file would leave that choice unmade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import behaviors, lexicon, room_types
from .errors import Report
from .narrow import as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping

SCOPE_CHECK = "scope"

#: The map whose supply the rows are checked against, and the rows themselves.
#: Both are spelled here because they are this check's two inputs, and the
#: `room_types` module exports its own file name while the behaviour corpus has
#: no such constant to borrow.
_ROOM_TYPES = f"catalog/{room_types.ROOM_TYPES_FILE}"
_BEHAVIORS = "catalog/behaviors.yaml"


def check_scope(report: Report) -> None:
    """Every row's required slots, against the supply of the scope it carries.

    A map that defines no room types is reported and stops: every room-scoped
    row would otherwise fail against an empty supply, which is one defect --
    an absent map -- reported once per row. The two supplies are then computed
    once, because the union over the types is the same set for every row and
    deriving it inside the row loop would repeat one join for the whole corpus.
    """
    room_map = room_types.load_room_types()
    if not room_map.types:
        report.add(
            SCOPE_CHECK,
            _ROOM_TYPES,
            "defines no room types; a room-scoped behaviour requires the slots "
            "some room type provides, and a map that names no type provides none",
        )
        return

    _check_map_names(report, room_map)

    supplies: dict[str, frozenset[str]] = {
        "room": _room_supply(room_map),
        "house": (
            frozenset(room_map.house_slots) if room_map.has_house else frozenset()
        ),
    }
    # A scope that exists and supplies nothing is reported once, where the
    # absence belongs -- against the map -- rather than once for each of its
    # rows. A `house` scope that was never written is left to `room_types`,
    # which reports it against the file and is the check that owns the map's
    # shape; the two absences are different findings and neither is this one.
    for scope in ("room", "house"):
        if not supplies[scope] and (scope == "room" or room_map.has_house):
            report.add(SCOPE_CHECK, _supply_where(scope), _empty_supply(scope))

    for row in behaviors.load_behaviors():
        _check_row(report, row, supplies)


def _room_supply(room_map: room_types.RoomTypeMap) -> frozenset[str]:
    """Every slot any room type offers, since a room row may be any room's.

    The union rather than a per-type check: a behaviour is a template a room of
    *some* type instantiates, so a slot is available to a room-scoped row if any
    one type supplies it, and checking each type in turn would report a slot
    that three types lack even though the fourth supplies it.
    """
    return frozenset(
        slot for room_type in room_map.types for slot in room_type.provides_slots
    )


def _check_map_names(report: Report, room_map: room_types.RoomTypeMap) -> None:
    """Every slot the map offers by name is a role the lexicon fixes.

    The check is over the map's supply and not over `catalog/slots.yaml`, which
    is checked against the same vocabulary in `slots`: two files written from
    one controlled set cannot drift from it in one of the two places and still
    be said to share it.
    """
    for room_type in room_map.types:
        where = f"{_ROOM_TYPES}:{room_type.name or '<unnamed>'}"
        _check_offered(report, room_type.provides_slots, where)
    if room_map.has_house:
        _check_offered(report, room_map.house_slots, f"{_ROOM_TYPES}:house")


def _check_offered(report: Report, slots: tuple[str, ...], where: str) -> None:
    unknown = sorted({slot for slot in slots if slot not in lexicon.ROLE_VOCABULARY})
    if not unknown:
        return
    report.add(
        SCOPE_CHECK,
        where,
        f"offers {unknown}, outside the controlled vocabulary "
        f"{list(lexicon.ROLE_VOCABULARY)}; a slot here is one a behaviour may "
        "require, so a name the lexicon does not fix supplies a placeholder no "
        "pack can bind",
    )


def _check_row(
    report: Report,
    row: Mapping[str, object],
    supplies: Mapping[str, frozenset[str]],
) -> None:
    """One row: the slots it requires, against the supply its scope reaches.

    A scope with an empty supply is skipped rather than failing each of its rows
    in turn: the empty supply was named once above, and repeating it per row
    would bury that one finding under however many rows share the scope.
    """
    scope = as_text(row.get("scope")) or ""
    supply = supplies.get(scope)
    if supply is None or not supply:
        return

    missing = sorted(set(_required(row)) - supply)
    if not missing:
        return
    report.add(
        SCOPE_CHECK,
        f"{_BEHAVIORS}:{as_text(row.get('id')) or '<unnamed>'}",
        f"is scoped `{scope}` and requires {missing}, which {_supply_clause(scope)}; "
        "a slot a behaviour's own scope cannot supply is one it can never bind, "
        "so the row is either scoped above what it needs or reaches past it",
    )


def _required(row: Mapping[str, object]) -> tuple[str, ...]:
    """The row's `required_slots` that are names, in order."""
    return tuple(
        text
        for text in (as_text(item) for item in as_sequence(row.get("required_slots")))
        if text
    )


def _supply_where(scope: str) -> str:
    return _ROOM_TYPES if scope == "room" else f"{_ROOM_TYPES}:house"


def _empty_supply(scope: str) -> str:
    if scope == "room":
        return (
            "offers no room type a slot; a room-scoped behaviour requires only "
            "slots some room type provides, and with none there is nothing to "
            "check it against"
        )
    return (
        "provides no slots; a house-scoped behaviour requires only slots the "
        "`house` scope provides, and a scope that supplies none fails every one"
    )


def _supply_clause(scope: str) -> str:
    if scope == "room":
        return "no room type in `catalog/room_types.yaml` provides"
    return "the `house` scope does not provide"
