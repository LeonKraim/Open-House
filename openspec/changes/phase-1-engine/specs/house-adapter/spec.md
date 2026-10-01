# Spec Delta — house-adapter

## Purpose

The `HouseAdapter` port: the one contract between the engine and any house it
decides against, defined and owned by the engine (`design.md` D1) and satisfied
by the `FakeHouseAdapter` in this phase and the Home Assistant adapter in Phase
4. It fixes the operations the engine may call, the **change context** every
change carries, the separation of availability from state, and the contract
suite that holds any implementation to the port — so the engine can be built,
tested and shipped with no Home Assistant present, and a second implementation
can be pointed at the same suite without the engine changing.

## ADDED Requirements

### Requirement: The port lives in the engine and is the engine's only outward edge
`engine/adapter.py` SHALL define a `typing.Protocol` named `HouseAdapter`, and
the engine SHALL import the port and no implementation of it. `sim/` implements
the port as `FakeHouseAdapter`; `ha_adapter/` stays empty this phase and
implements the port in Phase 4. The engine SHALL NOT import `sim/`,
`ha_adapter/`, or the composition root, directly or transitively.

The port is defined here rather than in `ha_adapter/` because that package's
name describes the Home Assistant *implementation* and its reason to exist is to
import `homeassistant` (`design.md` D1). Putting the engine's own contract there
would give the engine an import that drags Home Assistant in the moment Phase 4
lands, and would force the fake in `sim/` to import `ha_adapter` to satisfy a
port the engine owns. The name is close enough to mislead on first read, so the
rule is stated: `ha_adapter/` is the port's *second implementation's* home, not
the port's.

#### Scenario: The engine imports an implementation
- **WHEN** any file under `engine/` imports `sim`, `ha_adapter`, or the
  composition root, directly or under a guarded import
- **THEN** the purity scan fails and names the file and the import

#### Scenario: The port imports Home Assistant
- **WHEN** `engine/adapter.py` is imported in a checkout where `homeassistant`
  is not installed
- **THEN** the import succeeds, because the port carries no Home Assistant type

#### Scenario: A second implementation is added
- **WHEN** Phase 4 supplies an adapter under `ha_adapter/` that satisfies
  `HouseAdapter`
- **THEN** it satisfies the same port with no change to `engine/`, and the
  contract suite passes against it unmodified

### Requirement: The port's operation set is fixed, closed and minimal
`HouseAdapter` SHALL declare exactly these operations and no others: read one
entity, enumerate entities, actuate (write) an entity, add an entity, remove an
entity, set availability, inject a fault, restart the house, and snapshot the
adapter's state. The set is one interface serving two faces — the
**engine-facing** operations the engine calls during a tick (read, enumerate,
actuate, snapshot) and the **control-facing** operations the control surface and
scenario runner call to build and provoke the house (add, remove, set
availability, inject fault, restart, snapshot). The engine SHALL call only the
engine-facing subset; the control face SHALL be reachable only through the
composition root (`control-surface`), never from a behaviour.

The set is closed so that a behaviour cannot reach around the engine: there is
no port operation that names a slot, a behaviour, a mode, a binding, a pack, or
an enable flag, so a behaviour cannot read a decision's inputs or write a
decision's outputs except through the engine. The split into two faces is stated
because the two callers have different rights — a behaviour may actuate a lamp
and may not add an entity or inject a fault — and a single Protocol that did not
record which caller uses which operation would leave that boundary to review.

#### Scenario: The port carries an operation outside the set
- **WHEN** `HouseAdapter` declares a member that is not one of the nine
  enumerated operations
- **THEN** the port-shape check fails and names the member

#### Scenario: The engine calls a control-facing operation
- **WHEN** any file under `engine/` other than the tick's read/actuate path
  calls `add_entity`, `remove_entity`, `set_availability`, `inject_fault` or
  `restart`
- **THEN** the boundary check fails and names the file and the operation

#### Scenario: A port operation names a policy concept
- **WHEN** a `HouseAdapter` member takes or returns a slot name, a behaviour id,
  a mode, an enable flag or a binding
- **THEN** the port-shape check fails and names the member, because those are
  the engine's concepts and not the house's

### Requirement: Every change carries a change context, and a change without one cannot exist
Every state change the port records SHALL carry a `ChangeContext` naming its
origin, and `ChangeContext` SHALL be a required argument of every mutating
operation with no default, so a write that omits it fails at call construction
rather than being defaulted. The origin SHALL be a `ChangeOrigin` drawn from the
closed set `user`, `engine`, `world`, `fault` — a direct user action, the engine's
own actuation through the port, an external world change such as a sensor
tripping, and an injected fault — so the engine can tell its own write apart from
one the world made.

This is the single field manual-override detection depends on. The engine treats
"the last writer was a user" as the override signal, so if a write could omit
its context, or default to `user`, an engine-origin write could silently present
itself as a manual action and suppress the behaviour that made it. Requiring the
argument rather than documenting it is the difference between a rule and a
convention: a missing context is a `TypeError` at the call site, not a wrong
decision three ticks later. The real adapter must supply the same signal from
Home Assistant's context object in Phase 4, which is why the field is a port
requirement rather than a fake detail.

#### Scenario: A write omits its context
- **WHEN** a mutating call is made without a `ChangeContext`
- **THEN** the call fails at construction, naming the missing argument

#### Scenario: Contexts are distinguishable
- **WHEN** one entity is written with an `engine` context and another with a
  `user` context
- **THEN** a read of each reports the two origins, and the engine's override
  detection sees only the second as overridden

### Requirement: Only a manual user action produces a `user`-origin change
A change SHALL carry `origin: user` only when a caller invoked the manual
user-action path; no fault, no availability change, no restart, no engine-origin
actuation and no `world`-origin change SHALL produce a `user`-origin change. The manual
user-action path is the control surface's `user_action` operation
(`control-surface`), which reaches the port as an actuation carrying a
`user`-origin `ChangeContext`.

An origin a scenario cannot trust is worse than no origin: the override,
rate-limit and arbitration outcomes all read it, and a fault or a restart that
claimed `user` would suppress exactly the behaviour the scenario is asserting
fired. The rule is stated negatively on purpose — a test asserts that every
operation *except* the user-action path leaves the last-writer origin as
something other than `user` — because the failure mode is a false `user`, not a
missing one.

#### Scenario: A fault does not masquerade as a user
- **WHEN** a fault is injected on an entity and the entity is then read
- **THEN** its origin is `fault`, and the engine's override detection does not
  suppress the behaviour on account of it

#### Scenario: A restart does not masquerade as a user
- **WHEN** the house is restarted and its entities are read
- **THEN** no entity's last-writer origin is `user`

#### Scenario: A user action is the one user-origin path
- **WHEN** the `user_action` operation is called on an entity
- **THEN** that entity's last-writer origin is `user`, and it is the only
  operation in the port that can produce that origin

### Requirement: Availability is separate from state, and unavailable is not off
A read SHALL return availability as a field distinct from the entity's state,
and an unavailable entity SHALL NOT be reported as `off` or as any other
ordinary state. Setting availability SHALL be its own operation, independent of
state: marking an entity unavailable SHALL leave its last known state readable
and SHALL mark it unavailable, and returning it to available SHALL NOT be an
actuation.

An engine that cannot tell "off" from "unknown" makes a wrong decision in the
case the corpus calls out most often — a device that goes unavailable and
returns (`catalog/edge_cases.yaml`). Collapsing the two would let a behavioural
"turn the lights off" command be read as already satisfied, or let a motion
behaviour treat a dead sensor as "no motion" and the room as empty. Keeping
availability a separate field is what lets a behaviour decide to skip, to hold
the last state, or to take a fallback rather than to mistake silence for
evidence.

#### Scenario: An unavailable entity is not off
- **WHEN** an entity reading `on` is marked unavailable
- **THEN** the read reports `available: false` with its state not equal to
  `off`, and the two are distinguishable

#### Scenario: An unavailable device returns
- **WHEN** an entity is marked unavailable and later marked available
- **THEN** the read reports `available: true` and its state is readable, without
  any actuation having been issued

#### Scenario: Availability is not written as a state
- **WHEN** a behaviour writes a state to an unavailable entity
- **THEN** availability is unchanged by the write, because the two are separate
  fields with separate operations

### Requirement: Reads are typed by entity and identify entities by `domain.object_id`
Every entity SHALL be identified by an `entity_id` matching
`^[a-z_]+\.[a-z0-9_]+$` — the same shape the frozen house schema gives a binding
(`schemas/house/1.0.0.json`) and the corpus gives an `entity_ref` — and an
operation that receives an id outside that shape SHALL fail naming the id. A
read SHALL return that entity's domain, state, attributes and availability. A
read of an entity that does not exist SHALL fail with a named error and SHALL
NOT return a default state, because a default is a silent "off" the engine
cannot tell from a real one.

The id shape is not restated here for its own sake: it is the point where the
engine's world meets the frozen schemas, and a loose id would let a fixture bind
an entity the house schema could never name, which is exactly the drift
`configuration-schemas`' conformance check exists to prevent.

#### Scenario: A read of an unknown entity
- **WHEN** a read names an `entity_id` that is not in the adapter
- **THEN** the read fails with an error naming the id, rather than returning a
  default state

#### Scenario: An entity id outside the schema shape
- **WHEN** an operation receives an id that does not match
  `^[a-z_]+\.[a-z0-9_]+$`
- **THEN** the operation fails and names the id

#### Scenario: A read carries domain, state, attributes and availability
- **WHEN** an entity is read
- **THEN** the result carries its domain, its state, its attributes and its
  availability, each addressable without a second call

### Requirement: The port can add and remove entities and enumerate the house
The port SHALL expose operations to add an entity with an initial state and
attributes, to remove an entity, and to enumerate the entity ids present. A
removed entity SHALL read as absent — not as `off` — and enumerating SHALL NOT
include it. Adding and removing are what makes every entity a mock: the fake
house's fixtures, its duplicate bindings and its unavailable devices are
produced through these operations rather than loaded as a snapshot
(`design.md` D11), so a fixture cannot hold a state the port could not produce.

#### Scenario: A removed entity is absent, not off
- **WHEN** an entity is removed and then read
- **THEN** the read fails with a named error, and enumerating the house does not
  list the entity

#### Scenario: A fixture is built through the port
- **WHEN** a fixture house is built
- **THEN** it is constructed by calling `add_entity` and the other control-facing
  operations, and holds no state those operations could not reach

### Requirement: Fault injection is a first-class operation with its own origin
The port SHALL expose a fault-injection operation that changes an entity's state
or availability and records the change with `origin: fault`. A fault SHALL be
distinguishable, on a subsequent read and in the decision log, from a change the
engine made and from one a user made.

Faults are a change *origin* rather than a change *value* because the engine's
whole recovery story — the flaky device, the sensor that stops reporting, the
restart mid-absence (`catalog/edge_cases.yaml`) — turns on the engine noticing
that the change was not its own and not the user's. Folding a fault into an
engine write would make the engine treat its own failure as its own decision,
and folding it into a user write would trip override detection on a device that
never saw a human.

#### Scenario: An injected fault is recorded as a fault
- **WHEN** a fault is injected on an entity
- **THEN** a subsequent read reports the entity's last-writer origin as `fault`

#### Scenario: A fault is not an engine action
- **WHEN** the decision log holds a record produced by a fault-driven change
- **THEN** the record's actor is the fault origin and not the engine, so a
  scenario can assert the engine did not choose it

### Requirement: Restart returns the house to a defined startup condition, per device
Restart SHALL be a port operation that returns the house to a defined startup
condition, and what each entity's startup condition is SHALL be the adapter's to
define — an entity whose integration has not reconnected is `unavailable` at
startup, not `off`. A restart SHALL NOT produce a `user`-origin change on any
entity. The startup condition SHALL be part of the adapter's snapshottable state
so that a restart is reproducible from a fixed configuration.

"What a device does on restart" is the adapter's, not the engine's
(`design.md`), because only the adapter knows the device. The engine's job is to
decide correctly against whatever condition it finds, and the corpus's restart
seeds — a house reloading with exterior lights already on, a restart mid-absence
— are only expressible if the condition a restart produces is stated and
observable rather than assumed to be "everything off". Reporting a
reconnecting entity as `off` would be the same unavailable-is-not-off mistake in
a second place.

#### Scenario: Restart yields a defined startup condition
- **WHEN** the house is restarted from a fixed configuration
- **THEN** every entity reads its startup condition, and an entity whose
  startup condition is unavailable reads `unavailable`, not `off`

#### Scenario: Restart is not a user action
- **WHEN** a restart completes
- **THEN** no entity's last-writer origin is `user`

#### Scenario: Restart is reproducible
- **WHEN** the same configuration is restarted twice
- **THEN** the resulting startup condition is identical, so a scenario's seed
  reproduces the same post-restart decisions

### Requirement: The port snapshots its own state and nothing else
The port SHALL expose a snapshot operation that captures exactly the adapter's
own state — the entities present, their states, their attributes and their
availability. It SHALL NOT capture the engine's state (bindings, modes, enable
flags, override records, rate-limit windows) or the simulator's state (the
clock position, the random stream position); those are snapshotted by
`engine-core` and `simulation` and combined by the composition root.

The boundary is drawn here because it is the same boundary as the rest of the
port: what the adapter owns is what a second adapter would have to reproduce,
and a snapshot that reached into the engine would make the fake's snapshot a
test-only object the real adapter could never satisfy. Splitting the snapshot by
owner is what lets `design.md`'s enumerated restore — which does include the
clock and the stream — be assembled from two honest halves rather than one that
knows about Home Assistant's absence and a wall clock at once.

#### Scenario: The port snapshot excludes engine state
- **WHEN** the port's snapshot is taken
- **THEN** it carries entities, states, attributes and availability, and carries
  no binding, mode, enable flag, override record, rate-limit window, clock
  position or random-stream position

#### Scenario: A restore from the two halves is identical
- **WHEN** the port snapshot and the engine/simulator snapshot are taken, then
  restored and the same seed replayed
- **THEN** the decisions are identical to the original run

### Requirement: The port is pure and carries no Home Assistant type
No operation in `engine/adapter.py` SHALL name a `homeassistant` type in its
signature, its return type, or any type it defines, and the module SHALL import
no `homeassistant` submodule and no third-party module outside the declared
dependency list. The port's types — the entity view, the change context and its
origin, the fault descriptor and the snapshot — SHALL be defined in
`engine/adapter.py` or in the standard library.

This extends `architecture-invariants`' engine-purity rule from an empty tree to
real code and states the part that rule could not: that the *contract* is
HA-free, not merely the code behind it. A port whose types came from Home
Assistant would compile in this phase and fail in Phase 4, because the fake
would have to fabricate HA types and the engine would carry the dependency it
exists to avoid.

#### Scenario: The port names a Home Assistant type
- **WHEN** any signature, field or return type in `engine/adapter.py` references
  a `homeassistant` name
- **THEN** the purity scan fails and names the member and the type

#### Scenario: The port satisfies the declared-dependency rule
- **WHEN** `engine/adapter.py` imports a third-party module
- **THEN** the module is in the root `pyproject.toml` `dependencies`, or the
  check fails and names the file and the module

### Requirement: The contract suite binds every implementation to the port
There SHALL be a contract suite — a test that binds an implementation to
`HouseAdapter` — and it SHALL be a pure function of the port: it SHALL name no
method, attribute or behaviour specific to `FakeHouseAdapter` or to any other
implementation. It SHALL be parametrized over a registry of implementations, and
`FakeHouseAdapter` SHALL be registered in this phase. The suite SHALL assert
every requirement in this capability against a subject it is given, including
the change-context construction rule, the user-origin rule, the
availability-stands-apart rule, the absence semantics, the fault origin and the
restart semantics.

The suite is a pure function of the port so that Phase 4 can point it at the
Home Assistant adapter unchanged: a suite with a fake-specific line is a suite
that must be edited — and therefore weakened — exactly when it is first asked to
hold a second implementation to the same contract. The registry is named rather
than implied so that "no implementation is subject to the suite" is a detectable
state rather than a passing run over an empty parameter list.

#### Scenario: The suite references a fake-specific member
- **WHEN** the contract suite calls a method or reads an attribute that is not
  declared on `HouseAdapter`
- **THEN** the suite's own guard fails and names the fake-specific reference

#### Scenario: An implementation is not held to the contract
- **WHEN** the implementation registry is inspected
- **THEN** it is non-empty and `FakeHouseAdapter` is registered, so an empty
  registry fails rather than passes

#### Scenario: The contract suite passes against the fake
- **WHEN** the contract suite runs against `FakeHouseAdapter`
- **THEN** every requirement in this capability is exercised and passes

### Requirement: The port carries mechanism, not policy
The port SHALL move states and record who moved them, and SHALL carry no notion
of a slot, a behaviour, a mode, an enable flag, a rate limit, an arbitration
priority, or safety. In particular the port SHALL NOT refuse an actuation on
safety grounds: a non-user unlock or open-cover command is refused by the
engine's action gate (`product-invariants`), before the adapter is asked, and is
recorded in the decision log as `refused: unsafe`.

The safety veto is deliberately not in the adapter (`design.md` D9): an adapter
that refuses a command has already let a behaviour *propose* it, so the proposal
would be invisible in the log and the guarantee that a behaviour cannot propose
an unlock at all would be lost. Keeping the port free of policy is also what
keeps it implementable by an adapter that has no opinion — the real adapter's
job in Phase 4 is to relay, not to decide.

#### Scenario: The port refuses an actuation on safety grounds
- **WHEN** an adapter refuses a lock-unlock or cover-open actuation because its
  origin is not `user`
- **THEN** the contract check fails, because the refusal must appear in the
  engine's decision log as `refused: unsafe` rather than at the port

#### Scenario: A user-origin unlock reaches the house
- **WHEN** the engine's gate admits a `user`-origin unlock and passes it to the
  port
- **THEN** the port actuates it, because the port does not re-decide the gate's
  verdict
