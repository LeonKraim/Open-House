"""The written registers -- section 6, tasks 6.1 and 6.2.

`catalog/edge_cases.yaml` and `catalog/pain_points.yaml` are the two files in the
corpus that hold prose, which makes them the two a licence can be broken by and
the two where a field can be present and still say nothing. A guard is a string
whether it reads `a debounce on the door sensor` or `tbd`; an `our_answer` is a
string whether it names Phase 4 or names no phase at all. The schema stops the
first kind of emptiness -- the missing or wrong-typed field -- and cannot see the
second, which is what the check under test is for.

So the failures here are all of one shape: a row that is structurally fine and
says nothing. An edge case with no guard, a seed with no Phase 1 placeholder, an
answer that commits to no phase, a repository that contributes to neither
register. Each negative test below is built on a *complete* register -- one seed
or one entry per source repo, every field filled -- so the single finding it
asserts is the single defect it introduced, and a check that had stopped
reporting would make the test fail rather than pass.

The two files are checked together because they share the one fact neither can
supply alone, the set of repositories the project draws from, and that fact
comes from `catalog/licenses.yaml`. The check is a pure function of the committed
tree, so none of this needs a clone; the last group is the half that needs the
real tree to be green.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from tools.catalog import licenses, paths, seeds
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

#: The four source repositories, as `catalog/licenses.yaml` spells them. Used to
#: build fixtures; the check itself reads the set from the licence records and
#: never from this tuple.
REPOS = ("ccostan", "renemarc", "fwartner", "johnkoht")

EDGE_PATH = "catalog/edge_cases.yaml"
PAIN_PATH = "catalog/pain_points.yaml"
LICENCES_PATH = "catalog/licenses.yaml"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _edge(
    repo: str,
    guard: str = "a guard the repo uses",
    phase_1: str = "Phase 1: the scenario this seed becomes",
    scenario: str = "a scenario the repo handles",
) -> dict[str, object]:
    return {
        "scenario": scenario,
        "guard": guard,
        "repo": repo,
        "phase_1": phase_1,
    }


def _pain(
    repo: str,
    our_answer: str = "Phase 4: the flow that answers it",
    friction: str = "something is hard",
    why_hard: str = "because of a reason",
) -> dict[str, object]:
    return {
        "repo": repo,
        "friction": friction,
        "why_hard": why_hard,
        "our_answer": our_answer,
    }


def _edges_document(entries: list[dict[str, object]]) -> str:
    return yaml.safe_dump({"edge_cases": entries}, sort_keys=False)


def _pains_document(entries: list[dict[str, object]]) -> str:
    return yaml.safe_dump({"pain_points": entries}, sort_keys=False)


def _licences_document(repos: tuple[str, ...] | list[str]) -> str:
    return yaml.safe_dump(
        {"repos": [{"repo": name} for name in repos]}, sort_keys=False
    )


def _one_per_repo() -> list[dict[str, object]]:
    """A complete register: one seed or entry per source repo."""
    return [_edge(repo) for repo in REPOS]


def _one_per_repo_pain() -> list[dict[str, object]]:
    return [_pain(repo) for repo in REPOS]


def _commit(
    root: Path,
    edges: list[dict[str, object]] | None = None,
    pains: list[dict[str, object]] | None = None,
    repos: tuple[str, ...] = REPOS,
) -> None:
    """Write both registers and the licence records the check reads the repos from.

    The licence file is written every time because the repository-coverage rule
    is phrased over the repositories the project draws from, and that set is read
    from `licenses.yaml`. A fixture that omitted it would make every seed's repo
    an unknown one and assert the wrong failure.
    """
    write(root, LICENCES_PATH, _licences_document(repos))
    write(root, EDGE_PATH, _edges_document(_one_per_repo() if edges is None else edges))
    write(
        root,
        PAIN_PATH,
        _pains_document(_one_per_repo_pain() if pains is None else pains),
    )


def _findings(check: str) -> list[tuple[str, str]]:
    """One register's findings, collected the way the hook collects them.

    The report is built and the check run through the same `CheckError` handling
    `validate_all` applies, so a corrupt file is asserted as the diagnostic the
    command returns rather than allowed to escape as a traceback. `validate_all`
    is not called directly because the module is new and its entry point may not
    yet be wired into the validator registry; routing through the identical
    handling keeps the behaviour under test the same either way.
    """
    report = Report()
    try:
        seeds.check_seeds(report)
    except CheckError as exc:
        report.add(exc.check, exc.where, exc.message)
    return [(d.where, d.message) for d in report.diagnostics if d.check == check]


def _edge_findings() -> list[tuple[str, str]]:
    return _findings(seeds.EDGE_CASE_CHECK)


def _pain_findings() -> list[tuple[str, str]]:
    return _findings(seeds.PAIN_POINT_CHECK)


def _messages(findings: list[tuple[str, str]]) -> str:
    return "\n".join(f"{where}: {message}" for where, message in findings)


# --------------------------------------------------------------------------
# The fixtures themselves
# --------------------------------------------------------------------------


def test_the_seed_fixture_is_closed(fake_root: Path) -> None:
    """The default fixture is complete in both registers, so each negative test
    below is asserting the one defect it introduced rather than a hole in the
    fixture it was built on."""
    _commit(fake_root)

    assert _edge_findings() == []
    assert _pain_findings() == []


# --------------------------------------------------------------------------
# Task 6.1 -- a repo, a guard, and a Phase 1 placeholder
# --------------------------------------------------------------------------


def test_an_edge_case_that_names_no_repo_is_named(fake_root: Path) -> None:
    """A seed with no source cannot be weighed against that source's licence, and
    the guard it records has nothing to be checked against."""
    edges: list[dict[str, object]] = [
        _edge("ccostan"),
        _edge("ccostan"),
        _edge("renemarc"),
        _edge("fwartner"),
        _edge("johnkoht"),
    ]
    edges[1]["repo"] = ""
    _commit(fake_root, edges)

    findings = _edge_findings()
    assert len(findings) == 1, _messages(findings)
    assert "names no `repo`" in findings[0][1]


def test_an_edge_case_that_names_an_unknown_repo_is_named(fake_root: Path) -> None:
    """A seed from a fifth source would arrive without a licence record, so the
    guard would be reused under a grant nobody read."""
    edges = [*_one_per_repo(), _edge("someoneelse")]
    _commit(fake_root, edges)

    findings = _edge_findings()
    assert len(findings) == 1, _messages(findings)
    assert "someoneelse" in findings[0][1]
    assert "not one of the repositories the project draws from" in findings[0][1]


def test_an_edge_case_that_names_no_guard_is_named(fake_root: Path) -> None:
    """The requirement records what the repo does about the scenario; a seed
    without a guard is a problem with no solution to reuse."""
    edges = _one_per_repo()
    edges[0]["guard"] = ""
    _commit(fake_root, edges)

    findings = _edge_findings()
    assert len(findings) == 1, _messages(findings)
    assert "names no `guard`" in findings[0][1]
    assert findings[0][0].endswith("ccostan#1")


def test_an_edge_case_with_no_phase_1_placeholder_is_named(fake_root: Path) -> None:
    """The placeholder is the field that makes the register a list of work; a
    seed without one is a note nobody will act on."""
    edges = _one_per_repo()
    edges[2]["phase_1"] = ""
    _commit(fake_root, edges)

    findings = _edge_findings()
    assert len(findings) == 1, _messages(findings)
    assert "has no `phase_1` placeholder" in findings[0][1]


def test_a_repo_with_no_edge_case_is_named(fake_root: Path) -> None:
    """Every source repo contributes at least one solved edge case, so a dropped
    contribution would leave no seed behind to say so."""
    edges = [_edge(repo) for repo in REPOS if repo != "renemarc"]
    _commit(fake_root, edges)

    findings = _edge_findings()
    assert len(findings) == 1, _messages(findings)
    assert findings[0][0] == EDGE_PATH
    assert "no edge case names `renemarc`" in findings[0][1]


# --------------------------------------------------------------------------
# Task 6.2 -- a non-empty answer naming a phase, and four repos
# --------------------------------------------------------------------------


def test_a_pain_point_with_an_empty_our_answer_is_named(fake_root: Path) -> None:
    """An unanswered pain point is a complaint; the field is the whole
    difference between a register of work and a wish list."""
    pains = _one_per_repo_pain()
    pains[0]["our_answer"] = ""
    _commit(fake_root, pains=pains)

    findings = _pain_findings()
    assert len(findings) == 1, _messages(findings)
    assert "has an empty `our_answer`" in findings[0][1]


def test_a_pain_point_whose_answer_names_no_phase_is_named(fake_root: Path) -> None:
    """A non-empty answer still fails if it commits to no phase -- a pain point
    assigned to no phase is one nobody has taken."""
    pains = _one_per_repo_pain()
    pains[1]["our_answer"] = "we will make it easier somehow"
    _commit(fake_root, pains=pains)

    findings = _pain_findings()
    assert len(findings) == 1, _messages(findings)
    assert "names no downstream phase" in findings[0][1]


def test_a_pain_point_whose_answer_names_a_phase_passes(fake_root: Path) -> None:
    """The positive half of the same rule: an answer that names a phase is
    accepted, so the negative test above is not passing on an empty register."""
    pains = _one_per_repo_pain()
    pains[3]["our_answer"] = "Phase 3 covers the migration; see also phase 1."
    _commit(fake_root, pains=pains)

    assert _pain_findings() == []


def test_a_pain_point_that_names_no_repo_is_named(fake_root: Path) -> None:
    """An observation with no source cannot be checked against the coverage the
    requirement asks of the four repositories."""
    pains: list[dict[str, object]] = [
        _pain("ccostan"),
        _pain("ccostan"),
        _pain("renemarc"),
        _pain("fwartner"),
        _pain("johnkoht"),
    ]
    pains[1]["repo"] = ""
    _commit(fake_root, pains=pains)

    findings = _pain_findings()
    assert len(findings) == 1, _messages(findings)
    assert "names no `repo`" in findings[0][1]


def test_a_repo_with_no_pain_point_is_named(fake_root: Path) -> None:
    """Each of the four repos contributes at least one entry, or the register
    would answer the question of the best-documented setup instead of the one
    this corpus is built on."""
    pains = [_pain(repo) for repo in REPOS if repo != "fwartner"]
    _commit(fake_root, pains=pains)

    findings = _pain_findings()
    assert len(findings) == 1, _messages(findings)
    assert findings[0][0] == PAIN_PATH
    assert "no pain point names `fwartner`" in findings[0][1]


# --------------------------------------------------------------------------
# The failure modes a corrupt file can reach
# --------------------------------------------------------------------------


def test_a_register_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else, so an
    unguarded load here would reach it as a traceback and take the findings of
    the other checks with it."""
    _commit(fake_root)
    write(fake_root, EDGE_PATH, "edge_cases: [\n")

    findings = _edge_findings()
    assert len(findings) == 1, _messages(findings)
    assert "cannot be parsed" in findings[0][1]


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/` and no `.local/`, and the suite is run in
    a checkout without either. Nothing here touches them."""
    assert not paths.RESSOURCES.exists()
    assert not paths.LOCAL.exists()

    _commit(fake_root)
    assert _edge_findings() == []
    assert _pain_findings() == []


# --------------------------------------------------------------------------
# The committed registers
# --------------------------------------------------------------------------


def test_the_committed_seed_registers_pass_their_checks() -> None:
    report = Report()
    seeds.check_seeds(report)
    assert report.diagnostics == []


def test_every_source_repo_contributes_to_both_registers() -> None:
    """Stated directly against the committed files, which is the clause task 6.2
    names: each of the four repos contributes at least one entry -- and, from
    6.1, at least one solved edge case too."""
    known = {record.repo for record in licenses.load_licences() if record.repo}
    assert known, "the licence records name no repos to compare against"

    assert known <= {edge.repo for edge in seeds.load_edge_cases()}
    assert known <= {pain.repo for pain in seeds.load_pain_points()}


def test_every_committed_answer_names_a_downstream_phase() -> None:
    """The clause stated as a property of the data rather than through the
    diagnostics that enforce it."""
    for pain in seeds.load_pain_points():
        assert seeds.names_a_phase(pain.our_answer), pain.repo


def test_every_committed_edge_case_carries_a_guard_and_a_placeholder() -> None:
    for edge in seeds.load_edge_cases():
        assert edge.guard, edge.repo
        assert edge.phase_1, edge.repo


def test_the_seed_registers_are_discovered_as_catalog_data_files() -> None:
    """A register the data-file check does not see is hand-written data nothing
    validates against a schema, and every check would still be green."""
    assert set(seeds.seed_paths()) <= set(paths.catalog_data_files())
