"""What a behaviour is, and the whole of what it may reach.

`first-behaviours` divides the phase's decision path in two: `engine-core` owns
*how a decision becomes a command and why it may not*, and `engine/behaviours/`
owns *what a behaviour decides and when* (`design.md` D8). This module is the
seam, and it is deliberately narrow enough to be the boundary a Phase 2 pack
interpreter is written against: a behaviour is a value with seven declared facts
and one method, and the method is handed a context that can read and propose and
do nothing else.

Three things about that context are load-bearing rather than tidy:

- **There is no adapter in it.** A behaviour cannot actuate, add, remove, mark
  unavailable or inject a fault, because none of those operations is reachable
  from `BehaviourContext`. `first-behaviours` states the rule ("A behaviour SHALL
  NOT call the adapter to change state") and a check scans the units for a
  violation; the protocol is what makes the violation impossible to write by
  accident rather than merely detectable.
- **Reads are declared, and every one is recorded.** `read` takes a `Reduction`
  with no default, and both the read and every `setting` resolution land in the
  record's `inputs` without the behaviour having to remember to write them down.
  A behaviour that consulted a value the log cannot name would be a behaviour
  whose decisions cannot be explained, so the context records on the way past.
- **`consult` exists for the one input that is neither.** The dark test's branch
  is recorded as a named input (`dark_source: lux` or `dark_source: sun`) and the
  sun's elevation is not a slot read or a resolved setting, so `consult` is how a
  behaviour puts any other named fact into the record. It is the only way a
  behaviour writes to the record at all.

`enabled` is declared here and **defaults to `false`**. It is the unit's own
default rather than the whole mechanism: `engine-core` resolves
`behaviour.<id>.enabled` through the layered resolver, and this value is the
bottom of that stack -- the answer when no layer sets the key. A behaviour
carrying `enabled = True` would be one that ships on, which is the product rule
this phase exists to enforce.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from engine.adapter import ChangeOrigin, EntityView
from engine.binding import Reduction, Scope, SlotBinding, SlotRead
from engine.config import ResolvedSetting
from engine.decision_log import Input
from engine.overrides import ResetCondition
from engine.safety import Hazard


class BehaviourError(Exception):
    """Base for the failures this package defines."""


#: The config keys a unit's facts resolve under. The enable flag and the
#: arbitrated priority are settings like any other (`engine-core`: "the enable
#: flag SHALL be a per-behaviour config key ... resolved through the layered
#: resolver"), so they are named here rather than spelled at each call site -- a
#: key assembled by concatenation in two places is a key two places can disagree
#: about.
def enable_key(behaviour_id: str) -> str:
    """The key a behaviour's enable flag resolves under."""
    return f"behaviour.{behaviour_id}.enabled"


def priority_key(behaviour_id: str) -> str:
    """The key a behaviour's arbitration priority resolves under."""
    return f"behaviour.{behaviour_id}.priority"


def module_enable_key(module: str) -> str:
    """The key a whole behaviour family's enable flag resolves under.

    `engine-core` requires the module-level flag to gate a family "by the same
    mechanism" as a unit's own flag. No unit in this phase belongs to a module --
    modules are Phase 2 -- so the key is defined here and resolved by the engine
    whenever a unit declares one, rather than being deferred with the modules and
    leaving the gate to be retrofitted.
    """
    return f"module.{module}.enabled"


class BehaviourScope(StrEnum):
    """The scope a behaviour is evaluated in.

    The two members are the corpus's own two scope words, so a unit's declared
    scope and its primary row's `scope` are the same value and the conformance
    check is an equality rather than a translation. A room-scoped behaviour is
    evaluated once per room; a house-scoped one, once.
    """

    ROOM = "room"
    HOUSE = "house"


@runtime_checkable
class BehaviourContext(Protocol):
    """One evaluation's view: what it may read, and the one thing it may say.

    Implemented by the engine, never by a behaviour or a test double that
    actuates. The protocol is `runtime_checkable` so a test can assert a stub
    satisfies it, which is the cheapest way to notice that the seam has grown.
    """

    @property
    def scope(self) -> Scope:
        """The room this evaluation is for, or the house. Never guessed."""
        ...

    @property
    def now(self) -> datetime:
        """The virtual instant of this evaluation. Never the wall clock."""
        ...

    def binding(self, slot: str) -> SlotBinding:
        """The slot's members in this scope, and whether the slot is required."""
        ...

    def read(self, slot: str, reduction: Reduction) -> SlotRead:
        """Open a read of `slot` and record it as an input of this evaluation."""
        ...

    def views(self, read: SlotRead) -> tuple[EntityView, ...]:
        """The entities a read names, read-only, in binding order.

        The read-only half of the adapter, and the only half a behaviour may
        reach: a view carries state, attributes, availability and last origin,
        and carries no way to change any of them.
        """
        ...

    def holds(self, read: SlotRead, predicate: Callable[[EntityView], bool]) -> bool:
        """Whether `read`'s members satisfy `predicate`, under its reduction."""
        ...

    def setting(self, key: str) -> ResolvedSetting:
        """Resolve `key` for this scope and record it as an input."""
        ...

    def mode_is_active(self, name: str) -> bool:
        """Whether the house is in `name`, recorded as an input. Unknown fails."""
        ...

    def quiet_for(self, slot: str, timeout: timedelta) -> bool:
        """Whether `slot` has been clear of activity for at least `timeout`."""
        ...

    def house_is_empty(self) -> bool:
        """Whether every observed room is quiet, recorded as an input.

        House-scoped only: emptiness is a claim about every room at once, so a
        room-scoped evaluation has no business asking. Derived from the engine's
        own observations rather than from a device, because no slot in the
        vocabulary reports a house's emptiness.
        """
        ...

    def sun_elevation(self) -> float:
        """The sun's elevation at `now` for the fixture's location, in degrees."""
        ...

    def hazards(self) -> tuple[Hazard, ...]:
        """Every hazard detector that is alarming right now, recorded as inputs.

        House-scoped only, and derived from the devices rather than from a slot,
        because a smoke, CO or leak detector is recognised by its `device_class`
        (`engine/safety.py`) and the vocabulary has no smoke slot to bind. The
        engine scans the house and records what it found as `HazardReading`
        inputs, so the alarm a safety evaluation answered is named in the record
        rather than inferred from what it wrote.
        """
        ...

    def lapsed(self) -> tuple[tuple[str, ResetCondition], ...]:
        """This tick's override releases for entities in this evaluation's scope.

        Read by the override unit so a release is reported in the record of the
        evaluation that met it, which is where `engine-core` requires the
        condition that ended an override to appear.
        """
        ...

    def is_overridden(self, entity_id: str) -> bool:
        """Whether `entity_id` is overridden right now.

        Read by a unit that records overrides so it does not re-record one that
        is already in force. That read is not an optimisation: the port says who
        last wrote an entity and not when, so a unit that re-noted every tick
        would replace the record each time and push its expiry forward, and the
        `override_timeout` condition would become unreachable. Declining to
        restate a record already in force is what keeps the timeout a timeout.
        """
        ...

    def register_override(
        self,
        entity_id: str,
        *,
        origin: ChangeOrigin,
        duration: timedelta,
    ) -> None:
        """Tell the engine who last wrote `entity_id`, so it can stand down.

        Not an actuation: nothing is written to the house, and a behaviour that
        calls this proposes no command. The origin is passed rather than decided
        here -- the registry is what knows that only a user's origin creates an
        override, and what knows whether this is a new touch or the same one it
        has already accounted for. The engine fills in the room from this
        evaluation's scope and the mode in force from the mode set, because both
        are facts the context holds and the unit does not.

        A unit calls this for every entity it reads and not only for the ones it
        suspects, because a non-user origin is not a no-op: it is what tells the
        registry that a user's touch is no longer the last thing that happened.
        """
        ...

    def consult(self, entry: Input) -> None:
        """Record a named fact in this evaluation's inputs."""
        ...

    def matched(self, rule: str) -> None:
        """Name the corpus concept this evaluation is about.

        The record's `rule` when the evaluation proposes nothing. A decline that
        named no rule would be a record saying a behaviour looked and found
        nothing without saying what it was looking for.
        """
        ...

    def propose(
        self, *, slot: str, action: str, rule: str, safety: bool = False
    ) -> None:
        """Offer a command to the engine, which arbitrates and may not apply it.

        `safety` marks the command as a hazard response. It is the sole way a
        unit reaches the engine's one unsuppressible path: a safety command
        outranks every behaviour in arbitration and is admitted whatever an
        override or a rate limit would otherwise say (`engine/engine.py`). It
        defaults to `False` so an ordinary proposal is unchanged, and it is a
        per-command flag rather than a fact on the unit, so a normally suppressed
        behaviour can raise one urgent command without becoming unsuppressible
        for every command it proposes.
        """
        ...


@runtime_checkable
class Behaviour(Protocol):
    """A policy unit: seven declared facts and one method.

    The facts are checked against the corpus rather than trusted (`a behaviour is
    a policy unit bound to a corpus row`): `scope` and `required_slots` against
    the primary row, `optional_slots` against every cited row. The `id` is the
    unit's stable identity and keys the enable flag, the arbitration tie-break
    and the record's `actor`; `corpus_rows` is what the conformance check reads;
    `priority` is the number arbitration ranks by; `module` is the family a
    module-level enable flag gates, and is `None` for every unit in this phase,
    because modules are Phase 2.
    """

    @property
    def id(self) -> str:
        """The stable unit identity. Not the corpus concept id -- see `corpus_rows`."""
        ...

    @property
    def corpus_rows(self) -> tuple[str, ...]:
        """The `catalog/behaviors.yaml` rows this unit is derived from, primary first."""
        ...

    @property
    def scope(self) -> BehaviourScope:
        """Whether the unit is evaluated per room or once for the house."""
        ...

    @property
    def required_slots(self) -> tuple[str, ...]:
        """The slots the unit cannot run without. An unbound one skips it."""
        ...

    @property
    def optional_slots(self) -> tuple[str, ...]:
        """The slots the unit may read. An unbound one degrades it, never skips it."""
        ...

    @property
    def priority(self) -> int:
        """The number arbitration ranks this unit's proposals by."""
        ...

    @property
    def module(self) -> str | None:
        """The behaviour family a module-level enable flag gates, if any."""
        ...

    @property
    def enabled(self) -> bool:
        """The unit's own default. The bottom of the enable-flag stack; off."""
        ...

    @property
    def defaults(self) -> Mapping[str, object]:
        """The unit's own tunable defaults, keyed as the resolver's built-in layer.

        The behaviour's half of "every tunable resolves through the layered
        resolver": the unit declares what its numbers are, the engine puts them
        at the bottom of the stack, and a house or a room overrides them without
        an edit here. A tunable read from a module constant instead would be a
        value no layer can reach, which is the drift the no-hardcoded-tunables
        check exists to catch.
        """
        ...

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Decide for `ctx`'s scope, proposing commands through `ctx` and nothing else."""
        ...
