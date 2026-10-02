"""The catalog data set and the schemas that validate it -- task 1.6.

Two claims live here and they are different claims. One is about the committed
stubs: that every one of them validates against its schema, which is what makes
`catalog/` a directory of checked files rather than a directory of YAML. The
other is about the check itself, which is the weaker thing to leave untested.

The distinction matters because a check can pass every stub while looking at
none of them. Asserting that a file *has* a schema next to it satisfies "every
stub passes" on an empty file set and on every file set, because the assertion
never opens the data. So the tests below exercise the check on trees built to
violate it, in the fixtures-both-ways arrangement `conftest` describes: a check
seen only to pass on the committed tree is a check nobody has seen fail.

Deferred, and where it lands: 1.6's clause "a catalog schema redefining the slot
shape fails the conformance check" is task 7.6's, whose text puts
`schemas/catalog/` in scope precisely because it is the one place a concept shape
could be restated. Nothing here asserts it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tools.catalog import facts, paths, validate
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

CATALOG_CHECK = "catalog-schema"

#: The catalog data files, by stem. Named rather than discovered, because a test
#: that reads the answer out of the directory it is checking agrees with any
#: directory, including one where a stub was never written.
#:
#: Tasks 1.6 and 2.x wrote all but the four `golden_*` ones, which are section 3's
#: and the first here that are not stubs: a stub is an empty document waiting for
#: an extractor, and a golden file is hand-written data that is complete on the
#: day it lands. They are in the same tuple because the tuple is the set of files
#: the schema pairing has to hold for, and that set does not care which task
#: wrote one. `pack-policy` is Phase 2's and is no stub either: it is complete on
#: the day it lands, like a golden file, and it is here for the same reason -- the
#: pairing has to hold for it. `services` is Phase 2's too, for the same reason:
#: the service-to-state table is complete on its first day, because a row is a
#: judgement about a service rather than a measurement of a corpus.
STUBS = (
    "behaviors",
    "edge_cases",
    "file_rules",
    "golden_ccostan",
    "golden_fwartner",
    "golden_johnkoht",
    "golden_renemarc",
    "hardcoded_refs",
    "integrations",
    "inventory",
    "inventory_exceptions",
    "licenses",
    "overlap_exceptions",
    "pack-policy",
    "pain_points",
    "raw-behaviors",
    "repos",
    "room_types",
    "rooms",
    "services",
    "slots",
)

#: Schemas written in the first pass whose data files arrive in section 3, when
#: there is an extraction to emit. They are listed because their presence ahead
#: of their data is the ordering task 1.6 asks for -- the schema exists before
#: the file it describes, so the file cannot be written into an unchecked gap.
#:
#: `inventory` left this list when task 3.3 generated it and `raw-behaviors` when
#: task 3.5 wrote it, which is the list working as intended: the entry is removed
#: by the task that fills the gap, and until then it is the record that the gap
#: was deliberate. It is empty now, and kept -- a tuple nobody has to re-invent
#: the next time a schema lands ahead of its data.
DEFERRED_DATA: tuple[str, ...] = ()

_DRAFT = '"$schema": "https://json-schema.org/draft/2020-12/schema"'

_URI_PREFIX = "https://open-house.invalid/schemas/"
SLOTS_URI = f"{_URI_PREFIX}catalog/slots.json"
HARDCODED_REFS_URI = f"{_URI_PREFIX}catalog/hardcoded_refs.json"
SLOT_URI = f"{_URI_PREFIX}slot/1.0.0.json"

_SLOT_SCHEMA = f"""{{
  "$id": "{SLOT_URI}",
  {_DRAFT},
  "schema_version": "1.0.0",
  "type": "object",
  "properties": {{"name": {{"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}}}}
}}
"""


def _seed_schema(root: Path, stem: str, body: str) -> None:
    write(root, f"schemas/catalog/{stem}.json", body)


def _seed_data(root: Path, stem: str, body: str) -> None:
    write(root, f"catalog/{stem}.yaml", body)


def _diagnostics() -> list[tuple[str, str]]:
    report = Report()
    validate.check_catalog_data_files(report)
    return [(d.where, d.message) for d in report.diagnostics]


def _only(diagnostics: list[tuple[str, str]], where: str) -> str:
    messages = [message for name, message in diagnostics if name == where]
    assert len(messages) == 1, (
        f"expected exactly one diagnostic for {where}: {diagnostics}"
    )
    return messages[0]


# --- the committed set ------------------------------------------------------


def test_every_stub_validates_on_the_empty_state(real_root: Path) -> None:
    """Task 1.6's first verify clause, on the tree it ships.

    The stubs are empty, and "validates on the empty state" is a weaker claim
    than validating at all -- but it is the claim the task makes, and it is a
    real one: a schema with `"minItems": 1` or a required member would reject its
    own stub, and the failure would be a committed tree that cannot pass its own
    hook.
    """
    report = Report()
    validate.check_catalog_data_files(report)
    assert report.diagnostics == [], report.render()


def test_the_committed_stub_set_is_the_one_the_task_names(real_root: Path) -> None:
    """Naming the set is what stops the clause above from being vacuous.

    `report.diagnostics == []` is also true of a `catalog/` directory with no
    files in it, which is why this is a separate assertion rather than a comment.
    """
    stems = {path.stem for path in paths.catalog_data_files()}
    assert stems == set(STUBS)


def test_every_committed_data_file_has_a_schema_and_every_schema_is_used(
    real_root: Path,
) -> None:
    """The pairing holds in both directions, and the name says both.

    One direction is task 1.6's own clause. The other -- a catalog schema with no
    data file -- is not required by anything, but it is how the section-3 schemas
    already sit, and a schema nothing validates is a schema nothing has ever
    parsed. Loading each one here means a typo in the deferred schemas is found
    now rather than in section 3.

    The reverse assertion is the one the name promises and an earlier revision of
    this test only half made: it checked that every data file had a schema, and
    that the two deferred schemas existed, but never that no schema was orphaned.
    """
    stems = {path.stem for path in paths.catalog_data_files()}
    schemas = {path.stem for path in paths.SCHEMA_CATALOG.glob("*.json")}
    assert stems <= schemas
    assert schemas <= stems | set(DEFERRED_DATA)
    for stem in sorted(schemas):
        assert validate.load_catalog_schema(stem)


# --- the check reads the instance -------------------------------------------


def test_a_data_file_that_contradicts_its_schema_fails_naming_the_file(
    fake_root: Path,
) -> None:
    """Task 1.6's second verify clause, and the one the check was rewritten for.

    An earlier revision asserted only that a schema file existed with the right
    name. Every stub passed that, including the stubs that said nothing; this
    one fails, and it fails by naming the file rather than by being skipped.
    """
    _seed_schema(
        fake_root,
        "behaviors",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/behaviors.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "required": ["behaviors"],
          "properties": {{"behaviors": {{"type": "array"}}}}}}""",
    )
    _seed_data(fake_root, "behaviors", "behaviors: 3\n")

    diagnostics = _diagnostics()
    assert _only(diagnostics, "catalog/behaviors.yaml") == (
        "behaviors: 3 is not of type 'array'"
    )


def test_the_failure_locates_the_value_inside_the_file(fake_root: Path) -> None:
    """Naming the file is not enough to fix it.

    The same schema and a different mistake produce a different location, so a
    reader is told which entry is wrong rather than which file.
    """
    _seed_schema(
        fake_root,
        "behaviors",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/behaviors.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "required": ["behaviors"],
          "properties": {{"behaviors": {{"type": "array", "items":
            {{"type": "object", "required": ["concept"]}}}}}}}}""",
    )
    _seed_data(fake_root, "behaviors", "behaviors:\n  - id: x\n")

    assert _only(_diagnostics(), "catalog/behaviors.yaml") == (
        "behaviors/0: 'concept' is a required property"
    )


def test_a_data_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The hook runs on every commit; a traceback is not a failure report."""
    _seed_schema(
        fake_root,
        "slots",
        f'{{"$id": "https://open-house.invalid/schemas/catalog/slots.json",'
        f' {_DRAFT}, "schema_version": "1.0.0", "type": "object"}}',
    )
    _seed_data(fake_root, "slots", "slots: [\n")

    assert "cannot be read as YAML or JSON" in _only(
        _diagnostics(), "catalog/slots.yaml"
    )


# --- referencing the runtime schemas rather than restating them --------------


def test_a_catalog_schema_resolves_a_reference_to_a_runtime_schema(
    fake_root: Path,
) -> None:
    """Task 1.6's "referencing the runtime schemas rather than restating them".

    The value is rejected by the *runtime* schema's pattern, which is the only
    thing that distinguishes a live reference from a copied pattern that happens
    to agree today. A restated pattern would also reject it -- and would also go
    on agreeing after the runtime schema changed, which is the failure mode the
    clause exists to prevent.
    """
    write(fake_root, "schemas/slot/1.0.0.json", _SLOT_SCHEMA)
    _seed_schema(
        fake_root,
        "hardcoded_refs",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/hardcoded_refs.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "required": ["slot"],
          "properties": {{"slot": {{"$ref": "../slot/1.0.0.json#/properties/name"}}}}}}""",
    )

    _seed_data(fake_root, "hardcoded_refs", 'slot: "Ceiling Light"\n')
    assert _only(_diagnostics(), "catalog/hardcoded_refs.yaml") == (
        "slot: 'Ceiling Light' does not match '^[a-z][a-z0-9_]*$'"
    )

    _seed_data(fake_root, "hardcoded_refs", "slot: ceiling_light\n")
    assert _diagnostics() == []


def test_a_reference_that_resolves_to_nothing_is_reported_against_the_schema(
    fake_root: Path,
) -> None:
    """The reference is checked even where no instance reaches it.

    `$defs.unused` is referenced by nothing, so no data file can ever make the
    validator look at it -- and it is exactly the shape a half-finished schema
    has, waiting to be pointed at later. A broken reference there is a claim
    about a schema that does not exist, and without this check nothing would say
    so until something finally pointed at it.
    """
    _seed_schema(
        fake_root,
        "slots",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/slots.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "$defs": {{"unused": {{"$ref": "../slot/9.9.9.json#/x"}}}},
          "properties": {{"slots": {{"type": "array"}}}}}}""",
    )
    _seed_data(fake_root, "slots", "slots: []\n")

    message = _only(_diagnostics(), "schemas/catalog/slots.json")
    assert "../slot/9.9.9.json#/x" in message


def test_a_same_document_reference_resolves_and_a_broken_one_does_not(
    fake_root: Path,
) -> None:
    """`#/$defs/entry` is the form every catalog schema uses most.

    Both halves are asserted together because a resolver that resolves nothing
    would pass the second half alone, and one that resolves anything would pass
    the first alone.
    """
    _seed_schema(
        fake_root,
        "repos",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/repos.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "required": ["repos"],
          "properties": {{"repos": {{"items": {{"$ref": "#/$defs/repo"}}}}}},
          "$defs": {{"repo": {{"type": "object", "required": ["repo"]}}}}}}""",
    )
    _seed_data(fake_root, "repos", "repos:\n  - repo: CCOSTAN\n")
    assert _diagnostics() == []

    _seed_schema(
        fake_root,
        "repos",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/repos.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "$defs": {{"entry": {{"type": "object"}}}},
          "properties": {{"repos": {{"items": {{"$ref": "#/$defs/repo"}}}}}}}}""",
    )
    assert "#/$defs/repo" in _only(_diagnostics(), "schemas/catalog/repos.json")


def test_a_reference_that_escapes_the_schemas_tree_is_refused(
    fake_root: Path,
) -> None:
    """A `$ref` is not a file read.

    `../../` in a reference resolves to a path outside `schemas/`, and a
    validator that followed it would read something that is not a schema. It is
    reported as unresolvable instead, which is what it is.
    """
    _seed_schema(
        fake_root,
        "slots",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/slots.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"x": {{"$ref": "../../../etc/passwd"}}}}}}""",
    )
    _seed_data(fake_root, "slots", "x: y\n")

    assert "../../../etc/passwd" in _only(_diagnostics(), "schemas/catalog/slots.json")


def test_a_reference_to_a_schema_that_will_not_parse_is_a_diagnostic(
    fake_root: Path,
) -> None:
    """The broken file is not the one being validated, and it still must not crash.

    A stray comma in a runtime schema is a `json.JSONDecodeError`, which is a
    `ValueError` and which nothing between the registry and the command converts.
    """
    write(fake_root, "schemas/slot/1.0.0.json", "{oops")
    _seed_schema(
        fake_root,
        "hardcoded_refs",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/hardcoded_refs.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"slot": {{"$ref": "../slot/1.0.0.json#/properties/name"}}}}}}""",
    )
    _seed_data(fake_root, "hardcoded_refs", "slot: ceiling_light\n")

    assert "../slot/1.0.0.json#/properties/name" in _only(
        _diagnostics(), "schemas/catalog/hardcoded_refs.json"
    )


def test_a_reference_that_is_not_a_string_is_reported_not_crashed(
    fake_root: Path,
) -> None:
    """A `$ref` of `42` is not a reference, and it must not be a traceback.

    This is the input that broke an earlier revision. The reference walk skipped
    a `$ref` whose value is not a string -- treating an integer as a leaf, which
    is what it is -- so the walk passed the document, instance validation reached
    the branch, and `jsonschema` raised `AttributeError: 'int' object has no
    attribute 'startswith'`. `AttributeError` is not a resolution failure, so the
    catch around validation did not see it either, and it reached the command as
    a traceback. Skipping a malformed reference is therefore not neutral: the
    static walk is the only thing that can report it.
    """
    _seed_schema(
        fake_root,
        "repos",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/repos.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"repos": {{"items": {{"$ref": 42}}}}}}}}""",
    )
    _seed_data(fake_root, "repos", "repos:\n  - repo: CCOSTAN\n")

    assert "42" in _only(_diagnostics(), "schemas/catalog/repos.json")


@pytest.mark.parametrize(
    ("rendering", "reported"),
    [("null", "null"), ('["#/$defs/x"]', "#/$defs/x")],
)
def test_a_reference_written_as_null_or_a_list_is_reported_too(
    fake_root: Path, rendering: str, reported: str
) -> None:
    """The other two shapes a non-string `$ref` takes, each actually seeded.

    Both are parametrised rather than described: an earlier revision of this test
    named `null` in its docstring and seeded only the list, so the case the name
    called out was the one it never exercised -- the same species of overclaim as
    a test asserting a property it does not drive. The rendered forms differ too,
    and the assertion is on the rendering, because `json.dumps` is what puts the
    value in the message: `null` reports as `null`, the list as its contents.
    """
    _seed_schema(
        fake_root,
        "repos",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/repos.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"repos": {{"items": {{"$ref": {rendering}}}}}}}}}""",
    )
    _seed_data(fake_root, "repos", "repos:\n  - repo: CCOSTAN\n")

    assert reported in _only(_diagnostics(), "schemas/catalog/repos.json")


def test_a_reference_the_static_walk_cannot_see_is_caught_during_validation(
    fake_root: Path,
) -> None:
    """Why the catch around instance validation is not redundant with the walk.

    `$dynamicRef` has no static target: its anchor is looked up at validation
    time, in the instance's context, so the walk is deliberately blind to it and
    cannot resolve it ahead. That makes this the one reference a malformed schema
    can use to reach validation unresolved -- and the catch is the only thing
    between it and a traceback from the pre-commit hook.

    This replaces an earlier test that asserted the caught family was wider than
    `Unresolvable` by comparing three exception classes to it. That test was
    unearned: it exercised the `referencing` hierarchy rather than this code, and
    narrowing the catch to `Unresolvable` left the whole suite green. Measured,
    `Resolver.lookup` normalises every missing-resource failure to
    `Unresolvable`, so this one class is the honest set and this test is the
    behavioural one that narrowing actually breaks.
    """
    _seed_schema(
        fake_root,
        "repos",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/repos.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"repos": {{"$dynamicRef": "#nosuchanchor"}}}}}}""",
    )
    _seed_data(fake_root, "repos", "repos:\n  - repo: CCOSTAN\n")

    message = _only(_diagnostics(), "catalog/repos.yaml")
    assert "nosuchanchor" in message
    assert "within" not in message, "the whole document should not be inlined"


def test_the_caught_failure_is_the_one_lookup_actually_raises() -> None:
    """The claim behind the narrow catch, asserted rather than assumed.

    Six resolution failures exist and they do not share a base, which is why an
    earlier revision caught all six. The narrower catch is right only because
    `Resolver.lookup` normalises the others to `Unresolvable`; if that ever
    stops being true, this fails here rather than as a traceback in the hook.
    """
    from referencing.exceptions import Unresolvable

    assert validate._RESOLUTION_FAILURE is Unresolvable

    registry = validate._registry()
    resolver = registry.resolver(
        base_uri="https://open-house.invalid/schemas/catalog/repos.json"
    )
    for reference in (
        "../slot/9.9.9.json#/x",
        "#/$defs/nope",
        "https://open-house.invalid/schemas/nope/1.0.0.json",
    ):
        with pytest.raises(Unresolvable):
            resolver.lookup(reference)


def test_a_schema_missing_its_identifier_is_reported(fake_root: Path) -> None:
    """`$id` is required because it is load bearing, not because it is tidy.

    It is the base a relative `$ref` resolves against, so without it the
    cross-file reference the same task asks for cannot be made at all.
    """
    _seed_schema(fake_root, "slots", f'{{{_DRAFT}, "schema_version": "1.0.0"}}')
    _seed_data(fake_root, "slots", "slots: []\n")

    assert "$id" in _only(_diagnostics(), "schemas/catalog/slots.json")


def test_a_schema_missing_its_version_is_reported(fake_root: Path) -> None:
    """The other half of the pair, and it needs its own test.

    Nothing functional depends on `schema_version` the way `$id` is depended on
    for resolution, so no fixture that omits `$id` can stand in for this: an
    implementation that dropped `schema_version` from the required pair would
    otherwise leave every test green.
    """
    _seed_schema(
        fake_root,
        "slots",
        f'{{"$id": "https://open-house.invalid/schemas/catalog/slots.json", {_DRAFT}}}',
    )
    _seed_data(fake_root, "slots", "slots: []\n")

    message = _only(_diagnostics(), "schemas/catalog/slots.json")

    assert "does not carry schema_version" in message

    # `$id` is present on this fixture, so the message must name only the field
    # that is actually missing. What stood here before -- `"$id" in message` --
    # could not fail: the message's own boilerplate reads "carries both $id and
    # schema_version", so the substring is present whatever the check does.
    assert "does not carry $id" not in message


def test_a_catalog_schema_that_is_not_json_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    _seed_schema(fake_root, "slots", "{not json")
    _seed_data(fake_root, "slots", "slots: []\n")

    assert "is not valid JSON" in _only(_diagnostics(), "schemas/catalog/slots.json")


def test_a_broken_schema_does_not_hide_the_other_files_findings(
    fake_root: Path,
) -> None:
    """One malformed schema used to abort the loop.

    `load_catalog_schema` raises `CheckError`, and `validate_all` catches that at
    the level of the whole check -- so a single typo in one catalog schema
    silently skipped validation of every other data file, in a check whose entire
    purpose is that no data file goes unvalidated.
    """
    _seed_schema(fake_root, "slots", "{not json")
    _seed_data(fake_root, "slots", "slots: []\n")
    _seed_schema(
        fake_root,
        "behaviors",
        f'{{"$id": "https://open-house.invalid/schemas/catalog/behaviors.json",'
        f' {_DRAFT}, "schema_version": "1.0.0", "required": ["behaviors"],'
        f' "properties": {{"behaviors": {{"type": "array"}}}}}}',
    )
    _seed_data(fake_root, "behaviors", "behaviors: 3\n")

    diagnostics = _diagnostics()
    assert [where for where, _ in diagnostics] == [
        "catalog/behaviors.yaml",
        "schemas/catalog/slots.json",
    ]


# --- the generated schema and the shapes it inlines -------------------------


def test_the_committed_raw_record_schema_equals_a_fresh_generation(
    real_root: Path,
) -> None:
    """A generated file that is hand-editable is not generated.

    Task 1.6 commits the schema the allowlist produces. Nothing at runtime
    regenerates it, so without this the file could drift from the allowlist it
    claims to be derived from and no check would notice.
    """
    committed = facts.raw_record_schema_path().read_bytes().decode("utf-8")
    assert committed == facts.render_schema(facts.generate_raw_record_schema())


def test_the_raw_record_schema_admits_exactly_the_allowlisted_fields(
    real_root: Path,
) -> None:
    """Closed in both directions, which is what makes the allowlist the answer.

    `additionalProperties: false` alone would admit a subset; the key set has to
    equal the allowlist for "a record's key set equals the fact allowlist
    exactly" to be a property of the schema rather than of the normaliser's
    good behaviour. Task 3.5 extends this with the directions that need a record
    to test -- an invented key, a permissive pattern, a free-form string.
    """
    document = json.loads(facts.raw_record_schema_path().read_bytes().decode("utf-8"))
    record = document["$defs"][facts.RAW_RECORD_DEF]

    assert record["additionalProperties"] is False
    assert set(record["properties"]) == set(facts.fact_fields())
    assert set(record["required"]) == set(facts.fact_fields())


def test_the_inlined_fact_shapes_equal_the_declared_ones(real_root: Path) -> None:
    """Two schemas restate shapes, and until task 3.6 routes them through the
    allowlist both would drift silently.

    `hardcoded_refs.json` inlines the `entity_ref` and `slug` patterns because a
    JSON Schema cannot be handed a YAML file's contents; `room_types.json`,
    `behaviors.json` and `slots.json` do not, because a `$ref` into a runtime
    schema is available to them. So the drift is bounded to the two patterns
    asserted here, and the assertion is what bounds it.

    An earlier revision of this docstring named `room_types.json` among the
    schemas that reference rather than restate, and `room_types.json` at the time
    inlined the slot pattern. The claim was checked only by
    `test_no_hand_written_catalog_schema_restates_the_slot_name_pattern` below.
    """
    document = json.loads(
        (paths.SCHEMA_CATALOG / "hardcoded_refs.json").read_bytes().decode("utf-8")
    )
    entry = document["$defs"]["entry"]["properties"]

    for field, shape in (("entity_ref", "entity_ref"), ("naming_convention", "slug")):
        assert entry[field]["pattern"] == facts.shape_fragment(shape)["pattern"], field


# --- a schema that cannot be applied ----------------------------------------


def _patterns(node: object) -> list[str]:
    """Every `pattern` string anywhere in a decoded schema document."""
    if isinstance(node, dict):
        found = [
            value
            for key, value in node.items()
            if key == "pattern" and isinstance(value, str)
        ]
        for value in node.values():
            found.extend(_patterns(value))
        return found
    if isinstance(node, list):
        found = []
        for item in node:
            found.extend(_patterns(item))
        return found
    return []


def test_no_hand_written_catalog_schema_restates_the_slot_name_pattern(
    real_root: Path,
) -> None:
    """Task 1.6: catalog schemas reference the runtime schemas, not restate them.

    Three hand-written catalog schemas used to inline `^[a-z][a-z0-9_]*$` where
    `schemas/slot/1.0.0.json` already states it. A restated pattern is a second
    definition free to drift from the published one, and a restated *slot* shape
    is exactly what task 7.6's conformance check is written to catch -- so the
    restatement was going to be found by that check, which is the wrong time to
    find out.

    `raw-behaviors.json` is exempt, and the exemption is the point rather than an
    escape hatch: it is generated, so the same pattern reaches it from the
    vocabulary's element schema through the generator. Nobody can introduce a
    drift into it by typing, which is the failure this test is about.
    """
    slot = json.loads(
        (paths.SCHEMAS / "slot" / "1.0.0.json").read_bytes().decode("utf-8")
    )
    pattern = slot["properties"]["name"]["pattern"]

    offenders = sorted(
        path.name
        for path in paths.SCHEMA_CATALOG.glob("*.json")
        if path.name != "raw-behaviors.json"
        and pattern in _patterns(json.loads(path.read_bytes().decode("utf-8")))
    )
    assert offenders == []


@pytest.mark.parametrize(
    ("keywords", "reason"),
    [
        pytest.param('"type": "objectt"', "objectt", id="unknown-type"),
        pytest.param('"maxItems": "x"', "integer", id="non-integer-bound"),
        pytest.param(
            '"properties": {"a": {"type": "string", "pattern": "["}}',
            "regex",
            id="unterminated-pattern",
        ),
        pytest.param('"multipleOf": 0', "minimum", id="zero-multiple"),
    ],
)
def test_a_catalog_schema_with_a_malformed_keyword_is_reported_not_crashed(
    fake_root: Path, keywords: str, reason: str
) -> None:
    """A schema whose JSON is well formed but whose keywords are wrong.

    This is the input that broke the hook after the previous rejection. Every one
    of these is a plausible typo in a hand-written schema file, and every one
    reached `iter_errors` and raised something outside the caught family --
    `UnknownType`, `TypeError`, `re.error`, `ZeroDivisionError` -- and escaped
    `validate_all`, which catches only `CheckError`. `schemas/catalog/` is edited
    in place as sections 2-7 proceed, so this is a live hazard rather than a
    hypothetical one.

    The instance is deliberately trivial. The point is that the schema is checked
    before it is applied, so the failure is a property of the schema and not of
    which branch a data file happens to reach.
    """
    _seed_schema(
        fake_root,
        "slots",
        f'{{"$id": "https://open-house.invalid/schemas/catalog/slots.json", '
        f'{_DRAFT}, "schema_version": "1.0.0", {keywords}}}',
    )
    _seed_data(fake_root, "slots", "slots: []\n")

    message = _only(_diagnostics(), "schemas/catalog/slots.json")

    assert "is not a valid JSON Schema" in message
    assert reason in message


def test_a_reference_to_a_file_that_is_not_a_valid_schema_is_reported(
    fake_root: Path,
) -> None:
    """A `$ref` target that parses but is not a schema.

    The sibling of the unparseable-file test, and the case the catalog schema's
    own meta-check cannot see: the referencing document is valid, the file it
    names exists and decodes, and the pointed-at fragment is *present*. Nothing
    goes wrong until the library applies the target -- inside `iter_errors`,
    where the diagnostic would name the data file that happened to reference it
    rather than the schema at fault.

    The malformed keyword is deliberately inside the branch the fragment names
    rather than one beside it. A fixture whose target had no `properties` at all
    would fail at the pointer, which is a resolution failure the walk already
    reports, and the test would pass with the meta-check deleted -- which is how
    the first version of it read.
    """
    write(
        fake_root,
        "schemas/slot/1.0.0.json",
        '{"$id": "https://open-house.invalid/schemas/slot/1.0.0.json", '
        f'{_DRAFT}, "schema_version": "1.0.0", "type": "object",'
        ' "properties": {"name": {"type": "string", "pattern": "["}}}',
    )
    _seed_schema(
        fake_root,
        "hardcoded_refs",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/hardcoded_refs.json",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"slot": {{"$ref": "../slot/1.0.0.json#/properties/name"}}}}}}""",
    )
    _seed_data(fake_root, "hardcoded_refs", "slot: ceiling_light\n")

    assert "../slot/1.0.0.json#/properties/name" in _only(
        _diagnostics(), "schemas/catalog/hardcoded_refs.json"
    )


def test_a_reference_cycle_is_a_diagnostic_not_a_traceback(fake_root: Path) -> None:
    """A cycle passes everything before validation and then closes on itself.

    `$ref` is a string to the meta-schema, which never follows one, so
    `check_schema` passes both documents; the walk resolves both references
    because each names a file that is present; and the loop only closes when the
    library applies them, as `RecursionError`. That is why the catch around
    `iter_errors` keeps a general arm rather than the resolution family alone --
    a `RecursionError` is not a resolution failure and no input short of this one
    shows it.
    """
    _seed_schema(
        fake_root,
        "repos",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/repos.json",
          {_DRAFT}, "schema_version": "1.0.0", "$ref": "rooms.json"}}""",
    )
    _seed_schema(
        fake_root,
        "rooms",
        f"""{{"$id": "https://open-house.invalid/schemas/catalog/rooms.json",
          {_DRAFT}, "schema_version": "1.0.0", "$ref": "repos.json"}}""",
    )
    _seed_data(fake_root, "repos", "repos: []\n")
    _seed_data(fake_root, "rooms", "rooms: []\n")

    assert "could not be applied" in _only(_diagnostics(), "catalog/repos.yaml")


# --- nesting past an interpreter frame budget --------------------------------
#
# The three tests below are the same finding on three different frames, and they
# are here rather than beside each other's code because what they share is the
# measurement, not the call site. The recursion limit is a budget of *frames*,
# and each of these recursions spends a different number of frames per level, so
# each fails at a different depth: the meta-validator spends about ten frames per
# level and dies at 100, `yaml.safe_load` dies on a nested sequence between 300
# and 500, and `json.loads` and the static reference walk hold out past 1000.
#
# That spread is the reason an earlier version of this package argued the deep
# case was unreachable and got it wrong: the argument was made from the loader's
# ceiling, and the frame that actually dies first is ten times cheaper to reach.

#: Deep enough to exhaust the stack in `check_schema` (measured: 80 accepted, 100
#: raises) and shallow enough that the loader and the reference walk, which spend
#: far fewer frames per level, still read it -- so a failure here is the
#: meta-validator's and the test is not passing for the loader's reason.
NESTED_SCHEMA_DEPTH = 200

#: Deep enough to exhaust `yaml.safe_load` on a nested sequence (measured: 300
#: loads, 500 raises). A nested *mapping* fails much earlier, but as a
#: `ScannerError`, which is a `YAMLError` and was already caught.
NESTED_DATA_DEPTH = 500


def _nested_schema(depth: int, schema_uri: str) -> str:
    """A schema of single-property objects, `depth` levels deep.

    Assembled by string concatenation rather than by nesting Python objects, so
    the test's own construction does not recurse and cannot be mistaken for the
    thing under test -- and so the fixture is exactly the bytes it claims to be.
    """
    node = '"type": "string"'
    for _ in range(depth):
        node = f'"type": "object", "properties": {{"name": {{{node}}}}}'
    return f'{{"$id": "{schema_uri}", {_DRAFT}, "schema_version": "1.0.0", {node}}}'


def test_a_schema_nested_too_deeply_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The frame the package used to call unreachable, reached.

    `schemas/catalog/` is edited in place as sections 2-7 proceed, so a schema
    grown past this depth is a file someone could commit, and the check's whole
    contract is that such a file is a diagnostic. Deleting the `RecursionError`
    arm from `_invalid_schema` makes this the traceback it is written against.
    """
    _seed_schema(
        fake_root,
        "slots",
        _nested_schema(NESTED_SCHEMA_DEPTH, SLOTS_URI),
    )
    # The check iterates the data files and looks up each one's schema, so a
    # schema with no stub beside it is never opened. The stub's contents are
    # never read here -- the schema is rejected before validation -- but its
    # presence is what makes this a test of anything.
    _seed_data(fake_root, "slots", "slots: []\n")

    assert "nested too deeply to be read as a JSON Schema" in _only(
        _diagnostics(), "schemas/catalog/slots.json"
    )


def test_a_data_file_nested_too_deeply_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The loader's ceiling, which is its own frame and its own message.

    Distinct from the syntax-error case above it in this file: the file is
    well-formed YAML, so "cannot be read as YAML or JSON" would send the reader
    looking for a stray bracket that is not there. Deleting the `RecursionError`
    arm from `_load_data_file` makes this a traceback.
    """
    _seed_schema(
        fake_root,
        "slots",
        f'{{"$id": "{SLOTS_URI}", {_DRAFT}, "schema_version": "1.0.0",'
        ' "type": "object"}',
    )
    _seed_data(fake_root, "slots", "[" * NESTED_DATA_DEPTH + "]" * NESTED_DATA_DEPTH)

    assert "nested too deeply to be parsed" in _only(
        _diagnostics(), "catalog/slots.yaml"
    )


def test_a_reference_to_a_schema_nested_too_deeply_is_reported(
    fake_root: Path,
) -> None:
    """The same depth reached through a `$ref` -- and the one case with no mutant.

    This test is the odd one of the three and says so. The other two fail when an
    arm of this package is deleted; this one cannot, because the package adds
    nothing here. `_retrieve`'s bare `check_schema` raises `RecursionError` on
    this document, `Registry.get_or_retrieve` wraps the whole retrieve callable
    in `except Exception` and re-raises as `Unretrievable`, and `lookup` maps
    that to `Unresolvable`, which `_unresolved_references` already catches. Adding
    a `RecursionError` arm to `_retrieve` was tried and removed again: with it
    present and with it absent, the diagnostic below is byte for byte identical.

    It is kept because the guarantee it pins is one this check *relies* on and
    would not otherwise notice losing -- the failure mode is a `referencing`
    release that narrows that `except`, and the symptom would be the hook dying
    on a `$ref` rather than naming it. A test whose guard is a dependency is
    weaker than one whose guard is this package; labelling it is the difference
    between a weak test and a test that is lying about what it covers.
    """
    write(
        fake_root,
        "schemas/slot/1.0.0.json",
        _nested_schema(NESTED_SCHEMA_DEPTH, SLOT_URI),
    )
    _seed_schema(
        fake_root,
        "hardcoded_refs",
        f"""{{"$id": "{HARDCODED_REFS_URI}",
          {_DRAFT}, "schema_version": "1.0.0", "type": "object",
          "properties": {{"slot": {{"$ref": "../slot/1.0.0.json#/properties/name"}}}}}}""",
    )
    _seed_data(fake_root, "hardcoded_refs", "slot: ceiling_light\n")

    assert "../slot/1.0.0.json#/properties/name" in _only(
        _diagnostics(), "schemas/catalog/hardcoded_refs.json"
    )
