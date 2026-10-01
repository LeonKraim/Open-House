"""Export, scrub, diff, relink and import -- the configuration round trip.

Phase 3's fourth requirement. An export is a document and a document is data, so
this module is a set of pure functions over mappings and the engine's own values
-- `House` and `ProfileSet` -- with no adapter, no clock and no file. The facade
is where a document is written and read; here it is only built and parsed.

Four functions, and the reason each is separate rather than one `export` with
flags:

- `export_backup` writes the *whole* configuration: rooms, their bindings (each
  with a registry id beside its entity id), the house scope, every profile and
  the selections in force. This is the half that must round-trip, and the
  property test drives exactly that pair.
- `scrub_template` takes a backup and produces a shareable *pack*: the device
  registry ids and entity ids are gone, because they are the one part of a
  configuration that names somebody's house, and what is left is the shape a
  stranger can adopt. The pack it produces is a `pack-manifest` document of kind
  `house-template` plus the template payload it names, so "a shareable template
  that is itself a pack" is a fact about the artifact rather than a claim.
- `dry_run` reports what importing a document would change, by comparing it
  against the configuration in hand. It changes nothing.
- `relink` rewrites entity ids from a registry-id-to-entity-id map, and reports
  the registry ids the map does not cover -- the re-link wizard's two answers.

`import_backup` is the inverse of `export_backup`. It first migrates the document
(`engine/migrations.py`), so an export written by an older build imports, and
then rebuilds the configuration. Export then import is the identity on the
configuration, which is the phase's exit criterion and what the property test in
`tests/test_export.py` asserts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import jsonschema

from engine.binding import House
from engine.migrations import (
    CURRENT_EXPORT_VERSION,
    UnsupportedExportVersionError,
    migrate_export,
)
from engine.profiles import ProfileSet

if TYPE_CHECKING:
    from engine.vocabulary import Vocabulary

__all__ = [
    "Change",
    "Diff",
    "RelinkResult",
    "TemplatePack",
    "build_house_document",
    "dry_run",
    "export_backup",
    "import_backup",
    "relink",
    "scrub_template",
]


class ExportError(Exception):
    """Base for the failures this module defines."""


class InvalidExportError(ExportError):
    """A document that does not conform to the export schema it names."""

    def __init__(self, pointer: str, message: str) -> None:
        super().__init__(f"the export document is invalid at {pointer}: {message}")
        self.pointer = pointer
        self.message = message


@dataclass(frozen=True, slots=True)
class Change:
    """One leaf that differs between two documents."""

    path: str
    before: object
    after: object


@dataclass(frozen=True, slots=True)
class Diff:
    """What importing a document would change, computed without changing anything."""

    changes: tuple[Change, ...]

    @property
    def empty(self) -> bool:
        """True when the two documents describe the same configuration."""
        return not self.changes


@dataclass(frozen=True, slots=True)
class RelinkResult:
    """A relinked document, and the registry ids the map did not cover."""

    document: Mapping[str, object]
    unresolved: tuple[str, ...]
    relinked: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplatePack:
    """A shareable template: the pack manifest, and the payload it names."""

    manifest: Mapping[str, object]
    template: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Backup:
    """The configuration an import produces."""

    house: House
    profiles: ProfileSet
    modes: tuple[Mapping[str, object], ...]
    exported_at: str | None


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def export_backup(
    *,
    house: House,
    profiles: ProfileSet,
    modes: Sequence[Mapping[str, object]] = (),
    registry_ids: Mapping[tuple[str, str], str] | None = None,
    exported_at: str | None = None,
) -> dict[str, object]:
    """Write the whole configuration as an export document.

    The registry id is the binding's own when the house carries one (it does not
    in this build -- the port addresses entity ids) and otherwise the entity id's
    object part, which is what the committed example uses and what a real device
    registry id looks like. `registry_ids` overrides the derivation for a caller
    that has the real ids, keyed by `(room, slot)`.
    """
    override = {} if registry_ids is None else dict(registry_ids)
    document: dict[str, object] = {
        "format_version": CURRENT_EXPORT_VERSION,
        "house": house.name,
        "rooms": [
            {
                "id": room.id,
                "name": room.name,
                "type": room.type,
                "bindings": {
                    slot: {
                        "registry_id": override.get(
                            (room.id, slot), _object_id(entity_id)
                        ),
                        "entity_id": entity_id,
                    }
                    for slot, entity_id in sorted(room.bindings.items())
                },
            }
            for room in house.rooms
        ],
        "house_scope": {"slots": list(house.house_scope_slots)},
        "profiles": list(_profile_documents(profiles)),
        "active": {
            "rooms": {
                room_id: dict(axes) for room_id, axes in profiles.selections().items()
            },
            "house_profile": profiles.house_profile,
        },
    }
    if modes:
        document["modes"] = [dict(mode) for mode in modes]
    if exported_at is not None:
        document["exported_at"] = exported_at
    return document


def _profile_documents(profiles: ProfileSet) -> tuple[Mapping[str, object], ...]:
    held = profiles.profiles
    return tuple(held[name].to_document() for name in sorted(held))


def _object_id(entity_id: str) -> str:
    """The object part of an entity id -- `living_room_main` from the light."""
    _, _, object_id = entity_id.partition(".")
    return object_id


# --------------------------------------------------------------------------
# Scrub
# --------------------------------------------------------------------------


def scrub_template(
    backup: Mapping[str, object],
    *,
    name: str,
    version: str = "1.0.0",
    description: str,
    engine_api: str = ">=1.0.0",
    license_code: str = "no_licence",
    exported_at: str | None = None,
) -> TemplatePack:
    """Turn a backup into a shareable pack: a manifest and its template payload.

    What is removed is what names a house: the registry ids and the entity ids.
    What is kept is what a stranger can adopt: the rooms and their types, the
    slots each room binds, the profiles and the selections. The template keeps a
    room's *slot names* rather than its bindings, so adopting it means binding
    each slot to a device of one's own -- which is the re-link wizard's job and
    not this function's.
    """
    document = _as_mapping(backup, "backup")
    template: dict[str, object] = {
        "house": document.get("house"),
        "rooms": [
            {
                "id": room.get("id"),
                "name": room.get("name"),
                "type": room.get("type"),
                "slots": sorted(_as_mapping(room.get("bindings"), "bindings")),
            }
            for room in _as_sequence(document.get("rooms"), "rooms")
        ],
        "house_scope": document.get("house_scope"),
        "profiles": document.get("profiles", []),
        "active": document.get("active", {"rooms": {}, "house_profile": None}),
    }
    if exported_at is not None:
        template["exported_at"] = exported_at
    manifest: dict[str, object] = {
        "name": name,
        "version": version,
        "description": description,
        "kind": "house-template",
        "engine_api": engine_api,
        "license": license_code,
        "i18n": {"default": {name: name, description: description}},
        "provides": [{"path": f"{name}/template.json", "class": "template"}],
    }
    return TemplatePack(manifest=manifest, template=template)


# --------------------------------------------------------------------------
# Diff and relink
# --------------------------------------------------------------------------


def dry_run(before: Mapping[str, object], after: Mapping[str, object]) -> Diff:
    """The leaf differences between two documents, ordered by path.

    A dry run is a diff and not a simulation: it answers what would change, and
    it does so by flattening both documents to their leaves and comparing them,
    so a nested binding and a profile's delta are reported by path rather than by
    a summary that would hide which one moved.
    """
    changes: list[Change] = []
    for path in sorted(set(_flatten(before)) | set(_flatten(after))):
        old = _flatten(before).get(path)
        new = _flatten(after).get(path)
        if old != new:
            changes.append(Change(path=path, before=old, after=new))
    return Diff(changes=tuple(changes))


def relink(document: Mapping[str, object], registry: Mapping[str, str]) -> RelinkResult:
    """Rewrite entity ids from registry ids, reporting what the map missed.

    The wizard's two answers are both returned rather than one: a call that
    silently left an unmapped binding alone would be a call that reported success
    while leaving a house pointing at a device that is gone.
    """
    unresolved: list[str] = []
    relinked: list[str] = []
    rebuilt = _relink_node(document, registry, unresolved, relinked)
    return RelinkResult(
        document=cast("Mapping[str, object]", rebuilt),
        unresolved=tuple(sorted(set(unresolved))),
        relinked=tuple(sorted(set(relinked))),
    )


def _relink_node(
    node: object,
    registry: Mapping[str, str],
    unresolved: list[str],
    relinked: list[str],
) -> object:
    if isinstance(node, Mapping):
        mapping = cast("Mapping[str, object]", node)
        if "registry_id" in mapping and "entity_id" in mapping:
            registry_id = cast("str", mapping["registry_id"])
            replacement = registry.get(registry_id)
            if replacement is None:
                unresolved.append(registry_id)
            else:
                relinked.append(registry_id)
                return {**mapping, "entity_id": replacement}
            return dict(mapping)
        return {
            key: _relink_node(value, registry, unresolved, relinked)
            for key, value in mapping.items()
        }
    if isinstance(node, list):
        return [_relink_node(item, registry, unresolved, relinked) for item in node]
    return node


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def import_backup(
    document: Mapping[str, object],
    *,
    vocabulary: Vocabulary,
    profile_schema: Mapping[str, object],
) -> Backup:
    """Rebuild a configuration from an export, migrating it first.

    A document from an older build is migrated rather than refused, because an
    export is a thing a user keeps and a build that could not read last year's is
    a backup that is not a backup. A document from a *newer* build is refused,
    because this build cannot know what a later version added.
    """
    migrated = _as_mapping(migrate_export(document), "export")
    house = House.from_document(build_house_document(migrated), vocabulary=vocabulary)
    profiles = ProfileSet.from_document(
        {
            "profiles": migrated.get("profiles", []),
            "selections": _as_mapping(migrated.get("active", {}), "active").get(
                "rooms", {}
            ),
            "house_profile": _as_mapping(migrated.get("active", {}), "active").get(
                "house_profile"
            ),
        },
        schema=profile_schema,
    )
    modes = tuple(
        cast("Mapping[str, object]", mode)
        for mode in _as_sequence(migrated.get("modes", []), "modes")
    )
    exported_at = migrated.get("exported_at")
    return Backup(
        house=house,
        profiles=profiles,
        modes=modes,
        exported_at=None if exported_at is None else str(exported_at),
    )


def build_house_document(document: Mapping[str, object]) -> dict[str, object]:
    """The house document an export describes, in `schemas/house/`'s shape."""
    rooms = document.get("rooms")
    if rooms is None:
        # A 1.0.0 flat binding list that the migration did not lift because the
        # document already validated as current: build the rooms from the flat
        # list so an import of the legacy form still produces a house.
        rooms = _rooms_from_bindings(document.get("bindings", []))
    return {
        "name": document["house"],
        "rooms": [
            {
                "id": room["id"],
                "name": room["name"],
                "type": room["type"],
                "bindings": {
                    slot: {"entity_id": binding["entity_id"]}
                    for slot, binding in _as_mapping(
                        room["bindings"], "bindings"
                    ).items()
                },
            }
            for room in _as_sequence(rooms, "rooms")
        ],
        "house_scope": {
            "slots": list(
                _as_sequence(
                    _as_mapping(document.get("house_scope", {}), "house_scope").get(
                        "slots", []
                    ),
                    "house_scope.slots",
                )
            )
        },
    }


def _rooms_from_bindings(bindings: object) -> list[dict[str, object]]:
    """Group a 1.0.0 flat binding list into rooms, for a document with no `rooms`."""
    grouped: dict[str, dict[str, object]] = {}
    for entry in _as_sequence(bindings, "bindings"):
        row = _as_mapping(entry, "binding")
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
            "entity_id": row["entity_id"]
        }
    return list(grouped.values())


def validate_export(
    document: Mapping[str, object], schema: Mapping[str, object]
) -> None:
    """Refuse a document that is not an export of the schema's version."""
    declared = str(document.get("format_version", ""))
    # A newer document is refused here rather than parsed hopefully; an older one
    # is expected to have been migrated by `import_backup` first.
    if declared > CURRENT_EXPORT_VERSION:
        raise UnsupportedExportVersionError(declared)
    validator = jsonschema.Draft202012Validator(cast("dict[str, object]", schema))
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(part) for part in first.absolute_path)
        raise InvalidExportError(pointer, first.message)


# --------------------------------------------------------------------------
# Small helpers over untrusted documents. Each names what it wants.
# --------------------------------------------------------------------------


def _flatten(document: object, prefix: str = "") -> dict[str, object]:
    if isinstance(document, Mapping):
        flat: dict[str, object] = {}
        for key, value in cast("Mapping[str, object]", document).items():
            flat.update(_flatten(value, f"{prefix}/{key}"))
        return flat
    if isinstance(document, list):
        flat = {}
        for index, item in enumerate(document):
            flat.update(_flatten(item, f"{prefix}/{index}"))
        return flat
    return {prefix: document}


def _as_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ExportError(f"export field {field!r} is not an object")
    return cast("Mapping[str, object]", value)


def _as_sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ExportError(f"export field {field!r} is not a list")
    return cast("Sequence[object]", value)
