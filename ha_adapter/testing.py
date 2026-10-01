"""`FakeHaTransport`: an in-memory Home Assistant state machine for tests.

`HAAdapter` is written against a `HaTransport` so that the contract suite can
hold it to the port without a live container. This is the transport that makes
that possible: a dict of entities that answers the same four operations the REST
transport answers, plus the two doors a test needs and the REST transport has no
use for -- `external_change`, which simulates a change the adapter did not make
(an automation, a hand at the wall panel), and a way to read back what was
written.

It is a shipped module rather than a test file because two test modules build it
and because it is the second half of a claim the tests make about the first:
`ha_adapter/transport.py`'s reconciliation of Home Assistant's single
state-or-availability field into the port's two is only meaningful if the fake
models the single field the way the wire does. `set_state(..., available=False)`
sets the entity's state to `"unavailable"` here, exactly as a real instance would
report, and it is `HAAdapter` -- never the fake -- that remembers the last known
state. A fake that kept availability as its own bit would let the adapter pass
the contract without ever doing the reconciliation the contract is about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from .adapter import HAAdapter
from .transport import UNAVAILABLE_STATE, HaState

__all__ = ["FakeHaTransport", "build_adapter"]


@dataclass(slots=True)
class _Entity:
    """One entity in the fake state machine, in Home Assistant's own terms."""

    state: str
    attributes: dict[str, object]
    #: The last state the entity reported while available. The fake keeps it for
    #: the same reason the wire forgets it and the REST transport keeps it: an
    #: entity Home Assistant calls `"unavailable"` no longer carries a state, and
    #: a transport that answered `"unavailable"` as the state would hand the
    #: engine a state it could act on.
    last_known: str
    user_id: str | None = None

    @property
    def available(self) -> bool:
        return self.state != UNAVAILABLE_STATE


@dataclass(slots=True)
class FakeHaTransport:
    """A `HaTransport` over an in-memory registry of Home Assistant entities."""

    _entities: dict[str, _Entity] = field(default_factory=dict[str, _Entity])

    # -- HaTransport --------------------------------------------------------

    def states(self) -> Sequence[HaState]:
        """Every entity, sorted by id so two enumerations agree."""
        return tuple(
            self._view(entity_id, self._entities[entity_id])
            for entity_id in sorted(self._entities)
        )

    def state(self, entity_id: str) -> HaState | None:
        """One entity, or `None` when the fake does not hold it."""
        entity = self._entities.get(entity_id)
        return None if entity is None else self._view(entity_id, entity)

    def set_state(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        available: bool | None = None,
        user_id: str | None = None,
    ) -> HaState:
        """Write `state` (creating the entity if new), and what else was given.

        `available=False` is written as the wire's `"unavailable"` state, since
        that is what a real instance reports, and the state the caller wrote is
        remembered so the reconciliation this seam performs has something to
        reconcile. Availability is unchanged when `available` is `None` -- a
        plain write to an unavailable entity leaves it unavailable, which is the
        port's rule that an actuation does not touch availability.
        """
        entity = self._entities.get(entity_id)
        if entity is None:
            entity = _Entity(state=state, attributes={}, last_known=state)
            self._entities[entity_id] = entity
        if attributes is not None:
            entity.attributes = dict(attributes)
        # An entity that is already unavailable, or is being made unavailable,
        # keeps the wire's sentinel and remembers the write as its last known
        # state. A write to an unavailable entity therefore does not restore its
        # availability -- the port's `actuate` must not -- while a write that
        # returns it to available, or an ordinary write to an available entity,
        # moves the state.
        if available is False or (
            available is None and entity.state == UNAVAILABLE_STATE
        ):
            entity.last_known = state
            entity.state = UNAVAILABLE_STATE
        else:
            entity.last_known = state
            entity.state = state
        entity.user_id = user_id
        return self._view(entity_id, entity)

    def remove(self, entity_id: str) -> bool:
        """Remove an entity, returning whether it was there to remove."""
        return self._entities.pop(entity_id, None) is not None

    # -- Test doors ---------------------------------------------------------

    def external_change(
        self, entity_id: str, state: str, *, user_id: str | None = None
    ) -> None:
        """Change an entity behind the adapter's back.

        A `user_id` models a person acting (a wall switch, the app); its absence
        models an automation or an integration writing. This is how a test checks
        that `HAAdapter` reads the house's own context for a change it did not
        make, which is the half of origin detection no adapter operation
        exercises.
        """
        entity = self._entities[entity_id]
        entity.state = state
        entity.last_known = state
        entity.user_id = user_id

    def make_unavailable(self, entity_id: str) -> None:
        """Take an entity offline behind the adapter's back, as an integration does."""
        self._entities[entity_id].state = UNAVAILABLE_STATE

    def wire_state(self, entity_id: str) -> str:
        """The state string a real instance would report for this entity.

        The reconciliation this seam performs -- Home Assistant's one field into
        the port's two -- is only honest if the fake genuinely *loses* the state
        the wire loses. This door shows the wire's own value, so a test can assert
        that an unavailable entity really carries `"unavailable"` underneath and
        that it is the transport's memory, not a fiction, that answers a read.
        """
        return self._entities[entity_id].state

    # -- Internals ----------------------------------------------------------

    @staticmethod
    def _view(entity_id: str, entity: _Entity) -> HaState:
        available = entity.available
        state = entity.last_known if not available else entity.state
        return HaState(
            entity_id=entity_id,
            state=state,
            attributes=MappingProxyType(dict(entity.attributes)),
            available=available,
            user_id=entity.user_id,
        )


def build_adapter() -> HAAdapter:
    """A fresh `HAAdapter` over an empty fake house.

    The zero-argument factory the contract registry and the adapter's own tests
    build a subject with, so a caller never has to know that a transport is what
    the adapter is constructed from.
    """
    return HAAdapter(transport=FakeHaTransport())
