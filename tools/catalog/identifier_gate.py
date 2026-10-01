"""The provenance-resolved identifier gate -- task 4.6.

A row may be written while some of its sources withhold reuse, because the
*concept* of a behaviour is a fact and facts are not owned. What is owned is the
particular spelling a repo gave it: the entity ids it happened to use. Copying
`light.kitchen_ceiling` out of a repo that granted nothing is copying an
identifier, even if the sentence around it is ours.

So the gate forbids a specific, derived set of strings rather than a hand-kept
list of patterns. The set is built *per row* by resolving that row's provenance:
take the raw records the row cites, union their `entity_refs`, and reject any of
those strings appearing in the row's free text -- but only when at least one
cited repo's code grant is `ideas_only`. The trigger and the forbidden set are
both functions of the row's own citations, which is what makes the gate
provenance-resolved rather than a global blacklist: an identifier that is fine in
a row derived from the granting repo is not smuggled in by a row that merely
happens to reuse the same word.

The fields scanned are read from the catalogue schema, not listed here. A field
added to the row schema is scanned the moment it is added, and a field removed
stops being scanned -- so the gate cannot fall behind the row it guards. Only
scalar strings are scanned: `expression` is an object by design and is *meant* to
hold a source's spelling when the licences permit it, and the slot arrays are
slots rather than prose.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from . import behaviors, errors, licenses, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

IDENTIFIER_GATE_CHECK = "identifier-gate"

BEHAVIORS_NAME = "behaviors.yaml"
RAW_RECORDS_NAME = "raw-behaviors.json"
_BEHAVIORS = f"catalog/{BEHAVIORS_NAME}"
_RAW_RECORDS = f"catalog/{RAW_RECORDS_NAME}"
_SCHEMA = "schemas/catalog/behaviors.json"

#: A dotted, lower-case token: the shape an entity id or a service name takes.
#: Deliberately not anchored to a known domain list -- the domains are Home
#: Assistant's to grow, and a gate that only recognised today's list would pass
#: a `select.foo` copied tomorrow.
_IDENTIFIER = re.compile(r"[a-z_]+(?:\.[a-z0-9_]+)+")

#: The status a code grant must be for its identifiers to be borrowed.
_GRANTING = "reusable"
_WITHHOLDING = "ideas_only"


def _read_json(relative: str) -> object:
    """A committed JSON file as parsed data, or `CheckError`.

    Raising rather than defaulting for the reason the rest of the package gives:
    a schema read as empty would scan no fields, and a gate that scans nothing
    passes everything.
    """
    path = paths.ROOT / relative
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(
            IDENTIFIER_GATE_CHECK, relative, f"cannot be read: {exc}"
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckError(
            IDENTIFIER_GATE_CHECK, relative, f"is not valid JSON: {exc}"
        ) from exc


def load_behaviors() -> list[dict[str, object]]:
    """The committed rows, in file order. Empty when the file is absent."""
    return behaviors.load_behaviors()


def string_fields() -> tuple[str, ...]:
    """The row schema's scalar-string fields, in schema order.

    Read from `$defs.behaviour.properties` so that the gate follows the schema it
    guards. A property counts when its `type` is, or includes, `"string"`: an
    enum without a type (`scope`, `classification`) is a closed value rather than
    free text and is skipped, and the array fields -- `source_repos`, `raw_ids`,
    the slot lists -- are skipped because a citation is not prose.
    """
    document = as_mapping(_read_json(_SCHEMA))
    defs = as_mapping(document.get("$defs"))
    behaviour = as_mapping(defs.get("behaviour"))
    properties = as_mapping(behaviour.get("properties"))
    names: list[str] = []
    for name, raw in properties.items():
        subschema = as_mapping(raw)
        declared = subschema.get("type")
        types = [declared] if isinstance(declared, str) else list(as_sequence(declared))
        if "string" in types:
            names.append(name)
    return tuple(names)


def entity_refs_for(
    raw_ids: Sequence[str], records_by_id: Mapping[str, Mapping[str, object]]
) -> frozenset[str]:
    """The union of `entity_refs` over the raw records a row cites.

    A raw id that resolves to nothing contributes nothing: a dangling citation is
    `behaviors.check_behaviors`'s to report, and failing it here as well would
    give one defect two owners.
    """
    found: set[str] = set()
    for raw_id in raw_ids:
        record = records_by_id.get(raw_id)
        if record is None:
            continue
        for ref in as_sequence(record.get("entity_refs")):
            text = as_text(ref)
            if text:
                found.add(text)
    return frozenset(found)


def leaked_identifiers(text: str, forbidden: frozenset[str]) -> tuple[str, ...]:
    """The forbidden identifiers that appear as dotted tokens in `text`."""
    if not text or not forbidden:
        return ()
    seen: list[str] = []
    for token in _IDENTIFIER.findall(text):
        if token in forbidden and token not in seen:
            seen.append(token)
    return tuple(seen)


def withholds(repos: Sequence[str], statuses: Mapping[str, str]) -> bool:
    """Whether any of `repos` has a code grant that is not `reusable`.

    Written as "not granting" rather than "is `ideas_only`" so an unknown repo is
    treated as withholding: the safe direction for a gate whose whole job is to
    stop a copy is to refuse when it cannot confirm a grant.
    """
    return any(statuses.get(repo) != _GRANTING for repo in repos)


def leaked_in_row(
    row: Mapping[str, object],
    fields: Sequence[str],
    records_by_id: Mapping[str, Mapping[str, object]],
    statuses: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    """Per-field leaks for one row, keyed by field name. Empty when clean.

    The row is skipped entirely when every cited repo grants, which is what makes
    a granting-only row that shares a word with a withholding repo pass: the
    withheld identifier is never in this row's forbidden set, because the row
    never cites the record that carries it.
    """
    repos = sorted(
        {
            text
            for text in (as_text(item) for item in as_sequence(row.get("source_repos")))
            if text
        }
    )
    if not withholds(repos, statuses):
        return {}
    raw_ids = [
        text
        for text in (as_text(item) for item in as_sequence(row.get("raw_ids")))
        if text
    ]
    forbidden = entity_refs_for(raw_ids, records_by_id)
    leaks: dict[str, tuple[str, ...]] = {}
    for field in fields:
        value = as_text(row.get(field))
        if value is None:
            continue
        hit = leaked_identifiers(value, forbidden)
        if hit:
            leaks[field] = hit
    return leaks


def slot_example_leaks(
    example: Mapping[str, object],
    records_by_id: Mapping[str, Mapping[str, object]],
    statuses: Mapping[str, str],
) -> tuple[str, ...]:
    """Identifiers an example copies from the archive it was drawn from.

    A worked example cites its own `raw_ids` and its own source repo, and is held
    to *its* provenance rather than the row's: an example may be drawn from one
    repo of a merged row, and the identifiers it must not copy are that repo's,
    which is what "resolved against its own raw_ids" means.
    """
    repo = as_text(example.get("source_repo")) or as_text(example.get("repo")) or ""
    if not withholds([repo], statuses):
        return ()
    raw_ids = [
        text
        for text in (as_text(item) for item in as_sequence(example.get("raw_ids")))
        if text
    ]
    forbidden = entity_refs_for(raw_ids, records_by_id)
    text = as_text(example.get("description")) or as_text(example.get("text")) or ""
    return leaked_identifiers(text, forbidden)


def check_identifier_gate(report: Report) -> None:
    """Every shipped row is free of its withholding sources' identifiers."""
    rows = load_behaviors()
    records = load_raw_records_by_id()
    statuses = {rec.repo: rec.reuse_status_code for rec in licenses.load_licences()}
    fields = string_fields()
    for row in rows:
        row_id = as_text(row.get("id")) or "<unnamed>"
        for field, hits in leaked_in_row(row, fields, records, statuses).items():
            report.add(
                IDENTIFIER_GATE_CHECK,
                f"{_BEHAVIORS}:{row_id}",
                f"copies {list(hits)} in its `{field}` from a source that "
                "withholds reuse; the behaviour may be described in our own "
                "words, but the identifier is the source's spelling and not ours "
                "to take",
            )


def load_raw_records_by_id() -> dict[str, Mapping[str, object]]:
    """The raw records keyed by id, resolved for the gate's provenance lookup."""
    path = paths.CATALOG / RAW_RECORDS_NAME
    if not path.is_file():
        return {}
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(
            IDENTIFIER_GATE_CHECK, _RAW_RECORDS, f"cannot be read: {exc}"
        ) from exc
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckError(
            IDENTIFIER_GATE_CHECK, _RAW_RECORDS, f"is not valid JSON: {exc}"
        ) from exc
    document = as_mapping(loaded)
    return {
        as_text(as_mapping(rec).get("id")) or "": as_mapping(rec)
        for rec in as_sequence(document.get("records"))
    }
