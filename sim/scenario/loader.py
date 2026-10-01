"""Loading a scenario from YAML: a safe read, a schema, and the validated types.

`scenario-runner` asks for two things here and they are the whole module: a
**safe loader** -- a scenario is data, and a document that could construct an
object would be a document that runs code before anything validated it -- and a
**schema**, because the failure a schema prevents is a scenario that "passes"
because a misspelled key was silently ignored. A `then: []` that asserts nothing
is a green run that proves nothing, and no amount of careful reading catches one
that a validator would have refused.

*Which* version of the schema is not decided here. `tools.catalog.schemas` owns
the question and this module asks it, so publishing a `1.1.0` of the scenario
schema changes what every scenario is validated against without a second place to
remember -- the same reason `openhouse/packs.py` reads its schema the same way.

The module never builds a house. A `given` block is resolved into the `Scenario`
value -- the fixture name is checked against the registry so an unknown one fails
at load rather than at the first step, the instant is parsed, and the seed and the
flags are defaulted -- and *building* is the composition root's act, because
choosing a repository root and opening a session are things `sim/` may not do
(`design.md` D12). Loading a scenario is reading a document; running one is
somebody else's job, and the split is what lets a corpus be validated without a
single entity being created.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012, Schema

from sim.fixtures import DEFAULT_SEED, DEFAULT_STARTED_AT, FIXTURE_NAMES
from tools.catalog import paths
from tools.catalog.narrow import as_mapping
from tools.catalog.schemas import current_version, load_versions

from .assertions import AVAILABILITY, STATE, LogExpectation, StateExpectation
from .model import Given, Scenario, Step, Then

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from jsonschema.exceptions import ValidationError

    Expectation = StateExpectation | LogExpectation

__all__ = ["LoadError", "load_directory", "load_scenario"]

#: The concept whose schema a scenario validates against, spelled once because it
#: is both the `schemas/<concept>/` directory name and what a diagnostic about a
#: missing schema says.
CONCEPT = "scenario"

#: How a scenario file is recognised in a corpus directory. Two suffixes rather
#: than one, because `.yml` is the same format and refusing it would be a
#: checker's opinion rather than a rule.
SUFFIXES = (".yaml", ".yml")

#: Every runtime schema is published under this prefix, and a `$ref` that names
#: anything else is not one this project wrote.
SCHEMA_URI_PREFIX = "https://open-house.invalid/schemas/"

#: Where an error has no path of its own: the document itself.
ROOT = "<root>"


class LoadError(Exception):
    """A scenario that will not load, naming the file, the place and the reason.

    All three are carried apart from the message as well as inside it, because a
    caller that has to report which file in a corpus failed -- and the corpus
    requirement says one bad file fails the run *naming the file* -- needs the
    path without parsing a sentence for it, and because a location of `<root>` is
    a real answer (the top level is wrong) that a message alone renders
    indistinguishably from an absent one.
    """

    def __init__(self, path: str, location: str, reason: str) -> None:
        self.path = path
        self.location = location
        self.reason = reason
        super().__init__(f"{path}: {location}: {reason}")


def load_scenario(path: str | Path) -> Scenario:
    """Load the scenario at `path`, or raise `LoadError` naming what is wrong.

    Reading, validating and building happen in that order and each is a separate
    function, so a fault in one is not reported as a fault in the next: a document
    that will not parse never reaches the schema, and a document that fails the
    schema never reaches the builders -- which is what keeps a builder's failure
    an honest statement that the *schema* did not close the set it claimed to.
    """
    source = Path(path)
    document = _read(source)
    _validate(document, source)
    return _build(document, source)


def load_directory(directory: str | Path) -> tuple[Scenario, ...]:
    """Load every scenario file in `directory`, by name, oldest sort first.

    Every file is loaded even though the first failure aborts, which is the
    opposite of what it looks like: nothing is caught, so a corpus with one bad
    file fails on that file and names it, and a corpus whose files are all good
    loads all of them. What it does not do is run anything -- a directory of
    scenarios is a set of documents until a runner is handed one.
    """
    root = Path(directory)
    return tuple(
        load_scenario(path)
        for path in sorted(
            candidate
            for candidate in root.iterdir()
            if candidate.is_file() and candidate.suffix in SUFFIXES
        )
    )


def _read(source: Path) -> Mapping[str, object]:
    """The file's YAML, as a mapping, or a `LoadError` naming the file.

    A safe loader, so a scenario is data and cannot construct anything; a document
    that is not a mapping is refused here rather than left for the schema, because
    "this scenario is a list" is not a schema violation a reader would expect to
    hunt for in a validator's output.
    """
    try:
        loaded: object = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LoadError(str(source), ROOT, f"it could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise LoadError(str(source), ROOT, "it is not a YAML mapping")
    return cast("Mapping[str, object]", loaded)


def _validate(document: Mapping[str, object], source: Path) -> None:
    """Fail on the first way `document` is not a scenario.

    The first and not all of them: a document has one shape, and a list of
    findings about a shape that is wrong is a list about a document that will be
    rewritten anyway. The message is the validator's own, because it names the
    offending key far better than a restatement here would, and the location is
    prepended so a reader has the key *and* where it sits.
    """
    current = current_version(load_versions(CONCEPT))
    if current is None:
        raise LoadError(
            str(source),
            ROOT,
            f"no single current {CONCEPT} schema is published to validate against",
        )
    validator = cast(
        "Validator", Draft202012Validator(current.document, registry=_registry())
    )
    errors: list[ValidationError] = list(validator.iter_errors(cast("Any", document)))
    if not errors:
        return
    error = min(errors, key=lambda item: list(item.absolute_path))
    raise LoadError(str(source), _location(error), error.message)


def _location(error: ValidationError) -> str:
    """Where a validator's error sits, in the spelling a YAML reader would use."""
    return "/".join(str(step) for step in error.absolute_path) or ROOT


def _build(document: Mapping[str, object], source: Path) -> Scenario:
    """The validated document as the `Scenario` value, with its inputs resolved."""
    name = document.get("name")
    given = _given(cast("Mapping[str, object]", document["given"]), source)
    steps = cast("Sequence[object]", document["when"])
    assertions = cast("Sequence[object]", document["then"])
    return Scenario(
        name=name if isinstance(name, str) and name else source.stem,
        path=source.as_posix(),
        given=given,
        when=tuple(_step(entry) for entry in steps),
        then=Then(
            tuple(
                _expectation(entry, source, index)
                for index, entry in enumerate(assertions)
            )
        ),
    )


def _given(block: Mapping[str, object], source: Path) -> Given:
    """The `given` block, with its defaults resolved and its fixture checked.

    The fixture name is *checked* here rather than only projected when a session
    is opened, which is the one thing this function does beyond defaulting: the
    requirement is that an unknown fixture "fails the run before the first step
    executes", and a corpus check that had to open a session to learn a name was
    misspelled would not be a check a corpus can be run through.
    """
    house = block["house"]
    if isinstance(house, str) and house not in _fixture_names():
        raise LoadError(
            str(source),
            "given/house",
            f"no fixture is named {house!r}; the fixtures are "
            f"{', '.join(sorted(_fixture_names()))}",
        )
    return Given(
        house=cast("str | Mapping[str, object]", house),
        seed=cast("int", block.get("seed", DEFAULT_SEED)),
        started_at=_instant(block.get("started_at"), source),
        enable_flags=cast("Mapping[str, object]", block.get("enable_flags", {})),
    )


def _fixture_names() -> frozenset[str]:
    """The fixture registry's names, as the strings a document writes."""
    return frozenset(str(item) for item in FIXTURE_NAMES)


def _instant(value: object, source: Path) -> datetime:
    """The run's starting instant, parsed, or the fixture's own when absent.

    A timestamp with no offset is refused rather than assumed to be UTC. The
    engine never reads the wall clock, so this string is the only "when" a run
    has, and a start that meant one instant locally and another in CI would make
    two runs of one document differ for a reason no reader of the document could
    see.
    """
    if value is None:
        return DEFAULT_STARTED_AT
    if not isinstance(value, str):
        raise LoadError(
            str(source), "given/started_at", f"{value!r} is not a timestamp string"
        )
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LoadError(
            str(source),
            "given/started_at",
            f"{value!r} is not an ISO 8601 timestamp: {exc}",
        ) from exc
    if moment.tzinfo is None:
        raise LoadError(
            str(source),
            "given/started_at",
            f"{value!r} carries no UTC offset, so the instant it names is ambiguous",
        )
    return moment


def _step(entry: object) -> Step:
    """One `when` entry: its single key is the verb, its value the parameters.

    The single-key shape is the schema's, and it is taken apart with an unpacking
    that would raise on a second key rather than a loop that would silently read
    the first -- but the schema has already refused that document, so what the
    unpacking states is the assumption the builder is entitled to make.
    """
    mapping = cast("Mapping[str, object]", entry)
    ((verb, parameters),) = mapping.items()
    return Step(verb=verb, parameters=cast("Mapping[str, object]", parameters))


def _expectation(entry: object, source: Path, index: int) -> Expectation:
    """One `then` entry, as the expectation its key names.

    The four kinds are the schema's closed set, so the fall-through is
    unreachable for a validated document and is written anyway: a builder that
    returned nothing would make `Then` hold fewer assertions than the document
    wrote, which is the exact failure -- a scenario asserting less than it says --
    the schema exists to prevent.
    """
    mapping = cast("Mapping[str, object]", entry)
    ((kind, body),) = mapping.items()
    where = f"then/{index}/{kind}"
    if kind == STATE:
        return StateExpectation(
            entity_id=_text(body, "entity_id"), field=STATE, expected=_value(body, "is")
        )
    if kind == AVAILABILITY:
        return StateExpectation(
            entity_id=_text(body, "entity_id"),
            field=AVAILABILITY,
            expected=_value(body, "is"),
        )
    if kind == "attribute":
        return StateExpectation(
            entity_id=_text(body, "entity_id"),
            field=_text(body, "name"),
            expected=_value(body, "is"),
        )
    if kind == "log":
        return _log(cast("Mapping[str, object]", body), source, where)
    raise LoadError(str(source), where, f"no assertion kind is named {kind!r}")


def _log(block: Mapping[str, object], source: Path, where: str) -> LogExpectation:
    """A `log` assertion, with the one rule a schema cannot state.

    `because` is the form that says a record was produced by a cause, and a
    citation naming neither a rule nor an outcome says only "something happened"
    -- which is the assertion D2 exists to replace. The schema constrains a
    record's shape and cannot say which of its fields is load-bearing, so the
    refusal is here.
    """
    because = block.get("because")
    if because is not None:
        citation = cast("Mapping[str, object]", because)
        if "rule" not in citation and "outcome" not in citation:
            raise LoadError(
                str(source),
                f"{where}/because",
                "it names neither a rule nor an outcome, so it cites nothing",
            )
    return LogExpectation(
        must=_matchers(block.get("must"), source, f"{where}/must"),
        must_not=_matchers(block.get("must_not"), source, f"{where}/must_not"),
        because=cast("Mapping[str, object] | None", because),
    )


def _matchers(
    value: object, source: Path, where: str
) -> tuple[Mapping[str, object], ...]:
    """A `must` or `must_not` list as a tuple of partial records.

    Absent reads as empty, which is the honest reading: an assertion that names
    no positive matcher asserts no positive record, and the schema has already
    refused a `log` assertion naming none of the three.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise LoadError(str(source), where, f"{value!r} is not a list of records")
    return tuple(
        cast("Mapping[str, object]", entry) for entry in cast("Sequence[object]", value)
    )


def _text(body: object, name: str) -> str:
    """A string field of an assertion, which the schema has already typed."""
    return cast("str", cast("Mapping[str, object]", body)[name])


def _value(body: object, name: str) -> object:
    """A field of an assertion, left as the document wrote it."""
    return cast("Mapping[str, object]", body)[name]


def _registry() -> Registry[Schema]:
    """A registry resolving the runtime schemas' published `$id`s to files.

    Built per call rather than at import time so it follows whatever root
    `tools.catalog.paths` is pointed at -- the suite redirects that root into a
    temporary tree, and a registry built once would keep resolving against the
    repository.

    This is the fourth near-identical retriever in the tree, after
    `tools/catalog/validate.py`, `tools/catalog/examples.py` and
    `openhouse/packs.py`, and the duplication is a cost paid knowingly. The three
    that exist are each local for a stated reason -- a `sim/` module may not
    import `openhouse/` (D12), and the tooling package is heavier than the twenty
    lines it would save -- and extracting a shared one would mean writing a new
    module into a closed phase's package. When Phase 2 has a reason to touch
    `tools/catalog/` anyway, this is the one thing it should consolidate.
    """
    return Registry(retrieve=_retrieve)


def _retrieve(uri: str) -> Resource[Schema]:
    """Resolve a `$ref` naming another committed runtime schema.

    Anything that will not resolve -- an absent file, one that will not parse, or
    a path that escapes `schemas/` -- is reported as unresolvable rather than
    raised through, so a malformed neighbour reaches the caller as a diagnostic
    about the scenario being loaded rather than as a traceback about a file the
    caller never named.
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
    if not isinstance(loaded, dict):
        raise NoSuchResource(ref=uri)
    return Resource(
        contents=as_mapping(cast("Mapping[str, object]", loaded)),
        specification=DRAFT202012,
    )
