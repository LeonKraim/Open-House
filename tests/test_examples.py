"""The hand-written examples, their allowlist and their schemas -- tasks 7.3, 7.4, 7.5.

Two things are proven here and they are different in kind.

The first is that the committed examples are really examples of their concept:
`example-house.yaml` is a house that the current house schema accepts,
`example-export.yaml` an export document the export schema accepts, and
`example-pack.yaml` a pack manifest the current `pack-manifest` schema accepts --
which, because that schema references `behavior-vocabulary`, is also where a pack
that declares a behaviour term the vocabulary does not carry is refused. That is
asserted against the *committed* tree, because an example that only validates
against a fixture schema proves nothing about the file the project ships.

The second is the `HANDWRITTEN` allowlist, and it is checked in both directions
on trees we build, because the committed tree is by construction the one where
the list is complete. A pack file present but unlisted, and a listed name with no
file behind it, are the two ways the marker rots; each has a failing fixture
naming the file, so the check is seen to fail and not only to pass.

The structural clause -- at least two room types, one optional slot bound and one
left unbound -- is read out of the committed example rather than asserted by the
check. `_optional_slots` below is why: "optional" is a property of the room type,
and the room types are only observably optional here because two rooms share a
type and differ on the slot. That construction is a property of *this example*,
and encoding it in the check would make the check a test of its own author.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import yaml
from jsonschema.validators import Draft202012Validator

from tools.catalog import examples, paths, schemas
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

#: The repository this file was imported from, captured before the `fake_root`
#: fixture redirects `paths`. The copy-based tests below need the committed
#: schema and example, which are only reachable here.
REAL_ROOT = paths.ROOT

HOUSE_PATH = "packs/official/example-house.yaml"
EXPORT_PATH = "packs/official/example-export.yaml"
PACK_PATH = "packs/official/example-pack.yaml"
HANDWRITTEN_PATH = "packs/official/HANDWRITTEN"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _minimal_schema(concept: str) -> str:
    document: dict[str, object] = {
        "$id": f"https://open-house.invalid/schemas/{concept}/1.0.0.json",
        "title": concept,
        "schema_version": "1.0.0",
        "supersedes": None,
        "type": "object",
    }
    return json.dumps(document, indent=2) + "\n"


def _seed(root: Path) -> None:
    """A tree where both examples validate and both are listed.

    Minimal schemas rather than the real ones, so a provenance test is not also
    a test of the house schema -- the isolation the golden-file fixtures also make
    for the rule list. The one exception is the binding test below, which copies
    the real export schema because the constraint it exercises is the schema's.
    """
    for concept in ("house", "export-document", "pack-manifest"):
        write(root, f"schemas/{concept}/1.0.0.json", _minimal_schema(concept))
    write(root, HOUSE_PATH, "name: A House\n")
    write(root, EXPORT_PATH, "format_version: 1.0.0\n")
    write(root, PACK_PATH, "name: example_pack\n")
    write(
        root,
        HANDWRITTEN_PATH,
        "example-house.yaml\nexample-export.yaml\nexample-pack.yaml\n",
    )


def _messages() -> str:
    report = Report()
    examples.check_examples(report)
    return report.render()


def _load_real(relative: str) -> object:
    return yaml.safe_load((REAL_ROOT / relative).read_text(encoding="utf-8"))


def _errors(concept: str, instance: object) -> list[str]:
    """The messages the current schema for a concept gives an instance.

    The house and export schemas are both self-contained -- every `$ref` they
    carry is a same-document `#/$defs/...` -- so validating against one needs no
    registry, and this uses the schema for the concept directly rather than the
    check's own resolver, which would test that resolver instead of the example.
    """
    current = schemas.current_version(schemas.load_versions(concept))
    assert current is not None, f"{concept} has no current version"
    validator = Draft202012Validator(current.document)
    return [error.message for error in validator.iter_errors(instance)]


def _current_pattern(concept: str, *path: str) -> str:
    """A `pattern` string from a runtime schema, read rather than restated.

    Reading it from the schema is the point: the test's job is to tie the example
    to the published schema, and a pattern copied into this file would be free to
    drift from the one it claims to check.
    """
    document = schemas.current_version(schemas.load_versions(concept))
    assert document is not None, f"{concept} has no current version"
    node: object = document.document
    for step in path:
        assert isinstance(node, dict), step
        node = node[step]
    assert isinstance(node, str), node
    return node


def _optional_slots(document: dict[str, object]) -> set[str]:
    """Slots bound in one room of a type and absent in another room of it.

    A slot the schema's room type provides may be absent, and that absence *is*
    how an optional slot is left unbound. Two rooms sharing a type make the
    difference observable without a room-type file: a slot that a required type
    would bind in both rooms is exactly the slot that one of them has and the
    other does not.
    """
    by_type: dict[str, list[set[str]]] = {}
    for room in document["rooms"]:
        assert isinstance(room, dict)
        by_type.setdefault(str(room["type"]), []).append(set(room["bindings"]))
    optional: set[str] = set()
    for bound_sets in by_type.values():
        for slot in set().union(*bound_sets):
            bound_in = [slot in bound for bound in bound_sets]
            if any(bound_in) and not all(bound_in):
                optional.add(slot)
    return optional


# --------------------------------------------------------------------------
# The committed examples
# --------------------------------------------------------------------------


def test_the_committed_examples_pass_their_checks() -> None:
    report = Report()
    examples.check_examples(report)
    assert report.ok, report.render()


def test_the_example_house_validates_against_the_house_schema() -> None:
    assert _errors("house", _load_real(HOUSE_PATH)) == []


def test_the_example_house_exercises_at_least_two_room_types() -> None:
    document = _load_real(HOUSE_PATH)
    assert isinstance(document, dict)
    types = {room["type"] for room in document["rooms"]}
    assert len(types) >= 2, types


def test_the_example_house_binds_one_optional_slot_and_leaves_one_unbound() -> None:
    document = _load_real(HOUSE_PATH)
    assert isinstance(document, dict)
    optional = _optional_slots(document)
    assert optional, "no optional slot is exercised at all"

    rooms = document["rooms"]
    assert isinstance(rooms, list)
    bound_optional = any(
        slot in room["bindings"] for room in rooms for slot in optional
    )
    unbound_optional = any(
        slot not in room["bindings"] for room in rooms for slot in optional
    )
    assert bound_optional, "no optional slot is bound anywhere"
    assert unbound_optional, "no optional slot is left unbound anywhere"


def test_the_example_names_are_valid_against_the_room_type_and_slot_schemas() -> None:
    """The house schema names room types and slots without constraining them.

    `type` is patterned by the house schema, but a binding's slot key is not
    constrained there at all -- `bindings` is an object whose *values* are
    checked and whose keys are free. So the slot names are held to the slot
    schema, and the room types to the room-type schema, which is the sense in
    which the example validates against all three schemas rather than only the
    one it is a document of.
    """
    document = _load_real(HOUSE_PATH)
    assert isinstance(document, dict)
    type_pattern = re.compile(
        _current_pattern("room-type", "properties", "name", "pattern")
    )
    slot_pattern = re.compile(_current_pattern("slot", "properties", "name", "pattern"))
    for room in document["rooms"]:
        assert isinstance(room, dict)
        assert type_pattern.match(room["type"]), room["type"]
        for slot in room["bindings"]:
            assert slot_pattern.match(slot), slot


def test_the_example_export_validates_against_the_export_document_schema() -> None:
    assert _errors("export-document", _load_real(EXPORT_PATH)) == []


def _pack_report(document: object) -> Report:
    """Validate a pack document against the committed `pack-manifest` schema.

    Through the check's own resolver rather than `_errors`, because the current
    `pack-manifest` schema is not self-contained: its behaviour terms are a `$ref`
    into `behavior-vocabulary`, so applying it needs the registry that resolves a
    reference into another committed schema. `PACK_PATH` is the location, so a
    finding names the pack the reader is looking at.
    """
    report = Report()
    examples._apply(
        report,
        PACK_PATH,
        document,
        examples._current_schema("pack-manifest"),
        examples._registry(),
    )
    return report


def test_the_example_pack_validates_against_the_manifest_and_vocabulary() -> None:
    """Task 7.4's admitting case, on the committed pack and schemas."""
    document = _load_real(PACK_PATH)
    assert isinstance(document, dict)
    assert _pack_report(document).ok, _pack_report(document).render()


def test_the_example_pack_declares_a_behaviour_outside_the_vocabulary_fails() -> None:
    """Task 7.4's failing scenario: the pack is named, and so is the term.

    `teleport` is an action no estate used, so it is not one `behavior-vocabulary`
    `1.1.0` publishes, so a pack that declares it is not a pack this vocabulary
    admits. The diagnostic names both the pack and the term, because a message
    that said only "an action is unknown" would leave the reader to find the
    behaviour by hand -- which is the work a diagnostic exists to do. The action
    is read from the committed pack and spoiled here, so the fixture cannot drift
    from the file it exercises.
    """
    document = _load_real(PACK_PATH)
    assert isinstance(document, dict)
    behaviours = document["behaviours"]
    assert isinstance(behaviours, list)
    first = behaviours[0]
    assert isinstance(first, dict)
    first["action"] = "teleport"

    rendered = _pack_report(document).render()
    assert PACK_PATH in rendered
    assert "teleport" in rendered


def test_the_export_schema_requires_a_registry_id_on_every_binding() -> None:
    """The shape rule itself, on the real schema rather than a restated one."""
    document = _load_real(EXPORT_PATH)
    binding = document["bindings"][0]
    binding.pop("registry_id")
    messages = _errors("export-document", document)
    assert any("registry_id" in message for message in messages), messages


def test_an_export_binding_with_only_entity_id_fails_naming_the_binding(
    fake_root: Path,
) -> None:
    """Task 7.5's failing scenario, on the real export schema.

    The schema is copied from the committed tree rather than restated, because
    the constraint being exercised *is* the schema's: `registry_id` is required
    so that a binding carrying only `entity_id` cannot be re-imported after a
    re-pair. A minimal fixture schema could not fail this, and a schema that
    dropped the requirement would make the test pass while the export stopped
    being re-importable.
    """
    schema_text = (REAL_ROOT / "schemas/export-document/1.0.0.json").read_text(
        encoding="utf-8"
    )
    write(fake_root, "schemas/export-document/1.0.0.json", schema_text)
    write(fake_root, "schemas/house/1.0.0.json", _minimal_schema("house"))

    document = _load_real(EXPORT_PATH)
    assert isinstance(document, dict)
    bindings = document["bindings"]
    assert isinstance(bindings, list)
    first = bindings[0]
    assert isinstance(first, dict)
    first.pop("registry_id")

    write(fake_root, EXPORT_PATH, yaml.safe_dump(document, sort_keys=False))
    write(fake_root, HOUSE_PATH, "name: A House\n")
    write(fake_root, HANDWRITTEN_PATH, "example-house.yaml\nexample-export.yaml\n")

    rendered = _messages()
    assert "bindings/0" in rendered
    assert "registry_id" in rendered


# --------------------------------------------------------------------------
# The allowlist, both directions
# --------------------------------------------------------------------------


def test_a_pack_file_present_but_unlisted_names_the_file(fake_root: Path) -> None:
    _seed(fake_root)
    write(fake_root, "packs/official/extra.yaml", "name: Extra\n")

    rendered = _messages()
    assert "packs/official/extra.yaml" in rendered
    assert "not named in packs/official/HANDWRITTEN" in rendered


def test_a_listed_name_with_no_file_names_the_missing_file(fake_root: Path) -> None:
    _seed(fake_root)
    write(
        fake_root,
        HANDWRITTEN_PATH,
        "example-house.yaml\nexample-export.yaml\nexample-pack.yaml\n"
        "example-ghost.yaml\n",
    )

    rendered = _messages()
    assert "names `example-ghost.yaml`" in rendered
    assert "is not under packs/official/" in rendered


def test_the_example_house_absent_from_handwritten_fails(fake_root: Path) -> None:
    """Task 7.3's scenario: the file is there, the claim is not.

    The export is left listed so that the one finding is the house and not a
    cascade, and a negative test with more than one finding would go on passing
    after the behaviour it means to test had been removed.
    """
    _seed(fake_root)
    write(fake_root, HANDWRITTEN_PATH, "example-export.yaml\n")

    report = Report()
    examples.check_examples(report)
    findings = [d for d in report.diagnostics if d.where == HOUSE_PATH]
    assert len(findings) == 1, report.render()
    assert "not named in packs/official/HANDWRITTEN" in findings[0].message


def test_a_missing_allowlist_is_reported_rather_than_read_as_empty(
    fake_root: Path,
) -> None:
    """An absent allowlist is not a list that names nothing.

    Read as empty, it would report every pack file as unlisted -- a cascade of
    true-sounding findings derived from a file that could not be opened.
    """
    _seed(fake_root)
    (fake_root / HANDWRITTEN_PATH).unlink()

    rendered = _messages()
    assert "packs/official/HANDWRITTEN" in rendered
    assert "is missing" in rendered


def test_a_missing_example_is_reported_naming_the_file(fake_root: Path) -> None:
    _seed(fake_root)
    (fake_root / HOUSE_PATH).unlink()

    rendered = _messages()
    assert HOUSE_PATH in rendered
    assert "is missing" in rendered
