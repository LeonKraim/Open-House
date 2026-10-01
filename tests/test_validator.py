"""The validator and the command that runs it -- tasks 1.1 and 1.6.

`validate_all` is the pre-commit hook, the CI gate and the acceptance script's
driver, so "the validator passed" has to mean the same thing in all three. These
tests are about the composition rather than the individual checks: that the
committed tree passes the whole thing, that a check which cannot continue is
reported rather than raised, and that the command exits with the status a hook
reads.

The per-check tests live beside the checks they cover.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from tools.catalog import validate
from tools.catalog.cli import app

from .conftest import commit, write

if TYPE_CHECKING:
    from pathlib import Path

CATALOG_CHECK = "catalog-schema"


def test_the_validator_is_clean_on_the_committed_tree(real_root: Path) -> None:
    """The command, on the tree it is pointed at. Nothing here is a fixture.

    A check proven only against a tree we built is a check that has never been
    seen to pass on the real one, and every Phase 0 claim about the repository
    being green rests on this.
    """
    report = validate.validate_all()
    assert report.diagnostics == [], report.render()


def test_a_catalog_data_file_with_no_schema_fails_naming_the_file(
    fake_root: Path,
) -> None:
    """Task 1.6's verify clause.

    The alternative to failing -- skipping the file -- is what lets a new data
    file arrive unvalidated simply by nobody adding a schema for it, which is the
    failure the pairing rule exists to prevent.
    """
    write(fake_root, "catalog/slots.yaml", "slots: []\n")
    report = validate.validate_all()
    assert "catalog/slots.yaml" in [d.where for d in report.diagnostics]


def test_a_markdown_artifact_needs_no_schema(fake_root: Path) -> None:
    """`README.md` and `overlap.md` are documents, not data.

    Requiring a schema for prose would mean inventing one to satisfy the checker,
    and the schema would describe nothing.
    """
    write(fake_root, "catalog/README.md", "# The catalog\n")
    write(fake_root, "catalog/overlap.md", "# Overlap\n")
    report = validate.validate_all()
    assert not [d for d in report.diagnostics if d.check == CATALOG_CHECK]


def test_the_command_exits_zero_and_is_silent_when_quiet(
    real_root: Path,
) -> None:
    """`-q` is what the pre-commit hook uses, and a hook that prints on every
    passing commit is a hook people learn to ignore."""
    result = CliRunner().invoke(app, ["validate", "--quiet"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_the_command_exits_non_zero_and_names_the_file_on_failure(
    fake_root: Path,
) -> None:
    """The exit status is what a hook reads; the named file is what a human reads.

    Both halves are asserted because either alone is a command that fails
    uselessly -- a non-zero status with no output, or a message with a zero
    status that a hook would treat as success.
    """
    write(fake_root, "catalog/slots.yaml", "slots: []\n")
    commit(fake_root)
    result = CliRunner().invoke(app, ["validate"])
    assert result.exit_code == 1
    assert "catalog/slots.yaml" in result.output
