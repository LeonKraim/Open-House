"""What the integration hands Home Assistant when a person downloads diagnostics.

Diagnostics exist to be pasted into a bug report, so the whole job is to say
enough to reproduce and nothing that is a secret. The config entry's data and the
room subentries are already the person's own house -- area ids, room types and
the entities they bound -- and none of it is a credential, so it is reported
verbatim. What this module adds beyond the entry is the *live* reading: each
room's mode, profile, auto-lighting setting, and occupancy as `occupied` reports
it right now, because "the automation did nothing" is usually answered by seeing
that the room reads `None` where its owner expected `False`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .runtime import OpenHouseRuntime

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

__all__ = ["async_get_config_entry_diagnostics"]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """The config entry, its rooms, and each room's live reading."""
    runtime: OpenHouseRuntime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    rooms: list[dict[str, Any]] = []
    if runtime is not None:
        for subentry_id, room in runtime.rooms.items():
            rooms.append(
                {
                    "subentry_id": subentry_id,
                    "area_id": room.area_id,
                    "room_type": room.room_type,
                    "bindings": dict(room.bindings),
                    "mode": room.mode,
                    "profile": room.profile,
                    "auto_lighting": room.auto_lighting,
                    "occupied": room.occupied,
                    "occupancy_entity_id": room.occupancy_entity_id,
                }
            )
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "version": entry.version,
            "data": dict(entry.data),
            "subentries": {
                subentry_id: {
                    "subentry_type": subentry.subentry_type,
                    "title": subentry.title,
                    "unique_id": subentry.unique_id,
                    "data": dict(subentry.data),
                }
                for subentry_id, subentry in entry.subentries.items()
            },
        },
        "rooms": rooms,
    }
