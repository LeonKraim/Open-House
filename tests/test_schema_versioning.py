"""Version succession, current-ness and immutability -- tasks 1.5 and 7.2.

The rule under test is that nothing is ever written into a file that already
exists. That makes the interesting cases the *invalid* ones, because a valid
succession is a file that was added and then left alone, and there is nothing to
observe in that. So each rejection test builds the shape a careless author would
produce -- two versions both claiming to be first, a version naming a
grandparent, two versions retiring the same one -- and asserts that the diagnostic
names the concept and every version involved, since a diagnostic that said only
"succession is broken" would leave the reader to find the versions by hand.

Scenarios deliberately left to a later task, named here so their absence is a
decision rather than an oversight:

- "A concept is defined twice" and "A catalog data file restates a runtime
  concept" (`configuration-schemas/spec.md:65,71`) -> task 7.6, the conformance
  check.
- Vocabulary scenarios (`:86,90,95`) -> tasks 7.1 and 7.4.
- The example house, pack and export (`:155,176,191`) -> tasks 7.3-7.5.
- "Exit criterion in CI" (`:207`) -> task 7.8.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from tools.catalog import paths, schemas, validate, vcs
from tools.catalog.errors import Report

from .conftest import commit, seed_concepts, write, write_version

if TYPE_CHECKING:
    from pathlib import Path

CHECK = "schema-versioning"
IMMUTABILITY = "schema-immutability"

OTHER_CONCEPTS = tuple(c for c in paths.RUNTIME_CONCEPTS if c != "slot")


def _report(root: Path) -> Report:
    report = Report()
    schemas.check_runtime_schemas(report)
    return report


def _for_concept(report: Report, concept: str) -> str:
    """Everything reported about one concept, as one searchable string.

    The prefix is posix because every diagnostic in the package spells paths that
    way. `str(Path)` is OS-native, so a prefix built from `Path` would match
    nothing on Windows and the assertions below would silently reduce to the two
    diagnostics that happen to name the concept in their message.
    """
    prefix = f"schemas/{concept}/"
    return "\n".join(str(d) for d in report.diagnostics if d.where.startswith(prefix))


def test_every_runtime_concept_has_one_current_version_with_metadata(
    real_root: Path,
) -> None:
    """The shipping tree: eight concepts, each with exactly one current version.

    The metadata is asserted alongside the count because a version file that is
    unique but unidentifiable is not a contract anyone can resolve.
    """
    report = _report(real_root)
    assert report.diagnostics == [], report.render()

    for concept in paths.RUNTIME_CONCEPTS:
        versions = schemas.load_versions(concept)
        assert versions, f"{concept} has no version files"
        current = schemas.current_version(versions)
        assert current is not None, f"{concept} has no single current version"
        assert current.document["schema_version"] == current.version
        for field in ("$id", "title", "schema_version"):
            assert current.document.get(field), f"{concept} lacks {field}"


def test_a_concept_with_no_schema_fails_naming_the_concept(fake_root: Path) -> None:
    assert _for_concept(_report(fake_root), "slot") != ""
    assert "concept `slot` has no schema" in _report(fake_root).render()


def test_a_version_file_missing_metadata_is_reported(fake_root: Path) -> None:
    """A version file that is unique but unidentifiable is not a contract.

    Written by hand rather than with `write_version`, because that helper always
    writes the three metadata fields and a fixture that cannot produce the
    violation cannot test for it.
    """
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write(
        fake_root,
        "schemas/slot/1.0.0.json",
        '{"$id": "x", "schema_version": "1.0.0", "supersedes": null}\n',
    )
    named = _for_concept(_report(fake_root), "slot")
    assert "schemas/slot/1.0.0.json" in named
    assert "missing `title`" in named


def test_two_versions_neither_superseding_the_other_fail_naming_both(
    fake_root: Path,
) -> None:
    """The requirement's scenario: both are current, so neither is."""
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write_version(fake_root, "slot", "1.0.0", None)
    write_version(fake_root, "slot", "1.1.0", None)

    named = _for_concept(_report(fake_root), "slot")
    assert "slot" in named
    assert "1.0.0" in named
    assert "1.1.0" in named
    assert "no single current version" in named


def test_supersedes_naming_a_non_adjacent_version_fails_naming_the_versions(
    fake_root: Path,
) -> None:
    """Succession is a step, not a chain: 1.2.0 may not retire 1.0.0 directly."""
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write_version(fake_root, "slot", "1.0.0", None)
    write_version(fake_root, "slot", "1.1.0", "1.0.0")
    write_version(fake_root, "slot", "1.2.0", "1.0.0")

    named = _for_concept(_report(fake_root), "slot")
    assert "schemas/slot/1.2.0.json" in named
    assert "1.0.0" in named
    # The version it should have named is named too, so the diagnostic says what
    # was expected and not merely what was found.
    assert "1.1.0" in named
    assert "immediately preceding" in named


def test_two_versions_superseding_the_same_one_fail_naming_the_versions(
    fake_root: Path,
) -> None:
    """A fork is reported as a fork, not only as two adjacency errors."""
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write_version(fake_root, "slot", "1.0.0", None)
    write_version(fake_root, "slot", "1.1.0", "1.0.0")
    write_version(fake_root, "slot", "1.2.0", "1.0.0")

    named = _for_concept(_report(fake_root), "slot")
    assert "superseded by more than one version" in named
    assert "1.1.0" in named
    assert "1.2.0" in named


def test_a_supersedes_reference_to_an_absent_version_is_reported(
    fake_root: Path,
) -> None:
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write_version(fake_root, "slot", "1.0.0", None)
    write_version(fake_root, "slot", "1.1.0", "0.9.0")
    named = _for_concept(_report(fake_root), "slot")
    assert "schemas/slot/1.1.0.json" in named
    assert "0.9.0" in named


def test_a_filename_that_disagrees_with_schema_version_is_reported(
    fake_root: Path,
) -> None:
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write(
        fake_root,
        "schemas/slot/1.0.0.json",
        '{"$id": "x", "title": "Slot", "schema_version": "1.0.1",'
        ' "supersedes": null}\n',
    )
    named = _for_concept(_report(fake_root), "slot")
    assert "schemas/slot/1.0.0.json" in named
    assert "filename is the version" in named


def test_a_valid_succession_passes_and_the_successor_is_current(
    fake_root: Path,
) -> None:
    """The admitting case, so the checks above cannot all be satisfied by
    rejecting everything."""
    seed_concepts(fake_root, OTHER_CONCEPTS)
    write_version(fake_root, "slot", "1.0.0", None)
    write_version(fake_root, "slot", "1.1.0", "1.0.0")

    report = _report(fake_root)
    assert report.diagnostics == [], report.render()

    versions = schemas.load_versions("slot")
    assert len(versions) == 2
    current = schemas.current_version(versions)
    assert current is not None
    assert current.version == "1.1.0"


def test_parse_version_orders_numerically_and_rejects_non_versions() -> None:
    """1.10.0 is later than 1.9.0, which a string comparison gets backwards."""
    assert schemas.parse_version("1.10.0") > schemas.parse_version("1.9.0")
    with pytest.raises(ValueError, match="dotted-integer"):
        schemas.parse_version("1.0.0-rc1")


def test_editing_a_published_version_is_a_change(fake_root: Path) -> None:
    """The immutability rule itself, on a repository that has the history."""
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    commit(fake_root, "add schemas")
    path = fake_root / "schemas/slot/1.0.0.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"title": "slot"', '"title": "Slot"'),
        encoding="utf-8",
        newline="",
    )

    report = Report()
    schemas.check_immutability(report)
    assert [d.where for d in report.diagnostics] == ["schemas/slot/1.0.0.json"]


def test_publishing_a_successor_by_editing_the_retired_version_is_a_change(
    fake_root: Path,
) -> None:
    """The exact mistake `supersedes` exists to make unnecessary.

    A `superseded_by` pointer written into the version being retired is the act
    the backwards arrow removes; if it were performed anyway it is an edit to a
    frozen file like any other, and it is caught the same way.
    """
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    write_version(fake_root, "slot", "1.1.0", "1.0.0")
    commit(fake_root, "add schemas")

    path = fake_root / "schemas/slot/1.0.0.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"supersedes": null', '"superseded_by": "1.1.0"'
        ),
        encoding="utf-8",
        newline="",
    )

    report = Report()
    schemas.check_immutability(report)
    assert "schemas/slot/1.0.0.json" in [d.where for d in report.diagnostics]


def test_a_superseded_version_that_is_deleted_is_a_change(fake_root: Path) -> None:
    """Deleting a version is the edit a content comparison cannot see.

    The tree is committed first, because the requirement's clause is about a
    version *introduced at some commit*: without history there is nothing to say
    the file was ever there, and the test would be exercising the dangling-
    `supersedes` rule under a deletion test's name.
    """
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    write_version(fake_root, "slot", "1.1.0", "1.0.0")
    commit(fake_root, "add schemas")
    (fake_root / "schemas/slot/1.0.0.json").unlink()

    report = Report()
    schemas.check_immutability(report)
    assert "schemas/slot/1.0.0.json" in [d.where for d in report.diagnostics]


def test_deleting_the_current_version_is_a_change(fake_root: Path) -> None:
    """The hole a `supersedes`-only absence rule leaves open.

    Nothing names the current version, so the dangling-reference rule cannot
    reach it, and the content loop globs the working tree so it never sees the
    file at all. Delete the tip of a succession and a published version has
    silently been un-published -- which is why the candidate set has to come from
    git rather than from the links between versions.
    """
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    write_version(fake_root, "slot", "1.1.0", "1.0.0")
    commit(fake_root, "add schemas")
    (fake_root / "schemas/slot/1.1.0.json").unlink()

    report = Report()
    schemas.check_immutability(report)
    assert "schemas/slot/1.1.0.json" in [d.where for d in report.diagnostics]


def test_a_root_with_no_history_fails_the_immutability_check(
    fake_root: Path,
) -> None:
    """The requirement's own scenario, on the check it actually names.

    Built from `fake_root` with its `.git` removed so that every location moves
    together. Patching `ROOT` alone would leave `SCHEMAS` pointing at the
    shipping repository, and the check would be reading two trees at once.
    """
    shutil.rmtree(fake_root / ".git")
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    report = Report()
    schemas.check_immutability(report)
    assert [d.where for d in report.diagnostics] == [fake_root.as_posix()]


def test_editing_a_catalog_schema_in_place_is_not_a_change(fake_root: Path) -> None:
    """`schemas/catalog/` is out of scope, by design and not by accident.

    A runtime version is frozen once published. A catalog schema describes this
    repository's own intermediate artifacts, nothing outside consumes it, and
    freezing it would turn every later task that adds a field to a catalog file
    into a version bump. So the edit is made on a tree that has the history, and
    it must pass.
    """
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    write(fake_root, "schemas/catalog/slots.json", '{"schema_version": "1.0.0"}\n')
    commit(fake_root, "add catalog schema")

    path = fake_root / "schemas/catalog/slots.json"
    path.write_text(
        '{"schema_version": "1.0.0", "type": "object"}\n',
        encoding="utf-8",
        newline="",
    )

    report = Report()
    schemas.check_immutability(report)
    assert report.diagnostics == [], report.render()


def test_the_committed_tree_is_green(real_root: Path) -> None:
    """The fifth clause of 7.2: the check on the tree the project actually ships.

    Every other test here builds the tree it checks, which is the only way to
    watch this check fail -- and is also why none of them has read the tree the
    project releases. `schema-immutability` is the one check whose subject is
    committed history rather than the working tree, so a tree whose runtime
    versions were never committed, or a check re-scoped off the eight concepts,
    is one a fixture cannot see and is named only here.

    The per-version assertion is what keeps the pass from being vacuous. A check
    that skipped every file because git knew none of them would report nothing,
    and nothing is exactly what a green result looks like, so the loop asserts
    that each version has an introducing commit before the empty report is
    trusted.

    Written against the tree as it stands, because 7.1 has not run yet. Once
    `behavior-vocabulary/1.1.0.json` is published this must still hold -- and it
    is precisely what fails if 7.1 publishes it by editing `1.0.0.json` rather
    than by adding a file, which is the act the backwards arrow exists to make
    unnecessary.
    """
    assert vcs.is_repository(), (
        "the committed tree must have history to compare against"
    )

    for concept in paths.RUNTIME_CONCEPTS:
        versions = schemas.load_versions(concept)
        assert versions, f"{concept} has no committed version to compare"
        for version in versions:
            commit = vcs.introducing_commit(version.relative)
            assert commit is not None, (
                f"{version.relative} has no introducing commit, so the "
                "immutability check skips it rather than comparing it"
            )

    report = Report()
    schemas.check_immutability(report)
    assert report.diagnostics == [], report.render()


def test_an_uncommitted_version_is_not_yet_a_change(fake_root: Path) -> None:
    """A version about to be added in this very commit is not a violation."""
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    report = Report()
    schemas.check_immutability(report)
    assert report.diagnostics == [], report.render()


def test_a_malformed_version_file_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The validator is the pre-commit hook; it may not die with a traceback.

    A check that raises propagates out of `oh-catalog validate`, so the command
    users run before every commit would fail with a stack trace instead of naming
    the file at fault.
    """
    seed_concepts(fake_root, paths.RUNTIME_CONCEPTS)
    write(fake_root, "schemas/slot/1.0.0.json", "{not json\n")
    report = validate.validate_all()
    assert any(d.where.endswith("schemas/slot/1.0.0.json") for d in report.diagnostics)
