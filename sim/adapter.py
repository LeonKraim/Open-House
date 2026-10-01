"""`FakeHouseAdapter`: the port implemented over a mock entity registry.

This is the first subject the `HouseAdapter` contract suite is held against
(`tests/adapter_subjects.py`), and it is a *pure* implementation of the port: it
adds no public method the port does not declare, so the engine can reach the fake
only through the seam the port exists to be. The alternative -- a fake with a
convenience method the suite never exercises, such as `make_user_action` beside
`actuate` -- was rejected because the engine would happily depend on it, and the
Home Assistant adapter Phase 4 may not have that convenience; the Phase 4
adapter would then fail to satisfy a port the engine only appeared to use
(`specs/simulation/spec.md`, "The fake carries a private door").

Three behaviours are the fake's own to define, and each is stated because a
reader would otherwise have to infer it from the code:

- **Restart returns the house to a defined startup condition.** Each entity
  remembers the state and attributes it was *added* with, and a restart restores
  that condition. A fixture that models a stateless light -- one that cannot
  confirm its own state at power-on -- adds it in that unconfirmed state, so the
  startup condition is honest rather than a fiction of the last command
  (`catalog/edge_cases.yaml`, "A light that reports no state of its own").
  Availability is deliberately *not* reset: an integration that has not
  reconnected is unavailable after a restart, not silently switched off, which is
  the unavailable-is-not-off rule in its second place.
- **Only an actuation may carry a `user` origin.** `specs/house-adapter/spec.md`
  requires that no availability change, fault, removal or restart ever *produces*
  a `user`-origin change; the fake enforces that by rejecting such a context
  rather than trusting its callers, because an origin a scenario cannot trust is
  worse than no origin.
- **The read never fabricates a state.** Availability is a field apart from
  state, so an unavailable lamp keeps its last state and a read of an absent
  entity raises rather than answering `off`.

The fake holds the run's `VirtualClock` and `RandomStream` (`specs/simulation`,
"The fake is constructed with its substrate") even though none of the port's
operations read them today: they are the run's one time source and one randomness
source, and a fixture built through the fake records them so the composition root
can cut and resume a run without a second source of either.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from engine.adapter import (
    AdapterError,
    AdapterSnapshot,
    ChangeContext,
    ChangeOrigin,
    EntitySnapshot,
    EntityView,
    Fault,
    UnknownEntityError,
    validate_entity_id,
)

if TYPE_CHECKING:
    from sim.clock import VirtualClock
    from sim.entropy import RandomStream


@dataclass(slots=True)
class _MockEntity:
    """One mocked device: its live state and the condition it starts up in.

    `state`, `attributes`, `available` and `last_origin` are what a read returns.
    `startup_state` and `startup_attributes` are the *configuration* captured when
    the entity was added, which is what a restart returns the entity to; keeping
    them separate from the live fields is what lets a stateless light be added in
    its unconfirmed condition and then be driven to a confirmed one without the
    restart forgetting which was which.
    """

    entity_id: str
    state: str
    attributes: dict[str, object]
    available: bool
    last_origin: ChangeOrigin
    startup_state: str
    startup_attributes: dict[str, object]


class OriginNotAllowedError(AdapterError):
    """A non-actuation mutation was handed a `user`-origin context.

    `specs/house-adapter/spec.md` makes only the manual user-action path -- an
    actuation carrying `user` -- able to produce a `user`-origin change. The fake
    fails closed here rather than recording a `user` origin a scenario never
    intended, because override detection reads that field and a false `user`
    would suppress exactly the behaviour being asserted.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} cannot produce a `user`-origin change; only the manual "
            "user-action path (an actuation carrying `user`) may"
        )
        self.operation = operation


@dataclass(slots=True)
class FakeHouseAdapter:
    """The `HouseAdapter` port over an in-memory registry of mocked devices.

    Construct it with the run's clock and random stream. Every operation the port
    declares is implemented here and nothing more; the registry is a plain dict
    keyed by entity id, so adding, removing and enumerating are direct.
    """

    clock: VirtualClock
    random_stream: RandomStream
    _entities: dict[str, _MockEntity] = field(default_factory=dict[str, _MockEntity])

    # -- Engine-facing ------------------------------------------------------

    def read_entity(self, entity_id: str) -> EntityView:
        """Return the entity's domain, state, attributes, availability and origin."""
        entity = self._entity(entity_id)
        return EntityView(
            entity_id=entity.entity_id,
            state=entity.state,
            attributes=_frozen(entity.attributes),
            available=entity.available,
            last_origin=entity.last_origin,
        )

    def list_entities(self) -> Sequence[str]:
        """The ids present, sorted so two enumerations of one house are equal."""
        return tuple(sorted(self._entities))

    def actuate(self, entity_id: str, state: str, *, context: ChangeContext) -> None:
        """Write `state`, leaving availability untouched.

        This is the one operation that may carry a `user` origin, because it is
        the operation the control surface's `user_action` reaches the port
        through; every other mutation rejects a `user` context.
        """
        entity = self._entity(entity_id)
        entity.state = state
        entity.last_origin = context.origin

    def snapshot(self) -> AdapterSnapshot:
        """The adapter's own slice: the entities, sorted by id for a stable form.

        Both conditions travel: the live state and attributes, and the startup
        state and attributes `restart` returns the entity to. Capturing only the
        live condition would make a restored restart return the entity to
        wherever it had been driven rather than to how it was configured.
        """
        return AdapterSnapshot(
            entities=tuple(
                EntitySnapshot(
                    entity_id=entity.entity_id,
                    state=entity.state,
                    attributes=_frozen(entity.attributes),
                    available=entity.available,
                    last_origin=entity.last_origin,
                    startup_state=entity.startup_state,
                    startup_attributes=_frozen(entity.startup_attributes),
                )
                for entity in sorted(self._entities.values(), key=_by_id)
            )
        )

    # -- Control-facing -----------------------------------------------------

    def add_entity(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        context: ChangeContext,
    ) -> None:
        """Add an entity, recording the state it will start up in.

        Adding an id the house already holds is refused rather than silently
        replacing it: a fixture that adds a device twice is a fixture bug, and a
        silent clobber would hide it behind a passing scenario.
        """
        self._require_non_user("add_entity", context)
        validate_entity_id(entity_id)
        if entity_id in self._entities:
            raise AdapterError(f"the house already holds an entity {entity_id!r}")
        supplied = dict(attributes) if attributes is not None else {}
        self._entities[entity_id] = _MockEntity(
            entity_id=entity_id,
            state=state,
            attributes=supplied,
            available=True,
            last_origin=context.origin,
            startup_state=state,
            startup_attributes=dict(supplied),
        )

    def remove_entity(self, entity_id: str, *, context: ChangeContext) -> None:
        """Remove an entity; it then reads as absent and is not enumerated."""
        self._require_non_user("remove_entity", context)
        self._entity(entity_id)
        del self._entities[entity_id]

    def set_availability(
        self, entity_id: str, *, available: bool, context: ChangeContext
    ) -> None:
        """Mark the entity available or unavailable, leaving its state readable."""
        self._require_non_user("set_availability", context)
        entity = self._entity(entity_id)
        entity.available = available
        entity.last_origin = context.origin

    def inject_fault(
        self, entity_id: str, fault: Fault, *, context: ChangeContext
    ) -> None:
        """Apply `fault`'s changes and record the `fault` origin the caller passed."""
        self._require_non_user("inject_fault", context)
        entity = self._entity(entity_id)
        if fault.state is not None:
            entity.state = fault.state
        if fault.available is not None:
            entity.available = fault.available
        entity.last_origin = context.origin

    def restart(self, *, context: ChangeContext) -> None:
        """Return every entity to the state and attributes it was added with.

        Availability stands apart from the startup condition: an entity marked
        unavailable is unavailable afterwards, because the integration that
        stopped reporting has not reconnected merely because the house reloaded.
        The origin becomes the restart's own context, so the change is observable
        to the engine as an operation rather than as silence.
        """
        self._require_non_user("restart", context)
        for entity in self._entities.values():
            entity.state = entity.startup_state
            entity.attributes = dict(entity.startup_attributes)
            entity.last_origin = context.origin

    # -- Internals ----------------------------------------------------------

    def _entity(self, entity_id: str) -> _MockEntity:
        validate_entity_id(entity_id)
        entity = self._entities.get(entity_id)
        if entity is None:
            raise UnknownEntityError(entity_id)
        return entity

    @staticmethod
    def _require_non_user(operation: str, context: ChangeContext) -> None:
        if context.origin is ChangeOrigin.USER:
            raise OriginNotAllowedError(operation)


def _by_id(entity: _MockEntity) -> str:
    return entity.entity_id


def _frozen(attributes: Mapping[str, object]) -> Mapping[str, object]:
    """A read-only copy of an entity's attributes.

    A copy rather than the live dict, and read-only rather than a plain dict, so
    a caller cannot reach back into the registry through the mapping a read
    handed it -- the read is a snapshot of the entity, not a handle on it.
    """
    return MappingProxyType(dict(attributes))
