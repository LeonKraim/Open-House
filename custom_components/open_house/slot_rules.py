"""The logic a person has put on a slot row, re-decided when the world moves.

`ha_adapter.slot_rules` says what a slot rule *is* and is pure: a kind, a payload,
and the sentence a card draws. This is the other half, and it is the only genuinely
new runtime the slot work adds. **A rule is not a run.** Every other piece of logic
in the product is worked out inside an automation: a module's casts are evaluated
whenever its automation fires, which is what makes attaching one cost nothing. A
slot is a standing fact -- a room's binding, read at build time and at every tick --
so a rule on it has to be re-decided when the world moves, and that needs a
listener rather than a run.

**What each kind listens to, and why each answer is different.**

* A **template** renders to an entity id. Home Assistant's own tracker watches
  whatever the text reads (`async_track_template_result`), so nothing here has to
  parse a template or decide what it depends on -- and the tracker re-renders on a
  time change as well as a state change, which a `now()` in the text needs.
* A **flow** writes an entity of its own (`sensor.open_house_flow_*`), which is
  the one kind that was already running before anybody asked it to decide
  anything: the rule is a state listener on that entity, and the entity's reading
  is the entity id.
* A **script** runs only when something calls it, so a person has to say what
  should (`slot_rules.watched_by`), and this calls it and reads the value back out
  of `stop: response_variable:` under `slot_rules.RESPONSE`.
* A **condition** answers yes or no and never an entity, so it decides *whether*:
  while it holds, the slot is the device the rule gates; while it does not, the
  slot falls back to what the room binds. Home Assistant's own condition machinery
  evaluates it (`condition.async_from_config`, the same object an automation
  holds), watched by the targets it names, plus a tick for the conditions no
  entity announces -- a `for:` duration, a time of day.

**One write, and one place that knows it happened.** Every kind ends at
`live_modules.settle_slot_rule`, which writes the device into `slot.<slot>.entity`
-- the very key a person's own device pick writes -- so the engine, the module's
card and the module's automation read one answer. `host.async_slot_changed` then
saves it and rebuilds the modules that reach the slot, because the *device* an
automation was built from is exactly what changed.

**Listeners follow the rules, and nothing else does.** A refresh reconciles the
listeners against the rules on the modules the house hosts right now: a rule that
appeared gets one, a rule that changed is built again, and a rule that was taken
off a row has its listener stopped. The signal the module platform already sends
on every build drives that, so no caller has to remember to tell this module
anything -- and a listener can never outlive the rule it was made for.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, TemplateError
from homeassistant.helpers.condition import (
    AndConditionChecker,
    ConditionError,
    async_from_config,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.target import async_track_target_selector_state_change_event
from homeassistant.helpers.template import Template

from ha_adapter import live_modules
from ha_adapter import slot_rules as rules
from ha_adapter.live import LiveSessionError

from . import modules as module_hosting
from .binary_sensor import TICK_SECONDS, condition_rows, watched_targets
from .const import SIGNAL_MODULES_CHANGED

__all__ = ["SlotRuleWatcher", "async_setup_slot_rules"]

_LOGGER = logging.getLogger(__name__)


async def async_setup_slot_rules(
    hass: HomeAssistant, entry: ConfigEntry, host: Any
) -> SlotRuleWatcher:
    """Start watching every slot rule the house holds.

    No `None` case, unlike its sibling `automation.async_setup_automation`: a
    watcher needs only a session, and the caller has one or it would not be
    calling. It is started after the host is up and before anything can be clicked,
    so a rule recorded by the last run is followed from the first moment of this
    one rather than from the first edit.
    """
    watcher = SlotRuleWatcher(hass, host)
    watcher.async_start()
    await watcher.async_refresh()
    return watcher


class SlotRuleWatcher:
    """One listener per slot rule, following the rules the house holds."""

    def __init__(self, hass: HomeAssistant, host: Any) -> None:
        self._hass = hass
        self._host = host
        self._listeners: dict[tuple[str, str, str], _Listener] = {}
        self._unsubscribers: list[Callable[[], None]] = []

    # -- Following the rules ------------------------------------------------

    @callback
    def async_start(self) -> None:
        """Listen for the module platform saying a module was built again.

        The one signal that covers every way a rule can change: a rule is written
        by a command that then rebuilds the module it is on (`websocket_api
        .ws_module_set_slot_rule`), an install or an import writes one by writing
        a module, and a rebuild is the module platform's own announcement. So this
        watches the announcement rather than each of the three writers.
        """
        self._unsubscribers.append(
            async_dispatcher_connect(
                self._hass, SIGNAL_MODULES_CHANGED, self._modules_changed
            )
        )

    @callback
    def async_stop(self) -> None:
        """Drop every listener. Idempotent, because unload can run twice."""
        for listener in self._listeners.values():
            listener.async_stop()
        self._listeners.clear()
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def _modules_changed(self, _record: Any = None) -> None:
        """A module was built again; re-read which rules are in force."""
        self._hass.async_create_task(self.async_refresh())

    async def async_refresh(self) -> None:
        """Make the listeners match the rules, then settle the new ones.

        Reconciliation rather than rebuild-everything, and for the reason the
        rebinding path gives about rebuilding modules: a house may hold a dozen
        rules, and stopping and starting all of them on every module build would
        make editing one row cost a dozen listener churns -- and, worse, would
        re-evaluate rules that did not change, writing settings nothing asked to
        move.

        A listener that *is* new settles itself on the way in (see `_Listener
        .async_start`), which is what makes this the whole of "a rule is in force
        from the moment it exists": a template renders, a condition is evaluated,
        a flow's entity is read -- and a script is deliberately *not* called,
        because a restart is not a reason to run somebody's script.
        """
        wanted = await self._wanted()
        for key in list(self._listeners):
            if key not in wanted:
                self._listeners.pop(key).async_stop()
        for key, rule in wanted.items():
            existing = self._listeners.get(key)
            if existing is not None and existing.rule == rule:
                continue
            if existing is not None:
                existing.async_stop()
            listener = _Listener(self._hass, self._host, key, rule)
            self._listeners[key] = listener
            listener.async_start()

    async def _wanted(self) -> dict[tuple[str, str, str], rules.SlotRule]:
        """Every rule in force, by module, room and slot.

        Read from the *hosted* records rather than from the engine's installed
        packs, because a module's room is a fact about where it was put and the
        installed set does not carry one: a rule's settings live at its room's
        scope (`live_modules._scope_for`), so the room has to come from the module
        the house hosts. A module whose pack is gone from the installed set is
        skipped by `slot_rules_of` answering nothing, which is the right reading --
        there is no row left to hold a rule.
        """
        session = self._host.session
        wanted: dict[tuple[str, str, str], rules.SlotRule] = {}
        for record in await module_hosting.async_records(self._hass):
            found = live_modules.slot_rules_of(
                session, pack=record.slug, room_id=record.room_id
            )
            for slot, rule in found.items():
                wanted[(record.slug, record.room_id, slot)] = rule
        return wanted


class _Listener:
    """One slot rule, and whatever it takes to re-decide it.

    Four kinds, one shape: subscribe, evaluate, write through the host. The kind
    decides only *which* subscription and *how* a value is read, so everything
    after the value -- the refusal when the rule has gone, the save, the rebuild
    -- is written once.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: Any,
        key: tuple[str, str, str],
        rule: rules.SlotRule,
    ) -> None:
        self._hass = hass
        self._host = host
        self._module, self._room_id, self._slot = key
        self.rule = rule
        self._stop: list[Callable[[], None]] = []
        self._check: AndConditionChecker | None = None
        self._pending = False

    def async_start(self) -> None:
        """Subscribe according to the kind, and read the world once.

        The initial read is the reason a rule works the moment it is written
        rather than at the next state change: a template renders at once, a
        condition is evaluated at once, and a flow's entity is read at once. A
        script has no initial read, because nothing has happened yet and calling
        somebody's script because Home Assistant started is not a decision this
        module may make on their behalf.
        """
        kind = self.rule.kind
        if kind == "template":
            self._start_template()
        elif kind == "flow":
            self._start_flow()
        elif kind == "script":
            self._start_script()
        elif kind == "condition":
            self._hass.add_job(self._start_condition)
        else:  # pragma: no cover - `recorded_rule` reads a kind nobody wrote
            _LOGGER.warning(
                "the slot rule on %s of %s claims an unknown kind %r; nothing "
                "knows how to decide it and the slot stays where it is",
                self._slot,
                self._module,
                kind,
            )

    def async_stop(self) -> None:
        """Give back everything this rule subscribed to."""
        for unsubscribe in self._stop:
            unsubscribe()
        self._stop.clear()
        if self._check is not None:
            self._check.async_unload()
            self._check = None

    @property
    def _session(self) -> Any:
        return self._host.session

    # -- One method per kind, and each is only how a value is read ----------

    def _start_template(self) -> None:
        """Let Home Assistant render the text and tell us when it changes.

        The tracker re-renders when anything the text read changes *or* when the
        clock moves past a moment it mentions, which is exactly the behaviour a
        rule wants and exactly what a hand-written dependency walk would get
        wrong. An error inside the template is reported to the listener once and
        then again when it clears, so an error reads as "not saying anything now"
        -- the slot falls back to the room rather than being left on a device the
        text no longer names.
        """
        template = Template(self.rule.value, self._hass)
        self._stop.append(
            async_track_template_result(
                self._hass,
                [TrackTemplate(template, None)],
                self._template_rendered,
            ).async_remove
        )

    @callback
    def _template_rendered(self, _event: Any, updates: list[Any]) -> None:
        if not updates:
            return
        result = updates[-1].result
        if isinstance(result, TemplateError):
            _LOGGER.debug(
                "the slot rule on %s of %s did not render: %s",
                self._slot,
                self._module,
                result,
            )
            self._settle(None)
            return
        self._settle(_entity(str(result)))

    def _start_flow(self) -> None:
        """Follow the flow's own entity, which holds the entity id."""
        self._stop.append(
            async_track_state_change_event(
                self._hass, self.rule.value, self._flow_written
            )
        )
        self._flow_written()

    @callback
    def _flow_written(self, _event: Any = None) -> None:
        state = self._hass.states.get(self.rule.value)
        self._settle(_entity(None if state is None else state.state))

    def _start_script(self) -> None:
        """Watch what the person said should start it, and call it on a change."""
        self._stop.append(
            async_track_state_change_event(
                self._hass, list(self.rule.when), self._script_fired
            )
        )

    @callback
    def _script_fired(self, _event: Any = None) -> None:
        self._hass.async_create_task(self._async_call_script())

    async def _async_call_script(self) -> None:
        """Call the script and read its answer back out of the response.

        `return_response=True` is what makes the answer arrive at all: a script
        hands a value back by `stop: response_variable:`, and Home Assistant
        passes that mapping to the caller only when it is asked for one. The key
        is `slot_rules.RESPONSE`, which is the same name a detached cast's value
        takes -- one spelling for "the value this logic worked out", so a script
        does not have to know which screen called it.

        A response without that key is logged rather than guessed at. A script
        that returns one unnamed value is a script the person can fix, and a
        watcher that took whatever it found would put a device into a slot that
        nobody named -- which is the one mistake here that would be invisible.
        """
        try:
            response = await self._hass.services.async_call(
                "script",
                self.rule.value,
                {},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, ValueError) as failure:
            _LOGGER.warning(
                "the script behind the slot rule on %s of %s could not be called: %s",
                self._slot,
                self._module,
                failure,
            )
            return
        if not isinstance(response, Mapping) or rules.RESPONSE not in response:
            _LOGGER.warning(
                "the script behind the slot rule on %s of %s answered nothing "
                "under %r, so the slot cannot be decided by it; it returned %r",
                self._slot,
                self._module,
                rules.RESPONSE,
                response,
            )
            return
        self._settle(_entity(response[rules.RESPONSE]))

    async def _start_condition(self) -> None:
        """Build Home Assistant's own checker, and watch what it reads.

        The condition is not translated into anything here (the same reason
        `binary_sensor` gives for its derived conditions): every condition type a
        person can build works, including the ones this integration has never
        heard of, because the code that decides is Home Assistant's. A condition
        that will not build is logged and *settles nothing*, leaving the slot on
        whatever the watcher last wrote rather than pretending it said no.
        """
        try:
            checkers = [
                await async_from_config(self._hass, row)
                for row in condition_rows(self.rule.value)
            ]
            checker = AndConditionChecker(self._hass, checkers)
            await checker.async_setup()
        except (ConditionError, HomeAssistantError, ValueError, KeyError) as failure:
            _LOGGER.warning(
                "the condition on %s of %s could not be built, so the slot will "
                "stay where it is: %s",
                self._slot,
                self._module,
                failure,
            )
            return
        self._check = checker
        for target in watched_targets(self.rule.value):
            # Awaited, because this one is a coroutine function returning the
            # unsubscribe rather than returning it outright -- the same shape
            # `binary_sensor` records for the same helper.
            self._stop.append(
                await async_track_target_selector_state_change_event(
                    self._hass, target, self._condition_changed
                )
            )
        self._stop.append(
            async_track_time_interval(
                self._hass,
                self._condition_changed,
                timedelta(seconds=TICK_SECONDS),
            )
        )
        self._condition_changed()

    @callback
    def _condition_changed(self, _event: Any = None) -> None:
        if self._check is None:
            return
        try:
            answered = self._check.async_check()
        except ConditionError as unreadable:
            _LOGGER.debug(
                "%s of %s could not be evaluated: %s",
                self._slot,
                self._module,
                unreadable,
            )
            answered = None
        # Unknown reads as *not holding*, which is the safe direction here: an
        # unreadable condition leaves the slot on the room's binding rather than
        # on a device somebody picked for a moment that has passed.
        self._settle(rules.device_of(self.rule) if answered else None)

    # -- The one write every kind ends at -----------------------------------

    @callback
    def _settle(self, entity_id: str | None) -> None:
        """Hand the value to the house, if it is not the one already there.

        Scheduled rather than awaited, because every caller is a Home Assistant
        callback and the write is asynchronous: the listener fires, this makes a
        task, and the answer comes back through the host.
        """
        self._hass.async_create_task(self._async_settle(entity_id))

    async def _async_settle(self, entity_id: str | None) -> None:
        if self._pending:
            # One write in flight at a time. Two settles for the same rule would
            # otherwise race -- a template rendering twice inside one save -- and
            # the later-started one is the correct final answer, so the guard
            # defers rather than queues: the next evaluation of a template, a
            # condition or a state change will arrive on its own.
            return
        self._pending = True
        try:
            changed = live_modules.settle_slot_rule(
                self._session,
                room_id=self._room_id,
                pack=self._module,
                slot=self._slot,
                entity_id=entity_id,
            )
        except LiveSessionError as refusal:
            # The rule this listener was made for has gone -- somebody took it
            # off the row while this change was in flight. The refresh that
            # follows the write will drop this listener; saying so here is what
            # makes the one window where that has not happened yet visible.
            _LOGGER.debug(
                "%s of %s no longer has a rule on %s: %s",
                self._module,
                self._room_id,
                self._slot,
                refusal,
            )
            return
        finally:
            self._pending = False
        if changed:
            await self._host.async_slot_changed(self._room_id, self._slot)


def _entity(value: Any) -> str | None:
    """An entity id from whatever a rule produced, or nothing.

    Text, trimmed, and only a dotted `domain.object_id` is an entity: a template
    that rendered an empty string, a number, or a sentence is a rule that did not
    name a device, and the honest reading is "nothing" -- which puts the slot back
    on the room's binding rather than writing a value no adapter can resolve.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "." not in text or " " in text:
        return None
    return text
