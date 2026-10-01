"""The `HouseAdapter` port: the engine's single outward edge.

The engine decides, and the house answers. This module is the whole of the
seam between the two: the operations the engine may call on a house, the change
context every change carries, and the errors a caller can act on. It is the
*only* thing under `engine/` that reaches outward, and it reaches a
`Protocol` rather than an implementation, so the engine can be built, tested
and shipped with no Home Assistant present.

The port is defined here rather than in `ha_adapter/` (`design.md` D1). The
alternative -- putting the interface in the package whose name describes the
Home Assistant implementation -- was rejected because that package's reason to
exist is to import `homeassistant`: the engine would then carry an import that
drags Home Assistant in the moment Phase 4 lands, and the simulator's fake would
import `ha_adapter` to satisfy a port the engine owns. Putting the port in the
engine gives the engine exactly one outward edge, to a contract it defines, and
lets `sim/` and Phase 4's adapter both implement it without either importing the
other.

Three properties are load-bearing and stated because a reader would otherwise
have to infer them:

- **The operation set is closed and minimal.** Nine operations, no more. There
  is deliberately no operation that names a slot, a behaviour, a mode, a
  binding, an enable flag or a pack, so a behaviour cannot read a decision's
  inputs or write a decision's outputs except through the engine. The set
  serves two faces -- the *engine-facing* operations the engine calls during a
  tick and the *control-facing* operations the control surface calls to build
  and provoke a house -- and the split is explicit below because the two callers
  have different rights.
- **Mechanism, not policy.** The port moves states and records who moved them.
  It carries no notion of arbitration, override, rate limits or safety, and in
  particular it never refuses an actuation on safety grounds: the engine's gate
  refuses a non-user unlock *before* the adapter is asked, so the refusal is
  visible in the decision log rather than hidden behind the port (`design.md`
  D9).
- **Every change carries a change context.** Manual-override detection turns on
  one fact -- whether the last writer of an entity was a user -- so every
  mutating operation takes a `ChangeContext` with no default. The alternative,
  documenting the requirement and defaulting the argument, was rejected: an
  engine-origin write that defaulted to `user` would silently suppress the very
  behaviour that made it, three ticks after the mistake. A missing context is a
  `TypeError` at the call site instead.

The port names no `homeassistant` type and imports no third-party module, so
this file imports in a checkout where Home Assistant is not installed -- which
is the phase's defining constraint and the reason the port is worth isolating.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

#: The shape every entity id must take: `domain.object_id`, lowercase, as the
#: frozen house schema (`schemas/house/1.0.0.json`) gives a binding and the
#: corpus gives an `entity_ref`. It is restated here because this is the point
#: where the engine's runtime world meets the frozen schemas: a loose id would
#: let a fixture name an entity the house schema could never bind, which is the
#: exact drift `configuration-schemas`' conformance check exists to prevent.
ENTITY_ID_PATTERN = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


class AdapterError(Exception):
    """Base for every failure the port defines.

    A named error rather than a `KeyError` or a bare `ValueError`, because the
    engine and the scenario runner both have to tell "this entity does not
    exist" from "this id is malformed" without matching on a message.
    """


class InvalidEntityIdError(AdapterError):
    """An id that does not match `ENTITY_ID_PATTERN`.

    Carries the offending id on the instance, so a caller can report it without
    parsing the message.
    """

    def __init__(self, entity_id: str) -> None:
        super().__init__(
            f"entity id {entity_id!r} is not of the form `domain.object_id`"
        )
        self.entity_id = entity_id


class UnknownEntityError(AdapterError):
    """A read or write aimed at an entity the adapter does not hold.

    This is raised rather than returning a default state. A default is a silent
    "off" the engine cannot tell from a real one, which is the failure the
    corpus records as a cloud integration that stops reporting; an entity that
    is genuinely gone must say so.
    """

    def __init__(self, entity_id: str) -> None:
        super().__init__(f"the house holds no entity {entity_id!r}")
        self.entity_id = entity_id


def validate_entity_id(entity_id: str) -> str:
    """Return `entity_id` if it matches the port's shape, else raise.

    A shared helper rather than a rule each implementation repeats: the fake and
    Phase 4's adapter must reject the same ids with the same error, and a rule
    written twice is a rule that drifts. Returns the id so the common
    ``entity_id = validate_entity_id(entity_id)`` idiom type-narrows.
    """
    if not ENTITY_ID_PATTERN.match(entity_id):
        raise InvalidEntityIdError(entity_id)
    return entity_id


def domain_of(entity_id: str) -> str:
    """The domain part of a valid id (`light.kitchen` -> `light`).

    The id is validated first so this cannot be used to derive a domain from a
    malformed id and quietly proceed.
    """
    validate_entity_id(entity_id)
    return entity_id.split(".", 1)[0]


class ChangeOrigin(StrEnum):
    """Who caused a change.

    The closed set is four, and the four are the four ways an entity can move:
    a direct `user` action, the `engine`'s own actuation through this port, an
    external `world` change such as a sensor tripping, and an injected `fault`.
    The set is closed so the engine's override detection has a total answer --
    "was the last writer a user?" -- and not a third case it must guess at.
    """

    USER = "user"
    ENGINE = "engine"
    WORLD = "world"
    FAULT = "fault"


@dataclass(frozen=True, slots=True)
class ChangeContext:
    """The origin a mutating operation must carry, and nothing else for now.

    A wrapper around `ChangeOrigin` rather than the enum itself, because the
    requirement is that the *argument* cannot be omitted, and because a future
    field -- a Home Assistant `user_id`, a reason string -- extends this type
    without changing any operation's signature. Phase 1 needs only the origin,
    so only the origin is here; a speculative second field would be a field
    nothing reads, and the port is kept minimal on purpose.

    Construct it through the named classmethods (`ChangeContext.user()` and
    friends) so a call site reads as its intent rather than as an enum member.
    """

    origin: ChangeOrigin

    @classmethod
    def user(cls) -> ChangeContext:
        """A change a person made. The one origin override detection reacts to."""
        return cls(ChangeOrigin.USER)

    @classmethod
    def engine(cls) -> ChangeContext:
        """The engine's own actuation through the port."""
        return cls(ChangeOrigin.ENGINE)

    @classmethod
    def world(cls) -> ChangeContext:
        """An external change: a sensor tripping, the house reloading."""
        return cls(ChangeOrigin.WORLD)

    @classmethod
    def fault(cls) -> ChangeContext:
        """An injected fault, which the engine must tell from its own write."""
        return cls(ChangeOrigin.FAULT)


@dataclass(frozen=True, slots=True)
class Fault:
    """What an injected fault does to an entity.

    A fault is a change of *value* -- this entity now reads this, or this entity
    is now unavailable -- while its *origin* is supplied separately as the
    `ChangeContext` of the `inject_fault` call. Splitting the two lets one
    operation describe a device that stops reporting (`Fault(available=False)`)
    and a device that reports something wrong (`Fault(state="off")`) without a
    second operation or a second origin.

    At least one of `state` and `available` must be set; a fault that changes
    nothing is a caller error, caught at construction rather than becoming a
    no-op the decision log would show as a fault that never happened.
    """

    #: The state to force the entity to, or `None` to leave its state alone.
    state: str | None = None
    #: The availability to force, or `None` to leave availability alone. Note
    #: that `False` is a value and not the same as `None`: a fault injected to
    #: take a device offline sets `available=False`, it does not omit the field.
    available: bool | None = None

    def __post_init__(self) -> None:
        if self.state is None and self.available is None:
            raise ValueError("a fault must change the entity's state or availability")


@dataclass(frozen=True, slots=True)
class EntityView:
    """What a read returns for one entity.

    Availability is a field separate from `state` on purpose (`house-adapter`:
    unavailable is not off). Collapsing the two would let a behaviour read a
    dead sensor as "no motion" and a dark room as empty, or read a lamp an
    integration cannot confirm as already "off" -- the two mistakes the corpus
    calls out most often. `last_origin` is the origin of the change that last
    touched the entity, which is the single signal the engine's override
    detection reads.
    """

    entity_id: str
    state: str
    attributes: Mapping[str, object]
    available: bool
    last_origin: ChangeOrigin

    @property
    def domain(self) -> str:
        """The entity's domain, derived from its id rather than stored twice."""
        return domain_of(self.entity_id)


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    """One entity's captured state, with no engine concept attached.

    Two conditions are captured, not one, because the port distinguishes them:
    `state` and `attributes` are the entity *now*, and `startup_state` and
    `startup_attributes` are the condition `restart` returns it to. A snapshot
    carrying only the live condition would restore a house whose restart
    produced a different house from the one that was captured -- an entity added
    off, actuated on and captured would restart on instead of off -- so the
    startup condition is part of the snapshottable state rather than something a
    restore is allowed to re-derive from whatever the entity happens to read.
    """

    entity_id: str
    state: str
    attributes: Mapping[str, object]
    available: bool
    last_origin: ChangeOrigin
    startup_state: str
    startup_attributes: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AdapterSnapshot:
    """The adapter's slice of a run's state: the entities, and only them.

    It carries exactly what this port owns -- the entities present, their
    states, their attributes, their availability, the condition each starts up
    in and who last touched each -- and nothing the engine or the simulator
    owns. Bindings, modes, enable flags,
    override records and rate-limit windows belong to `engine-core`; the clock
    position and the random-stream position belong to `simulation`; the
    composition root joins the halves into the one document the `snapshot`
    operation returns. Drawing the boundary here is what lets Phase 4's adapter
    take an honest snapshot without knowing about a virtual clock, and what
    keeps the fake's snapshot from being a test-only object.

    `last_origin` is included even though the requirement's headline list does
    not name it, because it *is* the adapter's own record of the last writer and
    a restore that dropped it would silently un-override a manually-set lamp,
    making restore-then-replay diverge. It is not engine state: the engine reads
    it from here rather than storing its own copy.

    `entities` is a tuple rather than a mapping so the document is immutable and
    its order is the adapter's to fix (sorted by id is the natural choice), which
    is what makes two snapshots of the same state equal documents.
    """

    entities: tuple[EntitySnapshot, ...]


class HouseAdapter(Protocol):
    """The one contract between the engine and any house it decides against.

    The members split into two faces, and the split is normative rather than
    descriptive:

    - **Engine-facing** -- `read_entity`, `list_entities`, `actuate`,
      `snapshot`: what the engine calls during a tick. The engine may not reach
      the control face.
    - **Control-facing** -- `add_entity`, `remove_entity`, `set_availability`,
      `inject_fault`, `restart` (plus `snapshot`): what the control surface and
      the scenario runner call to build and provoke a house. These have no path
      to them from a behaviour, which is what stops a behaviour from reaching
      around the engine.

    Each mutating operation below takes `context` as a required keyword-only
    argument with no default.
    """

    # -- Engine-facing ------------------------------------------------------

    def read_entity(self, entity_id: str) -> EntityView:
        """Return an entity's domain, state, attributes, availability and origin.

        Raises `InvalidEntityIdError` if the id is malformed and
        `UnknownEntityError` if it is well-formed but absent. It never returns a
        default state: a default is a silent "off" the engine cannot tell from a
        real one.
        """
        ...

    def list_entities(self) -> Sequence[str]:
        """The ids present in the house. Removed entities are absent from it."""
        ...

    def actuate(self, entity_id: str, state: str, *, context: ChangeContext) -> None:
        """Write `state` to an entity.

        The engine's own writes carry `ChangeContext.engine()`; the control
        surface's `set_state` carries `world` and its `user_action` carries
        `user`. This is the operation override detection is measured against, so
        it is the one the "no context, no call" rule most needs to hold for.
        Writing does not change availability: the two are separate fields with
        separate operations.
        """
        ...

    def snapshot(self) -> AdapterSnapshot:
        """Capture the adapter's own state -- the entities, and nothing else.

        "The entities" includes the condition each one starts up in: a snapshot
        that carried only the live state could not reproduce a restart, and
        restart's reproducibility from a fixed configuration is a requirement
        of this port rather than a nicety of the fake.
        """
        ...

    # -- Control-facing -----------------------------------------------------

    def add_entity(
        self,
        entity_id: str,
        state: str,
        *,
        attributes: Mapping[str, object] | None = None,
        context: ChangeContext,
    ) -> None:
        """Add an entity with an initial state and attributes.

        The state and attributes given are also the entity's *startup*
        condition: what `restart` returns it to. That is what makes a fixture a
        fixed configuration rather than a starting point the adapter forgets, and
        it is why a later actuation changes the entity without changing what a
        restart produces.

        Fixtures are built through this operation rather than loaded as
        snapshots (`design.md` D11), so a fixture cannot hold a state the port
        could not produce.
        """
        ...

    def remove_entity(self, entity_id: str, *, context: ChangeContext) -> None:
        """Remove an entity. It then reads as absent -- raising on a read -- and
        is not listed, rather than reading as `off`."""
        ...

    def set_availability(
        self, entity_id: str, *, available: bool, context: ChangeContext
    ) -> None:
        """Mark an entity available or unavailable, leaving its state readable.

        Its own operation, independent of `actuate`: returning a device to
        available is not an actuation, and an unavailable entity keeps its last
        known state rather than being reported as `off`.
        """
        ...

    def inject_fault(
        self, entity_id: str, fault: Fault, *, context: ChangeContext
    ) -> None:
        """Apply `fault` to an entity, recording the change's origin.

        The caller passes `ChangeContext.fault()`, so the change is
        distinguishable on a later read -- and in the decision log -- from one
        the engine or a user made. Faults are a change *origin* rather than a
        change *value* because the engine's recovery story turns on noticing
        that a change was neither its own nor a user's.
        """
        ...

    def restart(self, *, context: ChangeContext) -> None:
        """Return the house to a defined startup condition.

        What each entity's startup condition is -- and that an entity whose
        integration has not reconnected is `unavailable` at startup rather than
        `off` -- is the adapter's to define. Restart is not restore: it produces
        the startup condition from a fixed configuration, reproducible across
        restarts, and never a `user`-origin change.
        """
        ...


#: The port's operations, by face. Closed on purpose: an operation outside this
#: set is one that lets a behaviour reach around the engine.
ENGINE_FACING_OPERATIONS: frozenset[str] = frozenset(
    {"read_entity", "list_entities", "actuate", "snapshot"}
)

#: The control-facing operations, including `snapshot`, which both callers need.
CONTROL_FACING_OPERATIONS: frozenset[str] = frozenset(
    {
        "add_entity",
        "remove_entity",
        "set_availability",
        "inject_fault",
        "restart",
        "snapshot",
    }
)

#: Every operation the port declares. `snapshot` is the one operation in both
#: faces, so the union is the nine, not the ten the two sets sum to.
PORT_OPERATIONS: frozenset[str] = ENGINE_FACING_OPERATIONS | CONTROL_FACING_OPERATIONS
