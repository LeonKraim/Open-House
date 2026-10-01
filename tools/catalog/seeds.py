"""The two written registers: solved edge cases and the friction they answer.

`catalog/edge_cases.yaml` and `catalog/pain_points.yaml` are the only two files
in the corpus where a person writes a sentence about a source repository instead
of recording a structural fact. Everywhere else the committed data is a path, an
identifier or an enum, chosen by a rule; here it is prose, and prose is something
three of the four repositories grant no right to reuse -- renemarc, fwartner and
johnkoht -- and two of those, fwartner and johnkoht, grant nothing for their code
either. So the file that reads most like documentation is the file a licence can
be broken by, and both registers are bounded by the same device: a field the
check can fail on.

An edge case that names no guard is a scenario with no solution to reuse, and
one that names no repository cannot be weighed against that repository's
licence. A pain point whose `our_answer` is empty, or names no downstream phase,
is a complaint filed as a work item. Those are the three shapes this check keeps
out, and they are the whole of it: nothing here judges the prose, because a
check cannot, and a file that had to pass a taste test would be rewritten until
it did. What it can do is refuse a record that could have been written by
anyone, which is the half of the licence position a schema cannot reach -- the
schema sees that `guard` is a non-empty string, never that it says nothing.

The repository a seed names is checked against `catalog/licenses.yaml`, which
already answers "who are the sources" for the rest of the package; a second list
here would be the one that goes stale, which is the reason `golden.py` reads the
same file rather than keeping its own.

`phase_1` is a field of its own rather than a phrase read out of the prose, so
the placeholder's presence is the fact the requirement records and a seed either
carries one or does not. The phase a pain point's answer names has no field to
live in, so it is read out of the free text -- the answer is the one place in
these two files where a machine-checkable commitment has to be extracted from a
sentence, and "Phase 4" is that commitment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, licenses, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from pathlib import Path

#: One module, two registers, and a check id each. They are separate ids rather
#: than one because a diagnostic has to say which file to open, and the two
#: files are checked by different rules -- a guard for one, a named phase for
#: the other -- so a reader who greps a check id should land on one of them.
EDGE_CASE_CHECK = "edge-cases"
PAIN_POINT_CHECK = "pain-points"

EDGE_CASES_NAME = "edge_cases.yaml"
PAIN_POINTS_NAME = "pain_points.yaml"

#: A downstream phase named in prose -- `Phase 4`, `phase 2`. Matched on the
#: number rather than on the word alone, because "a later phase" names no phase
#: and is exactly the non-commitment the requirement excludes. Case is ignored:
#: the phase is the fact, and the capital is a house style nothing enforces.
_PHASE = re.compile(r"\bphase\s+\d+\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EdgeCase:
    """One solved edge case: what goes wrong, the guard, the repo, the seed."""

    scenario: str
    guard: str
    repo: str
    phase_1: str


@dataclass(frozen=True, slots=True)
class PainPoint:
    """One friction, with the phase that will answer it."""

    repo: str
    friction: str
    why_hard: str
    our_answer: str


def seed_paths() -> tuple[Path, Path]:
    """The two committed registers, by name rather than by directory walk.

    The two files are named constants because the check pairs each with its own
    rules, and a walk of `catalog/` would find files this check has no opinion
    about. `catalog_data_files()` still sees both -- a test asserts the pair is a
    subset of it -- so neither can arrive without a schema.
    """
    return (paths.CATALOG / EDGE_CASES_NAME, paths.CATALOG / PAIN_POINTS_NAME)


def names_a_phase(answer: str) -> bool:
    """Whether the answer commits to a phase, read from the number it carries.

    The requirement asks for the downstream phase that will act on the pain
    point, so the test is whether a phase is named by number: "a later phase" and
    "some phase will sort it out" say a phase exists somewhere without committing
    to one, which is exactly the non-commitment the requirement excludes, while
    "Phase 4" is assignable the way a dated promise is. The number is the whole
    of the test; the capital letter and the spacing are house style that nothing
    enforces, so neither is read.
    """
    return _PHASE.search(answer) is not None


def _load(filename: str, key: str, check: str) -> list[dict[str, object]]:
    """The rows of one register, or `CheckError` if the file cannot be read.

    Raising rather than skipping, for the reason `licenses.load_licences` gives:
    `validate_all` catches `CheckError` and nothing else, so an unguarded load
    here would escape as a traceback from the pre-commit hook and take the
    findings of every other check down with it. Raising rather than returning an
    empty list, too, because an empty register is a legitimate answer and a
    corrupt file read as an empty one would be reported by the coverage check as
    four repositories having simply stopped contributing -- a true-sounding
    finding derived from a file nobody could open.

    A file that is not there is read as empty instead, which is the one case
    that is not a parse failure: `licenses` treats a missing licence file the same
    way, and a genuinely deleted register then shows up in the coverage findings,
    which name the file the rows should have been in.
    """
    path = paths.CATALOG / filename
    where = f"catalog/{filename}"
    if not path.is_file():
        return []
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(check, where, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(check, where, f"cannot be parsed: {exc}") from exc
    document = as_mapping(loaded)
    return [as_mapping(item) for item in as_sequence(document.get(key))]


def load_edge_cases() -> tuple[EdgeCase, ...]:
    """Every seed, or the load's own `CheckError` when the file will not read.

    A field of the wrong type is read as the empty string rather than refused
    here, because the wrong type is the schema's finding to report -- it holds
    the row, the field and the type it found -- and this module exists for the
    other emptiness: the row whose type is right and whose text says nothing. An
    empty string is the shape that second finding is written against, so
    coercing keeps the two defects with two owners instead of reporting one row
    twice. The records are frozen so nothing downstream can rewrite one between
    the load and the check that reads it.
    """
    return tuple(
        EdgeCase(
            scenario=as_text(row.get("scenario")) or "",
            guard=as_text(row.get("guard")) or "",
            repo=as_text(row.get("repo")) or "",
            phase_1=as_text(row.get("phase_1")) or "",
        )
        for row in _load(EDGE_CASES_NAME, "edge_cases", EDGE_CASE_CHECK)
    )


def load_pain_points() -> tuple[PainPoint, ...]:
    """Every entry, or the load's own `CheckError` when the file will not read.

    A field of the wrong type is read as the empty string rather than refused,
    for the reason the edge-case loader gives: the type is the schema's finding,
    and an absent `our_answer` has to reach this check as an empty string so the
    requirement's own finding -- an unanswered pain point -- is what gets
    reported, instead of a type error surfacing from beneath it. The records are
    frozen so a later check cannot read a record a caller rewrote.
    """
    return tuple(
        PainPoint(
            repo=as_text(row.get("repo")) or "",
            friction=as_text(row.get("friction")) or "",
            why_hard=as_text(row.get("why_hard")) or "",
            our_answer=as_text(row.get("our_answer")) or "",
        )
        for row in _load(PAIN_POINTS_NAME, "pain_points", PAIN_POINT_CHECK)
    )


def _known_repos() -> tuple[str, ...]:
    """The repositories the project draws from, from the licence records.

    Sorted, so a diagnostic that lists the sources lists them the same way on
    every run and in every checkout.
    """
    return tuple(
        sorted({record.repo for record in licenses.load_licences() if record.repo})
    )


def _where(filename: str, repo: str, index: int) -> str:
    """A name for one row that is stable and unique within its file.

    There is no id field on either record -- a seed is identified by the
    scenario it describes, which is prose and can repeat -- so the location is
    the repository plus the row's position. That is enough for a reader to find
    the line, which is what the requirement means by naming the seed.
    """
    return f"catalog/{filename}:{repo or '<unnamed>'}#{index + 1}"


def check_seeds(report: Report) -> None:
    """Both registers, each row against the rules the requirement states.

    The two are checked together because they share the one fact a single file
    cannot supply: which repositories exist. That comes from the licence
    records, so a fifth source added there without a seed here is reported in
    both registers rather than in neither.
    """
    known = _known_repos()
    _check_edge_cases(report, known, load_edge_cases())
    _check_pain_points(report, known, load_pain_points())


def _check_edge_cases(
    report: Report, known: tuple[str, ...], entries: tuple[EdgeCase, ...]
) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = _where(EDGE_CASES_NAME, entry.repo, index)
        if not entry.repo:
            report.add(
                EDGE_CASE_CHECK,
                where,
                "names no `repo`; a seed that does not say which repository it "
                "came from cannot be weighed against that repository's licence, "
                "and the guard it records has no source to be checked against",
            )
        elif entry.repo not in known:
            report.add(
                EDGE_CASE_CHECK,
                where,
                f"names repo `{entry.repo}`, which is not one of the "
                f"repositories the project draws from {list(known)}; a seed from "
                "a source with no licence record is a guard reused under a grant "
                "nobody read",
            )
        else:
            seen.add(entry.repo)
        if not entry.guard:
            report.add(
                EDGE_CASE_CHECK,
                where,
                "names no `guard`; the requirement records what the repository "
                "does about the scenario, and a seed without one is a problem "
                "with no solution to reuse, which is a note and not a work item",
            )
        if not entry.phase_1:
            report.add(
                EDGE_CASE_CHECK,
                where,
                "has no `phase_1` placeholder; the placeholder is what makes "
                "this register a list of work rather than a reading list, and a "
                "seed nobody will act on is the failure the field exists to "
                "prevent",
            )
    for repo in known:
        if repo not in seen:
            report.add(
                EDGE_CASE_CHECK,
                f"catalog/{EDGE_CASES_NAME}",
                f"no edge case names `{repo}`; every source repository "
                "contributes at least one solved edge case, so a repository whose "
                "contribution had been dropped would leave no seed behind to say "
                "so",
            )


def _check_pain_points(
    report: Report, known: tuple[str, ...], entries: tuple[PainPoint, ...]
) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = _where(PAIN_POINTS_NAME, entry.repo, index)
        if not entry.repo:
            report.add(
                PAIN_POINT_CHECK,
                where,
                "names no `repo`; the friction was observed somewhere, and an "
                "observation with no source cannot be checked against the "
                "coverage the requirement asks of the four repositories",
            )
        elif entry.repo not in known:
            report.add(
                PAIN_POINT_CHECK,
                where,
                f"names repo `{entry.repo}`, which is not one of the "
                f"repositories the project draws from {list(known)}",
            )
        else:
            seen.add(entry.repo)
        if not entry.our_answer:
            report.add(
                PAIN_POINT_CHECK,
                where,
                "has an empty `our_answer`; an unanswered pain point is a "
                "complaint, and the field is the whole difference between a "
                "register of work and a wish list",
            )
        elif not names_a_phase(entry.our_answer):
            report.add(
                PAIN_POINT_CHECK,
                where,
                f"`our_answer` names no downstream phase ({entry.our_answer!r}); "
                "the answer has to say which phase will act on it, because a "
                "pain point assigned to no phase is one nobody has taken",
            )
    for repo in known:
        if repo not in seen:
            report.add(
                PAIN_POINT_CHECK,
                f"catalog/{PAIN_POINTS_NAME}",
                f"no pain point names `{repo}`; each of the four repositories "
                "contributes at least one entry, or the register would answer "
                "the question of the best-documented setup and not the one this "
                "corpus is built on",
            )
