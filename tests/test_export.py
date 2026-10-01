"""Export, scrub, diff, relink, import -- Phase 3's round trip.

The exit criterion is that export then import is the identity, and that is a
property rather than an example: the property test drives it over generated
houses and profiles, and the other checks pin the four pieces the round trip is
made of -- the scrub that produces a shareable pack, the dry run that changes
nothing, the re-link that reports what its map missed, and the import that keeps
an undo. The pack-manifest check goes through the corpus registry, because the
manifest's behaviour terms are a cross-file `$ref` and validating it against the
file alone would resolve nothing.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator

from engine import export as engine_export
from engine import migrations as engine_migrations
from engine.binding import House
from engine.profiles import ProfileSet, load_profile_schema
from engine.vocabulary import Vocabulary
from openhouse import packs
from openhouse.facade import OpenHouse, open_session
from tools.catalog import examples
from tools.catalog import schemas as catalog_schemas

ROOT = Path(__file__).resolve().parents[1]

BOUND_KEY = "engine.rate_limit.bound"

ROOM_IDS = ("foyer", "kitchen", "garage", "bedroom")
ROOM_SLOTS = ("light_group", "motion_sensor", "contact_sensor", "lock", "cover")
HOUSE_SLOTS = ("light_group", "lock", "media_player")
DOMAINS = {
    "light_group": "light",
    "motion_sensor": "binary_sensor",
    "contact_sensor": "binary_sensor",
    "lock": "lock",
    "cover": "cover",
    "media_player": "media_player",
}


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def profile_schema() -> Mapping[str, object]:
    return load_profile_schema(ROOT)


@pytest.fixture(scope="module")
def export_schema() -> Mapping[str, object]:
    current = catalog_schemas.current_version(
        catalog_schemas.load_versions("export-document")
    )
    assert current is not None
    return cast("Mapping[str, object]", current.document)


# -- The house and profiles a round trip carries ------------------------------


def _inline_house() -> dict[str, object]:
    return {
        "name": "a round-tripping house",
        "rooms": [
            {
                "id": "foyer",
                "name": "Foyer",
                "type": "foyer",
                "bindings": {
                    "light_group": {"entity_id": "light.foyer"},
                    "motion_sensor": {"entity_id": "binary_sensor.foyer_motion"},
                },
            },
            {
                "id": "garage",
                "name": "Garage",
                "type": "garage",
                "bindings": {"light_group": {"entity_id": "light.garage"}},
            },
        ],
        "house_scope": {"slots": ["light_group"]},
    }


def _profiles() -> tuple[dict[str, object], ...]:
    return (
        {
            "name": "bright",
            "kind": "room",
            "description": "bright lighting",
            "axis": "lighting",
            "deltas": {BOUND_KEY: 10},
        },
        {
            "name": "warm",
            "kind": "room",
            "description": "a warm room",
            "axis": "climate",
            "deltas": {BOUND_KEY: 20},
        },
        {
            "name": "vacation",
            "kind": "house",
            "description": "the house is away",
            "selections": {"foyer": {"lighting": "bright"}},
        },
    )


def _session(vocabulary: Vocabulary) -> OpenHouse:
    session = open_session(house=_inline_house(), vocabulary=vocabulary)
    session.add_profiles(_profiles())
    session.select_profile(room_id="garage", axis="lighting", name="bright")
    return session


def _keys(value: object) -> set[str]:
    """Every key anywhere in a document, however deeply nested."""
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {str(key) for key in mapping} | {
            nested for item in mapping.values() for nested in _keys(item)
        }
    if isinstance(value, list):
        items = cast("list[object]", value)
        return {nested for item in items for nested in _keys(item)}
    return set()


# --------------------------------------------------------------------------
# The identity, as a property
# --------------------------------------------------------------------------


@st.composite
def _house_documents(draw: st.DrawFn) -> dict[str, object]:
    """A house document the frozen schema and the slot vocabulary both admit."""
    room_ids = draw(
        st.lists(st.sampled_from(ROOM_IDS), min_size=1, max_size=3, unique=True)
    )
    rooms: list[dict[str, object]] = []
    for room_id in room_ids:
        slots = draw(st.lists(st.sampled_from(ROOM_SLOTS), max_size=3, unique=True))
        rooms.append(
            {
                "id": room_id,
                "name": room_id.replace("_", " ").title(),
                "type": room_id,
                "bindings": {
                    slot: {"entity_id": f"{DOMAINS[slot]}.{room_id}_{slot}"}
                    for slot in slots
                },
            }
        )
    scope = draw(st.lists(st.sampled_from(HOUSE_SLOTS), max_size=2, unique=True))
    return {
        "name": draw(st.text(alphabet="abcdefgh", min_size=1, max_size=8)),
        "rooms": rooms,
        "house_scope": {"slots": list(scope)},
    }


@st.composite
def _profile_sets(draw: st.DrawFn, schema: Mapping[str, object]) -> ProfileSet:
    """A set of room profiles, some of them selected, and an optional house one."""
    documents: list[dict[str, object]] = []
    count = draw(st.integers(min_value=0, max_value=3))
    for index in range(count):
        documents.append(
            {
                "name": f"p{index}",
                "kind": "room",
                "description": f"profile {index}",
                "axis": draw(st.sampled_from(("lighting", "climate"))),
                "deltas": {BOUND_KEY: draw(st.integers(min_value=1, max_value=500))},
            }
        )
    house_profile = draw(st.booleans())
    if house_profile:
        documents.append(
            {
                "name": "whole_house",
                "kind": "house",
                "description": "a whole-house profile",
                "selections": {},
                "deltas": {BOUND_KEY: draw(st.integers(min_value=1, max_value=500))},
            }
        )
    profiles = ProfileSet(documents, schema=schema)
    for document in documents:
        if document["kind"] != "room" or not draw(st.booleans()):
            continue
        room_id = draw(st.sampled_from(ROOM_IDS))
        profiles.select(
            room_id, cast("str", document["axis"]), cast("str", document["name"])
        )
    if house_profile:
        profiles.activate_house_profile("whole_house")
    return profiles


@given(document=_house_documents(), data=st.data())
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_export_then_import_is_the_identity(
    document: dict[str, object],
    data: st.DataObject,
    vocabulary: Vocabulary,
    profile_schema: Mapping[str, object],
    export_schema: Mapping[str, object],
) -> None:
    """The exit criterion: a configuration survives a write and a read unchanged."""
    house = House.from_document(document, vocabulary=vocabulary)
    profiles = data.draw(_profile_sets(profile_schema))
    modes = data.draw(
        st.lists(
            st.fixed_dictionaries(
                {
                    "name": st.sampled_from(("home", "away")),
                    "description": st.just("a mode"),
                }
            ),
            max_size=2,
            unique_by=lambda mode: mode["name"],
        )
    )

    exported = engine_export.export_backup(house=house, profiles=profiles, modes=modes)
    assert exported["format_version"] == engine_migrations.CURRENT_EXPORT_VERSION
    assert list(Draft202012Validator(dict(export_schema)).iter_errors(exported)) == []

    backup = engine_export.import_backup(
        exported, vocabulary=vocabulary, profile_schema=profile_schema
    )
    assert backup.house == house
    assert backup.profiles.to_document() == profiles.to_document()
    assert backup.modes == tuple(modes)


def test_the_facade_export_import_round_trip_is_the_identity(
    vocabulary: Vocabulary,
) -> None:
    session = _session(vocabulary)
    before = session.house
    active = session.active_profiles()

    document = session.export_backup()
    result = session.import_backup(document)

    assert result["undo"] is True
    assert session.house == before
    assert session.active_profiles() == active


# --------------------------------------------------------------------------
# The scrub: a shareable pack with no device ids
# --------------------------------------------------------------------------


def _manifest_errors(document: Mapping[str, object]) -> list[str]:
    """The manifest validated against the current schema, through the registry.

    The registry is the corpus one, because the current `pack-manifest` schema
    references `behavior-vocabulary` and a validator pointed at the file alone
    would resolve nothing and report a false failure.
    """
    schema = examples._current_schema("pack-manifest")
    validator = cast(
        "Validator", Draft202012Validator(schema, registry=examples._registry())
    )
    return [error.message for error in validator.iter_errors(cast("object", document))]


def test_a_scrubbed_template_is_a_pack_with_no_device_ids(
    vocabulary: Vocabulary,
) -> None:
    session = _session(vocabulary)
    pack = session.scrub_template(name="foyer_template", description="A foyer to adopt")

    assert _manifest_errors(pack.manifest) == []
    assert pack.manifest["kind"] == "house-template"
    assert pack.manifest["provides"] == [
        {"path": "foyer_template/template.json", "class": "template"}
    ]

    keys = _keys(pack.template)
    assert "entity_id" not in keys
    assert "registry_id" not in keys
    # What is kept is the shape a stranger can adopt: the rooms and their slots.
    assert _keys(pack.template) >= {"house", "rooms", "slots", "profiles", "active"}
    assert pack.template["rooms"][0]["slots"] == ["light_group", "motion_sensor"]


# --------------------------------------------------------------------------
# The dry run and the re-link
# --------------------------------------------------------------------------


def test_a_dry_run_of_the_same_document_reports_no_change(
    vocabulary: Vocabulary,
) -> None:
    session = _session(vocabulary)
    assert session.dry_run_import(session.export_backup()).empty


def test_a_dry_run_reports_a_change_without_making_one(
    vocabulary: Vocabulary,
) -> None:
    session = _session(vocabulary)
    document = copy.deepcopy(session.export_backup())
    document["house"] = "somewhere else"

    diff = session.dry_run_import(document)

    assert not diff.empty
    assert any(change.path == "/house" for change in diff.changes)
    # Nothing changed: the session still names the house it did before.
    assert session.house.name == "a round-tripping house"


def test_relinking_rewrites_what_the_map_covers_and_reports_the_rest() -> None:
    document = {
        "rooms": [
            {
                "id": "foyer",
                "bindings": {
                    "light_group": {
                        "registry_id": "foyer_main",
                        "entity_id": "light.foyer",
                    },
                    "lock": {
                        "registry_id": "front_door",
                        "entity_id": "lock.front",
                    },
                },
            }
        ]
    }
    result = engine_export.relink(document, {"foyer_main": "light.foyer_new"})

    assert result.relinked == ("foyer_main",)
    assert result.unresolved == ("front_door",)
    rebuilt = cast("Mapping[str, object]", result.document)
    bindings = cast(
        "Mapping[str, Mapping[str, Mapping[str, str]]]", rebuilt["rooms"][0]["bindings"]
    )
    assert bindings["light_group"]["entity_id"] == "light.foyer_new"
    assert bindings["lock"]["entity_id"] == "lock.front"


# --------------------------------------------------------------------------
# The import's edges
# --------------------------------------------------------------------------


def test_a_document_from_a_newer_build_is_refused(vocabulary: Vocabulary) -> None:
    session = _session(vocabulary)
    document = copy.deepcopy(session.export_backup())
    document["format_version"] = "9.9.9"

    with pytest.raises(engine_migrations.UnsupportedExportVersionError):
        session.import_backup(document)


def test_undo_puts_the_configuration_back(vocabulary: Vocabulary) -> None:
    session = _session(vocabulary)
    document = copy.deepcopy(session.export_backup())
    document["house"] = "somewhere else"

    session.import_backup(document)
    assert session.house.name == "somewhere else"

    session.undo_import()
    assert session.house.name == "a round-tripping house"


def test_undoing_an_import_that_never_happened_is_refused(
    vocabulary: Vocabulary,
) -> None:
    session = _session(vocabulary)
    with pytest.raises(packs.PackError):
        session.undo_import()


def test_the_exported_document_carries_every_room_and_profile(
    vocabulary: Vocabulary,
) -> None:
    session = _session(vocabulary)
    document = session.export_backup()

    assert [room["id"] for room in document["rooms"]] == ["foyer", "garage"]
    names = [profile["name"] for profile in document["profiles"]]
    assert names == ["bright", "vacation", "warm"]
    assert document["active"]["rooms"] == {"garage": {"lighting": "bright"}}


def test_the_exported_document_is_current_version(vocabulary: Vocabulary) -> None:
    session = _session(vocabulary)
    assert session.export_backup()["format_version"] == "1.1.0"
