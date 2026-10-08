"""Names the Open House integration and the four entities it creates per room.

The four per-room entities `spec.txt` fixes -- mode, profile select, occupied
and auto-lighting -- are named as string keys here and shared by every platform
and by the tests, so the entity a `select` creates and the entity a `switch`
creates cannot drift apart by one letter.

`MODES` and `PROFILES` are the *defaults* this skeleton ships: the engine's modes
and profiles are pack data (`catalog/`, Phase 2), and the copy here is what the
integration offers before a pack replaces it. It is deliberately short and
generic -- Home, Away, Sleep, Guest -- and marked as a default rather than a
vocabulary so nobody reads it as the frozen list.
"""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.const import Platform

from . import _bootstrap

DOMAIN = "open_house"

#: The integration's own version, read from the manifest beside this module.
#: Read here rather than through `async_get_integration`, which is a coroutine and
#: would make a constant into an await: the panel shows the version on two screens
#: and neither should have to be asynchronous to say what it is looking at.
VERSION: str = str(
    json.loads(Path(__file__).with_name("manifest.json").read_text("utf-8"))["version"]
)

#: The engine API version to report when there is no session to ask. Every live
#: house reads the authoritative value off its own vocabulary
#: (`engine.vocabulary.Vocabulary.engine_api_version`), so this is only the answer
#: for an instance that has never completed the setup flow -- the case where the
#: panel is showing and there is no catalog-loaded session behind it. It is a
#: literal rather than a disk read because the alternative is a second catalog
#: search on the one path that is supposed to work before anything is configured.
ENGINE_API_FALLBACK = "1.0.0"


def catalog_root() -> Path | None:
    """The checkout's root, found by the catalog it must contain.

    Both the setup flow (`config_flow.py`) and the live engine
    (`automation.py`) read the frozen vocabulary rather than a copy of it, so
    the search for the catalog is one function rather than two that could
    disagree. The integration is loaded from inside the checkout --
    `custom_components/` is a directory of it -- so the catalog is an ancestor
    directory, and the search is a walk up rather than an assumption about depth.

    Inside the container the ancestor walk does not reach the catalog: the
    integration is loaded from `/config/custom_components/`, while the packages
    and the catalog it shares with the simulator, the CLI and the tests are
    mounted at `/openhouse-src`. `_bootstrap.source_root()` is that mount, so it
    is tried first and the walk is the fallback -- which is what keeps the
    repository's own test runs, where there is no mount, resolving as before.
    `None` means the vocabulary is genuinely absent, which is a condition the
    caller reports rather than papers over with an invented list.
    """
    mounted = _bootstrap.source_root()
    ancestors = Path(__file__).resolve().parents
    for parent in (mounted, *ancestors) if mounted is not None else ancestors:
        if (parent / "catalog" / "room_types.yaml").is_file():
            return parent
    return None


#: The subentry type a room is. Rooms are Home Assistant areas projected into the
#: integration as subentries (`spec.txt`: "HA Areas are the source of truth for
#: rooms; rooms are subentries where supported"), and the area id is the subentry's
#: `unique_id`, so one area cannot be added as a room twice.
SUBENTRY_ROOM = "room"

#: The platforms the integration forwards to. Base Home Assistant platforms only;
#: no custom component of our own, so nothing here has to be installed beside it.
PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)

#: The two entities each room gets from `select`, and the one each from
#: `binary_sensor` and `switch`.
ENTITY_MODE = "mode"
ENTITY_PROFILE = "profile"
ENTITY_OCCUPIED = "occupied"
ENTITY_AUTO_LIGHTING = "auto_lighting"

#: Every per-room entity key, in the order a room's device page lists them.
ROOM_ENTITIES: tuple[str, ...] = (
    ENTITY_MODE,
    ENTITY_PROFILE,
    ENTITY_OCCUPIED,
    ENTITY_AUTO_LIGHTING,
)

#: The house modes offered before a pack supplies its own.
MODES: tuple[str, ...] = ("Home", "Away", "Sleep", "Guest")

#: The profiles offered before a pack supplies its own.
PROFILES: tuple[str, ...] = ("Default",)

#: Where a person's own packs live, relative to Home Assistant's config
#: directory. Named here because three things have to agree about it -- the
#: session that reads it, the Dev tab that writes it, and a person who wants to
#: copy one out by hand -- and because the checkout is not it: the source tree is
#: read-only in every real deployment, and a module belongs beside the house.
PACKS_DIRECTORY: tuple[str, ...] = ("open_house", "packs")

#: Where a hosted module's record lives -- what it is called, which automation
#: runs it, what a person filled its inputs with and what it publishes. One file
#: for the whole house (`ha_adapter.module_records.FILENAME` inside this
#: directory), because the records are read together at setup and written
#: together after an import, and a file each would be a directory listing where
#: one read would do.
MODULES_DIRECTORY: tuple[str, ...] = ("open_house",)

#: Where the modules this house *offers* live -- one file each, named after the
#: module (`ha_adapter.module_definitions`), under the same `open_house` directory
#: as the records. A directory rather than a file for the reason the records are a
#: file: an offer is a thing a person hands to somebody else, so it has to be one
#: file a person can copy, and a house has one of these for every module it
#: publishes and none at all before it has published any.
DEFINITIONS_DIRECTORY: tuple[str, ...] = ("open_house", "modules")

#: The signal the entity platform listens on for a module that did not exist when
#: it was set up. A module is imported while the integration is already running,
#: so its outputs' entities have to be added to a platform that has already
#: finished loading -- a reload of the whole entry would take the engine, the
#: rooms and the panel down with it to add one sensor.
SIGNAL_MODULES_CHANGED = f"{DOMAIN}_modules_changed"

#: The signal for a module that is *gone*, carrying its slug.
#:
#: A separate signal rather than the same one with a flag, because the two ask
#: opposite things of the platform: `SIGNAL_MODULES_CHANGED` says "here is a
#: record, make sure its outputs exist" and this says "this module is no longer
#: here, take its outputs away". A platform that had to infer which of the two it
#: was reading would need the record to say whether it exists, which is the
#: absence it is being told about.
SIGNAL_MODULE_REMOVED = f"{DOMAIN}_module_removed"

#: The key naming the room's Home Assistant area id inside a room subentry's data.
DATA_AREA_ID = "area_id"
#: The key naming the room type the person confirmed.
DATA_ROOM_TYPE = "room_type"
#: The key naming the slot bindings the setup guessed and the person confirmed.
DATA_BINDINGS = "bindings"
