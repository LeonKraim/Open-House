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

Two requirements have no check that enforces them, and the mapping records that
with a `gap:` rather than inventing a test to paper over it: an unenforced clause
is the one thing this gate exists to surface, so it is a finding here and not a
silence. See the mapping section of `docs/reference/phase-0-verification.md`.

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

#: The directory the four delta specs live under, relative to the repository
#: root. The areas are *discovered* from it rather than named, so a fifth delta
#: spec is picked up without an edit here -- the same reason the requirement
#: count is parsed rather than written down.
SPEC_ROOT = "openspec/changes/phase-0-foundations/specs"

#: The document the mapping is written to, and the info string on the fenced
#: block that carries it. The block is machine-readable on purpose: the task
#: writes the mapping *to* this document, so the document is the one source of
#: the mapping and this module reads it back rather than keeping a second copy.
MAPPING_DOC = "docs/reference/phase-0-verification.md"
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


def spec_requirements(root: Path) -> list[Requirement]:
    """Every requirement across every delta spec under `SPEC_ROOT`.

    The areas are globbed rather than listed so the set of requirements is a
    function of the specs on disk and nothing else -- the whole point of parsing
    instead of hard-coding a count.
    """
    found: list[Requirement] = []
    for spec in sorted((root / SPEC_ROOT).glob("*/spec.md")):
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


def load_mapping(root: Path) -> list[Mapping]:
    """The mapping rows, read from the fenced block in the verification doc.

    Raises `CheckError` when the block is absent: an acceptance run against a
    document that has lost its mapping must say so, not report zero
    requirements and pass.
    """
    document = root / MAPPING_DOC
    try:
        text = read_text(document)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(
            ACCEPTANCE_CHECK, MAPPING_DOC, f"cannot be read: {exc}"
        ) from exc

    blocks = list(_fenced_blocks(text, MAPPING_FENCE))
    if not blocks:
        raise CheckError(
            ACCEPTANCE_CHECK,
            MAPPING_DOC,
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
    collect: Callable[[Path], set[str]] = collect,
    run: Callable[[Path, str], int] = run_test,
) -> Report:
    """Check every requirement against the mapping and the collected suite.

    The two sides are compared rather than trusted: a requirement with no row is
    reported, and so is a row naming a requirement that is not in the specs, so
    neither can drift away from the other unnoticed.
    """
    report = Report()
    requirements = spec_requirements(root)
    if not requirements:
        report.add(
            ACCEPTANCE_CHECK,
            SPEC_ROOT,
            "carries no `### Requirement:` heading; the spec set could not be "
            "read, so every requirement would be vacuously mapped",
        )
        return report

    by_key: dict[str, Mapping] = {}
    for entry in load_mapping(root):
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


def main() -> int:
    """Run the gate against the repository and print what it found."""
    report = verify(paths.ROOT)
    if report.ok:
        print("every requirement is enforced")
        return 0
    print(report.render())
    print(f"\n{len(report.diagnostics)} problem(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
