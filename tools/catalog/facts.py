"""The fact allowlist, the closed shape set, and the schema generated from them.

`catalog/raw-behaviors.json` is the committed record of every selected file in
every reference repo. It carries facts for all four repos -- including the two
that grant no licence to reuse their expression -- so its schema has to be a
guarantee rather than a description: the record must be *incapable* of holding
authored text, not merely intended not to.

Three things together make that true, and no one of them alone.

`schemas/catalog/fact-fields.yaml` is the allowlist: a committed file naming
every field a record may carry, and the type that field may take. The schema
below is **generated from it**, so the two cannot drift in field *names*.

`schemas/catalog/fact-shapes.yaml` is the closed set of shapes those types may
name. A field does not carry a pattern of its own; it names a shape, and a shape
is a member of a committed set. That difference is the load-bearing one -- any
pattern satisfies "has a pattern", `^.*$` included, so a rule stated over
patterns would relocate the guarantee into the regex and into whoever wrote it.

And the generated record schema carries `additionalProperties: false`, so a
record holding a key outside the set fails rather than carrying it.

So expression is not merely withheld from the committed store. It has nowhere in
it to go.

The generator is deliberately narrow about what it will resolve. A membership
shape names a `source`, and only the sources this module knows how to read are
accepted; a vocabulary field names an axis of the current `behavior-vocabulary`
version; an `enum_ref` names a file and a field. Anything else raises, because a
generator that guessed would be the place the guarantee silently widened.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import yaml

from . import paths, schemas
from .errors import CheckError, Report
from .narrow import as_bool, as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

FACT_CHECK = "fact-allowlist"

#: The allowlist and the shape set, both under `schemas/catalog/`.
FACT_FIELDS_FILE = "fact-fields.yaml"
FACT_SHAPES_FILE = "fact-shapes.yaml"

#: The generated schema, and the `$defs` key inside it that holds one record.
RAW_RECORD_SCHEMA_FILE = "raw-behaviors.json"
RAW_RECORD_DEF = "raw_record"

#: The second generated schema -- the hardcoding audit's entry schema -- and the
#: allowlist section it is generated from. Two sections of one allowlist file:
#: `fields` describes a raw record, `hardcoded_fields` describes one audit entry,
#: and both are generated so neither field set can grow past its guarantee by
#: editing a schema alone.
HARDCODED_REFS_SCHEMA_FILE = "hardcoded_refs.json"
HARDCODED_ENTRY_DEF = "entry"
HARDCODED_FIELDS_KEY = "hardcoded_fields"

#: The allowlist key naming the fields of an audit entry that form an entry-level
#: `oneOf`: exactly one of them is non-null. Absent from an allowlist that has no
#: such pair, so a minimal fixture still generates a schema.
HARDCODED_ONE_OF_KEY = "hardcoded_entry_one_of"

SCHEMA_URI_PREFIX = "https://open-house.invalid/schemas/"

#: A string no fact-shape may admit, used to decide whether a `pattern` shape is
#: really a constraint. Authored text is the thing the committed store may not
#: hold, and a pattern that matches prose is a pattern that holds prose -- so a
#: shape is admissible only if it refuses this string. Prose rather than a single
#: space, because a pattern may admit a space without admitting a sentence.
_PROSE_PROBE = "Authored prose, with spaces and CAPS! 42 ?"

_BLOCK_NOTE_KEYS = ("note",)


def _load_yaml(path: Path) -> dict[str, object]:
    """A YAML document under `schemas/catalog/`, as a mapping or a named failure.

    Raising rather than returning empty: every field of the generated schema
    depends on this file, so a version of it that could not be read would produce
    a *narrower* schema than the one committed -- and a silently narrower
    guarantee is the failure this whole module exists to prevent.
    """
    relative = path.relative_to(paths.ROOT).as_posix()
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise CheckError(FACT_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CheckError(FACT_CHECK, relative, f"is not valid YAML: {exc}") from exc
    document = as_mapping(loaded)
    if not document:
        raise CheckError(FACT_CHECK, relative, "is not a YAML mapping")
    return document


def fact_shapes() -> dict[str, object]:
    """Every named shape, keyed by name."""
    shapes = as_mapping(
        _load_yaml(paths.SCHEMA_CATALOG / FACT_SHAPES_FILE).get("shapes")
    )
    if not shapes:
        raise CheckError(
            FACT_CHECK,
            f"schemas/catalog/{FACT_SHAPES_FILE}",
            "declares no shapes; a field naming a shape would have nothing to "
            "resolve to",
        )
    return shapes


def fact_fields() -> dict[str, object]:
    """Every allowed field, keyed by name."""
    fields = as_mapping(
        _load_yaml(paths.SCHEMA_CATALOG / FACT_FIELDS_FILE).get("fields")
    )
    if not fields:
        raise CheckError(
            FACT_CHECK,
            f"schemas/catalog/{FACT_FIELDS_FILE}",
            "declares no fields; the generated record schema would be empty",
        )
    return fields


def hardcoded_fields() -> dict[str, object]:
    """Every allowed hardcoding-audit entry field, keyed by name.

    The second section of the same allowlist, read the same way and for the same
    reason: the `hardcoded_refs` entry schema is generated from it, so the
    second facts store cannot grow an expression-bearing field by editing its
    schema alone either.
    """
    fields = as_mapping(
        _load_yaml(paths.SCHEMA_CATALOG / FACT_FIELDS_FILE).get(HARDCODED_FIELDS_KEY)
    )
    if not fields:
        raise CheckError(
            FACT_CHECK,
            f"schemas/catalog/{FACT_FIELDS_FILE}",
            f"declares no {HARDCODED_FIELDS_KEY}; the generated audit-entry "
            "schema would be empty",
        )
    return fields


def hardcoded_entry_one_of() -> tuple[str, ...]:
    """The audit-entry fields that form an entry-level `oneOf`, if the allowlist
    names any.

    A relation between fields rather than a property of one, so it is stated at
    the section's top level and not on either field: the per-field forms above
    each describe a *value*, and none of them can say that exactly one of two
    fields is non-null. Absent key means the allowlist states no such pair, and a
    minimal fixture that omits it still generates a schema.
    """
    raw = _load_yaml(paths.SCHEMA_CATALOG / FACT_FIELDS_FILE).get(HARDCODED_ONE_OF_KEY)
    names = [as_text(item) or "" for item in as_sequence(raw)]
    return tuple(name for name in names if name)


def _members_of(source: str, where: str) -> list[str]:
    """The member values of a membership shape, by its declared source.

    A membership shape admits a value because the value is *in a committed set*,
    not because it matches a pattern. Only one source is known today, and an
    unknown one raises rather than returning empty: returning empty would make
    the shape admit nothing, which reads as a stricter guard but is really a
    generator that has stopped working.
    """
    if source != "catalog/inventory.json":
        raise CheckError(
            FACT_CHECK,
            where,
            f"names membership source {source!r}, which this generator cannot "
            "read; a new membership shape needs a reader here",
        )
    path = paths.CATALOG / "inventory.json"
    if not path.is_file():
        # The inventory is emitted by 3.3's walker, and until it is there
        # nothing is selected, so the member set is genuinely empty rather than
        # unknown. Empty is a temporary state and not a quiet one: the committed
        # schema is regenerated the moment the inventory lands, and the
        # byte-identity test in `test_catalog_data.py` is what says so.
        return []
    try:
        loaded: object = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckError(
            FACT_CHECK,
            path.relative_to(paths.ROOT).as_posix(),
            f"cannot be read as JSON: {exc}",
        ) from exc
    members: set[str] = set()
    for repo in as_sequence(as_mapping(loaded).get("repos")):
        for entry in as_sequence(as_mapping(repo).get("files")):
            record = as_mapping(entry)
            if as_bool(record.get("selected")) is not True:
                continue
            value = as_text(record.get("path"))
            if value:
                members.add(value)
    return sorted(members)


def _deref(
    fragment: dict[str, object], root: dict[str, object], where: str
) -> dict[str, object]:
    """Follow a local `$ref` into the document's own `$defs`.

    Only same-document references, because that is all the runtime schemas use
    for their inner definitions. A reference out of the document would be a
    cross-file one, and resolving those is the validator's job with a registry;
    a generator that quietly inlined one would be resolving something the schema
    author expected the validator to resolve at validation time.
    """
    reference = as_text(fragment.get("$ref"))
    if reference is None:
        return fragment
    if not reference.startswith("#/"):
        raise CheckError(
            FACT_CHECK,
            where,
            f"resolves to `$ref: {reference}`, which is not a same-document "
            "reference; this generator reads definitions, not other files",
        )
    target: object = root
    for part in reference[2:].split("/"):
        target = as_mapping(target).get(part)
    resolved = as_mapping(target)
    if not resolved:
        raise CheckError(
            FACT_CHECK, where, f"resolves to `$ref: {reference}`, which is absent"
        )
    return resolved


def _vocabulary_element(axis: str, where: str) -> dict[str, object]:
    """What the current behaviour vocabulary permits as *one* term of an axis.

    The vocabulary's own schema for the axis, minus its array wrapper -- because
    what a fact field needs is the type of a single term, and the record decides
    how many it holds. Taking the element rather than a snapshot of an enumerated
    term list is deliberate: the vocabulary is the one runtime concept expected
    to gain a version in Phase 0, and task 7.1 publishes its derived terms as
    `1.1.0`. Resolving through the *current* version means the generated record
    schema follows that publication, where an enum frozen today would keep
    admitting the terms the extraction replaced.

    It also means this works before the terms exist. `1.0.0` declares the shape
    of the vocabulary -- a term is a lower-snake-case string -- and enumerates
    nothing, because the terms are learned from the extraction in stage A and are
    not knowable when the schema is frozen. So a term is shape-constrained today
    and enumerated once `1.1.0` lands, which is the honest ordering rather than a
    generator that refuses to run in the meantime.
    """
    versions = schemas.load_versions("behavior-vocabulary")
    current = schemas.current_version(versions)
    if current is None:
        raise CheckError(
            FACT_CHECK,
            where,
            "resolves against `behavior-vocabulary`, which has no single current "
            "version, so its terms cannot be read",
        )
    root = current.document
    # The axes are declared under `properties`, and the mode and profile axes are
    # one level further in -- `properties.axes.properties` -- because the
    # vocabulary document groups them. Read from the schema's declarations rather
    # than from a list of axis names kept here, so adding an axis to the
    # vocabulary is a change to the vocabulary and not to this generator.
    container = as_mapping(root.get("properties"))
    if axis in ("modes", "profiles"):
        container = as_mapping(as_mapping(container.get("axes")).get("properties"))
    axis_schema = _deref(as_mapping(container.get(axis)), root, where)
    if not axis_schema:
        raise CheckError(
            FACT_CHECK,
            where,
            f"names vocabulary axis {axis!r}, which the current "
            "`behavior-vocabulary` does not carry",
        )
    if "enum" in axis_schema:
        values = [
            item
            for item in (as_text(x) for x in as_sequence(axis_schema["enum"]))
            if item
        ]
        if not values:
            raise CheckError(FACT_CHECK, where, f"axis {axis!r} declares an empty enum")
        return {"enum": values}
    items = _deref(as_mapping(axis_schema.get("items")), root, where)
    if not items:
        raise CheckError(
            FACT_CHECK,
            where,
            f"axis {axis!r} declares neither an `enum` nor an `items` schema, so "
            "a single term of it has no type",
        )
    return items


def _enum_ref_terms(reference: str, where: str) -> list[str]:
    """The named field of every entry of a committed file's sequences.

    `catalog/slots.yaml#name` means: read that file, and take the `name` field of
    every mapping in any of its top-level sequences. Written over the whole
    document rather than over a key this module already knows, so that pointing a
    field at a different commmitted list is a change to the allowlist and not a
    change to the generator.
    """
    relative, _, field = reference.partition("#")
    if not relative or not field:
        raise CheckError(
            FACT_CHECK,
            where,
            f"names enum reference {reference!r}, which is not `<path>#<field>`",
        )
    path = paths.ROOT / relative
    if not path.is_file():
        return []
    document = _load_yaml(path)
    members: set[str] = set()
    for value in document.values():
        for entry in as_sequence(value):
            candidate = as_text(as_mapping(entry).get(field))
            if candidate:
                members.add(candidate)
    return sorted(members)


def _shape_schema(
    name: str, shapes: dict[str, object], where: str
) -> dict[str, object]:
    """The JSON Schema fragment a named shape resolves to.

    Two kinds only. A `pattern` shape pins a tight character class, and a
    `membership` shape admits by being an element of a committed set -- which is
    what carries the `path` fact, since a pattern tight enough to exclude
    authored text would also reject real tracked paths.
    """
    spec = as_mapping(shapes.get(name))
    if not spec:
        raise CheckError(
            FACT_CHECK,
            where,
            f"names shape {name!r}, which `schemas/catalog/{FACT_SHAPES_FILE}` "
            "does not declare",
        )
    kind = as_text(spec.get("kind"))
    if kind == "pattern":
        pattern = as_text(spec.get("pattern"))
        if pattern is None:
            raise CheckError(FACT_CHECK, where, f"shape {name!r} declares no `pattern`")
        return {"type": "string", "pattern": pattern}
    if kind == "membership":
        source = as_text(spec.get("source"))
        if source is None:
            raise CheckError(FACT_CHECK, where, f"shape {name!r} declares no `source`")
        return {"enum": _members_of(source, where)}
    raise CheckError(
        FACT_CHECK,
        where,
        f"shape {name!r} declares kind {kind!r}; the known kinds are `pattern` "
        "and `membership`",
    )


def shape_fragment(name: str) -> dict[str, object]:
    """One named shape as a JSON Schema fragment.

    Public because a schema that inlines a shape rather than being generated from
    the allowlist -- `hardcoded_refs.json` does, until task 3.6 routes it through
    the same files -- needs a test that its inlined pattern equals the declared
    one. Without that, the second facts store would carry a copy of the shape and
    the copies could drift, which is the failure the closed shape set exists to
    prevent.
    """
    where = f"schemas/catalog/{FACT_SHAPES_FILE}:{name}"
    return _shape_schema(name, fact_shapes(), where)


def _shape_names(value: object, where: str) -> list[str]:
    """The shape names a `shape:` key carries -- one, or an ordered list."""
    if isinstance(value, str):
        return [value]
    names = [name for name in (as_text(item) for item in as_sequence(value)) if name]
    if not names:
        raise CheckError(FACT_CHECK, where, "names no shape")
    return names


def _form_schema(
    spec: dict[str, object], shapes: dict[str, object], where: str
) -> dict[str, object]:
    """One field's declared type, as a JSON Schema fragment.

    The forms are exactly the ones `fact-fields.yaml` documents, and there is no
    fall-through: a field that declares none of them raises, because a default
    here would be a type nobody chose and the only safe default -- an untyped
    field -- is the one thing this module may not emit.
    """
    if "enum" in spec:
        values = [
            item for item in (as_text(x) for x in as_sequence(spec["enum"])) if item
        ]
        if not values:
            raise CheckError(FACT_CHECK, where, "declares an empty `enum`")
        return {"enum": values}
    if "integer" in spec:
        bounds = as_mapping(spec["integer"])
        fragment: dict[str, object] = {"type": "integer"}
        for key in ("minimum", "maximum"):
            limit = bounds.get(key)
            if isinstance(limit, int) and not isinstance(limit, bool):
                fragment[key] = limit
        return fragment
    if as_bool(spec.get("boolean")) is True:
        return {"type": "boolean"}
    if "enum_ref" in spec:
        reference = as_text(spec["enum_ref"]) or ""
        return {"enum": _enum_ref_terms(reference, where)}
    if "vocabulary" in spec:
        return _vocabulary_element(as_text(spec["vocabulary"]) or "", where)
    if "shape" in spec:
        names = _shape_names(spec["shape"], where)
        if len(names) == 1:
            return _shape_schema(names[0], shapes, where)
        return {"anyOf": [_shape_schema(name, shapes, where) for name in names]}
    if "list_of" in spec:
        element = as_mapping(spec["list_of"])
        if not element:
            raise CheckError(FACT_CHECK, where, "declares an empty `list_of`")
        return {"type": "array", "items": _form_schema(element, shapes, where)}
    if "pattern" in spec:
        raise CheckError(
            FACT_CHECK,
            where,
            "carries its own `pattern` rather than naming a committed shape; a "
            "string fact resolves through `schemas/catalog/"
            f"{FACT_SHAPES_FILE}`, so a pattern written here is a second "
            "definition free to drift from the shape set it was meant to draw on",
        )
    raise CheckError(
        FACT_CHECK,
        where,
        "declares none of `enum`, `integer`, `boolean`, `enum_ref`, "
        "`vocabulary`, `shape` or `list_of`; a free-form field is not permitted "
        "in the committed record",
    )


def _make_nullable(fragment: dict[str, object]) -> None:
    """Admit null alongside the declared type, in place.

    Null is a value here rather than an omission -- `unclaimed: null` is the
    record that a shipped row claims this file -- so the field stays required and
    gains `null` as a permitted value.
    """
    if "enum" in fragment:
        fragment["enum"] = [*as_sequence(fragment["enum"]), None]
        return
    kind = fragment.get("type")
    if isinstance(kind, str):
        fragment["type"] = [kind, "null"]
        return
    if isinstance(kind, list):
        fragment["type"] = [*kind, "null"]
        return
    if "anyOf" in fragment:
        fragment["anyOf"] = [*as_sequence(fragment["anyOf"]), {"type": "null"}]
        return
    msg = "a nullable field declares no type to widen"
    raise CheckError(FACT_CHECK, "schemas/catalog/fact-fields.yaml", msg)


def _field_properties(
    fields: dict[str, object], shapes: dict[str, object], section: str
) -> dict[str, object]:
    """Every field of one allowlist section, as JSON Schema property fragments.

    Shared by both generated schemas so the two cannot diverge in how a field is
    interpreted -- an `enum` means the same thing in `fields` and in
    `hardcoded_fields`, and the one place that decides it is here.
    """
    properties: dict[str, object] = {}
    for name in sorted(fields):
        where = f"schemas/catalog/{FACT_FIELDS_FILE}:{section}.{name}"
        spec = as_mapping(fields[name])
        fragment = _form_schema(spec, shapes, where)
        if as_bool(spec.get("nullable")) is True:
            _make_nullable(fragment)
        for key in _BLOCK_NOTE_KEYS:
            note = as_text(spec.get(key))
            if note:
                fragment["description"] = note.strip()
        properties[name] = fragment
    return properties


def generate_raw_record_schema() -> dict[str, object]:
    """The whole `raw-behaviors.json` schema, built from the allowlist.

    Every allowlisted field is required, because the record's key set is supposed
    to equal the allowlist *exactly* -- an optional field would make the key set
    a subset of the allowlist and the guarantee correspondingly weaker.
    """
    shapes = fact_shapes()
    fields = fact_fields()
    properties = _field_properties(fields, shapes, "fields")

    return {
        "$id": f"{SCHEMA_URI_PREFIX}catalog/{RAW_RECORD_SCHEMA_FILE}",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Committed raw behaviour records",
        "description": (
            "Generated from schemas/catalog/fact-fields.yaml by "
            "tools/catalog/facts.py; edit the allowlist and regenerate rather "
            "than editing this file. One record per selected file in every "
            "reference repo, including the two that grant no reuse, carrying "
            "facts only: a path, an id, an artifact class, vocabulary terms, "
            "slot bindings, an `unclaimed` marker and the `domain.object_id` "
            "strings the file references. No field here can hold authored text, "
            "which is what makes the licence position structural rather than "
            "declarative. This schema is also the schema for the unclaimed "
            "register, because the marker lives as a field on each record."
        ),
        "schema_version": "1.0.0",
        "type": "object",
        "required": ["records"],
        "additionalProperties": False,
        "properties": {
            "records": {
                "type": "array",
                "items": {"$ref": f"#/$defs/{RAW_RECORD_DEF}"},
                "description": (
                    "One entry per selected file. A record no shipped row claims "
                    "carries a non-null `unclaimed` reason; the count of those is "
                    "reported on every validator run."
                ),
            },
            "change_notice": {
                "type": ["string", "null"],
                "description": (
                    "Non-null when the extraction itself is an adaptation a "
                    "`state_changes` source obliges us to describe; null "
                    "otherwise, since the notice is an obligation attached to "
                    "specific sources rather than a blanket disclaimer."
                ),
            },
        },
        "$defs": {
            RAW_RECORD_DEF: {
                "type": "object",
                "description": (
                    "A fact record. Its key set equals the allowlist exactly, "
                    "enforced from both sides: this schema is generated from the "
                    "allowlist, and `additionalProperties: false` closes it in "
                    "the other direction."
                ),
                "required": sorted(properties),
                "additionalProperties": False,
                "properties": properties,
            }
        },
    }


def generate_hardcoded_refs_schema() -> dict[str, object]:
    """The whole `hardcoded_refs.json` schema, built from the allowlist.

    The second facts store gets the same three guarantees as the first, and for
    the same reason: an entry holds a `domain.object_id`, a repo, a scope and a
    naming-convention label, none of which is authored text, and the guarantee
    would be worth nothing if a later round could add a field able to hold the
    alias or the comment the entry was drawn from. So this file, too, is
    *generated* -- from `hardcoded_fields`, the second section of the same
    allowlist -- and its entry carries `additionalProperties: false`. Every field
    is required for the same reason the record's are: the key set equals the
    allowlist, not a subset of it.

    The string fields resolve through `fact-shapes.yaml`, so their patterns are
    the committed shapes' and not a copy that could drift. `slot` resolves
    through `catalog/slots.yaml` to the product's own slot vocabulary, exactly as
    the raw record's `slots` field does -- the one place a slot name is allowed
    to come from.
    """
    shapes = fact_shapes()
    fields = hardcoded_fields()
    properties = _field_properties(fields, shapes, HARDCODED_FIELDS_KEY)
    entry_def = _hardcoded_entry_def(properties)

    return {
        "$id": f"{SCHEMA_URI_PREFIX}catalog/{HARDCODED_REFS_SCHEMA_FILE}",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Hardcoding audit",
        "description": (
            "Generated from schemas/catalog/fact-fields.yaml by "
            "tools/catalog/facts.py; edit the allowlist and regenerate rather "
            "than editing this file. Every distinct `entity_ref` the extraction "
            "found, with its source repo, its inferred scope and its repo's "
            "naming-convention label. This is the second committed facts store "
            "and it carries identifiers from all four repos including the two "
            "that grant nothing: an entry holds a `domain.object_id`, a scope and "
            "a naming-convention label, and none of those is authored text. No "
            "field here can hold an alias, a display name, a comment or a YAML "
            "fragment from a source, which is what makes the withheld-expression "
            "position structural rather than declarative."
        ),
        "schema_version": "1.0.0",
        "type": "object",
        "required": ["refs"],
        "additionalProperties": False,
        "properties": {
            "refs": {
                "type": "array",
                "items": {"$ref": f"#/$defs/{HARDCODED_ENTRY_DEF}"},
                "description": (
                    "One entry per distinct `domain.object_id` in a repo. A "
                    "service call never appears here: the extraction classifies "
                    "each reference structurally and only entity references enter "
                    "the audit."
                ),
            }
        },
        "$defs": {
            HARDCODED_ENTRY_DEF: entry_def,
        },
    }


def _hardcoded_entry_def(properties: dict[str, object]) -> dict[str, object]:
    """One audit entry's schema, with the entry-level `oneOf` the allowlist names.

    The `oneOf` is the structural half of "every entry resolves to a slot or a
    constant": each branch admits the entry only when one of the pair is non-null,
    so an entry with both null matches no branch and one with both set matches two,
    and either way the schema refuses it. `check_slots` reports the same defect by
    naming the reference and its source repo; this closes the shape the schema
    admits to the shape the data are allowed to take, so a future writer who
    validates against the schema alone still cannot commit a both-null entry.
    """
    entry: dict[str, object] = {
        "type": "object",
        "description": (
            "An audit entry. Its key set equals the allowlist exactly, "
            "enforced from both sides: this schema is generated from the "
            "allowlist, and `additionalProperties: false` closes it in "
            "the other direction."
        ),
        "required": sorted(properties),
        "additionalProperties": False,
        "properties": properties,
    }
    one_of = hardcoded_entry_one_of()
    unknown = [name for name in one_of if name not in properties]
    if unknown:
        raise CheckError(
            FACT_CHECK,
            f"schemas/catalog/{FACT_FIELDS_FILE}",
            f"names {unknown} in `{HARDCODED_ONE_OF_KEY}`, which "
            f"`{HARDCODED_FIELDS_KEY}` does not declare; a one-of over a field "
            "that is not in the entry cannot constrain anything",
        )
    if one_of:
        entry["oneOf"] = [
            {"properties": {name: {"not": {"type": "null"}}}} for name in one_of
        ]
    return entry


def _admits_arbitrary_text(pattern: str) -> bool:
    """Whether a `pattern` shape really constrains anything.

    The test is the probe string above: a shape that matches prose admits prose,
    and the whole point of a shape is that it does not. `^.*$` and `^[\\s\\S]*$`
    match it; a tight identifier class does not. A pattern that will not compile
    is treated as admitting everything, because a field the validator cannot even
    apply is not a constraint either.
    """
    try:
        return re.search(pattern, _PROSE_PROBE) is not None
    except re.error:
        return True


def _check_generated(
    report: Report,
    where: str,
    fields: dict[str, object],
    shapes: dict[str, object],
    committed_path: Path,
    generate: Callable[[], dict[str, object]],
    section: str,
    def_name: str,
) -> None:
    """One generated schema, against its allowlist and its committed copy.

    Three findings, and they are separate on purpose. A field that declares no
    permitted form -- a free-form string, or a pattern of its own rather than a
    named shape -- is named as it is resolved. A committed schema that differs
    from a fresh generation is named, because a generated file that is
    hand-editable is not generated. And the field sets must be equal in both
    directions: a schema property absent from the allowlist and an allowlist
    entry absent from the schema are different defects and the message says
    which one the reader is looking at.
    """
    for name in sorted(fields):
        try:
            fragment = _form_schema(
                as_mapping(fields[name]), shapes, f"{where}:{section}.{name}"
            )
        except CheckError as exc:
            report.add(exc.check, exc.where, exc.message)
            continue
        pattern = as_text(fragment.get("pattern"))
        if pattern is not None and _admits_arbitrary_text(pattern):
            report.add(
                FACT_CHECK,
                f"{where}:{section}.{name}",
                f"resolves to pattern {pattern!r}, which matches arbitrary "
                "printable text; a string fact names a committed shape that "
                "constrains it, and a pattern admitting prose is no constraint",
            )

    try:
        generated = generate()
    except CheckError as exc:
        # The per-field loop above runs `_form_schema` once per field and so
        # reports the first field that will not resolve; `generate` resolves the
        # same fields again and raises on that same field. Reporting the second
        # arrival would put one defect in the report twice, and a caller counting
        # or asserting on exactly one diagnostic per location -- which the
        # acceptance fixtures do -- would see a duplicate where there is one
        # finding.
        if not any(
            diagnostic.check == exc.check
            and diagnostic.where == exc.where
            and diagnostic.message == exc.message
            for diagnostic in report.diagnostics
        ):
            report.add(exc.check, exc.where, exc.message)
        return

    committed = _load_json_or_report(report, committed_path)
    if committed is None:
        return

    expected = as_mapping(as_mapping(generated.get("$defs")).get(def_name))
    actual = as_mapping(as_mapping(committed.get("$defs")).get(def_name))
    expected_properties = set(as_mapping(expected.get("properties")))
    actual_properties = set(as_mapping(actual.get("properties")))
    for name in sorted(actual_properties - expected_properties):
        report.add(
            FACT_CHECK,
            f"schemas/catalog/{committed_path.name}",
            f"carries property `{name}`, which `schemas/catalog/{FACT_FIELDS_FILE}` "
            "does not allow; a field set can grow past the guarantee only by "
            "editing this schema alone, which is the edit this check exists to "
            "refuse",
        )
    for name in sorted(expected_properties - actual_properties):
        report.add(
            FACT_CHECK,
            f"schemas/catalog/{FACT_FIELDS_FILE}",
            f"allows `{section}.{name}`, which the committed "
            f"schemas/catalog/{committed_path.name} does not carry; the "
            "allowlist and the schema it is generated from have drifted apart",
        )

    if render_schema(generated) != render_schema(committed):
        report.add(
            FACT_CHECK,
            f"schemas/catalog/{committed_path.name}",
            "differs from a fresh generation from "
            f"schemas/catalog/{FACT_FIELDS_FILE}; it is generated, so it must be "
            "regenerated rather than edited, and a hand edit is exactly the drift "
            "the generation is meant to prevent",
        )


def _load_json_or_report(report: Report, path: Path) -> dict[str, object] | None:
    relative = path.relative_to(paths.ROOT).as_posix()
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        report.add(FACT_CHECK, relative, f"cannot be read: {exc}")
        return None
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        report.add(FACT_CHECK, relative, f"is not valid JSON: {exc}")
        return None
    document = as_mapping(loaded)
    if not document:
        report.add(FACT_CHECK, relative, "is not a JSON object")
        return None
    return document


def check_fact_allowlist(report: Report) -> None:
    """The allowlist, the closed shape set, and the two schemas generated from them.

    The record store and the audit store are one guarantee applied twice: a
    committed facts file whose schema is generated from a named allowlist, whose
    string fields name committed shapes, and whose field set is closed in both
    directions. Checking them in one pass is what makes the second store adopt the
    first's guarantee rather than re-argue it -- and it is the check that fails
    when a later round adds `description: {type: string}` to either schema, which
    is the one edit the guarantee exists to refuse.

    The shape set is checked before either schema, because a permissive shape is
    a defect in the guarantee itself and a field naming it would otherwise be
    reported as a field defect.
    """
    shapes = fact_shapes()
    for name in sorted(shapes):
        spec = as_mapping(shapes[name])
        kind = as_text(spec.get("kind"))
        if kind not in ("pattern", "membership"):
            report.add(
                FACT_CHECK,
                f"schemas/catalog/{FACT_SHAPES_FILE}:{name}",
                f"declares kind {kind!r}; the known kinds are `pattern` and "
                "`membership`, and a shape of any other kind admits nothing this "
                "check can reason about",
            )
            continue
        if kind != "pattern":
            continue
        pattern = as_text(spec.get("pattern"))
        if pattern is None:
            report.add(
                FACT_CHECK,
                f"schemas/catalog/{FACT_SHAPES_FILE}:{name}",
                "declares no `pattern`, so a field naming it constrains nothing",
            )
        elif _admits_arbitrary_text(pattern):
            report.add(
                FACT_CHECK,
                f"schemas/catalog/{FACT_SHAPES_FILE}:{name}",
                f"declares pattern {pattern!r}, which matches arbitrary printable "
                "text; a shape that admits prose is not a constraint, and the "
                "guarantee would rest on the regex a field author writes",
            )

    _check_generated(
        report,
        f"schemas/catalog/{FACT_FIELDS_FILE}",
        fact_fields(),
        shapes,
        raw_record_schema_path(),
        generate_raw_record_schema,
        "fields",
        RAW_RECORD_DEF,
    )
    _check_generated(
        report,
        f"schemas/catalog/{FACT_FIELDS_FILE}",
        hardcoded_fields(),
        shapes,
        hardcoded_refs_schema_path(),
        generate_hardcoded_refs_schema,
        HARDCODED_FIELDS_KEY,
        HARDCODED_ENTRY_DEF,
    )


def render_schema(document: dict[str, object]) -> str:
    """The canonical text of a generated schema.

    Sorted keys and a fixed indent, because task 3.5 asserts that the committed
    schema equals a fresh generation: byte-identity has to depend on the
    allowlist alone and not on the order a dict happened to be built in.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def raw_record_schema_path() -> Path:
    return paths.SCHEMA_CATALOG / RAW_RECORD_SCHEMA_FILE


def hardcoded_refs_schema_path() -> Path:
    return paths.SCHEMA_CATALOG / HARDCODED_REFS_SCHEMA_FILE


def write_raw_record_schema() -> Path:
    """Regenerate the committed schema from the allowlist."""
    path = raw_record_schema_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_schema(generate_raw_record_schema()).encode("utf-8"))
    return path


def write_hardcoded_refs_schema() -> Path:
    """Regenerate the audit-entry schema from the allowlist."""
    path = hardcoded_refs_schema_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_schema(generate_hardcoded_refs_schema()).encode("utf-8"))
    return path
