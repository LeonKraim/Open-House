"""The acceptance gate -- task 8.1.

The gate is the one check whose subject is every other check, so its own tests
have to keep it honest in both directions: it must rebuild the requirement set
from the specs (never from a count written down), and it must fail a requirement
whose check was not observed to fire. The tests here drive the gate with an
injected collector and runner, so they exercise its decision procedure without
recursing into a real pytest run; one test holds the committed mapping to the
committed specs, which is the property that keeps the two documents from
drifting apart.

`python -m tools.acceptance` runs the suite for real, which is why this module
is not under `tools/catalog/` and is not wired into `validate_all`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tools import acceptance
from tools.catalog.errors import CheckError

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

_SPEC = acceptance.SPEC_ROOT


def _spec_text(titles: list[str]) -> str:
    body = "\n".join(f"### Requirement: {title}\n" for title in titles)
    return f"# Spec\n\n{body}"


def _doc_text(rows: list[str]) -> str:
    return (
        "# Verification\n\n"
        f"```{acceptance.MAPPING_FENCE}\n" + "\n".join(rows) + "\n```\n"
    )


def _row(spec: str, requirement: str, **fields: str) -> str:
    lines = [f"- spec: {spec}", f"  requirement: {requirement}"]
    lines.extend(f"  {key}: {value}" for key, value in fields.items())
    return "\n".join(lines)


def _build(
    root: Path,
    *,
    titles: list[str] | None = None,
    rows: list[str] | None = None,
) -> None:
    area = "reference-catalog"
    write(root, f"{_SPEC}/{area}/spec.md", _spec_text(titles or ["A requirement"]))
    write(root, acceptance.MAPPING_DOC, _doc_text(rows or []))


def _no_tests(_root: Path) -> set[str]:
    return set()


def _always_pass(_root: Path, _node: str) -> int:
    return 0


# --------------------------------------------------------------------------
# Parsing the specs
# --------------------------------------------------------------------------


def test_parse_requirements_reads_every_requirement_heading() -> None:
    text = _spec_text(["First", "Second", "Third"])
    parsed = acceptance.parse_requirements("some-area", text)
    assert [requirement.title for requirement in parsed] == ["First", "Second", "Third"]
    assert all(requirement.area == "some-area" for requirement in parsed)


def test_a_scenario_heading_is_not_a_requirement() -> None:
    """Only `### Requirement:` counts; scenarios are `####` and carry no key."""
    text = "### Requirement: Real\n#### Scenario: Not a requirement\n"
    assert [r.title for r in acceptance.parse_requirements("a", text)] == ["Real"]


def test_the_committed_specs_parse_to_every_area(real_root: Path) -> None:
    requirements = acceptance.spec_requirements(real_root)
    areas = {requirement.area for requirement in requirements}
    assert areas == {
        "architecture-invariants",
        "attribution",
        "configuration-schemas",
        "reference-catalog",
    }
    assert len(requirements) == len({requirement.key for requirement in requirements})


# --------------------------------------------------------------------------
# Reading the mapping
# --------------------------------------------------------------------------


def test_load_mapping_reads_a_fenced_block(fake_root: Path) -> None:
    _build(
        fake_root,
        rows=[_row("reference-catalog", "A requirement", test="tests/x.py::test_y")],
    )
    rows = acceptance.load_mapping(fake_root)
    assert len(rows) == 1
    assert rows[0].requirement == "A requirement"
    assert rows[0].test == "tests/x.py::test_y"
    assert rows[0].gap is None


def test_a_document_without_the_block_is_a_check_error_not_an_empty_mapping(
    fake_root: Path,
) -> None:
    """An absent mapping must fail, not report zero requirements and pass."""
    _build(fake_root, rows=[])
    write(fake_root, acceptance.MAPPING_DOC, "# Verification\n\nno fenced block\n")
    with pytest.raises(CheckError):
        acceptance.load_mapping(fake_root)


# --------------------------------------------------------------------------
# The decision procedure
# --------------------------------------------------------------------------


def test_verify_reports_a_requirement_with_no_row(fake_root: Path) -> None:
    _build(fake_root, titles=["Unmapped"], rows=[])
    report = acceptance.verify(fake_root, collect=_no_tests, run=_always_pass)
    assert any(d.where == "reference-catalog: Unmapped" for d in report.diagnostics)


def test_verify_reports_a_test_the_suite_does_not_collect(fake_root: Path) -> None:
    _build(
        fake_root,
        titles=["Mapped"],
        rows=[_row("reference-catalog", "Mapped", test="tests/x.py::test_y")],
    )
    report = acceptance.verify(fake_root, collect=_no_tests, run=_always_pass)
    assert any("does not carry" in d.message for d in report.diagnostics)


def test_verify_reports_a_check_that_did_not_reject_its_fixture(
    fake_root: Path,
) -> None:
    """The heart of the gate: a test that does not pass is a check that did not
    fire on its violating fixture."""
    _build(
        fake_root,
        titles=["Mapped"],
        rows=[
            _row(
                "reference-catalog",
                "Mapped",
                test="tests/x.py::test_y",
                fixture="a violating tree",
            )
        ],
    )
    report = acceptance.verify(
        fake_root,
        collect=lambda _root: {"tests/x.py::test_y"},
        run=lambda _root, _node: 1,
    )
    assert any("did not pass" in d.message for d in report.diagnostics)


def test_verify_passes_when_the_named_test_is_collected_and_passes(
    fake_root: Path,
) -> None:
    _build(
        fake_root,
        titles=["Mapped"],
        rows=[_row("reference-catalog", "Mapped", test="tests/x.py::test_y")],
    )
    report = acceptance.verify(
        fake_root,
        collect=lambda _root: {"tests/x.py::test_y"},
        run=lambda _root, _node: 0,
    )
    assert report.diagnostics == [], report.render()


def test_verify_reports_a_row_naming_no_requirement(fake_root: Path) -> None:
    _build(
        fake_root,
        titles=["Real"],
        rows=[
            _row("reference-catalog", "Real", test="tests/x.py::test_y"),
            _row("reference-catalog", "Ghost", test="tests/x.py::test_y"),
        ],
    )
    report = acceptance.verify(
        fake_root,
        collect=lambda _root: {"tests/x.py::test_y"},
        run=lambda _root, _node: 0,
    )
    assert any(d.where == "reference-catalog: Ghost" for d in report.diagnostics)


def test_verify_reports_a_gap_as_unenforced(fake_root: Path) -> None:
    _build(
        fake_root,
        titles=["Partly enforced"],
        rows=[
            _row(
                "reference-catalog",
                "Partly enforced",
                test="tests/x.py::test_y",
                gap="the other half has no check",
            )
        ],
    )
    report = acceptance.verify(
        fake_root,
        collect=lambda _root: {"tests/x.py::test_y"},
        run=lambda _root, _node: 0,
    )
    assert any("is unenforced" in d.message for d in report.diagnostics)


def test_verify_reports_an_empty_spec_set_rather_than_passing(fake_root: Path) -> None:
    write(
        fake_root,
        acceptance.MAPPING_DOC,
        _doc_text([_row("reference-catalog", "Only", test="tests/x.py::test_y")]),
    )
    report = acceptance.verify(fake_root, collect=_no_tests, run=_always_pass)
    assert any("no `### Requirement:`" in d.message for d in report.diagnostics)


# --------------------------------------------------------------------------
# The committed mapping
# --------------------------------------------------------------------------


def test_the_committed_mapping_covers_every_committed_requirement(
    real_root: Path,
) -> None:
    """The two documents cannot drift: every parsed requirement has a row.

    This is the property the gate exists to hold, and it is a pure comparison --
    no pytest run -- because a mapping that has fallen behind its specs is a
    mapping that would silently skip a requirement.
    """
    requirements = {
        requirement.key for requirement in acceptance.spec_requirements(real_root)
    }
    mapped = {row.key for row in acceptance.load_mapping(real_root)}
    assert requirements - mapped == set(), "unmapped requirements"
    assert mapped - requirements == set(), "rows for requirements that do not exist"


def test_every_committed_row_names_a_well_formed_test_or_a_gap(real_root: Path) -> None:
    """A node id is `tests/....py::test_...`; a gap carries a reason."""
    for row in acceptance.load_mapping(real_root):
        if row.gap is not None:
            assert row.gap.strip(), f"{row.key} carries an empty gap reason"
            continue
        assert row.test is not None, f"{row.key} names no test and is not a gap"
        assert row.test.startswith("tests/test_"), row.test
        assert "::test_" in row.test, row.test
        assert row.fixture.strip(), f"{row.key} names no violating fixture"
