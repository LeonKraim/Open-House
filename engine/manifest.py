"""Validating a pack manifest against the four authorities that judge it.

A manifest is not one document checked by one rule. It is judged by the *schema*
(what a document may be), by the *licence vocabulary* (what a code means and
where it sits in the published order), by the *corpus* (what a row grants to a
pack derived from it) and by the *marker* (which files a person wrote rather than
a derivation produced). Each authority is read through `engine/vocabulary.py`, so
this module opens no catalog or schema path of its own -- the property
`engine/sandbox.py` is held to, and for the same reason: one place decides which
artifact answers which question.

**The schema runs first, and nothing else runs if it fails.** A document the
schema refuses is not a manifest, so asking whether its `engine_api` admits the
engine's version would be asking a question about a field the document does not
have. The spec states this for one clause and the rule is general:
`pack-manifest`'s "A missing or malformed range fails validation, not the check"
is the schema-then-check order written down for `engine_api`, and a validator that
reached the range check anyway would report an incompatibility for a pack that has
no range. So the passes are ordered and the second one is guarded by the first,
which is also what makes one defect one failure.

**Three schema failures are reclassified, and the reasons say why.** `pack-cli`
publishes a class list in which `schema`, `unknown term` and `licence` are three
different classes with three different remedies, so a validator that filed every
schema failure under `schema` would make exactly the distinctions the list exists
for invisible. A behaviour term outside `behavior-vocabulary` is `unknown_term`
(naming the pack, the axis and the term); a `license` outside the published codes
is `unknown_licence` (naming the value and the codes); and a manifest carrying
`min_engine_version` is `retired_clause`, because `1.2.0` retired it in favour of
`engine_api` and "Additional properties are not allowed" would send the author
looking for a typo rather than a migration. Nothing else is reclassified: a
structural failure stays `schema` and its message names the constraint that
refused it.

**No reason is declared that no document can produce.** A malformed range never
appears here, because the schema's `pattern` refuses one before the range check is
reached -- `semver.parse_range` and the schema's pattern admit the same grammar
(`engine/semver.py`), so a `SemverError` raised from this module would be a defect
in that module rather than a fact about a pack, and it is deliberately not caught.
Catching it would report a pack failure for the engine's own bug, which is the one
error a validator must never make.

This module is data-in, data-out: it reads no clock, holds no state, and touches
nothing but the document it was handed and the artifacts the gateway read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator
from referencing.exceptions import Unresolvable

from engine import semver
from engine.vocabulary import ManifestArtifacts, Vocabulary, runtime_registry

#: Every reason a failure can carry, in the order a reader meets them: the schema
#: and its three reclassifications, then the four authorities' own checks. Closed,
#: because a reason invented at the point of a check would be one no caller could
#: enumerate -- and it is the finest distinction this module draws, so `pack-cli`'s
#: coarser classes (`unknown term`, `derivation`) fold *from* this list rather than
#: being restated in it.
REASONS: tuple[str, ...] = (
    "schema",
    "unknown_term",
    "unknown_licence",
    "retired_clause",
    "engine_api_mismatch",
    "self_dependency",
    "unknown_source_row",
    "ideas_only_source",
    "licence_too_restrictive",
    "handwritten_derivation",
    "missing_default",
    "override_without_default",
    "option_mismatch",
    "unknown_option",
)

#: The clause `1.2.0` retired. Named here because the failure has to name it: a
#: manifest carrying it is not merely wrong, it is written against a clause that
#: no longer exists, and the remedy is `engine_api`.
_RETIRED_CLAUSE = "min_engine_version"

#: The three behaviour axes, whose values are `$ref`s to the published
#: vocabulary. A schema failure landing exactly on one of these is the schema
#: refusing a *term*, and it is the one schema failure whose reason is finer than
#: `schema`.
_TERM_AXES = ("trigger", "condition", "action")

#: The two keys a manifest's own name and description take in `i18n.default`,
#: beside one key per declared behaviour.
#:
#: The schema says *which* names need a default -- "the pack's own name and
#: description, and each declared behaviour's name" -- and cannot say what those
#: two keys are called, because a static schema cannot name the keys a document
#: will declare. The spelling is therefore the one hand-written manifest's:
#: `packs/official/example-pack.yaml` spells them `pack` and `description`, and
#: the requirement that the shipped example validates is what pins it. A pack that
#: spelled the first `name` would collide with a behaviour named `name`, which is
#: the other reason the two clause keys are spelled as clauses.
_CLAUSE_KEYS = ("pack", "description")

#: What a row's `reuse_status` must not be for a pack to reproduce its expression.
#: The corpus is the authority and this is the one value of that field the gate
#: refuses, so the value is named rather than the comparison inverted: a status
#: this phase does not know about is not silently treated as reusable.
_IDEAS_ONLY = "ideas_only"


class MalformedManifestError(Exception):
    """A file that is not a manifest at all.

    Distinct from a validation failure, and the distinction is `pack-cli`'s: a
    file that will not parse is a *usage* error about the thing the caller
    pointed at, and reporting it as a pack failure would tell an author their
    manifest is wrong when the truth is that it is not readable.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path.as_posix()} is not a manifest: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Manifest:
    """A manifest file and the document it holds."""

    path: Path
    document: Mapping[str, object]

    @property
    def name(self) -> str:
        """The pack's own name, or the file's when the document has none.

        A failure has to name the pack it is about, and a document missing `name`
        is exactly the case where the field is unavailable -- so the fallback is
        to the file, which is still a name a reader can act on.
        """
        declared = self.document.get("name")
        return declared if isinstance(declared, str) else self.path.name


@dataclass(frozen=True, slots=True)
class Failure:
    """One reason a manifest was refused, and where.

    `path` is the failing instance path in the document -- `<document>` for the
    whole of it, `/behaviours/0/action` for a term -- and it is the field
    `pack-cli` reports beside the class, so a caller can point at the clause
    without parsing the message. `message` names the constraint that refused it,
    because the class alone does not say what was expected.
    """

    reason: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ManifestResult:
    """A manifest's outcome: no failures, or every one of them."""

    failures: tuple[Failure, ...]

    @property
    def ok(self) -> bool:
        """Whether the manifest validated."""
        return not self.failures


def load_manifest(path: Path) -> Manifest:
    """Read a manifest file, failing by naming what is wrong with it.

    A file that is absent, unreadable, unparseable or not a mapping is a
    `MalformedManifestError` rather than a validation failure, because none of
    those four is a statement about a pack.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedManifestError(
            path, f"it cannot be read ({exc.strerror})"
        ) from exc
    try:
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MalformedManifestError(path, f"it cannot be parsed ({exc})") from exc
    except RecursionError as exc:
        raise MalformedManifestError(
            path, "it is nested too deeply to be parsed"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise MalformedManifestError(path, "it is not a mapping of clauses")
    return Manifest(path=path, document=cast("Mapping[str, object]", loaded))


def validate_manifest(
    manifest: Manifest,
    artifacts: ManifestArtifacts,
    vocabulary: Vocabulary,
) -> ManifestResult:
    """Every failure the manifest has, in one pass each.

    The schema pass runs alone when it fails; the four authorities' checks run
    together when it does not, so a manifest that is a pack is judged by all of
    them and a manifest that is not is judged once.
    """
    schema_failures = _schema_failures(manifest, artifacts)
    if schema_failures:
        return ManifestResult(failures=schema_failures)
    return ManifestResult(failures=_semantic_failures(manifest, artifacts, vocabulary))


def strings(manifest: Manifest, locale: str | None = None) -> Mapping[str, str]:
    """The pack's user-visible strings, resolved for a locale.

    With no locale this is `i18n.default`, which is why the block is required
    rather than optional: a name with no default is a name some locale cannot
    render. With a locale it is the default with that locale's overrides applied
    on top, so a locale that overrides one behaviour reads the override there and
    the default everywhere else, and a locale the pack does not mention at all
    reads the default unchanged. There is no third case: an override with no
    default behind it is refused by `validate_manifest` rather than resolved here.
    """
    i18n = _mapping(manifest.document.get("i18n"))
    resolved = dict(_strings_of(_mapping(i18n.get("default"))))
    if locale is None:
        return resolved
    overrides = _mapping(_mapping(i18n.get("locales")).get(locale))
    resolved.update(_strings_of(overrides))
    return resolved


def _schema_failures(
    manifest: Manifest, artifacts: ManifestArtifacts
) -> tuple[Failure, ...]:
    """Every schema failure, each under the reason it actually is.

    Sorted before classification so the report is deterministic: the schema's
    error order depends on the order of its own clauses, which is a fact about the
    artifact rather than about the document, and two runs over one manifest must
    not report the same failures in two orders.
    """
    validator = cast(
        "Validator",
        Draft202012Validator(
            artifacts.schema.document, registry=runtime_registry(artifacts.root)
        ),
    )
    try:
        errors = sorted(
            validator.iter_errors(cast("Any", manifest.document)),
            key=lambda error: (list(error.absolute_path), error.message),
        )
    except Unresolvable as exc:
        # A schema whose own `$ref` does not resolve is a checkout fault and not a
        # pack's. Raised rather than reported against the manifest, because an
        # author sent to fix a pack over a broken repository would be sent to the
        # wrong file -- the failure `engine/vocabulary.py` raises for a missing
        # artifact, arriving from a different direction.
        raise MalformedManifestError(
            manifest.path,
            f"the schema it is validated against does not resolve ({exc})",
        ) from exc
    return tuple(_classify(error, manifest, artifacts) for error in errors)


def _classify(
    error: ValidationError, manifest: Manifest, artifacts: ManifestArtifacts
) -> Failure:
    """One schema error, under the reason it actually is."""
    path = _pointer(error)
    if _is_retired_clause(error):
        return Failure(
            reason="retired_clause",
            path=path,
            message=(
                f"the pack {manifest.name!r} declares {_RETIRED_CLAUSE!r}, which "
                f"{artifacts.schema.version} retired; `engine_api` is the semver "
                "range that replaced it, and it is a range because the clause it "
                "replaced pinned a lower bound and could express no upper one"
            ),
        )
    axis = _term_axis(error)
    if axis is not None:
        term = error.instance
        return Failure(
            reason="unknown_term",
            path=path,
            message=(
                f"the pack {manifest.name!r} names {term!r} as its {axis}, and the "
                "behaviour vocabulary this schema references publishes no such "
                f"term on that axis"
            ),
        )
    if _is_licence(error):
        published = ", ".join(artifacts.licences.codes)
        return Failure(
            reason="unknown_licence",
            path=path,
            message=(
                f"the pack {manifest.name!r} declares the licence {error.instance!r}, "
                f"which is not one of the published codes ({published}); the SPDX "
                "identifier is a property of the code and not a spelling of it"
            ),
        )
    return Failure(
        reason="schema",
        path=path,
        message=f"{error.message} (the `{error.validator}` constraint)",
    )


def _pointer(error: ValidationError) -> str:
    """A validation error's instance path, or `<document>` for the whole of it."""
    return "/".join(str(part) for part in error.absolute_path) or "<document>"


def _is_retired_clause(error: ValidationError) -> bool:
    """Whether this error is `min_engine_version` arriving where it is not allowed.

    Asked of the *instance* rather than of the message: the error says only that
    some additional property is unexpected, and which one is a fact about the
    document the error was raised over.
    """
    return (
        error.validator == "additionalProperties"
        and isinstance(error.instance, Mapping)
        and _RETIRED_CLAUSE in error.instance
    )


def _term_axis(error: ValidationError) -> str | None:
    """The behaviour axis this error refused a term on, if it is one of them.

    A `$ref` to the vocabulary's `items` fails as an `enum`, and its absolute path
    is the behaviour's own position -- `/behaviours/0/action`. Anything else at
    another depth is a different failure that happens to mention a term.
    """
    path = list(error.absolute_path)
    if error.validator != "enum" or len(path) != 3:
        return None
    if path[0] != "behaviours" or path[2] not in _TERM_AXES:
        return None
    return str(path[2])


def _is_licence(error: ValidationError) -> bool:
    """Whether this error refused the `license` clause's value."""
    return error.validator == "enum" and list(error.absolute_path) == ["license"]


def _semantic_failures(
    manifest: Manifest, artifacts: ManifestArtifacts, vocabulary: Vocabulary
) -> tuple[Failure, ...]:
    """The five authorities' own checks, over a document the schema accepted.

    The document conforms, so the casts below are the schema's guarantees rather
    than hopeful ones -- the same footing `engine/binding.py` validates on. The
    fifth, `_option_failures`, is the one clause whose rules are comparisons
    *between* properties and so cannot live in a static schema at all.
    """
    document = manifest.document
    failures: list[Failure] = []
    failures.extend(_engine_api_failures(document, vocabulary))
    failures.extend(_declaration_failures(document))
    failures.extend(_derivation_failures(manifest, artifacts))
    failures.extend(_i18n_failures(manifest))
    failures.extend(_option_failures(document))
    return tuple(failures)


#: What a `default` has to be for each option `type`, so the check below is a
#: table lookup rather than a chain of branches. `bool` is tested before `int`
#: everywhere a number is, because Python's `bool` *is* an `int` and a manifest
#: writing `default: true` for an `integer` would otherwise pass as `1`.
_OPTION_TYPES: Mapping[str, tuple[type, ...]] = {
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
    "string": (str,),
    "enum": (str,),
    "duration": (int,),
}

#: The option types a `minimum`/`maximum` pair means anything for, and so the
#: types whose bounds are checked against each other.
_RANGED_OPTION_TYPES = frozenset({"integer", "number", "duration"})


def _option_failures(document: Mapping[str, object]) -> list[Failure]:
    """The option clause's own checks, which a static schema cannot make.

    Every one of these is a comparison *between* two properties, and the manifest
    schema is a static document: it can say an option carries a `type` and a
    `default`, and it cannot say the `default` is of that type. The repository
    answers that kind of question in Python and says so where the schema would
    have asked it (`$defs/i18n`'s coverage rule is the precedent), because the
    alternative is a schema nobody can read.

    Four checks, and each is a defect the panel would otherwise render:

    - **The default is of the declared type.** An `integer` option defaulting to
      `"5"` is a control that opens empty, because the value a person sees is the
      one the resolver found and the resolver found a string.
    - **An `enum` carries members and nothing else does.** A `type: enum` with no
      `enum` admits no value; an `enum` beside a `boolean` is a clause nothing
      reads and everybody copies.
    - **A range is a range.** `minimum` above `maximum` admits nothing, and is
      worth refusing here rather than rendering as a slider that cannot be moved.
    - **A behaviour's `for` names a `duration` option.** The clause *is* the name
      of an option; a name that is not declared, or is declared as something
      other than a duration, is a behaviour whose reading would never hold --
      which evaluates as a behaviour that silently never acts, the failure mode
      hardest to notice from the outside.
    """
    options = _rows(document, "options")
    failures: list[Failure] = []
    durations: set[str] = set()
    for index, option in enumerate(options):
        key = option.get("key")
        kind = option.get("type")
        where = f"options[{index}]"
        if isinstance(key, str) and kind == "duration":
            durations.add(key)
        failures.extend(_one_option_failures(option, where=where, kind=kind, key=key))
    for index, behaviour in enumerate(_rows(document, "behaviours")):
        named = behaviour.get("for")
        if isinstance(named, str) and named not in durations:
            failures.append(
                Failure(
                    reason="unknown_option",
                    path=f"behaviours[{index}].for",
                    message=(
                        f"the behaviour {behaviour.get('name', '?')!r} waits on "
                        f"{named!r}, which is not a `duration` option this pack "
                        f"declares"
                    ),
                )
            )
    return failures


def _one_option_failures(
    option: Mapping[str, object], *, where: str, kind: object, key: object
) -> list[Failure]:
    """The four checks that need only one option to make."""
    name = key if isinstance(key, str) else "?"
    failures: list[Failure] = []
    expected = _OPTION_TYPES.get(kind) if isinstance(kind, str) else None
    if expected is not None and not _is_of_type(option.get("default"), expected):
        failures.append(
            Failure(
                reason="option_mismatch",
                path=f"{where}.default",
                message=(
                    f"the option {name!r} is of type {kind!r}, so its default must "
                    f"be {_type_name(expected)}; the manifest gives "
                    f"{option.get('default')!r}"
                ),
            )
        )
    members = option.get("enum")
    if kind == "enum" and not (isinstance(members, list) and members):
        failures.append(
            Failure(
                reason="option_mismatch",
                path=f"{where}.enum",
                message=(
                    f"the option {name!r} is an enum, so it must list the members "
                    f"it admits; the manifest lists none"
                ),
            )
        )
    if kind != "enum" and members is not None:
        failures.append(
            Failure(
                reason="option_mismatch",
                path=f"{where}.enum",
                message=(
                    f"the option {name!r} is of type {kind!r} and lists enum "
                    f"members, which nothing reads"
                ),
            )
        )
    if isinstance(kind, str) and kind in _RANGED_OPTION_TYPES:
        low = option.get("minimum")
        high = option.get("maximum")
        if _is_number(low) and _is_number(high) and low > high:
            failures.append(
                Failure(
                    reason="option_mismatch",
                    path=f"{where}.minimum",
                    message=(
                        f"the option {name!r} admits nothing: its minimum {low} is "
                        f"above its maximum {high}"
                    ),
                )
            )
    return failures


def _is_of_type(value: object, expected: tuple[type, ...]) -> bool:
    """Whether `value` is one of `expected`, with `bool` kept apart from `int`."""
    if isinstance(value, bool):
        return bool in expected
    return isinstance(value, expected)


def _is_number(value: object) -> bool:
    """Whether `value` is a real number and not a `bool`, which Python counts as one."""
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _type_name(expected: tuple[type, ...]) -> str:
    """The type's name for a message: the union spelled out when it is one."""
    return " or ".join(sorted({member.__name__ for member in expected}))


def _engine_api_failures(
    document: Mapping[str, object], vocabulary: Vocabulary
) -> list[Failure]:
    """Whether the manifest's range admits the engine's declared API version.

    The version comes from the `engine-api` artifact and never from
    `pyproject.toml`, whose `version` is a packaging value that moves for
    packaging reasons: a range checked against it would be a lie the first time
    the package was rebuilt without the API changing.
    """
    declared = cast("str", document["engine_api"])
    version = vocabulary.engine_api_version
    if semver.satisfies(version, declared):
        return []
    return [
        Failure(
            reason="engine_api_mismatch",
            path="engine_api",
            message=(
                f"the pack {cast('str', document.get('name', '?'))!r} declares "
                f"engine_api {declared!r}, which excludes the engine's declared API "
                f"version {version}"
            ),
        )
    ]


def _declaration_failures(document: Mapping[str, object]) -> list[Failure]:
    """Whether the manifest depends on itself.

    A cycle and an unsatisfied range are not asked about here: both are questions
    about a *house*, resolved against the set of packs actually installed, and a
    manifest on its own has no such set. What a manifest can be wrong about on its
    own is naming itself, because the pack it asks for cannot be installed beside
    the pack asking.
    """
    name = cast("str", document["name"])
    failures: list[Failure] = []
    for index, entry in enumerate(_rows(document, "dependencies")):
        requested = cast("str", entry["name"])
        if requested == name:
            failures.append(
                Failure(
                    reason="self_dependency",
                    path=f"dependencies/{index}",
                    message=(
                        f"the pack {name!r} depends on itself with the range "
                        f"{cast('str', entry['range'])!r}; a pack cannot be installed "
                        "beside itself, so the dependency can never be satisfied"
                    ),
                )
            )
    return failures


def _derivation_failures(
    manifest: Manifest, artifacts: ManifestArtifacts
) -> list[Failure]:
    """Whether the pack's `derives_from` names rows that may ground it, and under
    a licence its own code permits.

    Four questions, and the corpus answers three of them. The marker answers the
    fourth, and it is asked first because a hand-written pack carrying the clause
    is wrong whatever the clause names: a file a person wrote reproduces no row's
    expression, so naming a row is a claim about provenance that is false rather
    than a claim about licensing that is too broad.
    """
    rows = manifest.document.get("derives_from")
    if not isinstance(rows, list):
        return []
    licence = cast("str", manifest.document["license"])
    failures: list[Failure] = []
    relative = _relative(manifest.path, artifacts.root)
    if relative is not None and relative in artifacts.handwritten:
        failures.append(
            Failure(
                reason="handwritten_derivation",
                path="derives_from",
                message=(
                    f"the pack {manifest.name!r} is listed in the hand-written marker "
                    f"and carries `derives_from`; {relative} was written by a person, "
                    "so it reproduces no row's expression and the clause is a claim "
                    "about provenance that is false"
                ),
            )
        )
    for index, identifier in enumerate(cast("list[object]", rows)):
        row = artifacts.corpus.get(cast("str", identifier))
        if row is None:
            failures.append(
                Failure(
                    reason="unknown_source_row",
                    path=f"derives_from/{index}",
                    message=(
                        f"the pack {manifest.name!r} derives from {identifier!r}, which "
                        "is no row of `catalog/behaviors.yaml`; a misspelled id is not "
                        "a derivation"
                    ),
                )
            )
            continue
        if row.reuse_status == _IDEAS_ONLY:
            failures.append(
                Failure(
                    reason="ideas_only_source",
                    path=f"derives_from/{index}",
                    message=(
                        f"the pack {manifest.name!r} derives from {row.id!r}, whose "
                        f"reuse_status is {row.reuse_status!r}; the row may inform a "
                        "hand-written pack but may not be a source of reproduced "
                        "expression, because the corpus records that its licence "
                        f"({row.license}) grants nothing to reproduce"
                    ),
                )
            )
            continue
        if artifacts.licences.more_restrictive(licence, row.license):
            failures.append(
                Failure(
                    reason="licence_too_restrictive",
                    path="derives_from",
                    message=(
                        f"the pack {manifest.name!r} is licensed {licence!r} over "
                        f"expression it derives from {row.id!r}, which is {row.license!r}; "
                        "claiming a narrower licence than the source grants is a claim "
                        "the source does not support, judged in the order "
                        f"{', '.join(artifacts.licences.codes)} publishes"
                    ),
                )
            )
    return failures


def _i18n_failures(manifest: Manifest) -> list[Failure]:
    """Whether every declared name has a default, and every override a default.

    The schema can require the block and its non-emptiness; it cannot require the
    keys, because the keys are the names the document declares. So this is the
    half of the requirement a schema cannot carry: the set of names is read from
    the manifest's own `behaviours` and its two clauses, and a locale that
    overrides a name nothing declares is refused -- an override is a replacement
    for something, and a replacement with nothing behind it is a string that
    disappears in every other locale.
    """
    i18n = _mapping(manifest.document["i18n"])
    defaults = _mapping(i18n.get("default"))
    declared = _declared_names(manifest.document)
    failures = [
        Failure(
            reason="missing_default",
            path="i18n/default",
            message=(
                f"the pack {manifest.name!r} declares the name {name!r} and its "
                "`i18n.default` gives no string for it; a name with no default is a "
                "name some locale cannot render"
            ),
        )
        for name in sorted(declared - set(defaults))
    ]
    for locale, overrides in sorted(_mapping(i18n.get("locales")).items()):
        for key in sorted(set(_mapping(overrides)) - set(defaults)):
            failures.append(
                Failure(
                    reason="override_without_default",
                    path=f"i18n/locales/{locale}",
                    message=(
                        f"the pack {manifest.name!r} overrides {key!r} for locale "
                        f"{locale!r} and `i18n.default` has no {key!r} for it to "
                        "replace; an override with nothing behind it is a string no "
                        "other locale can render"
                    ),
                )
            )
    return failures


def _declared_names(document: Mapping[str, object]) -> set[str]:
    """Every name the manifest declares that needs a default string.

    The pack's own two clause keys and one key per declared behaviour's name. A
    behaviour that declares no name declares no user-visible name, so it needs no
    default -- which is why the walk skips one rather than reporting it.
    """
    names = set(_CLAUSE_KEYS)
    for behaviour in _rows(document, "behaviours"):
        name = behaviour.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _relative(path: Path, root: Path) -> str | None:
    """A path relative to the root, or `None` when it is not under it.

    A manifest outside the tree -- one a caller passed by absolute path from
    somewhere else -- is not in the marker and not in any pack directory, so the
    hand-written question is answered "no" rather than raising on a `ValueError`
    that says nothing about the pack.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _mapping(value: object) -> Mapping[str, object]:
    """A value as a mapping, or an empty one.

    Called only on clauses the schema has already typed -- except in `strings`,
    which a caller may reach without validating first, and where an absent block
    reading as no strings is the answer the caller asked for.
    """
    return cast("Mapping[str, object]", value) if isinstance(value, Mapping) else {}


def _strings_of(value: Mapping[str, object]) -> Mapping[str, str]:
    """A string map with only its string values, which the schema guarantees."""
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _rows(document: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    """One clause's rows, which the schema has already typed as a list.

    Absent reads as empty: the clauses are conditionally required, so a pack of a
    kind that does not carry one simply has none.
    """
    value = document.get(field)
    if not isinstance(value, list):
        return []
    return [
        cast("Mapping[str, object]", row) for row in value if isinstance(row, Mapping)
    ]
