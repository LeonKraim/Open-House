"""The golden selections: a handful of paths per repo, each with its class.

The rule list decides every file in the corpus, and on its own it cannot be
wrong in a way anybody notices. A list that has stopped covering a directory
still produces a partition that closes, still sums, still prints counts -- the
counts are just smaller, and the files that used to be in them are simply not
mentioned anywhere. Nothing in a corpus defined as "whatever the rules select"
can be noticed to be missing from it.

The golden files are the one place a path is written down twice: once by the
rule list, once by hand, with the class it is expected to receive. That makes
them the only check that can fail in the *excluding* direction. An exclude rule
broad enough to swallow a directory takes a pinned path with it, and this check
fails naming the path and the rule that decided it.

They do not guard the other direction, and pretending otherwise would be worse
than the gap. A select rule broad enough to pull new files in changes no pinned
path, so no entry here fails; what catches it is the diff in
`catalog/inventory.json` and the per-repo counts in `catalog/README.md`, read by
a person. A mechanism that tried to close that direction too would have to
commit an exhaustive path list, which is a copy of the inventory -- a file that
nobody reads and that therefore goes stale without anybody noticing, which is the
failure mode this section exists to remove.

Everything here is a pure function of `catalog/file_rules.yaml`, the golden
files, and `catalog/licenses.yaml`, which is what lets it run in CI with no
clone present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from . import errors, licenses, paths, rules
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from pathlib import Path

GOLDEN_CHECK = "golden-paths"

#: The filename convention, which is also how a golden file's repo is known:
#: `catalog/golden_ccostan.yaml` is ccostan's, and its `repo:` field has to say
#: so too.
PREFIX = "golden_"
SUFFIXES = (".yaml", ".yml")


@dataclass(frozen=True, slots=True)
class GoldenEntry:
    path: str
    artifact_class: str


@dataclass(frozen=True, slots=True)
class GoldenFile:
    repo: str
    name: str
    source: str
    declared_repo: str
    entries: tuple[GoldenEntry, ...]


def _suffix_ok(path: Path) -> bool:
    return path.suffix in SUFFIXES


def golden_paths() -> list[Path]:
    """The committed golden files, by filename.

    By filename and not by `catalog_data_files()`: the repo a file pins is in its
    name, so a file this could not find is a file no check knows the repo of.
    The two sets are the same today -- the data-file check sees every `.yaml`
    under `catalog/`, this sees every `golden_*.yaml` under it -- and a test
    asserts the second is a subset of the first, because a golden file the
    schema check does not see would be hand-written data nothing validates.
    """
    return [
        path
        for path in sorted(paths.CATALOG.glob(f"{PREFIX}*"))
        if path.is_file() and _suffix_ok(path)
    ]


def _repo_of(path: Path) -> str:
    return path.stem[len(PREFIX) :]


def load_golden_files() -> tuple[GoldenFile, ...]:
    """Every golden file, or `CheckError` if one cannot be read.

    Raising rather than skipping, for the reason `licenses.load_licences` gives:
    an unreadable golden file read as an absent one would leave the class it pins
    reported as unpinned by the coverage check -- a true-sounding finding
    derived from a file we could not open -- and read as an empty one would leave
    every entry in it unchecked while the check stayed green.
    """
    out: list[GoldenFile] = []
    for path in golden_paths():
        source = f"catalog/{path.name}"
        try:
            text = errors.read_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise CheckError(GOLDEN_CHECK, source, f"cannot be read: {exc}") from exc
        try:
            loaded: object = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError) as exc:
            raise CheckError(GOLDEN_CHECK, source, f"cannot be parsed: {exc}") from exc
        document = as_mapping(loaded)

        entries: list[GoldenEntry] = []
        for entry in as_sequence(document.get("entries")):
            row = as_mapping(entry)
            if not row:
                continue
            entries.append(
                GoldenEntry(
                    path=as_text(row.get("path")) or "",
                    artifact_class=as_text(row.get("class")) or "",
                )
            )

        out.append(
            GoldenFile(
                repo=_repo_of(path),
                name=path.name,
                source=source,
                declared_repo=as_text(document.get("repo")) or "",
                entries=tuple(entries),
            )
        )
    return tuple(out)


def check_golden(report: Report) -> None:
    """The golden selections against the rule list and each other.

    Three things, and the first two are per entry. A pinned path must still be
    decided by the rule list, and decided as a select rule conferring exactly the
    class the entry declares -- one diagnostic, naming the path, the class
    expected and the class actually conferred, or the order of the exclude rule
    that took it. Then the file's own name and its `repo:` field must agree, and
    every repo the project draws from must have a file. Then, across all four,
    every pinnable class must be pinned somewhere.

    That last one is what makes the set of files a set rather than four
    independent lists: a class can be dropped from all four at once by a rule
    change that each file individually survives.
    """
    ruleset = rules.load_rules()
    files = load_golden_files()
    _check_naming(report, files)
    _check_repo_coverage(report, files)
    _check_entries(report, files, ruleset)
    _check_pinning(report, files)


def _check_naming(report: Report, files: tuple[GoldenFile, ...]) -> None:
    """The name and the `repo:` field are two statements of the same fact.

    Worth a check because nothing else compares them: the schema can hold `repo`
    to a constant, and does, but the constant it holds it to is written into the
    schema by hand as well. A file copied from ccostan's to start johnkoht's and
    half edited passes both -- if the schema were copied too -- and the entries
    would be checked against the wrong repository's paths.
    """
    for golden in files:
        if golden.declared_repo != golden.repo:
            report.add(
                GOLDEN_CHECK,
                golden.source,
                f"is named for `{golden.repo}` but declares `repo: "
                f"{golden.declared_repo}`; the filename is what a reader and "
                "every other check take the repo from, so the two disagreeing "
                "means one of them is describing another repository's paths",
            )


def _check_repo_coverage(report: Report, files: tuple[GoldenFile, ...]) -> None:
    """Every repo in the corpus has a golden file.

    The repo list comes from `catalog/licenses.yaml`, which is already the
    project's record of which repositories it draws from -- a fifth clone added
    there without a golden file is a repository whose contribution no check can
    see shrink, and this is the only place that would say so.
    """
    records = licenses.load_licences()
    present = {golden.declared_repo for golden in files}
    for record in records:
        if record.repo and record.repo not in present:
            report.add(
                GOLDEN_CHECK,
                "catalog",
                f"no golden file pins any path in `{record.repo}`; it is a "
                "repository the project draws from, so nothing would notice "
                "its contribution to the corpus quietly emptying",
            )


def _check_entries(
    report: Report, files: tuple[GoldenFile, ...], ruleset: rules.RuleSet
) -> None:
    for golden in files:
        if not golden.entries:
            report.add(
                GOLDEN_CHECK,
                golden.source,
                "carries no entries, so it pins no path and guards nothing",
            )
        for entry in golden.entries:
            _check_entry(report, golden, entry, ruleset)


def _check_entry(
    report: Report,
    golden: GoldenFile,
    entry: GoldenEntry,
    ruleset: rules.RuleSet,
) -> None:
    where = f"{golden.source}:{entry.path or '<unnamed>'}"
    if not entry.path:
        report.add(GOLDEN_CHECK, where, "carries no `path`")
        return
    if entry.artifact_class not in paths.PINNABLE_CLASSES:
        report.add(
            GOLDEN_CHECK,
            where,
            f"pins `{entry.artifact_class}`, which is not one of the classes a "
            f"golden entry may pin {list(paths.PINNABLE_CLASSES)}; `other` is "
            "the residual a file falls into when no class fits, so pinning it "
            "would commit the corpus to keeping a file whose only "
            "justification is that we could not classify it",
        )
        return

    rule = rules.decide(entry.path, ruleset.rules)
    if rule is None:
        report.add(
            GOLDEN_CHECK,
            where,
            "is matched by no rule; a golden path is a check only for as long "
            "as the rule list still decides it, and this one has fallen into a "
            "hole in the list",
        )
    elif not rule.selects:
        report.add(
            GOLDEN_CHECK,
            where,
            f"expects `{entry.artifact_class}` but is excluded by rule "
            f"{rule.order} ({rule.pattern!r}) -- {rule.reason.strip()!r}; an "
            "exclude rule that has grown to cover a pinned path is exactly what "
            "this entry exists to catch",
        )
    elif rule.artifact_class != entry.artifact_class:
        report.add(
            GOLDEN_CHECK,
            where,
            f"expects `{entry.artifact_class}`, but rule {rule.order} "
            f"({rule.pattern!r}) confers `{rule.artifact_class}`",
        )


def _check_pinning(report: Report, files: tuple[GoldenFile, ...]) -> None:
    """Every pinnable class is pinned by at least one repo.

    The classes are covered across the four files rather than one file each,
    because the four repositories are differently laid out -- one has no
    packages, another no templates -- so a per-file requirement would be a
    requirement that some of them invent entries for a class they do not use.
    """
    pinned = {entry.artifact_class for golden in files for entry in golden.entries}
    for artifact_class in paths.PINNABLE_CLASSES:
        if artifact_class not in pinned:
            report.add(
                GOLDEN_CHECK,
                "catalog",
                f"no golden entry pins `{artifact_class}`; every class but "
                "`other` has to be pinned somewhere, or a change that dropped "
                "it from all four repositories at once would leave the corpus "
                "with no files of that class and nothing saying so",
            )


def classes_pinned() -> dict[str, tuple[str, ...]]:
    """Each pinned class, and the repos that pin it. For the docs and tests."""
    out: dict[str, list[str]] = {}
    for golden in load_golden_files():
        for entry in golden.entries:
            out.setdefault(entry.artifact_class, [])
            if golden.repo not in out[entry.artifact_class]:
                out[entry.artifact_class].append(golden.repo)
    return {name: tuple(sorted(repos)) for name, repos in sorted(out.items())}
