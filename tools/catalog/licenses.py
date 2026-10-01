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

import yaml

from . import paths
from .errors import Report
from .narrow import as_mapping, as_sequence, as_text

LICENCE_CHECK = "licenses"
ROW_LICENCE_CHECK = "row-license"

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
    path = paths.CATALOG / "licenses.yaml"
    if not path.is_file():
        return []
    loaded = yaml.safe_load(path.read_bytes().decode("utf-8"))
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


def check_licenses(report: Report) -> None:
    """Validate the licence records and every derived value on them."""
    records = load_licences()

    for record in records:
        where = f"catalog/licenses.yaml:{record.repo or '<unnamed>'}"

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

        # A declared status must agree with the table. This is the clause that
        # makes the derivation binding rather than decorative.
        declared_code = record.raw.get("reuse_status_code")
        if declared_code is not None:
            expected = (
                derive(record.license_code)[0]
                if record.license_code in LICENCE_ORDER
                else None
            )
            if expected is not None and declared_code != expected:
                report.add(
                    LICENCE_CHECK,
                    where,
                    f"hand-written `reuse_status_code: {declared_code}` contradicts "
                    f"the derivation table, which gives {expected!r} for "
                    f"`license_code: {record.license_code}`",
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
    where = f"catalog/licenses.yaml:{record.repo or '<unnamed>'}"
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
