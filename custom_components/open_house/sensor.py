"""A module's outputs, as entities anything in the house can read.

An output is how one module hands a value to another, and it is a real Home
Assistant entity -- `sensor.open_house_<module>_<key>` -- so that what consumes
it is not a special Open House mechanism: a template, a dashboard, an automation
a person writes by hand and another module's input binding all read it the same
way. That is deliberate. A private bus between modules would work and would be
usable only from inside this integration.

**The entity shows a reading, not a memory.** Its value is whatever the module
last published, and `unknown` before the module has ever run the step that
publishes it. Those are different states and the entity keeps them different: a
module that has not fired yet has published nothing, and reporting `0` for it
would be inventing a reading. The last value is restored across a restart
(`RestoreEntity`) because a restart is not the module un-publishing anything.

**One entity per output, and its id is decided by the record.** The id is
`ha_adapter.module_host.output_entity_id` -- one spelling, shared with the
make-public step that writes here and with the binding that reads from here, so
the three cannot drift apart. It is set on the entity directly rather than left
to Home Assistant's name-derived id, because a consumer binds to this id by
value: a differently-derived id would be a binding that resolves to nothing.

**A module imported while the house is up gets its entities through a signal.**
The alternative is reloading the config entry, which would take the rooms, the
engine and the panel down to add one sensor. `SIGNAL_MODULES_CHANGED` carries the
new record and this platform adds what it needs, which is what the dispatcher
exists for.

**The same signal is what a rebuilt module arrives on, and it is not a new
module.** Changing a module's settings builds its automation again, and the
outputs keep their names, their entities and their last published values -- so
this platform tells the difference by unique id and refreshes an output's entity
rather than making a second one. Home Assistant refuses a duplicate unique id,
which is how a rebuild that tried to re-register would announce itself: an error
in the log and an output stuck on a value its module has moved past.

**A module taken out of the house takes its entities with it**, which needs a
signal of its own (`SIGNAL_MODULE_REMOVED`): the module is *absent* afterwards,
and absence is not something `SIGNAL_MODULES_CHANGED` can carry, since that one
hands over a record. It carries the slug instead and this platform removes every
entity whose unique id was made from it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any, cast

# `RestoreSensor` comes from the sensor component and not from
# `helpers.restore_state`, which holds only `RestoreEntity`: the sensor-specific
# reading -- the last value and its unit, not just the last state -- is defined
# beside `SensorEntity`, and the import is the one Home Assistant's own sensor
# platforms use (`components/mqtt/sensor.py`, `components/shelly/sensor.py`).
from homeassistant.components.sensor import RestoreSensor, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ha_adapter.module_host import Output, flow_entity_id, output_entity_id
from ha_adapter.module_records import ModuleRecord

from .const import DOMAIN, SIGNAL_MODULE_REMOVED, SIGNAL_MODULES_CHANGED
from .runtime import HostedModule, OpenHouseRuntime

__all__ = ["async_setup_entry"]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one entity per output of every module this house hosts.

    Called when the entry loads, when the records file has already been read into
    the runtime, and subscribed afterwards so a module imported while the house
    is running gets its outputs too. Both paths make the same entities through
    the same function, so a module imported now and a module loaded at startup
    cannot end up with different sets.
    """
    runtime: OpenHouseRuntime = hass.data[DOMAIN][entry.entry_id]
    #: The entity each output and each flow has, by unique id. An output's entity
    #: is keyed by the module and the output, so it is the same entity across a
    #: rebuild -- and Home Assistant refuses to add a second entity for a unique
    #: id it already has, loudly, which is what makes this a map rather than a
    #: list. Both kinds live in it because both are keyed the same way and both
    #: have to survive a rebuild the same way.
    made: dict[str, _ModuleSensor] = {}

    def _install(
        modules: Iterable[HostedModule],
    ) -> tuple[list[_ModuleSensor], set[str]]:
        """The entities for these modules, and which unique ids they now carry.

        The second half is what tells a *rebuild* from an *arrival*: a module
        whose outputs were changed has entities that are no longer its, and the
        only way to know which is to know what it has, which is what this returns
        rather than a caller re-spelling the ids.

        A module that is here already -- a module whose settings were just
        changed -- keeps its entities and has them re-read the declaration
        instead, so its last published value survives the change and its entity
        ids do not move.
        """
        fresh: list[_ModuleSensor] = []
        live: set[str] = set()
        for module in modules:
            for output in module.record.outputs:
                _keep(fresh, live, ModuleOutputSensor(module, output, entry.entry_id))
            for name in module.record.flows:
                _keep(fresh, live, FlowSensor(module, name, entry.entry_id))
        return fresh, live

    def _keep(
        fresh: list[_ModuleSensor], live: set[str], entity: _ModuleSensor
    ) -> None:
        """Add one entity if it is new, and otherwise re-point the one there is."""
        live.add(entity.unique_id)
        existing = made.get(entity.unique_id)
        if existing is None:
            made[entity.unique_id] = entity
            fresh.append(entity)
        else:
            existing.refresh(entity.module, entity.declaration)

    def _forget(prefix: str, live: set[str]) -> None:
        """Take away the entities under `prefix` that are not among `live`.

        By unique id and not by the record, because what is being removed is
        exactly the part of a module that no longer has a record to be read from.
        Dropping them from the map alone would leave the entity running in Home
        Assistant, so each is removed through Home Assistant as well.

        Both halves have to go, and neither takes the other with it. Removing the
        entity while its registry entry stands leaves an `unavailable` entity
        behind -- which is what Home Assistant does on purpose for an entity a
        platform stops providing, and is wrong here: this is a house that decided
        the reading is gone, not a platform that failed to answer, and an
        `unavailable` reading sitting in the house is a thing a person cannot act
        on and cannot switch off. So the entry is removed too, and the entity is
        removed with `force_remove` so its state goes rather than being written
        as the unavailable it would otherwise be.
        """
        registry = er.async_get(hass)
        for unique_id in [
            uid for uid in made if uid.startswith(prefix) and uid not in live
        ]:
            entity = made.pop(unique_id)
            if registry.async_get(entity.entity_id) is not None:
                registry.async_remove(entity.entity_id)
            hass.async_create_task(entity.async_remove(force_remove=True))

    async_add_entities(_install(runtime.modules.values())[0])

    @callback
    def _added(record: ModuleRecord) -> None:
        """One module arrived or was rebuilt; make, refresh and prune its entities.

        **Pruned and not only added**, because a rebuild is not always the same
        module: editing it is how an output is taken off, and a module that has
        stopped publishing one of its values has to stop *having* the entity for
        it. Leaving it would leave a `sensor` in the house that no automation
        writes, no screen offers and a person cannot act on -- and that another
        module could later be bound to as if it had a value.
        """
        hosted = runtime.modules.get(record.slug)
        if hosted is None:
            return
        fresh, live = _install([hosted])
        _forget(f"{entry.entry_id}_module_{record.slug}_", live)
        async_add_entities(fresh)

    @callback
    def _removed(slug: str) -> None:
        """One module was taken out of the house; take its outputs away.

        Every output goes, whether or not it ever published anything: an entity
        showing `unknown` is still a module the house no longer has, and leaving
        it would leave an entity a person could bind another module to.
        """
        _forget(f"{entry.entry_id}_module_{slug}_", set())

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_MODULES_CHANGED, _added)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_MODULE_REMOVED, _removed)
    )


class _ModuleSensor(RestoreSensor, SensorEntity):
    """One of a module's readings: who it belongs to, and what makes it redraw.

    Shared by the two kinds -- a value the module *published* and a value a flow
    *wrote* -- because Home Assistant will not have two classes claiming the same
    unique id, and because the two agree about everything except where the value
    comes from. That part is `_key`, and it is all the subclasses differ in.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        module: HostedModule,
        *,
        suffix: str,
        label: str,
        entity_id: str,
        key: str,
        declaration: Any,
        entry_id: str,
    ) -> None:
        self._module = module
        self._declaration = declaration
        self._key = key
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_module_{module.record.slug}_{suffix}"
        #: The module's own name and the reading's, so the entity reads as
        #: "Dynamic Lighting Min Lux" on the device page rather than as a bare
        #: key, and so the id can be the exact spelling consumers bind to.
        self._attr_name = f"{module.record.title} {label}"
        self.entity_id = entity_id

    @property
    def module(self) -> HostedModule:
        """The module this reading belongs to; what a rebuild re-points."""
        return self._module

    @property
    def declaration(self) -> Any:
        """What this reading was declared as; the output, or the input's name."""
        return self._declaration

    @callback
    def refresh(self, module: HostedModule, declaration: Any) -> None:
        """Re-read the module's declaration after it was built again.

        A settings change rebuilds the automation, which may move where a
        reading's expression is read from even when its own name has not changed
        -- an output that mirrors an input follows the entity that input is now
        bound to. The entity is the same entity; what it reports about the module
        is what has to be brought up to date.
        """
        self._module = module
        self._declaration = declaration
        self._attr_name = f"{module.record.title} {self._label(declaration)}"
        self.async_write_ha_state()

    def _label(self, declaration: Any) -> str:
        """The declaration as the half of the name that is not the module's."""
        raise NotImplementedError

    @property
    def device_info(self) -> DeviceInfo:
        """The module, as the device all of its readings hang from.

        A device of its own rather than the room's: a module may be placed in a
        room, but its readings are the module's, and a consumer looking for them
        should find them beside the automation that produces them.
        """
        record = self._module.record
        return DeviceInfo(
            identifiers={(DOMAIN, f"module:{record.slug}")},
            name=record.title,
            manufacturer="Open House",
            model="Module",
        )

    @property
    def native_value(self) -> Any:
        """The value that was last written, or `None` while none has been.

        `None` is Home Assistant's `unknown`, which is the right answer for a
        reading nothing has written yet -- see the module docstring.
        """
        return _native(self._module.values.get(self._key))

    async def async_added_to_hass(self) -> None:
        """Restore the last value, and redraw when a new one arrives.

        Restoring first is what keeps a restart from reading as every module
        having gone quiet, and it is *only* a restore: a value this instance has
        not written in this run is still replaced by the next real write.
        """
        await super().async_added_to_hass()
        self._module.listen(self._written)
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            self._module.values.setdefault(self._key, last.native_value)

    @callback
    def _written(self, key: str) -> None:
        """One of this module's readings moved; redraw if it was this one."""
        if key == self._key:
            self.async_write_ha_state()


class ModuleOutputSensor(_ModuleSensor):
    """One published value, as a reading any automation can take as input."""

    def __init__(self, module: HostedModule, output: Output, entry_id: str) -> None:
        super().__init__(
            module,
            suffix=output.key,
            label=_title(output.key),
            entity_id=output_entity_id(module.record.slug, output.key),
            key=output.key,
            declaration=output,
            entry_id=entry_id,
        )

    def _label(self, declaration: Any) -> str:
        return _title(cast("Output", declaration).key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where this reading comes from, for a person looking at the entity.

        The kind is what the person chose at import; the automation is what
        publishes it. Both are on the entity rather than only in the panel,
        because the state machine is where somebody debugging a template will be
        looking when they ask why an output is unknown.
        """
        return {
            "module": self._module.record.slug,
            "output": cast("Output", self._declaration).key,
            "kind": cast("Output", self._declaration).kind,
            "automation": self._module.record.automation_id,
        }


class FlowSensor(_ModuleSensor):
    """The entity a Node-RED flow writes, and the input answered by it reads.

    Named `sensor.open_house_flow_<module>_<input>` by
    `ha_adapter.module_host.flow_entity_id`, which the flow's own output node and
    the module's binding are both spelled from -- so the three cannot disagree
    about where the answer is put.

    It shows `unknown` until the flow has run, for the reason an output does: a
    module whose flow has not written yet has no answer, and reporting `0` or
    `off` for it would be inventing one.
    """

    def __init__(self, module: HostedModule, name: str, entry_id: str) -> None:
        super().__init__(
            module,
            suffix=f"flow_{name}",
            label=f"{_title(name)} (flow)",
            entity_id=flow_entity_id(module.record.slug, name),
            # Prefixed so it cannot collide with an *output* of the same name on
            # one module: the two are different readings and the module keeps
            # both without either overwriting the other.
            key=f"flow:{name}",
            declaration=name,
            entry_id=entry_id,
        )

    def _label(self, declaration: Any) -> str:
        return f"{_title(str(declaration))} (flow)"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Where this reading comes from: the flow that writes it."""
        name = str(self._declaration)
        return {
            "module": self._module.record.slug,
            "input": name,
            "flow_id": self._module.record.flows.get(name, ""),
        }


def _native(value: Any) -> Any:
    """A published value as something a `sensor` may report.

    A sensor's state is a scalar. An output that is a list or a mapping is
    therefore shown as its JSON, which is lossy in the sense that the state is a
    string and not the value -- so it is last, and it is why the whole value is
    also kept on the module's runtime for anything inside Open House that wants
    it whole. Anything that will not serialise reports `None`: an output whose
    value cannot be shown is unknown, and it is not a reason to fail a redraw.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float, str)):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return None


def _title(key: str) -> str:
    """An output key as a name a person reads: `min_lux` becomes `Min Lux`."""
    return " ".join(word.capitalize() for word in key.split("_") if word)
