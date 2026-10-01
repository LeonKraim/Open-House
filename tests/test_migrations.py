"""Schema migrations: golden old exports lifted to the current version.

Phase 3's fifth requirement, and the half of the exit criterion that is not the
round trip. The goldens here are 1.0.0 documents, kept as data because a backup
is a thing a user keeps and a build that could only read its own version would
make every version bump a backup nobody can restore. The named checks are the
four the phase asks of the registry: an old document migrates, a current one
passes through unchanged, a newer one is refused rather than parsed hopefully,
and a document with no version is refused rather than guessed at.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from jsonschema.validators import Draft202012Validator

from engine import export as engine_export
from engine import migrations as engine_migrations
from engine.binding import House
from engine.profiles import ProfileSet, load_profile_schema
from engine.vocabulary import Vocabulary
from tools.catalog import schemas as catalog_schemas

ROOT = Path(__file__).resolve().parents[1]

#: A 1.0.0 export in the only form that version had: a flat binding list, each
#: row naming a room and a slot. Two rooms, one of them bound twice, so the
#: migration's grouping is exercised rather than assumed.
GOLDEN_1_0_0: dict[str, object] = {
    "format_version": "1.0.0",
    "house": "the legacy house",
    "exported_at": "2020-01-01T00:00:00Z",
    "bindings": [
        {
            "room": "living_room",
            "slot": "light_group",
            "registry_id": "living_room_main",
            "entity_id": "light.living_room_main",
        },
        {
            "room": "living_room",
            "slot": "motion_sensor",
            "registry_id": "living_room_motion",
            "entity_id": "binary_sensor.living_room_motion",
        },
        {
            "room": "bedroom",
            "slot": "light_group",
            "registry_id": "bedroom_main",
            "entity_id": "light.bedroom_main",
        },
    ],
}

#: A second golden whose two rooms are interleaved, so a migration that grouped
#: by sorting rather than by first appearance would reorder them and this would
#: say so.
INTERLEAVED_1_0_0: dict[str, object] = {
    "format_version": "1.0.0",
    "house": "an interleaved house",
    "bindings": [
        {
            "room": "kitchen",
            "slot": "light_group",
            "registry_id": "kitchen_main",
            "entity_id": "light.kitchen_main",
        },
        {
            "room": "garage",
            "slot": "light_group",
            "registry_id": "garage_main",
            "entity_id": "light.garage_main",
        },
        {
            "room": "kitchen",
            "slot": "motion_sensor",
            "registry_id": "kitchen_motion",
            "entity_id": "binary_sensor.kitchen_motion",
        },
    ],
}


def _current_export_schema() -> Mapping[str, object]:
    current = catalog_schemas.current_version(
        catalog_schemas.load_versions("export-document")
    )
    assert current is not None
    return current.document


def _assert_valid(document: Mapping[str, object]) -> None:
    assert (
        list(Draft202012Validator(dict(_current_export_schema())).iter_errors(document))
        == []
    )


# --------------------------------------------------------------------------
# The step
# --------------------------------------------------------------------------


def test_a_golden_export_migrates_to_the_current_version() -> None:
    migrated = engine_migrations.migrate_export(GOLDEN_1_0_0)
    assert migrated["format_version"] == engine_migrations.CURRENT_EXPORT_VERSION
    assert migrated["house"] == "the legacy house"
    assert migrated["exported_at"] == "2020-01-01T00:00:00Z"


def test_the_migration_groups_the_flat_bindings_into_rooms() -> None:
    migrated = engine_migrations.migrate_export(GOLDEN_1_0_0)
    rooms = migrated["rooms"]
    assert [room["id"] for room in rooms] == ["living_room", "bedroom"]

    living_room = rooms[0]
    assert living_room["name"] == "Living Room"
    assert living_room["type"] == "living_room"
    assert set(living_room["bindings"]) == {"light_group", "motion_sensor"}
    assert living_room["bindings"]["light_group"] == {
        "registry_id": "living_room_main",
        "entity_id": "light.living_room_main",
    }


def test_the_migration_preserves_the_order_rooms_first_appear() -> None:
    migrated = engine_migrations.migrate_export(INTERLEAVED_1_0_0)
    assert [room["id"] for room in migrated["rooms"]] == ["kitchen", "garage"]


def test_the_migrated_document_fills_the_new_fields_with_the_empty_default() -> None:
    migrated = engine_migrations.migrate_export(GOLDEN_1_0_0)
    assert migrated["house_scope"] == {"slots": []}
    assert migrated["profiles"] == []
    assert migrated["active"] == {"rooms": {}, "house_profile": None}


def test_the_migrated_document_validates_against_the_current_schema() -> None:
    _assert_valid(engine_migrations.migrate_export(GOLDEN_1_0_0))


# --------------------------------------------------------------------------
# The edges: current, newer, and version-less
# --------------------------------------------------------------------------


def test_a_current_document_migrates_to_itself() -> None:
    document = engine_export.export_backup(
        house=_house(), profiles=ProfileSet([], schema=load_profile_schema(ROOT))
    )
    assert engine_migrations.migrate_export(copy.deepcopy(document)) == document


def test_a_document_from_a_newer_build_is_refused() -> None:
    with pytest.raises(engine_migrations.UnsupportedExportVersionError) as raised:
        engine_migrations.migrate_export(
            {"format_version": "9.9.9", "house": "a house"}
        )
    assert raised.value.version == "9.9.9"


def test_a_document_with_no_version_is_refused() -> None:
    with pytest.raises(engine_migrations.MigrationError):
        engine_migrations.migrate_export({"house": "a house"})


def test_a_legacy_document_with_no_binding_list_is_refused() -> None:
    with pytest.raises(engine_migrations.MigrationError):
        engine_migrations.migrate_export(
            {"format_version": "1.0.0", "house": "a house"}
        )


def test_a_legacy_binding_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(engine_migrations.MigrationError):
        engine_migrations.migrate_export(
            {"format_version": "1.0.0", "house": "a house", "bindings": ["nope"]}
        )


# --------------------------------------------------------------------------
# The migrated golden imports, and the committed example still reads
# --------------------------------------------------------------------------


def test_the_migrated_golden_imports_into_a_house() -> None:
    vocabulary = _vocabulary()
    backup = engine_export.import_backup(
        GOLDEN_1_0_0, vocabulary=vocabulary, profile_schema=load_profile_schema(ROOT)
    )
    assert backup.house.name == "the legacy house"
    assert [room.id for room in backup.house.rooms] == ["living_room", "bedroom"]
    living_room = backup.house.rooms[0]
    assert living_room.type == "living_room"
    assert living_room.bindings["light_group"] == "light.living_room_main"


def test_the_committed_legacy_example_still_reads() -> None:
    """The example the project ships is a document of the *current* schema.

    It is a 1.0.0 document, and the export concept now has a 1.1.0 -- so this is
    the claim that the older version is still an export and that the migration
    lifts it, rather than the claim that the file has been rewritten.
    """
    document = yaml.safe_load(
        (ROOT / "packs/official/example-export.yaml").read_text(encoding="utf-8")
    )
    assert document["format_version"] == "1.0.0"
    migrated = engine_migrations.migrate_export(document)
    assert migrated["format_version"] == engine_migrations.CURRENT_EXPORT_VERSION
    _assert_valid(migrated)


# -- Small helpers so the tests read as the claims they make ------------------


def _vocabulary() -> Vocabulary:
    return Vocabulary.load(ROOT)


def _house() -> House:
    return House.from_document(
        {
            "name": "a house",
            "rooms": [
                {
                    "id": "foyer",
                    "name": "Foyer",
                    "type": "foyer",
                    "bindings": {"light_group": {"entity_id": "light.foyer"}},
                }
            ],
            "house_scope": {"slots": ["light_group"]},
        },
        vocabulary=_vocabulary(),
    )
