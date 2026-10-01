"""Attribution: the generated notice, and the check that keeps it true.

Attribution owed to a reference repo is an output of its licence record, not a
document someone remembers to update. `catalog/licenses.yaml` is the record of
what each repo is under and what that obliges; `docs/attribution.md` is the
user-facing statement of it, and this module derives the second from the first
so the two cannot disagree.

The derivation is deliberate rather than convenient. A hand-maintained notice
drifts from the records it claims to reflect the moment a licence changes, and
nothing notices: the file keeps naming the old status with the same confidence
as the new one. Regenerating it makes the notice exactly as correct as the file
it is read from, and the drift check turns the one remaining failure -- an edit
to the notice that was not made to the record, or a record change whose
regeneration was not committed -- into a diagnostic that names the repo at
fault.

Everything here reads committed files and nothing else. The notice is a
function of `catalog/licenses.yaml` alone, which is what lets the check run in
CI, where the four clones are absent; it may not consult them and does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import errors, licenses, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_text

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .licenses import LicenceRecord

ATTRIBUTION_CHECK = "attribution"

#: The generated document's filename, and its path relative to the repository
#: root. Named once so a diagnostic carries the path without spelling it out.
DOCS_ATTRIBUTION_NAME = "attribution.md"
_ATTRIBUTION = f"docs/{DOCS_ATTRIBUTION_NAME}"

#: The heading a repo's block opens with. Fixed rather than free-form because
#: the drift check parses committed headings to find a repo the records no
#: longer carry; a heading shaped differently would be invisible to that parse
#: and would let a stale repo's entry survive a regeneration unnoticed.
_HEADING = "## "

#: The preamble, which belongs to no repo and so is compared only as part of the
#: whole file. It carries no `_HEADING` line of its own -- the parse above reads
#: every one it finds, and a heading here would be read as a repo the records do
#: not have.
_INTRO = (
    "# Attribution\n"
    "\n"
    "Attribution owed to the reference repositories this corpus draws from. This\n"
    "file is generated from [`catalog/licenses.yaml`](../catalog/licenses.yaml) by\n"
    "`tools/catalog/attribution.py`, and is compared against a fresh regeneration\n"
    "on every run: a hand edit is overwritten rather than kept, and a record change\n"
    "whose regeneration was not committed fails the build. Do not edit it.\n"
    "\n"
    "Each entry names a repository, its author, the licence its code and its prose\n"
    "are under, the obligations those licences attach, and what the corpus takes\n"
    "from it. Where a repo grants nothing, what may be taken is stated as what it\n"
    "is, because the record's derived status is the whole of the answer."
)


def _licence_cell(licence: str | None, status: str | None) -> str:
    """A licence value and the status it derives, or that none is recorded.

    Status is shown beside the value rather than instead of it: the value is what
    the repo is under and the status is what the table makes of it, and a reader
    deciding whether they may reuse something needs the first even when they
    already trust the second.
    """
    if licence is None:
        return "not recorded"
    if status is None:
        return f"`{licence}`"
    return f"`{licence}` — `{status}`"


def _obligations(record: LicenceRecord) -> str:
    """The repo's code obligations, or `none` rather than a blank cell.

    A blank cell reads as a field someone forgot to fill, which for exactly the
    repos that owe nothing is the reading that would prompt a second look at a
    record that is already right.
    """
    if not record.obligations_code:
        return "none"
    return ", ".join(f"`{item}`" for item in record.obligations_code)


def _rows(record: LicenceRecord) -> list[tuple[str, str]]:
    """The record's fields in a fixed order, so an edit moves one row, not many.

    The order is the record's own rather than alphabetical: a reader checking
    the notice against `catalog/licenses.yaml` reads the two side by side, and
    matching their sequence is what makes that a comparison rather than a search.
    """
    rows: list[tuple[str, str]] = [
        ("Repository", f"`{record.repo}`"),
        ("Author", record.author),
        (
            "Licence file",
            f"`{record.license_file}`" if record.license_file else "none — `null`",
        ),
        ("Code licence", _licence_cell(record.license_code, record.reuse_status_code)),
        (
            "Prose licence",
            _licence_cell(record.license_prose, record.reuse_status_prose),
        ),
        ("Obligations", _obligations(record)),
        (
            "Reference",
            f"[`docs/reference/{record.repo}.md`](reference/{record.repo}.md)",
        ),
    ]

    # The two optional fields a reader needs and the record may or may not carry.
    # Both are stated only when present: an unlicensed repo with no README claim
    # has nothing to say here, and an empty row would be a claim of its own.
    claim = as_text(record.raw.get("readme_licence_claim"))
    if claim is not None:
        rows.append(("README licence claim", f"`{claim}` — a claim, not a grant"))
    contact = as_mapping(record.raw.get("author_contact"))
    if contact:
        outcome = as_text(contact.get("outcome")) or "unrecorded"
        rows.append(("Author contact", f"`{outcome}`"))

    return rows


def _code_taken(record: LicenceRecord) -> str:
    """The reuse sentence for code, read off the *derived* code status.

    The derived value and not the record's written one, because the whole point
    of the derivation is that the status is not an opinion stated in two places;
    a notice that quoted a hand-written status would resurrect the second
    opinion the table exists to remove.
    """
    if record.reuse_status_code != "reusable":
        return (
            "Not reused. What the corpus records from this repo is facts — "
            "identifiers noted as data rather than as text — and concepts "
            "restated in our own words."
        )
    note = f"Reused and adapted, under `{record.license_code}`."
    if "state_changes" in record.obligations_code:
        note += (
            " Adapted rows carry a change notice naming Open House, and the files "
            "that contain them carry a file-level notice."
        )
    return note


def _prose_taken(record: LicenceRecord) -> str:
    """The reuse sentence for prose, which can differ from the code answer.

    Separate from `_code_taken` because renemarc is the case where it does: the
    repo's code is reusable and its prose is not, and one sentence covering both
    halves would have to be wrong about one of them.
    """
    if record.reuse_status_prose == "reusable":
        return f"May be quoted, under `{record.license_prose}`."
    return "Not quoted. Any idea taken from it is restated in our own words."


def _taken(record: LicenceRecord) -> list[str]:
    """What the corpus takes from the repo, per artifact kind.

    Two bullets, mirroring the split the record is built on: a repo can grant its
    code and withhold its prose, so a single line per repo would have to pick one
    of the two and be wrong about the other.
    """
    return [
        f"- **Code and structure.** {_code_taken(record)}",
        f"- **Prose.** {_prose_taken(record)}",
    ]


def render_section(record: LicenceRecord) -> str:
    """One repo's entry: its heading, its record table, and what is taken.

    The unit the drift check compares, so that a divergence can be named for the
    repo whose entry it is rather than against the file as a whole.
    """
    lines = [f"{_HEADING}{record.repo}", "", "| | |", "| --- | --- |"]
    lines.extend(f"| {label} | {value} |" for label, value in _rows(record))
    lines.append("")
    lines.append("Material the corpus takes from this repository:")
    lines.extend(_taken(record))
    return "\n".join(lines)


def render(records: Sequence[LicenceRecord]) -> str:
    """The whole notice, as the bytes that get committed.

    Records in the order `catalog/licenses.yaml` carries them, which is the order
    the repos are introduced everywhere else; the file is assembled from them
    rather than sorted, so reordering the record reorders the notice and the
    drift check says so. A trailing newline, because every other file in the
    project has one.
    """
    parts = [_INTRO]
    parts.extend(render_section(record) for record in records)
    return "\n\n".join(parts) + "\n"


def attribution_path() -> Path:
    return paths.DOCS / DOCS_ATTRIBUTION_NAME


def write_attribution() -> Path:
    """Regenerate `docs/attribution.md` from the committed licence records."""
    target = attribution_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(licenses.load_licences()), encoding="utf-8", newline="\n")
    return target


def _headings(text: str) -> list[str]:
    """The repo tokens of committed `_HEADING` lines, in file order.

    Parsed rather than assumed, because this is the only way to see a stale
    entry: a heading the records no longer call for leaves no missing block to
    notice, only an extra one, and comparing headings is what finds it.
    """
    return [
        line[len(_HEADING) :].strip()
        for line in text.splitlines()
        if line.startswith(_HEADING)
    ]


def check_attribution(report: Report) -> None:
    """Fail when the committed notice differs from a fresh regeneration.

    Compared by repo *block* before it is compared by file, because the
    requirement is to name the divergent repo: a whole-file mismatch says only
    that something changed, while the reader's next move -- regenerate, or edit
    the record -- depends on which repo's entry is stale. An entry the records
    still call for but the file no longer contains is that repo's failure, and a
    heading the file carries for a repo the records no longer do is named too.

    A file that differs only outside every repo's entry -- the preamble, or
    trailing text -- has no repo to name and is reported against the file, which
    is still a failure: the notice is generated, so any divergence is drift.
    """
    try:
        records = licenses.load_licences()
    except CheckError:
        # An unreadable `catalog/licenses.yaml` is `licenses.check_licenses`'s
        # finding, reported there against the file with the error it produced.
        # Re-raising would put the same defect in two checks' reports, and the
        # notice cannot be regenerated from a record file that will not parse
        # either way.
        return

    path = attribution_path()
    if not path.is_file():
        report.add(
            ATTRIBUTION_CHECK,
            _ATTRIBUTION,
            "does not exist; it is generated from catalog/licenses.yaml by "
            "`tools.catalog.attribution.write_attribution()` and must be committed",
        )
        return
    try:
        actual = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        report.add(ATTRIBUTION_CHECK, _ATTRIBUTION, f"cannot be read: {exc}")
        return

    if actual == render(records):
        return

    named = 0
    for record in records:
        if render_section(record) not in actual:
            report.add(
                ATTRIBUTION_CHECK,
                f"{_ATTRIBUTION}:{record.repo}",
                "differs from a fresh regeneration of catalog/licenses.yaml; the "
                "notice is generated, so regenerate it and commit the result "
                "rather than editing it",
            )
            named += 1

    known = {record.repo for record in records}
    for heading in _headings(actual):
        if heading not in known:
            report.add(
                ATTRIBUTION_CHECK,
                f"{_ATTRIBUTION}:{heading}",
                "names a repository catalog/licenses.yaml no longer records; the "
                "notice is generated from the records, so regenerate it",
            )
            named += 1

    if named == 0:
        report.add(
            ATTRIBUTION_CHECK,
            _ATTRIBUTION,
            "differs from a fresh regeneration of catalog/licenses.yaml outside "
            "any repository's entry",
        )
