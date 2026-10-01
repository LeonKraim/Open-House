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
- **A service is proposed as the command's action.** `ProposedCommand.action` is
  documented as "the state to write, which is what the port's `actuate` takes",
  and `light.turn_on` is not a state. The spec's own words are "calling a declared
  service on it", so the service is what this proposes -- and a port that takes a
  state rather than a service call will write `light.turn_on` as though it were
  one. **This is the gap that blocks the exit criterion**: two packs proposing for
  one light reduce to one command either way, but the command that reaches the
  light is not the one the pack asked for until the port is widened or the engine
  learns to map a service to the state it writes. It is recorded, not papered over
  with a mapping table invented here, because a service-to-state table is a
  vocabulary and vocabularies belong in an artifact.
- **A pack behaviour is room-scoped.** The manifest declares no scope, and a pack
  lands in rooms, so `ROOM` is the only scope a declared behaviour can be. A
  house-scoped pack behaviour is not expressible in `1.2.0` and this module does
  not invent the clause that would express it.
- **A behaviour with no declared priority takes the published default.** That one
  is not a gap: `catalog/pack-policy.yaml` publishes `default_priority` and the
  engine reads it, so two authors comparing packs compare a stated number rather
  than one module's constant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from engine.behaviours.base import (
    BehaviourContext,
    BehaviourScope,
)
from engine.binding import Reduction

__all__ = ["DeclaredBehaviour", "behaviour_id"]


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

    def evaluate(self, ctx: BehaviourContext) -> None:
        """Propose the declared services through the action slot.

        The whole of the interpreter, and it is short because the sandbox made it
        so: there is no condition to test, no loop to run and no expression to
        evaluate, so an evaluation proposes or it declines and nothing else. The
        read is opened before the proposal rather than after, so the record names
        the slot and the entities even when the slot turned out to be unbound --
        "the pack reached for nothing" and "the pack never reached" are different
        facts, and only the record can tell them apart.
        """
        ctx.matched(self.name)
        slot = self.action_slot
        if slot is None:
            return
        if ctx.binding(slot).is_empty:
            return
        ctx.read(slot, Reduction.ANY)
        for service in self.services:
            ctx.propose(slot=slot, action=service, rule=self.name)
