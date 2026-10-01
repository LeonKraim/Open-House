"""Conformance: a concept shape has exactly one home.

Every runtime concept's shape is declared by its version files under
`schemas/<concept>/`, and nothing else may declare it. A second declaration is
not a style problem. A catalog schema or a pack that restates the slot fields
receives none of the edits the runtime schema receives, and no other check
compares the two, so the day the runtime schema gains or renames a field the copy
keeps validating the old shape while every check stays green. This is the check
that can see the copy.

The scope is drawn around the *runtime version directories* and not around
`schemas/`, and that is the load-bearing choice rather than a detail.
`schemas/catalog/` sits inside `schemas/`; it is a catalog data directory rather
than a runtime one, and it is the likeliest hiding place for a restated shape --
`slots.json` and `room_types.json` are exactly the files that describe slots and
room types, and each could have restated the shape where it references it. An
earlier draft scoped the check to "outside `schemas/`", which exempted the one
directory its own motivation named.

What counts as *declaring* a shape is the whole check, and it is a subset test
rather than a regex. A file declares `slot`'s shape when it carries a mapping --
any mapping in a `.json`, `.yaml` or `.yml` document anywhere but
`schemas/slot/` -- with a `properties` map whose names *include every property
the current `slot` schema requires*. That test is the narrowest one that still
fires on a copy and never on a use: a `$ref` into the runtime schema carries no
`properties`, so the correct way to use a shape passes, and a rule broad enough
to fail a `$ref` would make the check unusable. The required names are read from
the runtime schema rather than written here, so this module holds no second copy
of the shape it guards -- a signature written down would be the very drift it
exists to catch.

A pattern-equality rule was the alternative and was rejected on measurement.
`room-type`'s `name` pattern and `slot`'s are byte-identical, and the `house`
binding's `entity_id` pattern equals the `entity_ref` fact shape that
`hardcoded_refs.json` legitimately inlines -- so a rule keyed on a copied pattern
would both fire on a file doing the right thing and be unable to say which
concept had been duplicated.

One boundary is silent and is stated here so it is a decision. A file that
inlines a single property -- the slot `name` pattern alone, say -- is not caught:
it declares one field that `slot`, `room-type` and `house` all require, so no
single concept can be named as the one duplicated, and the requirement is about a
file declaring a *room-type, slot or house shape*, which the required set is what
identifies. A concept whose current schema requires no property likewise has no
signature, and this check guards nothing for it.

Files are enumerated through git -- tracked, plus untracked and not ignored -- so
the four reference clones and the virtualenv are out of scope by the ignore rules
rather than by a list kept here. A document the loader refuses is skipped: a
restated shape is found by reading a `properties` map, which an unparseable
document does not yield, so such a file holds nothing this check could report.
Parseability of the files that must load is `catalog-schema`'s subject, not this
one's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, paths, schemas, vcs
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Iterable

CONFORMANCE_CHECK = "conformance"

#: The concepts whose shape is one concept's to declare. The requirement names
#: exactly these three, and they are the runtime concepts the catalog's own data
#: files describe -- `catalog/slots.yaml` and `catalog/room_types.yaml` -- so
#: they are the ones a catalog schema could restate. `house` comes with them
#: because the example house is the third shape a pack could redeclare.
SHAPED_CONCEPTS: tuple[str, ...] = ("room-type", "slot", "house")

#: The formats a JSON Schema shape is written in. A shape is a structured
#: document, so a source file is out of scope by construction: code could restate
#: a shape only by writing it in another language's type system, which is not a
#: JSON Schema and not something this check could compare.
SHAPE_SUFFIXES: tuple[str, ...] = (".json", ".yaml", ".yml")

#: Returned by `_load_document` for a file the loader will not read. A distinct
#: object rather than `None`, because `null` is a legitimate YAML document that
#: parses to `None` and must not be confused with a file that failed to load.
_UNPARSED = object()


@dataclass(frozen=True, slots=True)
class ConceptShape:
    """One concept, the schema that owns its shape, and its identifying names."""

    concept: str
    #: The current version file, as every diagnostic spells paths: posix.
    schema: str
    #: The property names the current version requires. A mapping that declares
    #: all of them is declaring this concept's shape rather than using it.
    required: frozenset[str]


def _concept_shapes() -> tuple[ConceptShape, ...]:
    """The required property set of each shaped concept's current version.

    Read from the runtime schemas rather than written down, for the reason the
    module exists: a signature kept here would be a second declaration of the
    shape, free to drift from the published one -- the failure this check detects
    in every other file.
    """
    shapes: list[ConceptShape] = []
    for concept in SHAPED_CONCEPTS:
        current = schemas.current_version(schemas.load_versions(concept))
        if current is None:
            # Fatal rather than a finding, and not a duplicate of the
            # schema-versioning check's: without a current version there is no
            # shape to conform to, and the alternative -- continuing -- would pass
            # on a tree whose shapes are undefined, which is the one outcome this
            # check may not produce.
            raise CheckError(
                CONFORMANCE_CHECK,
                f"schemas/{concept}/",
                f"`{concept}` has no single current version, so the shape every "
                "other file is forbidden to restate cannot be read; the check "
                "would otherwise pass on a tree whose shapes are undefined",
            )
        document = current.document
        declared = set(as_mapping(document.get("properties")))
        required = frozenset(
            name
            for name in (
                as_text(item) for item in as_sequence(document.get("required"))
            )
            # A required name the schema does not also declare as a property
            # cannot be declared by a restatement either, so it is not part of
            # the identifying set.
            if name and name in declared
        )
        shapes.append(
            ConceptShape(
                concept=concept,
                schema=current.relative,
                required=required,
            )
        )
    return tuple(shapes)


def _load_document(relative: str) -> object:
    """A structured file's parsed contents, or `_UNPARSED`.

    The suffix decides the parser, matching the format the file is written in.
    `_UNPARSED` is returned rather than raised for both a read that failed and a
    document that would not load: this check's subject is the shapes a document
    declares, and a file that cannot be read declares none of them, so reporting
    it would be a conformance finding about a document this check never read.
    """
    try:
        text = errors.read_text(paths.ROOT / relative)
    except (OSError, UnicodeDecodeError):
        return _UNPARSED
    try:
        if relative.endswith(".json"):
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError, RecursionError):
        return _UNPARSED


def _property_named_schemas(document: object) -> list[tuple[str, set[str]]]:
    """Every mapping in a document that carries a `properties` map.

    The locator is the path walked to reach the mapping, so a diagnostic can
    point inside a file rather than only at it. The walk is iterative and refuses
    to revisit a node, because YAML anchors can make a document contain itself and
    a recursive walk would then not terminate. The guard is keyed on identity
    rather than on value, so a document that is genuinely self-referential and one
    that merely repeats a literal are told apart; only a container can hold
    another node, so skipping a repeated leaf costs nothing.
    """
    found: list[tuple[str, set[str]]] = []
    stack: list[tuple[object, str]] = [(document, "")]
    seen: set[int] = set()
    while stack:
        node, location = stack.pop()
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        mapping = as_mapping(node)
        properties = as_mapping(mapping.get("properties"))
        if properties:
            found.append((location, set(properties)))
        for key, value in mapping.items():
            stack.append((value, f"{location}/{key}"))
        for index, item in enumerate(as_sequence(node)):
            stack.append((item, f"{location}/{index}"))
    return found


def _message(shape: ConceptShape, location: str, names: Iterable[str]) -> str:
    """The finding: the file, the state it is in, and the schema it duplicates."""
    where = location or "<document>"
    declared = ", ".join(sorted(names))
    return (
        f"declares the `{shape.concept}` shape: the object at `{where}` declares "
        f"{declared}, which is exactly what `{shape.schema}` requires. A concept "
        "shape has one home, and a second declaration is free to drift from the "
        "published one, so this file must reference the runtime schema rather "
        "than restate it"
    )


def check_conformance(report: Report) -> None:
    """No file outside a concept's version directory declares its shape.

    One diagnostic per (file, concept): the requirement is that the file and the
    schema it duplicates are both named, and a file that restates one shape in two
    places is still one file restating one shape. The exemption is the concept's
    own directory, so a file under `schemas/slot/` may declare the slot shape and
    a file under `schemas/house/` may not.
    """
    shapes = _concept_shapes()
    for relative in sorted(vcs.listed_files()):
        if not relative.endswith(SHAPE_SUFFIXES):
            continue
        document = _load_document(relative)
        if document is _UNPARSED:
            continue
        _check_document(report, relative, document, shapes)


def _check_document(
    report: Report,
    relative: str,
    document: object,
    shapes: tuple[ConceptShape, ...],
) -> None:
    """Report each concept this one file declares, naming the duplicated schema."""
    matches: dict[str, tuple[str, set[str]]] = {}
    for location, keys in sorted(_property_named_schemas(document)):
        for shape in shapes:
            if shape.concept in matches:
                continue
            if relative.startswith(f"schemas/{shape.concept}/"):
                continue
            if shape.required and shape.required <= keys:
                matches[shape.concept] = (location, keys)
    for shape in shapes:
        found = matches.get(shape.concept)
        if found is not None:
            location, keys = found
            report.add(
                CONFORMANCE_CHECK,
                relative,
                _message(shape, location, shape.required & keys),
            )
