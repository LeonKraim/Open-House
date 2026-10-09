"""The Dev tab's server half: reading Home Assistant's own automations and blueprints.

**Home Assistant is asked for its own automations rather than read off disk.**
`hass.data["automation"]` holds one entity per automation, YAML mode and UI mode
alike, and each carries the `raw_config` the automation editor itself edits. That
is the document a person sees when they open an automation in Home Assistant, so
it is the document these rows name -- not a re-parse of `automations.yaml`, which
would silently miss every automation a person made in the UI.

**A blueprint is read through Home Assistant's blueprint model, which means
through Home Assistant's validation.** `DomainBlueprints` loads and validates, so
a blueprint Home Assistant would refuse is a row with a reason rather than
omitted: "there is a file here and it is not a blueprint" is a fact a person who
put it there wants, and a list that quietly dropped it would send them looking in
the wrong place.
"""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import dump as yaml_dump

from ha_adapter import pack_authoring

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

    **Home Assistant's own dumper, and not `yaml.safe_dump`.** An automation's
    `raw_config` is the document Home Assistant's editor holds, and it is
    *annotated*: every key and value is a `NodeStrClass`/`NodeListClass`/
    `NodeDictClass` -- subclasses of `str`/`list`/`dict` carrying the line and
    column they were parsed from, which is what the editor uses to point at the
    line a mistake is on. PyYAML has no representer for those, so `safe_dump`
    raises `RepresenterError` on the first key. It also only unwraps the
    outermost mapping, and the nested nodes go just as unrepresented, so a
    deep-copy would not have been enough either. `homeassistant.util.yaml.dump`
    is the same function Home Assistant itself writes `automations.yaml` with,
    and it registers a representer for exactly those classes -- so the text this
    hands the importer is the text a person would see.
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
        try:
            return yaml_dump(dict(raw))
        except Exception as err:
            # The refusal is `AuthoringError` because the caller only catches
            # that (`websocket_api.ws_modules_read`): anything else leaves the
            # panel with Home Assistant's generic "Unknown error" and a stack
            # trace in the log, when what the person needs is a sentence saying
            # this one automation could not be read.
            raise pack_authoring.AuthoringError(
                f"the automation {key!r} could not be written back out as YAML: {err}"
            ) from err
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


__all__ = ["automations", "blueprints", "source_text"]
