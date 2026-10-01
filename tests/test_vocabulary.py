"""The published behaviour vocabulary -- task 7.1.

Task 7.1 asks for one thing the whole project has been promising and not yet
delivering: the vocabulary stops being a shape and starts carrying *terms*.
`1.0.0` declares that a trigger is a lower-snake-case string and enumerates
nothing, because the terms are learned from the four reference estates in stage
A and are not knowable when a shape is frozen. `1.1.0` carries the terms the
extraction found, and it does so the only way a published schema may change: by
being a new file that names the one it replaces.

Four claims are proven here, and the fourth is the one with teeth.

The first is succession. `1.1.0` is current, it names `1.0.0` in its `supersedes`,
and the arrow points only backwards. The second is that `1.0.0` is still present
and *byte-identical* to what the commit that introduced it (task 1.5) stored --
which is exactly the assertion that fails if `1.1.0` were published by editing
`1.0.0.json` instead of by adding a file. That is the mistake the whole
backwards-succession design exists to make unnecessary, so a test that only
checked "1.1.0 exists and is current" would pass on the very edit the rule
forbids.

The third is that the published terms are the extraction's terms, and that the
generated record schema carries them. The vocabulary is not a document written to
taste: its enums are read back out of `catalog/raw-behaviors.json`, the facts
store the extraction produced, and the record schema generated from the allowlist
is required to have taken them from the vocabulary rather than from a pattern. A
vocabulary the extraction does not corroborate, or a regenerated schema that
still admits any string, fails here.

The fourth is the pack clause, and it is deliberately run against the *committed*
vocabulary rather than a fixture. A pack declares its behaviours in the
vocabulary, and a behaviour whose action is not a published action term must
fail, naming the pack and the term. There is no pack validator yet -- task 7.4
owns it, and `example-pack.yaml` is withheld until then -- so this test supplies
the smallest schema that reaches the published element: a pack schema whose
behaviour `action` is a `$ref` into `behavior-vocabulary/1.1.0.json`, resolved
through the vocabulary's own `$id`. The term list is therefore never restated in
this file; drop `service` from the vocabulary and the positive control below
fails with it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import yaml
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012, Schema

from tools.catalog import paths, schemas, vcs
from tools.catalog.errors import read_text

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

#: The repository this file was imported from, captured before any fixture
#: redirects `paths`. The pack test below reads the *committed* vocabulary, which
#: a fixture root cannot supply.
REAL_ROOT = paths.ROOT

CONCEPT = "behavior-vocabulary"

#: The version this task publishes, and the one it names. Spelled once so that a
#: later bump is a change to these two lines and not to the prose of every test.
PUBLISHED = "1.1.0"
PREDECESSOR = "1.0.0"

#: The three axes the extraction derives terms for. `modes` and `profiles` are
#: not here: the extraction derives no term for either, so `1.1.0` carries them
#: by `$ref` to `1.0.0`'s pattern and there is nothing to compare.
AXES = ("triggers", "conditions", "actions")

#: The facts store, whose records carry the terms the extraction actually found.
FACTS = "catalog/raw-behaviors.json"

#: The generated record schema, which takes its three vocabulary fields from the
#: current vocabulary element and so must be regenerated when the vocabulary is
#: published.
RECORD_SCHEMA = "schemas/catalog/raw-behaviors.json"

#: The pack the failing case is written as. The file lives in a temporary tree,
#: never in the repository: task 7.4 owns the committed `example-pack.yaml`, and
#: writing it here would publish a pack against a validator that does not exist.
PACK_PATH = "packs/official/example-pack.yaml"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _published() -> schemas.SchemaVersion:
    """The current version of the vocabulary, or a named failure."""
    current = schemas.current_version(schemas.load_versions(CONCEPT))
    assert current is not None, f"{CONCEPT} has no single current version"
    return current


def _axis_enum(properties: object, axis: str) -> list[str]:
    """The published term list of one axis, read out of the schema.

    Read from the `items` enum rather than restated: this is the same element the
    generator resolves (`facts._vocabulary_element`), so a test that copied the
    terms into a tuple would keep passing after the vocabulary gained or lost one.
    """
    assert isinstance(properties, dict), axis
    node = properties[axis]
    assert isinstance(node, dict), axis
    items = node["items"]
    assert isinstance(items, dict), axis
    enum = items["enum"]
    assert isinstance(enum, list), axis
    return [term for term in enum if isinstance(term, str)]


def _observed_terms(path: Path) -> dict[str, set[str]]:
    """Every trigger, condition and action term the extraction recorded."""
    loaded: object = json.loads(path.read_bytes().decode("utf-8"))
    assert isinstance(loaded, dict)
    records = loaded["records"]
    assert isinstance(records, list)
    observed: dict[str, set[str]] = {axis: set() for axis in AXES}
    for record in records:
        assert isinstance(record, dict)
        for axis in AXES:
            terms = record.get(axis)
            assert isinstance(terms, list), axis
            observed[axis].update(term for term in terms if isinstance(term, str))
    return observed


def _pack_schema(uri: str) -> dict[str, object]:
    """The smallest pack schema whose behaviour action is the published element.

    The `$ref` is a JSON pointer into the vocabulary document, so the set of
    actions a pack may use is whatever `1.1.0` publishes. A pack validator written
    in task 7.4 will reach the same element; this file reaches it early so the
    clause can be proven before the validator exists.
    """
    return {
        "type": "object",
        "required": ["behaviours"],
        "properties": {
            "behaviours": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {"$ref": f"{uri}#/properties/actions/items"}
                    },
                },
            }
        },
    }


def _validate_pack(path: Path, relative: str) -> list[str]:
    """Diagnostics for a pack file, each located by the pack it names.

    The location is the pack's path followed by the JSON pointer to the failing
    term, which is the shape a real pack diagnostic has: the reader is told which
    pack failed and where in it, not merely that some action is unknown. The
    relative name is passed in because the pack lives in a temporary tree, so
    `paths.ROOT` is the repository and the fixture is not under it.
    """
    document = _published().document
    uri = document["$id"]
    assert isinstance(uri, str)
    registry: Registry[Schema] = Registry().with_resource(
        uri, Resource(contents=document, specification=DRAFT202012)
    )
    validator = cast(
        "Validator", Draft202012Validator(_pack_schema(uri), registry=registry)
    )
    instance: object = yaml.safe_load(path.read_bytes().decode("utf-8"))
    errors = sorted(
        validator.iter_errors(cast("Any", instance)),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    return [
        f"{relative}: "
        f"{'/'.join(str(part) for part in error.absolute_path) or '<document>'}: "
        f"{error.message}"
        for error in errors
    ]


# --------------------------------------------------------------------------
# Succession
# --------------------------------------------------------------------------


def test_the_published_version_is_current() -> None:
    """1.1.0 is the version a resolver would reach, and it is the only one."""
    versions = schemas.load_versions(CONCEPT)
    assert [version.version for version in versions] == [PREDECESSOR, PUBLISHED]

    current = _published()
    assert current.version == PUBLISHED
    assert current.document["schema_version"] == PUBLISHED


def test_the_successor_names_the_version_it_replaces() -> None:
    """The arrow points backwards, and only backwards."""
    versions = {version.version: version for version in schemas.load_versions(CONCEPT)}

    assert versions[PUBLISHED].supersedes == PREDECESSOR
    # The retired version names nothing: retiring it is the *absence* of a
    # successor naming it, so publishing 1.1.0 wrote nothing into 1.0.0 at all.
    assert versions[PREDECESSOR].supersedes is None
    assert "superseded_by" not in versions[PREDECESSOR].document


def test_the_predecessor_is_present_and_byte_identical(real_root: Path) -> None:
    """1.0.0 is presence *and* content, not merely presence.

    Byte-identity against the committing blob is the clause the backwards arrow
    exists to protect, and it is the one assertion here that fails on the edit
    the requirement names as unacceptable: publishing 1.1.0 by rewriting 1.0.0's
    term lists in place. Presence alone would pass on that edit, because the file
    would still be there.
    """
    relative = f"schemas/{CONCEPT}/{PREDECESSOR}.json"
    path = real_root / relative
    assert path.is_file(), f"{relative} is absent; a superseded version is retained"

    commit = vcs.introducing_commit(relative)
    assert commit is not None, (
        f"{relative} has no introducing commit, so there is nothing to compare a "
        "publication against"
    )
    original = vcs.file_at(commit, relative)
    assert original is not None, f"{relative} is unreadable from {commit[:8]}"
    assert read_text(path) == original, (
        f"{relative} differs from its content at {commit[:8]}; a published schema "
        "version is immutable and a change must be published as a new file"
    )


# --------------------------------------------------------------------------
# The terms, and the schema generated from them
# --------------------------------------------------------------------------


def test_every_published_term_is_one_the_extraction_found(real_root: Path) -> None:
    """The enums are read back out of the facts store, not written to taste.

    The extraction is the source of the terms; the vocabulary is where they are
    frozen. An enum here that the extraction does not corroborate -- a guessed
    term, or one dropped when the corpus changed -- is a claim the corpus does
    not support, and it fails the moment the two are compared.
    """
    properties = _published().document["properties"]
    observed = _observed_terms(real_root / FACTS)
    for axis in AXES:
        assert observed[axis], f"the extraction recorded no {axis} term at all"
        assert _axis_enum(properties, axis) == sorted(observed[axis]), axis


def test_the_record_schema_carries_the_published_terms(real_root: Path) -> None:
    """The generated schema took its three fields from the vocabulary.

    Before this task the record schema admitted any lower-snake-case string for
    each axis, because `1.0.0` did; after it, the fields are the published enums.
    The generated artifact is regenerated in the same commit for exactly this
    reason, so the committed record schema and the vocabulary agree -- and a
    stale regeneration, which still carried the pattern, fails here.
    """
    published = _published().document["properties"]
    loaded: object = json.loads(
        (real_root / RECORD_SCHEMA).read_bytes().decode("utf-8")
    )
    assert isinstance(loaded, dict)
    defs = loaded["$defs"]
    assert isinstance(defs, dict)
    raw_record = defs["raw_record"]
    assert isinstance(raw_record, dict)
    record_properties = raw_record["properties"]

    for axis in AXES:
        assert _axis_enum(record_properties, axis) == _axis_enum(published, axis), axis


# --------------------------------------------------------------------------
# A pack outside the vocabulary
# --------------------------------------------------------------------------


def test_a_pack_using_an_out_of_vocabulary_action_fails_naming_the_pack(
    tmp_path: Path,
) -> None:
    """The clause: the pack is named, and so is the term it used.

    `teleport` is not an action any estate used, so it is not one `1.1.0`
    publishes, so a pack that declares it is not a pack this vocabulary admits.
    The diagnostic names both the pack and the term, because a message that said
    only "an action is unknown" would leave the reader to find the behaviour by
    hand -- which is the work a diagnostic exists to do.
    """
    write(tmp_path, PACK_PATH, "behaviours:\n  - action: teleport\n")

    rendered = _validate_pack(tmp_path / PACK_PATH, PACK_PATH)
    assert len(rendered) == 1, rendered
    assert PACK_PATH in rendered[0]
    assert "teleport" in rendered[0]


def test_a_pack_using_a_published_action_passes(tmp_path: Path) -> None:
    """The admitting case, so the clause above cannot be satisfied by rejecting
    every pack. The term is read from the vocabulary rather than chosen here, so
    the control is tied to the published list and not to this file's taste.
    """
    term = _axis_enum(_published().document["properties"], "actions")[0]
    write(tmp_path, PACK_PATH, f"behaviours:\n  - action: {term}\n")

    assert _validate_pack(tmp_path / PACK_PATH, PACK_PATH) == []
