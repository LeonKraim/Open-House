"""The units an installed pack contributes to a **live** engine.

`openhouse/facade.py` has built real `DeclaredBehaviour` units from a manifest
since Phase 2, and the live path never did: `build_live_house` composed
`default_behaviours()` -- the four hand-written units -- and passed the installed
set to the engine as a *record* only. So a pack installed through the panel was
stored, listed, flagged and enabled, and evaluated by nothing. `docs/pack-author-
guide.md` states the consequence in as many words: "installing a pack does not
make a live house actuate".

This module is the missing half. It resolves each installed pack back to the
manifest the registry published, reads the behaviour clauses out of it, and
returns the units the engine should evaluate beside its own four. It lives in
`ha_adapter/` rather than in `engine/` because it reads the *registry*
(`tools.registry`), and the engine is pure: `engine/` may not know what a
catalog is, only what a vocabulary is
(`specs/architecture-invariants/spec.md`).

**Resolved from the registry rather than remembered on the record.** The
installed record carries the pack's name, version, digest, bound slots and
behaviour *ids* -- deliberately, because the record is a house's memory of what a
person chose and a copy of the manifest would be a second source of truth for
the same file. The manifest is re-read here from the same index the panel
installs *from*, which is the one property that makes a restart restore the same
atoms a person installed rather than an older copy of them.

**A pack that cannot be resolved contributes nothing, and says so by its
absence.** A name in the installed set with no row in the index, or a row whose
manifest will not load, is skipped rather than raised: a checkout whose registry
was pruned must still boot a house, and the visible symptom -- the module lists
its behaviours and none of them can be evaluated -- is the honest one. Raising
here would take the whole integration down over one stale name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from engine import manifest as pack_manifest
from engine.behaviours.declared import DeclaredBehaviour, declared_units
from engine.declared_slots import grow_vocabulary
from engine.install import InstalledSet
from engine.vocabulary import Vocabulary, load_service_states
from tools.registry.errors import RegistryError
from tools.registry.index import load_index

__all__ = ["installed_units", "with_declared_slots"]

#: Where the published set lives under a checkout root, matching
#: `ha_adapter.live_modules`' `REGISTRY` and `tools.registry.layout`.
REGISTRY = "registry"


def installed_units(
    root: Path, installed: InstalledSet | None, vocabulary: Vocabulary
) -> tuple[DeclaredBehaviour, ...]:
    """The declared units every installed pack contributes, in pack-name order.

    Order matters and is the pack's name ascending: the engine keys units by id
    and the decision log is read in evaluation order, so two runs over the same
    installed set have to visit the atoms in the same sequence or a replay stops
    being a replay.

    `None` is the empty set, because `build_live_house` is called with no
    installed argument on the path that has no session state yet, and "no packs"
    and "not asked" are the same answer here.
    """
    if installed is None or not installed.names:
        return ()
    wanted = set(installed.names)
    default_priority = vocabulary.pack_policy.default_priority
    service_states = load_service_states(root)
    found: list[DeclaredBehaviour] = []
    for name, document in _manifests(root, wanted):
        found.extend(
            declared_units(
                name,
                document,
                default_priority=default_priority,
                service_states=service_states,
            )
        )
    return tuple(found)


def with_declared_slots(
    root: Path, installed: InstalledSet | None, vocabulary: Vocabulary
) -> Vocabulary:
    """`vocabulary` plus every device the installed packs declare of their own.

    **What is this layer's half.** The installed set names packs and nothing
    more; a manifest is a file, and finding the file a pack's name points at is
    the registry's job, which is what makes this function a member of
    `ha_adapter/` rather than of `engine/` -- the engine may not know what a
    registry is (`specs/architecture-invariants/spec.md`). The merge itself is
    `engine.declared_slots.grow_vocabulary`, and it is shared rather than
    reimplemented here because the simulator installs packs too: a session
    driven through `openhouse/` grows its house's vocabulary by the same rule,
    and two implementations of "what does a declaration add" would be two
    answers to one question.

    **The order is pack-name ascending.** Two installed packs may declare the
    same *new* name, and then one of them has to be the device; reading them in
    pack-name order means two runs over the same installed set add the same
    slot, which is `installed_units`' ordering rule and here for its reason -- a
    replay has to visit the same house twice.

    `None` or an empty set returns `vocabulary` unchanged, so the path that builds
    a house before any session state exists costs nothing.
    """
    if installed is None or not installed.names:
        return vocabulary
    manifests = sorted(_manifests(root, set(installed.names)), key=lambda pair: pair[0])
    return grow_vocabulary(vocabulary, manifests)


def _manifests(
    root: Path, wanted: set[str]
) -> Iterable[tuple[str, Mapping[str, object]]]:
    """Each wanted pack's name and manifest document, skipping what will not load.

    The index is read once and the manifests are loaded through
    `engine.manifest.load_manifest`, which is the loader the installer and the
    registry checks use -- a second YAML reader here would be a second reading of
    what a manifest is, free to disagree with the one that validated it.
    """
    try:
        index = load_index(root / REGISTRY)
    except RegistryError:
        return
    for entry in index.entries:
        pointer = entry.pointer
        if pointer.name not in wanted:
            continue
        try:
            manifest = pack_manifest.load_manifest(root / pointer.path)
        except (OSError, pack_manifest.MalformedManifestError):
            continue
        yield pointer.name, manifest.document
