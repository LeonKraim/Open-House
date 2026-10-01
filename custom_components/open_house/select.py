"""A room's two selects: which mode it is in, and which profile it runs.

Both are the same shape -- choose one string from a fixed list -- so they share
one base and differ only in which value of the room's runtime they read and
write. The lists are the integration's defaults (`const.MODES`, `const.PROFILES`)
until a pack supplies the house's own; the entity reports whichever list is in
force, so replacing the vocabulary does not strand a person on an option the
select no longer offers.

**Choosing is all these do.** Setting a mode records the choice on the room's
runtime and redraws; it does not call a service or move a light. Acting on a mode
is the engine's decision, made behind the adapter, and an entity that acted here
would be a second place the house's behaviour was decided.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENTITY_MODE, ENTITY_PROFILE, MODES, PROFILES
from .entity import OpenHouseRoomEntity
from .runtime import OpenHouseRuntime, RoomRuntime

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the mode and profile select for every room of the entry."""
    runtime: OpenHouseRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for room in runtime.rooms.values():
        entities.append(RoomModeSelect(room, entry.entry_id))
        entities.append(RoomProfileSelect(room, entry.entry_id))
    async_add_entities(entities)


class _RoomChoiceSelect(OpenHouseRoomEntity, SelectEntity):
    """A select whose value is one string field of the room's runtime."""

    #: The choices offered, in order.
    _vocabulary: tuple[str, ...] = ()
    #: The title key the entity's name is translated from.
    _attr_translation_key: str

    @property
    def options(self) -> list[str]:
        """The choices the select offers, in their declared order."""
        return list(self._vocabulary)

    @property
    def current_option(self) -> str:
        """The room's chosen value, or the first offered when it is off-list.

        A value that is no longer offered -- a pack replaced the vocabulary while
        the room was set to a word it dropped -- reports as the first option
        rather than raising or showing blank, which is what a person sees and
        what the next selection moves away from.
        """
        chosen = self._read()
        if chosen in self._vocabulary:
            return chosen
        return self._vocabulary[0]

    async def async_select_option(self, option: str) -> None:
        """Record the choice on the room and let every view of it redraw."""
        self._write(option)
        self._room.notify()

    def _read(self) -> str:
        """The room's current value for this select."""
        raise NotImplementedError

    def _write(self, option: str) -> None:
        """Record the chosen value on the room."""
        raise NotImplementedError


class RoomModeSelect(_RoomChoiceSelect):
    """The room's mode: Home, Away, Sleep, or whatever a pack names."""

    _attr_translation_key = ENTITY_MODE
    _attr_icon = "mdi:home-variant"
    _vocabulary = MODES

    def __init__(self, room: RoomRuntime, entry_id: str) -> None:
        super().__init__(room, entry_id, ENTITY_MODE)

    def _read(self) -> str:
        return self._room.mode

    def _write(self, option: str) -> None:
        self._room.mode = option


class RoomProfileSelect(_RoomChoiceSelect):
    """The room's profile: which behaviour set the engine should run for it."""

    _attr_translation_key = ENTITY_PROFILE
    _attr_icon = "mdi:account-details"
    _vocabulary = PROFILES

    def __init__(self, room: RoomRuntime, entry_id: str) -> None:
        super().__init__(room, entry_id, ENTITY_PROFILE)

    def _read(self) -> str:
        return self._room.profile

    def _write(self, option: str) -> None:
        self._room.profile = option
