"""The base every per-room entity shares: identity, grouping, and redraws.

Each room of the integration shows four entities, and every one of them needs
the same three things: a unique id that cannot collide with another room's, a
device so the four group together on one page, and a subscription to the room's
runtime so a change made through one entity redraws the others. Writing those
three once here keeps the four platform modules down to the part that is
actually different -- what each entity reads and writes.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .runtime import RoomRuntime

__all__ = ["OpenHouseRoomEntity"]


class OpenHouseRoomEntity(Entity):
    """One of a room's entities, grouped under the room's device.

    Entities push their state rather than poll it: the room's runtime is the
    source, a listener is registered when the entity is added, and
    `_on_room_change` redraws. `should_poll` is off because there is nothing to
    poll -- the entity's value is answered synchronously from the room.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, room: RoomRuntime, entry_id: str, key: str) -> None:
        self._room = room
        self._entry_id = entry_id
        #: The area id, not the subentry id, is what makes the id stable across a
        #: reload: a subentry is recreated on reload, an area is not.
        self._attr_unique_id = f"{entry_id}_{room.area_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """The room, as the device all four of its entities hang from."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._room.area_id)},
            name=self._room.name or self._room.area_id,
            manufacturer="Open House",
            model=self._room.room_type,
        )

    async def async_added_to_hass(self) -> None:
        """Redraw this entity whenever the room it belongs to changes."""
        self._room.listen(self._on_room_change)

    def _on_room_change(self) -> None:
        self.async_write_ha_state()
