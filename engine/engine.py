"""The engine: one tick, one record per behaviour evaluation, one write per entity.

This is where `engine-core`'s parts meet. A tick observes the house, releases the
overrides whose conditions now hold, evaluates every behaviour once per scope,
reduces the proposals to one command per entity, runs them past the three
suppression stages and the safety veto, actuates what survives, and appends a
record for every evaluation it made -- enabled or skipped, acted or declined.

The order of those stages is the phase's central seam and is fixed here rather
than left to each behaviour (`design.md` D6):

    arbitration -> manual override -> rate limit -> safety veto -> actuation

Arbitration first, because it decides *which* command an entity receives and the
stages after it judge that one command rather than a field of candidates. Override
before the limit, because a command a person's hand suppressed should not also
spend the entity's allowance -- otherwise a room somebody is using would exhaust
the budget its own automation needs the moment they leave. Safety last, because
it is the only stage that can refuse a command the engine fully intends to send,
and `refused: unsafe` should mean nothing else was going to stop it.

Three properties are worth stating because they are what the phase is *for* and
each is a decision rather than an accident:

- **Every evaluation leaves exactly one record.** A disabled behaviour, a
  behaviour whose required slot is unbound, a behaviour that declined, and a
  behaviour that acted all append. `design.md` D2 turns the whole oracle on
  telling "off because the rule behaved" from "off because the light was never
  on", and that difference is invisible in device state.
- **The tick is a pure function of the state it starts from.** Nothing here reads
  a wall clock -- `now` is the injected `Clock`'s position, and the clock is
  supplied by the composition root because the engine may not import `sim/`
  (`design.md` D12). Nothing here iterates an unordered collection; the behaviours
  are visited by ascending `id`, the rooms in the house's own order, and the
  contentions by entity id, so two runs at one seed append identical logs.
- **The engine is the only writer.** A behaviour reaches the house through
  `propose` and through nothing else, so a command that was suppressed, vetoed or
  lost is a fact in the log rather than a write that happened anyway.

The engine's own half of the snapshot is `state()`, and it is JSON-safe because
`sim/snapshot.py` serialises it whole. Restoring means constructing a new `Engine`
over the rebuilt adapter and clock with that mapping as `state`, which is why the
constructor takes it: a restore is a fresh engine that resumes, not a mutation of
the one that stopped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from engine.adapter import (
    ChangeContext,
    ChangeOrigin,
    EntityView,
    HouseAdapter,
)
from engine.arbitration import Proposal, arbitrate
from engine.behaviours import behaviour_defaults, default_behaviours, suppresses
from engine.behaviours.base import (
    Behaviour,
    BehaviourContext,
    BehaviourError,
    BehaviourScope,
    enable_key,
    module_enable_key,
    priority_key,
    scope_key,
)
from engine.behaviours.declared import option_key, slot_entity_key, slot_part_key
from engine.binding import (
    House,
    HouseScope,
    Reduction,
    RoomScope,
    Scope,
    SlotBinding,
    SlotRead,
    resolve_slot,
)
from engine.config import (
    BUILTIN_DEFAULTS,
    LOG_BOUND_KEY,
    PRESENCE_QUIET_TIMEOUT_KEY,
    RATE_LIMIT_BOUND_KEY,
    RATE_LIMIT_WINDOW_KEY,
    ConfigResolver,
    ResolvedSetting,
)
from engine.decision_log import (
    DecisionLog,
    DecisionRecord,
    HazardReading,
    HousePresence,
    Input,
    ModeReading,
    ModeRequest,
    ModuleSuppression,
    Outcome,
    OverrideNote,
    ProposedCommand,
    Repair,
    StateChange,
)
from engine.declared_slots import QUALIFIER
from engine.dwell import DwellRegistry
from engine.install import InstalledSet
from engine.modes import ModeSet
from engine.overrides import OverrideRegistry, ResetCondition
from engine.rate_limit import RateLimiter
from engine.safety import Hazard, hazard_kind, refuses
from engine.solar import Location, sun_elevation

#: The slot both the quiet-timer and the emptiness claim are read from. Named
#: once because `first-behaviours` and `engine-core`'s `room_emptied` must agree
#: on what "the room is quiet" was measured from, and two spellings would let a
#: house tune one and not the other.
MOTION_SLOT = "motion_sensor"

#: The state a binary sensor reads when it reports activity.
_ACTIVE = "on"


class EngineError(Exception):
    """Base for the failures this module defines."""


@runtime_checkable
class Clock(Protocol):
    """The one time source the engine reads.

    A protocol rather than an import, so `engine/` never reaches into `sim/`
    (`design.md` D12) while still reading the run's single virtual clock. The
    property is read-only on purpose: an engine that could move the clock could
    make time pass, and time in this phase passes only when a scenario says so.
    """

    @property
    def now(self) -> datetime:
        """The current virtual instant."""
        ...


class Engine:
    """The decision engine over one house and one adapter.

    Constructed by the composition root with the clock, the fixture's location and
    the configuration layers; it builds its own sub-state -- the log, the override
    registry, the rate limiter, the dwell registry and the layered resolver -- so
    that the engine's own half of the snapshot has exactly one author.
    """

    def __init__(
        self,
        *,
        adapter: HouseAdapter,
        house: House,
        clock: Clock,
        location: Location,
        modes: ModeSet,
        behaviours: Iterable[Behaviour] | None = None,
        house_settings: Mapping[str, object] | None = None,
        room_settings: Mapping[str, Mapping[str, object]] | None = None,
        profile_settings: Mapping[str, object] | None = None,
        profile_room_settings: Mapping[str, Mapping[str, object]] | None = None,
        installed: InstalledSet | None = None,
        module_rooms: Mapping[str, str] | None = None,
        state: Mapping[str, object] | None = None,
    ) -> None:
        self._adapter = adapter
        self._house = house
        self._clock = clock
        self._location = location
        self._modes = modes
        self._installed = InstalledSet() if installed is None else installed
        #: Which room each installed pack was put in, by pack name. The installed
        #: set says a pack is *in the house* and the rooms say which slots it
        #: binds, and neither says where a person put it -- which is the one fact
        #: a house-scoped behaviour narrowed to a room needs, because narrowing
        #: means "the room the module sits in" and the button that fires it is
        #: there. Empty is the honest answer for a caller that never placed one
        #: (`openhouse.facade`, and any house built before this mapping existed),
        #: and `_scopes` handles it.
        self._module_rooms: Mapping[str, str] = (
            {} if module_rooms is None else dict(module_rooms)
        )
        self._behaviours: Mapping[str, Behaviour] = (
            _by_id(behaviours) if behaviours is not None else default_behaviours()
        )
        self._settings = ConfigResolver(
            builtin={
                **BUILTIN_DEFAULTS,
                **behaviour_defaults(self._behaviours.values()),
            },
            house=house_settings,
            rooms=room_settings,
            profile=profile_settings,
            profile_rooms=profile_room_settings,
        )
        self._overrides = OverrideRegistry()
        self._dwell = DwellRegistry()
        self._log = DecisionLog(bound=self.count(LOG_BOUND_KEY, HouseScope()))
        self._rate = RateLimiter(
            bound=self.count(RATE_LIMIT_BOUND_KEY, HouseScope()),
            window=timedelta(seconds=self.seconds(RATE_LIMIT_WINDOW_KEY, HouseScope())),
        )
        self._released: Mapping[str, ResetCondition] = {}
        if state is not None:
            self._adopt(state)

    # -- The surface the composition root and the control surface read -------

    def _restore_installed(self, recorded: object) -> InstalledSet:
        """The installed set a snapshot records, checked against this registry.

        The check is the reason a restore cannot resurrect a house whose packs
        are gone: the record names the units each pack registered, and a house
        rebuilt without them -- because the pack was uninstalled, or because this
        build never had it -- would be a house claiming to hold a pack whose
        behaviours do not exist. Refusing names the pack, so the diagnosis is
        "install it again" rather than a run that quietly decides differently.

        It is the same shape as the bindings check below and for the same reason:
        a snapshot restores onto the house it was taken from, and the seam is
        where that is said rather than three decisions later.
        """
        installed = InstalledSet.from_document(_mapping(recorded, "installed_packs"))
        for name in installed.names:
            missing = [
                unit
                for unit in installed.packs[name].behaviours
                if unit not in self._behaviours
            ]
            if missing:
                raise EngineError(
                    f"the snapshot holds {name!r}, whose behaviours {sorted(missing)} "
                    "this build does not register; a pack has to be installed "
                    "before a snapshot that holds it can be restored"
                )
        return installed

    @property
    def installed(self) -> InstalledSet:
        """The packs this house holds, with the facts that explain why."""
        return self._installed

    @property
    def house(self) -> House:
        """The house this engine decides for."""
        return self._house

    @property
    def house_adapter(self) -> HouseAdapter:
        """The port. Exposed for the controls that are the port's, not the engine's."""
        return self._adapter

    @property
    def settings(self) -> ConfigResolver:
        """The layered resolver, so a control can set and clear an override."""
        return self._settings

    @property
    def modes(self) -> ModeSet:
        """The house's modes, so a control can set one."""
        return self._modes

    @property
    def overrides(self) -> OverrideRegistry:
        """The override registry, so a control can clear one explicitly."""
        return self._overrides

    @property
    def log(self) -> DecisionLog:
        """The decision log. Read through `window`, never enumerated for control."""
        return self._log

    @property
    def behaviours(self) -> Mapping[str, Behaviour]:
        """The registered units, keyed by `id`."""
        return self._behaviours

    @property
    def clock(self) -> Clock:
        """The injected clock. The engine never reads another."""
        return self._clock

    @property
    def location(self) -> Location:
        """The fixture's location, which is configuration and not engine state."""
        return self._location

    def now(self) -> datetime:
        """The current virtual instant."""
        return self._clock.now

    def sun_elevation(self) -> float:
        """The sun's elevation at this instant for the fixture's location."""
        return sun_elevation(at=self._clock.now, location=self._location)

    # -- Hazards and the sensors that stopped answering ----------------------

    def hazards(self) -> tuple[Hazard, ...]:
        """Every hazard detector alarming right now, ordered by entity id.

        Found by device class rather than by a slot (`engine/safety.py`): a smoke,
        CO or leak detector is a `binary_sensor` whose `device_class` is an alert
        class and which reads `on`. The house is enumerated through the port's
        engine-facing `list_entities`, which no behaviour may reach -- a behaviour
        asks the context for the hazards it may answer, and the scan stays here.
        """
        found = (
            Hazard(entity_id=entity_id, kind=kind)
            for entity_id in sorted(self._adapter.list_entities())
            if (kind := hazard_kind(self._adapter.read_entity(entity_id))) is not None
        )
        return tuple(found)

    def repairs(self) -> tuple[Repair, ...]:
        """Every bound sensor that stopped answering, ordered by room then slot.

        The engine's half of the HA integration's Repairs: a room whose motion
        sensor has gone unavailable can no longer be read, and "unavailable is not
        off" means the house must say so rather than let the silence pass for a
        clear room. A sensor that is *gone* -- the device a person bound and then
        removed from the house -- is at least as much a repair, and the same read
        reaches it: `SlotRead.views` returns an absent member as a present-but-
        unreadable view (`engine.binding._absent`), so it is reported here rather
        than raising out of this scan and taking the Rooms tab with it. Derived
        from the port on each call rather than stored, because it is a fact about
        the devices and not about the engine's state -- a repair that persisted
        after the sensor came back would train a person to ignore repairs
        (`custom_components/open_house/repairs.py`).
        """
        repairs: list[Repair] = []
        for room in self._house.rooms:
            binding = resolve_slot(self._house, RoomScope(room.id), MOTION_SLOT)
            if binding.is_empty:
                continue
            views = binding.read(Reduction.ANY).views(self._adapter)
            if views and not any(view.available for view in views):
                for view in views:
                    repairs.append(
                        Repair(
                            room_id=room.id, slot=MOTION_SLOT, entity_id=view.entity_id
                        )
                    )
        return tuple(repairs)

    # -- Settings ------------------------------------------------------------

    def resolve(self, key: str, scope: Scope) -> ResolvedSetting:
        """Resolve `key` for `scope` through the layered resolver."""
        return self._settings.resolve(key, scope)

    def resolve_or(self, key: str, scope: Scope, default: object) -> ResolvedSetting:
        """Resolve `key`, falling back to `default` at the built-in layer."""
        return self._settings.resolve_or(key, scope, default)

    def seconds(self, key: str, scope: Scope) -> float:
        """A duration-valued engine setting, in seconds.

        Resolved with `resolve` rather than `resolve_or`: every key this is called
        with has a built-in default, so a key that reaches here and resolves to
        nothing means an engine constant was dropped from `BUILTIN_DEFAULTS`, and
        a fallback would hide that as a zero-second duration.
        """
        return self._settings.resolve(key, scope).number()

    def count(self, key: str, scope: Scope) -> int:
        """A count-valued engine setting, such as the log's bound."""
        return self._settings.resolve(key, scope).integer()

    # -- The tick ------------------------------------------------------------

    def advance(self, delta: timedelta) -> tuple[DecisionRecord, ...]:
        """Move the clock by `delta` and tick, unless `delta` is zero.

        A zero advance produces no tick, which is `engine-core`'s "advancing the
        clock by zero changes nothing" stated as a branch: producing one would
        append a record and possibly a command for an instant that did not pass.
        """
        if delta <= timedelta(0):
            return ()
        moved = self._clock
        advance = getattr(moved, "advance", None)
        if advance is None:
            raise EngineError("the injected clock cannot be advanced")
        advance(delta)
        return self.tick()

    def tick(self) -> tuple[DecisionRecord, ...]:
        """Evaluate, resolve, actuate and record one tick. Returns what was recorded."""
        now = self._clock.now
        self._observe(now)
        self._released = dict(
            self._overrides.lapse(
                now=now,
                empty_rooms=self.empty_rooms(now),
                active_modes=self._modes.active,
            )
        )
        drafts = self._evaluate(now)
        self._resolve(drafts, now)
        self._enter_modes(drafts)
        records = tuple(draft.record() for draft in drafts)
        for record in records:
            self._log.append(record)
        return records

    # -- Observation ---------------------------------------------------------

    def _observe(self, now: datetime) -> None:
        """Record this tick's motion reading for every room that has a sensor.

        A room with no `motion_sensor` bound is left unobserved rather than
        recorded as quiet: "nothing is watching this room" and "this room is
        empty" are different facts, and only the second may release an override
        or support a claim that the house has emptied.

        A sensor that is bound but cannot answer is the third case, and it is
        *not* treated as a clear reading. `available is not off` (`house-adapter`)
        means a dead detector's silence is "unknown", not "empty"; recording it as
        quiet would let a room whose sensor died read as vacated, and a house
        whose only sensor died read as empty enough to shut its own lighting down.
        The observation is marked unreadable so `quiet` refuses to answer, and the
        repair reaches the log through whichever evaluation reads the dead slot.
        """
        for room in self._house.rooms:
            binding = resolve_slot(self._house, RoomScope(room.id), MOTION_SLOT)
            if binding.is_empty:
                continue
            read = binding.read(Reduction.ANY)
            views = read.views(self._adapter)
            self._dwell.observe(
                room.id,
                MOTION_SLOT,
                active=any(_reports_motion(view) for view in views),
                at=now,
                known=any(view.available for view in views),
            )

    def empty_rooms(self, now: datetime) -> tuple[str, ...]:
        """The rooms whose motion has been clear for the presence quiet timeout.

        This is `room_emptied`'s condition and half of `presence.house_emptied`.
        Both read the same number under the engine's own key so that a house
        cannot tune "the room emptied" and "the house is empty" apart.
        """
        return self._dwell.quiet_rooms(
            slot=MOTION_SLOT, at=now, timeout=self.presence_timeout()
        )

    def presence_timeout(self) -> timedelta:
        """The quiet period after which a room, and so a house, reads as empty."""
        return timedelta(seconds=self.seconds(PRESENCE_QUIET_TIMEOUT_KEY, HouseScope()))

    # -- Evaluation ----------------------------------------------------------

    def _evaluate(self, now: datetime) -> list[_Draft]:
        """Evaluate every behaviour once per scope, in a fixed order.

        The order is ascending `id`, then the house's own room order, so the
        records a tick appends are in the same sequence on every replay. A
        room-scoped unit is evaluated once per room because its required slots
        are that room's; a house-scoped one is evaluated once. Both of those are
        the unit's *declared* scope, and which scopes a unit actually runs in is
        a setting -- `_scopes` is where the two are reconciled.
        """
        drafts: list[_Draft] = []
        for unit in self._behaviours.values():
            for scope in self._scopes(unit):
                drafts.append(self._evaluate_one(unit, scope, now))
        return drafts

    def _scopes(self, unit: Behaviour) -> tuple[Scope, ...]:
        """The scopes this unit is evaluated in, chosen before it runs.

        A unit's declared scope is what its pack is *about* and the resolver's
        answer when nobody has said otherwise, so the two words are read through
        `scope_key` rather than off the unit. The setting is the whole point: a
        bedtime pack is one behaviour that dims the room it sits in and another
        that shuts the house down, and which of the two a person wants is not
        something the manifest can know.

        Narrowing a house unit to `room` runs it in the one room its pack was
        installed into, and nowhere else. The alternative -- running it in every
        room -- would turn one house-wide act into a dozen room acts, when the
        button that fired it lives in exactly one room and the person who
        configured it meant *that* room. The room is not this engine's to guess:
        `module_rooms` is the placement the live path records, and an engine
        built without one falls back to the rooms the unit is switched on in,
        which is the nearest thing the engine knows on its own. A narrowed unit
        with neither runs nowhere, which is what "off" already means.

        Widening a room unit to `house` is not symmetric with that, and is not
        meant to be: the same rule runs once for the whole house, against the
        house's collected bindings, so "the lights" become every room's lights.
        That is a real thing to want ("when anyone comes home, light the house")
        and it costs nothing to allow, because `resolve_slot` already answers at
        house scope for any slot the house scope makes available.
        """
        chosen = self._settings.resolve_or(
            scope_key(unit.id), HouseScope(), str(unit.scope)
        ).text()
        if chosen == BehaviourScope.HOUSE:
            # A house unit runs at house scope because that is what it declared.
            # A room unit runs there only when the house can resolve every slot it
            # names; a setting asking for more than the house can answer is left
            # unhonoured rather than honoured into a `resolve_slot` refusal, which
            # would take the whole tick down over one line of configuration.
            if unit.scope is BehaviourScope.HOUSE or self.reaches_house(unit.id):
                return (HouseScope(),)
            return tuple(RoomScope(room.id) for room in self._house.rooms)
        if unit.scope is not BehaviourScope.HOUSE:
            return tuple(RoomScope(room.id) for room in self._house.rooms)
        return self._narrowed_rooms(unit)

    def active_rooms(self, unit_id: str) -> tuple[str, ...]:
        """The rooms a unit is actually switched on in, in the house's own order.

        A different question from `_scopes`', and the one a person asking "where
        does this apply" means. `_scopes` says where a unit is *evaluated* --
        every room, for a unit its pack declared room-scoped -- and this says
        which of those evaluations the enable flag lets through: a room whose
        flag is off is `skipped: disabled` on every tick rather than run in, so a
        control offering the evaluated rooms would offer rooms the engine never
        acts in.

        The scope setting is honoured because `_scopes` is what reads it: a unit
        widened to `house` evaluates at house scope and answers no rooms at all,
        which is the honest reading -- it runs once for the whole house and in
        none of its rooms. The module's master flag is read first because the
        gate reads it first: a pack switched off runs nowhere, whatever its
        atoms' own flags say.

        Asked of the engine by anything that has to draw the answer
        (`ha_adapter.live_modules`'s module view) rather than recomputed there,
        because "where does this run" is one question and a second answer to it
        is free to drift from the first. The flag's fallback is the unit's own
        declared `enabled`, which is what `_gate` falls back to -- not the
        `False` `_flag` in the live layer uses, because this is the gate's
        question and not the panel's.
        """
        unit = self._behaviours.get(unit_id)
        if unit is None:
            return ()
        if unit.module is not None:
            master = self._settings.resolve_or(
                module_enable_key(unit.module), HouseScope(), False
            )
            if not master.flag():
                return ()
            # A module another module is holding off runs nowhere, which is the
            # same answer as "switched off" and the honest one: the ticks are
            # empty and the person asking "where does this apply" is owed the
            # reason in the module view, not a reach this control would offer to
            # widen.
            if self.suppressed_by(unit.module) is not None:
                return ()
        return tuple(
            scope.room_id
            for scope in self._scopes(unit)
            if isinstance(scope, RoomScope)
            and self._settings.resolve_or(
                enable_key(unit.id), scope, unit.enabled
            ).flag()
        )

    def suppressions(self) -> Mapping[str, tuple[str, str]]:
        """Which modules are held off right now, and by whom: pack -> (pack, atom).

        One module switching another off, derived rather than stored. The
        suppressor declares the packs it overrides (`pack-manifest`'s
        `suppresses`), and this asks of the house which of those declarers are
        currently switched on -- so a suppression has no lifecycle of its own to
        get wrong: the target's own switch is untouched, nothing is written down,
        and the moment the suppressor goes off the target is back. "Never
        permanently" is therefore not a rule this method obeys but a fact about
        where the answer lives.

        A suppressor is switched on when it is on in *any* scope it is evaluated
        in (`_switched_on`), which is the reading a person means by "the motion
        lighting is running" -- and deliberately the reading `_gate` makes with
        the suppression stage removed, so a suppressor's own state is never asked
        through the thing it decides. Two packs suppressing each other therefore
        both go quiet, which is the fixpoint the reading gives and not a loop.

        First declarer wins when two packs name the same target, in the engine's
        fixed ascending-`id` order, so the answer a screen draws is the same on
        every rebuild; the loser's declaration is still visible in the pack's own
        manifest, and a house that cares can turn one of the two off.
        """
        held: dict[str, tuple[str, str]] = {}
        for unit in self._behaviours.values():
            for target in suppresses(unit):
                # A pack cannot hold itself off: the clause would be a pack
                # saying "I am off", which is the module switch and not this.
                # `engine/manifest.py` refuses it at authoring time; the skip
                # here is what keeps a hand-edited document from making a module
                # that can never run and cannot be explained by any switch.
                if target == unit.module or target in held:
                    continue
                if self._switched_on(unit):
                    held[target] = (unit.module or unit.id, unit.id)
        return held

    def suppressed_by(self, module: str) -> str | None:
        """The pack holding `module` off, or `None` when nothing is."""
        found = self.suppressions().get(module)
        return None if found is None else found[0]

    def _switched_on(self, unit: Behaviour) -> bool:
        """Whether `unit` is switched on in any scope it is evaluated in.

        The module master flag and the unit's own enable flag, asked in every
        scope `_scopes` gives -- the same two gates `_gate` applies, and the
        reason this is a method rather than a line inside `suppressions`: it must
        *not* consult the suppression, or two packs naming each other could never
        be answered.
        """
        if (
            unit.module is not None
            and not self._settings.resolve_or(
                module_enable_key(unit.module), HouseScope(), False
            ).flag()
        ):
            return False
        return any(
            self._settings.resolve_or(enable_key(unit.id), scope, unit.enabled).flag()
            for scope in self._scopes(unit)
        )

    def reaches_house(self, unit_id: str) -> bool:
        """Whether unit `unit_id` can be evaluated at house scope at all.

        House scope resolves each of a unit's slots by collecting it from every
        room (`engine.binding.resolve_slot`), and a slot the house scope does not
        make available -- a room type's own, or a device a pack declared to be
        held separately -- has no such reading. So the question "may this
        behaviour run for the whole house" is a question about its slots, and
        this is that question asked once, by the engine, for the two callers that
        need the same answer: `_scopes`, which must not be crashed by a setting a
        hand-edited file wrote, and the panel, which must not offer a switch the
        engine would refuse to honour.

        Required slots are asked about as well as declared ones, because the gate
        resolves the required list at the chosen scope before the behaviour ever
        runs: widening a unit whose pack requires a room-only slot would skip it
        with `unbound slot` on every tick rather than doing anything.
        """
        unit = self._behaviours.get(unit_id)
        if unit is None:
            return False
        return all(
            slot in self._house.house_scope_slots
            for slot in (*unit.required_slots, *unit.slots)
        )

    def _narrowed_rooms(self, unit: Behaviour) -> tuple[Scope, ...]:
        """Where a house-scoped unit runs once a person has narrowed it.

        The module's own room when the engine was told it, and otherwise the
        rooms the unit is enabled in. The second is not a second policy: it is
        the same question -- "where does this run" -- answered from the flags
        when the placement is unknown, so a house whose engine was built by the
        simulator narrows to something rather than to nothing.
        """
        placed = None if unit.module is None else self._module_rooms.get(unit.module)
        if placed is not None and self._house.has_room(placed):
            return (RoomScope(placed),)
        return tuple(
            RoomScope(room.id)
            for room in self._house.rooms
            if self._settings.resolve_or(
                enable_key(unit.id), RoomScope(room.id), unit.enabled
            ).flag()
        )

    def _evaluate_one(self, unit: Behaviour, scope: Scope, now: datetime) -> _Draft:
        """One unit in one scope: gate it, or run it, and never neither."""
        gate = self._gate(unit, scope, now)
        if gate is not None:
            return gate
        ctx = _Evaluation(
            engine=self, actor=unit.id, scope=scope, now=now, pack=unit.module
        )
        unit.evaluate(ctx)
        return _Draft(
            actor=unit.id,
            scope=scope,
            now=now,
            inputs=ctx.inputs,
            rule=ctx.rule,
            commands=ctx.commands,
            modes=ctx.modes,
            priority=self._priority(unit),
        )

    def _gate(self, unit: Behaviour, scope: Scope, now: datetime) -> _Draft | None:
        """The record a gated unit leaves instead of evaluating, or `None` to run it.

        Three gates, all before any rule is reached, which is why all three
        leave `rule` as `None`: the enable flag (`engine-core`: "the evaluation
        gate SHALL evaluate a behaviour's policy only when that behaviour is
        enabled"), the required slots (`engine-core`: a required slot that
        resolves empty "SHALL be skipped ... recorded with outcome `skipped:
        unbound slot` naming the behaviour and the slot"), and a module another
        module is holding off. A module's flag gates its whole family ahead of
        the unit's own, and the suppression is read after it so the record says
        which of the two it was.
        """
        if unit.module is not None:
            module_flag = self._settings.resolve_or(
                module_enable_key(unit.module), HouseScope(), False
            )
            if not module_flag.flag():
                return _skipped(unit, scope, now, Outcome.SKIPPED_DISABLED, (), None)
            # Another module holding this one off is a third gate beside the two
            # `_gate`'s docstring names, and it sits *after* the module's own
            # switch because the two are different answers: "you switched it off"
            # and "another module is holding it off". The record carries who.
            holder = self.suppressions().get(unit.module)
            if holder is not None:
                return _skipped(
                    unit,
                    scope,
                    now,
                    Outcome.SKIPPED_SUPPRESSED,
                    (
                        ModuleSuppression(
                            module=unit.module, by=holder[0], behaviour=holder[1]
                        ),
                    ),
                    None,
                )
        flag = self._settings.resolve_or(enable_key(unit.id), scope, unit.enabled)
        if not flag.flag():
            return _skipped(unit, scope, now, Outcome.SKIPPED_DISABLED, (flag,), None)
        for slot in unit.required_slots:
            binding = resolve_slot(
                self._house,
                scope,
                _slot_key(
                    slot,
                    _slot_part(self._settings, self._house, unit.module, scope, slot),
                ),
                own=_slot_override(self._settings, unit.module, scope, slot),
            )
            if binding.is_empty:
                return _skipped(
                    unit,
                    scope,
                    now,
                    Outcome.SKIPPED_UNBOUND_SLOT,
                    (_unbound(binding),),
                    None,
                )
        return None

    def _priority(self, unit: Behaviour) -> int:
        """The unit's arbitrated priority, resolved at house scope.

        House scope because arbitration is per *entity* and an entity can be
        named by units evaluated in different rooms -- a house-scoped shutdown and
        a room-scoped motion rule can both want one light -- so a priority that
        varied by room would leave the same pair of proposals ranked differently
        depending on which room was asked.
        """
        return self._settings.resolve_or(
            priority_key(unit.id), HouseScope(), unit.priority
        ).integer()

    # -- Resolution ----------------------------------------------------------

    def _resolve(self, drafts: Sequence[_Draft], now: datetime) -> None:
        """Reduce the tick's proposals and apply what survives, per entity.

        Every proposal is built first, because arbitration ranks a *field* of
        candidates and cannot start until all of them exist; then each entity's
        contention is settled in turn. The draft a proposal came from is tracked
        by `id` rather than by value: two units may propose commands that compare
        equal -- `Proposal` is a frozen dataclass and so hashable -- and an index
        built on equality would then credit one draft's fate to another's record.
        """
        proposals: list[Proposal] = []
        source: dict[int, int] = {}
        for index, draft in enumerate(drafts):
            for command in draft.commands:
                proposal = Proposal(
                    actor=draft.actor,
                    rule=draft.rule,
                    priority=draft.priority,
                    command=command,
                )
                proposals.append(proposal)
                source[id(proposal)] = index

        for contention in arbitrate(proposals):
            winner = contention.winner
            index = source[id(winner)]
            entity_id = contention.entity_id
            outcome, change = self._apply(winner, entity_id, now)
            drafts[index].fates.append((entity_id, outcome))
            note = self._override_note(entity_id, outcome)
            if note is not None:
                drafts[index].inputs.append(note)
            if change is not None:
                drafts[index].delta.append(change)
            for loser in contention.losers:
                drafts[source[id(loser)]].fates.append(
                    (entity_id, Outcome.LOST_ARBITRATION)
                )

    def _enter_modes(self, drafts: Sequence[_Draft]) -> None:
        """Put the house in the modes this tick's evaluations asked for.

        After resolution rather than during it, because a mode is a fact about
        the house rather than a write to a device and the two do not arbitrate
        against each other: a behaviour that enters Sleep and a behaviour that
        turns a lamp off are both satisfied, where two commands for one lamp
        would not be. The order is `(priority, mode)` ascending, so a tick whose
        two behaviours ask for different modes of one exclusive group settles the
        way arbitration would -- the higher priority last, and so left active --
        and two runs of one scenario enter them in the same sequence. A mode
        requested twice is entered once, which is what `dict.fromkeys` is doing
        here rather than a set: the order has to survive the deduplication.
        """
        requested = sorted(
            (draft.priority, mode) for draft in drafts for mode in draft.modes
        )
        for _, mode in dict.fromkeys(requested):
            self._modes.activate(mode)

    def _override_note(self, entity_id: str, outcome: Outcome) -> OverrideNote | None:
        """The override fact that explains `outcome` for `entity_id`, if any.

        A suppressed command names the conditions still being awaited -- so "the
        engine chose not to act because a person is in charge" is explainable and
        not merely visible. A command that *did* act on an entity released this
        tick names the condition that released it, because `engine-core` requires
        the condition that ended an override to appear in the record that resumes
        acting, and that record is this one.

        Nothing is reported for the other outcomes: a command that lost an
        arbitration or was rate-limited was not suppressed by an override, and a
        note saying so would be a fact about a decision this record did not make.
        """
        if outcome is Outcome.OVERRIDDEN:
            return OverrideNote(
                entity_id=entity_id, awaited=self._overrides.awaited_for(entity_id)
            )
        if outcome is Outcome.ACTED and entity_id in self._released:
            return OverrideNote(entity_id=entity_id, released=self._released[entity_id])
        return None

    def _apply(
        self, proposal: Proposal, entity_id: str, now: datetime
    ) -> tuple[Outcome, StateChange | None]:
        """Run one winning command past the suppression stages and the veto.

        One entity at a time, and one write at a time: a command that named
        several entities may win one and lose another, so the write is the
        command's action against this entity alone rather than the command's own
        entity tuple (`engine/arbitration.py` states the same contract from the
        other side).

        The four stages are in `design.md` D6's order and each is a different
        answer to "why not": an override is a person's hand, a rate limit is the
        engine's own budget, a refusal is the product rule, and `acted` is the
        only one that writes. The veto judges the *command's* context rather than
        the engine's, so a user-origin command that reached here -- a future
        control surface's direct action, arbitrated like any other -- is permitted
        to unlock a door, while every behaviour's `engine`-origin command is not.

        A **safety** command skips the override and the rate limit but not the
        veto. Those two are the stages that can silently swallow a warning: an
        override is a person's hand on a lamp and a rate limit is the engine's own
        budget, and neither is a reason a fire alarm goes unannounced. The veto is
        not skipped -- it is not a suppression stage at all, and a hazard response
        that reached for a lock would still be refused, because the safety audit's
        two rules must not cancel: bypassing the stages that silence an alert may
        never become a way past the rule that nothing auto-unlocks a door. The
        rate limiter is not consulted, so an alert spends no allowance and cannot
        be starved by one either.
        """
        command = proposal.command
        if not command.safety:
            if self._overrides.is_overridden(entity_id):
                return Outcome.OVERRIDDEN, None
            if not self._rate.admit(entity_id, now=now):
                return Outcome.RATE_LIMITED, None
        if refuses(entity_id, command.action, context=command.context):
            return Outcome.REFUSED_UNSAFE, None
        before = self._adapter.read_entity(entity_id).state
        self._adapter.actuate(entity_id, command.action, context=ChangeContext.engine())
        return (
            Outcome.ACTED,
            StateChange(entity_id=entity_id, before=before, after=command.action),
        )

    # -- The engine's half of the snapshot -----------------------------------

    def state(self) -> dict[str, object]:
        """The runtime state a restore resumes, JSON-safe and complete.

        Complete is the whole claim: `simulation` asserts the restore-and-replay
        property, so anything missing here is a run that decides differently after
        being resumed. Three of the fields are the house's and the modes'
        (`bindings`, `modes`), three are the registries' (`override_records`,
        `rate_limit_windows`, `room_timers`), one is the enable flags, which
        belong to the resolver and are captured as the *resolved* answers rather
        than as the layers, because the layers are the composition root's to
        supply again and the flags are what a resumed run must keep, and one is
        the installed set -- which is state rather than history for the reason
        `pack-install` gives: a resumed run decides from the packs this house
        holds, so a snapshot that dropped them would resume a house whose
        behaviours it could not explain.
        """
        return {
            "bindings": {room.id: dict(room.bindings) for room in self._house.rooms},
            "modes": sorted(self._modes.active),
            "enable_flags": self._enable_flags(),
            "override_records": self._overrides.to_document(),
            "rate_limit_windows": {
                entity_id: [at.isoformat() for at in admissions]
                for entity_id, admissions in self._rate.windows().items()
            },
            "room_timers": list(self._dwell.to_document()),
            "installed_packs": self._installed.to_document(),
        }

    def _enable_flags(self) -> dict[str, object]:
        """Every unit's resolved enable flag, per scope, as the snapshot keeps them."""
        return {
            unit.id: {
                "house": self._settings.resolve_or(
                    enable_key(unit.id), HouseScope(), unit.enabled
                ).flag(),
                "rooms": {
                    room.id: self._settings.resolve_or(
                        enable_key(unit.id), RoomScope(room.id), unit.enabled
                    ).flag()
                    for room in self._house.rooms
                },
            }
            for unit in self._behaviours.values()
        }

    def _adopt(self, state: Mapping[str, object]) -> None:
        """Rebuild the engine's runtime state from `state()`'s form.

        The bindings are read and not applied: they are the house's, and the
        house handed to this constructor is the one the snapshot was taken from.
        They are still checked, because a snapshot restored against a *different*
        house is a mistake that would otherwise surface as a run that quietly
        decides differently rather than as a refusal at the seam.
        """
        self._installed = self._restore_installed(state["installed_packs"])
        bindings = _mapping(state["bindings"], "bindings")
        for room in self._house.rooms:
            recorded = _mapping(bindings.get(room.id, {}), f"bindings.{room.id}")
            if dict(recorded) != dict(room.bindings):
                raise EngineError(
                    f"the snapshot's bindings for room {room.id!r} are not the "
                    "house's; a snapshot restores onto the house it was taken from"
                )
        for name in _strings(state["modes"], "modes"):
            self._modes.activate(name)
        self._overrides = OverrideRegistry.from_document(state["override_records"])
        self._rate = RateLimiter(
            bound=self.count(RATE_LIMIT_BOUND_KEY, HouseScope()),
            window=timedelta(seconds=self.seconds(RATE_LIMIT_WINDOW_KEY, HouseScope())),
            windows=_windows(state["rate_limit_windows"]),
        )
        self._dwell = DwellRegistry.from_document(state["room_timers"])
        for unit_id, flags in _mapping(state["enable_flags"], "enable_flags").items():
            if unit_id not in self._behaviours:
                raise EngineError(
                    f"the snapshot enables {unit_id!r}, which is not a behaviour "
                    "this build registers"
                )
            recorded = _mapping(flags, f"enable_flags.{unit_id}")
            unit = self._behaviours[unit_id]
            self._keep_flag(
                enable_key(unit_id),
                HouseScope(),
                unit.enabled,
                _boolean(recorded["house"], f"enable_flags.{unit_id}.house"),
            )
            rooms = _mapping(recorded["rooms"], f"enable_flags.{unit_id}.rooms")
            for room_id, value in rooms.items():
                self._keep_flag(
                    enable_key(unit_id),
                    RoomScope(room_id),
                    unit.enabled,
                    _boolean(value, f"enable_flags.{unit_id}.rooms.{room_id}"),
                )

    def _keep_flag(self, key: str, scope: Scope, default: bool, recorded: bool) -> None:
        """Restore one recorded enable flag, or leave its layer alone if it agrees.

        The state document carries the *resolved* flags and not the layers
        (`state`), so a flag no layer has moved already resolves to the recorded
        answer when this engine is constructed and there is nothing to do to it.
        Writing it as an override anyway is what this method exists not to do: an
        override outranks every layer, so every record after a restore would cite
        the setting as decided by `override` where the record before it cited
        `builtin`, and `simulation`'s restore-and-replay claim compares exactly
        those records -- a run paused and resumed has to decide, and report, what
        the run that never stopped did. The layers are the composition root's to
        supply again (`state`), which is why agreement is the expected case and a
        disagreement is the one worth writing down.
        """
        if self._settings.resolve_or(key, scope, default).flag() == recorded:
            return
        self._settings.set_override(key, scope, recorded)


# --------------------------------------------------------------------------
# The draft: one evaluation's record, filled in as the tick resolves it.
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Draft:
    """One evaluation's record, before the resolution stage has decided its outcome.

    Mutable because the tick fills it in two passes -- the behaviour's reads
    first, then the fate of each of its proposals -- and because a record is
    immutable only once it is appended. `fates` and `delta` are the resolution
    stage's, in the order the contentions were settled, which is entity id order.

    `priority` is resolved once, at evaluation time, and carried rather than
    looked up per proposal: it is a layered setting, and a resolution that could
    raise for a behaviour that proposed three commands should raise once rather
    than on the third.
    """

    actor: str
    scope: Scope
    now: datetime
    inputs: list[Input]
    rule: str | None
    commands: tuple[ProposedCommand, ...]
    #: The modes this evaluation asked the house to enter. Carried on the draft
    #: rather than applied from the context, so the request lands after the tick
    #: has finished arbitrating: a mode applied mid-evaluation would change what
    #: the behaviours evaluated later in the same tick read.
    modes: tuple[str, ...] = ()
    priority: int = 0
    #: The outcome this draft reports when it proposed nothing: `declined` for an
    #: evaluation that reached a rule and chose not to command, or the skip that
    #: gated it before it reached one. Never one of the resolution stage's
    #: outcomes, because those are computed from `fates`.
    unproposed: Outcome = Outcome.DECLINED
    fates: list[tuple[str, Outcome]] = field(default_factory=list[tuple[str, Outcome]])
    delta: list[StateChange] = field(default_factory=list[StateChange])

    def record(self) -> DecisionRecord:
        """The record this evaluation leaves."""
        return DecisionRecord(
            at=self.now,
            actor=self.actor,
            inputs=tuple(self.inputs),
            rule=self.rule,
            commands=self.commands,
            outcome=self.outcome(),
            state_delta=tuple(self.delta),
        )

    def outcome(self) -> Outcome:
        """This evaluation's outcome, by the precedence the stages run in.

        A draft that proposed nothing reports what it was: the gate that skipped
        it, or a decline. Otherwise the outcome is the first of the resolution
        stage's outcomes that met any of its proposals, in the stage order --
        which is what makes a command that won one entity and lost another record
        `acted` rather than `lost arbitration`, with a `state_delta` naming
        exactly the entities that were written. That is the honest report of a
        partial win: the evaluation did act.
        """
        if not self.commands:
            return self.unproposed
        seen = {outcome for _, outcome in self.fates}
        for outcome in _PRECEDENCE:
            if outcome in seen:
                return outcome
        raise EngineError(
            f"the evaluation by {self.actor!r} proposed {len(self.commands)} "
            "command(s) and the resolution settled none of them"
        )


#: The outcomes the resolution stage produces, in the order the stages run. The
#: first that met any of a draft's proposals is the one it reports, so the order
#: here *is* the precedence -- which is also why `acted` leads: a write that
#: happened outranks every reason a different command to the same evaluation did
#: not. The two skips are absent because they are settled before any proposal
#: exists, and `declined` because a draft that proposed nothing is not ordered at
#: all.
_PRECEDENCE: tuple[Outcome, ...] = (
    Outcome.ACTED,
    Outcome.LOST_ARBITRATION,
    Outcome.OVERRIDDEN,
    Outcome.RATE_LIMITED,
    Outcome.REFUSED_UNSAFE,
)


def _skipped(
    unit: Behaviour,
    scope: Scope,
    now: datetime,
    outcome: Outcome,
    inputs: tuple[Input, ...],
    rule: str | None,
) -> _Draft:
    """The record a gated unit leaves. It read nothing, so it consulted nothing.

    `outcome` is passed by the caller rather than derived: the gates propose
    nothing, so they are indistinguishable from the fields above and the answer
    has to come from whichever gate ran. It is checked here because a draft that
    proposed nothing can report only a skip or a decline, and a caller passing
    the second would be recording "the rule ran and chose not to act" for a unit
    whose rule never ran.
    """
    if outcome not in (
        Outcome.SKIPPED_DISABLED,
        Outcome.SKIPPED_UNBOUND_SLOT,
        Outcome.SKIPPED_SUPPRESSED,
    ):
        raise EngineError(f"{outcome!r} is not an outcome a gate leaves")
    return _Draft(
        actor=unit.id,
        scope=scope,
        now=now,
        inputs=list(inputs),
        rule=rule,
        commands=(),
        unproposed=outcome,
    )


def _unbound(binding: SlotBinding) -> SlotRead:
    """An empty read naming the slot a required binding left unbound.

    The reduction is `ANY` because a reduction says how a *multi-entity* read is
    folded and there is nothing here to fold; what the row carries is the slot's
    name, which is what `engine-core` requires the skip to name.
    """
    return binding.read(Reduction.ANY)


def _slot_override(
    settings: ConfigResolver, pack: str | None, scope: Scope, slot: str
) -> str | None:
    """The entity the pack `pack` points `slot` at, or `None` when it takes the binding.

    A module may aim one of its own slots at an entity of its own rather than at
    whatever the room bound -- the per-user override a person sets for the single
    device a module reaches. It is stored under
    `module.<pack>.slot.<slot>.entity` (`engine.behaviours.declared.slot_key`),
    which is to say in the same namespace as the pack's options, so it resolves
    through the layered resolver like every other setting and a room's answer can
    beat the house's.

    The override belongs to the *pack*, not to the behaviour: two behaviours of
    one pack reaching the same slot see one entity, because a person overriding
    "the lamp this module acts on" is answering a question about the module. A
    declared behaviour's pack is the family `unit.module` names
    (`DeclaredBehaviour.module`), and a built-in unit answers `None`: it belongs
    to no pack, holds nothing in that namespace, and so has no override to read.

    A value that is not a non-empty string is not an entity and is read as no
    override at all, which is the safe reading: a hand-edited file cannot make a
    slot resolve to `None` or to a number, it simply fails to override.
    """
    if pack is None:
        return None
    value = settings.resolve_or(
        option_key(pack, slot_entity_key(slot)), scope, ""
    ).value
    return value if isinstance(value, str) and value else None


def _slot_part(
    settings: ConfigResolver,
    house: House,
    pack: str | None,
    scope: Scope,
    slot: str,
) -> str | None:
    """Which part of a split slot `pack` acts through, or `None` for the slot itself.

    A role a person divided (`ha_adapter.slot_parts`) is still one role with one
    name, and each half binds under a key of its own -- `light_group__a`. A module
    on a half therefore reaches a *different slot key* while everything it was
    authored against still says `light_group`, and this is the reader that turns
    one into the other. The part's key is looked up instead of the parent's, so two
    modules on two halves act on two devices and two modules on one half provably
    act on one.

    Stored beside the entity override (`module.<pack>.slot.<slot>.part`), in the
    same namespace and for the same reason: it is a setting about one module, so a
    room's answer can beat the house's and a rebuild carries it.

    It does **not** replace the entity override. A person who has pointed this
    module's slot at their own lamp has answered the same question more precisely
    than the part did, so the override is the last word (`Binding.__init__`'s
    `own=`), exactly as it is for an unsplit slot.

    **A part the record no longer carries resolves to the slot itself.** The
    vocabulary is grown from the parts record (`ha_adapter.slot_parts.grow`), so
    "the key is not one the house's vocabulary defines" *is* "the record does not
    carry this part" -- a name left behind by a removal, or written into the file
    by hand. Without this check that name folds into a slot key `resolve_slot`
    refuses outright (`UnknownSlotError`), and nothing between this reader and
    `Engine.tick` catches it: one stale row would crash every tick for the whole
    house, forever, where the panel (`live_modules.slot_parts_of`) had already
    learned to drop it. The fallback is the honest answer and the safe one -- the
    module acts on the role until the part exists again -- and it means a
    hand-edited file cannot make a slot resolve to nothing, whether the name it
    carries is junk or merely no longer a part. A part name that is not a non-empty
    string at all is the same reading `_slot_override` makes.
    """
    if pack is None:
        return None
    value = settings.resolve_or(option_key(pack, slot_part_key(slot)), scope, "").value
    if not (isinstance(value, str) and value):
        return None
    # The record decides, and the vocabulary is its projection: a part whose key
    # the house no longer carries is a part that has been removed (`_slot_key` is
    # the same join the record writes under, so the two cannot disagree).
    return value if _slot_key(slot, value) in house.vocabulary.slots else None


def _slot_key(slot: str, part: str | None) -> str:
    """The name `slot` resolves under once a module's part is folded in.

    The slot's own name, or the part's key -- `slot_parts.key_of`'s join, spelled
    here rather than imported because the engine is what the adapter is built on:
    the joiner is `engine.declared_slots.QUALIFIER`, and a part is the same shape
    of key a `separate: true` declaration already mints.
    """
    return slot if part is None else f"{slot}{QUALIFIER}{part}"


# --------------------------------------------------------------------------
# The context: everything a behaviour can reach, and nothing else.
# --------------------------------------------------------------------------


class _Evaluation(BehaviourContext):
    """One behaviour's evaluation in one scope, and the record it is building.

    The adapter is not reachable from here and no method below writes to the
    house: a behaviour can read, resolve, gate and propose, and the engine is the
    only writer. That is `first-behaviours`' "A behaviour proposes commands and
    never actuates" held by construction rather than by inspection.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        actor: str,
        scope: Scope,
        now: datetime,
        pack: str | None = None,
    ) -> None:
        self._engine = engine
        self._actor = actor
        self._scope = scope
        self._now = now
        self._pack = pack
        self._inputs: list[Input] = []
        self._commands: list[ProposedCommand] = []
        self._modes: list[str] = []
        self._rule: str | None = None

    # -- What the engine reads back ----------------------------------------

    @property
    def inputs(self) -> list[Input]:
        """Everything this evaluation consulted, in the order it consulted it."""
        return self._inputs

    @property
    def commands(self) -> tuple[ProposedCommand, ...]:
        """What this evaluation proposed."""
        return tuple(self._commands)

    @property
    def modes(self) -> tuple[str, ...]:
        """The modes this evaluation asked the house to enter."""
        return tuple(self._modes)

    @property
    def rule(self) -> str | None:
        """The concept this evaluation matched, or `None` if it reached no rule.

        One value rather than one per proposal: a behaviour names the row it is
        about once, with `matched`, before deciding what to do about it. Two
        different rows in one evaluation would mean two questions asked at once,
        and a record whose `rule` was the first of them would answer the wrong
        one.
        """
        return self._rule

    # -- BehaviourContext ---------------------------------------------------

    @property
    def scope(self) -> Scope:
        return self._scope

    @property
    def now(self) -> datetime:
        return self._now

    def binding(self, slot: str) -> SlotBinding:
        """Resolve `slot`, honouring the entity and the part this module names.

        Two answers narrow the broadest one, and this is the reader that applies
        both. The **part** says *which half of the role* this module stands in:
        a person who split the role has said the halves are two devices, so the
        module resolves at the part's key (`light_group__a`) rather than at the
        role's, and two modules on one half provably act on one entity. The
        **override** then beats even that, because it is the narrowest answer of
        all -- one device for one module -- so an unsplit role with a pointer and a
        split role with a pointer both end at the device the person chose.

        Reading both here rather than at each call site is what keeps `read`,
        `propose`, `quiet_for` and `held_for` moving together -- a behaviour that
        resolved the slot one way and proposed to it another would be worse than
        one that ignored the part entirely.
        """
        return resolve_slot(
            self._engine.house,
            self._scope,
            self._key(slot),
            own=self._own(slot),
        )

    def _key(self, slot: str) -> str:
        """The key `slot` resolves under for this module: the role's, or its part's."""
        return _slot_key(
            slot,
            _slot_part(
                self._engine.settings,
                self._engine.house,
                self._pack,
                self._scope,
                slot,
            ),
        )

    def _own(self, slot: str) -> str | None:
        """This module's entity for `slot`, or `None` when it takes the binding."""
        return _slot_override(self._engine.settings, self._pack, self._scope, slot)

    def read(self, slot: str, reduction: Reduction) -> SlotRead:
        read = self.binding(slot).read(reduction)
        self._inputs.append(read)
        return read

    def views(self, read: SlotRead) -> tuple[EntityView, ...]:
        return read.views(self._engine.house_adapter)

    def holds(self, read: SlotRead, predicate) -> bool:  # type: ignore[no-untyped-def]
        return read.holds(self._engine.house_adapter, predicate)

    def setting(self, key: str) -> ResolvedSetting:
        resolved = self._engine.resolve(key, self._scope)
        self._inputs.append(resolved)
        return resolved

    def enter_mode(self, name: str) -> None:
        declared = name in self._engine.modes.declared
        self._inputs.append(ModeRequest(mode=name, taken=declared))
        if declared:
            self._modes.append(name)

    def mode_is_active(self, name: str) -> bool:
        active = self._engine.modes.is_active(name)
        self._inputs.append(ModeReading(mode=name, active=active))
        return active

    def quiet_for(self, slot: str, timeout: timedelta) -> bool:
        if not isinstance(self._scope, RoomScope):
            raise BehaviourError(
                "a quiet period is a room's; a house-scoped evaluation has none"
            )
        return self._engine._dwell.quiet(
            self._scope.room_id, self._dwell_key(slot), at=self._now, timeout=timeout
        )

    def held_for(self, slot: str, read: SlotRead) -> timedelta | None:
        """How long the slot has held the reading `read` carries, observed now.

        The reading is the one string a multi-entity slot can be held to: the
        states of every member, sorted and joined, so "every lamp is off" and
        "one lamp is on" are two different readings and a change in either order
        of the same set is not a change at all. `active` is deliberately False --
        the quiet period is the *motion* path's duration (`_observe_presence`),
        and a reading observed here is asking a different question of a slot the
        presence timer never looks at.
        """
        views = read.views(self._engine.house_adapter)
        reading = "|".join(sorted(view.state for view in views))
        key = self._dwell_key(slot)
        self._engine._dwell.observe(
            self._dwell_room(),
            key,
            active=False,
            at=self._now,
            known=all(view.available for view in views),
            reading=reading,
        )
        return self._engine._dwell.held_for(self._dwell_room(), key, at=self._now)

    def option(self, pack: str, key: str, default: object) -> ResolvedSetting:
        resolved = self._engine.settings.resolve_or(
            option_key(pack, key), self._scope, default
        )
        self._inputs.append(resolved)
        return resolved

    def _dwell_room(self) -> str:
        """The room the duration registry keys this evaluation's slots under.

        The empty string for a house-scoped evaluation: a house's slot is not any
        room's, and inventing a room's key for it would let a house-scoped
        behaviour's reading age against a room's and vice versa. The registry is
        keyed by a pair, so `""` is a key no room can hold and both readings stay
        separate facts.
        """
        return self._scope.room_id if isinstance(self._scope, RoomScope) else ""

    def _dwell_key(self, slot: str) -> str:
        """The name the duration registry holds `slot` under for this evaluation.

        The part's key, unless this module has pointed the slot at an entity of
        its own -- in which case the override is folded in too. The registry is
        keyed by a `(room, slot)` pair, so a module acting on its own lamp and the
        room's premade binding for the same slot would otherwise share one record:
        the module's lamp would age the room's reading, or reset it, and a `for`
        clause on either would be measured against a device it never read. A part
        is the same collision one level in -- two modules on two halves of one role
        in one room are two devices -- so the part is folded in for the same
        reason. A slot with neither stays under exactly the name every existing
        record and clause already uses.
        """
        key = self._key(slot)
        own = self._own(slot)
        return key if own is None else f"{key}@{own}"

    def house_is_empty(self) -> bool:
        rooms = self._engine.empty_rooms(self._now)
        observed = tuple(
            room.id
            for room in self._engine.house.rooms
            if not resolve_slot(
                self._engine.house, RoomScope(room.id), MOTION_SLOT
            ).is_empty
        )
        empty = bool(observed) and all(room_id in rooms for room_id in observed)
        self._inputs.append(HousePresence(empty=empty, quiet_rooms=rooms))
        return empty

    def sun_elevation(self) -> float:
        return self._engine.sun_elevation()

    def lapsed(self) -> tuple[tuple[str, ResetCondition], ...]:
        """This tick's releases for the entities this evaluation has read.

        Narrowed to what the evaluation itself named, so a room's record does not
        report another room's release as though it had met it. The narrowing
        reads the reads already recorded in `inputs` rather than the room's
        bindings: the question is "did *this* evaluation read the entity", and a
        binding the evaluation never looked at is not evidence that it did.
        """
        named = {
            entity_id
            for entry in self._inputs
            if isinstance(entry, SlotRead)
            for entity_id in entry.entities
        }
        return tuple(
            (entity_id, condition)
            for entity_id, condition in sorted(self._engine._released.items())
            if entity_id in named
        )

    def is_overridden(self, entity_id: str) -> bool:
        return self._engine.overrides.is_overridden(entity_id)

    def register_override(
        self,
        entity_id: str,
        *,
        origin: ChangeOrigin,
        duration: timedelta,
    ) -> None:
        room_id = self._scope.room_id if isinstance(self._scope, RoomScope) else None
        self._engine.overrides.note(
            entity_id,
            origin=origin,
            at=self._now,
            duration=duration,
            room_id=room_id,
            mode=self._mode_in_force(),
        )

    def consult(self, entry: Input) -> None:
        self._inputs.append(entry)

    def matched(self, rule: str) -> None:
        self._rule = rule

    def propose(
        self, *, slot: str, action: str, rule: str, safety: bool = False
    ) -> None:
        binding = self.binding(slot)
        if binding.is_empty:
            raise BehaviourError(
                f"{self._actor} proposes to the slot {slot!r}, which is unbound in "
                f"{self._scope!r}"
            )
        self._commands.append(
            ProposedCommand(
                slot=slot,
                entities=binding.entities,
                action=action,
                context=ChangeContext.engine(),
                safety=safety,
            )
        )
        self._rule = rule

    def hazards(self) -> tuple[Hazard, ...]:
        """The alarms the house is raising, recorded as this evaluation's inputs.

        The scan is the engine's (`Engine.hazards`); recording it here is what
        makes the decision explainable, so the record names which detectors were
        alarming rather than only what the response wrote.
        """
        found = self._engine.hazards()
        for hazard in found:
            self._inputs.append(
                HazardReading(entity_id=hazard.entity_id, kind=hazard.kind)
            )
        return found

    # -- Internals ----------------------------------------------------------

    def _mode_in_force(self) -> str | None:
        """The mode an override recorded now would be recorded under.

        One name, because `OverrideRecord.mode` is one; when more than one mode
        is active the first in ascending order is taken, so the choice is a
        function of the mode set rather than of the order the modes were set in.
        """
        active = sorted(self._engine.modes.active)
        return active[0] if active else None


# --------------------------------------------------------------------------
# Small helpers over untrusted snapshot input. Each names what it wants.
# --------------------------------------------------------------------------


def _by_id(behaviours: Iterable[Behaviour]) -> Mapping[str, Behaviour]:
    """Key units by `id`, refusing two that share one.

    A shared id would make the enable flag, the arbitration tie-break and the
    record's `actor` ambiguous, which is the ambiguity the id exists to remove.
    """
    keyed: dict[str, Behaviour] = {}
    for unit in sorted(behaviours, key=lambda unit: unit.id):
        if unit.id in keyed:
            raise BehaviourError(f"two behaviours share the id {unit.id!r}")
        keyed[unit.id] = unit
    return keyed


def _reports_motion(view: EntityView) -> bool:
    """Whether one member of a motion read is reporting activity.

    Availability is part of the answer: a sensor that stopped reporting keeps its
    last state, and a stale `on` would hold a room out of the quiet set forever.
    """
    return view.available and view.state == _ACTIVE


def _windows(value: object) -> dict[str, tuple[datetime, ...]]:
    entries = _mapping(value, "rate_limit_windows")
    return {
        entity_id: tuple(
            _instant(item, f"rate_limit_windows.{entity_id}[]")
            for item in _sequence(admissions, f"rate_limit_windows.{entity_id}")
        )
        for entity_id, admissions in entries.items()
    }


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EngineError(f"snapshot field {field!r} is not an object")
    return value  # type: ignore[return-value]


def _sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EngineError(f"snapshot field {field!r} is not a list")
    return value  # type: ignore[return-value]


def _strings(value: object, field: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{field}[]") for item in _sequence(value, field))


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise EngineError(f"snapshot field {field!r} is not a string")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise EngineError(f"snapshot field {field!r} is not a boolean")
    return value


def _instant(value: object, field: str) -> datetime:
    text = _string(value, field)
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise EngineError(f"snapshot field {field!r} is not a timestamp") from error
