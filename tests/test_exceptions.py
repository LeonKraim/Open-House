"""Residual-class justifications -- section 3, task 3.4.

`other` is the class that admits anything, which is why it needs a second file.
The ledger and the inventory are two views that have to agree, and the tests
below break that agreement from both sides, because the two directions are
different failures: a file that fell into the residual without a reason, and a
reason left behind by a file that no longer needs one.

The check is a pure function of two committed files, so the fixtures write those
two files and nothing else -- no clone, no rules, no git history. They are routed
through the check directly rather than through `validate_all`, unlike the tests
for the sibling checks, because every other check in that run would contribute
diagnostics about a fixture that was never a whole repository, and the count of
findings is asserted here: a test that expected one finding and got two would
pass while reporting something else entirely.

The inventory documents are built to satisfy `schemas/catalog/inventory.json`
even though this check does not validate them against it. A fixture that would
fail its own schema is a fixture asserting a shape the project does not have.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

import yaml

from tools.catalog import exceptions, inventory, paths
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

LEDGER_PATH = f"catalog/{exceptions.EXCEPTIONS_FILE}"
INVENTORY_PATH = f"catalog/{inventory.INVENTORY_FILE}"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _selected(path: str, artifact_class: str, rule: int = 10) -> dict[str, object]:
    return {
        "path": path,
        "selected": True,
        "class": artifact_class,
        "parse": "parsed",
        "rule": rule,
        "error": None,
    }


def _excluded(path: str, rule: int) -> dict[str, object]:
    return {
        "path": path,
        "selected": False,
        "class": None,
        "parse": "not_applicable",
        "rule": rule,
        "error": None,
    }


def _inventory(files: list[dict[str, object]], repo: str = "x") -> dict[str, object]:
    """A one-repo inventory document, closing over the files it is given."""
    selected = [record for record in files if record["selected"] is True]
    by_rule: dict[str, dict[str, int]] = {}
    for record in files:
        entry = by_rule.setdefault(
            str(record["rule"]), {"decided": 0, "selected": 0, "excluded": 0}
        )
        entry["decided"] += 1
        entry["selected" if record["selected"] is True else "excluded"] += 1
    counts = {
        "tracked": len(files),
        "selected": len(selected),
        "excluded": len(files) - len(selected),
    }
    return {
        "repos": [
            {
                "repo": repo,
                "counts": counts,
                "files": files,
                "by_class": dict(Counter(str(r["class"]) for r in selected)),
                "by_rule": by_rule,
                "by_parse": dict(Counter(str(r["parse"]) for r in selected)),
            }
        ],
        "totals": counts,
    }


def _commit(
    root: Path,
    *,
    files: list[dict[str, object]],
    entries: list[dict[str, object]] | None = None,
    ledger: str | None = None,
) -> None:
    """The two committed files this check reads, and nothing else."""
    write(root, INVENTORY_PATH, json.dumps(_inventory(files), indent=2) + "\n")
    if ledger is not None:
        write(root, LEDGER_PATH, ledger)
        return
    write(
        root,
        LEDGER_PATH,
        yaml.safe_dump({"exceptions": entries or []}, sort_keys=False),
    )


def _justification(path: str, reason: str = "unknown_artifact", repo: str = "x"):
    return {"repo": repo, "path": path, "reason": reason}


def _diagnostics() -> list[str]:
    """Every diagnostic this check produces, as rendered lines.

    `CheckError` is caught here the way `validate_all` catches it, because a
    check that cannot continue raises and a fixture that made it raise would
    otherwise surface as a traceback rather than as the diagnostic it is.
    """
    report = Report()
    try:
        exceptions.check_exceptions(report)
    except CheckError as exc:
        report.add(exc.check, exc.where, exc.message)
    return [str(diagnostic) for diagnostic in report.diagnostics]


# --------------------------------------------------------------------------
# The two directions the requirement names
# --------------------------------------------------------------------------


def test_an_other_file_with_no_entry_is_named(fake_root: Path) -> None:
    """A file that fell into the residual and was not accounted for.

    The class admits anything, so without this the residual is where the hard
    cases go to look handled.
    """
    _commit(fake_root, files=[_selected("a.yaml", "other")])

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "x:a.yaml" in messages[0]
    assert "no entry in catalog/inventory_exceptions.yaml" in messages[0]


def test_a_justified_other_file_passes(fake_root: Path) -> None:
    """The passing half, so the test above is not failing for some other reason."""
    _commit(
        fake_root,
        files=[_selected("a.yaml", "other")],
        entries=[_justification("a.yaml", "generated_file")],
    )

    assert _diagnostics() == []


def test_an_invented_reason_is_named(fake_root: Path) -> None:
    """The reason set is closed, and the check reads it rather than any string.

    Without this the file would accept `"it seemed fine"`, and a reason that can
    be anything is not a reason.
    """
    _commit(
        fake_root,
        files=[_selected("a.yaml", "other")],
        entries=[_justification("a.yaml", "it_seemed_fine")],
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "x:a.yaml" in messages[0]
    assert "'it_seemed_fine'" in messages[0]
    assert "'unknown_artifact', 'generated_file'" in messages[0]


def test_an_entry_for_an_excluded_path_names_the_precedence(fake_root: Path) -> None:
    """Exclude rules take precedence, so a file cannot travel both routes.

    An entry naming an excluded path is a justification for a classification
    that never happened, and the diagnostic has to say which rule excluded it --
    otherwise the reader is told the entry is wrong without being told why.
    """
    _commit(
        fake_root,
        files=[_excluded("b.yaml", 30)],
        entries=[_justification("b.yaml", "unknown_artifact")],
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "x:b.yaml" in messages[0]
    assert "excludes" in messages[0] and "rule 30" in messages[0]
    assert "receives no artifact class and needs no entry" in messages[0]


# --------------------------------------------------------------------------
# The ledger read against the inventory rather than trusted
# --------------------------------------------------------------------------


def test_an_entry_for_a_file_selected_under_a_real_class_is_reported(
    fake_root: Path,
) -> None:
    """The stale row, which is what a rule change leaves behind.

    Nothing else in the project reads this file, so an entry that has outlived
    its file would sit there justifying nothing for as long as the repository
    lives.
    """
    _commit(
        fake_root,
        files=[_selected("a.yaml", "automation")],
        entries=[_justification("a.yaml", "unknown_artifact")],
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "x:a.yaml" in messages[0]
    assert "selects as 'automation'" in messages[0]


def test_an_entry_naming_no_file_in_the_inventory_is_reported(
    fake_root: Path,
) -> None:
    _commit(
        fake_root,
        files=[_selected("a.yaml", "automation")],
        entries=[_justification("ghost.yaml", "unknown_artifact")],
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "x:ghost.yaml" in messages[0]
    assert "names no file the inventory carries" in messages[0]


def test_a_duplicate_entry_is_reported(fake_root: Path) -> None:
    """Two rows for one file is two reasons, and neither is the deciding one."""
    _commit(
        fake_root,
        files=[_selected("a.yaml", "other")],
        entries=[
            _justification("a.yaml", "unknown_artifact"),
            _justification("a.yaml", "generated_file"),
        ],
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "justified more than once" in messages[0]


def test_an_entry_missing_its_repo_or_path_is_reported(fake_root: Path) -> None:
    """A path is repo-relative, so an entry naming one and not the other names
    nothing -- the same path can exist in two repos."""
    _commit(
        fake_root,
        files=[_selected("a.yaml", "automation")],
        entries=[{"repo": "", "path": "a.yaml", "reason": "unknown_artifact"}],
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert "does not name both a `repo` and a `path`" in messages[0]


# --------------------------------------------------------------------------
# The ledger itself, and the boundary
# --------------------------------------------------------------------------


def test_a_missing_ledger_is_a_diagnostic_not_a_traceback(fake_root: Path) -> None:
    """Absence is a change to the repository, not a state the corpus may be in.

    The file is committed and tracked by `catalog_data_files()`, so reading it as
    an empty ledger would turn the weakest possible input into a pass -- and it
    would do it silently, because an empty ledger is also the correct answer for
    a corpus with no residual files.
    """
    write(fake_root, INVENTORY_PATH, json.dumps(_inventory([]), indent=2) + "\n")

    assert _diagnostics() == [
        f"[{exceptions.EXCEPTIONS_CHECK}] {LEDGER_PATH}: does not exist"
    ]


def test_a_ledger_that_will_not_parse_is_a_diagnostic(fake_root: Path) -> None:
    """`validate_all` catches `CheckError` and nothing else, so an unguarded
    parse here would reach the pre-commit hook as a traceback and lose every
    other check's findings with it."""
    _commit(fake_root, files=[], ledger="exceptions: [\n")

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert LEDGER_PATH in messages[0]
    assert "is not valid YAML" in messages[0]


def test_a_missing_inventory_is_a_diagnostic_not_a_traceback(fake_root: Path) -> None:
    """This check is a second reader of a file the inventory check also reads,
    so a broken inventory has to be reported twice rather than crash once."""
    write(
        fake_root,
        LEDGER_PATH,
        yaml.safe_dump({"exceptions": []}, sort_keys=False),
    )

    messages = _diagnostics()
    assert len(messages) == 1, messages
    assert INVENTORY_PATH in messages[0]
    assert "does not exist" in messages[0]


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """The boundary the spec draws, asserted rather than assumed.

    Exactly two checks are local. This is not one of them: it reads two committed
    files, so it runs in CI, and the fixture proves it by having no `ressources/`
    at all.
    """
    assert not (fake_root / "ressources").exists()

    _commit(
        fake_root,
        files=[_selected("a.yaml", "other")],
        entries=[_justification("a.yaml", "unknown_artifact")],
    )

    assert _diagnostics() == []


def test_the_committed_ledger_and_inventory_agree(real_root: Path) -> None:
    """The real tree, where the answer is whatever it is.

    It is a passing test today because no file in the four repositories landed in
    the residual, and that is the finding rather than an absence of one: the
    ledger is empty because the rule list classifies everything it selects.
    """
    report = Report()
    exceptions.check_exceptions(report)

    assert report.diagnostics == [], report.render()


def test_the_residual_is_a_class_the_enum_carries(real_root: Path) -> None:
    """`OTHER` is read off the closed enum rather than spelled again, so that a
    second module cannot disagree with `paths` about which value is the
    residual."""
    assert exceptions.OTHER in paths.ARTIFACT_CLASSES
