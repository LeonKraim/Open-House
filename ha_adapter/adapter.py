"""`HAAdapter`: the `HouseAdapter` port over a real Home Assistant instance.

This is the second implementation the port exists for. `sim/adapter.py`'s fake is
the first, and `tests/test_ha_contract.py` points the same contract suite at both
so that the fake stays honest about the house it claims to be. Nothing here
imports `homeassistant`: the adapter talks to the instance through a
`HaTransport` (`ha_adapter/transport.py`), so it loads, and is tested, in a
checkout where Home Assistant is not installed -- the phase's defining
constraint, and the reason the port lives in `engine/` and reaches a `Protocol`
rather than an implementation.

**What the adapter actually does.** A Home Assistant state object and the port's
`EntityView` differ in exactly two ways, and both are the adapter's work:

- **Origin.** Home Assistant records the `user_id` of the context that produced a
  change but has no notion of "the engine wrote this" or "a fault wrote this".
  The port's override detection turns on one fact -- whether the last writer was
  a *user* -- so the adapter keeps it: it records the origin of its own writes
  and, when it reads a change it did not make, derives the origin from the
  context (`user_id` set means a person; unset means the world). That a write
  through Home Assistant's REST API is stamped with the token's user is exactly
  why the memory is needed: without it, every engine write would read back as a
  user action and permanently suppress the behaviour that made it.

- **Startup condition.** The port's `restart` returns the house to a defined
  condition and the snapshot carries that condition beside the live one. Home
  Assistant is the source of truth for a real house and the adapter cannot reset
  one, so `restart` here means what it can honestly mean: re-read the house from
  the instance and re-capture the condition each entity is in as its startup
  condition. An entity still reported unavailable is unavailable afterwards,
  which is the port's rule -- unavailable is not off -- arriving through a
  restart.

Everything else is a straight projection: a read is a read, a write is a write,
and the adapter re-decides nothing the engine already decided (`house-adapter`:
the safety veto is the engine's action gate and the port applies what it is
handed).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

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

from .transport import HaState, HaTransport

__all__ = ["HAAdapter", "OriginNotAllowedError"]


class OriginNotAllowedError(AdapterError):
    """A non-actuation mutation was handed a `user`-origin context.

    Only the manual user-action path -- an actuation carrying `user` -- may
    produce a `user`-origin change (`specs/house-adapter/spec.md`,
    `sim/adapter.py`). The adapter fails closed rather than recording a `user`
    origin a caller never intended, because override detection reads that field
    and a false `user` would suppress exactly the behaviour being asserted.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} cannot produce a `user`-origin change; only the manual "
            "user-action path (an actuation carrying `user`) may"
        )
        self.operation = operation


@dataclass(slots=True)
class HAAdapter:
    """The `HouseAdapter` port over one Home Assistant instance.

    Construct it with a transport. The three caches on the instance are the
    adapter's own state, not the house's: the origin of the last writer of each
    entity, the state the adapter last saw each entity in (so it can tell a
    change it made from one the house made), and the condition each entity starts
    up in (so a snapshot can carry it and a restart can re-capture it). None of
    the three is anything the engine owns, which is what keeps the adapter's
    `snapshot` the adapter's honest slice and not a second copy of engine state.
    """

    transport: HaTransport
    _origin: dict[str, ChangeOrigin] = field(default_factory=dict, init=False)
    _seen_state: dict[str, str] = field(default_factory=dict, init=False)
    _startup_state: dict[str, str] = field(default_factory=dict, init=False)
    _startup_attributes: dict[str, Mapping[str, object]] = field(
        default_factory=dict, init=False
    )

    # -- Engine-facing ------------------------------------------------------

    def read_entity(self, entity_id: str) -> EntityView:
        """Return the entity's domain, state, attributes, availability and origin.

        The origin is the adapter's record when it has one, and the house's
        context when the change was not the adapter's -- which is how an
        automation or a hand at the wall panel is told from the engine's own
        write.
        """
        validate_entity_id(entity_id)
        state = self.transport.state(entity_id)
        if state is None:
            raise UnknownEntityError(entity_id)
        origin = self._observe(entity_id, state)
        return EntityView(
            entity_id=entity_id,
            state=state.state,
            attributes=_frozen(state.attributes),
            available=state.available,
            last_origin=origin,
        )

    def list_entities(self) -> Sequence[str]:
        """The ids the transport holds, sorted so two enumerations agree."""
        return tuple(sorted(state.entity_id for state in self.transport.states()))

    def actuate(self, entity_id: str, state: str, *, context: ChangeContext) -> None:
        """Write `state`, leaving availability untouched.

        The one operation that may carry a `user` origin: it is the operation the
        control surface's `user_action` reaches the port through. Availability is
        left alone by passing `available=None`, so a write to an unavailable
        entity leaves it unavailable -- the port's rule that availability is a
        field apart from state.
        """
        validate_entity_id(entity_id)
        if self.transport.state(entity_id) is None:
            raise UnknownEntityError(entity_id)
        self.transport.set_state(entity_id, state, user_id=None)
        self._record(entity_id, state, context.origin)

    def snapshot(self) -> AdapterSnapshot:
        """The adapter's slice: the entities, with the condition each starts in.

        Both conditions travel: the live state and attributes a read returns, and
        the startup state and attributes a restart re-captures. Capturing only
        the live condition is the mistake the port's `EntitySnapshot` exists to
        prevent.
        """
        entries: list[EntitySnapshot] = []
        for entity_id in self.list_entities():
            view = self.read_entity(entity_id)
            entries.append(
                EntitySnapshot(
                    entity_id=entity_id,
                    state=view.state,
                    attributes=view.attributes,
                    available=view.available,
                    last_origin=view.last_origin,
                    startup_state=self._startup_state.get(entity_id, view.state),
                    startup_attributes=self._startup_attributes.get(
                        entity_id, view.attributes
                    ),
                )
            )
        return AdapterSnapshot(entities=tuple(entries))

    # -- Control-facing -----------------------------------------------------

    def add_entity(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        context: ChangeContext,
    ) -> None:
        """Add an entity, recording the state it starts up in.

        The state and attributes given are also the entity's startup condition,
        so a fixture built through this operation is a fixed configuration rather
        than a starting point the adapter forgets. Adding an id the house already
        holds is refused rather than silently replacing it.
        """
        self._require_non_user("add_entity", context)
        validate_entity_id(entity_id)
        if self.transport.state(entity_id) is not None:
            raise AdapterError(f"the house already holds an entity {entity_id!r}")
        supplied = dict(attributes) if attributes is not None else {}
        self.transport.set_state(entity_id, state, attributes=supplied, available=True)
        self._record(entity_id, state, context.origin)
        self._startup_state[entity_id] = state
        self._startup_attributes[entity_id] = _frozen(supplied)

    def remove_entity(self, entity_id: str, *, context: ChangeContext) -> None:
        """Remove an entity; it then reads as absent and is not enumerated."""
        self._require_non_user("remove_entity", context)
        validate_entity_id(entity_id)
        if not self.transport.remove(entity_id):
            raise UnknownEntityError(entity_id)
        self._forget(entity_id)

    def set_availability(
        self, entity_id: str, *, available: bool, context: ChangeContext
    ) -> None:
        """Mark the entity available or unavailable, leaving its state readable.

        Its own operation, independent of `actuate`: the transport is told to
        change availability only, so the entity's state is untouched and an
        unavailable entity keeps its last known state rather than being reported
        as `off`.
        """
        self._require_non_user("set_availability", context)
        validate_entity_id(entity_id)
        state = self.transport.state(entity_id)
        if state is None:
            raise UnknownEntityError(entity_id)
        self.transport.set_state(entity_id, state.state, available=available)
        self._record(entity_id, state.state, context.origin)

    def inject_fault(
        self, entity_id: str, fault: Fault, *, context: ChangeContext
    ) -> None:
        """Apply `fault`'s changes and record the `fault` origin the caller passed."""
        self._require_non_user("inject_fault", context)
        validate_entity_id(entity_id)
        state = self.transport.state(entity_id)
        if state is None:
            raise UnknownEntityError(entity_id)
        new_state = fault.state if fault.state is not None else state.state
        self.transport.set_state(entity_id, new_state, available=fault.available)
        self._record(entity_id, new_state, context.origin)

    def restart(self, *, context: ChangeContext) -> None:
        """Re-read every entity from the instance and re-capture its startup state.

        A real house is Home Assistant's to run and the adapter cannot reset one;
        what restart means here is that the adapter re-synchronises its view and
        records the condition each entity now reports as the one it starts up in.
        Reproducible across restarts by construction, and never a `user`-origin
        change -- availability is not reset, so an entity whose integration has
        not reconnected stays unavailable, which is unavailable-is-not-off
        holding through a restart.
        """
        self._require_non_user("restart", context)
        for state in self.transport.states():
            self._record(state.entity_id, state.state, context.origin)
            self._startup_state[state.entity_id] = state.state
            self._startup_attributes[state.entity_id] = _frozen(state.attributes)

    # -- Internals ----------------------------------------------------------

    def _observe(self, entity_id: str, state: HaState) -> ChangeOrigin:
        """The origin of the entity's last change, updating the record.

        A first sighting -- an entity the adapter has not touched -- takes its
        origin from the house: a `user_id` on the context means a person, its
        absence means the world. A later sighting whose state has moved since the
        adapter last looked is a change the adapter did not make, so it is read
        the same way; a sighting that has not moved leaves the recorded origin
        alone, which is what keeps an engine write reading back as `engine`.
        """
        previous = self._seen_state.get(entity_id)
        self._seen_state[entity_id] = state.state
        if entity_id not in self._origin:
            origin = (
                ChangeOrigin.USER if state.user_id is not None else ChangeOrigin.WORLD
            )
            self._origin[entity_id] = origin
            self._startup_state.setdefault(entity_id, state.state)
            self._startup_attributes.setdefault(entity_id, _frozen(state.attributes))
            return origin
        if previous != state.state:
            self._origin[entity_id] = (
                ChangeOrigin.USER if state.user_id is not None else ChangeOrigin.WORLD
            )
        return self._origin[entity_id]

    def _record(self, entity_id: str, state: str, origin: ChangeOrigin) -> None:
        """Remember that this entity now reads `state` and this origin wrote it."""
        self._seen_state[entity_id] = state
        self._origin[entity_id] = origin

    def _forget(self, entity_id: str) -> None:
        """Drop every memory of an entity that has left the house."""
        self._origin.pop(entity_id, None)
        self._seen_state.pop(entity_id, None)
        self._startup_state.pop(entity_id, None)
        self._startup_attributes.pop(entity_id, None)

    @staticmethod
    def _require_non_user(operation: str, context: ChangeContext) -> None:
        if context.origin is ChangeOrigin.USER:
            raise OriginNotAllowedError(operation)


def _frozen(attributes: Mapping[str, object]) -> Mapping[str, object]:
    """A read-only copy of an entity's attributes.

    A copy rather than the live mapping, so a caller cannot reach back into the
    transport through what a read handed it -- the read is a snapshot of the
    entity, not a handle on it.
    """
    return MappingProxyType(dict(attributes))
