"""The dependency ledger: what each reference setup relies on, and our answer.

A list of dependencies is not a decision. All four setups install things, and a
file that recorded only *what they install* would answer the wrong question: a
later phase needs to know which dependencies it may take for granted and which
it must not, and the bare fact of use does not say. So every entry carries a
disposition, and the disposition is the point of the file. `require` means the
product may assume the dependency, `replace` means a better option exists and is
named, and `avoid` means it is abandoned upstream or superseded by a core
feature, with the successor named.

Two rules turn that from a note into something the file can be wrong about, and
this module owns both. The first is the count. Three or more repos reaching for
the same dependency is the corroboration that makes it safe to assume on a
stranger's behalf, so an entry used by three or more repos *must* be `require`;
one that is widely used and recorded as anything else is the ledger disagreeing
with its own evidence. The second is the successor: a disposition that is not
`require` has to name what took its place, and a `require` has to name none,
because both fields answer the same question and an entry that answers it twice
has answered it in two directions at once.

There is a third rule and it exists to protect the first. Two entries carrying
the same name divide that dependency's use between them, so a dependency three
repos reach for can be written down as two entries of fewer than three and slip
below the threshold -- the count is only as sound as the names being distinct.

Everything here is a pure function of `catalog/integrations.yaml`. The entries
were read out of the clones once, by a person, and committed; this check reads
the committed file alone, the way every other check in the registry does, so it
runs in CI where no clone is present. It therefore cannot confirm that a repo
uses what the ledger says it uses -- that is a claim about a clone. What it can
confirm, and does, is that the ledger is internally true: the counts agree with
the dispositions, and the dispositions carry their successors.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from . import errors, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

INTEGRATIONS_CHECK = "integrations"

INTEGRATIONS_FILE = "integrations.yaml"

#: The closed set a disposition is drawn from. Written down here as well as in
#: the schema's `enum`, because the schema is a shape and this is the spec's
#: vocabulary: a check that agreed with an `enum` widened to admit a fourth
#: disposition nobody chose would be no check at all, and a test pins the two
#: together so one going stale without the other is visible.
DISPOSITIONS: tuple[str, ...] = ("require", "replace", "avoid")

#: How many repos reaching for one dependency makes it required. Three is the
#: spec's number and it is a number rather than a judgement on purpose: at two,
#: one setup copied from another would be enough to promote a habit to a rule,
#: and below three the ledger says so rather than assuming.
WIDE_USE_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class IntegrationEntry:
    """One integration, add-on or bridge, and what the project is to do with it."""

    name: str
    repos: tuple[str, ...]
    disposition: str
    replacement: str | None

    @property
    def repo_count(self) -> int:
        """How many repos use it. The number the `require` rule reads."""
        return len(self.repos)


def load_integrations() -> tuple[IntegrationEntry, ...]:
    """The committed ledger, or `CheckError` if the file cannot be read.

    Raising rather than returning `()`, for the reason `licenses.load_licences`
    gives: an empty sequence is the answer for "no dependencies are recorded", so
    a check that read a corrupt ledger as an empty one would report nothing --
    which on the committed tree is the difference between a full ledger and a
    file that has been emptied out, arriving through the guard meant to prevent
    it. `validate_all` catches `CheckError` and nothing else, so an unguarded
    `yaml.safe_load` would reach the pre-commit hook as a traceback instead of
    naming the file.

    A missing file is a `CheckError` too, matching `rules.load_rules` rather than
    `licenses.load_licences`: this file is not optional, and reading its absence
    as "nothing recorded" is the failure above with the file simply gone.
    """
    path = paths.CATALOG / INTEGRATIONS_FILE
    relative = f"catalog/{INTEGRATIONS_FILE}"
    if not path.is_file():
        raise CheckError(INTEGRATIONS_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(
            INTEGRATIONS_CHECK, relative, f"cannot be read: {exc}"
        ) from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(
            INTEGRATIONS_CHECK, relative, f"cannot be parsed: {exc}"
        ) from exc
    document = as_mapping(loaded)

    out: list[IntegrationEntry] = []
    for entry in as_sequence(document.get("integrations")):
        row = as_mapping(entry)
        if not row:
            continue
        out.append(
            IntegrationEntry(
                name=as_text(row.get("name")) or "",
                repos=tuple(
                    text
                    for text in (
                        as_text(item) for item in as_sequence(row.get("repos"))
                    )
                    if text
                ),
                disposition=as_text(row.get("disposition")) or "",
                # Collapsed to None so an absent field and an explicit `null` --
                # and an empty string the schema would reject -- all read as "no
                # successor named", which is the one distinction this check draws.
                replacement=as_text(row.get("replacement")) or None,
            )
        )
    return tuple(out)


def check_integrations(report: Report) -> None:
    """Every ledger requirement that needs nothing but the committed file.

    Two findings are the spec's own scenarios: a dependency used by three or
    more repos whose disposition is not `require`, and a `replace` or `avoid`
    that names no successor. The rest close the same question from the sides the
    scenarios leave implicit -- a disposition outside the closed vocabulary, a
    `require` that names a successor anyway, a successor that is the entry
    itself -- because an entry that answers "what do we do with this" in two
    directions at once is wrong in whichever direction a later reader happens to
    believe first.
    """
    entries = load_integrations()
    where = f"catalog/{INTEGRATIONS_FILE}"
    if not entries:
        report.add(
            INTEGRATIONS_CHECK,
            where,
            "records no integrations; the ledger is the project's answer to what "
            "it may build on, and one that records nothing answers it by silence",
        )
        return

    for entry in entries:
        _check_entry(report, entry)
    _check_distinct(report, entries)


def _check_entry(report: Report, entry: IntegrationEntry) -> None:
    """The per-entry rules, in the one order that leaves a broken entry one finding.

    The checks differ in what they need from the entry, so they run as a
    sequence of gates rather than independently: an entry with no `name` cannot
    be named in a later finding, an out-of-vocabulary `disposition` has no
    answer for whether a successor is required, and the successor rule reads the
    disposition the vocabulary check just cleared. Each gate returns on the
    first thing wrong, so an entry that is broken once reads as one diagnostic
    against the field at fault instead of a cascade of consequences, and the
    reader is sent to the correction that fixes the whole entry. The wide-use
    rule is the one that does not return: it fires before the disposition is
    known to be valid, because a count that contradicts its disposition is worth
    reporting even when that disposition is also misspelled.
    """
    where = f"catalog/{INTEGRATIONS_FILE}:{entry.name or '<unnamed>'}"

    if not entry.name:
        report.add(INTEGRATIONS_CHECK, where, "carries no `name`")
        return

    _check_wide_use(report, entry, where)

    if entry.disposition not in DISPOSITIONS:
        report.add(
            INTEGRATIONS_CHECK,
            where,
            f"has disposition {entry.disposition!r}, which is not one of "
            f"{list(DISPOSITIONS)}; a disposition outside the closed set records a "
            "dependency without saying whether the product may take it, which is "
            "the question the ledger exists to answer",
        )
        return

    _check_successor(report, entry, where)


def _check_wide_use(report: Report, entry: IntegrationEntry, where: str) -> None:
    """The spec's first scenario: three or more repos makes a dependency required.

    The repos are named as well as the integration, because the count is the
    evidence and a reader has to be able to check it against the four setups
    rather than take the number on trust.
    """
    if entry.repo_count < WIDE_USE_THRESHOLD or entry.disposition == "require":
        return
    report.add(
        INTEGRATIONS_CHECK,
        where,
        f"is used by {entry.repo_count} repos "
        f"({', '.join(sorted(entry.repos))}) but its disposition is "
        f"{entry.disposition!r}; an integration three or more repos reach for is "
        "`require`, because the count is the corroboration that makes it safe to "
        "assume on a stranger's behalf",
    )


def _check_successor(report: Report, entry: IntegrationEntry, where: str) -> None:
    """The spec's second scenario, from both sides of the disposition.

    A `replace` or `avoid` names its successor -- the spec's clause. A `require`
    names none, which is the same clause read backwards: `replacement` exists to
    hold what took the dependency's place, so a required dependency, which by
    definition is still in place, has nothing to put there. Then the successor,
    where there is one, has to be something other than the entry: a dependency
    that names itself has named nothing.
    """
    if entry.disposition == "require":
        if entry.replacement is not None:
            report.add(
                INTEGRATIONS_CHECK,
                where,
                f"is `require` but names a replacement ({entry.replacement!r}); a "
                "required dependency has nothing it is being replaced by, so the "
                "two fields are stating opposite things",
            )
        return

    if entry.replacement is None:
        report.add(
            INTEGRATIONS_CHECK,
            where,
            f"is `{entry.disposition}` but names no replacement; a `replace` or "
            "`avoid` entry has to name what took the dependency's place, because a "
            "disposition with nothing behind it is an opinion",
        )
        return

    if entry.replacement == entry.name:
        report.add(
            INTEGRATIONS_CHECK,
            where,
            f"names itself ({entry.name!r}) as its replacement; a successor is "
            "what took the dependency's place, and a thing cannot take its own",
        )


def _check_distinct(report: Report, entries: tuple[IntegrationEntry, ...]) -> None:
    """One entry per dependency.

    Two entries for one integration each carry their own repos, so the wide-use
    rule reads a count that is short by however many the other entry holds: a
    dependency three repos reach for, written down twice as two entries of fewer
    than three, passes every other check in this module -- and the threshold,
    which is the whole reason the file exists, is silently defeated. The unit is
    the dependency, so a second entry for one splits its evidence and is named.
    """
    seen: set[str] = set()
    for entry in entries:
        key = entry.name.casefold()
        if key in seen:
            report.add(
                INTEGRATIONS_CHECK,
                f"catalog/{INTEGRATIONS_FILE}:{entry.name}",
                "is recorded twice; a dependency split across two entries divides "
                "its own use count, so one that three or more repos reach for can "
                "read as two short entries and fall below the threshold this file "
                "exists to enforce",
            )
            continue
        seen.add(key)
