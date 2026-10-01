"""Schema migrations: an old export lifted to the current one, step by step.

Phase 3's fifth requirement, and the half of the exit criterion that is not the
round trip. A backup is a document a user keeps for years, so a build that only
read its own version would make every version bump a backup nobody can restore.
The migration is therefore a real artifact with its own golden fixtures, and not
a compatibility branch hidden in the parser.

The shape is a registry of steps keyed by the version a document *is*, each one
lifting it to the next. `migrate_export` walks the steps until the document is
current, so a future 1.1.0-to-1.2.0 step is added beside the one below it and no
earlier step moves. A document already at the current version is returned
unchanged; a document *newer* than this build is refused rather than parsed
hopefully, because this build cannot know what a later version added and a
half-understood import is worse than a refusal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import cast

__all__ = [
    "CURRENT_EXPORT_VERSION",
    "MigrationError",
    "UnsupportedExportVersionError",
    "migrate_export",
]

#: The export version this build writes and reads. Kept here rather than in
#: `engine/export.py` so the migrations registry and the writer cannot name two
#: different current versions.
CURRENT_EXPORT_VERSION = "1.1.0"


class MigrationError(Exception):
    """Base for the failures this module defines."""


class UnsupportedExportVersionError(MigrationError):
    """A document whose version this build has no path from, or one ahead of it."""

    def __init__(self, version: str) -> None:
        super().__init__(
            f"the export version {version!r} is not one this build understands; "
            f"it reads up to {CURRENT_EXPORT_VERSION!r}"
        )
        self.version = version


def migrate_export(document: Mapping[str, object]) -> Mapping[str, object]:
    """Lift an export document to the current version, one step at a time."""
    version = _version_of(document)
    while version != CURRENT_EXPORT_VERSION:
        step = _STEPS.get(version)
        if step is None:
            raise UnsupportedExportVersionError(version)
        document = step(document)
        version = _version_of(document)
    return document


def _version_of(document: Mapping[str, object]) -> str:
    declared = document.get("format_version")
    if not isinstance(declared, str):
        raise MigrationError("an export document carries no 'format_version' string")
    return declared


# --------------------------------------------------------------------------
# The steps. Each takes a document at its version and returns one at the next.
# --------------------------------------------------------------------------


def _upgrade_1_0_0_to_1_1_0(document: Mapping[str, object]) -> dict[str, object]:
    """Lift 1.0.0's flat binding list into rooms, and add the Phase 3 fields.

    A 1.0.0 export names a room only by id and carries no room name, type,
    house scope, profiles or selections. The lifted document therefore has rooms
    whose name is derived from the id and whose type is the id itself -- the one
    guess that keeps a binding addressable -- an empty house scope, no profiles
    and no selections. Nothing is invented that the old document could not have
    meant: the rooms and their bindings are exactly the old ones, and the rest is
    the empty default a house with none of it has.
    """
    if "bindings" not in document:
        raise MigrationError("a 1.0.0 export must carry a 'bindings' list")
    rooms = _rooms_from_flat_bindings(document["bindings"])
    upgraded: dict[str, object] = {
        "format_version": "1.1.0",
        "house": document["house"],
        "rooms": rooms,
        "house_scope": {"slots": []},
        "profiles": [],
        "active": {"rooms": {}, "house_profile": None},
    }
    if "exported_at" in document:
        upgraded["exported_at"] = document["exported_at"]
    return upgraded


def _rooms_from_flat_bindings(bindings: object) -> list[dict[str, object]]:
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)):
        raise MigrationError("a 1.0.0 export must carry a 'bindings' list")
    grouped: dict[str, dict[str, object]] = {}
    for entry in cast("Sequence[object]", bindings):
        if not isinstance(entry, Mapping):
            raise MigrationError(f"a 1.0.0 binding is not an object: {entry!r}")
        row = cast("Mapping[str, object]", entry)
        room_id = str(row["room"])
        room = grouped.setdefault(
            room_id,
            {
                "id": room_id,
                "name": room_id.replace("_", " ").title(),
                "type": room_id,
                "bindings": {},
            },
        )
        cast("dict[str, object]", room["bindings"])[str(row["slot"])] = {
            "registry_id": str(row["registry_id"]),
            "entity_id": str(row["entity_id"]),
        }
    return list(grouped.values())


#: Version -> the step that lifts a document at that version to the next. A
#: registry rather than a chain of `if`s so a step is added, named and tested in
#: one place, and the order is the version order rather than the source order.
_STEPS: Mapping[str, Callable[[Mapping[str, object]], Mapping[str, object]]] = {
    "1.0.0": _upgrade_1_0_0_to_1_1_0,
}
