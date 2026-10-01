"""The generator: pointer files in, `index.json` and `revoked.json` out.

The index is a *view* of the pointer tree and never a source of truth. A
publisher adds a pointer file; the generator reads every pointer under
`pointers/` and writes the flat list the store loads, so the two can never
disagree about which packs exist -- and CI re-runs the generator and fails when
the committed files differ from its output, which turns a forgotten regeneration
into a red build rather than a store that silently cannot see a pack.

`revoked.json` is generated the same way from `revocations.yaml`. Keeping the
hand-edited list and the machine-read file separate is deliberate: the list
carries comments explaining each revocation, and JSON carries none, so a single
file would make the explanation the first casualty of a regeneration.

Determinism is a requirement rather than a nicety here, because the check above
is "is the committed file byte-identical to a fresh generation" -- a generator
whose output depends on directory order would fail its own check on a different
machine. Every list is sorted and every mapping is written with sorted keys.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml

from tools.catalog.narrow import as_sequence

from .errors import RegistryError
from .layout import (
    INDEX_FILENAME,
    POINTERS_DIRECTORY,
    REVOCATIONS_FILENAME,
    REVOKED_FILENAME,
)
from .pointer import Pointer, load_pointer
from .tiers import load_tiers

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "INDEX_SCHEMA_VERSION",
    "Generated",
    "Index",
    "IndexEntry",
    "Revocation",
    "build_index",
    "generate",
    "load_index",
    "load_revoked",
    "pointer_files",
    "verify_current",
]

#: The index format's own version, which is not the registry's and not a pack's.
INDEX_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Revocation:
    """One entry of the revocation list.

    `version` and `sha256` are optional because they narrow the revocation, and
    a revocation that names neither revokes every version of the named pack --
    which is the right entry for a pack its author withdrew, while a version
    naming one release is the right entry for a release that shipped a defect.
    """

    name: str
    reason: str
    version: str | None = None
    sha256: str | None = None

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {"name": self.name, "reason": self.reason}
        if self.version is not None:
            document["version"] = self.version
        if self.sha256 is not None:
            document["sha256"] = self.sha256
        return document

    def revokes(self, pointer: Pointer) -> bool:
        """Whether this entry covers `pointer`.

        Every field the entry states has to match, so a version-scoped entry
        does not revoke a sibling release and a digest-scoped one does not
        revoke a rebuilt manifest that merely shares the version number.
        """
        if pointer.name != self.name:
            return False
        if self.version is not None and pointer.version != self.version:
            return False
        return self.sha256 is None or pointer.sha256 == self.sha256


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One pointer as the index carries it: the pinned facts, and where it sat."""

    pointer: Pointer
    location: str

    def to_document(self) -> dict[str, object]:
        return {**self.pointer.to_document(), "pointer": self.location}


@dataclass(frozen=True, slots=True)
class Index:
    """Every published pointer, in one deterministic order."""

    entries: tuple[IndexEntry, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "entries": [entry.to_document() for entry in self.entries],
        }

    def find(self, name: str, version: str | None = None) -> IndexEntry | None:
        """The entry for `name` at `version`, or the highest one when unversioned.

        Highest by version ordering, which is the semver order the pack manifest
        already promises: a caller asking for a name without a version is asking
        for the current release, and returning the first one in the file would
        make "current" a fact about the file's layout.
        """
        candidates = [entry for entry in self.entries if entry.pointer.name == name]
        if version is not None:
            for entry in candidates:
                if entry.pointer.version == version:
                    return entry
            return None
        if not candidates:
            return None
        return max(candidates, key=lambda entry: _version_key(entry.pointer.version))

    def versions(self, name: str) -> tuple[str, ...]:
        """Every published version of `name`, in ascending order."""
        return tuple(
            sorted(
                (
                    entry.pointer.version
                    for entry in self.entries
                    if entry.pointer.name == name
                ),
                key=_version_key,
            )
        )

    def names(self) -> tuple[str, ...]:
        """Every published pack name, once each, in sorted order."""
        return tuple(sorted({entry.pointer.name for entry in self.entries}))


@dataclass(frozen=True, slots=True)
class Generated:
    """What a generation produced, whether or not it wrote anything."""

    index: Index
    revocations: tuple[Revocation, ...]
    index_path: Path
    revoked_path: Path
    index_document: Mapping[str, object]
    revoked_document: Mapping[str, object]


def pointer_files(registry_root: Path) -> tuple[Path, ...]:
    """Every pointer file under `pointers/`, sorted by path.

    Sorted rather than left in directory order, so two generations over one tree
    produce the same bytes -- which is the property `verify_current` tests.
    """
    directory = registry_root / POINTERS_DIRECTORY
    if not directory.is_dir():
        return ()
    return tuple(
        path
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    )


def build_index(registry_root: Path) -> Index:
    """The index a fresh generation of `registry_root` produces.

    A pointer file whose directory names a tier that does not publish is read
    and left out of the index, which is how a `local` pointer can sit in the
    tree for a check to see without ever being offered to a store. Two pointers
    claiming one name and version are refused: one of them would win by file
    order, and which one is not a fact the tree states.
    """
    tiers = load_tiers(registry_root)
    entries: list[IndexEntry] = []
    seen: dict[tuple[str, str], str] = {}
    for path in pointer_files(registry_root):
        location = path.relative_to(registry_root).as_posix()
        pointer = load_pointer(path)
        if pointer.tier not in tiers:
            raise RegistryError(location, f"names the unknown tier {pointer.tier!r}")
        if pointer.key in seen:
            raise RegistryError(
                location,
                f"claims {pointer.name} {pointer.version}, already published by {seen[pointer.key]}",
            )
        seen[pointer.key] = location
        if not tiers[pointer.tier].publishes:
            continue
        entries.append(IndexEntry(pointer=pointer, location=location))
    entries.sort(
        key=lambda entry: (entry.pointer.name, _version_key(entry.pointer.version))
    )
    return Index(entries=tuple(entries))


def load_revocations(registry_root: Path) -> tuple[Revocation, ...]:
    """Every revocation `revocations.yaml` declares, in file order."""
    path = registry_root / REVOCATIONS_FILENAME
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RegistryError(path.as_posix(), f"could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RegistryError(path.as_posix(), "is not a YAML mapping")
    mapping = cast("Mapping[str, object]", loaded)
    if not isinstance(mapping.get("revocations"), list):
        raise RegistryError(path.as_posix(), "does not declare `revocations` as a list")
    revocations: list[Revocation] = []
    for row in as_sequence(mapping.get("revocations")):
        if not isinstance(row, dict):
            raise RegistryError(
                path.as_posix(), "carries a revocation that is not a mapping"
            )
        fields = cast("Mapping[str, object]", row)
        revocations.append(
            Revocation(
                name=_required(path, fields, "name"),
                reason=_required(path, fields, "reason"),
                version=_optional(path, fields, "version"),
                sha256=_optional(path, fields, "sha256"),
            )
        )
    return tuple(revocations)


def generate(registry_root: Path, *, write: bool = True) -> Generated:
    """Build the index and the revocation file, and write them unless told not to.

    `write=False` is how the freshness check is built: it produces exactly the
    documents a write would and touches nothing, so comparing them against what
    is committed is comparing like with like rather than comparing a file to a
    re-serialisation that might differ for a formatting reason.
    """
    index = build_index(registry_root)
    revocations = load_revocations(registry_root)
    index_document = index.to_document()
    revoked_document: dict[str, object] = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "revocations": [revocation.to_document() for revocation in revocations],
    }
    index_path = registry_root / INDEX_FILENAME
    revoked_path = registry_root / REVOKED_FILENAME
    if write:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(_json(index_document), encoding="utf-8", newline="")
        revoked_path.write_text(_json(revoked_document), encoding="utf-8", newline="")
    return Generated(
        index=index,
        revocations=revocations,
        index_path=index_path,
        revoked_path=revoked_path,
        index_document=index_document,
        revoked_document=revoked_document,
    )


def verify_current(registry_root: Path) -> tuple[str, ...]:
    """Every way the committed generated files differ from a fresh generation.

    Byte comparison rather than a parsed one, because the claim being checked is
    that the committed file is what the generator writes. A parsed comparison
    would pass for a file a human reformatted, and the next generation would
    then show a diff nobody made.
    """
    generated = generate(registry_root, write=False)
    findings: list[str] = []
    for path, document in (
        (generated.index_path, generated.index_document),
        (generated.revoked_path, generated.revoked_document),
    ):
        expected = _json(document)
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(
                f"{path.name} is missing; run `python -m tools.registry.cli generate`"
            )
            continue
        if actual != expected:
            findings.append(
                f"{path.name} differs from a fresh generation; run "
                "`python -m tools.registry.cli generate` and commit the result"
            )
    return tuple(findings)


def load_index(registry_root: Path) -> Index:
    """The committed index, read back without regenerating anything."""
    return _read_index(registry_root / INDEX_FILENAME)


def load_revoked(registry_root: Path) -> tuple[Revocation, ...]:
    """The committed revocation file, read back without regenerating anything."""
    path = registry_root / REVOKED_FILENAME
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(path.as_posix(), f"could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RegistryError(path.as_posix(), "is not a JSON object")
    mapping = cast("Mapping[str, object]", loaded)
    if not isinstance(mapping.get("revocations"), list):
        raise RegistryError(path.as_posix(), "does not declare `revocations` as a list")
    return tuple(
        _revocation(path, row) for row in as_sequence(mapping.get("revocations"))
    )


def _read_index(path: Path) -> Index:
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(path.as_posix(), f"could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RegistryError(path.as_posix(), "is not a JSON object")
    mapping = cast("Mapping[str, object]", loaded)
    if not isinstance(mapping.get("entries"), list):
        raise RegistryError(path.as_posix(), "does not declare `entries` as a list")
    entries: list[IndexEntry] = []
    for row in as_sequence(mapping.get("entries")):
        if not isinstance(row, dict):
            raise RegistryError(
                path.as_posix(), "carries an entry that is not an object"
            )
        fields = cast("Mapping[str, object]", row)
        location = fields.get("pointer")
        if not isinstance(location, str):
            raise RegistryError(path.as_posix(), "carries an entry with no `pointer`")
        document = {key: value for key, value in fields.items() if key != "pointer"}
        entries.append(IndexEntry(pointer=_pointer(path, document), location=location))
    return Index(entries=tuple(entries))


def _pointer(path: Path, document: Mapping[str, object]) -> Pointer:
    """A pointer built from an index entry, failing by naming what is missing."""
    return Pointer(
        name=_required(path, document, "name"),
        version=_required(path, document, "version"),
        repo=_required(path, document, "repo"),
        commit=_required(path, document, "commit"),
        path=_required(path, document, "path"),
        sha256=_required(path, document, "sha256"),
        tier=_required(path, document, "tier"),
        abandoned=document.get("abandoned", False) is True,
    )


def _revocation(path: Path, row: object) -> Revocation:
    if not isinstance(row, dict):
        raise RegistryError(
            path.as_posix(), "carries a revocation that is not an object"
        )
    fields = cast("Mapping[str, object]", row)
    return Revocation(
        name=_required(path, fields, "name"),
        reason=_required(path, fields, "reason"),
        version=_optional(path, fields, "version"),
        sha256=_optional(path, fields, "sha256"),
    )


def _required(path: Path, document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise RegistryError(path.as_posix(), f"carries no usable `{field}`")
    return value


def _optional(path: Path, document: Mapping[str, object], field: str) -> str | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise RegistryError(path.as_posix(), f"carries no usable `{field}`")
    return value


def _version_key(version: str) -> tuple[int, int, int, str]:
    """A comparable key for a version string, tolerant of a suffix.

    Versions are `MAJOR.MINOR.PATCH` and may run on to a pre-release, which is
    not comparable as text. The numeric part decides the order and the remainder
    breaks ties, so `1.10.0` sorts after `1.9.0` rather than before it.
    """
    core, _, remainder = version.partition("-")
    numbers = [int(part) for part in core.split(".") if part.isdigit()]
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2], remainder)


def _json(document: Mapping[str, object]) -> str:
    """One document as stable JSON text: same content, same string, same bytes."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
