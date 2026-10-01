"""Fixtures for the Phase 0 checks.

Two kinds of test appear in this suite, and they need different things. A check's
behaviour on a *violating* tree has to be exercised on a tree we build, because
the committed tree is by construction the one that passes, and a check proven
only against it is a check that has never been seen to fail. A check's behaviour
on the *committed* tree has to be exercised on the real one, because a fixture
that merely resembles the repository cannot tell us the repository is green.

So the fixtures come in pairs. `fake_root` redirects every location in
`tools.catalog.paths` into a temporary directory. `real_root` is the repository
as it is, and the tests that use it are the ones asserting the tree ships clean.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from tools.catalog import paths

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


#: Locations `tools.catalog.paths` derives from its own file position, as paths
#: relative to the root. Every one of them has to move together: a check that
#: read `ROOT` from the fixture and `SCHEMAS` from the repository would be
#: testing neither. Spelled out rather than derived by lowercasing the attribute
#: name, because `SCHEMA_CATALOG` is `schemas/catalog` and not `schema_catalog`,
#: and a fixture that got that wrong would silently test the wrong tree.
_LOCATIONS = {
    "CATALOG": "catalog",
    "SCHEMAS": "schemas",
    "SCHEMA_CATALOG": "schemas/catalog",
    "DOCS": "docs",
    "PACKS": "packs",
    "RESSOURCES": "ressources",
    "LOCAL": ".local",
}


@pytest.fixture
def real_root() -> Path:
    """The repository as committed. Never written to."""
    return paths.ROOT


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A writable stand-in for the repository root.

    `tools.catalog.paths` is patched in place rather than the checks being given
    a root argument, because the checks are written against module-level
    locations and rewriting them to thread a root through every function would
    make the production code carry the test's shape.
    """
    monkeypatch.setattr(paths, "ROOT", tmp_path, raising=True)
    for name, relative in _LOCATIONS.items():
        monkeypatch.setattr(paths, name, tmp_path / relative, raising=True)
    # git must not escape the fixture. Two tests delete a fixture's `.git` and
    # assert the tree is repository-less; if the temporary directory were itself
    # inside a checkout, git would walk up, find the enclosing repository, and
    # those tests would pass for a reason that has nothing to do with the tree
    # they built. The ceiling blocks the directory above the fixture and
    # everything above that, while the fixture root itself is still searched --
    # it is the working directory, and that is always consulted.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    # The registry boundary and the version-control checks enumerate files
    # through git, so a fixture tree has to be a repository or those checks
    # would report the absence of history rather than the thing under test.
    _git_init(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.0"\n'
        'dependencies = ["pyyaml>=6.0"]\n',
        encoding="utf-8",
        newline="",
    )
    yield tmp_path


def _git_init(root: Path) -> None:
    """Make a fixture tree a git repository, with a deterministic configuration.

    Everything the checks read is pinned per invocation rather than inherited
    from the machine, because each of these settings changes what git reports and
    a test that read the developer's global config would pass or fail on whose
    laptop it is on. The settings that matter here:

    - `user.email`/`user.name`, so committing does not require a global identity.
    - `commit.gpgsign`, so a machine with signing configured can still commit.
    - `core.excludesFile`, pointed at a path inside the fixture that does not
      exist. The registry scan enumerates untracked-not-ignored files, and a
      developer whose global ignore file covers `*.yaml` would otherwise make a
      fixture file vanish from the scan and fail a test about the check.
    - `core.autocrlf false`, so a fixture written with LF is stored with LF. The
      immutability check compares a working-tree file against the blob, and
      checkout translation would make that comparison depend on the platform.
    """
    for args in (
        ("init", "-q"),
        ("config", "user.email", "tests@open-house.invalid"),
        ("config", "user.name", "Open House tests"),
        ("config", "commit.gpgsign", "false"),
        ("config", "core.autocrlf", "false"),
        (
            "config",
            "core.excludesFile",
            (root / ".git" / "no-global-excludes").as_posix(),
        ),
    ):
        subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )


def commit(root: Path, message: str = "fixture") -> None:
    """Stage and commit everything in a fixture tree."""
    for args in (("add", "-A"), ("commit", "-q", "-m", message)):
        subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )


def write(root: Path, relative: str, text: str) -> Path:
    """Write a file into a fixture tree, creating parents. LF, no translation."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def write_version(
    root: Path, concept: str, version: str, supersedes: str | None
) -> Path:
    """Write a minimal but complete version file for a concept."""
    document: dict[str, object] = {
        "$id": f"https://open-house.invalid/schemas/{concept}/{version}.json",
        "title": concept,
        "schema_version": version,
        "supersedes": supersedes,
        "type": "object",
    }
    return write(
        root,
        f"schemas/{concept}/{version}.json",
        json.dumps(document, indent=2) + "\n",
    )


def seed_concepts(root: Path, concepts: tuple[str, ...] | list[str]) -> None:
    """Give every named concept a single, current 1.0.0 version file.

    Tests that are about one concept's succession start from a tree where the
    other seven are already satisfied, so that a failure cannot be a missing
    schema somewhere else being reported under the same check.
    """
    for concept in concepts:
        write_version(root, concept, "1.0.0", None)
