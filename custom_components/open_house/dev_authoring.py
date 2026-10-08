"""The Dev tab's server half: reading Home Assistant's own automations, and writing modules.

**The conversion itself lives in `ha_adapter.pack_authoring`, and deliberately.**
That module is pure -- it takes a document and a plan and returns a mapping -- so
it can be read, tested and reasoned about without a Home Assistant in the room.
This module is the part that cannot be pure: it knows where a person's automations
are, where their modules may be written, and what this house's vocabulary is. The
split is the same one `setup_flow` and `config_flow` already draw, and it is drawn
here for the same reason -- the interesting half stays testable.

**Home Assistant is asked for its own automations rather than read off disk.**
`hass.data["automation"]` holds one entity per automation, YAML mode and UI mode
alike, and each carries the `raw_config` the automation editor itself edits. That
is the document a person sees when they open an automation in Home Assistant, so
it is the document this imports -- not a re-parse of `automations.yaml`, which
would silently miss every automation a person made in the UI.

**A blueprint is read through Home Assistant's blueprint model, which means
through Home Assistant's validation.** `DomainBlueprints` loads and validates, and
`Blueprint.yaml()` re-emits the document in canonical form. Handing that text to
the importer rather than the file on disk means a blueprint Home Assistant would
refuse is a blueprint this refuses, with Home Assistant's own reason.

**A module a person authors is written under `config/open_house/packs`.** The
checkout is the integration's own source tree and is read-only in every real
deployment -- a module belongs beside the house, not inside a program the person
will update. The session learns that directory at setup (`LiveSession.user_root`),
which is what makes a saved module show up in the same catalog the Store reads
from, with the `local` tier the tiers table already named for it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from homeassistant.core import HomeAssistant

from engine import vocabulary as engine_vocabulary
from engine.binding import HouseScope, RoomScope, resolve_slot
from ha_adapter import pack_authoring
from ha_adapter.live import LiveSession

from .const import PACKS_DIRECTORY

_LOGGER = logging.getLogger(__name__)


def packs_root(hass: HomeAssistant) -> Path:
    """The directory a person's own modules live in.

    `PACKS_DIRECTORY` is spelled in `const.py` rather than here, because the
    session the host builds reads the same directory and the two have to agree
    about it to the letter.
    """
    return Path(hass.config.path(*PACKS_DIRECTORY))


def saved(root: Path) -> list[Mapping[str, object]]:
    """The modules a person has authored, as rows the Dev tab lists.

    A plain directory read, so the caller runs it in an executor -- the panel
    asks for this list every time the tab opens, and globbing a directory on the
    event loop is the blocking call Home Assistant's own watchdog names.

    A file that will not parse is skipped rather than reported: this is the list
    a person navigates by, and the module that refuses to load is one the
    *install* path will name precisely when they try to install it. Reporting it
    here as a row with no name and no version would be a worse list, not a
    fuller one.
    """
    try:
        paths = sorted(root.glob("*.yaml"))
    except OSError:
        return []
    found: list[Mapping[str, object]] = []
    for path in paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(document, Mapping):
            continue
        found.append(
            {
                "name": str(document.get("name") or path.stem),
                "title": str(
                    _nested(document, "i18n", "default", "pack")
                    or document.get("name")
                    or path.stem
                ),
                "description": str(document.get("description") or ""),
                "version": str(document.get("version") or ""),
                "behaviours": len(document.get("behaviours") or []),
                "options": len(document.get("options") or []),
                "file": path.name,
            }
        )
    return found


def _nested(document: Mapping[str, Any], *keys: str) -> object:
    """Walk a document by keys, answering `None` the moment one is missing."""
    node: object = document
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


# --------------------------------------------------------------------------
# Reading what a person may import
# --------------------------------------------------------------------------


def automations(hass: HomeAssistant) -> tuple[Mapping[str, object], ...]:
    """Every automation this instance holds, as rows the panel can list.

    The entity id is the key rather than the automation's name, because two
    automations may share an alias and a list that addressed them by alias would
    have two rows pointing at one document. YAML-mode and UI-mode automations are
    one list here -- they are one list to a person in Home Assistant too -- and
    the `yaml` flag beside each row is only there so the panel can say where a
    removal would have to happen.
    """
    component = hass.data.get("automation")
    if component is None:
        # The automation integration is not set up: a house with no automations
        # at all, which is a real configuration and not a failure.
        return ()
    rows: list[Mapping[str, object]] = []
    for entity in sorted(component.entities, key=lambda item: item.entity_id):
        raw = getattr(entity, "raw_config", None)
        if not isinstance(raw, Mapping):
            continue
        rows.append(
            {
                "key": entity.entity_id,
                "name": str(raw.get("alias") or entity.entity_id),
                "description": str(raw.get("description") or ""),
            }
        )
    return tuple(rows)


async def blueprints(hass: HomeAssistant) -> tuple[Mapping[str, object], ...]:
    """Every blueprint this instance holds, as rows the panel can list.

    Read through `DomainBlueprints`, so the load is Home Assistant's own and a
    blueprint it would refuse is a row with a reason rather than omitted: "there
    is a file here and it is not a blueprint" is a fact a person who put it there
    wants, and a list that quietly dropped it would send them looking in the
    wrong place.
    """
    from homeassistant.components.automation.helpers import async_get_blueprints

    # `DomainBlueprints.async_get_blueprints` is the coroutine, not the listing:
    # it runs the directory glob and the YAML parse in an executor of its own, so
    # awaiting it here is both the correct call and the off-loop one. Calling it
    # without the await yielded a coroutine, and `.items()` on that is the
    # `AttributeError` this screen used to come up with.
    registry = async_get_blueprints(hass)
    loaded = await registry.async_get_blueprints()
    rows: list[Mapping[str, object]] = []
    for path, blueprint in sorted(loaded.items()):
        if not hasattr(blueprint, "metadata"):
            # A file under `blueprints/` that is not a blueprint, or one whose
            # schema this Home Assistant refuses. Reported by name rather than
            # dropped: a person looking for a blueprint they just imported is
            # owed the fact that Home Assistant has it and will not read it.
            rows.append(
                {
                    "key": path,
                    "name": path,
                    "description": str(blueprint),
                    "source_url": "",
                    "domain": "",
                }
            )
            continue
        metadatum = blueprint.metadata
        rows.append(
            {
                "key": path,
                "name": str(metadatum.get("name") or path),
                "description": " ".join(
                    str(metadatum.get("description") or "").split()
                ),
                "source_url": str(metadatum.get("source_url") or ""),
                "domain": str(metadatum.get("domain") or ""),
            }
        )
    return tuple(rows)


async def source_text(hass: HomeAssistant, kind: str, key: str) -> str:
    """The YAML text of one automation or blueprint, or a refusal naming why not.

    An automation's config is re-emitted rather than handed over as a mapping so
    that both kinds reach the importer as text, through one door, with one
    reading. The round trip through YAML is lossless for the shapes Home
    Assistant permits, and it is what makes a paste, a file and a picker the same
    thing to `pack_authoring.read_source`.
    """

    if kind == "automation":
        component = hass.data.get("automation")
        if component is None:
            raise pack_authoring.AuthoringError(
                "this instance has no automations set up, so there is nothing to import"
            )
        entity = component.get_entity(key)
        if entity is None:
            raise pack_authoring.AuthoringError(f"no automation is called {key!r}")
        raw = getattr(entity, "raw_config", None)
        if not isinstance(raw, Mapping):
            raise pack_authoring.AuthoringError(
                f"the automation {key!r} carries no configuration to read"
            )
        return yaml.safe_dump(dict(raw), sort_keys=False, allow_unicode=True)
    if kind == "blueprint":
        from homeassistant.components.automation.helpers import async_get_blueprints

        registry = async_get_blueprints(hass)
        loaded = await registry.async_get_blueprints()
        blueprint = loaded.get(key)
        if not hasattr(blueprint, "yaml"):
            raise pack_authoring.AuthoringError(
                f"the blueprint {key!r} is not one this instance can read: {blueprint}"
            )
        # `Blueprint.yaml()` is an in-memory re-emit of the config already
        # parsed by the load above, not a second read of the file, so it stays
        # on the loop with the rest of this function.
        return str(blueprint.yaml())
    raise pack_authoring.AuthoringError(
        f"{kind!r} is not a kind of source this reads; it reads an automation, a "
        "blueprint, or a document somebody pasted"
    )


def reference(
    session: LiveSession,
) -> tuple[tuple[str, ...], Mapping[str, Sequence[str]]]:
    """The service list and the slot vocabulary this house validates against.

    Both are read from the checkout, both are disk, and the caller reads them in
    an executor job for that reason. The slot vocabulary is `catalog/slots.yaml`
    as `setup_flow` reads it -- slot name to the domains it accepts -- because the
    importer's one job that needs it is refusing a slot bound to a device it
    cannot ask, and that question is the catalog's to answer.
    """
    from ha_adapter.setup_flow import load_slot_domains

    return (
        tuple(engine_vocabulary.load_service_states(session.root)),
        load_slot_domains(session.root),
    )


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Saved:
    """A module written to disk, and the two paths it was written to."""

    manifest: Path
    artifact: Path
    document: Mapping[str, object]
    text: str


def save(root: Path, draft: pack_authoring.Draft) -> Saved:
    """Write a drafted module under `root`, one manifest file and one artifact.

    Two files rather than one because the schema asks for it: a pack pins what it
    confers (`provides`), and a pin points at a real file inside the pack's own
    directory. The artifact is the automation the module was read from, restated
    in the engine's terms -- the interpreter binds the manifest's clauses and
    never reads it, and it is written anyway for the reason `packs/official`
    writes one: a pack that conferred nothing but a declaration would be a pack
    whose manifest nobody could check against anything.

    Blocking, by definition. The caller is an executor job, because this runs
    from a websocket handler and the event loop is not where the disk belongs.
    """
    name = str(draft.document["name"])
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / f"{name}.yaml"
    artifact = root / draft.artifact_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    text = pack_authoring.module_text(draft.document)
    manifest.write_text(text, encoding="utf-8")
    artifact.write_text(draft.artifact_text, encoding="utf-8")
    return Saved(
        manifest=manifest, artifact=artifact, document=draft.document, text=text
    )


# --------------------------------------------------------------------------
# Exporting
# --------------------------------------------------------------------------


def bound_slots(
    session: LiveSession, behaviour: Mapping[str, object], room_id: str | None
) -> Mapping[str, Sequence[str]]:
    """The entities each of one behaviour's slots resolves to, for a room.

    The same resolution the engine performs when the behaviour runs, asked the
    same way (`engine.binding.resolve_slot`), so an export is what the house
    *does* and not what its bindings happen to look like. A behaviour declared at
    house scope resolves at house scope whichever room asked, which is the
    distinction that would be lost by reading the room's bindings directly.

    A slot that resolves to nothing is present with an empty tuple rather than
    absent, so an exported automation that acts on nothing says so by naming no
    entity instead of by omitting its `target` and reading as an automation that
    acts on everything.
    """
    house = session.engine.house
    scope = (
        HouseScope()
        if behaviour.get("scope") == "house"
        else RoomScope(room_id if room_id is not None else HOUSE_SCOPE)
    )
    resolved: dict[str, Sequence[str]] = {}
    for slot in behaviour.get("slots", []):
        if not isinstance(slot, str):
            continue
        try:
            resolved[slot] = resolve_slot(house, scope, slot).entities
        except Exception:
            # A slot the vocabulary does not know, or one no room binds: the
            # honest answer is "nothing", and `resolve_slot` raises for the
            # former. Narrowing this would mean enumerating `engine.binding`'s
            # failures here, which is a second definition of its contract.
            resolved[slot] = ()
    return resolved


#: The room id a behaviour declared at house scope is resolved with when the
#: caller named no room. The engine's own spelling for the house.
HOUSE_SCOPE = ""


__all__ = [
    "HOUSE_SCOPE",
    "Saved",
    "automations",
    "blueprints",
    "bound_slots",
    "packs_root",
    "reference",
    "save",
    "saved",
    "source_text",
]
