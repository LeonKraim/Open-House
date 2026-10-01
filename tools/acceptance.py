"""The acceptance gate: every requirement, with a check seen to fail on it.

Task 8.1 asks for a mapping from each `### Requirement:` in the four delta specs
to a named test that observes the requirement's check *failing* on a violating
fixture. Presence in a hand-written file is not evidence that a check exists:
the mapping is a document, and a document cannot fail. So this module rebuilds
the requirement set by parsing the specs -- never by trusting a count written
down anywhere -- reads the mapping the document carries, and then *runs* each
named test, failing the gate when a requirement is unmapped, when its test is
not collected, or when that test does not pass.

The polarity is worth stating plainly, because the sentence in `tasks.md` reads
the other way round. "run it against its violating fixture, and fail if the test
is missing or passes" names the *check* under test: a violating fixture is input
a check is supposed to reject, so a check that "passes" on it reports nothing and
enforces nothing. The named test is the observation of that rejection --
`tests/test_slots.py` says the acceptance stage wants "exactly that
observation", one violating fixture per requirement -- so the test passing *is*
the check failing, and the gate fails when the test does not pass. Reading
"the test" as the check is the only reading under which the clause says what the
phase needs it to say.

A requirement no check enforces can be recorded in the mapping with a `gap:`
instead of a test, and the gate reports it rather than inventing a test to paper
over it: an unenforced clause is the one thing this gate exists to surface, so it
is a finding here and not a silence. No row uses `gap:` today -- the one that did,
`State-change notices are honoured`, closed when task 6.5 landed
`notices.check_notices` -- but the arm is kept, because the next gap should be
reportable without this module being edited first. See the mapping section of
`docs/reference/phase-0-verification.md`.

The gate runs against one *change package* at a time and is told which. Both the
spec set and the mapping document are discovered from the change's name rather
than written down, for the same reason the spec areas are globbed: a phase that
adds its own requirements and its own mapping is gated by naming it and editing
nothing here. `python -m tools.acceptance --change phase-1-engine` gates Phase 1;
with no argument the gate runs against Phase 0, which is the phase it was written
for and the one whose document was its first mapping.

This module is deliberately *not* under `tools/catalog/`: it runs pytest, which
neither the pre-commit hook nor CI can do recursively, so it is not a check that
`validate_all` wires.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from .catalog import paths
from .catalog.errors import CheckError, Report, read_text
from .catalog.narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

ACCEPTANCE_CHECK = "acceptance"

#: The change package the gate runs against when no `--change` is given. Named
#: once so the default is a decision rather than the last thing anyone typed.
DEFAULT_CHANGE = "phase-0-foundations"

#: Where a change package's delta specs live, relative to the repository root,
#: as a format string. The *areas* under it are discovered rather than named, so
#: a fifth delta spec is picked up without an edit here -- the same reason the
#: requirement count is parsed rather than written down.
SPEC_DIR = "openspec/changes/{change}/specs"

#: The default change package's own spec directory, as a path relative to the
#: repository root. Kept as a name because `tests/test_acceptance.py` reads it and
#: because it is what this module pointed at before it could be aimed at another
#: change; it is derived from `SPEC_DIR` so the two cannot drift.
SPEC_ROOT = SPEC_DIR.format(change=DEFAULT_CHANGE)

#: Where a change's verification document lives, and how it is recognised: the
#: document is the change's *phase prefix* plus `-verification.md`. A change
#: named `phase-1-engine` therefore looks for `phase-1*-verification.md` and
#: finds `phase-1-verification.md`, and a phase is never gated against another
#: phase's mapping by a path someone typed by hand.
VERIFICATION_DIR = "docs/reference"
VERIFICATION_SUFFIX = "-verification.md"

#: The info string on the fenced block that carries the mapping. The block is
#: machine-readable on purpose: the task writes the mapping *to* the document, so
#: the document is the one source of the mapping and this module reads it back
#: rather than keeping a second copy.
MAPPING_FENCE = "yaml acceptance-mapping"

#: A level-three heading that opens a requirement. The title is taken whole,
#: including any punctuation it carries, because the mapping keys on it.
_REQUIREMENT = re.compile(r"^### Requirement: (?P<title>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class Requirement:
    """One `### Requirement:` heading, and the spec area it came from."""

    area: str
    title: str

    @property
    def key(self) -> str:
        return f"{self.area}: {self.title}"


@dataclass(frozen=True, slots=True)
class Mapping:
    """One row of the hand-written mapping.

    `test` is the pytest node id that observes the requirement's check failing.
    `fixture` is the violating input the test builds, recorded for a reader
    rather than consulted by the gate -- the fixture is realised inside the test,
    which is the only place a violating tree can be built without the clones.
    `gap`, when set, says the requirement has no check that enforces it, and the
    gate reports that rather than passing the requirement on a test that covers
    only part of it.
    """

    area: str
    requirement: str
    test: str | None
    fixture: str
    gap: str | None

    @property
    def key(self) -> str:
        return f"{self.area}: {self.requirement}"


def parse_requirements(area: str, text: str) -> list[Requirement]:
    """Every `### Requirement:` heading in one spec, in document order."""
    found: list[Requirement] = []
    for line in text.splitlines():
        match = _REQUIREMENT.match(line)
        if match is not None:
            found.append(Requirement(area=area, title=match.group("title")))
    return found


def phase_prefix(change: str) -> str:
    """A change name's phase, being its first two hyphen-separated words.

    `phase-1-engine` is `phase-1`, and so is `phase-1-scenarios`; that is what
    makes the verification document discoverable from the change alone. A name
    that is not a phase is refused rather than silently reduced to one, because
    a gate that read the wrong mapping would report every requirement as
    unmapped and the reader would blame the specs.
    """
    parts = change.split("-")
    if len(parts) < 2 or parts[0] != "phase":
        raise CheckError(
            ACCEPTANCE_CHECK,
            change,
            "is not a phase change; a change name is `phase-<number>-<topic>` and "
            "its verification document is found by its phase prefix",
        )
    return "-".join(parts[:2])


def spec_root(change: str) -> str:
    """The delta spec directory of one change package, relative to the root."""
    return SPEC_DIR.format(change=change)


#: The default change's verification document, by the same naming rule
#: `mapping_doc` applies by discovery. Kept as a name because
#: `tests/test_acceptance.py` reads it and because it is what this module pointed
#: at before it could be aimed at another change; being derived from the same
#: prefix rule, it cannot disagree with what `mapping_doc` finds.
MAPPING_DOC = f"{VERIFICATION_DIR}/{phase_prefix(DEFAULT_CHANGE)}{VERIFICATION_SUFFIX}"


def mapping_doc(root: Path, change: str) -> str:
    """The verification document one change's mapping lives in.

    Discovered by the change's phase prefix rather than written down, and the
    discovery must be unambiguous: two documents for one phase is a mapping
    whose source nobody can name, and none is a phase that was gated against a
    document that does not exist.
    """
    prefix = f"{phase_prefix(change)}*{VERIFICATION_SUFFIX}"
    candidates = sorted(
        path.relative_to(root).as_posix()
        for path in (root / VERIFICATION_DIR).glob(prefix)
    )
    if len(candidates) != 1:
        raise CheckError(
            ACCEPTANCE_CHECK,
            VERIFICATION_DIR,
            f"carries {len(candidates)} document(s) matching {prefix}; a change's "
            "acceptance mapping lives in exactly one",
        )
    return candidates[0]


def spec_requirements(root: Path, change: str = DEFAULT_CHANGE) -> list[Requirement]:
    """Every requirement across every delta spec of one change package.

    The areas are globbed rather than listed so the set of requirements is a
    function of the specs on disk and nothing else -- the whole point of parsing
    instead of hard-coding a count.
    """
    found: list[Requirement] = []
    for spec in sorted((root / spec_root(change)).glob("*/spec.md")):
        found.extend(parse_requirements(spec.parent.name, read_text(spec)))
    return found


def _fenced_blocks(text: str, info: str) -> Iterator[list[str]]:
    """The bodies of every ``` ``info`` fenced block in a Markdown document."""
    inside = False
    body: list[str] = []
    for line in text.splitlines():
        if not inside:
            if line.strip() == f"```{info}":
                inside = True
                body = []
            continue
        if line.strip() == "```":
            inside = False
            yield body
            continue
        body.append(line)


def load_mapping(root: Path, change: str = DEFAULT_CHANGE) -> list[Mapping]:
    """The mapping rows, read from the fenced block in the verification doc.

    Raises `CheckError` when the block is absent: an acceptance run against a
    document that has lost its mapping must say so, not report zero
    requirements and pass.
    """
    location = mapping_doc(root, change)
    document = root / location
    try:
        text = read_text(document)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(ACCEPTANCE_CHECK, location, f"cannot be read: {exc}") from exc

    blocks = list(_fenced_blocks(text, MAPPING_FENCE))
    if not blocks:
        raise CheckError(
            ACCEPTANCE_CHECK,
            location,
            f"carries no ```{MAPPING_FENCE} block; the mapping is what the "
            "acceptance gate reads, so its absence is a failure rather than an "
            "empty mapping",
        )

    rows: list[Mapping] = []
    for block in blocks:
        loaded: object = yaml.safe_load("\n".join(block))
        for item in as_sequence(loaded):
            row = as_mapping(item)
            rows.append(
                Mapping(
                    area=as_text(row.get("spec")) or "",
                    requirement=as_text(row.get("requirement")) or "",
                    test=as_text(row.get("test")),
                    fixture=as_text(row.get("fixture")) or "",
                    gap=as_text(row.get("gap")),
                )
            )
    return rows


def _pytest(root: Path, args: Sequence[str]) -> tuple[int, str]:
    """Run pytest in `root` and return its exit code and combined output."""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


def collect(root: Path) -> set[str]:
    """Every test node id the suite collects, as pytest prints them.

    Collected once for the whole run rather than once per requirement, because
    `--collect-only` imports every test module and doing that thirty-seven times
    would dominate the run.
    """
    _, output = _pytest(
        root, ["--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"]
    )
    return {
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    }


def run_test(root: Path, node_id: str) -> int:
    """Run one node id and return pytest's exit code (0 when it passes)."""
    code, _ = _pytest(
        root,
        [node_id, "-q", "--no-header", "-p", "no:cacheprovider"],
    )
    return code


def _judge(
    report: Report,
    entry: Mapping,
    collected: set[str],
    run: Callable[[Path, str], int],
    root: Path,
) -> None:
    """One mapping row, tested against the suite that has to make it true."""
    if entry.gap is not None:
        report.add(
            ACCEPTANCE_CHECK,
            entry.key,
            f"is unenforced: {entry.gap}",
        )
        return
    if not entry.test:
        report.add(
            ACCEPTANCE_CHECK, entry.key, "names no test to observe its check failing"
        )
        return
    if entry.test not in collected:
        report.add(
            ACCEPTANCE_CHECK,
            entry.key,
            f"names {entry.test}, which the collected suite does not carry; a "
            "test named only in this document proves nothing",
        )
        return
    if run(root, entry.test) != 0:
        report.add(
            ACCEPTANCE_CHECK,
            entry.key,
            f"is not enforced: {entry.test} did not pass, so its check did not "
            "reject the violating fixture it builds",
        )


def verify(
    root: Path,
    *,
    change: str = DEFAULT_CHANGE,
    collect: Callable[[Path], set[str]] = collect,
    run: Callable[[Path, str], int] = run_test,
) -> Report:
    """Check every requirement against the mapping and the collected suite.

    The two sides are compared rather than trusted: a requirement with no row is
    reported, and so is a row naming a requirement that is not in the specs, so
    neither can drift away from the other unnoticed.
    """
    report = Report()
    requirements = spec_requirements(root, change)
    if not requirements:
        report.add(
            ACCEPTANCE_CHECK,
            spec_root(change),
            "carries no `### Requirement:` heading; the spec set could not be "
            "read, so every requirement would be vacuously mapped",
        )
        return report

    by_key: dict[str, Mapping] = {}
    for entry in load_mapping(root, change):
        if entry.key in by_key:
            report.add(ACCEPTANCE_CHECK, entry.key, "is mapped more than once")
        by_key[entry.key] = entry

    collected = collect(root)
    expected: set[str] = set()
    for requirement in requirements:
        expected.add(requirement.key)
        entry = by_key.get(requirement.key)
        if entry is None:
            report.add(
                ACCEPTANCE_CHECK,
                requirement.key,
                "has no row in the acceptance mapping; a requirement no test "
                "observes is a requirement nothing enforces",
            )
            continue
        _judge(report, entry, collected, run, root)

    for key in by_key:
        if key not in expected:
            report.add(
                ACCEPTANCE_CHECK,
                key,
                "names no requirement in the delta specs; a row for a "
                "requirement that no longer exists checks nothing",
            )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate against one change package and print what it found.

    `--change <name>` selects the package; with no argument the gate runs
    against `DEFAULT_CHANGE`. An unrecognised argument is a usage error rather
    than ignored, because a mistyped change name that silently ran Phase 0's
    gate would report a green run for a phase nobody gated.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    change = DEFAULT_CHANGE
    if args:
        if len(args) != 2 or args[0] != "--change":
            print("usage: python -m tools.acceptance [--change <change>]")
            return 2
        change = args[1]

    report = verify(paths.ROOT, change=change)
    if report.ok:
        print(f"every requirement in {change} is enforced")
        return 0
    print(report.render())
    print(f"\n{len(report.diagnostics)} problem(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
