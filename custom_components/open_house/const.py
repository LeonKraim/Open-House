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

#: The key naming the room's Home Assistant area id inside a room subentry's data.
DATA_AREA_ID = "area_id"
#: The key naming the room type the person confirmed.
DATA_ROOM_TYPE = "room_type"
#: The key naming the slot bindings the setup guessed and the person confirmed.
DATA_BINDINGS = "bindings"
