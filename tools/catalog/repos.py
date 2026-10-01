"""The per-repo identification records: what each reference repo is, and is for.

`catalog/repos.yaml` carries one record per repo the project draws from -- its
author, its purpose, how its configuration is laid out, the Home Assistant
version it is on, and the one thing it contributes that the others do not. The
last field is why the record exists rather than a note in a README: without it
the four repos are read as four interchangeable sources of the same material,
and a later phase chooses between them by nothing in particular. `best_at` makes
the choice explicit.

This module has two halves, for the reason `inventory` has two. Exactly one
field, `ha_version`, is a claim about a file that lives only in the reference
clone -- the repo's own `.HA_VERSION` -- and the clones are gitignored, two of
them grant no licence to redistribute, and none of them is present in CI. Every
other field is a property of the committed record. So the halves are separate
functions with separate contracts:

- `check_repos()` reads `catalog/repos.yaml` and nothing else. It runs inside
  `oh-catalog validate`, which is the pre-commit hook and the CI gate.
- `check_ha_versions()` reads the clones under `ressources/` and compares each
  `.HA_VERSION` against the recorded value. It runs **locally only** and must
  not be added to the check registry: in a checkout with no clones it could only
  fail on the missing clones or pass having read nothing, and neither result is
  a statement about the records.

That split is the point of writing it out. A single check that read the clones
when they happened to be present and passed quietly when they were not would be
green in CI while verifying nothing -- the one failure a check exists to prevent
-- so the clone-dependent clause is a function CI never calls, and the committed
half is written to stand on its own without it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, inventory, licenses, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from pathlib import Path

REPOS_CHECK = "repos"

REPOS_FILE = "repos.yaml"

#: The closed set of structural styles, in the order the `reference-catalog`
#: spec lists them. Duplicated from the enum in `schemas/catalog/repos.json`
#: rather than read out of it, the same way `paths.ARTIFACT_CLASSES` is written
#: down beside `file_rules.json`: this half of the project checks data files
#: without loading the schema that describes their shape, and a set read from
#: the schema would make the check agree with any schema, including one widened
#: to admit a style nobody chose.
HA_STYLES: tuple[str, ...] = (
    "package",
    "split-include",
    "blueprint-driven",
    "custom-integration",
    "dashboard-strategy",
    "appdaemon",
)

#: The scalar fields every record carries, none of which may be left empty.
#: `ha_style` is absent because it is a list with rules of its own, not a string
#: with a minimum length.
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "repo_url",
    "author",
    "purpose",
    "scale",
    "ha_version",
    "best_at",
)

#: Where a clone keeps its Home Assistant version, tried in this order. The
#: clone root is the usual place; Home Assistant's container layout puts the
#: configuration under `config/`, and CCOSTAN's clone carries it there. A list
#: of candidate names rather than a per-repo table, because a table would be a
#: second place a repo's location is written down, free to disagree with the
#: clone it names.
HA_VERSION_FILES: tuple[str, ...] = (".HA_VERSION", "config/.HA_VERSION")


@dataclass(frozen=True, slots=True)
class RepoRecord:
    """One repo's identification, as committed."""

    name: str
    repo_url: str
    author: str
    purpose: str
    ha_style: tuple[str, ...]
    scale: str
    ha_version: str
    best_at: str


# --------------------------------------------------------------------------
# Reading the committed records -- runs in CI
# --------------------------------------------------------------------------


def load_repos() -> list[RepoRecord]:
    """The committed records, or `CheckError` if the file cannot be read.

    Raising rather than returning `[]` for the reason `rules.load_rules` gives:
    an absent or unreadable file read as an empty one would leave every
    per-record check passing on nothing, and on the committed tree that is the
    difference between four records being right and four records being gone --
    a green run with the corpus's whole repo identification missing.

    `ha_style` keeps only the string entries, the way `licenses` keeps only the
    string obligations: a non-string value is refused by the schema that
    describes the list, and reporting it here as a type error would be this
    check answering for a shape it does not own.
    """
    path = paths.CATALOG / REPOS_FILE
    relative = f"catalog/{REPOS_FILE}"
    if not path.is_file():
        raise CheckError(REPOS_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(REPOS_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(REPOS_CHECK, relative, f"cannot be parsed: {exc}") from exc
    document = as_mapping(loaded)

    out: list[RepoRecord] = []
    for entry in as_sequence(document.get("repos")):
        row = as_mapping(entry)
        if not row:
            continue
        styles = tuple(
            style
            for style in (as_text(item) for item in as_sequence(row.get("ha_style")))
            if style
        )
        out.append(
            RepoRecord(
                name=as_text(row.get("name")) or "",
                repo_url=as_text(row.get("repo_url")) or "",
                author=as_text(row.get("author")) or "",
                purpose=as_text(row.get("purpose")) or "",
                ha_style=styles,
                scale=as_text(row.get("scale")) or "",
                ha_version=as_text(row.get("ha_version")) or "",
                best_at=as_text(row.get("best_at")) or "",
            )
        )
    return out


def check_repos(report: Report) -> None:
    """Everything about the records that needs no clone.

    Per record: every scalar field present and non-empty, and `ha_style` a
    non-empty list drawn from the closed set with no repeats -- because a repo
    can exhibit several styles at once, and one that listed a style twice, or a
    style nobody defined, would still read as a complete record.

    Across the records: exactly one per repo, checked both ways against
    `catalog/licenses.yaml`, which is already the project's list of which
    repositories it draws from. A repo named there with no record here is a
    source whose `best_at` nothing states; a record for a repo no licence names
    is identification for a repository that contributes nothing and grants
    nothing.
    """
    records = load_repos()
    for record in records:
        _check_record(report, record)
    _check_repo_set(report, records)


def _check_record(report: Report, record: RepoRecord) -> None:
    where = f"catalog/{REPOS_FILE}:{record.name or '<unnamed>'}"
    for field in REQUIRED_FIELDS:
        if not getattr(record, field):
            report.add(REPOS_CHECK, where, f"`{field}` is empty")
    _check_styles(report, where, record.ha_style)


def _check_styles(report: Report, where: str, styles: tuple[str, ...]) -> None:
    """`ha_style` against the closed set, emptiness and repeats.

    The list is unordered and a repeat is the same claim twice, so both the
    duplicate and the out-of-set value are reported rather than any ordering
    being required: an earlier draft asked for "most characteristic first", and
    nothing can check that, so the requirement would have been decoration.
    """
    if not styles:
        report.add(
            REPOS_CHECK,
            where,
            "carries no `ha_style`; every repo has at least one structural "
            "style, so an empty list is a record that states nothing about how "
            "the repo is built",
        )
        return

    for style in sorted({style for style in styles if style not in HA_STYLES}):
        report.add(
            REPOS_CHECK,
            where,
            f"`ha_style` names {style!r}, which is outside the closed set "
            f"{list(HA_STYLES)}",
        )

    counts = Counter(styles)
    for style in sorted(style for style, count in counts.items() if count > 1):
        report.add(
            REPOS_CHECK,
            where,
            f"`ha_style` lists {style!r} more than once; the list is the set of "
            "styles the repo exhibits, so a repeat says nothing a single entry "
            "did not",
        )


def _check_repo_set(report: Report, records: list[RepoRecord]) -> None:
    """One record per repo, in both directions.

    Duplicates are reported as well as missing and extra repos: two records for
    one repo would each pass the per-record checks while the pair disagreed, and
    the later of the two is not the one a reader trusts.
    """
    counts = Counter(record.name for record in records if record.name)
    for name in sorted(name for name, count in counts.items() if count > 1):
        report.add(
            REPOS_CHECK,
            f"catalog/{REPOS_FILE}:{name}",
            "appears more than once; a repo has one record, and a second is a "
            "copy free to drift from the first",
        )

    recorded = {record.repo for record in licenses.load_licences() if record.repo}
    present = set(counts)
    for repo in sorted(recorded - present):
        report.add(
            REPOS_CHECK,
            "catalog",
            f"no record names `{repo}`, which has a licence record; a repo the "
            "project draws from with no identification record is one whose "
            "purpose and contribution nothing states",
        )
    for repo in sorted(present - recorded):
        report.add(
            REPOS_CHECK,
            "catalog",
            f"records `{repo}`, which has no licence record; its material would "
            "enter the corpus with no grant and no attribution behind it",
        )


# --------------------------------------------------------------------------
# Reading the clones -- local only
# --------------------------------------------------------------------------


def read_ha_version(repo_dir: Path) -> str | None:
    """The version a clone declares, or None if it declares none to read.

    Taken from the clone rather than written down beside the record, because
    `ha_version` is a claim about the version the repo is *on*, and only the
    repo's own `.HA_VERSION` can settle it. A value typed from a README would be
    a second statement free to disagree with the file, which is the disagreement
    this field is checked for in the first place.

    None rather than raising, so the caller can name every repo it cannot
    confirm instead of stopping at the first: the check's value is covering all
    four repos, and a crash on one clone would leave the other three unchecked.
    """
    for relative in HA_VERSION_FILES:
        candidate = repo_dir / relative
        if not candidate.is_file():
            continue
        try:
            text = errors.read_text(candidate)
        except (OSError, UnicodeDecodeError):
            return None
        version = text.strip()
        if version:
            return version
    return None


def check_ha_versions(report: Report) -> None:
    """Each recorded `ha_version` against its clone's own `.HA_VERSION`.

    **Local only.** This function must not be added to the `_CHECKS` tuple in
    `tools/catalog/validate.py`: it reads the four clones, which the committed
    tree does not carry and a CI checkout does not have. Adding it there would
    give CI a check that cannot run, and the failure that matters -- a check
    silently no-opping once the clones are gone, green while testing nothing --
    is exactly what keeping it a separate function avoids. `check_repos()` is
    the half that runs everywhere.

    A missing clone or a missing version file is a finding rather than a crash,
    so a run names every repo it cannot confirm. The handle-to-clone mapping
    comes from `inventory.discover_clones()`, which reads each handle off the
    clone's `origin` remote, so a record and its clone are tied by the same
    handle every other module in the package uses.
    """
    records = load_repos()
    clones = inventory.discover_clones()
    for record in records:
        where = f"catalog/{REPOS_FILE}:{record.name or '<unnamed>'}"
        clone = clones.get(record.name)
        if clone is None:
            report.add(
                REPOS_CHECK,
                where,
                f"no clone under ressources/ reports the owner {record.name!r}, "
                "so its recorded ha_version cannot be checked against the "
                "repository it names",
            )
            continue
        actual = read_ha_version(clone)
        if actual is None:
            report.add(
                REPOS_CHECK,
                where,
                f"the clone at ressources/{clone.name} carries no readable "
                f".HA_VERSION at {list(HA_VERSION_FILES)}, so the recorded "
                f"ha_version {record.ha_version!r} is left unchecked",
            )
        elif actual != record.ha_version:
            report.add(
                REPOS_CHECK,
                where,
                f"records ha_version {record.ha_version!r}, but the clone's own "
                f".HA_VERSION says {actual!r}; the file is the fact and the "
                "record is the claim, so the record is what changes",
            )
