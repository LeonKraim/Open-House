"""`HassTransport`: the `HaTransport` seam over the running Home Assistant.

The adapter port has three implementations of its transport now, and each exists
for a different caller: `RestTransport` speaks to an instance from outside,
`FakeHaTransport` is the contract suite's in-memory house, and this one is the
integration's -- it reads and writes the `hass` object the integration is already
holding, with no socket and no token, because it *is* inside the process.

It reconciles Home Assistant's single state-or-availability field into the port's
two exactly as the other transports do: `hass.states` reports `"unavailable"` as
the state of an entity whose integration has stopped, and this class remembers
the last state that entity reported while available, so a read through the port
still yields that state beside `available=False`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.const import STATE_UNAVAILABLE

from ha_adapter.transport import HaState

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, State


class HassTransport:
    """A `HaTransport` over `hass.states`, in process, with no network."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        #: The last state each entity reported while available, keyed by id, so an
        #: entity Home Assistant now calls `"unavailable"` can still be read in
        #: the port's terms (`HaTransport`'s rule: unavailable keeps the state).
        self._last_known: dict[str, str] = {}

    def states(self) -> Sequence[HaState]:
        """Every state Home Assistant holds, in the port's terms, sorted by id."""
        return tuple(
            sorted(
                (self._to_ha_state(state) for state in self._hass.states.async_all()),
                key=lambda state: state.entity_id,
            )
        )

    def state(self, entity_id: str) -> HaState | None:
        """One entity, or `None` when Home Assistant does not hold it."""
        state = self._hass.states.get(entity_id)
        return None if state is None else self._to_ha_state(state)

    def set_state(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        available: bool | None = None,
        user_id: str | None = None,
    ) -> HaState:
        """Set a state, and whichever of attributes/availability were given.

        Availability is expressed the way Home Assistant expresses it -- the
        state `"unavailable"` -- and the memory above is what keeps the port's
        "unavailable keeps its state" true. A write that does not name
        availability leaves it alone, so `actuate` on an unavailable entity
        changes what it will report next without claiming it is back.
        """
        del user_id
        previous = self._hass.states.get(entity_id)
        if available is False or (
            available is None
            and previous is not None
            and previous.state == STATE_UNAVAILABLE
        ):
            merged = (
                dict(attributes)
                if attributes is not None
                else (dict(previous.attributes) if previous is not None else {})
            )
            self._hass.states.async_set(entity_id, STATE_UNAVAILABLE, merged)
            self._last_known[entity_id] = state
            return HaState(entity_id, state, MappingProxyType(merged), False, None)

        merged = (
            dict(attributes)
            if attributes is not None
            else (dict(previous.attributes) if previous is not None else {})
        )
        self._hass.states.async_set(entity_id, state, merged)
        self._last_known[entity_id] = state
        return HaState(entity_id, state, MappingProxyType(merged), True, None)

    def remove(self, entity_id: str) -> bool:
        """Remove an entity, returning whether it was there to remove."""
        if self._hass.states.get(entity_id) is None:
            return False
        self._hass.states.async_remove(entity_id)
        self._last_known.pop(entity_id, None)
        return True

    def _to_ha_state(self, state: State) -> HaState:
        """One `hass` state, projected to `HaState`."""
        attributes: Mapping[str, object] = MappingProxyType(dict(state.attributes))
        user_id = getattr(state.context, "user_id", None)
        if state.state == STATE_UNAVAILABLE:
            known = self._last_known.get(state.entity_id)
            return HaState(
                entity_id=state.entity_id,
                state=known if known is not None else STATE_UNAVAILABLE,
                attributes=attributes,
                available=False,
                user_id=user_id if isinstance(user_id, str) else None,
            )
        self._last_known[state.entity_id] = state.state
        return HaState(
            entity_id=state.entity_id,
            state=state.state,
            attributes=attributes,
            available=True,
            user_id=user_id if isinstance(user_id, str) else None,
        )
