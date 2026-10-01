"""The install lifecycle: what a house holds, and what changes when a pack arrives.

`pack-install` states the whole of this module. Phase 1 shipped `install_pack` at
a scope it stated in full -- validate the manifest, resolve `requires_slots`, and
nothing else -- and named this phase as the one that completes it. What completes
it is a *set*: the packs a house holds, each with the facts that explain why it is
there, and the four acts that change the set rather than the one that adds to it.

Five things about this module are decisions rather than mechanics:

- **The installed set is a value, and every act returns a new one.** Nothing here
  mutates. `install`, `uninstall` and a version change each take the set they were
  given and return the set that replaces it, raising before they return anything
  if a check refuses. That is what makes `A failed install leaves the house
  unchanged` structural rather than a promise: a refusal has no partially-built
  value to leak, because the caller is the one that decides to keep the result.
- **Resolution consults the set and never a directory.** A dependency is a name
  and a range, and the only thing that can satisfy it is a pack this house holds.
  A pack that installs here is therefore installable wherever the same packs are
  installed, which is the property the requirement is written around; a resolver
  that read the pack directory would make "installs on my machine" true again.
- **The record carries the digest of the manifest that was validated.** The
  version is not enough: a manifest edited without a version bump is exactly the
  case a digest catches and a version cannot, and it is the case that matters,
  because the file on disk is what will be re-read the next time the house
  restarts.
- **A cycle is refused as a cycle and not as an unsatisfied dependency.** The
  difference is the remedy: an unsatisfied dependency is a pack to install and a
  cycle is a pair of manifests to rewrite, and a resolver that reported the second
  as the first would send an author to the wrong file. The check runs over the
  graph the arriving pack would *produce*, before the dependency check, so a
  refusal names the loop rather than one of its edges.
- **The re-check after a version change is the dependency check, run again over
  the resulting set.** A downgrade that breaks another pack is not a special case
  of anything: it is the same question -- does every installed pack's dependency
  resolve? -- asked about the set the change would leave behind. One check
  answering both is why there is no `breaking_downgrade` rule to get wrong.

Nothing here reads a file. The manifest's clauses arrive as an `Arrival`, the
flags arrive from `engine/sandbox.py`, and the bound entities arrive from
`engine/binding.py`; this module decides about the set. That is what lets it run
unchanged in Phase 4, where the house is a real adapter and the manifest came off
a store rather than a checkout.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from engine import semver

__all__ = [
    "REASONS",
    "Arrival",
    "InstallFailure",
    "InstallRefusedError",
    "InstalledPack",
    "InstalledSet",
    "Reference",
    "digest",
    "install",
    "uninstall",
]

#: Every reason a refusal *this module* carries. The reasons a caller can see are
#: the union of three sets, because an install is refused by three layers and each
#: one's reason is its own: `engine/manifest.py`'s twelve, `engine/sandbox.py`'s
#: sixteen, and these six. Filing another module's reason here would be a second
#: name for a distinction that already has one, and an install report that
#: flattened them would tell an author "the pack is bad" when the truth is which
#: of three kinds of bad it is.
REASONS: tuple[str, ...] = (
    "unsatisfied_dependency",
    "dependency_cycle",
    "conflict",
    "not_installed",
    "dependent_installed",
    "version_change_breaks_dependency",
)


class InstallError(Exception):
    """Base for the failures this module defines."""


@dataclass(frozen=True, slots=True)
class InstallFailure:
    """One reason an act on the installed set was refused.

    `pack` is the pack the failure is *about* and `other` is the second party
    where there is one -- the installed pack that declares the conflict, the
    dependent that blocks an uninstall. A conflict has two sides and a report
    that named one of them would leave the reader to find the other.
    """

    reason: str
    pack: str
    message: str
    other: str | None = None


class InstallRefusedError(InstallError):
    """An act refused, carrying every failure that refused it.

    Every one and not the first: a pack with two unsatisfied dependencies is one
    edit away from being installable, and a report that named one of them at a
    time would make the author install and retry twice. The failures are in the
    order the checks ran, which is fixed, so two refusals of the same manifest
    read the same way.
    """

    def __init__(self, failures: Iterable[InstallFailure]) -> None:
        self.failures = tuple(failures)
        super().__init__("; ".join(failure.message for failure in self.failures))

    @property
    def reasons(self) -> tuple[str, ...]:
        """The failures' reasons, in order, one per failure."""
        return tuple(failure.reason for failure in self.failures)


@dataclass(frozen=True, slots=True)
class Reference:
    """A pack named with a semver range: what a dependency and a conflict both are.

    One type for both because the spec makes them the same shape -- `dependencies`
    and `conflicts` are each "a name and a semver range" -- and the difference is
    entirely in how the range is read: a dependency needs it satisfied, a conflict
    needs it unsatisfied.
    """

    name: str
    range: str

    def admits(self, version: str) -> bool:
        """Whether `version` falls in this reference's range."""
        return semver.satisfies(version, self.range)


@dataclass(frozen=True, slots=True)
class Arrival:
    """What an arriving pack declares, read off the manifest that was validated.

    A value rather than the manifest document, so this module cannot come to
    depend on the manifest format: the clauses it needs are unwrapped once, by
    the caller that already had the document, and a field this module never reads
    is a field that cannot drift under it.
    """

    name: str
    version: str
    digest: str
    dependencies: tuple[Reference, ...] = ()
    conflicts: tuple[Reference, ...] = ()


@dataclass(frozen=True, slots=True)
class InstalledPack:
    """One pack this house holds, and the facts that explain why.

    `slots` is a tuple of pairs rather than a mapping because a document has to
    round-trip it and the order a reader sees should be the manifest's, not a
    hash's. `dependencies` names the pack *and the version that satisfied it*,
    which is the difference between a record that says what was asked and one that
    says what answered -- and the second is what a person reads when a later
    uninstall is refused.
    """

    name: str
    version: str
    digest: str
    slots: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: The dependencies that satisfied this pack: the range it declared and the
    #: version that answered it. Both, because they are read at different times:
    #: the version is what a person reads when an uninstall is refused, and the
    #: range is what the re-check reads after a version change moves the pack
    #: that answered it. A record keeping only the version could not re-check
    #: anything, and one keeping only the range could not say what answered.
    dependencies: tuple[tuple[Reference, str], ...] = ()
    #: The conflicts the pack declared, kept because the check is symmetric: an
    #: installed pack's conflict has to be readable when a *later* pack arrives,
    #: and that is the only moment it is ever consulted.
    conflicts: tuple[Reference, ...] = ()
    #: The units this pack registered, by behaviour id. Stored rather than
    #: derived from the name, because a pack declares its behaviour ids and the
    #: engine does not get to guess them: uninstall has to remove exactly the
    #: units this pack contributed, and a restore has to be able to tell that a
    #: house rebuilt without them is not the house the snapshot was taken from.
    behaviours: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    #: The version this pack replaced in this house, if it replaced one. `None`
    #: for a first install, which is what makes "the record names the change" a
    #: fact a reader can see rather than an event nobody wrote down.
    replaced: str | None = None

    @property
    def change(self) -> str:
        """What the arriving act was: a first install, an upgrade, a downgrade."""
        if self.replaced is None:
            return "install"
        if semver.satisfies(self.replaced, f">{self.version}"):
            return "downgrade"
        if semver.satisfies(self.replaced, f"={self.version}"):
            return "reinstall"
        return "upgrade"

    def bound(self, slot: str) -> tuple[str, ...]:
        """The entities this pack's `slot` was bound to, or nothing."""
        for name, entities in self.slots:
            if name == slot:
                return entities
        return ()

    def matches(self, document: Mapping[str, object]) -> bool:
        """Whether `document` is the manifest this record was installed from.

        The digest's whole purpose, and the reason both fields exist: a version
        says a pack changed only when a person says so, so a manifest edited
        without a bump reads as the same version here and as a different document
        here too. The pair of answers is the report -- "the version says no change
        and the digest says otherwise" is a caller comparing the two, and this is
        the comparison rather than a rule the caller has to know to write.
        """
        return digest(document) == self.digest

    def to_document(self) -> dict[str, object]:
        """The record as a JSON-safe mapping, built deterministically."""
        return {
            "name": self.name,
            "version": self.version,
            "digest": self.digest,
            "slots": {slot: list(entities) for slot, entities in self.slots},
            "dependencies": [
                {"name": item.name, "range": item.range, "version": version}
                for item, version in self.dependencies
            ],
            "conflicts": [
                {"name": item.name, "range": item.range} for item in self.conflicts
            ],
            "behaviours": list(self.behaviours),
            "flags": list(self.flags),
            "replaced": self.replaced,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> InstalledPack:
        """Read a record back, failing rather than defaulting a missing field.

        A `slots` entry that came back as a bare string is refused rather than
        iterated, because iterating it would bind a slot to its characters -- a
        failure that would surface as a pack acting on entities named `l`, `i`
        and `g`.
        """
        replaced = document.get("replaced")
        return cls(
            name=_require_str(document, "name"),
            version=_require_str(document, "version"),
            digest=_require_str(document, "digest"),
            slots=tuple(
                (str(slot), _require_strings(entities, f"slots.{slot}"))
                for slot, entities in _require_mapping(document, "slots").items()
            ),
            dependencies=tuple(
                (
                    Reference(name=str(entry["name"]), range=str(entry["range"])),
                    str(entry["version"]),
                )
                for entry in _require_sequence(
                    document.get("dependencies", []), "dependencies"
                )
                if isinstance(entry, Mapping)
            ),
            conflicts=tuple(
                Reference(name=str(entry["name"]), range=str(entry["range"]))
                for entry in _require_sequence(
                    document.get("conflicts", []), "conflicts"
                )
            ),
            flags=_require_strings(document.get("flags", []), "flags"),
            behaviours=_require_strings(document.get("behaviours", []), "behaviours"),
            replaced=None if replaced is None else str(replaced),
        )


@dataclass(frozen=True, slots=True)
class InstalledSet:
    """The packs a house holds, keyed by name.

    Keyed by name and not by a pair, because a house holds one version of a pack:
    `Installing another version of a pack is a change to that pack` is the reason
    the mapping has no room for two entries of one name, and a second entry would
    be a state the rest of this module has no rule for.
    """

    packs: Mapping[str, InstalledPack] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.packs)

    def __contains__(self, name: object) -> bool:
        return name in self.packs

    @property
    def names(self) -> tuple[str, ...]:
        """The installed names, ascending, so a listing is the same twice."""
        return tuple(sorted(self.packs))

    def get(self, name: str) -> InstalledPack | None:
        """The pack of that name, or nothing."""
        return self.packs.get(name)

    def version_of(self, name: str) -> str | None:
        """The installed version of `name`, or nothing."""
        record = self.packs.get(name)
        return None if record is None else record.version

    def dependents(self, name: str) -> tuple[str, ...]:
        """Every installed pack that declares a dependency on `name`, ascending."""
        return tuple(
            sorted(
                other.name
                for other in self.packs.values()
                if other.name != name
                and any(item.name == name for item, _ in other.dependencies)
            )
        )

    def replace(self, record: InstalledPack) -> InstalledSet:
        """This set with `record` in it, at its name, replacing any version."""
        return InstalledSet({**self.packs, record.name: record})

    def remove(self, name: str) -> InstalledSet:
        """This set without `name`, or this set when it holds no such pack."""
        return InstalledSet(
            {other: record for other, record in self.packs.items() if other != name}
        )

    def to_document(self) -> dict[str, object]:
        """The set as a JSON-safe mapping, keyed by name."""
        return {name: self.packs[name].to_document() for name in self.names}

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> InstalledSet:
        """Read a set back, keying each record by its own name and checking it.

        The key and the record's `name` are compared rather than trusted: a
        document whose key and value disagree is one where a lookup by name would
        find a different pack than a listing shows.
        """
        packs: dict[str, InstalledPack] = {}
        for key, entry in document.items():
            record = InstalledPack.from_document(_as_mapping(entry, f"packs.{key}"))
            if record.name != key:
                raise InstallError(
                    f"the installed set is keyed by {key!r} but that entry names "
                    f"itself {record.name!r}"
                )
            packs[record.name] = record
        return cls(packs)


def digest(document: Mapping[str, object]) -> str:
    """The digest of a manifest document, over its canonical JSON form.

    Canonical rather than over the file's bytes, because two manifests that say
    the same thing are the same manifest: a reordered key or a changed indentation
    is not an edit to a pack, and a digest that changed for one would report a
    mismatch for a file nobody edited. The `sha256:` prefix names the function, so
    a stored digest can be compared against a future one rather than merely
    differing from it.
    """
    canonical = json.dumps(
        _json_safe(document), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def install(
    installed: InstalledSet,
    arrival: Arrival,
    *,
    slots: tuple[tuple[str, tuple[str, ...]], ...] = (),
    behaviours: tuple[str, ...] = (),
    flags: tuple[str, ...] = (),
) -> InstalledSet:
    """The set that results from `arrival` joining `installed`, or a refusal.

    The checks run in a fixed order -- cycle, dependencies, conflicts, the
    re-check of every installed pack's dependencies over the result -- and *all*
    of them that can refuse do, so one report carries every reason rather than
    the first. The order is observable only through which failure is listed
    first, never through what the house holds afterwards: nothing is built until
    every check has passed, and the refusal is raised before the new set exists.
    """
    failures = (
        _cycle_failures(installed, arrival)
        + _dependency_failures(installed, arrival)
        + _conflict_failures(installed, arrival)
    )
    if failures:
        raise InstallRefusedError(failures)

    record = InstalledPack(
        name=arrival.name,
        version=arrival.version,
        digest=arrival.digest,
        slots=slots,
        dependencies=tuple(
            (item, version)
            for item in arrival.dependencies
            if (version := installed.version_of(item.name)) is not None
        ),
        conflicts=arrival.conflicts,
        behaviours=behaviours,
        flags=flags,
        replaced=installed.version_of(arrival.name),
    )
    resulting = installed.replace(record)
    broken = _resulting_dependency_failures(resulting)
    if broken:
        raise InstallRefusedError(broken)
    return resulting


def uninstall(installed: InstalledSet, name: str) -> InstalledSet:
    """The set that results from removing `name`, or a refusal.

    Refused while another installed pack depends on it, naming the dependents: a
    house that removed the pack and left them unsatisfied would hold packs whose
    dependencies are statements about nothing, and the refusal is what keeps
    "installed" meaning "and everything it needs is here".

    Uninstalling is not disabling, and this is where the two part company: a
    disabled behaviour is a unit that is still registered, still in the record and
    still listed, and this removes all three. The two are separate operations
    because a person who disabled a pack's behaviour would otherwise discover that
    the way to get it back was to install it again.
    """
    record = installed.get(name)
    if record is None:
        raise InstallRefusedError(
            [
                InstallFailure(
                    reason="not_installed",
                    pack=name,
                    message=f"{name!r} is not installed in this house",
                )
            ]
        )
    dependents = installed.dependents(name)
    if dependents:
        raise InstallRefusedError(
            [
                InstallFailure(
                    reason="dependent_installed",
                    pack=name,
                    other=dependent,
                    message=(
                        f"{name!r} cannot be uninstalled: {dependent!r} depends on it"
                    ),
                )
                for dependent in dependents
            ]
        )
    return installed.remove(name)


def _cycle_failures(
    installed: InstalledSet, arrival: Arrival
) -> tuple[InstallFailure, ...]:
    """Whether the arriving pack's edges close a loop.

    Run before the dependency check because a cycle is a loop in the graph and an
    unsatisfied dependency is an edge to nothing, and the two are refused with
    different words because they are fixed in different files. The traversal
    starts from the arriving pack and walks the edges the resulting set would
    have; a back edge to a pack already on the path is the loop, and the failure
    names it from the point it closes so an author reads a chain rather than a
    set.
    """
    edges: dict[str, tuple[str, ...]] = {
        name: tuple(item.name for item, _ in record.dependencies)
        for name, record in installed.packs.items()
    }
    edges[arrival.name] = tuple(item.name for item in arrival.dependencies)

    path: list[str] = []
    seen: set[str] = set()

    def walk(name: str) -> tuple[str, ...] | None:
        if name in path:
            return tuple(path[path.index(name) :])
        if name in seen:
            return None
        seen.add(name)
        path.append(name)
        for edge in edges.get(name, ()):
            closed = walk(edge)
            if closed is not None:
                return closed
        path.pop()
        return None

    closed = walk(arrival.name)
    if closed is None:
        return ()
    chain = " -> ".join((*closed, closed[0]))
    return (
        InstallFailure(
            reason="dependency_cycle",
            pack=arrival.name,
            other=closed[0] if closed[0] != arrival.name else None,
            message=(
                f"{arrival.name!r} would join a dependency cycle: {chain}. A "
                "cycle cannot be satisfied by installing anything, because every "
                "pack in it waits for another"
            ),
        ),
    )


def _dependency_failures(
    installed: InstalledSet, arrival: Arrival
) -> tuple[InstallFailure, ...]:
    """Every dependency of the arriving pack that this house cannot satisfy."""
    failures: list[InstallFailure] = []
    for item in arrival.dependencies:
        version = installed.version_of(item.name)
        if version is None:
            failures.append(
                InstallFailure(
                    reason="unsatisfied_dependency",
                    pack=arrival.name,
                    other=item.name,
                    message=(
                        f"{arrival.name!r} requires {item.name} {item.range}, which "
                        "is not installed in this house"
                    ),
                )
            )
        elif not item.admits(version):
            failures.append(
                InstallFailure(
                    reason="unsatisfied_dependency",
                    pack=arrival.name,
                    other=item.name,
                    message=(
                        f"{arrival.name!r} requires {item.name} {item.range}, but "
                        f"this house has {item.name} {version}"
                    ),
                )
            )
    return tuple(failures)


def _conflict_failures(
    installed: InstalledSet, arrival: Arrival
) -> tuple[InstallFailure, ...]:
    """Every conflict between the arriving pack and the set, from both sides.

    A conflict is symmetric, so it is read from the arriving pack to each
    installed one *and* from each installed pack back to the arriving one. A
    check that read only one direction would make the refusal depend on which
    pack arrived second, which is the install-order dependence the exit criterion
    forbids -- and it would be a refusal a person could avoid by installing the
    other pack first, which is not a rule anyone can hold in their head.
    """
    failures: list[InstallFailure] = []
    for item in arrival.conflicts:
        version = installed.version_of(item.name)
        if version is not None and item.admits(version):
            failures.append(
                InstallFailure(
                    reason="conflict",
                    pack=arrival.name,
                    other=item.name,
                    message=(
                        f"{arrival.name!r} declares a conflict with {item.name} "
                        f"{item.range}, which this house has at {version}"
                    ),
                )
            )
    for name in installed.names:
        for item in installed.packs[name].conflicts:
            if item.name != arrival.name:
                continue
            if item.admits(arrival.version):
                failures.append(
                    InstallFailure(
                        reason="conflict",
                        pack=name,
                        other=arrival.name,
                        message=(
                            f"{name!r} declares a conflict with {item.name} "
                            f"{item.range}, which the arriving {arrival.name} "
                            f"{arrival.version} satisfies"
                        ),
                    )
                )
    return tuple(failures)


def _resulting_dependency_failures(
    installed: InstalledSet,
) -> tuple[InstallFailure, ...]:
    """Whether any installed pack's dependency stopped resolving after a change.

    This is the downgrade rule, and it is the dependency rule: a version change
    that leaves another pack's range unsatisfied is refused for the same reason
    an arrival with an unsatisfied range is, and saying so once is why there is no
    separate rule. A pack that depends on *itself* cannot appear here, because the
    manifest validator refuses a self-dependency before an `Arrival` exists.
    """
    failures: list[InstallFailure] = []
    for name in installed.names:
        record = installed.packs[name]
        for item, version in record.dependencies:
            if item.name == name:
                continue
            present = installed.version_of(item.name)
            if present is not None and item.admits(present):
                continue
            failures.append(
                InstallFailure(
                    reason="version_change_breaks_dependency",
                    pack=name,
                    other=item.name,
                    message=(
                        f"{name!r} requires {item.name} {item.range} and was "
                        f"satisfied by {version}, but this change leaves "
                        + (
                            "it uninstalled"
                            if present is None
                            else f"{item.name} {present}"
                        )
                    ),
                )
            )
    return tuple(failures)


def _json_safe(value: object, *, where: str = "<document>") -> object:
    """`value` in the JSON subset, refusing anything that cannot be written.

    A manifest is JSON-compatible by construction -- its schema is a JSON Schema
    and its clauses are strings, numbers and lists -- so a value outside the
    subset is a document that was never a manifest, and refusing it here names the
    path rather than letting `json.dumps` raise on an object it cannot describe.
    """
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, where=f"{where}/{key}")
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [
            _json_safe(item, where=f"{where}/{index}")
            for index, item in enumerate(value)
        ]
    raise InstallError(
        f"{where} holds a {type(value).__name__}, which cannot appear in a manifest"
    )


def _as_mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InstallError(f"{where} is a {type(value).__name__}, not a mapping")
    return {str(key): item for key, item in value.items()}


def _require_mapping(
    document: Mapping[str, object], field: str
) -> Mapping[str, object]:
    if field not in document:
        raise InstallError(f"the installed record has no {field!r}")
    return _as_mapping(document[field], field)


def _require_str(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise InstallError(f"the installed record's {field!r} is not a name")
    return value


def _require_strings(value: object, where: str) -> tuple[str, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, Iterable):
        raise InstallError(f"{where} is not a list of names")
    return tuple(str(item) for item in value)


def _require_sequence(value: object, where: str) -> Sequence[object]:
    if isinstance(value, str | bytes | Mapping) or not isinstance(value, Sequence):
        raise InstallError(f"{where} is not a list")
    return value
