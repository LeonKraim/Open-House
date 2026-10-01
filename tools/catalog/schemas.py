"""Versioned schemas: succession, current-ness and immutability.

The rule this module exists to enforce is that **nothing is ever written into a
file that already exists**. A published schema version is frozen; the only way
to change a concept is to publish a new version file, and the new file is the
one that says what it replaces:

    schemas/<concept>/1.0.0.json   supersedes: null
    schemas/<concept>/1.1.0.json   supersedes: "1.0.0"

A version is *current* when no other version present for that concept names it.
The arrow points backwards for a reason worth stating, because two earlier
designs pointed it forwards and both were broken in the same way: with a
`superseded_by` field, publishing a successor means writing into the version
being retired, and the immutability rule fires on the very act of publishing.
Retiring a version is not a content change to it -- it is the *absence* of any
successor naming it -- so no write is needed and none is permitted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import paths, vcs
from .errors import CheckError, Report, read_text
from .narrow import as_mapping

if TYPE_CHECKING:
    from pathlib import Path

CHECK = "schema-versioning"
IMMUTABILITY_CHECK = "schema-immutability"


def parse_version(text: str) -> tuple[int, ...]:
    """Order versions by their numeric components.

    Version strings are dotted integers. Comparison is tuple-wise so that 1.10.0
    sorts after 1.9.0, which a string comparison gets backwards.
    """
    parts = text.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a dotted-integer version: {text!r}")
    return tuple(int(p) for p in parts)


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    concept: str
    version: str
    order: tuple[int, ...]
    path: Path
    supersedes: str | None
    document: dict[str, object]

    @property
    def relative(self) -> str:
        """The path as every diagnostic in this package spells it: posix.

        `str(Path)` is OS-native, so on Windows it produces
        `schemas\\slot\\1.0.0.json` while the git enumeration in `vcs` produces
        `schemas/slot/1.0.0.json`. A caller that filtered diagnostics by the
        prefix git gave it -- which is exactly how the tests select one
        concept's findings -- would match nothing on one platform and everything
        on the other.
        """
        return self.path.relative_to(paths.ROOT).as_posix()


def _where(path: Path) -> str:
    """A path as every diagnostic in this package spells it: posix, from the root.

    `str(Path)` is OS-native, so on Windows a `CheckError` would name
    `C:\\...\\schemas\\slot\\1.0.0.json` while every other diagnostic and every
    path git returns names `schemas/slot/1.0.0.json`. Two spellings of the same
    path in one report is how a filter over the report silently matches nothing.
    """
    try:
        return path.relative_to(paths.ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def load_versions(concept: str) -> list[SchemaVersion]:
    """Every version file present for a concept, ordered oldest first."""
    directory = paths.SCHEMAS / concept
    if not directory.is_dir():
        return []
    found: list[SchemaVersion] = []
    for path in sorted(directory.glob("*.json")):
        try:
            order = parse_version(path.stem)
        except ValueError as exc:
            # A stem that is not a dotted integer cannot be ordered, and the
            # sort below would raise the same error as an unhandled traceback
            # rather than as a diagnostic naming the file.
            raise CheckError(CHECK, _where(path), str(exc)) from exc
        try:
            loaded: object = json.loads(path.read_bytes().decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CheckError(
                CHECK, _where(path), f"version file is not valid JSON: {exc}"
            ) from exc
        document = as_mapping(loaded)
        if not document:
            raise CheckError(CHECK, _where(path), "version file is not a JSON object")
        supersedes = document.get("supersedes")
        if supersedes is not None and not isinstance(supersedes, str):
            raise CheckError(
                CHECK,
                _where(path),
                f"`supersedes` must be a version string or null, got {supersedes!r}",
            )
        found.append(
            SchemaVersion(
                concept=concept,
                version=path.stem,
                order=order,
                path=path,
                supersedes=supersedes,
                document=document,
            )
        )
    found.sort(key=lambda v: v.order)
    return found


def current_version(versions: list[SchemaVersion]) -> SchemaVersion | None:
    """The version no other version present names in `supersedes`.

    Returns `None` when the answer is not unique, which covers a concept with no
    versions and one where a cycle leaves every version named by another.
    """
    named = {v.supersedes for v in versions if v.supersedes is not None}
    candidates = [v for v in versions if v.version not in named]
    if len(candidates) != 1:
        return None
    return candidates[0]


def check_runtime_schemas(report: Report) -> None:
    """Validate the eight runtime concepts and their version files."""
    for concept in paths.RUNTIME_CONCEPTS:
        versions = load_versions(concept)

        if not versions:
            report.add(
                CHECK, f"schemas/{concept}/", f"concept `{concept}` has no schema"
            )
            continue

        for version in versions:
            for field in ("$id", "title", "schema_version"):
                if not version.document.get(field):
                    report.add(
                        CHECK,
                        version.relative,
                        f"schema is missing `{field}`",
                    )
            declared = version.document.get("schema_version")
            if declared is not None and declared != version.version:
                report.add(
                    CHECK,
                    version.relative,
                    f"`schema_version` is {declared!r} but the filename says "
                    f"{version.version!r}; the filename is the version",
                )

        # Succession is a step, not a chain and not a fork: every version but the
        # oldest names its immediate predecessor, and no two versions name the
        # same one. A dangling reference fails here too, because a version that
        # is not in the list cannot be an immediate predecessor.
        superseded_by: dict[str, list[str]] = {}
        for index, version in enumerate(versions):
            expected = None if index == 0 else versions[index - 1].version
            if version.supersedes != expected:
                report.add(
                    CHECK,
                    version.relative,
                    f"`supersedes` is {version.supersedes!r} but the immediately "
                    f"preceding version of `{concept}` is {expected!r}; succession "
                    "is a step from the version before it, not a chain or a fork",
                )
            if version.supersedes is not None:
                superseded_by.setdefault(version.supersedes, []).append(version.version)

        for target, successors in superseded_by.items():
            if len(successors) > 1:
                report.add(
                    CHECK,
                    f"schemas/{concept}/",
                    f"version {target!r} is superseded by more than one version: "
                    f"{sorted(successors)}",
                )

        current = current_version(versions)
        if current is None:
            # Name the versions rather than the count. The requirement's failing
            # scenario is "names the concept and both versions", and a diagnostic
            # that said "no single current one" would leave a reader to work out
            # which two by hand -- which is exactly the work a diagnostic exists
            # to do.
            named = {v.supersedes for v in versions if v.supersedes is not None}
            candidates = [v.version for v in versions if v.version not in named]
            report.add(
                CHECK,
                f"schemas/{concept}/",
                f"`{concept}` has no single current version: "
                f"{sorted(candidates)} are each unnamed by another version's "
                f"`supersedes`, out of {[v.version for v in versions]}; exactly "
                "one version must be current",
            )


def check_immutability(report: Report) -> None:
    """A published version file must equal its content at the commit that added it.

    `schemas/catalog/` is out of scope: it describes this repository's own
    intermediate artifacts, nothing outside consumes it, and freezing it would
    turn every later task that adds a field to a catalog file into a version
    bump.

    Absence counts as a change. A content comparison has nothing to compare
    against a deleted file, so without this clause "a superseded version is
    retained" would be unenforceable, and deleting a version would be a way to
    edit history that the check could not see. The candidate set for that clause
    comes from git, not from the `supersedes` links: a deleted *current* version
    is named by nobody and would otherwise go unreported.

    A root with no history fails rather than passing quietly. Without a
    repository there is no commit to compare any version against, and a check
    that skipped every file would report success on a tree it never read --
    which is the one outcome the requirement names as unacceptable.
    """
    if not vcs.is_repository():
        report.add(
            IMMUTABILITY_CHECK,
            paths.ROOT.as_posix(),
            "the project root has no git history; no published schema version "
            "can be compared against the commit that introduced it",
        )
        return

    for concept in paths.RUNTIME_CONCEPTS:
        directory = paths.SCHEMAS / concept
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            relative = path.relative_to(paths.ROOT).as_posix()
            commit = vcs.introducing_commit(relative)
            if commit is None:
                # Not committed yet. Nothing to compare against, and this is not
                # a failure: the file may be about to be added in this very
                # commit. The check has teeth once the commit exists.
                continue
            original = vcs.file_at(commit, relative)
            if original is None:
                report.add(
                    IMMUTABILITY_CHECK,
                    relative,
                    f"committed at {commit[:8]} but unreadable from git",
                )
                continue
            present = read_text(path)
            if present != original:
                report.add(
                    IMMUTABILITY_CHECK,
                    relative,
                    f"content differs from the version introduced at "
                    f"{commit[:8]}; a published schema version is immutable and a "
                    "change must be published as a new version file",
                )

    # A version named by a present version's `supersedes` must itself be present.
    for concept in paths.RUNTIME_CONCEPTS:
        versions = load_versions(concept)
        present = {v.version for v in versions}
        for version in versions:
            if version.supersedes is not None and version.supersedes not in present:
                report.add(
                    IMMUTABILITY_CHECK,
                    f"schemas/{concept}/{version.supersedes}.json",
                    f"named in the `supersedes` of {version.version} but absent "
                    "from the tree; a superseded version is retained, and absence "
                    "is a change to a frozen file",
                )

    # Absence is a change, and the candidate set has to come from history.
    #
    # Neither of the loops above can see a deleted *current* version. The content
    # loop globs the working tree, so a file that is gone is never a candidate;
    # the `supersedes` loop only reaches versions some present version names, and
    # the version nothing supersedes is named by nobody. Delete the tip of a
    # succession and both loops pass while a published version has silently been
    # un-published -- which is the one edit a content comparison cannot see, and
    # the only reason the absence clause exists at all.
    #
    # So the question is asked of git instead: of every path this repository has
    # ever recorded as added under a concept directory, which are no longer
    # there? `introduced_paths` keeps a path that was later deleted, which is
    # exactly the finding, and returns nothing on a tree with no history -- a
    # case the guard above has already failed on.
    for concept in paths.RUNTIME_CONCEPTS:
        prefix = f"schemas/{concept}/"
        for relative in sorted(set(vcs.introduced_paths(prefix))):
            if not relative.endswith(".json"):
                continue
            if not (paths.ROOT / relative).is_file():
                report.add(
                    IMMUTABILITY_CHECK,
                    relative,
                    "was added to this repository and is now absent; a published "
                    "schema version is retained, and absence is a change to a "
                    "frozen file",
                )
