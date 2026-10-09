"""Two kinds of reading a room answers with: whether it is occupied, and whether
a module's condition holds.

The first is the entity the startup-safety rule exists for. Home Assistant's
`binary_sensor` has three answers -- on, off, and unknown -- and this one uses
all three: `is_on` is `True` when the room's bound motion sensor reads motion,
`False` when it reads clear, and `None` when the room has no sensor or the sensor
is unavailable. `None` is the honest answer, and the reason the rule is stated as
a rule: a room whose sensor has dropped off the network is not empty, it is
unreadable, and reporting it `False` would let an absence shutdown empty a room a
person is sitting in.

**The second is a condition, made into an entity.** A person importing a
blueprint may answer one of its inputs with *logic* rather than with a value --
Home Assistant's own condition editor, the one automations and blueprints carry.
An input that takes a value holds logic happily, because a template is rendered
where it lands. A **trigger's `entity_id` does not**: Home Assistant matches it
against the entities the house actually has rather than rendering it, so a
condition written there is compared as text, matches nothing, and leaves an
automation that installs and never fires -- and says nothing at all while it does
not. So the condition is not written into the module at all. Open House evaluates
it here, publishes yes or no as this entity, and *that* is what the input, and so
the trigger, is pointed at.

Which means the condition is evaluated by Home Assistant's own machinery --
`condition.async_from_config` hands back a `ConditionChecker`, the same object an
automation holds -- rather than by anything this integration synthesises. Nothing
is translated, so nothing can be translated *wrongly*: every condition type a
person can build in the editor works here, including the ones this integration
has never heard of, because the code that decides is Home Assistant's.

The entity re-evaluates when a `for:` duration elapses or the clock crosses a
`condition: time` boundary as well as when a tracked device changes, which is why
it also ticks: a condition is a question about the world and the world changes
without an entity moving.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.condition import (
    AndConditionChecker,
    ConditionError,
    async_from_config,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.target import async_track_target_selector_state_change_event

from ha_adapter.module_host import derived_entity_id
from ha_adapter.module_records import ModuleRecord

from .const import (
    DOMAIN,
    ENTITY_OCCUPIED,
    SIGNAL_MODULE_REMOVED,
    SIGNAL_MODULES_CHANGED,
)
from .entity import OpenHouseRoomEntity
from .runtime import HostedModule, OpenHouseRuntime, RoomRuntime

__all__ = [
    "TICK_SECONDS",
    "async_setup_entry",
    "condition_rows",
    "watched_targets",
]

_LOGGER = logging.getLogger(__name__)

#: How often a derived condition is re-read besides when a device it names
#: changes. A condition is not only about devices: `for:` is a question about how
#: long something has been true, and a `condition: time` is a question about the
#: clock, and neither announces itself as an event. Thirty seconds is the same
#: cadence the engine ticks at, and it is short enough that a light following a
#: condition is not visibly late.
TICK_SECONDS = 30

#: The keys any of which makes a mapping a *target selection* -- the shape
#: `async_track_target_selector_state_change_event` takes, and the shape a
#: condition stores the devices it reads. A condition's own `entity_id` is a
#: target selection of one key, which is why this is the same set.
_TARGET_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the occupancy sensor for every room, and the condition sensor for
    every condition input of every module this house hosts."""
    runtime: OpenHouseRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoomOccupiedSensor(room, entry.entry_id) for room in runtime.rooms.values()
    )
    add_derived = _DerivedPlatform(hass, entry, async_add_entities)
    async_add_entities(add_derived.install(runtime.modules.values()))
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_MODULES_CHANGED, add_derived.added)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_MODULE_REMOVED, add_derived.removed)
    )


class _DerivedPlatform:
    """The condition-made entities, and the two ways they come and go.

    Kept together in one object rather than as closures because it holds state --
    which entity belongs to which unique id -- that both the add and the remove
    path have to read, exactly as the output platform does.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
    ) -> None:
        self._hass = hass
        self._add = add
        self._entry_id = entry.entry_id
        #: The entity each condition input has, by unique id. Home Assistant
        #: refuses a second entity for a unique id it already holds, which is
        #: what makes this a map rather than a list: a rebuilt module keeps its
        #: entities and has them re-read the condition instead.
        self._made: dict[str, DerivedConditionSensor] = {}

    def install(self, modules: Iterable[HostedModule]) -> list[DerivedConditionSensor]:
        """The entities for these modules' conditions that do not exist yet."""
        fresh: list[DerivedConditionSensor] = []
        for module in modules:
            for name, condition in module.record.derived.items():
                entity = DerivedConditionSensor(module, name, condition, self._entry_id)
                existing = self._made.get(entity.unique_id)
                if existing is None:
                    self._made[entity.unique_id] = entity
                    fresh.append(entity)
                else:
                    existing.refresh(module, name, condition)
        return fresh

    @callback
    def added(self, record: ModuleRecord) -> None:
        """One module arrived or was built again; make or refresh its entities."""
        hosted = self._hass.data.get(DOMAIN, {}).get(self._entry_id)
        module = (getattr(hosted, "modules", None) or {}).get(record.slug)
        if module is not None:
            self._add(self.install([module]))
        # A condition that was *removed* from a rebuilt module leaves an entity
        # nothing will ever write to again, and it is taken away here rather than
        # left reading `unknown` forever beside the ones that still answer.
        prefix = f"{self._entry_id}_module_{record.slug}_derived_"
        for unique_id in [
            uid
            for uid in self._made
            if uid.startswith(prefix)
            and uid.rsplit("_derived_", 1)[1] not in record.derived
        ]:
            self._drop(self._made.pop(unique_id))

    @callback
    def removed(self, slug: str) -> None:
        """One module was taken out of the house; take its conditions away too."""
        prefix = f"{self._entry_id}_module_{slug}_derived_"
        for unique_id in [uid for uid in self._made if uid.startswith(prefix)]:
            self._drop(self._made.pop(unique_id))

    def _drop(self, entity: DerivedConditionSensor) -> None:
        """Remove one derived entity from the house as well as from the map.

        Both halves, for the reason the output platform gives: removing it from
        the map alone would leave an entity Home Assistant still shows, and
        removing the entity alone would leave a registry entry -- an
        `unavailable` reading a person can neither act on nor switch off.
        """
        registry = er.async_get(self._hass)
        if registry.async_get(entity.entity_id) is not None:
            registry.async_remove(entity.entity_id)
        self._hass.async_create_task(entity.async_remove(force_remove=True))


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


class DerivedConditionSensor(BinarySensorEntity):
    """One imported input's condition, as a yes-or-no the house can read.

    The id is `ha_adapter.module_host.derived_entity_id` -- one spelling, shared
    with the binding that points the input at it -- and it is set on the entity
    directly rather than left to Home Assistant's name-derived id, because what
    points at this entity does so by value.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        module: HostedModule,
        name: str,
        condition: Any,
        entry_id: str,
    ) -> None:
        self._module = module
        self._name = name
        self._condition = condition
        self._entry_id = entry_id
        self._state: bool | None = None
        self._check: AndConditionChecker | None = None
        self._stop: list[Any] = []
        self._attr_unique_id = f"{entry_id}_module_{module.record.slug}_derived_{name}"
        self._attr_name = f"{module.record.title} {_title(name)}"
        self.entity_id = derived_entity_id(module.record.slug, name)

    @callback
    def refresh(self, module: HostedModule, name: str, condition: Any) -> None:
        """Re-read the condition after the module was built again.

        The condition is rebuilt rather than merely re-pointed, because the
        editor's answer is the config: a person who changed *what* the condition
        asks has changed the object the entity is built from, and keeping the old
        checker would leave the entity answering the question they replaced.
        """
        changed = condition != self._condition
        self._module = module
        self._name = name
        self._condition = condition
        self._attr_name = f"{module.record.title} {_title(name)}"
        # A checker only exists once the entity has been added; a refresh that
        # arrives before that is a name change, and the condition is built on the
        # way in regardless.
        if changed and self._check is not None:
            self.hass.async_create_task(self._rebuild())

    @property
    def device_info(self) -> DeviceInfo:
        """The module, as the device this condition hangs from.

        The module's device and not the room's, for the reason its outputs are:
        a condition is part of what the module is, not a reading the room
        carries, and a person looking for why a module behaves as it does should
        find its pieces together.
        """
        record = self._module.record
        return DeviceInfo(
            identifiers={(DOMAIN, f"module:{record.slug}")},
            name=record.title,
            manufacturer="Open House",
            model="Module",
        )

    @property
    def is_on(self) -> bool | None:
        """The condition's answer, or `None` when it cannot be evaluated.

        `None` is unknown and it is not `False`. A condition naming an entity
        that is currently `unavailable` -- or a numeric condition over a state
        that is not a number -- cannot say no any more than it can say yes, and
        reporting `False` would be answering a question the house could not read.
        """
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What this entity is answering, for somebody reading the state machine."""
        return {
            "module": self._module.record.slug,
            "input": self._name,
            "condition": self._condition,
        }

    async def async_added_to_hass(self) -> None:
        """Build the checker, watch what it reads, and evaluate it once."""
        await super().async_added_to_hass()
        await self._rebuild()

    async def async_will_remove_from_hass(self) -> None:
        """Drop the checker and its subscriptions before the entity goes."""
        self._release()
        await super().async_will_remove_from_hass()

    def _release(self) -> None:
        """Everything this entity subscribed to, given back."""
        for stop in self._stop:
            stop()
        self._stop.clear()
        if self._check is not None:
            self._check.async_unload()
            self._check = None

    async def _rebuild(self) -> None:
        """Make the checker from the current condition and re-read the world."""
        self._release()
        try:
            checkers = [
                await async_from_config(self.hass, row)
                for row in condition_rows(self._condition)
            ]
            checker = AndConditionChecker(self.hass, checkers)
            await checker.async_setup()
        except (
            ConditionError,
            HomeAssistantError,
            ValueError,
            KeyError,
            # A condition naming a target that resolves to nothing raises
            # `AttributeError` from Home Assistant's own `TargetSelection`, not a
            # `ConditionError` -- `condition.py` reaches `config.get(...)` on a
            # `None` and the guard above would let it through, leaving a sensor
            # that is never added and nothing saying why. Observed live on a
            # derived condition whose target selector was left unresolved.
            AttributeError,
        ) as failure:
            # A condition that will not build is a condition this entity cannot
            # answer, and the honest reading of it is unknown. It is logged
            # because it is a refusal a person can only fix by editing the
            # condition -- a panel row would show a sensor that is simply never
            # true, with nothing saying why.
            _LOGGER.warning(
                "the condition on %s (%s) could not be built: %s",
                self.entity_id,
                self._module.record.slug,
                failure,
            )
            self._state = None
            self.async_write_ha_state()
            return
        self._check = checker
        for target in watched_targets(self._condition):
            # **Awaited**, because this one is a coroutine function returning the
            # unsubscribe rather than returning it outright -- the family's other
            # `async_track_*` helpers are callbacks, so the shape is not the one
            # to assume. Appending what it returned without awaiting stored a
            # coroutine, and calling *that* on unload is what raised
            # `'coroutine' object is not callable`.
            self._stop.append(
                await async_track_target_selector_state_change_event(
                    self.hass, target, self._changed
                )
            )
        self._stop.append(
            async_track_time_interval(
                self.hass, self._changed, timedelta(seconds=TICK_SECONDS)
            )
        )
        self._evaluate()

    @callback
    def _changed(self, _event: Any = None) -> None:
        """Something the condition reads moved, or the tick came round."""
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        """Ask the condition, and write whatever it answered.

        Nothing to ask before the checker exists, which is the moment between the
        entity being made and the condition being built from it.
        """
        if self._check is None:
            return
        try:
            answered = self._check.async_check()
        except ConditionError as unreadable:
            # `ConditionError` is the condition *saying it could not tell* -- an
            # entity that is unavailable, a state that is not a number. That is
            # unknown and not off; see `is_on`.
            _LOGGER.debug("%s could not be evaluated: %s", self.entity_id, unreadable)
            answered = None
        changed = answered is not self._state
        self._state = answered
        if changed:
            self.async_write_ha_state()


def condition_rows(condition: Any) -> list[Any]:
    """The condition configs to build, whatever shape the editor handed over.

    A condition selector gives a *list* of conditions -- the builder's rows,
    which Home Assistant's own automation schema reads as "all of these" -- and a
    single mapping is the same thing with one row. Both are accepted because both
    are things a person may have written in the condition editor.
    """
    if isinstance(condition, list):
        return [row for row in condition if isinstance(row, Mapping)]
    if isinstance(condition, Mapping):
        return [condition]
    return []


def watched_targets(condition: Any) -> list[Mapping[str, Any]]:
    """The device selections this condition reads, as the tracker takes them.

    Walked rather than listed, because a condition names the things it reads in
    the shape its own type needs: a state condition has an `entity_id` at the top
    level, a numeric one the same, and one that acts on a `target` has it under
    that key. All of them are the same selection -- the keys `_TARGET_KEYS` names
    -- so all of them are tracked the same way, and a condition this integration
    has never heard of is followed as readily as the ones it has.
    """
    found: list[Mapping[str, Any]] = []
    for row in condition_rows(condition):
        found.extend(_walk_targets(row))
    return found


def _walk_targets(node: Any) -> list[Mapping[str, Any]]:
    """Every target selection anywhere inside one condition's config.

    A mapping carrying any of `_TARGET_KEYS` *is* one, wherever it sits: a state
    condition keeps its `entity_id` at the top level and one acting on a `target`
    keeps the same keys under that key, so the recursion finds both by looking at
    what a mapping holds rather than at where it holds it.
    """
    found: list[Mapping[str, Any]] = []
    if isinstance(node, Mapping):
        selected = {key: node[key] for key in _TARGET_KEYS if key in node}
        if selected:
            found.append(selected)
        for value in node.values():
            found.extend(_walk_targets(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_targets(item))
    return found


def _title(name: str) -> str:
    """An input name as a name a person reads: `sleep_entity` -> `Sleep Entity`."""
    return " ".join(word.capitalize() for word in name.split("_") if word)
