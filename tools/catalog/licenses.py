"""Licences: the derivation table, the published order, and the row rule.

Nothing in this module is a judgement call made at the point of use. Status and
obligations are *derived* from a licence value by the table below, and the table
is published in the `attribution` spec -- so a hand-written status that
contradicts it fails the build, and a licence value outside the order fails the
build, rather than either being quietly accepted.

The order ranks **licence values only**, never statuses. That distinction is not
pedantry: three of the five values share the status `reusable`, so an order over
statuses would be unable to say whether `mit` or `apache_2_0` is stricter, and
"most restrictive source licence" would have no answer for a merged row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import yaml

from . import errors, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping

LICENCE_CHECK = "licenses"
ROW_LICENCE_CHECK = "row-license"

#: The file these checks own. `_LICENCES_NAME` locates it under `paths.CATALOG`;
#: `_LICENCES` is the repository-relative path every record-level diagnostic
#: carries a suffix of, named once so a message need not spell it out per branch.
_LICENCES_NAME = "licenses.yaml"
_LICENCES = f"catalog/{_LICENCES_NAME}"

#: The published order, least to most restrictive. Licence values only.
LICENCE_ORDER: tuple[str, ...] = (
    "public_domain",
    "mit",
    "apache_2_0",
    "cc_by_nc_sa",
    "no_licence",
)

#: The normative derivation table. Keyed by licence value; the same table is
#: applied once to `license_code` and once to `license_prose`.
DERIVATION: dict[str, tuple[str, tuple[str, ...]]] = {
    "public_domain": ("reusable", ()),
    "mit": ("reusable", ("attribution",)),
    "apache_2_0": ("reusable", ("attribution", "state_changes")),
    "cc_by_nc_sa": ("ideas_only", ("attribution", "share_alike", "non_commercial")),
    "no_licence": ("ideas_only", ()),
}

#: The closed set an obligation may be drawn from.
OBLIGATIONS: frozenset[str] = frozenset(
    {"attribution", "state_changes", "share_alike", "non_commercial"}
)

#: The closed set of author-contact outcomes.
CONTACT_OUTCOMES: frozenset[str] = frozenset(
    {"not_attempted", "contacted", "granted", "declined", "no_response"}
)


def derive(licence: str) -> tuple[str, tuple[str, ...]]:
    """Status and obligations for a licence value, or raise if unknown.

    Raising rather than returning a default is deliberate: an unrecognised
    licence value is the case where a default would be wrong in whichever
    direction it fell, and the requirement is that validation *names* the value.
    """
    if licence not in DERIVATION:
        msg = f"unknown licence value {licence!r}; known values are {LICENCE_ORDER}"
        raise ValueError(msg)
    return DERIVATION[licence]


def restrictiveness(licence: str) -> int:
    """Index into the published order. Higher means more restrictive."""
    if licence not in LICENCE_ORDER:
        msg = f"unknown licence value {licence!r}"
        raise ValueError(msg)
    return LICENCE_ORDER.index(licence)


def most_restrictive(licences: list[str]) -> str:
    """The strictest of a set of licence values, under the published order."""
    return max(licences, key=restrictiveness)


@dataclass(frozen=True, slots=True)
class LicenceRecord:
    repo: str
    author: str
    license_code: str
    license_prose: str | None
    license_file: str | None
    raw: dict[str, object]

    @property
    def reuse_status_code(self) -> str:
        return derive(self.license_code)[0]

    @property
    def obligations_code(self) -> tuple[str, ...]:
        return derive(self.license_code)[1]

    @property
    def reuse_status_prose(self) -> str | None:
        if self.license_prose is None:
            return None
        return derive(self.license_prose)[0]


def load_licences() -> list[LicenceRecord]:
    """The committed records, or `CheckError` if the file cannot be read.

    Raising rather than letting the parser's own exception out. `validate_all`
    catches `CheckError` and nothing else, so an unguarded `yaml.safe_load` here
    would escape this check, escape `validate_all`, and reach the pre-commit
    hook as a traceback: the report is never rendered, so the findings of all
    nine checks are lost -- including the diagnostic `check_catalog_data_files`
    builds for this very file, since that check reads it too. A single stray
    bracket was enough to do it.

    Raising rather than returning `[]`, too, and that is the stronger half. An
    empty list is the answer for "there are no records", so a check that read a
    corrupt licence file as an empty one would report *nothing* -- which is the
    failure this whole section exists to prevent, arriving through the guard
    meant to prevent it. `CheckError` costs this check's findings and no others,
    and names the file.
    """
    path = paths.CATALOG / _LICENCES_NAME
    if not path.is_file():
        return []
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(LICENCE_CHECK, _LICENCES, f"cannot be read: {exc}") from exc
    try:
        loaded = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(
            LICENCE_CHECK, _LICENCES, f"cannot be parsed: {_parse_failure(exc)}"
        ) from exc
    document = as_mapping(loaded)
    repos = as_sequence(document.get("repos"))

    out: list[LicenceRecord] = []
    for entry in repos:
        row = as_mapping(entry)
        if not row:
            continue
        out.append(
            LicenceRecord(
                repo=as_text(row.get("repo")) or "",
                author=as_text(row.get("author")) or "",
                license_code=as_text(row.get("license_code")) or "",
                license_prose=as_text(row.get("license_prose")),
                license_file=as_text(row.get("license_file")),
                raw=row,
            )
        )
    return out


def _parse_failure(exc: Exception) -> str:
    """Why a YAML load failed, as the one sentence worth printing.

    `yaml.safe_load` reports a marked error as a block: the context, the problem,
    a copy of the offending line and a caret under the column. In a diagnostic
    the first line is the least useful part of that -- "while parsing a flow
    node" says where a reader is already standing -- so the problem is taken
    directly, with its position, and the block is dropped. `RecursionError` is
    the other arm and has no such attributes; it prints as itself.
    """
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem:
        mark = exc.problem_mark
        if mark is not None:
            return f"{exc.problem} at line {mark.line + 1}, column {mark.column + 1}"
        return str(exc.problem)
    return str(exc).splitlines()[0]


def check_licenses(report: Report) -> None:
    """Validate the licence records and every derived value on them."""
    records = load_licences()

    for record in records:
        _check_record(report, record)

    # Rows after records, because a row's expectation is computed from the
    # records: a row citing an unsettled repo cannot be checked at all, and
    # running this first would report every row twice in a tree whose licence
    # file is incomplete.
    _check_rows(report, records)


def _check_record(report: Report, record: LicenceRecord) -> None:
    where = f"{_LICENCES}:{record.repo or '<unnamed>'}"

    for field in ("repo", "author", "license_code"):
        if not getattr(record, field):
            report.add(LICENCE_CHECK, where, f"`{field}` is empty")

    for field in ("license_code", "license_prose"):
        value = getattr(record, field)
        if value is None:
            continue
        if value not in LICENCE_ORDER:
            report.add(
                LICENCE_CHECK,
                where,
                f"`{field}` is {value!r}, which is not in the published order "
                f"{list(LICENCE_ORDER)}",
            )

    # The three derived fields must agree with the table. This is the clause that
    # makes the derivation binding rather than decorative, and it is why the file
    # can carry them at all: the schema demands a complete record, so they are
    # written down, and every one of them is recomputed here rather than trusted.
    #
    # A field whose licence is absent, or is a value the table has no row for, is
    # skipped instead of reported: the loop above already names that licence, and
    # this cannot say what the status should be for a value nothing derives from.
    for field, licence in (
        ("reuse_status_code", record.license_code),
        ("reuse_status_prose", record.license_prose),
    ):
        declared = record.raw.get(field)
        if declared is None or licence not in DERIVATION:
            continue
        expected = derive(licence)[0]
        if declared != expected:
            report.add(
                LICENCE_CHECK,
                where,
                f"hand-written `{field}: {declared}` contradicts the derivation "
                f"table, which gives {expected!r} for its licence {licence!r}",
            )

    declared_obligations = record.raw.get("obligations")
    if declared_obligations is not None:
        stated = {
            text
            for text in (as_text(item) for item in as_sequence(declared_obligations))
            if text
        }
        unknown = sorted(stated - OBLIGATIONS)
        if unknown:
            # Outside the closed set, so there is no row to derive from and the
            # set difference below would compare against the wrong thing.
            report.add(
                LICENCE_CHECK,
                where,
                f"`obligations` carries {unknown}, which is outside the closed "
                f"set {sorted(OBLIGATIONS)}",
            )
        elif record.license_code in DERIVATION:
            expected = set(derive(record.license_code)[1])
            missing = sorted(expected - stated)
            extra = sorted(stated - expected)
            if missing or extra:
                parts: list[str] = []
                if missing:
                    parts.append(f"omits {missing}")
                if extra:
                    parts.append(f"adds {extra}")
                report.add(
                    LICENCE_CHECK,
                    where,
                    f"hand-written `obligations` has {sorted(stated)}, but "
                    f"`license_code: {record.license_code}` derives "
                    f"{sorted(expected)}; the record {' and '.join(parts)}",
                )

    # A repo with no licence file is `no_licence` for that artifact kind,
    # whatever its README says, and the discrepancy must be recorded.
    if record.license_file is None:
        claimed = record.raw.get("readme_licence_claim")
        if claimed is not None and not record.raw.get("licence_claim_discrepancy"):
            report.add(
                LICENCE_CHECK,
                where,
                f"records a README licence claim ({claimed!r}) with no "
                "`licence_claim_discrepancy`; a claim is not a grant",
            )

    _check_author_contact(report, record)


def _check_author_contact(report: Report, record: LicenceRecord) -> None:
    """Unlicensed repos need a recorded contact attempt, and honest dates."""
    if record.license_file is not None:
        return
    where = f"{_LICENCES}:{record.repo or '<unnamed>'}"
    contact = as_mapping(record.raw.get("author_contact"))
    if not contact:
        report.add(
            LICENCE_CHECK,
            where,
            "repo has no licence file but no `author_contact` block; the contact "
            "attempt is recorded whether or not it was made",
        )
        return

    outcome = contact.get("outcome")
    if outcome not in CONTACT_OUTCOMES:
        report.add(
            LICENCE_CHECK,
            where,
            f"`author_contact.outcome` is {outcome!r}, which is not one of "
            f"{sorted(CONTACT_OUTCOMES)}",
        )
        return

    if outcome == "not_attempted":
        if contact.get("attempted_on") is not None:
            report.add(
                LICENCE_CHECK,
                where,
                "`author_contact.outcome` is `not_attempted` but "
                "`attempted_on` carries a date; there is no date on which "
                "nothing happened",
            )
        if not contact.get("reason"):
            report.add(
                LICENCE_CHECK,
                where,
                "`author_contact.outcome` is `not_attempted` with no `reason` recorded",
            )


def _check_rows(report: Report, records: list[LicenceRecord]) -> None:
    """Every behaviour row's derived fields, recomputed from the repos it cites.

    Row-level because a row is the unit of adaptation: it names the repos it was
    merged from, so its `license` is a claim about *those* sources rather than
    about the corpus, and two rows citing different sets legitimately carry
    different licences.

    Read here rather than in its own top-level check so the capability stays one
    check, which is what the verification document counts. The diagnostics carry
    `row-license` rather than `licenses`, because a failure here is about a row
    and the reader's next move is to open `behaviors.yaml`, not this file.

    Silent on an empty or absent `behaviors.yaml`, which is the state until
    section 4: the row rule is implemented here because it is a licence rule,
    and it goes live when there are rows to apply it to.

    A `behaviors.yaml` that will not load is silent too, and that is not the same
    statement. This function reads a file another check owns: an unparseable
    `behaviors.yaml` is `check_catalog_data_files`'s finding, reported there
    against the file with the schema it failed, and reporting it a second time
    from here would give one malformed file two diagnostics in different
    categories. What this must not do is *raise* -- `yaml.safe_load` raises
    `YAMLError` on a stray bracket and `RecursionError` on one nested past the
    parser's ceiling, neither is a `CheckError`, and `validate_all` catches only
    that, so either would reach the pre-commit hook as a traceback. The
    `RecursionError` arm is the same one `_load_data_file` carries, for the same
    reason: it is a `RuntimeError`, not a parse error, and the two parsers have
    different depth ceilings.
    """
    path = paths.CATALOG / "behaviors.yaml"
    if not path.is_file():
        return
    try:
        loaded = yaml.safe_load(errors.read_text(path))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError):
        return
    document = as_mapping(loaded)
    rows = as_sequence(document.get("behaviors"))
    if not rows:
        return

    settled = {record.repo: record.license_code for record in records}
    for entry in rows:
        row = as_mapping(entry)
        if not row:
            continue
        _check_row(report, row, settled)


def _check_row(
    report: Report, row: Mapping[str, object], settled: Mapping[str, str]
) -> None:
    """One row, against the code licences of the repos it cites."""
    row_id = as_text(row.get("id")) or "<unnamed>"
    where = f"catalog/behaviors.yaml:{row_id}"

    repos = [
        text
        for text in (as_text(item) for item in as_sequence(row.get("source_repos")))
        if text
    ]

    # The spec's "adaptation precedes the record": a row cannot be settled while
    # a repo it came from is not. Checked before the derivation, because the
    # derivation has no answer for an unknown repo and would have to invent one.
    unsettled = sorted({name for name in repos if name not in settled})
    if unsettled:
        report.add(
            ROW_LICENCE_CHECK,
            where,
            f"cites {unsettled}, which ha{'s' if len(unsettled) == 1 else 've'} no "
            "record in catalog/licenses.yaml; a row's licence is derived from its "
            "sources, so it cannot be settled before they are",
        )
        return

    # A row with no sources is silent here rather than reported, for the same
    # reason `_check_rows` is silent on an unparseable file: `source_repos` is
    # `minItems: 1` in `schemas/catalog/behaviors.json`, so `check_catalog_data_files`
    # already reports it against the row, and a second diagnostic from here would
    # give one defect two owners. It is also what keeps `derive_row_license`'s
    # `ValueError` -- "a row must cite at least one source repo" -- unreachable
    # from a validated tree; that function's refusal is a guard for direct
    # callers, not a diagnostic this check relies on.
    if not repos:
        return

    # A source whose own recorded licence the order has no place for cannot be
    # merged: `derive_row_license` runs the order over the sources and
    # `most_restrictive` raises `ValueError` on a value outside it -- not a
    # `CheckError`, so it would reach the hook as a traceback and take every
    # other finding with it. The record's bad `license_code` is named by
    # `_check_record`; this names the *row* that depends on it, so the reader
    # sees the row is blocked rather than silently unlicensed.
    blocked = sorted(
        f"{name} ({settled[name]})"
        for name in set(repos)
        if settled[name] not in LICENCE_ORDER
    )
    if blocked:
        report.add(
            ROW_LICENCE_CHECK,
            where,
            f"cites {blocked}, whose recorded `license_code` is not a value in "
            f"the published order {list(LICENCE_ORDER)}; a row's licence is read "
            "off that order, so it has no answer while a source is not in it",
        )
        return

    expected = derive_row_license([settled[name] for name in repos])
    _compare(report, where, row, expected, repos, settled)


def _compare(
    report: Report,
    where: str,
    row: Mapping[str, object],
    expected: dict[str, object],
    repos: list[str],
    settled: Mapping[str, str],
) -> None:
    """Report each derived field a row states differently, or omits."""
    sources = ", ".join(f"{name} ({settled[name]})" for name in sorted(set(repos)))

    licence = expected["license"]
    if row.get("license") != licence:
        report.add(
            ROW_LICENCE_CHECK,
            where,
            f"carries `license: {row.get('license')}`, but its sources are "
            f"{sources}; the most restrictive of those is {licence}",
        )

    status = expected["reuse_status"]
    if row.get("reuse_status") != status:
        report.add(
            ROW_LICENCE_CHECK,
            where,
            f"carries `reuse_status: {row.get('reuse_status')}`, but "
            f"`license: {licence}` derives {status!r} under the table",
        )

    # Compared as sets, because the union an obligation set represents has no
    # order and a reordered list is not a different claim. The message names the
    # direction, since "omits state_changes" and "adds state_changes" call for
    # different corrections -- the first is an unhonoured obligation, the second
    # an invented one.
    want = set(cast("list[str]", expected["obligations"]))
    have = {
        text
        for text in (as_text(item) for item in as_sequence(row.get("obligations")))
        if text
    }
    missing = sorted(want - have)
    extra = sorted(have - want)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"omits {missing}")
        if extra:
            parts.append(f"adds {extra}")
        report.add(
            ROW_LICENCE_CHECK,
            where,
            f"has obligations {sorted(have)}, but its sources ({sources}) incur "
            f"{sorted(want)}; the row {' and '.join(parts)}",
        )


def derive_row_license(source_code_licences: list[str]) -> dict[str, object]:
    """The `license`, `reuse_status` and `obligations` a merged row carries.

    `license` is the most restrictive source **code licence** -- a licence
    value, not a status, because statuses do not order. `reuse_status` is what
    the table gives that licence. `obligations` is the **union** of the source
    code licences' obligations, since an obligation incurred by any source is
    incurred by the row.
    """
    if not source_code_licences:
        msg = "a row must cite at least one source repo"
        raise ValueError(msg)
    licence = most_restrictive(source_code_licences)
    status, _ = derive(licence)
    obligations: set[str] = set()
    for value in source_code_licences:
        obligations.update(derive(value)[1])
    return {
        "license": licence,
        "reuse_status": status,
        "obligations": sorted(obligations),
    }
