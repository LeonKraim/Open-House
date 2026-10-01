"""A room's auto-lighting switch: whether the engine may light this room for you.

The switch is the room's *permission*, not its lamp. Turning it off says "do not
run this room's lighting for me"; it does not turn the lights off, and turning it
on does not turn them on. It is the one control a person reaches for when a room
is being lit at the wrong times, and keeping it a permission rather than an
actuation is what makes it safe to flip without watching the room.

Like the selects, it records the choice on the room's runtime and redraws;
whether anything acts on the choice is the engine's decision, behind the adapter.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_AUTO_LIGHTING
from .entity import OpenHouseRoomEntity
from .runtime import OpenHouseRuntime, RoomRuntime

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the auto-lighting switch for every room of the entry."""
    runtime: OpenHouseRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoomAutoLightingSwitch(room, entry.entry_id) for room in runtime.rooms.values()
    )


class RoomAutoLightingSwitch(OpenHouseRoomEntity, SwitchEntity):
    """Whether the room's lighting may be run for the people in it."""

    _attr_translation_key = ENTITY_AUTO_LIGHTING
    _attr_icon = "mdi:lightbulb-auto"

    def __init__(self, room: RoomRuntime, entry_id: str) -> None:
        super().__init__(room, entry_id, ENTITY_AUTO_LIGHTING)

    @property
    def is_on(self) -> bool:
        """Whether the room's lighting may be run for it."""
        return self._room.auto_lighting

    async def async_turn_on(self, **kwargs: object) -> None:
        """Allow the room's lighting to be run, and redraw."""
        self._room.auto_lighting = True
        self._room.notify()

    async def async_turn_off(self, **kwargs: object) -> None:
        """Stop the room's lighting being run for it, and redraw."""
        self._room.auto_lighting = False
        self._room.notify()
