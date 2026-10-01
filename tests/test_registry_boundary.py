"""Registry boundary and version control -- task 1.4.

The registry requirement is one sentence long and the interesting part of it is
the words *committed artifact*. The three pointer keys are named in the
requirement's own prose, in the design record and in this package, so a
recogniser that flagged any file containing them would fail on the tree it
exists to keep green; and the scan is over the repository rather than the
working tree, so a virtualenv is not mistaken for the project. The tests below
are therefore as much about what the check declines to flag as about what it
flags.

Scenarios deliberately left to a later task, named here so their absence is a
decision rather than an oversight:

- "Boundary checks run in CI" and "CI job needs the reference clones"
  (`architecture-invariants/spec.md:130,134`) -> task 7.8, which wires CI. There
  is no CI configuration to check until then.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from tools.catalog import invariants
from tools.catalog.errors import Report

from .conftest import commit, write

if TYPE_CHECKING:
    from pathlib import Path

REGISTRY = "registry-boundary"
VERSION_CONTROL = "version-control"


def _registry_report(root: Path) -> Report:
    report = Report()
    invariants.check_registry_boundary(report)
    return report


@pytest.mark.parametrize("key", ["registry_url", "registry_index", "registry_key"])
def test_a_committed_artifact_carrying_a_pointer_key_is_rejected(
    fake_root: Path, key: str
) -> None:
    """The requirement says *committed*, so the fixture is committed.

    The check reads untracked-but-not-ignored files too, which is a deliberate
    widening tested separately below. Testing the widening here would have left
    the scenario as written -- a file that is in the repository -- untested.
    """
    write(fake_root, "catalog/packs.yaml", f"{key}: https://example.invalid\n")
    commit(fake_root)
    report = _registry_report(fake_root)
    assert [d.where for d in report.diagnostics] == ["catalog/packs.yaml"]
    assert key in report.render()


def test_an_untracked_file_that_is_not_ignored_is_still_scanned(
    fake_root: Path,
) -> None:
    """A deliberate widening of the requirement, asserted so it is a decision.

    The requirement's stated purpose is that pointer files cannot accrete *in the
    meantime*. Accrual happens in the working tree before it happens in the
    index, so a scratch file at the root is scanned. The cost is that an
    unintended `*.yaml` at the root fails a pre-commit run; the alternative --
    waiting for the pointer to be committed -- is waiting until the boundary has
    already been crossed.
    """
    write(fake_root, "scratch.yaml", "registry_index: https://example.invalid\n")
    report = _registry_report(fake_root)
    assert [d.where for d in report.diagnostics] == ["scratch.yaml"]


def test_artifact_matching_the_registry_filename_is_rejected(fake_root: Path) -> None:
    """A pointer file is recognised by its name as well as by its keys."""
    write(fake_root, "catalog/community.registry.json", "{}\n")
    report = _registry_report(fake_root)
    assert [d.where for d in report.diagnostics] == ["catalog/community.registry.json"]


def test_prose_naming_the_keys_is_exempt(fake_root: Path) -> None:
    """The requirement's own text names all three keys.

    If this failed, the check could not run against the tree that contains the
    requirement, which is the tree it is for.
    """
    write(
        fake_root,
        "docs/registry.md",
        "No `registry_url`, `registry_index` or `registry_key` may appear.\n",
    )
    assert _registry_report(fake_root).diagnostics == []


@pytest.mark.parametrize("key", ["registry_url", "registry_index", "registry_key"])
def test_a_dotenv_file_carrying_a_pointer_key_is_rejected(
    fake_root: Path, key: str
) -> None:
    """`.env` is a dotfile, and pathlib reports no suffix for one.

    `PurePath(".env").suffix` is `""`, because pathlib does not read a leading
    dot as an extension separator. So an allowlist entry spelled `.env` would be
    inert: it would match `config.env` and skip the file it was added for, which
    is the same failure the check exists to prevent, one level down -- a
    constraint that constrains nothing.
    """
    write(fake_root, ".env", f"HA_TOKEN=x\n{key}=https://example.invalid\n")
    report = _registry_report(fake_root)
    assert [d.where for d in report.diagnostics] == [".env"]


def test_the_artifact_name_predicate_matches_dotfiles() -> None:
    """The predicate is asserted directly.

    The scan's behaviour on a dotfile depends on a pathlib quirk that is easy to
    get wrong and invisible in a fixture whose files all have ordinary names, so
    the rule is stated once, in one place, and checked there.
    """
    assert invariants.is_artifact_name(".env")
    assert invariants.is_artifact_name(".env.local")
    assert invariants.is_artifact_name("config.env")
    assert invariants.is_artifact_name("rules.yaml")
    assert not invariants.is_artifact_name("checker.py")
    assert not invariants.is_artifact_name("HOSTS")


def test_a_local_pack_reference_is_not_a_pointer(fake_root: Path) -> None:
    """A pack naming a sibling pack in `packs/official/` is configuration.

    The fixture deliberately carries `registry` as a key and a path where a URL
    would sit, because the scenario exists to keep the recogniser narrow:
    `registry:` is an ordinary thing for a pack index to hold, and a recogniser
    keyed on the word, or on "a URL", would fail a legal pack. A fixture with
    none of those would test nothing -- nothing could fail it.
    """
    write(
        fake_root,
        "packs/official/example-house.yaml",
        "house: example\n"
        "registry:\n"
        "  source: ../official/example-pack.yaml\n"
        "packs:\n  - official/example-pack\n",
    )
    write(
        fake_root,
        "packs/official/example-pack.yaml",
        "name: example-pack\nkind: pack\n",
    )
    assert _registry_report(fake_root).diagnostics == []


def test_ordinary_tooling_is_not_mistaken_for_a_pointer(fake_root: Path) -> None:
    """`repo:` in pre-commit and `registry` in package.json are not pointers.

    The key set is explicit rather than "a URL" for exactly this reason, and the
    scenario is asserted rather than assumed, so a later widening of the
    recogniser fails here rather than in a user's build.
    """
    write(
        fake_root,
        ".pre-commit-config.yaml",
        "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.16.9\n",
    )
    write(
        fake_root,
        "package.json",
        '{"name": "panel", "registry": "https://registry.npmjs.org/"}\n',
    )
    report = _registry_report(fake_root)
    invariants.check_declared_dependency_names(report)
    assert report.diagnostics == [], report.render()


def test_the_clones_and_the_local_store_are_out_of_scope(fake_root: Path) -> None:
    """`ressources/` and `.local/` hold third-party text we may not commit.

    Their contents are not ours to police and are absent in CI, so a check that
    read them would pass locally and fail in the pipeline.
    """
    write(fake_root, "ressources/johnkoht/notes.yaml", "registry_key: abc\n")
    write(fake_root, ".local/raw.yaml", "registry_url: https://example.invalid\n")
    assert _registry_report(fake_root).diagnostics == []


def test_ignored_files_are_not_scanned(fake_root: Path) -> None:
    """An ignored directory is not part of the project, whatever it holds.

    This is the clause that keeps a virtualenv, a built panel and a container's
    generated config from being read as though they were ours.
    """
    write(fake_root, ".gitignore", "build/\n")
    write(fake_root, "build/vendor.json", '{"registry_url": "https://x.invalid"}\n')
    assert _registry_report(fake_root).diagnostics == []


def test_committed_tree_is_clean(real_root: Path) -> None:
    report = Report()
    invariants.check_registry_boundary(report)
    invariants.check_declared_dependency_names(report)
    invariants.check_version_control(report)
    assert report.diagnostics == [], report.render()


def test_a_tracked_clone_is_rejected_naming_the_path(fake_root: Path) -> None:
    """A clone dragged into the index is the one commit that must never happen."""
    write(fake_root, "ressources/johnkoht/README.md", "hello\n")
    subprocess.run(
        ["git", "add", "-f", "ressources/johnkoht/README.md"],
        cwd=fake_root,
        capture_output=True,
        text=True,
        check=True,
    )
    report = Report()
    invariants.check_version_control(report)
    assert [d.where for d in report.diagnostics] == ["ressources/johnkoht/README.md"]


def test_an_untracked_clone_is_not_reported(fake_root: Path) -> None:
    """The clones are supposed to be present and untracked. That is the design."""
    write(fake_root, "ressources/johnkoht/README.md", "hello\n")
    report = Report()
    invariants.check_version_control(report)
    assert report.diagnostics == []


def test_a_root_with_no_history_fails_naming_the_root(fake_root: Path) -> None:
    """The requirement's own scenario: no history is a failure, not a skip.

    Built from `fake_root` with its `.git` removed, rather than by patching
    `ROOT` alone. Patching one location would leave `SCHEMAS` and the rest
    pointing at the repository being tested, so the check would be reading two
    trees at once and passing for the wrong reason.
    """
    shutil.rmtree(fake_root / ".git")
    report = Report()
    invariants.check_version_control(report)
    assert [d.where for d in report.diagnostics] == [fake_root.as_posix()]


def test_committed_fixture_is_still_clean(fake_root: Path) -> None:
    """A tree that has been committed does not become unclean for being so."""
    write(fake_root, "catalog/slots.yaml", "slots: []\n")
    commit(fake_root)
    report = Report()
    invariants.check_registry_boundary(report)
    invariants.check_version_control(report)
    assert report.diagnostics == [], report.render()
