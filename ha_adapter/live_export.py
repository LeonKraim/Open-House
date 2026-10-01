"""The live house's export, its import, and the Activity tab's projection.

Three of the panel's commands are answered here, and they are answered in one
module because they are one concern seen from three sides: the document a house
is, the document a house becomes, and the record of what a house decided. A live
session (`ha_adapter/live.py`) owns the wiring an engine is rebuilt from; this
module is what turns that wiring into a file and a file back into wiring, and
what turns the engine's ordinary vocabulary of dispositions into the five words
the Activity tab shows.

`openhouse/facade.py` is the template, and every operation here is its live
twin: `export_document` is `OpenHouse.export_backup`, `preview` is its
`dry_run_import` joined to its `relink_import`, `apply` is its `import_backup`
without the undo it cannot honestly promise, and `activity` plays the part of
its `get_decision_log`. The differences are the live path's and they are all
consequences of one fact -- a live house does not own its devices. The simulator
adds entities to an adapter when a house document is imported (`sim.fixtures`);
Home Assistant already holds its entities, and a backup that conjured a device
would be a backup that invented hardware. So `apply` rebinds and rebuilds, and
leaves the world alone.

**This module hands back documents, not result objects.** The facade's
operations return dataclasses its own callers read; the callers here are
websocket handlers that serialise straight to the panel, so nothing returns a
value the JSON encoder cannot take -- tuples, mappings, strings, numbers and
`None` -- and no engine dataclass crosses the boundary unprojected.

**Nothing here imports `homeassistant`.** The transport is the seam
(`ha_adapter/transport.py`), and this module goes further than not importing it:
`preview` never asks whether a device exists, only whether a *registry id* is
one this house knows, because a registry id is the only handle that survives a
re-pair and the only one a document carries.

The hard part is `activity_entry`, and it is hard for one reason: the engine's
closed set of outcomes has eight members and the panel's has five. The mapping
between them is stated as a table with a rule beside it (`_PANEL_OUTCOME`),
every member of `Outcome` is walked by a test, and a ninth member added later
fails that test rather than disappearing from the Activity tab.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from engine import export as engine_export
from engine import migrations as engine_migrations
from engine.binding import BindingError, House
from engine.decision_log import (
    DecisionRecord,
    HazardReading,
    HousePresence,
    ModeReading,
    Outcome,
    OverrideNote,
    Repair,
    ResolvedSetting,
    SlotRead,
)
from engine.profiles import ProfileError
from tools.catalog.schemas import current_version, load_versions

from .composition import LiveRoom, mode_documents
from .live import LiveSessionError

if TYPE_CHECKING:
    from engine.decision_log import Input

    from .adapter import HAAdapter
    from .live import LiveSession

__all__ = [
    "activity",
    "activity_entry",
    "apply",
    "export_document",
    "preview",
]

#: The schema concept an export is validated against, and whose *current*
#: version is the version this module both writes and reads. Named here rather
#: than spelled as a file name so a version bump is followed rather than missed,
#: which is the same reason `engine/profiles.py` reads `schemas/profile/` the way
#: it does.
EXPORT_CONCEPT = "export-document"

#: How many replacement devices a re-link row offers. The panel renders the
#: candidates in a `<select>`, and a list longer than a person will read is a
#: list that hides the best answer at the top of it; every candidate is still
#: ranked, so the cut is of the least likely rather than of the arbitrarily
#: ordered.
CANDIDATE_LIMIT = 10

#: The panel's five outcomes, as the Activity tab's own union spells them
#: (`panel/src/api/models.ts`). Spelled here only so the module can say what it
#: promises to produce; the values themselves come from the table below.
PANEL_OUTCOMES: tuple[str, ...] = (
    "applied",
    "skipped",
    "overridden",
    "blocked",
    "error",
)

#: The engine's disposition to the panel's, as a table with a rule.
#:
#: The rule, in one sentence: **the panel's outcome names the authority that
#: settled the evaluation's command.**
#:
#: | engine outcome            | panel outcome | the authority that settled it     |
#: |---------------------------|---------------|-----------------------------------|
#: | `acted`                   | `applied`     | none -- the command was written   |
#: | `declined`                | `skipped`     | none -- there was no command      |
#: | `skipped: unbound slot`   | `skipped`     | none -- there was no command      |
#: | `skipped: disabled`       | `skipped`     | none -- there was no command      |
#: | `lost arbitration`        | `blocked`     | another behaviour                 |
#: | `rate-limited`            | `blocked`     | the rate limiter                  |
#: | `overridden`              | `overridden`  | a person                          |
#: | `refused: unsafe`         | `error`       | the safety gate                   |
#:
#: Three things about this table are decisions rather than transcription.
#:
#: **The rule is stated, not the rows.** A reader who meets a ninth outcome
#: asks "who settled it", and the answer picks the column; a lookup that had
#: been transcribed from the eight would leave that question unanswerable and
#: the ninth outcome unmapped -- which is exactly the silent disappearance the
#: Activity tab cannot afford.
#:
#: **`error` is the safety gate and not a failure.** The panel has no `refused`
#: outcome, and `error` is its only word for "the engine declined to carry out
#: the command it was given". The refusal is deliberate and correct -- the gate
#: is the product rule working -- and the entry's `reason` says so in words, so
#: the loud chip is not a claim that something broke.
#:
#: **Nothing reaches `error` by default.** The mapping has no fallback branch:
#: `_panel_outcome` looks the outcome up and raises `LiveSessionError` naming it
#: when the row is absent, because a default would be the silent drop this table
#: exists to prevent. `test_live_export.py` walks every member of `Outcome`
#: through `activity_entry`, so a ninth member fails the suite.
_PANEL_OUTCOME: Mapping[Outcome, str] = {
    Outcome.ACTED: "applied",
    Outcome.DECLINED: "skipped",
    Outcome.SKIPPED_UNBOUND_SLOT: "skipped",
    Outcome.SKIPPED_DISABLED: "skipped",
    Outcome.LOST_ARBITRATION: "blocked",
    Outcome.RATE_LIMITED: "blocked",
    Outcome.OVERRIDDEN: "overridden",
    Outcome.REFUSED_UNSAFE: "error",
}

#: The first path segment of a diff leaf, to the row's `scope`. The `ImportDiffRow`
#: scope is what the panel groups by, so it names the *part of the configuration*
#: a leaf belongs to rather than repeating the leaf's own path; a segment this
#: map does not know -- a top-level field such as `house` or `format_version` --
#: is `document`, which is the honest answer for a leaf that belongs to no part.
_SCOPES: Mapping[str, str] = {
    "rooms": "room",
    "profiles": "profile",
    "modes": "mode",
    "house_scope": "house",
    "active": "selection",
}


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def export_document(
    session: LiveSession, *, registry_ids: Mapping[str, str] | None = None
) -> Mapping[str, object]:
    """Write the live house's whole configuration as an export document.

    The frozen `schemas/export-document` shape and nothing beside it, which is
    what makes a file this writes one this can read: `engine/export.py`'s own
    round trip is the property the phase is judged on, and a live path that
    added a field of its own would be a second format wearing the first one's
    name.

    The modes are projected rather than passed through, and that is the one
    place this differs from the facade. `OpenHouse.modes` holds mode
    *documents*; a live session holds mode *labels* ("Home", "Away"), because
    the label is what a person picks and `composition.mode_name` is what turns
    it into the name the engine gates on. The projection that runs on every
    rebuild (`composition.mode_documents`) is the one that runs here too, so an
    exported mode is the mode the running engine gated on rather than a second
    derivation of it.

    `registry_ids` is keyed by **entity id**, and the key is worth stating
    because `engine/export.py` keys its own by `(room, slot)`: that is the
    engine's addressing, and a live caller does not think in rooms and slots --
    it reads Home Assistant's entity registry, which answers "what is this
    entity's registry id" and nothing about where the entity is bound. A map
    that named a room and a slot would have to be rebuilt by every caller from
    the thing it actually holds. An entity the map does not name keeps the
    derived registry id (`engine/export.py`), which is the honest answer for a
    caller that has no registry to read.

    `exported_at` is the session's own clock and never the wall clock, for the
    reason `LiveSession` exists: the clock is the one the engine reads, and a
    document that stamped itself from somewhere else would date a backup by a
    time the house never held.
    """
    return engine_export.export_backup(
        house=session.engine.house,
        profiles=session.profiles,
        modes=mode_documents(session.modes),
        registry_ids=_registry_overrides(session, registry_ids),
        exported_at=session.clock.now.isoformat(),
    )


def _registry_overrides(
    session: LiveSession, registry_ids: Mapping[str, str] | None
) -> Mapping[tuple[str, str], str] | None:
    """A caller's entity-keyed registry ids, keyed the way the writer wants them.

    `None` when the caller gave none, so the writer's own derivation runs rather
    than being overridden by an empty map -- the difference between "I have no
    registry" and "every binding's registry id is a blank" is the difference
    between a usable document and an invalid one.

    The bindings are read from the *engine's* house rather than from
    `session.rooms`, because the engine's house is the one the exported document
    describes; a session whose rooms had drifted from its engine would export a
    document that disagreed with the house it names, which is the failure the
    session's rebuild-after-every-edit rule exists to prevent.
    """
    if registry_ids is None:
        return None
    overrides: dict[tuple[str, str], str] = {}
    for room in session.engine.house.rooms:
        for slot, entity_id in room.bindings.items():
            found = registry_ids.get(entity_id)
            if found is not None:
                overrides[(room.id, slot)] = found
    return overrides


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------


def preview(
    session: LiveSession, document: Mapping[str, object]
) -> Mapping[str, object]:
    """Report what importing `document` would change, changing nothing.

    The panel's `ImportPreview`, and the reason it is a command of its own
    rather than the apply with a flag: a preview that *were* the apply would be
    one refactor away from being it, and the whole promise of the tab is that a
    person can look before they leap.

    Three questions are answered, in the order a person asks them.

    **Can this build read the file at all?** `compatible` is not a guess and not
    a version comparison: the document is migrated
    (`engine/migrations.py`), validated against the current
    `schemas/export-document`, and *built* -- `engine/export.py`'s own import,
    which validates the house against this session's vocabulary and the
    profiles against this session's schema. A document that cannot be built is
    a document that cannot be applied, so it is refused here with the reason the
    builder gave rather than at the moment the user pressed Apply.

    **What would change?** `engine/export.py`'s dry run between the session's
    own export and the incoming one, leaf by leaf, so a nested binding and a
    profile's delta are both reported by path. The comparison is against the
    migrated document rather than the raw one, because a legacy 1.0.0 file
    describes the same house in a different shape and flattening the two forms
    against each other would report every binding as removed and re-added.

    **Which bindings name a device this house does not have?** `engine/export.py`'s
    re-link, given this house's own registry ids. A document's bindings carry a
    registry id and an entity id, and the registry id is the one that survives a
    re-pair: when it is one this house knows, the binding is silently pointed at
    the entity this house holds -- which is the re-pair case working -- and when
    it is not, the device is genuinely absent and the row asks the person to
    choose. `entity_id` in such a row is `None` because the entity the document
    named is the one that is gone; there is no counterpart to show.

    Nothing here writes to the session. The dry run compares documents, the
    re-link rewrites a copy, and a caller that called `preview` twice would get
    the same answer twice.
    """
    format_version = str(document.get("format_version", ""))
    migrated, failure = _readable(session, document)
    if migrated is None:
        return {
            "format_version": format_version,
            "compatible": False,
            "diff": (),
            "relink": (),
            "notes": (failure,),
        }

    diff = engine_export.dry_run(export_document(session), migrated)
    relink = _relink_requests(session, migrated)
    return {
        "format_version": format_version,
        "compatible": True,
        "diff": _diff_rows(diff),
        "relink": relink,
        "notes": _notes(session, migrated, diff, relink, declared=format_version),
    }


def _readable(
    session: LiveSession, document: Mapping[str, object]
) -> tuple[Mapping[str, object] | None, str]:
    """`document` migrated and found importable, or the reason it is not.

    The three checks run in the order a person can act on them, which is also
    the order `OpenHouse.import_backup` runs them: whether this build reads the
    version at all, whether the document is shaped like an export, and whether
    it describes a house *this vocabulary* holds. The last is the one that
    catches a document from another installation naming a room type or a slot
    this house's catalog does not define, which is a document nothing is wrong
    with that still cannot be imported here.

    The failure is returned as a sentence rather than raised, because the
    caller's whole job is to put it in `notes` -- the panel shows an
    incompatible file's reason beside the file, and an exception would arrive as
    an error banner with no preview to attach it to. `apply` calls this too and
    raises, so there is one reading of the document and two ways of reporting
    it.
    """
    if not isinstance(document, Mapping):
        return None, "This file is not an export document: it is not a JSON object."
    try:
        migrated = engine_migrations.migrate_export(document)
        engine_export.validate_export(migrated, _export_schema(session.root))
        engine_export.import_backup(
            migrated,
            vocabulary=session.vocabulary,
            profile_schema=session.profile_schema,
        )
    except (
        engine_migrations.MigrationError,
        engine_export.ExportError,
        BindingError,
        ProfileError,
        KeyError,
        TypeError,
    ) as failure:
        return None, f"This file cannot be imported: {_why(failure)}"
    return migrated, ""


def _why(failure: Exception) -> str:
    """A failure as a sentence the panel can show.

    A `KeyError`'s own message is the missing key in quotes and nothing else,
    which reads as a typo rather than as a diagnosis; every other failure in the
    list above already names what it found.
    """
    if isinstance(failure, KeyError) and failure.args:
        return f"it has no {failure.args[0]!r} field"
    return str(failure) or type(failure).__name__


def _export_schema(root: Path) -> Mapping[str, object]:
    """The current `schemas/export-document`, read from the tree at `root`.

    Read from the tree rather than from a module constant so a session pointed
    at another checkout validates against *that* checkout's schema, which is the
    same rule `Vocabulary.load` and `load_profile_schema` follow.
    """
    versions = load_versions(EXPORT_CONCEPT, root=root)
    current = current_version(versions)
    if current is None:
        raise LiveSessionError(f"no {EXPORT_CONCEPT} schema is present under {root}")
    return cast("Mapping[str, object]", current.document)


def _diff_rows(diff: engine_export.Diff) -> tuple[Mapping[str, object], ...]:
    """The dry run's leaves as the panel's `ImportDiffRow` list.

    `kind` is read off the two sides rather than off the change itself: the dry
    run says only that two leaves differ, and "there was nothing here" is what
    makes the difference an addition rather than a change. A leaf present in
    both documents with equal values is never emitted, which is why the panel's
    fourth kind -- `unchanged` -- has no row here: a dry run that listed what
    would *not* change would be a diff nobody can read.
    """
    rows: list[Mapping[str, object]] = []
    for change in diff.changes:
        rows.append(
            {
                "kind": _kind(change.before, change.after),
                "scope": _scope(change.path),
                "path": change.path,
                "before": _text(change.before),
                "after": _text(change.after),
            }
        )
    return tuple(rows)


def _kind(before: object, after: object) -> str:
    """Whether a changed leaf is an addition, a removal, or a change."""
    if before is None:
        return "add"
    if after is None:
        return "remove"
    return "change"


def _scope(path: str) -> str:
    """The part of the configuration a leaf's path belongs to."""
    segments = [segment for segment in path.split("/") if segment]
    first = segments[0] if segments else ""
    return _SCOPES.get(first, "document")


def _text(value: object) -> str | None:
    """One leaf as the panel's `before`/`after` column, which is text or nothing.

    The panel renders both columns as text, and a delta may hold a number, a
    boolean or a whole document -- `config` deltas are documents -- so the
    projection is made here, once, rather than left to the encoder at the far
    end where a nested mapping would arrive as `[object Object]`.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True)


def _notes(
    session: LiveSession,
    migrated: Mapping[str, object],
    diff: engine_export.Diff,
    relink: Sequence[Mapping[str, object]],
    *,
    declared: str,
) -> tuple[str, ...]:
    """The sentences the panel shows beside the diff.

    Each note is a fact the diff cannot express. A migration is invisible in a
    diff of the migrated document, so a file written by an older build says so
    -- which is why the version is the one the file *declares* and not the one
    the migrated copy carries, since a successful migration has already
    overwritten the latter and the note would never fire. A house name is not
    imported (`apply` explains why), so a file naming another house says so
    rather than letting the person wonder what the ignored field meant. And the
    count of re-link rows is repeated as a sentence because the table's presence
    is easy to miss when the list below it is long.
    """
    notes: list[str] = []
    written = str(session.engine.house.name)
    incoming = str(migrated.get("house", ""))
    if incoming and incoming != written:
        notes.append(
            f"This file is for the house {incoming!r}; importing it keeps this "
            f"house's name, {written!r}, which is the name Home Assistant holds."
        )
    if declared and declared != engine_migrations.CURRENT_EXPORT_VERSION:
        notes.append(
            f"This file is version {declared}, and it would be imported as "
            f"{engine_migrations.CURRENT_EXPORT_VERSION}."
        )
    if relink:
        notes.append(
            f"{len(relink)} binding(s) name a device this house does not hold "
            "and need re-linking."
        )
    if diff.empty:
        notes.append("Nothing would change.")
    return tuple(notes)


# --------------------------------------------------------------------------
# Re-link
# --------------------------------------------------------------------------


def _house_registry(session: LiveSession) -> dict[str, str]:
    """This house's registry ids, and the entity each names.

    Built by exporting the house and reading the result back, rather than by
    re-deriving a registry id per binding here. `engine/export.py` owns the
    derivation -- "the entity id's object part, or a caller's override" -- and a
    second copy of it in this module would be a second answer to "what is this
    binding's registry id", which is the one question a re-link turns on.

    A registry id bound in two rooms maps to the last room's entity, which is
    the honest collapse: a registry id is one device, and a device the house
    binds twice is one device with two bindings rather than two devices. The
    entity it names is then the one this house would re-link both bindings to,
    which is what the wizard wants.
    """
    document = export_document(session)
    registry: dict[str, str] = {}
    for room in _sequence(document.get("rooms")):
        bindings = room.get("bindings")
        if not isinstance(bindings, Mapping):
            continue
        for binding in bindings.values():
            if not isinstance(binding, Mapping):
                continue
            registry_id = binding.get("registry_id")
            entity_id = binding.get("entity_id")
            if isinstance(registry_id, str) and isinstance(entity_id, str):
                registry[registry_id] = entity_id
    return registry


def _relink_requests(
    session: LiveSession, document: Mapping[str, object]
) -> tuple[Mapping[str, object], ...]:
    """The panel's `RelinkRequest` rows: bindings no registry id here can resolve.

    The test is `engine/export.py`'s own re-link run against this house's
    registry: a binding whose registry id this house knows is *resolved* -- the
    document is pointed at the entity the house holds, which is a re-pair
    succeeding -- and one whose registry id this house does not know is left
    alone and reported. So the rows are exactly the devices that are gone from
    this house's point of view, and a document exported from this house and
    re-imported after entity ids moved produces none.

    The scope of the test is deliberately the registry id and not the entity id.
    An entity id changes; that is what a re-pair *is*, and a preview that
    reported a moved entity as a missing device would send a person to the
    re-link wizard for the case the wizard is not for.
    """
    unresolved = set(
        engine_export.relink(document, _house_registry(session)).unresolved
    )
    if not unresolved:
        return ()
    rows: list[Mapping[str, object]] = []
    for room_id, slot, registry_id, entity_id in _document_bindings(document):
        if registry_id not in unresolved:
            continue
        rows.append(
            {
                "room_id": room_id,
                "slot": slot,
                "registry_id": registry_id,
                "entity_id": None,
                "candidates": _candidates(session, slot=slot, entity_id=entity_id),
            }
        )
    return tuple(rows)


def _document_bindings(
    document: Mapping[str, object],
) -> tuple[tuple[str, str, str, str], ...]:
    """Every binding a document names, as `(room, slot, registry id, entity id)`.

    Read from the document rather than from `engine/export.py`'s re-link, which
    returns a rewritten copy and no index of what it found. The migration has
    already run by the time this does, so the `rooms` form is the only one there
    is to read.
    """
    rows: list[tuple[str, str, str, str]] = []
    for room in _sequence(document.get("rooms")):
        room_id = room.get("id")
        bindings = room.get("bindings")
        if not isinstance(room_id, str) or not isinstance(bindings, Mapping):
            continue
        for slot, binding in bindings.items():
            if not isinstance(binding, Mapping):
                continue
            registry_id = binding.get("registry_id")
            entity_id = binding.get("entity_id")
            if isinstance(registry_id, str) and isinstance(entity_id, str):
                rows.append((room_id, str(slot), registry_id, entity_id))
    return tuple(rows)


def _candidates(
    session: LiveSession, *, slot: str, entity_id: str
) -> tuple[Mapping[str, object], ...]:
    """The devices this house holds that could fill `slot`, best first.

    The ranking is a stated rule rather than a list, because the panel renders
    the list in the order it arrives and the order is therefore the whole of the
    answer:

    1. **A device already bound to this slot somewhere else in the house.** It is
       the slot's own precedent: the house has used this device for this slot
       before, and nothing about a re-pair changes that.
    2. **A device in the same domain as the one the document named.** A light
       group is filled by a light; the domain is all this module knows about a
       slot, because the vocabulary deliberately does not publish
       `accepts_domains` (`engine/vocabulary.py`).
    3. **Everything else**, last, because a suggestion list that put the
       kitchen's television above the hall's second lamp would be worse than no
       list.

    `score` is the panel's confidence, and it is the rank's own number rather
    than a finer-grained guess this module has no basis for: 1.0, 0.5, 0.1. The
    list is cut at `CANDIDATE_LIMIT`, so the ranking is also the cut.
    """
    adapter = session.adapter
    if adapter is None:  # pragma: no cover - `build` always fills it
        return ()
    precedent = {
        bound
        for room in session.engine.house.rooms
        if (bound := room.bindings.get(slot)) is not None
    }
    domain = entity_id.partition(".")[0]
    reverse = {
        entity: registry for registry, entity in _house_registry(session).items()
    }
    ranked: list[tuple[float, str]] = []
    for held in adapter.list_entities():
        if held in precedent:
            score = 1.0
        elif held.partition(".")[0] == domain:
            score = 0.5
        else:
            score = 0.1
        ranked.append((score, held))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return tuple(
        _suggestion(adapter, held, score=score, registry_id=reverse.get(held))
        for score, held in ranked[:CANDIDATE_LIMIT]
    )


def _suggestion(
    adapter: HAAdapter,
    entity_id: str,
    *,
    score: float,
    registry_id: str | None,
) -> Mapping[str, object]:
    """One candidate as the panel's `BindingSuggestion`.

    `friendly_name` is Home Assistant's own, so the wizard offers "Hall ceiling"
    rather than `light.hall_ceiling`, and the object part of the id is the
    fallback for an entity that has no name -- which is what the panel would
    have shown anyway had the attribute been absent.
    """
    name = entity_id.partition(".")[2]
    friendly = adapter.read_entity(entity_id).attributes.get("friendly_name")
    return {
        "entity_id": entity_id,
        "registry_id": registry_id,
        "friendly_name": friendly if isinstance(friendly, str) else name,
        "domain": entity_id.partition(".")[0],
        "score": score,
    }


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


def apply(session: LiveSession, document: Mapping[str, object]) -> Mapping[str, object]:
    """Replace the session's configuration from an export, and rebuild.

    `{applied, snapshot_id, diff}` -- the protocol's three keys
    (`panel/src/api/protocol.ts`). `diff` is the preview's diff, computed before
    anything was written: the panel shows what an import did, and recomputing it
    afterwards would compare the new configuration against itself and report
    nothing.

    What is replaced is the *configuration*: the rooms and their bindings, the
    profiles and the selections in force, and the mode labels. What is not
    replaced is everything that is not in the document, and each omission is
    deliberate rather than an oversight.

    - **The house name.** It is the config entry's title in Home Assistant, which
      is a fact about the installation rather than about the file; a backup that
      renamed somebody's house would be renaming the entry it was restored into.
    - **The installed packs.** The export document carries no installed set, so
      there is nothing to restore and nothing to remove: an import takes the
      house's shape, not its software.
    - **The house scope.** The live composition declares the vocabulary's whole
      house-slot list on every rebuild (`composition.house_document`), and an
      import must not narrow it: the engine *raises* for a house-scoped slot the
      scope omits before it can notice the slot is merely unbound, so a document
      that trimmed the list would crash the tick of every house-scoped unit whose
      slot is unbound.
    - **The devices.** A live house does not own them. The simulator's import
      adds the entities a document names to its adapter; Home Assistant already
      holds its own, and a backup that created a `light.hall_ceiling` that no
      integration provides would be a backup that invented hardware. A binding
      to a device that is gone is exactly what `preview`'s re-link rows are for.

    **`snapshot_id` is empty, and that is a finding rather than a placeholder.**
    The panel's reply says `Snapshot <id> taken, so this can be undone`
    (`panel/src/tabs/import-export.ts`), and the protocol types the field as a
    string. The simulator can honour that because `OpenHouse.snapshot` produces
    a document and `OpenHouse.undo_import` keeps one; the live path has neither
    a snapshot store nor an undo, and `LiveSession.to_state` is deliberately
    configuration-only -- it says so in its own docstring, because the engine's
    runtime state does not survive a restart. So there is nothing here to mint
    an id for, and this module returns the empty string rather than inventing a
    format that would look like a promise. A caller that wants the tab's
    sentence to be true has to give the session somewhere to keep the snapshot;
    that is a change to `LiveSession`, not one this module can make.
    """
    migrated, failure = _readable(session, document)
    if migrated is None:
        raise LiveSessionError(failure)
    backup = engine_export.import_backup(
        migrated,
        vocabulary=session.vocabulary,
        profile_schema=session.profile_schema,
    )
    # No check that the house has a room: `schemas/house/1.0.0.json` requires
    # `minItems: 1`, so a document describing an empty house was refused by
    # `_readable` above and never reaches the edit below. The guarantee is the
    # schema's, and restating it here would be a branch no input can take --
    # and, worse, one that would have to be kept in step with the schema's.
    diff = _diff_rows(engine_export.dry_run(export_document(session), migrated))
    session.set_rooms(_live_rooms(backup.house))
    session.set_profiles(backup.profiles)
    labels = _mode_labels(backup.modes)
    if labels:
        # The modes are the one field with no setter that rebuilds, so they are
        # written and one rebuild is spent on them. Only when the document
        # carried modes: a document that declares none must leave the labels the
        # session was configured with rather than emptying the mode set, which
        # would silently switch off every behaviour that gates on a mode.
        session.modes = labels
        session.rebuild()
    return {"applied": True, "snapshot_id": "", "diff": diff}


def _live_rooms(house: House) -> tuple[LiveRoom, ...]:
    """A house's rooms as the session's own room type.

    The bindings are copied out rather than referenced, because `LiveRoom` is
    the session's value and a shared mapping would let a later edit of one
    appear in the other -- the aliasing the session's rebuild-after-every-edit
    rule cannot protect against.

    `auto_lighting` takes its default. A document carries no lighting
    permission and inventing one either way would be a decision the file did not
    make; the default is the one a newly added room gets, so an imported house
    is a house whose rooms have not been switched off.
    """
    return tuple(
        LiveRoom(
            id=room.id,
            name=room.name,
            type=room.type,
            bindings=dict(room.bindings),
        )
        for room in house.rooms
    )


def _mode_labels(modes: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """A document's mode *names*, as the session's mode *labels*.

    A round trip that is not quite the identity, and the loss is stated rather
    than hidden: an export writes the name the engine gates on, and a live
    session's labels are what a person reads ("Home") while its names are what
    the engine reads ("home"). Re-importing a document therefore labels a mode
    by its name, which is the same string for every mode that was not renamed
    between the two. Recovering "Home" from "home" would mean guessing a
    capitalisation the document does not carry.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for mode in modes:
        name = mode.get("name")
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        labels.append(name)
    return tuple(labels)


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------


def activity(
    session: LiveSession, *, limit: int = 50, before: str | None = None
) -> tuple[Mapping[str, object], ...]:
    """The decision log as the Activity tab's rows, newest first.

    `limit` bounds the read and is the panel's `{limit?}`, passed straight to the
    log's own window rather than sliced here, so "the last fifty decisions" is
    one query against the log's bound rather than a copy of the log in this
    module.

    `before` is the opaque cursor the panel pages with: the `id` of the newest
    entry the caller already holds, answered with the entries strictly older
    than it. It is opaque on purpose -- the panel passes back a string this
    module minted and never parses it -- which is what lets the id be a digest
    of the record (`_entry_id`) rather than an index into a log whose bound
    drops its oldest entries.

    A cursor the window does not hold -- because the log's bound has since
    dropped the record it named, or because it came from another house -- is
    answered with the whole window rather than with nothing. "You have fallen
    off the end" and "there is nothing older" are different facts, and only the
    second is worth showing a person as an empty list.
    """
    if limit < 0:
        raise LiveSessionError(f"an activity limit cannot be negative: {limit}")
    entries = [activity_entry(record) for record in session.engine.log.window(limit)]
    if before is not None:
        identifiers = [entry["id"] for entry in entries]
        if before in identifiers:
            entries = entries[: identifiers.index(before)]
    entries.reverse()
    return tuple(entries)


def activity_entry(record: DecisionRecord) -> Mapping[str, object]:
    """One decision record as one `DecisionLogEntry`.

    The single source of the projection, and the reason it is a function of its
    own rather than a step inside `activity`: the panel's Activity tab is fed
    two ways -- the list command and the subscription that pushes one record at
    a time (`custom_components/open_house/host.py`) -- and two ways of building
    the same row would be two answers to the same question, drifting apart at
    the first change to either.

    **What the record can supply, and what it cannot.** A `DecisionRecord` is
    `(at, actor, inputs, rule, commands, outcome, state_delta)` and nothing
    else, so three of the panel's fields are read off it and two are not:

    - `behaviour` is the record's `actor`, the unit whose evaluation it was;
    - `entity_id` and `action` come from what the evaluation wrote, or proposed
      writing, when it did either;
    - `room` is `None` for most records, because a room-scoped evaluation's
      record does not name the room it was scoped to -- only a `Repair` and a
      single-room `HousePresence` name one, and `_room_of` reads those two;
    - `priority` is `None` always, because the engine resolves a unit's
      arbitrated priority at evaluation time and does not record it
      (`engine/engine.py`, `_Draft`). Guessing it from the behaviour's declared
      priority would be wrong for exactly the houses that have tuned it. A
      panel that needs it needs the engine to record it.

    Reporting `None` for what the record does not carry is the honest answer and
    not a gap left open: the alternative -- filling a field from something other
    than the record -- would make two entries that compare equal describe
    different decisions.
    """
    return {
        "id": _entry_id(record),
        "at": record.at.isoformat(),
        "room": _room_of(record),
        "behaviour": record.actor,
        "entity_id": _entity_of(record),
        "action": _action_of(record),
        "reason": _reason(record),
        "priority": None,
        "outcome": _panel_outcome(record.outcome),
    }


def _panel_outcome(outcome: Outcome) -> str:
    """The panel's outcome for an engine outcome, by `_PANEL_OUTCOME`'s rule.

    Raises rather than defaulting. An outcome with no row is an outcome this
    build has met and does not understand, and a record whose disposition the
    panel cannot name is a record that must fail loudly the moment it is read
    rather than appear in the Activity tab as something it is not. That is what
    makes adding a ninth member to `Outcome` a failing test instead of a
    disappearing row.
    """
    try:
        return _PANEL_OUTCOME[outcome]
    except KeyError:
        raise LiveSessionError(
            f"the engine outcome {outcome!r} has no panel outcome; the table in "
            "ha_adapter/live_export.py has to be extended for it"
        ) from None


def _entry_id(record: DecisionRecord) -> str:
    """A stable identifier for one record, opaque to the caller.

    A digest of the record's own document rather than a counter or a timestamp:
    the log is bounded and its oldest records fall out, so a position is not an
    identity, and two records evaluated in the same tick share an instant. The
    digest is of everything the record carries, so it is stable across calls,
    across a rebuild and across a restart, and it is what makes `before` a
    cursor a caller can hold without this module having to keep anything.

    Two records that agree on every field share an id, and that is correct: they
    are the same decision recorded twice, and a caller cannot tell them apart
    because there is nothing to tell apart.
    """
    document = record.to_document()
    encoded = json.dumps(document, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _room_of(record: DecisionRecord) -> str | None:
    """The room the record names, when it names exactly one.

    Two inputs name a room and no others do. `Repair` names the room whose slot
    stopped answering; `HousePresence` names the rooms a house-emptiness was
    decided from, and only when that list is exactly one is it "the room this
    was about" rather than a set of rooms behind a house-level fact. Everything
    else -- a byte-for-byte room-scoped evaluation's own record -- carries the
    slot and the entities it read and not the room, so the answer is `None`
    rather than a guess made from an entity id this function has no house to
    resolve.
    """
    for entry in record.inputs:
        if isinstance(entry, Repair):
            return entry.room_id
        if isinstance(entry, HousePresence) and len(entry.quiet_rooms) == 1:
            return entry.quiet_rooms[0]
    return None


def _entity_of(record: DecisionRecord) -> str | None:
    """The device the evaluation touched, in the order the record can name it.

    What was *written* first, then what was *proposed*, then what was *read* as
    a hazard or a repair. The order is what a person means by "the device" in the
    question the tab answers: an entry that changed a lamp is about the lamp,
    and one that was skipped before proposing anything is about the slot the
    record had to name to be read at all.
    """
    if record.state_delta:
        return record.state_delta[0].entity_id
    for command in record.commands:
        if command.entities:
            return command.entities[0]
    for entry in record.inputs:
        if isinstance(entry, (Repair, HazardReading)):
            return entry.entity_id
    return None


def _action_of(record: DecisionRecord) -> str:
    """What the evaluation asked for, as the panel's `action`.

    The proposed action when there is one, and otherwise the state that was
    written -- which a direct write produces and a behaviour does not. Empty for
    a record that proposed nothing and wrote nothing, which the panel renders as
    an em dash; the field is not nullable in `models.ts`, so the empty string is
    the shape's own way of saying "nothing was asked for".
    """
    if record.commands:
        return record.commands[0].action
    if record.state_delta:
        return record.state_delta[0].after
    return ""


def _reason(record: DecisionRecord) -> str:
    """Why this happened, in plain language, from the record's own fields.

    The point of the tab (`panel/src/tabs/activity.ts`: "the log's reason is the
    product, not debug output"). It is therefore written as a sentence about the
    house rather than as a dump of the record: the disposition first, then what
    the evaluation read, then what it did, each fact a clause of the same
    paragraph.

    Every clause is built from a field of *this* record and from nothing else --
    not from the engine, not from the house, not from the behaviour's source.
    That is what lets the reason be trusted: a record read on its own, months
    later, out of a file, produces the same sentence a live panel showed.
    """
    sentences = [f"{record.actor} {_disposition(record.outcome)}."]
    if record.rule is not None:
        sentences.append(f"It matched the rule {record.rule}.")
    for entry in record.inputs:
        sentences.append(_input_sentence(entry))
    sentences.extend(_result_sentences(record))
    return " ".join(sentence for sentence in sentences if sentence)


#: Which way each outcome fell, in the words a person would use. Written as a
#: clause rather than a sentence because `_reason` completes it with the unit
#: that did it -- "motion_lighting was skipped: it is switched off here." -- so
#: the same phrase reads correctly under every actor.
_DISPOSITION: Mapping[Outcome, str] = {
    Outcome.ACTED: "acted",
    Outcome.DECLINED: "reached its rule and decided that nothing needed doing",
    Outcome.LOST_ARBITRATION: (
        "lost arbitration: another behaviour wanted the same device and outranked it"
    ),
    Outcome.OVERRIDDEN: (
        "stood down, because a person's own change is in charge of the device"
    ),
    Outcome.RATE_LIMITED: (
        "waited, because it had already acted recently and the rate limit held it back"
    ),
    Outcome.SKIPPED_UNBOUND_SLOT: (
        "was skipped, because a slot it needs is bound to nothing"
    ),
    Outcome.SKIPPED_DISABLED: "was skipped, because it is switched off for this house",
    Outcome.REFUSED_UNSAFE: (
        "was refused: the safety rule will not carry out a command that would "
        "leave a device unsafe"
    ),
}


def _disposition(outcome: Outcome) -> str:
    """The clause a record's outcome contributes to its reason.

    Guarded for the same reason `_panel_outcome` is, and separately from it:
    this is a second table over the same enum, so a ninth outcome would fail
    here too, and a `KeyError` raised out of a dict literal is a worse answer
    than a failure that names what to do about it.
    """
    try:
        return _DISPOSITION[outcome]
    except KeyError:
        raise LiveSessionError(
            f"the engine outcome {outcome!r} has no disposition sentence; the "
            "table in ha_adapter/live_export.py has to be extended for it"
        ) from None


def _input_sentence(entry: Input) -> str:
    """One consulted input as a clause, or the empty string for a silent kind.

    A `SlotRead` and a `ResolvedSetting` are the two kinds a reader needs in
    order to check the arithmetic of a decision -- what was read and what
    threshold it was compared against -- and each gets a sentence. A
    `DarkSourceReading` does not, because the slot it stands for is already
    named by the read beside it; the remaining kinds each say something the
    disposition alone cannot.
    """
    if isinstance(entry, SlotRead):
        entities = ", ".join(entry.entities) if entry.entities else "nothing bound"
        return (
            f"It read {entry.slot} under the {entry.reduction} reduction ({entities})."
        )
    if isinstance(entry, ResolvedSetting):
        return (
            f"It read the setting {entry.key} as {entry.value!r}, "
            f"decided at the {entry.layer} layer."
        )
    if isinstance(entry, ModeReading):
        state = "active" if entry.active else "not active"
        return f"The {entry.mode} mode was {state}."
    if isinstance(entry, HousePresence):
        rooms = ", ".join(entry.quiet_rooms) if entry.quiet_rooms else "no room"
        return (
            f"The house read as {'empty' if entry.empty else 'occupied'} "
            f"(quiet for long enough: {rooms})."
        )
    if isinstance(entry, OverrideNote):
        return _override_sentence(entry)
    if isinstance(entry, HazardReading):
        return f"It answered the {entry.kind} alert raised by {entry.entity_id}."
    if isinstance(entry, Repair):
        return (
            f"It could not read {entry.slot} in {entry.room_id}, because "
            f"{entry.entity_id} stopped answering."
        )
    return ""


def _override_sentence(note: OverrideNote) -> str:
    """An override, in force or just ended, as a clause.

    The two cases are one type at two moments and they read very differently:
    an override still in force explains why nothing was done, and one that has
    just been released explains why something was done *now*. The awaited
    conditions are named in the first case because "we are waiting for the
    timeout" and "we are waiting for the room to empty" are different answers to
    "when will my lights come back".
    """
    if note.released is None:
        waited = (
            ", ".join(_phrase(condition) for condition in note.awaited)
            or "nothing in particular"
        )
        return (
            f"{note.entity_id} is under a person's override, and the engine is "
            f"waiting for {waited}."
        )
    return (
        f"The person's override on {note.entity_id} has ended "
        f"({_phrase(note.released)}), so the engine is acting again."
    )


def _result_sentences(record: DecisionRecord) -> list[str]:
    """What the evaluation did, or why nothing observable came of it.

    A record that wrote something names each write; a record that proposed
    something and wrote nothing says so, because "the engine wanted to turn the
    hall light on and did not" is the sentence a person is looking for when they
    open the tab. A record that did neither -- a decline, a skip -- adds nothing
    here, because the disposition has already said it.
    """
    if record.state_delta:
        return [
            f"It changed {change.entity_id} from {change.before!r} to {change.after!r}."
            for change in record.state_delta
        ]
    if record.commands:
        command = record.commands[0]
        devices = ", ".join(command.entities) if command.entities else "no device"
        return [f"It proposed {command.action} on {devices}, which was not applied."]
    return []


def _phrase(condition: object) -> str:
    """An enum value's name as words -- `override_timeout` as "override timeout"."""
    return str(condition).replace("_", " ")


# --------------------------------------------------------------------------
# Small readers over a document that already migrated and validated
# --------------------------------------------------------------------------


def _sequence(value: object) -> tuple[Mapping[str, object], ...]:
    """A migrated document's list field, keeping only the rows that are objects.

    A projection and not a check: the document validated against the frozen
    schema before this ran, so a row here is an object and a non-object would be
    a contradiction rather than a thing to report. Dropping it keeps the caller
    free of a branch no reachable input takes.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        cast("Mapping[str, object]", row) for row in value if isinstance(row, Mapping)
    )
