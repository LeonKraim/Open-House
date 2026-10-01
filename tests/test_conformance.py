"""The conformance check -- section 7, task 7.6.

A runtime concept's shape has one home: the version files under
`schemas/<concept>/`. Everywhere else the shape is *used*, by a `$ref`, and a
file that instead copies the shape's fields is a second declaration that receives
none of the runtime schema's later edits. Nothing else compares the two, so the
copy drifts in silence while every check stays green. The scenarios here are the
two the spec names -- any file outside a version directory that declares a shape,
and a catalog schema that restates one -- plus the admitting direction, because a
check that only ever rejected would also reject the `$ref` it is supposed to
allow.

The check is not yet in `tools/catalog/validate.py`'s registry, which the
coordinator owns, so these tests call `check_conformance` directly rather than
going through `validate_all`. That is the only difference from `tests/test_golden.py`;
the registration is reported rather than made, and the committed-tree test below
is the half that keeps the check honest against the real tree.

Deferred, and named here so the absence is a decision: the pattern-level
restatement -- a file inlining only the slot `name` pattern, say -- is not caught,
because that one property is required by all three concepts and no single concept
could be named as duplicated. The boundary is stated in the module docstring.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import conformance, paths
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

CHECK = conformance.CONFORMANCE_CHECK

#: The identifying property sets of the three shaped concepts, as the runtime
#: schemas declare them. Written here so a fixture can build a *copy* that the
#: check must recognise; the check itself reads the same names from the schemas.
SLOT = ("name", "accepts_domains", "required", "source_repos", "examples")
ROOM_TYPE = ("name", "default", "source_rooms", "provides_slots")
HOUSE = ("name", "rooms", "house_scope")

SHAPES = {"slot": SLOT, "room-type": ROOM_TYPE, "house": HOUSE}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _runtime_document(
    concept: str, version: str, required: tuple[str, ...], supersedes: str | None
) -> str:
    document: dict[str, object] = {
        "$id": f"https://open-house.invalid/schemas/{concept}/{version}.json",
        "title": concept,
        "schema_version": version,
        "supersedes": supersedes,
        "type": "object",
        "required": list(required),
        "properties": {name: {"type": "string"} for name in required},
    }
    return json.dumps(document, indent=2) + "\n"


def _write_runtime(
    root: Path,
    concept: str,
    version: str = "1.0.0",
    required: tuple[str, ...] | None = None,
    supersedes: str | None = None,
) -> None:
    """A current runtime version file for a concept, declaring only its shape."""
    names = required if required is not None else SHAPES[concept]
    write(
        root,
        f"schemas/{concept}/{version}.json",
        _runtime_document(concept, version, names, supersedes),
    )


def _seed_shapes(root: Path) -> None:
    """Give each shaped concept a current version, so a fixture starts complete."""
    for concept in conformance.SHAPED_CONCEPTS:
        _write_runtime(root, concept)


def _copy(names: tuple[str, ...]) -> dict[str, object]:
    """A JSON Schema object that restates a concept shape rather than referencing it."""
    return {
        "type": "object",
        "required": list(names),
        "properties": {name: {"type": "string"} for name in names},
    }


def _write_pack(root: Path, names: tuple[str, ...], name: str = "restated") -> str:
    """A pack file carrying a schema object that copies `names`."""
    relative = f"packs/official/{name}.yaml"
    write(root, relative, yaml.safe_dump({"shape": _copy(names)}, sort_keys=False))
    return relative


def _write_catalog_copy(root: Path, names: tuple[str, ...], stem: str = "slots") -> str:
    """A catalog schema whose item schema restates `names` instead of referencing."""
    relative = f"schemas/catalog/{stem}.json"
    document: dict[str, object] = {
        "$id": f"https://open-house.invalid/schemas/catalog/{stem}.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "A restated shape",
        "schema_version": "1.0.0",
        "type": "object",
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": _copy(names)}},
    }
    write(root, relative, json.dumps(document, indent=2) + "\n")
    return relative


def _report() -> Report:
    report = Report()
    conformance.check_conformance(report)
    return report


def _diagnostics() -> list[tuple[str, str]]:
    return [(d.where, d.message) for d in _report().diagnostics]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# A file outside a runtime version directory that declares a shape
# --------------------------------------------------------------------------


@pytest.mark.parametrize("concept", ["slot", "room-type", "house"])
def test_a_pack_that_restates_a_concept_shape_is_named(
    fake_root: Path, concept: str
) -> None:
    """Task 7.6's failing fixture in a pack.

    A pack is data, so the only way for one to declare a shape is to carry a
    schema object inline -- which is what a pack author reaching for a slot type
    would write. The finding has to name both the file and the schema it
    duplicates, or a reader is told something is wrong without being told against
    what.
    """
    _seed_shapes(fake_root)
    relative = _write_pack(fake_root, SHAPES[concept])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where == relative
    assert f"`{concept}` shape" in message
    assert f"schemas/{concept}/1.0.0.json" in message


def test_a_second_shape_in_one_file_is_still_one_finding(
    fake_root: Path,
) -> None:
    """The file and the schema are what is named, so two copies in one file name
    the same two things and are reported once rather than twice."""
    _seed_shapes(fake_root)
    relative = "packs/official/twice.yaml"
    write(
        fake_root,
        relative,
        yaml.safe_dump({"first": _copy(SLOT), "second": _copy(SLOT)}, sort_keys=False),
    )

    assert [where for where, _ in _diagnostics()] == [relative]


def test_a_runtime_version_file_of_another_concept_is_not_exempt(
    fake_root: Path,
) -> None:
    """The exemption is the concept's own directory, not every version directory.

    A file under `schemas/mode/` is a runtime version file and is still in scope
    for `slot`: the rule is that a shape lives in *its* directory, so a slot shape
    in the wrong runtime directory is the same drift as a slot shape in a pack.
    """
    _seed_shapes(fake_root)
    write(
        fake_root,
        "schemas/mode/1.0.0.json",
        _runtime_document("mode", "1.0.0", SLOT, None),
    )

    findings = _diagnostics()
    assert [where for where, _ in findings] == ["schemas/mode/1.0.0.json"]
    assert "schemas/slot/1.0.0.json" in findings[0][1]


# --------------------------------------------------------------------------
# A catalog schema that restates a runtime concept
# --------------------------------------------------------------------------


@pytest.mark.parametrize("concept", ["slot", "room-type"])
def test_a_catalog_schema_that_restates_a_shape_is_named(
    fake_root: Path, concept: str
) -> None:
    """The spec's second scenario, and the reason the scope includes `schemas/catalog/`.

    `schemas/catalog/` sits inside `schemas/`, so a scope of "outside `schemas/`"
    would exempt the one directory a catalog shape could actually hide in. The
    copy here is one level nested under `items`, which is where a real restatement
    would sit.
    """
    _seed_shapes(fake_root)
    stem = "slots" if concept == "slot" else "room_types"
    relative = _write_catalog_copy(fake_root, SHAPES[concept], stem=stem)

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where == relative
    assert f"schemas/{concept}/1.0.0.json" in message


def test_a_catalog_schema_that_references_the_runtime_shape_passes(
    fake_root: Path,
) -> None:
    """The admitting direction, and the clause the check's scope turns on.

    A `$ref` carries no `properties`, so the correct way to use a shape is
    invisible to a rule keyed on declared property names. A rule broad enough to
    fail this would make the check unusable, which is why the test asserts the
    absence of a finding rather than only its presence elsewhere.
    """
    _seed_shapes(fake_root)
    document: dict[str, object] = {
        "$id": "https://open-house.invalid/schemas/catalog/slots.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Slot vocabulary",
        "schema_version": "1.0.0",
        "type": "object",
        "required": ["slots"],
        "properties": {
            "slots": {"type": "array", "items": {"$ref": "../slot/1.0.0.json"}}
        },
    }
    write(
        fake_root,
        "schemas/catalog/slots.json",
        json.dumps(document, indent=2) + "\n",
    )

    assert _diagnostics() == [], _messages()


# --------------------------------------------------------------------------
# The passing fixture: a runtime version file
# --------------------------------------------------------------------------


def test_a_runtime_version_file_may_declare_its_own_shape(fake_root: Path) -> None:
    """A runtime version file is the one place its shape belongs, so it passes.

    Every seeded version file below declares its concept's required set, and the
    check must have nothing to say -- otherwise the check would forbid the shape
    from existing at all.
    """
    _seed_shapes(fake_root)
    assert _diagnostics() == [], _messages()


def test_a_second_version_may_declare_the_same_shape(fake_root: Path) -> None:
    """The exemption is the directory, not one filename.

    A concept is expected to gain versions -- the vocabulary gains a `1.1.0` in
    task 7.1 -- so every version file under the directory is the shape's home, and
    a check that exempted only the current file would fail the first successor.
    """
    _seed_shapes(fake_root)
    _write_runtime(fake_root, "slot", "1.1.0", supersedes="1.0.0")

    assert _diagnostics() == [], _messages()


def test_a_single_shared_property_is_not_a_restated_shape(fake_root: Path) -> None:
    """The boundary the module docstring states, driven rather than described.

    `name` is required by all three concepts, so a schema declaring only `name`
    cannot be attributed to one of them; the check declines rather than guess.
    This is the case a pattern-equality rule would have caught and mis-attributed.
    """
    _seed_shapes(fake_root)
    write(
        fake_root,
        "schemas/catalog/thing.json",
        json.dumps({"type": "object", "properties": {"name": {"type": "string"}}})
        + "\n",
    )

    assert _diagnostics() == [], _messages()


# --------------------------------------------------------------------------
# The signature is read from the runtime schema, not kept here
# --------------------------------------------------------------------------


def test_the_signature_comes_from_the_current_runtime_schema(fake_root: Path) -> None:
    """The shape is whatever the current version requires, and nothing is copied.

    A signature written into the check would be a second declaration of the shape
    -- the very drift the check exists to catch. So a runtime schema with an
    unexpected required set moves the check with it: the copy of the *new* set is
    caught and the copy of the old, real set is not.
    """
    _write_runtime(fake_root, "room-type")
    _write_runtime(fake_root, "house")
    _write_runtime(fake_root, "slot", required=("widget", "gadget"))

    write(
        fake_root,
        "packs/official/new.yaml",
        yaml.safe_dump({"shape": _copy(("widget", "gadget"))}, sort_keys=False),
    )
    write(
        fake_root,
        "packs/official/old.yaml",
        yaml.safe_dump({"shape": _copy(SLOT)}, sort_keys=False),
    )

    findings = _diagnostics()
    assert [where for where, _ in findings] == ["packs/official/new.yaml"]
    assert "schemas/slot/1.0.0.json" in findings[0][1]


# --------------------------------------------------------------------------
# Fatal conditions, absence of a clone, and files that will not parse
# --------------------------------------------------------------------------


def test_a_concept_with_no_current_version_is_fatal(fake_root: Path) -> None:
    """Without a current version there is no shape to conform to.

    The check raises rather than passing: a tree whose shapes are undefined is
    exactly the state in which "no file restates the shape" is vacuously true and
    therefore worthless. The failure names the concept, not a count.
    """
    _write_runtime(fake_root, "slot")
    _write_runtime(fake_root, "room-type")

    with pytest.raises(CheckError) as raised:
        conformance.check_conformance(Report())
    assert raised.value.where == "schemas/house/"
    assert "house" in raised.value.message


def test_a_document_that_will_not_parse_is_skipped(fake_root: Path) -> None:
    """A document that will not parse is skipped, not reported or raised on.

    A file the loader refuses declares no schema for this check to read, so it
    yields no finding; reporting it instead would be a conformance finding about a
    document this check never read.
    """
    _seed_shapes(fake_root)
    write(fake_root, "packs/official/broken.yaml", "shape: [\n")

    assert _diagnostics() == [], _messages()


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/` and no `.local/`, and nothing here reads one."""
    assert not paths.RESSOURCES.exists()
    assert not paths.LOCAL.exists()

    _seed_shapes(fake_root)
    _write_pack(fake_root, HOUSE)
    assert "`house` shape" in _messages()


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def test_the_committed_tree_declares_each_shape_once(real_root: Path) -> None:
    """The shipping tree: every catalog schema references the runtime shapes.

    This is the half a fixture cannot stand in for -- a tree built to look like
    the repository cannot tell us the repository is clean.
    """
    assert _diagnostics() == [], _messages()
