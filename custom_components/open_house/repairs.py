"""The two Repairs this integration raises, and the one thing they must say.

Repairs are how Home Assistant tells a person something needs them. Open House
has exactly one class of thing to say: a room cannot see what it needs to, and
the consequence is *unknown*, not *off*. A room with no motion sensor bound
reports `occupied = None`; a room whose bound sensor has gone unavailable does
the same. Both are silence in the same way -- the room's lighting will not react
to a person, and will not react to an empty room either -- and a person who only
saw the switch would read the silence as "the room is clear".

Both are raised as non-fixable issues because the fix is not a button: the person
picks a device in the room's settings, or they plug the sensor back in. The issue
carries the room so the message can name it, and it is deleted the moment the
room can see again -- a repair that lingers after its cause is gone trains people
to ignore repairs.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .runtime import OpenHouseRuntime, RoomRuntime

__all__ = [
    "NO_OCCUPANCY_SENSOR",
    "OCCUPANCY_SENSOR_UNAVAILABLE",
    "async_sync_startup_issues",
    "issue_id",
]

#: The issue a room raises when it has no way to read occupancy, and the one it
#: raises when the way it has is not answering. Two keys rather than one because
#: the person's action differs: bind a device, versus check the device.
NO_OCCUPANCY_SENSOR = "no_occupancy_sensor"
OCCUPANCY_SENSOR_UNAVAILABLE = "occupancy_sensor_unavailable"


def issue_id(kind: str, area_id: str) -> str:
    """A stable id, so re-checking updates the issue instead of duplicating it.

    Public because the Health tab has to name the Repairs a person can click
    through to: a panel that reconstructed this string would be a second copy of
    the format, and the two would drift the first time either was touched --
    silently, because a wrong issue id is not an error, it is a link that goes
    nowhere.
    """
    return f"{kind}_{area_id}"


def async_sync_startup_issues(
    hass: HomeAssistant, entry_id: str, runtime: OpenHouseRuntime
) -> None:
    """Raise an issue for every room that cannot currently read occupancy.

    Called at setup and again whenever a subentry changes, so the state of the
    repairs always matches the state of the rooms: each room is checked, the
    issue it needs is created (idempotently), and the issue it no longer needs is
    deleted. Nothing here is a decision -- the decision is the room's binding,
    already made -- so this only reports what is already true.
    """
    for subentry_id, room in runtime.rooms.items():
        _sync_room(hass, entry_id, subentry_id, room)


def _sync_room(
    hass: HomeAssistant, entry_id: str, subentry_id: str, room: RoomRuntime
) -> None:
    """Reconcile one room's two possible issues against its current reading."""
    missing = issue_id(NO_OCCUPANCY_SENSOR, room.area_id)
    unavailable = issue_id(OCCUPANCY_SENSOR_UNAVAILABLE, room.area_id)

    if room.occupancy_entity_id is None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            missing,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=NO_OCCUPANCY_SENSOR,
            translation_placeholders={"room": room.area_id},
            data={"entry_id": entry_id, "subentry_id": subentry_id},
        )
        ir.async_delete_issue(hass, DOMAIN, unavailable)
        return

    if room.occupied is None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            unavailable,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=OCCUPANCY_SENSOR_UNAVAILABLE,
            translation_placeholders={
                "room": room.area_id,
                "entity_id": room.occupancy_entity_id,
            },
            data={"entry_id": entry_id, "subentry_id": subentry_id},
        )
        ir.async_delete_issue(hass, DOMAIN, missing)
        return

    ir.async_delete_issue(hass, DOMAIN, missing)
    ir.async_delete_issue(hass, DOMAIN, unavailable)
