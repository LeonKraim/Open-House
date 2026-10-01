"""The shipped behaviour corpus and its cross-file rules -- tasks 4.3 to 4.8.

`catalog/behaviors.yaml` is the deliverable of stage B: the merged corpus of
behaviours, one row per behaviour found in one or more reference repos. A row's
per-field contracts are the catalogue schema's business, but four things a row
means are *relations between files* and are checked here:

- a row's `raw_ids` must exist in `catalog/raw-behaviors.json`, and every raw
  record must be claimed by exactly one row or carry an `unclaimed` reason from
  the closed set. That is what makes an unclassified artifact visible as a raw
  row no shipped row claims, which is the only completeness claim stage B can
  make about its own output.
- `expression` is source-derived structure and must be empty unless *every*
  source repo's code grant is `reusable`. The schema can say the field is an
  object or null; only this check can tie it to the licences of the sources.
- a `discard` row is retained for audit and never ships as a default, and a
  single-source row may only be `generic` when `catalog/overlap_exceptions.yaml`
  says why.
- every row with two or more sources appears in `catalog/overlap.md`, in the
  ranked order that file exists to record.

The ranking is the same key the report uses: descending by source-repo count,
then ascending by the published permissiveness order, so the widest agreement
with the fewest licence constraints ranks first. The order is derived here from
`behaviors.yaml` rather than read out of the report, because a report that
supplied its own ordering would agree with itself.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import yaml

from . import errors, licenses, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

BEHAVIORS_CHECK = "behaviors"
OVERLAP_CHECK = "overlap"

#: The files this check owns, named once so a diagnostic can name them.
BEHAVIORS_NAME = "behaviors.yaml"
RAW_RECORDS_NAME = "raw-behaviors.json"
OVERLAP_NAME = "overlap.md"
EXCEPTIONS_NAME = "overlap_exceptions.yaml"

_BEHAVIORS = f"catalog/{BEHAVIORS_NAME}"
_RAW_RECORDS = f"catalog/{RAW_RECORDS_NAME}"
_OVERLAP = f"catalog/{OVERLAP_NAME}"
_EXCEPTIONS = f"catalog/{EXCEPTIONS_NAME}"

#: The categories the corpus is organised into. A closed set rather than a free
#: string because the category is what a later phase groups packs by, and a
#: typo would silently create a category of one.
CATEGORIES: tuple[str, ...] = (
    "cleaning",
    "climate",
    "laundry",
    "lighting",
    "media",
    "modes",
    "notifications",
    "presence",
    "security_safety",
    "system",
)

#: The classifications a row may carry.
CLASSIFICATIONS: tuple[str, ...] = ("generic", "module_candidate", "discard")

#: Why a raw record no shipped row claims is nonetheless not a behaviour. The
#: closed set the `reference-catalog` spec names, repeated here because this is
#: where the register is enforced.
UNCLAIMED_REASONS: tuple[str, ...] = (
    "non_behavior_file",
    "duplicate_of",
    "vendor_config",
    "malformed",
)


def _load_yaml(path_name: str, relative: str) -> object:
    """A committed YAML file as parsed data, or `CheckError`.

    Raising rather than returning empty, for the reason `licenses.load_licences`
    gives: an unreadable corpus read as an empty one would report *nothing*,
    which is the failure the existence of the check is meant to prevent.
    """
    path = paths.CATALOG / path_name
    if not path.is_file():
        return None
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(BEHAVIORS_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(BEHAVIORS_CHECK, relative, f"cannot be parsed: {exc}") from exc


def load_behaviors() -> list[dict[str, object]]:
    """The committed rows, in file order. Empty when the file is absent."""
    loaded = _load_yaml(BEHAVIORS_NAME, _BEHAVIORS)
    if loaded is None:
        return []
    document = as_mapping(loaded)
    return [as_mapping(entry) for entry in as_sequence(document.get("behaviors"))]


def load_raw_records() -> list[dict[str, object]]:
    """The committed raw records. Empty when the store is absent."""
    path = paths.CATALOG / RAW_RECORDS_NAME
    if not path.is_file():
        return []
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(
            BEHAVIORS_CHECK, _RAW_RECORDS, f"cannot be read: {exc}"
        ) from exc
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckError(
            BEHAVIORS_CHECK, _RAW_RECORDS, f"is not valid JSON: {exc}"
        ) from exc
    document = as_mapping(loaded)
    return [as_mapping(entry) for entry in as_sequence(document.get("records"))]


def load_exceptions() -> list[str]:
    """The ids listed in `catalog/overlap_exceptions.yaml`."""
    loaded = _load_yaml(EXCEPTIONS_NAME, _EXCEPTIONS)
    if loaded is None:
        return []
    document = as_mapping(loaded)
    return [
        as_text(as_mapping(entry).get("id")) or ""
        for entry in as_sequence(document.get("exceptions"))
        if as_mapping(entry)
    ]


def _row_id(row: Mapping[str, object]) -> str:
    return as_text(row.get("id")) or "<unnamed>"


def _row_repos(row: Mapping[str, object]) -> list[str]:
    return sorted(
        {
            text
            for text in (as_text(item) for item in as_sequence(row.get("source_repos")))
            if text
        }
    )


def _row_raw_ids(row: Mapping[str, object]) -> list[str]:
    return [
        text
        for text in (as_text(item) for item in as_sequence(row.get("raw_ids")))
        if text
    ]


def claimed_ids(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """Every raw id a row cites, mapped to the row that cites it."""
    claimed: dict[str, str] = {}
    for row in rows:
        for raw_id in _row_raw_ids(row):
            claimed.setdefault(raw_id, _row_id(row))
    return claimed


def shipped_rows(
    rows: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """The rows a default house would ship: everything but the discarded ones.

    A `discard` row is retained for audit -- a behaviour tied to one named person
    is kept as the record that we saw it -- so the corpus is the set of rows and
    the shipped default set is that set with the discarded ones removed. Naming
    the distinction here is what makes "no discard row ships" checkable rather
    than a claim about a file nobody filters.
    """
    return [row for row in rows if as_text(row.get("classification")) != "discard"]


def unclaimed_count() -> int:
    """How many raw records carry a non-null `unclaimed` reason.

    Reported on every run rather than only on failure: a large count is a smell
    that stage B has stopped covering the extraction, and a smell only visible in
    a failing build is one nobody sees until something is already wrong.
    """
    return sum(1 for rec in load_raw_records() if rec.get("unclaimed") is not None)


def check_behaviors(report: Report) -> None:
    """The corpus's cross-file rules: provenance, coverage and derivation."""
    rows = load_behaviors()
    records = load_raw_records()
    _check_ids(report, rows)
    _check_expression(report, rows)
    _check_scope_and_classification(report, rows)
    _check_generics(report, rows)
    _check_coverage(report, rows, records)


def _check_ids(report: Report, rows: Sequence[Mapping[str, object]]) -> None:
    """Ids are unique, and a row cites at least one raw id.

    Uniqueness is a cross-record rule the schema cannot express. An empty
    `raw_ids` is nominally the schema's, but it is also the difference between
    "this row came from something" and "this row was invented", so it is named
    here too -- a row with no provenance is the one failure the whole
    concept/expression split depends on being caught.
    """
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        row_id = _row_id(row)
        if row_id in seen:
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{row_id}",
                f"duplicate id, first used by row {seen[row_id] + 1}; ids key "
                "`overlap_exceptions.yaml` and the overlap report, so a "
                "duplicate would make both resolve to the wrong row",
            )
        else:
            seen[row_id] = index
        if not _row_raw_ids(row):
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{row_id}",
                "cites no `raw_ids`; every shipped row traces to at least one "
                "raw record, and a row with none cannot be audited against the "
                "licence it carries",
            )


def _check_expression(report: Report, rows: Sequence[Mapping[str, object]]) -> None:
    """`expression` is populated only when every source repo's code grant is.

    Read against the licence records, not against the `reuse_status` a row
    carries: the row's own status is a derived value this same tree checks
    elsewhere, and reading it here would let a row that got its status wrong
    also get its expression past.
    """
    statuses = {rec.repo: rec.reuse_status_code for rec in licenses.load_licences()}
    for row in rows:
        expression = row.get("expression")
        if expression is None:
            continue
        blocked = sorted(
            repo for repo in _row_repos(row) if statuses.get(repo) != "reusable"
        )
        if blocked:
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{_row_id(row)}",
                f"populates `expression` but cites {blocked}, whose code grant is "
                "not `reusable`; expression is source-derived structure and may "
                "be carried only when every source permits its reuse",
            )


def _check_scope_and_classification(
    report: Report, rows: Sequence[Mapping[str, object]]
) -> None:
    """`retention: audit` exactly on `discard`, and the closed sets hold.

    Description, scope, category and classification are the schema's to enforce
    and are not repeated here; what is checked is the *relation* between the two
    fields -- a discard row is retained, and only a discard row is.
    """
    for row in rows:
        classification = as_text(row.get("classification")) or ""
        retention = row.get("retention")
        if classification == "discard" and retention != "audit":
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{_row_id(row)}",
                "is classified `discard` but does not carry `retention: audit`; "
                "a discarded behaviour is kept as the record that we saw it",
            )
        elif classification != "discard" and retention is not None:
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{_row_id(row)}",
                f"carries `retention: {retention}` but is not classified "
                "`discard`; retention is the marker of a retained discard and "
                "nothing else is retained",
            )
        category = as_text(row.get("category")) or ""
        if category and category not in CATEGORIES:
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{_row_id(row)}",
                f"carries category {category!r}, which is not one of the "
                f"corpus's categories {list(CATEGORIES)}",
            )


def _check_generics(report: Report, rows: Sequence[Mapping[str, object]]) -> None:
    """A single-source `generic` row needs a justification on file."""
    justified = set(load_exceptions())
    for row in rows:
        if as_text(row.get("classification")) != "generic":
            continue
        repos = _row_repos(row)
        if len(repos) >= 2 or _row_id(row) in justified:
            continue
        report.add(
            BEHAVIORS_CHECK,
            f"{_BEHAVIORS}:{_row_id(row)}",
            f"is `generic` on the single source {repos}; one repo's opinion is "
            "not corroboration, so a single-source default must be listed in "
            f"`{_EXCEPTIONS}` with a justification",
        )


def _check_coverage(
    report: Report,
    rows: Sequence[Mapping[str, object]],
    records: Sequence[Mapping[str, object]],
) -> None:
    """Every raw record is claimed once, or has a reason it is not.

    "Shipped row" is a row in `behaviors.yaml`, not a member of the shipped
    *default* set: `discard` rows ship in the corpus and are retained for audit,
    and the raw records behind them are claimed like any other row's.

    Three failures, named separately because they call for different fixes: a
    dangling citation names a raw id no record carries; a double claim names two
    rows; an unclaimed record with no reason names the record, and one with a
    reason the closed set does not name names the reason.
    """
    known = {as_text(rec.get("id")) or "": rec for rec in records}

    claimed: dict[str, list[str]] = {}
    for row in rows:
        for raw_id in _row_raw_ids(row):
            claimed.setdefault(raw_id, []).append(_row_id(row))

    for raw_id, owners in sorted(claimed.items()):
        if raw_id not in known:
            report.add(
                BEHAVIORS_CHECK,
                f"{_BEHAVIORS}:{owners[0]}",
                f"cites `{raw_id}`, which is not a record in `{_RAW_RECORDS}`; a "
                "dangling raw id is a citation to provenance that does not exist",
            )
        elif len(owners) > 1:
            report.add(
                BEHAVIORS_CHECK,
                f"{_RAW_RECORDS}:{raw_id}",
                f"is claimed by more than one row {owners}; a raw record is "
                "derived into exactly one behaviour, and two claims make the "
                "coverage count meaningless",
            )

    for raw_id, record in known.items():
        reason = record.get("unclaimed")
        if raw_id in claimed:
            if reason is not None:
                report.add(
                    BEHAVIORS_CHECK,
                    f"{_RAW_RECORDS}:{raw_id}",
                    f"is claimed by row {claimed[raw_id][0]} but carries "
                    f"`unclaimed: {reason}`; a claimed record's marker is null, "
                    "because the two states cannot both hold",
                )
        elif reason is None:
            report.add(
                BEHAVIORS_CHECK,
                f"{_RAW_RECORDS}:{raw_id}",
                "is claimed by no row and carries no `unclaimed` reason; every "
                "raw record is either derived into a behaviour or recorded as "
                "not one, and silence is neither",
            )
        elif reason not in UNCLAIMED_REASONS:
            report.add(
                BEHAVIORS_CHECK,
                f"{_RAW_RECORDS}:{raw_id}",
                f"carries `unclaimed: {reason}`, which is not one of "
                f"{list(UNCLAIMED_REASONS)}; an invented reason is an excuse "
                "rather than a record",
            )


def check_overlap(report: Report) -> None:
    """`catalog/overlap.md` lists every multi-source row, in the ranked order.

    The order is recomputed from `behaviors.yaml` and compared to the file's own,
    rather than read out of the file: a report that supplied its own ordering
    would agree with itself, which is exactly the self-measurement the ranking
    exists to avoid.
    """
    rows = load_behaviors()
    multis = [row for row in rows if len(_row_repos(row)) >= 2]
    path = paths.CATALOG / OVERLAP_NAME
    if not path.is_file():
        if multis:
            report.add(
                OVERLAP_CHECK,
                _OVERLAP,
                f"is missing, so {len(multis)} multi-source row(s) are unrecorded; "
                "each merged row records the approach chosen and the ones rejected",
            )
        return
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(OVERLAP_CHECK, _OVERLAP, f"cannot be read: {exc}") from exc

    entries = parse_overlap(text)
    present = {entry_id for entry_id, _ in entries}
    expected = {_row_id(row) for row in multis}
    by_id = {_row_id(row): row for row in multis}

    for row in multis:
        row_id = _row_id(row)
        if row_id not in present:
            report.add(
                OVERLAP_CHECK,
                f"{_OVERLAP}:{row_id}",
                f"does not appear; it has {len(_row_repos(row))} source repos, "
                "and every multi-source row merges into one recorded entry",
            )

    for entry_id, sources in entries:
        if entry_id not in expected:
            report.add(
                OVERLAP_CHECK,
                f"{_OVERLAP}:{entry_id}",
                "appears in the report but is not a multi-source row in "
                f"`{_BEHAVIORS}`; a stale entry describes a merge that is not "
                "in the corpus",
            )
            continue
        if len(sources) < 2:
            report.add(
                OVERLAP_CHECK,
                f"{_OVERLAP}:{entry_id}",
                f"names {len(sources)} source repo(s), but it is an overlap "
                "entry and names at least two",
            )
        listed = set(sources)
        declared = set(_row_repos(by_id[entry_id]))
        if listed != declared:
            report.add(
                OVERLAP_CHECK,
                f"{_OVERLAP}:{entry_id}",
                f"names {sorted(listed)} but the row cites {sorted(declared)}; "
                "the entry records the repos that were merged, so it and the row "
                "name the same set",
            )

    ordered = sorted(multis, key=_overlap_key)
    wanted = [_row_id(row) for row in ordered]
    got = [entry_id for entry_id, _ in entries]
    if wanted != got and set(wanted) == set(got):
        first = next(
            i for i, (a, b) in enumerate(zip(wanted, got, strict=True)) if a != b
        )
        report.add(
            OVERLAP_CHECK,
            f"{_OVERLAP}:{got[first]}",
            f"is out of order: it follows {got[first - 1] if first else 'the heading'} "
            f"but the ranking puts {wanted[first]} here. The order is descending "
            "by source-repo count, then ascending by permissiveness",
        )


def _overlap_key(row: Mapping[str, object]) -> tuple[int, int, str]:
    """The ranking key: widest agreement first, then most permissive licence.

    `licenses.restrictiveness` is an index into the published order, least
    restrictive first, so sorting by it ascending puts the most permissive
    licence at the front of a tie -- which is the rule the spec states.
    """
    repos = _row_repos(row)
    licence = as_text(row.get("license")) or ""
    rank = (
        licenses.restrictiveness(licence)
        if licence in licenses.LICENCE_ORDER
        else len(licenses.LICENCE_ORDER)
    )
    return (-len(repos), rank, _row_id(row))


def parse_overlap(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """The report's entries as `(row id, source repos)` in file order.

    Parsed from two structures the file keeps anyway -- a `## <id>` heading per
    entry and its `- sources:` line -- rather than a machine block bolted onto a
    document a person reads. The heading is the row id and the sources line is
    the claim the entry makes, so a reader and this check read the same two
    things.
    """
    entries: list[tuple[str, tuple[str, ...]]] = []
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            entries.append((current, ()))
        elif current is not None and line.startswith("- sources:"):
            listed = line.split(":", 1)[1]
            repos = tuple(part.strip() for part in listed.split(",") if part.strip())
            entries[-1] = (current, repos)
    return entries


def raw_records_by_id() -> dict[str, Mapping[str, Any]]:
    """The raw records keyed by id, for the identifier gate to resolve against."""
    return {
        as_text(rec.get("id")) or "": cast("Mapping[str, Any]", rec)
        for rec in load_raw_records()
    }
