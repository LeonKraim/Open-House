"""The live shape of one configured house: its rooms and what they mean now.

Home Assistant's entities are the UI; they are not the state. This module is
where the state actually lives -- one `RoomRuntime` per room subentry -- and the
entities are thin views onto it. A `select` writes `mode` here and redraws; a
`binary_sensor` reads `occupied` here; and a change fans out to every listener,
so the mode select and any other view of the same room update together instead
of each holding a private copy that can drift.

**Occupancy is a reading, not a memory.** `occupied` is derived from the room's
bound `motion_sensor`, through the same `HaTransport` seam the adapter uses, and
it is deliberately `bool | None`: `None` means *unknown* -- the room has no bound
sensor, or the bound one is unavailable -- and that is a different answer from
`False`. The integration never reports a room clear because its sensor went away,
which is the startup-safety rule ("unavailable is not off") applied to the one
entity where getting it wrong empties a house of its lights.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ha_adapter.transport import HaState

from .const import MODES, PROFILES

__all__ = ["OpenHouseRuntime", "RoomRuntime"]

#: The `motion_sensor` slot is the one slot whose reading becomes an entity
#: state rather than a binding the engine acts on, and the slot name comes from
#: `catalog/slots.yaml` so the flow's guess and this read agree.
OCCUPANCY_SLOT = "motion_sensor"

#: The state a Home Assistant `binary_sensor` reports when it is on. A motion
#: sensor is `on` when it reads motion; anything else -- `off`, a device class's
#: own string -- is not motion.
_ON_STATE = "on"


@dataclass(slots=True)
class RoomRuntime:
    """One room's state, and the listeners that show it.

    `read_state` is the transport's one-entity read (`HaTransport.state`), so
    occupancy is re-read from the house each time it is asked for rather than
    cached, and an unavailable sensor is visible as unavailable.
    """

    area_id: str
    room_type: str
    bindings: Mapping[str, str]
    read_state: Callable[[str], HaState | None]
    #: The area's own name, for a device page a person reads. Kept beside the id
    #: rather than looked up per render, because the registry read belongs to
    #: setup and a room's name does not change under its entities.
    name: str = ""
    mode: str = MODES[0]
    profile: str = PROFILES[0]
    auto_lighting: bool = True
    _listeners: list[Callable[[], None]] = field(
        default_factory=list[Callable[[], None]], init=False
    )

    @property
    def occupancy_entity_id(self) -> str | None:
        """The entity this room reads occupancy from, or `None` when unbound."""
        return self.bindings.get(OCCUPANCY_SLOT)

    @property
    def occupied(self) -> bool | None:
        """Whether the room is occupied: `True`, `False`, or `None` (unknown).

        `None` is not a stand-in for `False`. It is returned when the room has no
        bound motion sensor or the bound one is unavailable, and every caller
        treats it as "we do not know", never as "nobody is here".
        """
        entity_id = self.occupancy_entity_id
        if entity_id is None:
            return None
        view = self.read_state(entity_id)
        if view is None or not view.available:
            return None
        return view.state == _ON_STATE

    def listen(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when this room's state changes."""
        self._listeners.append(callback)

    def notify(self) -> None:
        """Tell every listener the room changed, so each redraws itself."""
        for callback in tuple(self._listeners):
            callback()


@dataclass(slots=True)
class OpenHouseRuntime:
    """Every room of one config entry, keyed by the subentry that describes it."""

    entry_id: str
    rooms: Mapping[str, RoomRuntime]

    def room(self, subentry_id: str) -> RoomRuntime | None:
        """One room by the id of the subentry that made it."""
        return self.rooms.get(subentry_id)
