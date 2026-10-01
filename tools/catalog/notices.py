"""State-change notices, at the row and at the file: clause 6.5.

A source under `apache_2_0` imposes `state_changes` -- the obligation to say, on
anything derived from it, that it was modified and who modified it -- and that
obligation lands on two units for two reasons the `attribution` spec gives. A
row is the unit of adaptation, so it carries the precise record of what came
from the source and what we did to it; a file is the unit of distribution and is
what a reader actually opens, so a notice only on the rows would be invisible
and a notice only on the file would attribute to the source the rows that never
touched it. The two are checked together here because they are one obligation,
and a check that enforced either alone would pass a tree the licence does not
permit.

The trigger is read off the licence records, never off a row's own
`obligations`: that field is a derived value this tree already checks against
the same records, and reading it here would let a row that got its obligations
wrong also escape the notice those obligations require. The adapted set is
therefore exactly the rows citing a repo whose code licence derives
`state_changes`, which on this corpus is one repo and is not written down as a
name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, licenses, paths
from .errors import Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping

NOTICES_CHECK = "notices"

#: The field both units carry. One name for both, because the obligation is one
#: and a file-level notice under a different key would be free to drift from the
#: rows it is meant to cover.
_NOTICE = "change_notice"

#: The party a notice credits. There is no "adapter" component in Phase 0 to
#: name, so the name is the product's own and is fixed here rather than left to
#: each notice: a notice that named nobody would satisfy the field and not the
#: obligation, which is to say *by whom*.
ADAPTER = "Open House"


@dataclass(frozen=True, slots=True)
class AdaptedArtifact:
    """One committed file that can hold rows derived from a licensed source.

    The file and the key its rows sit under are both named rather than
    discovered. A file's rows are reached through a top-level key only that
    file's schema knows, so discovery would have to guess the shape it is looking
    for -- and `catalog/slots.yaml` shows how badly that goes: it carries
    `source_repos` on a *slot*, not on a row, and a scan keyed on the field would
    demand a notice of a file that adapts nothing.
    """

    path: str
    rows_key: str


#: The artifacts this check holds to the obligation. `catalog/behaviors.yaml` is
#: the only committed file whose rows are derived from a source: `raw-behaviors.
#: json` records the sources themselves, and `slots.yaml` cites raw records
#: without transcribing any expression, so neither has an adaptation to notice.
#: A second adapted artifact is added here by naming its file and its rows key.
ARTIFACTS: tuple[AdaptedArtifact, ...] = (
    AdaptedArtifact(path="catalog/behaviors.yaml", rows_key="behaviors"),
)


def check_notices(report: Report) -> None:
    """Every adapted row carries a notice, and so does the file holding it."""
    adapted = _state_changes_repos()
    for artifact in ARTIFACTS:
        _check_artifact(report, artifact, adapted)


def _state_changes_repos() -> frozenset[str]:
    """The repos whose code licence imposes `state_changes`, by name.

    Derived through the licence table rather than compared against
    `"apache_2_0"`: the table is where the obligation is defined, and a check
    that named a licence value directly would keep passing if the table moved
    the obligation to a different one.
    """
    return frozenset(
        record.repo
        for record in licenses.load_licences()
        if record.repo and "state_changes" in record.obligations_code
    )


def _check_artifact(
    report: Report, artifact: AdaptedArtifact, adapted: frozenset[str]
) -> None:
    """One file: each row against its sources, then the file against its rows.

    The file-level notice is required only once a row has been found to need it,
    which is the obligation read literally: a file with no adapted row has
    nothing to declare, and demanding a notice of it would be the blanket
    disclaimer the row-level rule exists to avoid.
    """
    document = _load(artifact.path)
    if document is None:
        return

    any_adapted = False
    for entry in as_sequence(document.get(artifact.rows_key)):
        row = as_mapping(entry)
        if _is_adapted(row, adapted):
            any_adapted = True
            _check_row_notice(report, artifact, row)
        else:
            _check_row_uncited(report, artifact, row)

    if any_adapted and _notice(document.get(_NOTICE)) is None:
        report.add(
            NOTICES_CHECK,
            artifact.path,
            "contains rows adapted from a source whose licence imposes "
            f"`state_changes` but carries no file-level `{_NOTICE}`; the file is "
            "the unit a reader opens, so a notice only on its rows would be "
            "invisible to anyone who did not open the rows",
        )


def _check_row_notice(
    report: Report, artifact: AdaptedArtifact, row: Mapping[str, object]
) -> None:
    """An adapted row names what was changed and by whom."""
    where = f"{artifact.path}:{as_text(row.get('id')) or '<unnamed>'}"
    text = _notice(row.get(_NOTICE))
    if text is None:
        report.add(
            NOTICES_CHECK,
            where,
            "is derived from a source whose licence imposes `state_changes` and "
            f"carries no `{_NOTICE}`; the row is the unit of adaptation, so it is "
            "where the record of the change belongs",
        )
        return
    if ADAPTER not in text:
        report.add(
            NOTICES_CHECK,
            where,
            f"carries a `{_NOTICE}` that does not name {ADAPTER!r}; the obligation "
            "is to say by whom the material was changed, and in Phase 0 there is "
            "no adapter component to name but us",
        )


def _check_row_uncited(
    report: Report, artifact: AdaptedArtifact, row: Mapping[str, object]
) -> None:
    """A row from no `state_changes` source carries no notice, and needs none.

    The opposite error to a missing notice, and the one the row-level rule
    exists to refuse: a notice on every row would attribute to one source the
    rows that never touched it, which is as wrong as attributing nothing.
    """
    if _notice(row.get(_NOTICE)) is None:
        return
    report.add(
        NOTICES_CHECK,
        f"{artifact.path}:{as_text(row.get('id')) or '<unnamed>'}",
        f"carries a `{_NOTICE}` but cites no source whose licence imposes "
        "`state_changes`; the notice is an obligation attached to specific "
        "sources, so on any other row it is a blanket disclaimer the licence "
        "does not ask for",
    )


def _is_adapted(row: Mapping[str, object], adapted: frozenset[str]) -> bool:
    repos = {
        text
        for text in (as_text(item) for item in as_sequence(row.get("source_repos")))
        if text
    }
    return bool(repos & adapted)


def _notice(value: object) -> str | None:
    """A notice that says something, or None.

    An empty or whitespace-only string satisfies the field's type and states
    nothing, so it is read as the absence it is rather than as a notice.
    """
    text = as_text(value)
    if text is None:
        return None
    stripped = text.strip()
    return stripped or None


def _load(relative: str) -> dict[str, object] | None:
    """A committed artifact as parsed data, or None if it cannot be read.

    Silent on a missing or unparseable file, for the reason the licence-row check
    gives: this reads a file another check owns, and an absent file or a stray
    bracket is reported there against the file and its schema. Reporting it a
    second time from here would give one defect two diagnostics in different
    categories. Raising is the one thing this must not do -- `validate_all`
    catches `CheckError` and nothing else, so `yaml.safe_load` on a bracket or on
    a document nested past the parser's ceiling would reach the pre-commit hook
    as a traceback and take every other check's findings with it.
    """
    path = paths.ROOT / relative
    if not path.is_file():
        return None
    try:
        loaded: object = yaml.safe_load(errors.read_text(path))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError):
        return None
    return as_mapping(loaded)
