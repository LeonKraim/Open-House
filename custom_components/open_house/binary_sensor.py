"""A room's occupancy, as the room reads it right now.

This is the entity the startup-safety rule exists for. Home Assistant's
`binary_sensor` has three answers -- on, off, and unknown -- and this one uses
all three: `is_on` is `True` when the room's bound motion sensor reads motion,
`False` when it reads clear, and `None` when the room has no sensor or the sensor
is unavailable. `None` is the honest answer, and the reason the rule is stated as
a rule: a room whose sensor has dropped off the network is not empty, it is
unreadable, and reporting it `False` would let an absence shutdown empty a room a
person is sitting in.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_OCCUPIED
from .entity import OpenHouseRoomEntity
from .runtime import OpenHouseRuntime, RoomRuntime

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the occupancy sensor for every room of the entry."""
    runtime: OpenHouseRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoomOccupiedSensor(room, entry.entry_id) for room in runtime.rooms.values()
    )


class RoomOccupiedSensor(OpenHouseRoomEntity, BinarySensorEntity):
    """Whether the room is occupied, unknown, or known to be empty."""

    _attr_translation_key = ENTITY_OCCUPIED
    #: Occupancy is the device class, so Home Assistant renders the unknown state
    #: as unknown rather than as a bare off.
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, room: RoomRuntime, entry_id: str) -> None:
        super().__init__(room, entry_id, ENTITY_OCCUPIED)

    @property
    def is_on(self) -> bool | None:
        """`True`/`False` when readable, `None` when the room cannot be read."""
        return self._room.occupied
