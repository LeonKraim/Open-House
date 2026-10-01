"""The golden selections -- section 3, task 3.2.

The rule list can be internally consistent, close over every path it is given,
and still have stopped covering a directory: the partition stays total, the
counts still sum, and the files it no longer selects are simply absent from a
corpus that is defined as whatever it selects. A check that catches that has to
have a path written down somewhere other than the rule list, which is what a
golden file is -- a handful of paths per repo, each with the class it is
expected to receive.

So the failures here are all of one shape: a rule change that a count would not
show. A path that has fallen out of the list, a path some exclude rule has grown
over, a path whose class has quietly moved -- and, separately, a class that no
repository pins any more.

The fixtures below build a rule list and a golden file *together and completely*
-- one rule and one entry per pinnable class -- so that a fixture is closed, and
the one finding a test asserts is the one thing wrong with it. A negative test
built on an incomplete fixture would pass on the pinning diagnostic it also
triggered, and would go on passing after the behaviour it means to test had been
removed.

The check is a pure function of `file_rules.yaml` and the golden files, so none
of this needs a clone; `test_the_committed_golden_files_pass_their_own_checks`
is the half that needs the real tree to be green.

The last test here is not about the golden check at all, and it is the one that
gives the "pure function" claim its teeth. Every failure above is provoked on a
fixture that never touched a clone, which shows only that the golden check reads
none. The claim task 7.7 actually makes is about the *whole* registered suite --
that a check which reached for `ressources/` or `.local/` would fail in CI and
not on the machine that has them -- and it is only testable by running the suite
the command runs against a checkout built without either directory.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import yaml

from tools.catalog import golden, licenses, paths, rules, validate

from .conftest import commit, write

if TYPE_CHECKING:
    from pathlib import Path

RULES_PATH = "catalog/file_rules.yaml"

#: The repository this file was imported from, captured before the `fake_root`
#: fixture redirects `paths`. The checkout test below has to copy the committed
#: tree, and the tree is only reachable here: once the fixture has run, `paths.ROOT`
#: is the empty fixture root, and a checkout built from that would be empty too --
#: so the capture is at import time on purpose and not an oversight.
REAL_ROOT = paths.ROOT

#: Every class a golden entry may pin, in the enum's order. `other` is left out
#: by construction: it is the residual, and the check under test refuses it.
PINNABLE = [name for name in paths.ARTIFACT_CLASSES if name != "other"]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _rule(
    order: int,
    pattern: str,
    kind: str = "select",
    artifact_class: str | None = "package",
    reason: str = "a reason",
) -> dict[str, object]:
    return {
        "order": order,
        "pattern": pattern,
        "kind": kind,
        "class": artifact_class,
        "reason": reason,
    }


def _exclude(order: int, pattern: str, reason: str = "a reason") -> dict[str, object]:
    return _rule(order, pattern, kind="exclude", artifact_class=None, reason=reason)


def _rules_document(entries: list[dict[str, object]]) -> str:
    return yaml.safe_dump({"rules": entries}, sort_keys=False)


def _golden_document(
    repo: str, entries: list[tuple[str, str]], declared: str | None = None
) -> str:
    document: dict[str, object] = {
        "repo": repo if declared is None else declared,
        "entries": [{"path": path, "class": name} for path, name in entries],
    }
    return yaml.safe_dump(document, sort_keys=False)


def _licences_document(repos: list[str]) -> str:
    return yaml.safe_dump(
        {"repos": [{"repo": name} for name in repos]}, sort_keys=False
    )


def _pins(
    drop: tuple[str, ...] = (),
) -> tuple[list[dict[str, object]], list[tuple[str, str]]]:
    """One select rule and one entry per pinnable class -- a closed fixture.

    Orders are strided by ten so a test can insert a rule *below* one of them,
    which is the only way to express "an exclude rule has grown over this":
    order is the whole precedence, so a rule placed after the one that pins a
    path could never take that path away from it.
    """
    rules_out: list[dict[str, object]] = []
    entries: list[tuple[str, str]] = []
    for index, name in enumerate(PINNABLE):
        if name in drop:
            continue
        rules_out.append(_rule(10 + index * 10, f"**/{name}/**", artifact_class=name))
        entries.append((f"{name}/a.yaml", name))
    return rules_out, entries


def _commit_pins(
    root: Path,
    repo: str = "x",
    drop: tuple[str, ...] = (),
    extra_rules: list[dict[str, object]] | None = None,
    extra_entries: list[tuple[str, str]] | None = None,
) -> None:
    """Write a rule list and a golden file that pin every class, minus `drop`."""
    rules_out, entries = _pins(drop)
    write(root, RULES_PATH, _rules_document([*rules_out, *(extra_rules or [])]))
    write(
        root,
        f"catalog/golden_{repo}.yaml",
        _golden_document(repo, [*entries, *(extra_entries or [])]),
    )


def _diagnostics() -> list[tuple[str, str]]:
    """The golden check's findings, collected the way the hook collects them.

    Through `validate_all` rather than by calling `check_golden` directly, for
    the reason `tests/test_file_rules.py` gives: the property several of these
    tests are about is that a broken golden file produces a *report*, and a
    report is what the command at the outermost boundary returns. A direct call
    would let a `CheckError` escape as a traceback and the test would still pass.
    """
    report = validate.validate_all()
    return [
        (d.where, d.message)
        for d in report.diagnostics
        if d.check == golden.GOLDEN_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The spec's three scenarios
# --------------------------------------------------------------------------


def test_a_class_pinned_by_no_repo_is_named(fake_root: Path) -> None:
    """The classes are covered across the four files, so one can be dropped
    from all of them at once by a change each file individually survives."""
    _commit_pins(fake_root, drop=("template",))

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "no golden entry pins `template`" in findings[0][1]


def test_a_pinned_path_selected_as_the_wrong_class_names_both(
    fake_root: Path,
) -> None:
    """The rule list still selects the path, as something else. This is the
    quiet one: the corpus still has the file and every count still adds up."""
    _commit_pins(fake_root, extra_entries=[("template/a.yaml", "helper")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("template/a.yaml")
    assert "expects `helper`" in message
    # The template rule is the sixth of the nine, so its order is 60.
    assert "confers `template`" in message
    assert "rule 60" in message


def test_an_exclude_rule_empties_a_pinned_class(fake_root: Path) -> None:
    """The entry is still declared, so the class still looks pinned and the
    coverage check has nothing to say -- the entry check is the only thing that
    can see this, and it reports the rule and the rule's reason."""
    _commit_pins(
        fake_root,
        extra_rules=[_exclude(5, "**/template/**", reason="a vendored template tree")],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith("template/a.yaml")
    assert "is excluded by rule 5" in message
    assert "a vendored template tree" in message


# --------------------------------------------------------------------------
# The other ways a pinned path can stop being one
# --------------------------------------------------------------------------


def test_a_pinned_path_the_rule_list_no_longer_decides(fake_root: Path) -> None:
    """A hole in the list, which the walker reports as an unmatched path -- but
    only for paths it is given, and a file that is no longer selected is never
    given. The golden entry is the record that it used to be."""
    _commit_pins(fake_root, extra_entries=[("elsewhere/thing.yaml", "package")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert findings[0][0].endswith("elsewhere/thing.yaml")
    assert "is matched by no rule" in findings[0][1]


def test_a_pinned_entry_may_not_pin_the_residual_class(fake_root: Path) -> None:
    """`other` is what a file falls into when no class fits. Pinning it would
    commit the corpus to keeping a file whose only justification is that we
    could not classify it."""
    _commit_pins(fake_root, extra_entries=[("other/thing.yaml", "other")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "pins `other`" in findings[0][1]
    assert "which is not one of the classes a golden entry may pin" in findings[0][1]


def test_a_golden_file_with_no_entries_guards_nothing(fake_root: Path) -> None:
    write(fake_root, RULES_PATH, _rules_document(_pins()[0]))
    write(fake_root, "catalog/golden_x.yaml", _golden_document("x", []))

    assert "carries no entries" in _messages()


def test_a_golden_file_named_for_one_repo_and_declaring_another(
    fake_root: Path,
) -> None:
    """The filename is what a reader and the coverage check take the repo from,
    so the two disagreeing means one of them describes another repo's paths."""
    rules_out, entries = _pins()
    write(fake_root, RULES_PATH, _rules_document(rules_out))
    write(
        fake_root,
        "catalog/golden_x.yaml",
        _golden_document("x", entries, declared="y"),
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "is named for `x` but declares `repo: y`" in findings[0][1]


def test_a_repo_the_project_draws_from_with_no_golden_file(fake_root: Path) -> None:
    """A fifth clone added to `licenses.yaml` without a golden file is a
    repository whose contribution no check can see shrink."""
    _commit_pins(fake_root, repo="x")
    write(fake_root, "catalog/licenses.yaml", _licences_document(["x", "y"]))

    assert "no golden file pins any path in `y`" in _messages()


def test_a_golden_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else, so an
    unguarded read here would reach it as a traceback and take the findings of
    the other ten checks with it."""
    write(fake_root, RULES_PATH, _rules_document(_pins()[0]))
    write(
        fake_root, "catalog/golden_x.yaml", "repo: x\nentries:\n  - path: 'a'\n   x\n"
    )

    assert "cannot be parsed" in _messages()


# --------------------------------------------------------------------------
# The deciding rule, and the absence of a clone
# --------------------------------------------------------------------------


def test_the_deciding_rule_is_the_one_reported(fake_root: Path) -> None:
    """Two rules match a pinned path and the *first* is the one that has to be
    named. Naming the other would send a reader to a rule that is deciding
    nothing, and the pattern that actually took the path would stay invisible."""
    rules_out, entries = _pins(drop=("helper",))
    write(
        fake_root,
        RULES_PATH,
        _rules_document(
            [
                *rules_out,
                _exclude(15, "**/helper/**", reason="the tree it grew over"),
                _rule(200, "**/helper/**", artifact_class="helper"),
            ]
        ),
    )
    write(
        fake_root,
        "catalog/golden_x.yaml",
        _golden_document("x", [*entries, ("helper/a.yaml", "helper")]),
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "is excluded by rule 15" in findings[0][1]
    assert "rule 200" not in findings[0][1]


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/` and no `.local/`, and the suite is run
    in a checkout without either. Nothing here touches them."""
    assert not paths.RESSOURCES.exists()
    assert not paths.LOCAL.exists()

    _commit_pins(fake_root, drop=("scene",))
    assert "no golden entry pins `scene`" in _messages()


# --------------------------------------------------------------------------
# The whole suite, in a checkout without the clones
# --------------------------------------------------------------------------


def _committed_paths() -> list[str]:
    """The paths a real checkout of this repository would carry.

    `--cached --others --exclude-standard` is the same question the registry
    scan asks, and being the same one is the point: the checkout has to hold
    what CI receives and nothing more. Ignored paths are absent by
    construction, and `ressources/` and `.local/` are ignored -- so the absence
    of the two is a property of the real tree being copied, not an assumption
    this fixture makes about it.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            REAL_ROOT.as_posix(),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return [entry for entry in proc.stdout.split("\0") if entry]


def _build_checkout(target: Path) -> None:
    """A committed checkout of this repository, with neither local directory.

    Copied and then committed rather than merely written out, because three of
    the registered checks read git and not the working tree -- the immutability
    check compares a version file against the commit that added it, the registry
    scan enumerates `git ls-files`, and the version-control check requires there
    to be history at all. A written-out tree would therefore exercise a
    different path from the one CI runs and could pass for a reason CI would not
    share, which is the failure this whole test exists to catch.
    """
    for relative in _committed_paths():
        source = REAL_ROOT / relative
        if not source.is_file():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    commit(target)


def test_the_registered_suite_passes_in_a_checkout_without_the_clones(
    fake_root: Path,
) -> None:
    """Task 7.7's last clause: the whole suite, in a clone-free checkout.

    The golden check reading no clone is asserted just above and is the weaker
    half of the claim. What gives it teeth is that *every* check `oh-catalog
    validate` runs is a pure function of this repository, so a check added later
    which reaches for `ressources/` or `.local/` fails here rather than only in
    CI. That is only testable by running what the command runs --
    `validate.validate_all`, which iterates `validate._CHECKS` -- against a
    checkout built to have neither directory. Reading the suite out of the
    registry is what makes this test cover a check added later without being
    edited again; a hand-written list of checks would go stale the first time
    one was wired.

    `tests/test_check_registry.py` is the other half and is deliberately not
    duplicated: it proves every `check_*(report)` in the package is either in
    `_CHECKS` or named local-only, which is the boundary itself. This proves the
    registered side stays green once the local side is taken away.
    """
    _build_checkout(fake_root)
    assert not (fake_root / "ressources").exists()
    assert not (fake_root / ".local").exists()

    report = validate.validate_all()
    assert report.ok, report.render()


# --------------------------------------------------------------------------
# The committed golden files
# --------------------------------------------------------------------------


def test_the_committed_golden_files_pass_their_own_checks() -> None:
    assert _diagnostics() == []


def test_the_committed_golden_files_pin_every_pinnable_class() -> None:
    assert set(paths.PINNABLE_CLASSES) <= set(golden.classes_pinned())


def test_no_committed_entry_pins_the_residual_class() -> None:
    for golden_file in golden.load_golden_files():
        for entry in golden_file.entries:
            assert entry.artifact_class in paths.PINNABLE_CLASSES


def test_the_committed_golden_files_and_the_repo_records_agree() -> None:
    """One golden file per repo the project draws from -- no more, no fewer."""
    pinned = {golden_file.repo for golden_file in golden.load_golden_files()}
    recorded = {record.repo for record in licenses.load_licences()}
    assert pinned == recorded


def test_every_committed_golden_path_is_listed_once() -> None:
    """A path repeated in one file is a stale line. The second copy is checked
    too, so it is not a hole -- but it is a line nobody will notice going out of
    date alongside the first, and the first is the one a reader trusts."""
    for golden_file in golden.load_golden_files():
        listed = [entry.path for entry in golden_file.entries]
        assert len(listed) == len(set(listed)), golden_file.name


def test_every_golden_file_is_discovered_as_a_catalog_data_file() -> None:
    """A golden file the data-file check does not see is hand-written data that
    nothing validates against a schema, and every check would still be green."""
    assert set(golden.golden_paths()) <= set(paths.catalog_data_files())


def test_the_committed_rule_list_still_decides_every_pinned_path() -> None:
    """The property the whole file exists for, stated directly against the two
    committed artifacts rather than through the diagnostics."""
    ruleset = rules.load_rules()
    for golden_file in golden.load_golden_files():
        for entry in golden_file.entries:
            rule = rules.decide(entry.path, ruleset.rules)
            assert rule is not None and rule.selects, golden_file.name
            assert rule.artifact_class == entry.artifact_class, entry.path
