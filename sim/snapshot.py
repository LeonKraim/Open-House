"""Snapshot and restore over the runtime state a run resumes from.

A snapshot is the *state* a run will decide from, and nothing else. It carries
the adapter's slice -- every entity with its live state, its attributes and its
availability, plus the startup condition a restart returns it to -- the
engine-owned runtime state, the virtual clock's position and
the random stream's position, under a `snapshot_version`. It deliberately does
*not* carry the decision log: state is history-independent and history is not, so
restoring a run restores what it will decide from and not what it has already
decided, and a log carried through a restore would grow without bound along
exactly the axis the log's own bound exists to cap (`design.md` D3, `engine-core`
"only history is excluded from snapshots").

The document is split by owner rather than centralised. The adapter supplies its
own slice through the port's `snapshot` operation (`house-adapter`: the port is
not asked to hold state it does not own), `simulation` supplies the clock and the
stream because it owns them, and the engine-owned runtime state -- bindings,
modes, enable flags, override records, rate-limit windows and room timers -- is
passed *through* as an opaque mapping the engine fills. Passing it through rather than
declaring a type for it is what keeps the dependency edge one-way: the engine may
not import `sim/` (`design.md` D12), so a snapshot type the engine had to use
could not live here. The simulator owns the document's shape and validates that
the engine's half names the enumerated fields; it never inspects their contents.

Restore rebuilds rather than mutates. `VirtualClock` and `RandomStream` have no
setter -- a clock moves only through `advance`, and a stream is repositioned at
construction -- so the documented way back is `VirtualClock.started_at` and
`RandomStream.restored`, and `restore_snapshot` returns the rebuilt substrates.
The adapter is rebuilt through its own port operations (`add_entity`,
`set_availability`, `actuate`, `inject_fault`), so a restored house cannot hold a
state the adapter could not have produced (`design.md` D11) and the fake needs no
private door to be resumable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from engine.adapter import ChangeContext, ChangeOrigin, EntitySnapshot, Fault
from sim.adapter import FakeHouseAdapter
from sim.clock import VirtualClock
from sim.entropy import RandomStream

if TYPE_CHECKING:
    from engine.adapter import HouseAdapter

#: The snapshot document's format version. Bumping it is a deliberate change: a
#: restore refuses a version it does not understand rather than resuming from a
#: subset of a format it has never seen (`control-surface`). It is at 1.3.0
#: because the engine-owned half grew its seventh enumerated field: a 1.2.0
#: document records no installed set, so a restore from one would resume a house
#: whose packs it cannot account for -- and "this document predates the field" is
#: a better diagnosis than the "your snapshot is incomplete" a missing-field check
#: would give for a document that is merely older. (1.2.0 was the same change for
#: room timers, and 1.1.0 for the two `startup_*` fields on an entity entry.)
SNAPSHOT_VERSION = "1.3.0"

#: The engine-owned runtime state the snapshot must carry, named normatively.
#: The values are the engine's and are opaque here; the *names* are the document's
#: and are what makes a missing field detectable (`simulation`: an omission is
#: invisible until a replay diverges). `room_timers` is the dwell registry: how
#: long each room has been clear of motion, which decides both an override's
#: `room_emptied` lapse and the house reading as empty, and which therefore has to
#: survive a restore or a resumed run decides differently from the one it resumed.
ENGINE_STATE_FIELDS: tuple[str, ...] = (
    "bindings",
    "modes",
    "enable_flags",
    "override_records",
    "rate_limit_windows",
    "room_timers",
    "installed_packs",
)

#: Every top-level field the snapshot document carries.
_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "snapshot_version",
    "entities",
    "engine_state",
    "clock",
    "random_seed",
    "random_position",
)


class SnapshotError(Exception):
    """Base for the failures this module defines."""


class UnsupportedSnapshotVersionError(SnapshotError):
    """A document whose `snapshot_version` this build does not understand."""

    def __init__(self, version: str) -> None:
        super().__init__(
            f"unsupported snapshot_version {version!r}; this build understands "
            f"{SNAPSHOT_VERSION!r} only"
        )
        self.version = version


class IncompleteSnapshotError(SnapshotError):
    """A snapshot document missing an enumerated field.

    The missing names are carried on the instance so a failure can say *which*
    piece of state was lost, which is the whole point of enumerating the contents.
    """

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__(f"snapshot is missing enumerated fields: {sorted(missing)}")
        self.missing = tuple(missing)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The complete observable state a run resumes from, and nothing more.

    `entities` is the port's slice; `engine_state` is the engine's half, keyed by
    `ENGINE_STATE_FIELDS`; `clock` is an ISO instant and `random_seed` /
    `random_position` are the stream's whole position, so a restore reconstructs
    all three. The log is absent by construction -- there is no field for it.
    """

    snapshot_version: str
    entities: tuple[EntitySnapshot, ...]
    engine_state: Mapping[str, object]
    clock: str
    random_seed: int
    random_position: int

    def to_document(self) -> dict[str, object]:
        """The document as a JSON-serialisable mapping, built deterministically."""
        return {
            "snapshot_version": self.snapshot_version,
            "entities": [_entity_document(entry) for entry in self.entities],
            "engine_state": _json_safe(self.engine_state),
            "clock": self.clock,
            "random_seed": self.random_seed,
            "random_position": self.random_position,
        }

    def to_json(self) -> str:
        """The document as stable JSON text: same state, same string."""
        return json.dumps(self.to_document(), sort_keys=True)

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Snapshot:
        """Parse a document, failing on a missing field rather than defaulting it.

        Defaulting an absent field -- an empty attribute map, position zero -- is
        what turns an incomplete snapshot into a silent divergence; naming it
        instead is the check the enumeration exists for.
        """
        _require_fields(document, _SNAPSHOT_FIELDS)
        engine_state = _require_mapping(document["engine_state"], "engine_state")
        _require_fields(engine_state, ENGINE_STATE_FIELDS)
        entity_documents = _require_sequence(document["entities"], "entities")
        return cls(
            snapshot_version=_require_str(document["snapshot_version"], "version"),
            entities=tuple(_entity_from_document(item) for item in entity_documents),
            engine_state=engine_state,
            clock=_require_str(document["clock"], "clock"),
            random_seed=_require_int(document["random_seed"], "random_seed"),
            random_position=_require_int(
                document["random_position"], "random_position"
            ),
        )

    @classmethod
    def from_json(cls, text: str) -> Snapshot:
        """Parse the JSON text `to_json` writes."""
        loaded: object = json.loads(text)
        return cls.from_document(_require_mapping(loaded, "document"))


@dataclass(slots=True)
class Simulation:
    """The substrates a run is driven through, plus the engine-owned state.

    This is the unit `take_snapshot` captures and `restore_snapshot` rebuilds: a
    fake house, the clock and stream it is constructed with, and the engine's
    runtime state as an opaque mapping.
    """

    adapter: FakeHouseAdapter
    clock: VirtualClock
    stream: RandomStream
    engine_state: Mapping[str, object]

    @classmethod
    def start(
        cls,
        *,
        seed: int,
        started_at: datetime,
        engine_state: Mapping[str, object] | None = None,
    ) -> Simulation:
        """A fresh run: a clock fixed at `started_at`, a stream seeded, an empty house."""
        stream = RandomStream.from_seed(seed)
        clock = VirtualClock.started_at(started_at)
        return cls(
            adapter=FakeHouseAdapter(clock=clock, random_stream=stream),
            clock=clock,
            stream=stream,
            engine_state=_empty_engine_state()
            if engine_state is None
            else engine_state,
        )

    def snapshot(self) -> Snapshot:
        """Capture this run's state."""
        return take_snapshot(
            self.adapter,
            clock=self.clock,
            stream=self.stream,
            engine_state=self.engine_state,
        )


def take_snapshot(
    adapter: HouseAdapter,
    *,
    clock: VirtualClock,
    stream: RandomStream,
    engine_state: Mapping[str, object],
) -> Snapshot:
    """Compose the one snapshot document from the state's three owners.

    The adapter's slice comes from the port's own `snapshot`; the clock and the
    stream are read at their current positions; the engine's half is normalised to
    a JSON-safe form and checked to name every enumerated field.
    """
    _require_fields(engine_state, ENGINE_STATE_FIELDS)
    return Snapshot(
        snapshot_version=SNAPSHOT_VERSION,
        entities=adapter.snapshot().entities,
        engine_state=_require_mapping(_json_safe(engine_state), "engine_state"),
        clock=clock.now.isoformat(),
        random_seed=stream.seed,
        random_position=stream.position,
    )


def restore_snapshot(snapshot: Snapshot) -> Simulation:
    """Rebuild a run from a snapshot, refusing a version we do not understand."""
    if snapshot.snapshot_version != SNAPSHOT_VERSION:
        raise UnsupportedSnapshotVersionError(snapshot.snapshot_version)

    clock = VirtualClock.started_at(datetime.fromisoformat(snapshot.clock))
    stream = RandomStream.restored(snapshot.random_seed, snapshot.random_position)
    adapter = FakeHouseAdapter(clock=clock, random_stream=stream)
    for entry in snapshot.entities:
        _restore_entity(adapter, entry)
    return Simulation(
        adapter=adapter,
        clock=clock,
        stream=stream,
        engine_state=dict(snapshot.engine_state),
    )


def _empty_engine_state() -> dict[str, object]:
    return {
        "bindings": {},
        "modes": [],
        "enable_flags": {},
        "override_records": {"records": [], "lapsed": []},
        "rate_limit_windows": {},
        "room_timers": [],
        "installed_packs": {},
    }


def _restore_entity(adapter: FakeHouseAdapter, entry: EntitySnapshot) -> None:
    """Re-create one entity through the port's own control-facing operations.

    Two conditions are rebuilt and they are not the same one. `add_entity`
    supplies the *startup* condition -- the state and attributes a restart
    returns the entity to -- and the entity's live state and availability then
    arrive through the operations that produce them: `set_availability` for the
    availability, and, for the state, whichever operation produces the recorded
    last writer -- an actuation for `user`, `engine` and `world`, a fault for
    `fault`. An entity whose live state already equals its startup state is left
    at that state by the add, so the second call exists to re-establish the
    origin rather than the state -- and when the recorded origin is `world`,
    which is the origin the add itself recorded, there is nothing left to
    re-establish and the second call is skipped. Nothing private is reached, so
    a restored house cannot hold a state the adapter could not have produced.
    """
    adapter.add_entity(
        entry.entity_id,
        entry.startup_state,
        attributes=entry.startup_attributes,
        context=ChangeContext.world(),
    )
    if not entry.available:
        adapter.set_availability(
            entry.entity_id, available=False, context=ChangeContext.world()
        )
    if entry.state == entry.startup_state and entry.last_origin is ChangeOrigin.WORLD:
        return
    if entry.last_origin is ChangeOrigin.FAULT:
        adapter.inject_fault(
            entry.entity_id, Fault(state=entry.state), context=ChangeContext.fault()
        )
    else:
        adapter.actuate(
            entry.entity_id, entry.state, context=_origin_context(entry.last_origin)
        )


def _origin_context(origin: ChangeOrigin) -> ChangeContext:
    """The context that re-establishes `origin` through an actuation.

    Every origin but `fault` is produced by `actuate` with that context; `fault`
    is produced by `inject_fault` instead, so a caller outside the fault case
    passing it here is a bug rather than a state to build.
    """
    if origin is ChangeOrigin.USER:
        return ChangeContext.user()
    if origin is ChangeOrigin.ENGINE:
        return ChangeContext.engine()
    if origin is ChangeOrigin.WORLD:
        return ChangeContext.world()
    raise AssertionError(f"no actuation produces the {origin!r} origin")


# --------------------------------------------------------------------------
# Document shaping and parsing. The helpers fail by naming the field, never by
# defaulting it.
# --------------------------------------------------------------------------


def _entity_document(entry: EntitySnapshot) -> dict[str, object]:
    return {
        "entity_id": entry.entity_id,
        "state": entry.state,
        "attributes": {
            key: _json_safe(value) for key, value in entry.attributes.items()
        },
        "available": entry.available,
        "last_origin": str(entry.last_origin),
        "startup_state": entry.startup_state,
        "startup_attributes": {
            key: _json_safe(value) for key, value in entry.startup_attributes.items()
        },
    }


def _entity_from_document(item: object) -> EntitySnapshot:
    document = _require_mapping(item, "entity")
    _require_fields(
        document,
        (
            "entity_id",
            "state",
            "attributes",
            "available",
            "startup_state",
            "startup_attributes",
        ),
    )
    return EntitySnapshot(
        entity_id=_require_str(document["entity_id"], "entity_id"),
        state=_require_str(document["state"], "state"),
        attributes=_require_mapping(document["attributes"], "attributes"),
        available=_require_bool(document["available"], "available"),
        last_origin=_require_origin(document.get("last_origin")),
        startup_state=_require_str(document["startup_state"], "startup_state"),
        startup_attributes=_require_mapping(
            document["startup_attributes"], "startup_attributes"
        ),
    )


def _require_origin(value: object) -> ChangeOrigin:
    if value is None:
        # An older document that predates the field, or a hand-written one: the
        # origin is the adapter's own record of the last writer, and dropping it
        # would silently un-override a manually-set lamp, so its absence is a
        # missing enumerated fact rather than a default.
        raise IncompleteSnapshotError(["entity.last_origin"])
    if not isinstance(value, str):
        raise SnapshotError(f"entity origin {value!r} is not a string")
    try:
        return ChangeOrigin(value)
    except ValueError as error:
        raise SnapshotError(f"unknown change origin {value!r}") from error


def _json_safe(value: object) -> object:
    """A JSON-serialisable form of `value`, used so two equal states serialise equal.

    The engine's half is opaque here, so this normalises the containers JSON
    cannot take -- a mapping proxy, a tuple, a set -- without inspecting the
    values. A set is sorted by `repr` so its order is not the hash order, which is
    what makes two snapshots of the same state equal documents.
    """
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (tuple, list)):
        sequence = cast("Sequence[object]", value)
        return [_json_safe(item) for item in sequence]
    if isinstance(value, (set, frozenset)):
        items = cast("frozenset[object]", value)
        return [_json_safe(item) for item in sorted(items, key=repr)]
    return value


def _require_fields(document: Mapping[str, object], fields: Sequence[str]) -> None:
    missing = [field for field in fields if field not in document]
    if missing:
        raise IncompleteSnapshotError(missing)


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SnapshotError(f"snapshot field {field!r} is not an object")
    return cast("Mapping[str, object]", value)


def _require_sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SnapshotError(f"snapshot field {field!r} is not a list")
    return cast("Sequence[object]", value)


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise SnapshotError(f"snapshot field {field!r} is not a string")
    return value


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotError(f"snapshot field {field!r} is not an integer")
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotError(f"snapshot field {field!r} is not a boolean")
    return value
