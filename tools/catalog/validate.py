"""The validator: the whole catalogue checked in one pass.

`oh-catalog validate` is the command every other part of the project leans on --
it is the pre-commit hook, it is the CI gate, and it is what the acceptance
script in task 8.1 drives. It is therefore one function, so that "the validator
passed" means the same thing everywhere it is said.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import yaml
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource, Unresolvable
from referencing.jsonschema import DRAFT202012, Schema

from . import (
    attribution,
    behaviors,
    ci,
    conformance,
    examples,
    exceptions,
    facts,
    golden,
    identifier_gate,
    integrations,
    invariants,
    inventory,
    lexicon,
    licenses,
    normalise,
    notices,
    paths,
    repos,
    room_types,
    rooms,
    rules,
    schemas,
    scope,
    seeds,
    slots,
)
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CATALOG_CHECK = "catalog-schema"

#: Every way resolving a reference can fail.
#:
#: One class, and that is a finding rather than an oversight. `referencing`
#: exposes six resolution failures and they do not share a base:
#: `NoSuchResource` and `Unretrievable` descend from `KeyError`,
#: `CannotDetermineSpecification` from `Exception` directly, and only
#: `Unresolvable` covers a pointer that leads nowhere or an anchor that does not
#: exist -- which reads like a reason to catch all six.
#:
#: Measured, it is not. `Resolver.lookup` normalises every missing-resource
#: failure to `Unresolvable`, including one raised as `NoSuchResource` by
#: `_retrieve` below; `_resource` states its dialect instead of sniffing it, so
#: `CannotDetermineSpecification` cannot arise; and a document with no `$id` is
#: rejected before either runs, so `NoInternalID` cannot. An earlier revision
#: caught all six on the strength of the hierarchy alone, and no input could be
#: constructed that the narrower catch missed -- a wider catch that nothing can
#: reach is not safety, it is a claim the code does not make good on.
_RESOLUTION_FAILURE = Unresolvable

#: Every runtime and catalog schema is published under this prefix, which is what
#: makes a cross-file `$ref` resolvable to a file in this repository rather than
#: to the network.
SCHEMA_URI_PREFIX = "https://open-house.invalid/schemas/"


def check_catalog_data_files(report: Report) -> None:
    """Every data file under `catalog/` validates against its catalog schema.

    The pairing is by filename stem: `catalog/slots.yaml` is validated by
    `schemas/catalog/slots.json`. A data file with no schema fails naming the
    file, because the alternative -- skipping it -- would let a new file arrive
    unvalidated simply by not adding a schema for it.

    Having a schema next to a file is not the claim. The claim is that the file
    *validates* against it, so the instance is loaded and checked. An earlier
    revision of this function asserted only that the schema file existed, which
    is a different thing and a much weaker one: every stub would have passed
    while nothing looked at what any of them contained, and a data file that
    contradicted its own schema would have been reported clean.

    Markdown is exempt. `README.md` and `overlap.md` are documents; requiring a
    schema for prose would mean inventing one to satisfy the checker.

    A schema that is itself broken -- unparseable, missing its `$id`, or naming a
    `$ref` that resolves to nothing -- is reported against the schema rather than
    allowed to escape, and the loop continues. Both halves of that matter. An
    unresolvable `$ref` is raised by the validator in the middle of iteration,
    and `validate_all` catches only `CheckError`, so before this was handled the
    command that is the pre-commit hook died with a traceback. And a schema
    failure used to abort the whole loop, which meant one malformed catalog
    schema hid every finding about every other data file.
    """
    for data_file in paths.catalog_data_files():
        relative = data_file.relative_to(paths.ROOT).as_posix()
        schema_path = paths.SCHEMA_CATALOG / f"{data_file.stem}.json"
        schema_relative = schema_path.relative_to(paths.ROOT).as_posix()
        if not schema_path.is_file():
            report.add(
                CATALOG_CHECK,
                relative,
                f"no schema at schemas/catalog/{data_file.stem}.json; every data "
                "file under catalog/ must validate against one",
            )
            continue

        try:
            document = load_catalog_schema(data_file.stem)
        except CheckError as exc:
            report.add(exc.check, exc.where, exc.message)
            continue

        metadata = _missing_metadata(document)
        if metadata:
            report.add(CATALOG_CHECK, schema_relative, metadata)
            continue

        registry = _registry_for(document)
        written, malformed = _references(document)
        if malformed:
            # Reported and skipped, not walked past. A `$ref` that is not a
            # string is not a reference, and validation reaches it anyway: the
            # walk would have passed the document, and `jsonschema` raises
            # `AttributeError` when it tries to resolve an integer. That is not a
            # resolution failure -- it is not in the caught family at all -- so
            # it would surface as a traceback from the pre-commit hook.
            for rendered in malformed:
                report.add(
                    CATALOG_CHECK,
                    schema_relative,
                    f"has a `$ref` of {rendered}, which is not a string; a "
                    "reference is a JSON string naming another schema, and one "
                    "that is not a string names nothing",
                )
            continue

        # After the walk, not before it, and the order is load bearing. A `$ref`
        # value that is not a string is also a meta-schema failure -- the
        # meta-schema says `$ref` is a string -- so checking the schema first
        # would answer `"$ref": 42` with "is not of type 'string'" and leave the
        # branch below a report nothing can reach. The specific diagnostic is the
        # better one, so the general check waits its turn.
        invalid = _invalid_schema(document)
        if invalid:
            report.add(CATALOG_CHECK, schema_relative, invalid)
            continue

        unresolved = _unresolved_references(written, registry, _base_uri(document))
        if unresolved:
            for reference in unresolved:
                report.add(
                    CATALOG_CHECK,
                    schema_relative,
                    f"names {reference}, which does not resolve to a valid schema "
                    "in this repository; a catalog schema references the runtime "
                    "schemas rather than restating them, and a reference that "
                    "resolves to nothing -- or to a file that is not itself a "
                    "valid schema -- states nothing at all",
                )
            continue

        # `Any`, not `object`. The instance is whatever the file parsed to, and
        # `jsonschema` accepts any JSON value as one, so the honest annotation is
        # the one the library itself uses. `object` would be a claim that the
        # validator is narrower than it is, and strict mode would then reject the
        # call it is written to make.
        instance: Any = _load_data_file(data_file)
        if isinstance(instance, _Unparsed):
            report.add(CATALOG_CHECK, relative, instance.reason)
            continue

        # Widened to the protocol, which looks like a no-op and is not. The
        # concrete class is typed by a typeshed stub whose two `iter_errors`
        # overloads both leave the instance parameter unannotated, so strict mode
        # calls every call to it partially unknown -- for any argument, since the
        # gap is in the stub. Annotating the variable is not enough: pyright
        # narrows it back to `Draft202012Validator` from the right-hand side and
        # reads the member from there. `cast` is the one form that does not
        # narrow, and `protocols.Validator` declares the same method with both
        # the instance and the return type filled in.
        validator = cast("Validator", Draft202012Validator(document, registry=registry))
        errors: list[ValidationError] = []
        try:
            for error in validator.iter_errors(instance):
                errors.append(error)
        except _RESOLUTION_FAILURE as exc:
            # Reached by a reference the static walk cannot see, and the walk is
            # deliberately blind to one kind: `$dynamicRef` has no static target,
            # so its anchor is only looked up here, while validating. That is why
            # this catch is not redundant with the walk above -- remove it and a
            # `$dynamicRef` naming a missing anchor escapes as a traceback from
            # the pre-commit hook.
            report.add(
                CATALOG_CHECK,
                relative,
                f"cannot be validated: the schema references {_brief(exc)}, "
                "which does not resolve",
            )
        except Exception as exc:
            # The backstop, and it is reached rather than defensive. A `$ref`
            # cycle between two files is meta-schema-valid -- `$ref` is a string
            # to the meta-schema, which never follows one -- so `check_schema`
            # passes both documents, the walk above resolves both references
            # because each is present, and `iter_errors` then follows the cycle
            # until it raises `RecursionError`.
            # `test_a_reference_cycle_is_a_diagnostic_not_a_traceback` drives
            # exactly that. Anything else the library raises while applying a
            # schema lands here too, which is the point: this function's contract
            # is that a schema which cannot be applied is a diagnostic, and a
            # contract with a closed list of exceptions is a contract with holes.
            # The list cannot be closed here for a second reason -- the four
            # malformed keywords above raise `UnknownType`, `TypeError`,
            # `re.error` and `ZeroDivisionError`, which share no base.
            report.add(
                CATALOG_CHECK,
                relative,
                f"cannot be validated: the schema could not be applied: "
                f"{_first_line(exc)}",
            )
        for error in sorted(errors, key=_error_order):
            report.add(CATALOG_CHECK, relative, _describe(error))


class _Unparsed:
    """A sentinel for a data file that did not load, carrying why it did not.

    A module-level instance rather than `None`, because `null` is a legitimate
    YAML document and a file containing exactly `null` must be validated rather
    than reported as unreadable.

    The reason travels on the sentinel so that the two ways a load can fail stay
    distinguishable at the diagnostic. They are not the same finding and do not
    call for the same next move: a syntax error is a character to go and correct,
    while a document too deep to parse is a structure to flatten.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason


_UNREADABLE = _Unparsed(
    "cannot be read as YAML or JSON, so it cannot be validated against its schema"
)
_TOO_DEEP = _Unparsed(
    "is nested too deeply to be parsed, so it cannot be validated against its schema"
)


def _load_data_file(path: Path) -> object:
    """A data file's contents, or a sentinel saying why it could not be loaded.

    `RecursionError` needs its own arm. It is a `RuntimeError`, so it is neither a
    `yaml.YAMLError` nor a `json.JSONDecodeError`, and it is what the parsers
    raise -- not a parse error -- when the *nesting* gets too deep for the stack.
    Measured on this interpreter with the default limit of 1000: `yaml.safe_load`
    loads a sequence nested 300 deep and raises on one nested 500 deep, and
    `json.loads` holds past 1000 and raises between there and 1500. Those two
    are the only ceilings measured. A nested YAML *mapping* is not a measurement
    of the same kind and is not quoted as one: the obvious way to render one,
    `a: a: a: 1`, is a syntax error at every depth, so it raises `ScannerError`
    -- a `YAMLError`, already caught by the arm above -- and says nothing about
    how deep a mapping may go. Left uncaught, a `RecursionError` here escapes
    `validate_all`, which catches only `CheckError`, and takes the pre-commit
    hook down with a traceback instead of naming the file.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return _UNREADABLE
    try:
        if path.suffix == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        return _UNREADABLE
    except RecursionError:
        return _TOO_DEEP


def _error_order(error: ValidationError) -> tuple[list[str], str]:
    """A total order over validation errors, so the output is deterministic.

    `iter_errors` yields in whatever order the schema's keywords happen to be
    walked, and task 3.8 asserts byte-identical output from a re-run. Path
    elements are stringified because they are a mix of keys and list indices, and
    comparing the two kinds raises.
    """
    return [str(part) for part in error.absolute_path], error.message


def _brief(exc: Exception) -> str:
    """A resolution failure's message, without the schema it was looking in.

    `referencing` appends the entire resource to an anchor failure -- "NoSuchAnchor:
    'x' does not exist within {...the whole document...}" -- which buries the
    anchor name the reader needs under a copy of a file they already have. The
    separator is stable across the failures that carry a resource, and the ones
    that do not (`Unresolvable: ../slot/9.9.9.json#/x`) pass through unchanged.
    """
    return str(exc).split(" within ")[0]


def _first_line(exc: Exception) -> str:
    """A library exception's message, without the document it inlines.

    `jsonschema` renders its errors as a multi-line block ending in the whole
    schema -- or the whole instance -- that produced them. In a pre-commit
    diagnostic that is most of a screen of a file the reader already has, and
    the finding is the first line.
    """
    return str(exc).splitlines()[0]


def _invalid_schema(document: Mapping[str, Any]) -> str | None:
    """Why a document is not a schema the check can apply, as one message or nothing.

    The document is checked against the 2020-12 meta-schema before it is applied.
    Without this the check dies instead of reporting: a schema whose JSON is well
    formed but whose keywords are wrong reaches `iter_errors` and raises
    something that is not a resolution failure -- `UnknownType` for
    `"type": "objectt"`, `TypeError` for `"maxItems": "x"`, `re.error` for an
    unterminated `pattern`, `ZeroDivisionError` for `"multipleOf": 0` -- and that
    escapes `validate_all`, which catches only `CheckError`. `schemas/catalog/`
    is the one schema directory edited in place as sections 2-7 proceed, so a
    keyword typo is a live hazard rather than a hypothetical one.

    Two exceptions, not one, because the meta-validator has two ways to refuse.
    `SchemaError` is a schema it read and rejected. `RecursionError` is one it
    never finished reading: it recurses once per nesting level, so a `properties`
    chain deep enough exhausts the stack first. Measured on this interpreter
    (limit 1000) with a schema of nested single-property objects: depth 80 is
    accepted, depth 100 raises. `json.loads` on that same document is fine at
    every one of those depths -- it is the later frame that dies. So the catch is
    reachable, and by an input far smaller than the one that defeats the loader;
    an earlier version of this docstring claimed the opposite and was wrong.

    `_retrieve` calls `check_schema` directly instead, without this arm, and that
    asymmetry is deliberate rather than an oversight: a `RecursionError` raised
    there is caught by `Registry.get_or_retrieve` and arrives as an
    `Unresolvable`, so it is reported either way and the arm would be dead code.
    Measured, not reasoned: see the note at that call site.
    """
    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as exc:
        return f"is not a valid JSON Schema: {_first_line(exc)}"
    except RecursionError:
        return (
            "is nested too deeply to be read as a JSON Schema: "
            "checking it exhausts the interpreter's recursion limit"
        )
    return None


def _describe(error: ValidationError) -> str:
    """One validation error, located rather than merely stated."""
    location = "/".join(str(part) for part in error.absolute_path) or "<document>"
    return f"{location}: {error.message}"


def _schema_path_for_uri(uri: str) -> Path | None:
    """Map a published schema URI back to the file that carries it.

    Containment is checked, not assumed: a `$ref` of `../../../../etc/passwd`
    resolves to a path outside the schemas tree, and a validator that read it
    would turn a typo in a schema into a read of something that is not a schema
    at all. A URI outside the tree, or one that escapes it, resolves to nothing
    and is reported as unresolvable.
    """
    if not uri.startswith(SCHEMA_URI_PREFIX):
        return None
    candidate = paths.SCHEMAS / uri[len(SCHEMA_URI_PREFIX) :]
    try:
        candidate.resolve().relative_to(paths.SCHEMAS.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _retrieve(uri: str) -> Resource[Schema]:
    """Resolve a `$ref` that names another committed schema file.

    A file that will not parse is reported as unresolvable rather than allowed to
    propagate. `json.JSONDecodeError` is a `ValueError`, and nothing between here
    and the pre-commit hook converts it, so a runtime schema with a stray comma
    would take the hook down with a traceback instead of naming the file -- and
    the file at fault is not even the one being validated.

    A file that parses but is not a *valid* schema is reported the same way, for
    the same reason and one more: the failure it would otherwise cause happens
    later and elsewhere, inside `iter_errors`, where the diagnostic would name
    the data file that happened to reference it rather than the schema at fault.
    """
    path = _schema_path_for_uri(uri)
    if path is None or not path.is_file():
        raise NoSuchResource(ref=uri)
    try:
        loaded: object = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NoSuchResource(ref=uri) from exc

    # A referenced file that parses but is not a schema is treated the same as
    # one that is not there, and the walk's message says "valid" for that reason.
    # A `$ref` into a document the library cannot apply does not fail at
    # resolution -- it fails later, inside `iter_errors`, where the file named in
    # the diagnostic would be the data file rather than the schema at fault.
    # Checking here attributes the failure to the reference that named it. The
    # referenced document is meta-validated whole, not at the fragment, because
    # the malformed keyword may sit in a branch the fragment does not reach and
    # that branch is still one the library will walk.
    #
    # This call carries no `RecursionError` arm, unlike `_invalid_schema`'s, and
    # that is measured rather than assumed. `Registry.get_or_retrieve` wraps the
    # whole retrieve callable in `except Exception` and re-raises as
    # `Unretrievable`, and `Resolver.lookup` maps that to `Unresolvable`, which is
    # what `_unresolved_references` catches -- so a deeply nested document that
    # exhausts the stack here is already reported against the reference that
    # named it. Adding the arm changes nothing: with it removed and this left as a
    # bare `check_schema`, the same diagnostic is produced byte for byte.
    document = as_mapping(loaded)
    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as exc:
        raise NoSuchResource(ref=uri) from exc

    return _resource(document)


def _registry() -> Registry[Schema]:
    """A registry built per call, so it follows whatever root it is pointed at.

    `paths.SCHEMAS` is redirected into a temporary tree by the test fixtures, and
    a registry built at import time would keep resolving against the repository
    no matter which tree the check was asked about.
    """
    return Registry(retrieve=_retrieve)


def _registry_for(
    document: Mapping[str, Any],
) -> Registry[Schema]:
    """A registry that also carries the document being validated.

    A schema's same-document `$ref` -- `#/$defs/entry` -- is resolved by
    `jsonschema` internally during instance validation, without the registry.
    Resolving one *ahead* of validation, which is what the reference walk below
    does, goes through the registry instead, and it has to be able to find the
    document it started from. Registering it under its own `$id` is also what
    makes a relative reference (`../slot/1.0.0.json`) resolve against the right
    base.
    """
    identifier = document.get("$id")
    if not isinstance(identifier, str) or not identifier:
        return _registry()
    return _registry().with_resource(identifier, _resource(document))


def _base_uri(document: Mapping[str, Any]) -> str:
    """The URI a document's relative references resolve against.

    Its `$id`, or nothing. A relative reference is meaningless without one --
    `../slot/1.0.0.json` resolved from no base is not a path to anything -- which
    is the whole reason `$id` is required of a catalog schema rather than merely
    conventional.
    """
    identifier = document.get("$id")
    return identifier if isinstance(identifier, str) else ""


def _references(document: object) -> tuple[list[str], list[str]]:
    """Every `$ref` in a schema document, split into string and non-string.

    Both halves are every literal reference in the document, in document order
    and deduplicated, not only the ones an instance happens to reach. A `$ref`
    sitting in a branch no example exercises is still a claim that the referenced
    schema exists -- a shape under `$defs` that nothing points at yet is the
    ordinary case -- and a broken one is a hole in exactly the guarantee this
    check makes: that a catalog schema references the runtime schemas rather than
    restating them. If the reference resolves to nothing, that guarantee is void
    and nothing else would notice.

    The non-string half is returned rather than skipped, and that is the half
    that matters most. Skipping is not neutral: the walk would pass the document,
    instance validation would reach the branch, and `jsonschema` would raise
    `AttributeError` trying to resolve an integer -- which is not a resolution
    failure, so the catch around validation would not see it, and it would reach
    the pre-commit hook as a traceback. Returning it lets the caller report it
    and stop before validation runs.
    """
    written: list[str] = []
    malformed: list[str] = []
    seen: set[str] = set()

    def record(text: str, into: list[str]) -> None:
        if text not in seen:
            seen.add(text)
            into.append(text)

    def walk(node: object) -> None:
        # Through `narrow`'s helpers rather than `isinstance(node, Mapping)`,
        # which is the same test but leaves the key and value types unknown and
        # so cannot be checked. A string node yields neither a mapping nor a
        # sequence and is therefore a leaf, which is what it should be.
        for key, value in as_mapping(node).items():
            if key == "$ref":
                if isinstance(value, str):
                    record(value, written)
                else:
                    # Rendered, because the diagnostic has to show what was
                    # written. `json.dumps` cannot fail here: the value came out
                    # of `json.loads`.
                    record(json.dumps(value, sort_keys=True), malformed)
            else:
                walk(value)
        for item in as_sequence(node):
            walk(item)

    walk(document)
    return written, malformed


def _resource(document: Mapping[str, Any]) -> Resource[Schema]:
    """A schema document as a `referencing` resource, with its dialect stated.

    `Resource.from_contents` is the obvious constructor and the wrong one here:
    it infers the dialect by reading `$schema`, and raises
    `CannotDetermineSpecification` for a document that does not carry it, so a
    schema missing one field would crash the pre-commit hook. This repository
    publishes draft 2020-12 and nothing else, so the dialect is stated rather
    than sniffed.
    """
    return Resource(contents=document, specification=DRAFT202012)


def _unresolved_references(
    references: Sequence[str], registry: Registry[Schema], base: str
) -> list[str]:
    """The given references that resolve to nothing.

    The resolver is given the document's base URI, because a relative reference
    is meaningless without one.
    """
    resolver = registry.resolver(base_uri=base)
    unresolved: list[str] = []
    for reference in references:
        try:
            resolver.lookup(reference)
        except _RESOLUTION_FAILURE:
            unresolved.append(reference)
    return unresolved


def _missing_metadata(document: Mapping[str, object]) -> str | None:
    """The metadata a catalog schema must carry, as one message or nothing.

    Task 1.6 requires every catalog schema to carry `$id` and `schema_version`.
    Both are enforced rather than taken on trust. `$id` because it is load
    bearing: it is the base a relative `$ref` resolves against, so a schema
    without one cannot make the cross-file reference the same task asks for.
    `schema_version` because the file set is uniform, the field is required of
    every member, and a file that quietly lacks it is a file whose next reader
    finds that out instead of this check.
    """
    missing = [field for field in ("$id", "schema_version") if not document.get(field)]
    if not missing:
        return None
    return (
        f"does not carry {' or '.join(missing)}; every catalog schema carries "
        "both $id and schema_version"
    )


def load_catalog_schema(stem: str) -> dict[str, object]:
    """One catalog schema, as a mapping or a named failure.

    Raises `CheckError` rather than letting `json.JSONDecodeError` escape,
    because this runs inside `oh-catalog validate` and the command users run
    before every commit must name the file at fault rather than die with a
    traceback.
    """
    path = paths.SCHEMA_CATALOG / f"{stem}.json"
    relative = path.relative_to(paths.ROOT).as_posix()
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(CATALOG_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckError(CATALOG_CHECK, relative, f"is not valid JSON: {exc}") from exc
    document = as_mapping(loaded)
    if not document:
        raise CheckError(CATALOG_CHECK, relative, "is not a JSON object")
    return document


#: Every check that reads only this repository, in the order they run. A list
#: rather than a sequence of calls so that `validate_all` can wrap each one in
#: the same `CheckError` handling without repeating it at every site, and so that
#: adding a check is one line rather than two.
_CHECKS: tuple[Callable[[Report], None], ...] = (
    invariants.check_version_control,
    invariants.check_layout,
    invariants.check_engine_purity,
    invariants.check_registry_boundary,
    invariants.check_declared_dependency_names,
    schemas.check_runtime_schemas,
    schemas.check_immutability,
    check_catalog_data_files,
    licenses.check_licenses,
    repos.check_repos,
    rules.check_rules,
    golden.check_golden,
    inventory.check_inventory,
    exceptions.check_exceptions,
    conformance.check_conformance,
    facts.check_fact_allowlist,
    normalise.check_hardcoded_refs,
    room_types.check_room_types,
    rooms.check_rooms,
    slots.check_slots,
    integrations.check_integrations,
    seeds.check_seeds,
    lexicon.check_lexicon,
    behaviors.check_behaviors,
    behaviors.check_overlap,
    identifier_gate.check_identifier_gate,
    scope.check_scope,
    notices.check_notices,
    attribution.check_attribution,
    examples.check_examples,
    ci.check_ci_configuration,
)


def validate_all() -> Report:
    """Run every check that reads only this repository.

    The two clone-reading checks -- the `git ls-files` closure and the prose
    gate -- are deliberately absent: they run locally, their outputs are
    committed, and CI must pass without the clones present. Everything here is a
    pure function of the committed tree, which is what task 7.7 verifies by
    running the suite in a checkout with `ressources/` and `.local/` removed.

    A check that raises `CheckError` -- a schema that will not parse, a history
    that cannot be read -- is collected as a diagnostic rather than allowed to
    escape. Continuing past it would mean reporting findings derived from
    something already known to be broken, and letting it escape would mean the
    command that is the pre-commit hook and the CI gate could fail with a
    traceback instead of naming the file at fault.

    A `CheckError` is collected once, however many checks it reaches. Several
    checks read the same file and raise the same error from it -- a
    `catalog/licenses.yaml` that will not parse is raised by
    `licenses.load_licences`, which the licences check and every other check that
    needs a repo's status all call -- and rendered once per caller it is one
    line repeated with the same check, the same file and the same words. That
    reads as several defects and buries the diagnostics that are different, so a
    fact several checks each found is collapsed to the single diagnostic it is.
    The key is the whole rendered triple, so two genuinely different findings --
    which cannot share a check, a location and a message -- are never merged.
    """
    report = Report()
    seen: set[tuple[str, str, str]] = set()
    for check in _CHECKS:
        try:
            check(report)
        except CheckError as exc:
            key = (exc.check, exc.where, exc.message)
            if key in seen:
                continue
            seen.add(key)
            report.add(exc.check, exc.where, exc.message)
    return report
