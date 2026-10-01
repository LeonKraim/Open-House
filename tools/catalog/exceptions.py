"""The residual class, and the ledger that stops it becoming a dumping ground.

`other` exists so that a file nobody could classify is still counted rather than
dropped. That is the whole reason it is in the closed enum, and it is also the
reason it needs a second file: a class that admits anything is a class that
absorbs everything, and a corpus where the hard cases quietly collect in the
residual looks the same as one where they were handled.

So a selected file that receives `other` has to have an entry in
`catalog/inventory_exceptions.yaml` carrying a reason from a closed set of two.
The check runs in both directions, because either one alone is a different
weaker claim. An `other` file with no entry is a file that disappeared into the
residual; an entry with no `other` file behind it is a justification for
something that did not happen, and it is what a stale ledger looks like after a
rule change moved a file into a real class.

Exclude rules take precedence, and this is the clause that keeps the two routes
from meeting: a path the ordered rule list excludes receives no artifact class
at all, so it needs no entry here, and an entry naming one is reported rather
than ignored. Without that, "it is in the exceptions file" and "it is excluded"
would be two answers to one question and neither would be the deciding one.

What this module reads is committed: the inventory is the committed record of
what the clones contained, so the check is a pure function of this repository
and belongs to `oh-catalog validate` rather than to the local pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, inventory, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping

EXCEPTIONS_CHECK = "inventory-exceptions"

EXCEPTIONS_FILE = "inventory_exceptions.yaml"

#: The residual, named once. It is read off `paths.ARTIFACT_CLASSES` rather than
#: spelled here so that a second module cannot disagree with the closed enum
#: about which value is the residual.
OTHER = "other"


@dataclass(frozen=True, slots=True)
class Justification:
    """One row of the ledger: a repo-relative path and why it is `other`."""

    repo: str
    path: str
    reason: str


def load_exceptions() -> tuple[Justification, ...]:
    """The committed ledger, or `CheckError` if it cannot be read.

    Raising rather than letting the parser's own exception out, for the reason
    `licenses.load_licences` gives: `validate_all` catches `CheckError` and
    nothing else, so an unguarded `yaml.safe_load` here would reach the
    pre-commit hook as a traceback and lose every other check's findings too.

    A missing file is a `CheckError` and not an empty ledger. The file is
    committed and tracked by `catalog_data_files()`, so its absence is a change
    to the repository rather than a state the corpus can be in, and reading it as
    "nothing is justified" would turn the weakest possible input into a pass.
    """
    path = paths.CATALOG / EXCEPTIONS_FILE
    relative = f"catalog/{EXCEPTIONS_FILE}"
    if not path.is_file():
        raise CheckError(EXCEPTIONS_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(EXCEPTIONS_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CheckError(
            EXCEPTIONS_CHECK, relative, f"is not valid YAML: {_one_line(exc)}"
        ) from exc

    document = as_mapping(loaded)
    if not document:
        raise CheckError(EXCEPTIONS_CHECK, relative, "is not a YAML mapping")

    entries = as_sequence(document.get("exceptions"))
    rows: list[Justification] = []
    for entry in entries:
        record = as_mapping(entry)
        rows.append(
            Justification(
                repo=as_text(record.get("repo")) or "",
                path=as_text(record.get("path")) or "",
                reason=as_text(record.get("reason")) or "",
            )
        )
    return tuple(rows)


def _one_line(exc: yaml.YAMLError) -> str:
    """A parser error as one line, since it is being put inside a sentence."""
    problem = getattr(exc, "problem", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return str(problem)
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"


def _classified(
    inventory_document: Mapping[str, object],
) -> tuple[
    dict[tuple[str, str], str],
    dict[tuple[str, str], int],
]:
    """The inventory as two lookups: selected paths to class, excluded to rule.

    Both directions at once and not one, because the two failures this feeds are
    different claims about the same file: a path that is selected under a real
    class is an entry that is no longer needed, and a path that is excluded is an
    entry that duplicates the rule list's answer. Deciding which one a stale
    entry is means knowing both.
    """
    selected: dict[tuple[str, str], str] = {}
    excluded: dict[tuple[str, str], int] = {}
    for repo_entry in as_sequence(inventory_document.get("repos")):
        repo_row = as_mapping(repo_entry)
        repo = as_text(repo_row.get("repo")) or ""
        for file_entry in as_sequence(repo_row.get("files")):
            record = as_mapping(file_entry)
            path = as_text(record.get("path"))
            if not path:
                continue
            key = (repo, path)
            if record.get("selected") is True:
                selected[key] = as_text(record.get("class")) or ""
            else:
                selected.pop(key, None)
                excluded[key] = _as_int(record.get("rule"))
    return selected, excluded


def _as_int(value: object) -> int:
    """The deciding rule's order, or -1 when the inventory does not say.

    `-1` rather than `None` so the caller can put it in a sentence without a
    second branch, and it is not a value `order` can hold -- orders are positive
    by the time they are recorded.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def check_exceptions(report: Report) -> None:
    """Every `other` justified, and every justification earning its place.

    The ledger and the inventory are two views that have to agree, and the check
    is written in both directions because each direction is a distinct hole. The
    `other`-with-no-entry direction is the one the requirement is named for. The
    entry-with-no-`other` direction is what stops the file growing into a list
    nobody can delete from: once a rule change moves a file into a real class,
    the stale entry has to be reported or it will sit there justifying nothing
    forever, and nothing else in the project reads this file.
    """
    document = inventory.load_inventory()
    selected, excluded = _classified(document)
    rows = load_exceptions()

    justified: list[tuple[str, str]] = []
    for row in rows:
        where = f"catalog/{EXCEPTIONS_FILE}:{row.repo}:{row.path or '<unnamed>'}"
        if not row.repo or not row.path:
            report.add(
                EXCEPTIONS_CHECK,
                where,
                "does not name both a `repo` and a `path`; a path is "
                "repo-relative, so the same path can exist in two repos and one "
                "of them is not the other",
            )
            continue
        if row.reason not in paths.OTHER_REASONS:
            report.add(
                EXCEPTIONS_CHECK,
                where,
                f"gives reason {row.reason!r}, which is not one of "
                f"{list(paths.OTHER_REASONS)}; the set is closed because each "
                "value is a claim about why the class could not be decided, and "
                "a reason invented at the point of use is an excuse",
            )
        justified.append((row.repo, row.path))

        key = (row.repo, row.path)
        if key not in selected:
            if key in excluded:
                report.add(
                    EXCEPTIONS_CHECK,
                    where,
                    f"names a path the rule list excludes, by rule "
                    f"{excluded[key]} (see the same path's `rule` in "
                    f"catalog/{inventory.INVENTORY_FILE}); an excluded file "
                    "receives no artifact class and needs no entry, so this "
                    "justifies a classification that never happened",
                )
            else:
                report.add(
                    EXCEPTIONS_CHECK,
                    where,
                    "names no file the inventory carries; an entry is a "
                    "justification for a selected file, and one with nothing "
                    "behind it is a stale row rather than a defensive one",
                )
        elif selected[key] != OTHER:
            report.add(
                EXCEPTIONS_CHECK,
                where,
                f"justifies `other` for a file the inventory selects as "
                f"{selected[key]!r}; the class it received is a real one, so the "
                "entry is out of date rather than wrong",
            )

    duplicates = sorted({key for key in justified if justified.count(key) > 1})
    for repo, path in duplicates:
        report.add(
            EXCEPTIONS_CHECK,
            f"catalog/{EXCEPTIONS_FILE}:{repo}:{path}",
            "is justified more than once; two rows for one file is two reasons, "
            "and the reader has no way to tell which one decided it",
        )

    unlisted = sorted(
        key for key, artifact_class in selected.items() if artifact_class == OTHER
    )
    for repo, path in unlisted:
        if (repo, path) in set(justified):
            continue
        report.add(
            EXCEPTIONS_CHECK,
            f"catalog/{inventory.INVENTORY_FILE}:{repo}:{path}",
            f"received the residual class {OTHER!r} and has no entry in "
            f"catalog/{EXCEPTIONS_FILE}; the residual is where a file waits with "
            "a stated reason, not where it disappears",
        )
