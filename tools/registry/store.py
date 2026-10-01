"""The store: browse the index, install a pack by digest, gate an update.

This is the consumer end of the registry, and the one place a policy the
publishing side assumes is actually enforced. Three of its rules matter:

- **Install by digest.** A pointer names a digest and the store verifies the
  manifest against it before the engine ever sees the pack. A pointer whose
  digest does not match is refused by name, which is what makes the pointer a
  pin rather than a note.
- **A revoked pack is refused.** The revocation list is read beside the index, so
  a pack withdrawn after it was published cannot be installed by a caller who
  still has the index entry.
- **An update that widens a grant is gated.** A new version whose permissions
  are not a subset of the installed one needs the caller to have accepted the
  change. The diff is `openhouse.pack_verbs.diff_permissions`, the same verb CI
  runs and the CLI exposes, so the three cannot disagree about what changed.

The index is read from disk and never fetched, which is the *cached offline
index* the phase asks for: a household that has generated the registry once can
browse and install with the network down, and an update is a regeneration rather
than a call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engine.install import digest as pack_digest
from openhouse import pack_verbs, packs
from tools.catalog import paths
from tools.catalog.narrow import as_mapping

from .errors import RegistryError
from .index import Index, IndexEntry, Revocation, load_index, load_revoked
from .pointer import SELF_REPO, Pointer

if TYPE_CHECKING:
    from openhouse.facade import OpenHouse

__all__ = ["Resolved", "Store"]


@dataclass(frozen=True, slots=True)
class Resolved:
    """An index entry and the manifest on this machine it points at."""

    entry: IndexEntry
    manifest: Path

    @property
    def pointer(self) -> Pointer:
        """The entry's pointer, so a caller need not reach through `entry`."""
        return self.entry.pointer


class Store:
    """The index and the revocation list, and the installs they allow."""

    def __init__(
        self,
        root: Path,
        index: Index,
        revocations: tuple[Revocation, ...],
        *,
        checkouts: Mapping[str, Path] | None = None,
    ) -> None:
        self.root = root
        self.index = index
        self.revocations = revocations
        self.checkouts: Mapping[str, Path] = {} if checkouts is None else checkouts

    @classmethod
    def open(
        cls, registry_root: Path, *, checkouts: Mapping[str, Path] | None = None
    ) -> Store:
        """Open the store over a registry's committed index, with no network."""
        return cls(
            registry_root,
            load_index(registry_root),
            load_revoked(registry_root),
            checkouts=checkouts,
        )

    def browse(self) -> tuple[IndexEntry, ...]:
        """Every published entry, in the index's own order."""
        return self.index.entries

    def find(self, name: str, version: str | None = None) -> IndexEntry | None:
        """The entry for `name`, at `version` or at its highest published one."""
        return self.index.find(name, version)

    def revocation(self, entry: IndexEntry) -> Revocation | None:
        """The revocation covering `entry`, when one does."""
        for revocation in self.revocations:
            if revocation.revokes(entry.pointer):
                return revocation
        return None

    def resolve(
        self, entry: IndexEntry, checkouts: Mapping[str, Path] | None = None
    ) -> Path:
        """The manifest on this machine that `entry` points at.

        A pointer names its source by `repo`, and the store maps that name to a
        checkout. The one reserved value `.` is the repository that hosts the
        registry, which is how the project's own packs resolve without a second
        checkout; an absolute local directory resolves to itself, which is what
        lets a publisher check their own pack before it is merged. Anything else
        has to be named in `checkouts`, because a store that guessed would be a
        store that fetched.
        """
        mapping = self.checkouts if checkouts is None else checkouts
        pointer = entry.pointer
        if pointer.repo in mapping:
            base = mapping[pointer.repo]
        elif pointer.repo == SELF_REPO:
            base = paths.ROOT
        elif Path(pointer.repo).is_dir():
            base = Path(pointer.repo)
        else:
            raise RegistryError(
                pointer.repo,
                f"is not a checkout this store knows, so {pointer.name} "
                f"{pointer.version} cannot be resolved",
            )
        return base / pointer.path

    def verify(self, entry: IndexEntry, manifest: Path) -> None:
        """Refuse a manifest whose digest is not the one the pointer pins."""
        document = packs.load_manifest(manifest)
        actual = pack_digest(document)
        if actual != entry.pointer.sha256:
            raise RegistryError(
                manifest.as_posix(),
                f"its digest {actual!r} is not the pinned {entry.pointer.sha256!r}, "
                f"so {entry.pointer.name} {entry.pointer.version} has been changed "
                "since it was published",
            )

    def resolve_verified(
        self, entry: IndexEntry, checkouts: Mapping[str, Path] | None = None
    ) -> Resolved:
        """Resolve an entry, refuse it if revoked, and pin it by digest."""
        revocation = self.revocation(entry)
        if revocation is not None:
            raise RegistryError(
                f"{entry.pointer.name} {entry.pointer.version}",
                f"is revoked: {revocation.reason}",
            )
        manifest = self.resolve(entry, checkouts)
        self.verify(entry, manifest)
        return Resolved(entry=entry, manifest=manifest)

    def install(
        self,
        session: OpenHouse,
        name: str,
        *,
        version: str | None = None,
        checkouts: Mapping[str, Path] | None = None,
    ) -> Mapping[str, object]:
        """Install the published `name`, at `version` or at the current release."""
        entry = self.find(name, version)
        if entry is None:
            wanted = name if version is None else f"{name} {version}"
            raise RegistryError(wanted, "is not published in this registry")
        resolved = self.resolve_verified(entry, checkouts)
        result = session.install_pack(str(resolved.manifest))
        return {
            **result,
            "tier": entry.pointer.tier,
            "abandoned": entry.pointer.abandoned,
            "pinned": entry.pointer.sha256,
        }

    def update(
        self,
        session: OpenHouse,
        name: str,
        *,
        checkouts: Mapping[str, Path] | None = None,
        accept_permission_changes: bool = False,
    ) -> Mapping[str, object]:
        """Install the newest published `name`, gated on what it gains.

        The installed version is read from the session's snapshot rather than
        remembered here, because the snapshot is what the engine will restore
        and a store that kept its own idea of what was installed would be a
        second source of truth. When the new version declares a permission the
        installed one did not, the update is refused unless the caller accepted
        the change -- that is the gate the phase exists for, and its message
        names every gained service so a person can decide.
        """
        entry = self.find(name)
        if entry is None:
            raise RegistryError(name, "is not published in this registry")
        current = _installed_version(session, name)
        if current == entry.pointer.version:
            return {"pack": name, "updated": False, "version": current}
        gained = _gained(session, name, entry, self, checkouts)
        if gained and not accept_permission_changes:
            raise RegistryError(
                f"{name} {entry.pointer.version}",
                f"adds the permission(s) {sorted(gained)} over the installed "
                f"{current}; re-run with the change accepted to update",
            )
        result = self.install(
            session, name, version=entry.pointer.version, checkouts=checkouts
        )
        return {**result, "updated": True, "gained": tuple(sorted(gained))}


def _installed_version(session: OpenHouse, name: str) -> str | None:
    """The installed version of `name`, as the session's own snapshot reports it."""
    state = as_mapping(session.snapshot().get("engine_state"))
    installed = as_mapping(state.get("installed_packs"))
    record = as_mapping(installed.get(name))
    version = record.get("version")
    return version if isinstance(version, str) else None


def _gained(
    session: OpenHouse,
    name: str,
    entry: IndexEntry,
    store: Store,
    checkouts: Mapping[str, Path] | None,
) -> frozenset[str]:
    """The permissions `entry` adds over the version the session has installed.

    When the previous version is not itself a published entry, there is nothing
    to diff against and the gain is reported as empty: the alternative -- every
    permission of the new version -- would refuse every update of a pack the
    household installed from a file, which is not what the gate is for.
    """
    current = _installed_version(session, name)
    if current is None:
        return frozenset()
    previous = store.find(name, current)
    if previous is None:
        return frozenset()
    before = store.resolve(previous, checkouts)
    after = store.resolve(entry, checkouts)
    return frozenset(
        gain.service for gain in pack_verbs.diff_permissions(before, after).gained
    )
