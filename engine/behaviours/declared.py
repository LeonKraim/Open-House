"""The pack interpreter: a declared behaviour, run as a policy unit.

`base.py` is the seam this is written against, and the sandbox fixes the whole of
what it may reach: **resolve a slot to a bound entity and call a declared service
on it**, with no branch, loop, variable or expression evaluation (`design.md` D5,
`pack-sandbox`). So this unit is a `Behaviour` like any other -- it declares its
facts and it proposes commands through the context -- and the difference from the
three hand-written units beside it is that its facts come from a manifest rather
than from a Python class.

Four things are decisions, and three of them are places the spec is silent. All
three are named here rather than settled quietly, because each is a question the
critic should be able to answer differently without rewriting this file's
identity:

- **The action slot is the last declared slot.** A manifest declares `slots` as
  one flat list and `services` as another, and nothing pairs them: the shipped
  example is `slots: [motion_sensor, light_group]` with `services:
  [light.turn_on]`, which reads trigger-first and action-last. That is the
  convention this module implements -- the last slot is what the services act on,
  and the earlier ones are what the behaviour observes -- and it is a *reading of
  an example* and not a clause anywhere. A schema clause naming the action slot
  would replace it.
- **A service is resolved to the state it writes, through an artifact.** This was
  the module's open gap and it is now closed: a manifest declares a *service*
  (`light.turn_on`) and `ProposedCommand.action` is documented as "the state to
  write, which is what the port's `actuate` takes", and the two are not the same
  thing. `catalog/services.yaml` is the vocabulary that knows one from the other,
  read through `engine.vocabulary.load_service_states`, and a unit carries the
  resolved states in its `states` field -- resolved at *build* time, by
  `declared_units` below, so the interpreter holds no table and the artifact
  stays the one place the mapping is written. The earlier revision proposed the
  service string as the action, which meant a port that takes a state wrote
  `light.turn_on` onto the light; that is the failure this bullet used to record.
  **A service with no row is not an actuation.** The table is closed on purpose:
  `climate.set_temperature` names a setpoint and `notify.send_message` names an
  event, neither of which is a state an entity can be in, so a behaviour
  declaring one contributes no state and proposes nothing through it. The
  alternative -- writing the service name as though it were a state -- is worse
  than not firing, because it puts the entity into a state its own integration
  immediately corrects, and the engine reads the correction as a person's hand.
  Widening the port to carry a service *call* rather than a state is what would
  let those services actuate, and it is the change `catalog/services.yaml`'s own
  docstring anticipates.
- **A pack behaviour's scope is its own clause, and `room` is what silence
  means.** `scope: house` makes the behaviour a whole-house one -- evaluated once
  with the house's bindings, which is where the alarm, the doors, the energy
  meter and the vacuum live -- and a behaviour that states nothing is `room`,
  which is what every manifest written against `1.2.0` meant because that version
  could not say otherwise. The word is the corpus's own (`BehaviourScope` is
  `room`/`house` and so is a row's `scope`), so a pack derived from a row carries
  the row's scope rather than a translation.
- **A behaviour with no declared priority takes the published default.** That one
  is not a gap: `catalog/pack-policy.yaml` publishes `default_priority` and the
  engine reads it, so two authors comparing packs compare a stated number rather
  than one module's constant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

from engine.behaviours.base import (
    BehaviourContext,
    BehaviourScope,
)
from engine.binding import Reduction
from engine.declared_slots import optional_keys, required_keys, slot_keys

__all__ = [
    "DeclaredBehaviour",
    "behaviour_id",
    "declared_units",
    "option_key",
    "option_rows",
    "reach_key",
]

#: The layer key space an option resolves in: `module.<pack>.<key>`.
#:
#: Distinct from `behaviour.<id>.<key>` (a unit's own tunables) and from
#: `module.<pack>.enabled` (the pack's switch) by more than spelling: an option
#: belongs to the *pack*, so two behaviours of one pack that read `grace` read
#: the same number -- which is what makes it a setting a person sets once rather
#: than one they set per behaviour and get wrong in the room that mattered.
_OPTION_PREFIX = "module"


def option_key(pack: str, key: str) -> str:
    """The setting key an option resolves under: `module.<pack>.<key>`."""
    return f"{_OPTION_PREFIX}.{pack}.{key}"


def reach_key(slot: str) -> str:
    """The option key that says whether a pack addresses one role.

    The *unprefixed* half, because the two callers spell the pack differently --
    a behaviour reads it through `ctx.option(self.pack, ...)` and the panel's
    form is keyed by the resolver's own `option_key(pack, ...)` -- and one
    function for the two would be a function that has to be called correctly
    twice. `option_key(pack, reach_key(slot))` is the whole key.

    Named a "reach" rather than a "role" because the question it answers is not
    what the role *is* but whether this module reaches for it: the pack still
    declares the slot, the house still binds it, and the module simply stops
    writing to it. That is a setting rather than a re-install because the same
    pack in two rooms is two households' answers -- the bedtime button that shuts
    every door in one house is the button that only dims the lights in another,
    and neither is the manifest's mistake.
    """
    return f"reach.{slot}"


def option_rows(document: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """A manifest's declared options, in the order it wrote them.

    Filtered the way every other reader of a manifest filters -- a row that is
    not an object, or whose `key` is not a string, is not an option -- so the
    panel's form, the validator's checks and the engine's reader agree about how
    many options a pack has. The schema has already refused both shapes by the
    time this runs on an installed pack; the guard is here because a live
    session's document may be one that never met the schema (`ha_adapter`), and
    a form field with no name is worse than a missing one.
    """
    rows = document.get("options")
    if not isinstance(rows, list):
        return ()
    return tuple(
        row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("key"), str)
    )


def behaviour_id(pack: str, name: str) -> str:
    """The unit id of `name` in `pack`, which is the whole of its identity.

    Qualified by the pack because the arbitration tie-break is the ascending
    behaviour `id` and two packs may each declare a behaviour called `motion`.
    The tie-break has to be install-order-independent (`design.md` D8), and a bare
    name would make two packs' behaviours indistinguishable -- so a tie between
    them would fall through to whichever sorted first by accident rather than by
    a rule anyone wrote down.
    """
    return f"{pack}.{name}"


@dataclass(frozen=True, slots=True)
class DeclaredBehaviour:
    """One behaviour a pack declared, as the engine's policy-unit protocol.

    The facts are the manifest's, and the two that are not -- the action slot and
    the enable flag -- are read off the module docstring's conventions. `enabled`
    is `False` and is not a field: a pack that arrived enabled would be a pack
    that acted on its way in, and `pack-install`'s "installation is not
    activation" is exactly the rule that makes this value rather than a parameter.
    """

    pack: str
    name: str
    services: tuple[str, ...] = ()
    #: The readings this behaviour acts on, from `pack-manifest/1.3.0`'s `match`
    #: clause: it proposes only when the slot it acts through currently reads one
    #: of these. Empty means it acts whatever the device reads, which is every
    #: behaviour written against an older version and the right reading of one --
    #: a clause nobody wrote is not a clause that matched nothing.
    match: tuple[str, ...] = ()
    #: The house mode the behaviour enters when it acts, from 1.3.0's `mode`
    #: clause, or `None` for a behaviour that writes devices rather than modes.
    #: It is additive rather than alternative: a behaviour may turn a room's
    #: lights off *and* put the house to sleep, which is what the Bedtime pack's
    #: first act is, and the two reach the house by different roads -- the lights
    #: through a `ProposedCommand` and the mode through the context.
    mode: str | None = None
    #: The values this behaviour reads, from `pack-manifest/1.3.0`'s `for` clause:
    #: the name of a `duration` option the pack declares, which the reading
    #: `match` names must have held for before the behaviour proposes. `None` for
    #: every behaviour that says nothing, which is every 1.2.0 behaviour and every
    #: 1.3.0 one that acts on whatever it reads.
    #:
    #: Spelled `for_option` rather than `for` because `for` is a Python keyword
    #: and a field named after a clause is worth more than a field named after
    #: the language's convenience: the reader of this dataclass is looking for
    #: the manifest's clause and finds its name with one word appended.
    for_option: str | None = None
    #: The options the pack declares, as the manifest's own rows -- one mapping
    #: per entry of the top-level `options` array, unchanged. The unit carries
    #: them because the *manifest document* is not part of an installed record
    #: (`engine/install.py`'s `InstalledPack` keeps a digest, not a text), so the
    #: build is the only place the declarations and the pack can still be seen
    #: together, and the panel's form is drawn from them.
    #:
    #: Every behaviour of a pack carries the whole array rather than its own
    #: slice, because `options` is a clause of the *pack*: a behaviour reads one
    #: by name (`for_option`), and a reader that asked "what can be configured
    #: here" would otherwise have to gather the answers back from the behaviours.
    #: The duplicates are absorbed where they are read (`ha_adapter/live_profiles`
    #: keys by option name), and they must not go into `defaults`: that mapping is
    #: per-unit and `behaviour_defaults` refuses a key two units both declare,
    #: which is exactly what a pack-level clause repeated per behaviour is.
    options: tuple[Mapping[str, object], ...] = ()
    #: The states to write, resolved from `services` through `catalog/services.yaml`
    #: at build time by `declared_units`. Longer than `services` is impossible and
    #: shorter is ordinary: a declared service with no row in the table is not an
    #: actuation the port can perform and contributes no state, so the two lists
    #: are equal only when every service the behaviour named is one the port can
    #: carry out. This is what `evaluate` proposes, and it is why a behaviour whose
    #: services are all parameter-setting declines rather than miswrites.
    states: tuple[str, ...] = ()
    slots: tuple[str, ...] = ()
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    priority: int = 0
    scope: BehaviourScope = BehaviourScope.ROOM
    corpus_rows: tuple[str, ...] = ()
    defaults: Mapping[str, object] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """The unit id, which is the pack-qualified behaviour name."""
        return behaviour_id(self.pack, self.name)

    @property
    def module(self) -> str | None:
        """The family a module-level flag gates: the pack, for a pack's behaviour.

        Phase 1 had no modules and every unit answered `None`. A pack *is* the
        family here -- `module_enable_key` is the gate `engine-core` defined for
        exactly this -- so a pack can be switched off whole without touching each
        behaviour's flag, which is the act a person actually wants when a pack
        misbehaves.
        """
        return self.pack

    @property
    def enabled(self) -> bool:
        """Off. The bottom of the enable stack, and the product rule this phase keeps."""
        return False

    @property
    def action_slot(self) -> str | None:
        """The slot the declared services act through: the last one declared.

        The convention the module docstring states, and `None` for a behaviour
        that declares no slot at all -- which the schema admits, because a
        behaviour whose action is `service` may name its services and reach
        through nothing the pack declares.
        """
        return self.slots[-1] if self.slots else None

    @property
    def watch_slot(self) -> str | None:
        """The slot whose *reading* `match` and `for` are about: the first declared.

        The other half of the module docstring's convention -- "the last slot is
        what the services act on, and the earlier ones are what the behaviour
        observes" -- and the reason it is a separate property rather than the
        action slot: a fridge guard watches a `fridge_contact` and acts on a
        `light_group`, and reading the lights to decide whether the door is open
        is exactly the confusion the two-slot convention exists to prevent.

        The first slot rather than a named one, so a behaviour that declares one
        slot has that slot for both jobs -- which is every behaviour in the
        shipped packs and every one the tests build -- and a behaviour that
        declares two gets the observation first, as its author reads it.
        """
        return self.slots[0] if self.slots else None

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the states the declared services write, through the action slot.

        The whole of the interpreter, and it is short because the sandbox made it
        so: there is no loop to run and no expression to evaluate, so an
        evaluation proposes or it declines and nothing else. Three clauses narrow
        *when* it proposes and none is a condition to evaluate: `mode` asks the
        house to enter a mode, `match` names the readings the watched device must
        currently hold, and `for` says how long it must have held them.

        The read is opened before the match test rather than after, so the record
        names the slot and the entities even when the device reads something the
        clause does not match -- "the pack reached and the vacuum was docked" and
        "the pack never reached" are different facts, and only the record can tell
        them apart. `enter_mode` comes first, and before any slot is resolved,
        because a behaviour whose act is a mode may declare no slot at all: the
        Bedtime button's first act declares the lights it turns off, and a
        behaviour that only put the house to sleep would declare nothing.

        The two slots are read in the order the manifest writes them: the watched
        one must be bound before there is a reading to test, and the acted-on one
        before there is anywhere to write. Either being unbound declines, which is
        what an unbound slot means -- the room has not bound the device the pack
        reaches through, so there is nothing for it to read or to change.

        What is proposed is a *state* rather than the declared service name: the
        translation happened once, at build time, through `catalog/services.yaml`,
        and the interpreter holds the answer rather than the table. A behaviour
        whose services are all parameter-setting -- a thermostat setpoint, a
        notification -- has no state to write, so `states` is empty for it and
        this declines rather than proposing a service name the port would write as
        a state.
        """
        ctx.matched(self.name)
        if self.mode is not None:
            ctx.enter_mode(self.mode)
        watch = self.watch_slot
        if watch is None:
            return
        if ctx.binding(watch).is_empty:
            return
        read = ctx.read(watch, Reduction.ANY)
        # Observed *before* the match test, and that order is the whole of the
        # clause's correctness rather than a style: a reading that has just left
        # the matched set is exactly the reading that must reset the hold, and a
        # tick that returned at the match test would never record it -- so the
        # hold would survive a door that shut and opened again, which is the
        # failure a `for` clause exists to prevent.
        held = None if self.for_option is None else ctx.held_for(watch, read)
        if self.match and not ctx.holds(read, lambda view: view.state in self.match):
            return
        if self.for_option is not None and not self._held_long_enough(ctx, held):
            return
        slot = self.action_slot
        if slot is None or ctx.binding(slot).is_empty:
            return
        # The role's own switch, read after the slot resolves so the record names
        # the device the behaviour would have written to rather than leaving a
        # person to guess which role was turned off. Only an explicit `False`
        # declines: a role nobody has answered for is acted on, which is what
        # keeps every pack written before this setting existed behaving exactly
        # as it did.
        if ctx.option(self.pack, reach_key(slot), True).value is False:
            return
        for state in self.states:
            ctx.propose(slot=slot, action=state, rule=self.name)

    def _held_long_enough(self, ctx: BehaviourContext, held: timedelta | None) -> bool:
        """Whether a reading that has held for `held` has held long enough.

        `held` is the age `ctx.held_for` just measured, `None` for a slot nothing
        has observed a reading of -- which is a decline, not a pass: an unknown
        age is not an age that has elapsed.

        The duration is read through `ctx.option`, so the number is resolved by
        the same layered resolver as every other setting -- a room's override
        beats the pack's declared default, and the record names the layer that
        decided. A value that is not a non-negative whole number of seconds is
        treated as *not holding*, which is the safe reading: `for` is a gate, and
        a gate whose number is nonsense must not open.
        """
        seconds = ctx.option(self.pack, self.for_option or "", self._option_default())
        if held is None or not _is_seconds(seconds.value):
            return False
        return held >= timedelta(seconds=int(cast("int", seconds.value)))

    def _option_default(self) -> object:
        """The declared default of this behaviour's `for` option, or zero.

        Zero rather than `None`, because a `duration` option's default is an
        integer by the schema's own rule (`engine/manifest.py` checks it) and a
        behaviour waiting for zero seconds waits for nothing -- the same answer as
        a behaviour that stated no `for` at all. The lookup is by the option's own
        key, so a pack that renames an option and forgets a behaviour's `for`
        gets the validator's refusal rather than a silently different number.
        """
        for row in self.options:
            if row.get("key") == self.for_option:
                default = row.get("default")
                return default if _is_seconds(default) else 0
        return 0


def declared_units(
    pack: str,
    document: Mapping[str, object],
    *,
    default_priority: int,
    service_states: Mapping[str, str],
) -> tuple[DeclaredBehaviour, ...]:
    """Every behaviour `document` declares, as a unit the engine can evaluate.

    The one builder for a manifest's behaviours, shared by both paths that build
    them -- `openhouse.facade._declared` for the simulator and
    `ha_adapter.declared_units` for a live house -- so a change to what a declared
    behaviour *is* is one edit. The two had drifted into two copies of this
    projection, which is exactly the shape the architecture invariants forbid: the
    simulator and the live house would have disagreed about what a pack does, and
    the disagreement would have surfaced as a person's lights behaving differently
    after a restart.

    Three facts come from the pack and one from the artifact. The required and
    optional slot lists are the *pack's* rather than the behaviour's -- the sandbox
    and the installer check satisfaction once for the pack and the behaviour reads
    through the subsets it names -- and they are read through `required_keys` and
    `optional_keys` rather than off `requires_slots`/`optional_slots` directly,
    because a device the pack brought with it and wrote `required: true` is one the
    room must bind just as much as a catalog slot it named, and the panel asks one
    question about one list. `services` and `slots` are the behaviour's own
    clauses. And `states` is the artifact's projection of
    `services`: each declared service that `catalog/services.yaml` maps is
    resolved to the state it writes, and one it does not map is dropped, because
    what the port takes is a state and a service that writes none is not an
    actuation. The declared priority is the row's or the published default
    (`catalog/pack-policy.yaml`), read from the vocabulary rather than restated,
    so two authors comparing packs compare a stated number.

    **Every slot comes out as the key a house binds under**, not as the name the
    manifest wrote. The two are the same for every catalog slot and every shared
    declaration, and differ for a declaration the pack asked to hold separately
    (`engine/declared_slots.py`): that one binds under a pack-qualified key, so a
    unit carrying the written name would resolve to an empty binding for a device
    the room has bound. The translation is `slot_keys` and it happens here, once,
    for both the simulator and the live path -- which is why this builder is one
    function rather than two.
    """
    keys = slot_keys(pack, document)
    required = required_keys(pack, document)
    optional = optional_keys(pack, document)
    # Read once, outside the loop: `options` is the *pack's* clause, so every
    # behaviour carries the same tuple rather than each carrying a slice.
    options = option_rows(document)
    units: list[DeclaredBehaviour] = []
    for name, row in _behaviour_rows(document):
        services = _names(row.get("services"))
        units.append(
            DeclaredBehaviour(
                pack=pack,
                name=name,
                services=services,
                options=options,
                states=tuple(
                    service_states[service]
                    for service in services
                    if service in service_states
                ),
                # A behaviour's clauses are written in the manifest's own names --
                # `fridge_contact`, the word its author reads -- while the house
                # binds under the key a separate declaration earned. The unit
                # carries keys because the unit is what reads a binding, and a
                # name nothing is bound to resolves to nothing.
                slots=tuple(keys.get(slot, slot) for slot in _names(row.get("slots"))),
                required_slots=required,
                optional_slots=optional,
                match=_names(row.get("match")),
                mode=_clause_name(row.get("mode")),
                for_option=_clause_name(row.get("for")),
                priority=_priority(row.get("priority"), default_priority),
                scope=_scope(row.get("scope")),
            )
        )
    return tuple(units)


def _behaviour_rows(
    document: Mapping[str, object],
) -> tuple[tuple[str, Mapping[str, object]], ...]:
    """The manifest's behaviour clauses, each with the name it registers under.

    Filtered the way `engine/manifest.py` filters its own rows -- a row that is
    not an object, or whose `name` is not a string, is not a behaviour -- so this
    reader and the validator agree about which behaviours exist.
    """
    rows = document.get("behaviours")
    if not isinstance(rows, list):
        return ()
    seen: list[tuple[str, Mapping[str, object]]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        if isinstance(name, str):
            seen.append((name, row))
    return tuple(seen)


def _names(value: object) -> tuple[str, ...]:
    """A clause that is a list of strings, or nothing when it is not one."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _scope(value: object) -> BehaviourScope:
    """The declared scope, or `ROOM` when the behaviour states none.

    `ROOM` is the default because it is what every pack written against 1.2.0
    meant -- a manifest could not say otherwise, and a pack that lands in rooms
    and reaches through the room's placeholders is a room behaviour. `HOUSE` is
    the newer clause, and a value that is neither is `ROOM` rather than an error:
    the schema refuses an unknown word at authoring time, so anything else
    reaching here is a document that never went through the schema, and reading
    it as the default is the same answer `_priority` gives.
    """
    return (
        BehaviourScope.HOUSE if value == BehaviourScope.HOUSE else BehaviourScope.ROOM
    )


def _is_seconds(value: object) -> bool:
    """Whether a value is a usable number of seconds: a whole number, not negative.

    `bool` is excluded before `int` because Python's `bool` is an `int`, and
    `duration: true` meaning one second is a number no author wrote. Floats are
    refused rather than truncated: a duration option is `integer` by the schema's
    rule, so a float here is a document that never met the schema, and rounding it
    would be this module inventing a number the manifest does not contain.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _clause_name(value: object) -> str | None:
    """A clause that is one name -- a mode, or an option -- or `None` if absent.

    Shared by `mode` and `for`, which are the same shape and want the same
    answer: the schema types each as a name string and refuses anything else at
    authoring time, so a value that is not one here is a document that never went
    through the schema, and reading it as "the behaviour states none" is the same
    answer `_scope` and `_priority` give a value of the wrong shape.
    """
    return value if isinstance(value, str) else None


def _priority(value: object, fallback: int) -> int:
    """The declared priority, or the published default.

    `isinstance(value, bool)` is excluded before `int` because Python's `bool`
    is an `int`: a manifest that wrote `priority: true` would otherwise rank at
    `1`, which is a number the file does not contain anywhere.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value
