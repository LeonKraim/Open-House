"""The hand-written examples, and the allowlist that says they were hand-written.

Two claims are checked here, and they fail in different directions.

The first is that an example is a *document of a runtime concept* -- that
`example-house.yaml` is a house, `example-export.yaml` an export document and
`example-pack.yaml` a pack manifest, validated against the current version of the
concept's runtime schema, not against a snapshot taken when this file was
written. The pack's schema references the behaviour vocabulary, so this is also
where a pack that declares a behaviour term the vocabulary does not carry is
caught, naming the pack and the term. Everywhere else a pack is
data the project ships, and nothing validated it, because `check_catalog_data_files`
pairs only the files under `catalog/` with a schema. A pack that stopped being a
house would therefore be noticed by nobody.

The second is provenance, and it is deliberately weaker than it sounds. The
example house is hand-written because a generated example proves only that the
generator and the schema agree with each other, not that a person can write to
the schema. But there is no way to *detect* whether a file was generated -- the
`HANDWRITTEN` allowlist is a marker, not detection, and the spec says so. What
the marker buys is the cheap, checkable half: every pack file in the directory
must be named in the allowlist, and every name in the allowlist must be a file
that exists. That catches the two ways the marker would rot -- an example that
arrives without being claimed, and a claim left behind after the file it named
was deleted -- neither of which a reader would otherwise see.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import yaml
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource, Unresolvable
from referencing.jsonschema import DRAFT202012, Schema

from . import paths, schemas
from .errors import CheckError, Report, read_text
from .narrow import as_mapping

if TYPE_CHECKING:
    from pathlib import Path

CHECK = "handwritten-examples"

#: The committed allowlist, relative to the root. Not `packs/official/*.yaml`:
#: it is the list *of* those files, so it has no suffix and is never mistaken for
#: one of them.
HANDWRITTEN = "packs/official/HANDWRITTEN"

#: The pack documents this check validates, and the runtime concept each one is.
#:
#: Fixed names rather than a directory walk, because the set of shipped examples
#: is a decision rather than whatever happens to be in the directory: a file that
#: arrives unlisted is a pack-file finding, and a listed name with no concept is
#: one nothing can be validated against.
#:
#: `example-pack.yaml` is validated against `pack-manifest`, whose current version
#: references `behavior-vocabulary` -- so a pack that declares a behaviour outside
#: the vocabulary fails here, naming the pack and the term, and this check is the
#: pack validator `tests/test_vocabulary.py` supplies a stand-in for.
VALIDATED: tuple[tuple[str, str], ...] = (
    ("example-house.yaml", "house"),
    ("example-export.yaml", "export-document"),
    ("example-pack.yaml", "pack-manifest"),
)

#: The concept every other pack file is validated against.
#:
#: This exists because the tuple above is a hand-written list, and a hand-written
#: list of what to validate is a list a file can be absent from. Tasks 7.4 and
#: 7.5's eight new manifests arrived that way: they were named in `HANDWRITTEN`,
#: they passed the allowlist, `oh-catalog validate` was green -- and not one of
#: them had been validated against anything, because this check never looked at a
#: file it was not told about. The closure over the directory is what makes "each
#: shipped pack passes validation" a statement about the packs rather than about
#: the list.
#:
#: `pack-manifest` is the default and not an assumption about names: a file in
#: this directory that is not a house document or an export is a pack, which is
#: the only other thing the directory is for, and the two exceptions are named
#: above rather than inferred because their concepts do not follow from their
#: suffixes.
DEFAULT_CONCEPT = "pack-manifest"

#: Every runtime schema is published under this prefix, which is what makes a
#: cross-file `$ref` (`../slot/1.0.0.json`) resolve to a file in this repository
#: rather than to the network. The same prefix `validate.py` publishes under,
#: repeated rather than imported because it is the format of the `$id` field
#: inside the schemas, which both modules are reading rather than owning.
SCHEMA_URI_PREFIX = "https://open-house.invalid/schemas/"


def _packs_directory() -> Path:
    return paths.PACKS / "official"


def _pack_files() -> list[Path]:
    """The pack data files present, by suffix. `HANDWRITTEN` has none and so is
    not one of them, which is what keeps the allowlist out of the set it lists.
    """
    directory = _packs_directory()
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    ]


def _listed_names() -> set[str] | None:
    """The names the allowlist carries, or `None` if the allowlist is unreadable.

    `None` rather than an empty set so that "the list is missing" and "the list
    names nothing" stay distinguishable: the second would make every pack file
    read as unlisted, which is a cascade of findings derived from a file we could
    not open.
    """
    path = paths.ROOT / HANDWRITTEN
    if not path.is_file():
        return None
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(CHECK, HANDWRITTEN, f"cannot be read: {exc}") from exc
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.add(stripped)
    return names


def check_examples(report: Report) -> None:
    """The allowlist both ways, and every example against its current schema."""
    _check_allowlist(report)
    _check_documents(report)


def _check_allowlist(report: Report) -> None:
    """The two ways the marker rots, one diagnostic each, naming the file.

    A pack file present but unlisted is an example nothing has claimed; a listed
    name with no file behind it is a claim about a file that is gone. The first
    is the direction a generator fails in and the second is the direction an
    author fails in when they delete an example and forget the line.
    """
    listed = _listed_names()
    if listed is None:
        report.add(
            CHECK,
            HANDWRITTEN,
            "is missing; the hand-written example allowlist is how a pack file "
            "in this directory is claimed, and without it no example is",
        )
        return

    present = {path.name for path in _pack_files()}
    for name in sorted(present - listed):
        report.add(
            CHECK,
            f"packs/official/{name}",
            "is a pack file that is not named in packs/official/HANDWRITTEN; a "
            "file nothing claims as hand-written is a file no reader can tell "
            "was written rather than generated",
        )
    for name in sorted(listed - present):
        report.add(
            CHECK,
            HANDWRITTEN,
            f"names `{name}`, which is not under packs/official/; a claim about "
            "a file that does not exist is a claim that cannot be checked",
        )


def _check_documents(report: Report) -> None:
    """Every pack file is a document of its concept's current runtime schema.

    The named examples first, which is where a *missing* shipped artifact is
    reported, and then every other file in the directory as a pack -- the closure
    `DEFAULT_CONCEPT` explains. The two loops are separate because the first has
    a claim the second cannot make: a name in `VALIDATED` with no file behind it
    is a shipped artifact that has gone, and there is no list of the second kind
    to be absent from.
    """
    directory = _packs_directory()
    named = {filename for filename, _ in VALIDATED}
    documents: list[tuple[Path, str]] = [
        (directory / filename, concept) for filename, concept in VALIDATED
    ]

    for filename, _ in VALIDATED:
        if not (directory / filename).is_file():
            report.add(
                CHECK,
                f"packs/official/{filename}",
                "is missing; the example is a shipped artifact and its absence "
                "is what the CI exit criterion fails on",
            )

    for path in _pack_files():
        if path.name not in named:
            documents.append((path, DEFAULT_CONCEPT))

    for path, concept in documents:
        where = f"packs/official/{path.name}"
        if not path.is_file():
            # Reported by the loop above, and reported once.
            continue
        try:
            document = _current_schema(concept)
        except CheckError as exc:
            report.add(exc.check, exc.where, exc.message)
            continue
        instance = _load(path)
        if isinstance(instance, _Unreadable):
            report.add(CHECK, where, instance.reason)
            continue
        _apply(report, where, instance, document, _registry())


class _Unreadable:
    """A sentinel for an example file that did not load, carrying why.

    A sentinel rather than `None`, because `null` is a legitimate YAML document
    and an example that is exactly `null` must be validated against its schema --
    which will reject it by name -- rather than reported as unreadable.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _load(path: Path) -> object:
    """An example file's contents, or an `_Unreadable` saying why not."""
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError):
        return _Unreadable("cannot be read, so it cannot be validated")
    try:
        if path.suffix == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        return _Unreadable("cannot be parsed, so it cannot be validated")
    except RecursionError:
        return _Unreadable("is nested too deeply to be parsed")


def _current_schema(concept: str) -> dict[str, object]:
    """The current version document for a runtime concept, or a named failure.

    The *current* version rather than the lowest-numbered one, which is what
    makes the check follow a published change: when the concept gains a version,
    the example is validated against the new one without this file changing.
    """
    current = schemas.current_version(schemas.load_versions(concept))
    if current is None:
        raise CheckError(
            CHECK,
            f"schemas/{concept}/",
            "has no single current version, so the example has no schema to be "
            "validated against",
        )
    return current.document


def _registry() -> Registry[Schema]:
    """A registry that resolves the runtime schemas' published `$id`s to files.

    Built per call and not at import time, so it follows whatever root the
    `paths` module is pointed at; the tests redirect that root into a temporary
    tree, and a registry built once would keep resolving against the repository.
    """
    return Registry(retrieve=_retrieve)


def _retrieve(uri: str) -> Resource[Schema]:
    """Resolve a `$ref` naming another committed runtime schema.

    A file that will not parse, or that names a path outside the schemas tree, is
    reported the same way as an absent one -- unresolvable -- rather than allowed
    to propagate. Anything that escapes here would reach the pre-commit hook as a
    traceback instead of a diagnostic, and the file at fault would not even be
    the example being validated.
    """
    if not uri.startswith(SCHEMA_URI_PREFIX):
        raise NoSuchResource(ref=uri)
    candidate = paths.SCHEMAS / uri[len(SCHEMA_URI_PREFIX) :]
    try:
        candidate.resolve().relative_to(paths.SCHEMAS.resolve())
    except (OSError, ValueError) as exc:
        raise NoSuchResource(ref=uri) from exc
    if not candidate.is_file():
        raise NoSuchResource(ref=uri)
    try:
        loaded: object = json.loads(candidate.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NoSuchResource(ref=uri) from exc
    return Resource(contents=as_mapping(loaded), specification=DRAFT202012)


def _apply(
    report: Report,
    where: str,
    instance: object,
    schema: dict[str, object],
    registry: Registry[Schema],
) -> None:
    """Apply a schema to an example, reporting each failure by location.

    The validator is widened to the protocol for the reason `validate.py` gives:
    the typeshed stub for `Draft202012Validator` leaves `iter_errors`' instance
    parameter unannotated, so strict mode reports the call as partially unknown
    and annotating the variable does not help because pyright narrows it back
    from the right-hand side.
    """
    validator = cast("Validator", Draft202012Validator(schema, registry=registry))
    errors: list[ValidationError] = []
    try:
        for error in validator.iter_errors(cast("Any", instance)):
            errors.append(error)
    except Unresolvable as exc:
        report.add(
            CHECK,
            where,
            f"cannot be validated: the schema references {_brief(exc)}, "
            "which does not resolve",
        )
        return
    except RecursionError:
        report.add(
            CHECK,
            where,
            "cannot be validated: the schema is nested too deeply to apply",
        )
        return
    for error in sorted(errors, key=_error_order):
        report.add(CHECK, where, _describe(error))


def _brief(exc: Exception) -> str:
    """A resolution failure's message, without the resource it inlines."""
    return str(exc).split(" within ")[0]


def _error_order(error: ValidationError) -> tuple[list[str], str]:
    """A total order over validation errors, so the output is deterministic."""
    return [str(part) for part in error.absolute_path], error.message


def _describe(error: ValidationError) -> str:
    """One validation error, located rather than merely stated."""
    location = "/".join(str(part) for part in error.absolute_path) or "<document>"
    return f"{location}: {error.message}"
