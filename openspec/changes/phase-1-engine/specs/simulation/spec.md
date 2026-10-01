# Spec Delta — simulation

## Purpose

The deterministic substrate the phase stands on. It owns one package, `sim/`, and
inside it the four things that make an engine testable without a house: the
`FakeHouseAdapter`, in which every entity is a mock and every axis is reachable;
a virtual clock and a single seeded random stream, so a run is a number rather
than a moment; snapshot and restore over the full observable state, so a run can
be cut in half and resumed; and the four fixture houses the scenarios are written
against. It implements the port `house-adapter` defines and feeds the runner
`scenario-runner` owns, and it depends on neither of them in the other
direction: `sim/` may import `engine/`, and `engine/` may not import `sim/`
(`design.md` D12).

Determinism is not a property of the simulator for its own sake. It is what makes
a Hypothesis counterexample reproducible, what makes a surviving mutant a
statement about a missing test rather than about a flaky run, and what lets the
decision log `engine-core` records be compared across two runs at all (D3). So
the rules below are enforced by checks rather than stated as intentions: a scan
that no module in `engine/` or `sim/` reads the wall clock, a guard that fails if
a scenario run opens a socket, and a restore-and-replay property that fails when
the snapshot misses a piece of state. A promise nothing runs is not a promise.

Two product rules bind this capability as they bind every other, and both touch
the simulator. **Modules and behaviours are OFF by default and every one of them
can be turned off**: a freshly built fixture runs nothing, and the enable flags —
the state the rule is decided from — are part of what snapshot carries, so a
restored house resumes with exactly the set of behaviours it had before. And
**nothing in the system may ever auto-unlock a door or auto-open a garage**: the
simulator is where a command would physically be applied, so it is the last place
the veto can be observed to hold, and no operation of the fake originates such a
command (the gate itself is `engine-core`'s `engine/safety.py`, stated as a
product rule by `product-invariants`).

## ADDED Requirements

### Requirement: The fake implements the HouseAdapter port over a mock entity registry
`sim/adapter.py` SHALL define `FakeHouseAdapter`, and it SHALL satisfy every
member of the `HouseAdapter` `Protocol` defined in `engine/adapter.py` — the one
port `house-adapter` owns — over an in-memory registry of mock entities. It SHALL
implement the port and no method of it SHALL be a stub: every operation that
appears in the port has a working implementation here, and the port's contract
suite SHALL pass against the fake unchanged. The fake SHALL add nothing that the
engine could reach around the port for: a behaviour or the engine that calls a
fake-only method has reached past the seam the port exists to be, so the port's
surface is the fake's surface for the engine's purposes.

The reason is the one the two-implementation contract turns on. The port gains
its second subject in Phase 4, and the only thing that makes its second subject
comparable is that the first was never given a private door. A fake with extra
methods the suite does not exercise would let the engine quietly depend on the
fake's shape, and the Phase 4 adapter would then fail to satisfy a port the
engine only appeared to use.

#### Scenario: A port member is unimplemented
- **WHEN** an operation named in `HouseAdapter` has no working implementation in
  `FakeHouseAdapter`
- **THEN** the port's contract suite fails and names the operation

#### Scenario: The fake carries a private door
- **WHEN** `engine/` depends on a `FakeHouseAdapter` method that is not a member
  of `HouseAdapter`
- **THEN** the port-conformance check fails and names the method, because the
  engine is bound to the port and not to the fake

#### Scenario: The fake is constructed with its substrate
- **WHEN** a `FakeHouseAdapter` is constructed for a run
- **THEN** it is given the run's `VirtualClock` and its seeded `RandomStream`,
  and it reads time and randomness only from those

### Requirement: Every mock entity is controllable through the port
The fake SHALL expose every control axis `spec.txt` names, so that a failure a
scenario describes is a failure the fake can actually provoke: add an entity,
remove an entity, set an entity's state, make a manual user action, mark an
entity unavailable, inject a fault, and restart the house. Each axis SHALL be
reachable through the port's operations rather than through a fixture that was
built with the state baked in (D11).

The axes split across the port's two faces (`house-adapter`). `set_state`,
`user_action` and `inject_fault` are also operations of the `control-surface`
registry; `add_entity`, `remove_entity`, `set_availability` and `restart` are
control-face port operations the registry does not carry, which the composition
root exposes as a house-control facility and the scenario runner drives as
house-control steps (`scenario-runner`), so a scenario can provoke a device going
unavailable or a restart mid-absence without an eleventh registry operation
`spec.txt` does not enumerate.

This is the phase's "everything is controllable" stated as a check. A scenario
that cannot make a device go unavailable cannot test the recovery from an
unavailable device, and a fixture committed as a JSON snapshot could encode a
state — a duplicate binding, a half-open cover — the adapter's own operations
could never produce, which is a test that passes for a reason the system cannot
reproduce. Requiring the axes to be operations is what keeps a fixture and a
scenario speaking the same vocabulary.

#### Scenario: An entity is added and removed
- **WHEN** a scenario adds an entity and later removes it
- **THEN** the entity is enumerable after the add and absent after the remove,
  and both changes are ordinary port writes carrying a change context

#### Scenario: A device is made unavailable and returns
- **WHEN** an entity is marked unavailable and later marked available
- **THEN** a read while it is unavailable reports it unavailable rather than
  reporting its last value, and the return is observable as a state change

#### Scenario: A fault is injected
- **WHEN** `inject_fault` is called against the fake
- **THEN** the affected entity changes with a `fault`-origin context, so the
  change is distinguishable from one the engine or the user made

#### Scenario: The house is restarted
- **WHEN** the restart operation is called on the fake
- **THEN** the registry is returned to its defined startup condition and the
  restart is observable to the engine as an operation, not as silence

### Requirement: Every state change carries a change context
Every write the fake performs SHALL carry a change context naming its origin as
one of `user`, `engine`, `world` or `fault`, and the fake SHALL reject a write
constructed with no context. The four origins SHALL be distinguishable from the
change record alone, because the single field manual-override detection
(`engine-core`) depends on is the change's origin, and it reads it from the port
rather than from a fake detail (`house-adapter`). The `user_action` operation
SHALL be the one that produces a `user`-origin change, `set_state` a `world`-origin
change — a scenario or an agent tripping a sensor is the world changing, which
the engine must be able to tell from its own actuation — `inject_fault` a
`fault`-origin change, and the engine's own actuation through the port an
`engine`-origin change, so a sensor that trips, a fault that is injected and the
engine's own write are told apart by their context and not by their value.

The alternative — inferring "was this the user?" from a state delta — is what
makes an override system wrong in the field. A user turning a lamp on and an
automation turning the same lamp on to the same brightness leave the same delta
and a different intent, and the engine's whole override mechanism is the
difference. Rejecting a context-free write at construction is what stops the
distinction from being dropped by accident on a future path.

#### Scenario: A write carries no context
- **WHEN** a write is attempted through the fake without a change context
- **THEN** the fake raises and the write does not reach the registry

#### Scenario: The four origins are distinguishable
- **WHEN** the same entity is written once by `user_action`, once by `set_state`,
  once by `inject_fault` and once by the engine's own actuation
- **THEN** the four changes carry `user`, `world`, `fault` and `engine`
  respectively and are told apart by their context alone

### Requirement: The virtual clock is the only source of time
Time SHALL advance only through the clock, and the clock SHALL advance only when
`advance_time` is called: `sim/clock.py` SHALL define `VirtualClock`, whose `now`
returns the current virtual instant and whose `advance` moves it, and no member
of `engine/` or `sim/` SHALL read the wall clock. The wall-clock obligation is a
check, not a convention: no module in either package SHALL call `time.time`,
`time.monotonic`, `time.perf_counter`, `datetime.now`, `datetime.utcnow` or
`datetime.today`, and the only module permitted to touch the system clock at all
is `sim/clock.py`, which uses it once at construction to seed a start instant the
run then fixes.

This is what makes "the light goes off after a ten-minute quiet period" a
statement about a number rather than about sleeping, and it is why the phase
rejects the alternative of running on the real clock and freezing time in tests:
a freezer in the test does not stop the engine from holding a wall-clock
dependency that makes one path untestable by construction, and the exit criterion
wants an agent to advance time explicitly rather than wait for it.

#### Scenario: A module reads the wall clock
- **WHEN** any module under `engine/` or `sim/` other than `sim/clock.py` calls
  a wall-clock function
- **THEN** the clock scan fails and names the file and the call

#### Scenario: Time advances only on request
- **WHEN** a scenario advances the clock by ten minutes
- **THEN** the engine observes the virtual instant moved by exactly ten minutes,
  and no instant is observed to have passed before `advance_time` was called

#### Scenario: Advancing by zero changes nothing
- **WHEN** `advance_time` is called with a zero interval
- **THEN** no decision differs from the run without the call

### Requirement: Randomness is one seeded stream owned by the simulator
All randomness the engine or the simulator consumes SHALL come from a single
stream, `sim/entropy.py`'s `RandomStream`, constructed with the run's seed and
owned by the simulator; no module in `engine/` or `sim/` SHALL call the `random`
module directly or construct a second generator. Two runs at the same seed SHALL
produce identical decisions, and two runs at different seeds SHALL not. The
stream's position SHALL be part of the snapshot (below), so a restored run
continues the sequence the original would have continued rather than restarting
it.

One stream rather than one per component is what makes the seed a single control.
Two generators seeded from the same source diverge the instant one component
consumes a number the other did not, so a replay would be exact only for the
components that happened to draw the same count — which makes a Hypothesis
counterexample reproducible in principle and irreproducible in practice.

#### Scenario: The same seed replays
- **WHEN** a scenario runs twice at the same seed and clock
- **THEN** both runs produce the same device states and the same decision records

#### Scenario: Different seeds diverge
- **WHEN** a scenario whose behaviour depends on randomness runs at two seeds
- **THEN** the two runs are not identical, so the test above is not vacuous

#### Scenario: A second generator appears
- **WHEN** a module under `engine/` or `sim/` imports `random` or seeds a
  generator of its own instead of using `RandomStream`
- **THEN** the randomness scan fails and names the module

### Requirement: The simulator is hermetic — no network, no Home Assistant, no undeclared import
Neither `engine/` nor `sim/` SHALL import a networking module (`socket`, `ssl`,
`asyncio`'s transport layer, `http`, `urllib`, `smtplib`, `requests`, `aiohttp`),
SHALL import `homeassistant` in any form including guarded and `TYPE_CHECKING`
imports, or SHALL import a third-party module outside the declared dependency
list. A scenario run SHALL additionally execute under a guard that fails if a
socket is opened during the run, so the rule is checked at the moment it could be
broken rather than only over the import graph.

This is the phase's defining constraint, stated twice on purpose. The import scan
catches a networking module pulled in at the top of a file; the runtime guard
catches the same module reached dynamically, which is exactly the evasion a scan
alone would miss and the one an AI-authored commit is most likely to introduce
without meaning to. The Home Assistant half is the Phase 0 purity scan
(`architecture-invariants`) extended to `sim/`: the invariant stops being a claim
about an empty tree and becomes load-bearing the moment this package has code.

#### Scenario: A package imports a networking module
- **WHEN** a file under `engine/` or `sim/` imports a networking module
- **THEN** the hermeticity scan fails and names the file and the module

#### Scenario: A run opens a socket
- **WHEN** a scenario run executed under the socket guard opens a socket, however
  it reached it
- **THEN** the guard fails the run and names the call site

#### Scenario: A file imports Home Assistant behind a guard
- **WHEN** an import of `homeassistant` appears in a `try`, an `if TYPE_CHECKING`
  or a function body under `engine/` or `sim/`
- **THEN** the extended purity scan fails and names the file

#### Scenario: The committed tree is hermetic
- **WHEN** the hermeticity checks run over the committed `engine/` and `sim/`
- **THEN** they pass with no network and no Home Assistant installed

### Requirement: Snapshot captures the enumerated runtime state and excludes the log
`sim/snapshot.py` SHALL define the `Snapshot` document and the `take_snapshot`
and `restore_snapshot` operations. The port's own snapshot operation
(`house-adapter`) supplies the adapter's slice — entities, attributes and
availability — and the simulator composes it with the runtime state the engine
owns and the substrate's positions into the one document the `snapshot`
operation returns; the port is not asked to hold state it does not own. A
snapshot SHALL carry, under a `snapshot_version` field, the complete observable
state a run needs to resume:
every entity with its state, its attributes and its availability; the house's
slot bindings; the house modes; every behaviour's and module's enable flag; the
manual-override records; the rate-limit windows; the virtual clock's position;
and the random stream's position. It SHALL be serialisable to a stable JSON form
so two snapshots of the same state are equal documents. It SHALL NOT carry the
decision log, which is history rather than state.

The contents are enumerated rather than described because the failure they guard
against is an omission, and an omission is invisible until a replay diverges for
a reason no one can see (D3). The log is excluded deliberately: state is
history-independent and history is not, so restoring a run restores what it will
decide from and not what it has already decided — and a log carried into a
restore would grow without bound along exactly the axis the log's own bound
exists to cap.

#### Scenario: A snapshot round-trips through JSON
- **WHEN** a snapshot is serialised and read back
- **THEN** the two documents are equal and restoring either yields the same
  runtime state

#### Scenario: An enumerated field is missing
- **WHEN** a snapshot omits any of the enumerated fields — an entity's
  availability, the random stream's position, an enable flag
- **THEN** the restore-and-replay property below fails and names the field whose
  loss changed a decision

#### Scenario: The log is snapshotted
- **WHEN** a snapshot is taken after a run has appended decision records
- **THEN** the snapshot carries no decision record, and the document's size is
  independent of how long the run was

### Requirement: Restore resumes decision-making identically
Restoring a snapshot and continuing a run SHALL yield decisions identical to the
run that was never interrupted: for the same seed, the same input operations
after the restore SHALL produce the same device states and the same decision
records as those the uninterrupted run produced. This SHALL be asserted as a
property over the running engine rather than as a single example, so an
incompletely enumerated snapshot fails as a falsified invariant rather than as a
silent divergence.

The property is the one that makes the enumeration above more than bookkeeping:
without it, "the snapshot is complete" is a claim a reader checks, and with it,
completeness is a claim the suite checks. It is also the mechanism Phases 3 and 4
lean on — the export round-trip and the integration's restart handling both
assume a restored house decides the way the original did.

#### Scenario: Restore then continue equals uninterrupted
- **WHEN** a scenario is run once to completion and once with a snapshot taken
  midway, restored, and continued
- **THEN** the two runs' final states and decision records are identical

#### Scenario: A snapshot misses the random stream position
- **WHEN** the random stream's position is left out of the snapshot and a
  random-consuming behaviour runs after the restore
- **THEN** the property fails, so the omission is caught rather than tolerated

### Requirement: A restart returns the house to a defined startup condition
The restart operation SHALL return the house to a defined startup condition, and
that condition SHALL preserve the phase's rule that an unavailable device is not
off: a device that was unavailable before the restart SHALL be unavailable after
it, not silently read as switched off. What a restart means, and what each
entity's startup condition is, are the port's to define (`house-adapter`); this
capability owns the fake's enactment of them, so the engine observes only that a
restart happened, and the fixture houses SHALL include the restart conditions
the corpus's edge cases name — a stateless light whose condition is unknown at startup, a
presence selector left mid-transition, a mode and its timers surviving a restart
mid-absence — so the seeds in `catalog/edge_cases.yaml` are expressible.

The distinction between unavailable and off is the reason this is a requirement
rather than a default. Treating an unavailable device as off is the failure the
corpus records as "a cloud integration stops reporting and an appliance
temperature reads unknown", and a fake that erased availability on restart would
make the bug untestable in exactly the phase built to test it.

#### Scenario: An unavailable device crosses a restart
- **WHEN** a restart runs with an entity marked unavailable
- **THEN** the entity is unavailable afterwards, and a behaviour reading it is
  not told it is off

#### Scenario: A stateless light at startup
- **WHEN** the house restarts with a light whose state it cannot confirm
- **THEN** the defined startup condition leaves it in the state the current mode
  implies rather than a fiction of its last command

#### Scenario: A restart mid-absence
- **WHEN** the house restarts with an away mode set and its timers running
- **THEN** the mode and the timers survive the restart in the fake's startup
  condition, so a scenario can assert they do

### Requirement: The four fixture houses are built through the adapter
`sim/fixtures/` SHALL provide a `build_fixture` entry point and exactly four
fixtures, each constructed by calling the fake's own operations rather than
loaded from a baked snapshot: `minimal` (one or two rooms and a handful of
devices), `messy` (one slot bound in two rooms, so a house-scoped `light_group`
resolves to two entities, at least one device
reporting unavailable, and a missing lux sensor), `large` (150 or more entities),
and `no_lux` (no `lux_sensor` bound anywhere). Each fixture SHALL be reproducible
from its seed, and each SHALL be selectable by name so a scenario's `given` block
and an agent's session name the same fixture.

Building through the adapter is what keeps a fixture honest. A fixture committed
as JSON could hold a state the adapter cannot reach — a binding to a nonexistent
entity, a duplicate the port would not produce — and a scenario passing against
it would pass for a reason the running system cannot reproduce (D11). The four
fixtures are chosen to cover the ways "everything is a mock" is strained: the
messy one stresses list-capable binding and availability, the large one proves
the engine is not quadratic and the decision log stays legible, and the no-lux
one is the sun fallback's own subject.

#### Scenario: A fixture holds an impossible state
- **WHEN** a fixture is inspected and contains a binding, a duplicate or an
  availability the fake's operations could not have produced
- **THEN** the fixture check fails and names the state and the operation that
  would have had to produce it

#### Scenario: The large fixture is large enough
- **WHEN** the `large` fixture is built
- **THEN** it contains at least 150 entities, and building it does not open a
  network connection or read the wall clock

#### Scenario: The messy fixture strains binding
- **WHEN** the `messy` fixture is built
- **THEN** the house-scoped `light_group` slot resolves to two entities (the same
  slot bound in two rooms) and at least one device reports unavailable

#### Scenario: The no-lux fixture has no lux
- **WHEN** the `no_lux` fixture is built
- **THEN** no room binds `lux_sensor`, so a behaviour taking the sun branch is
  never offered a lux reading

### Requirement: Fixture location and time zone are fixture data
Each fixture SHALL carry a latitude, a longitude and a time zone as fixture
configuration, and the sun position a sun-following branch needs SHALL be
computed from those and the virtual clock rather than from any engine state. The
corpus supplies no location, so the fixtures invent one and the spec records it
as fixture data: the location is part of the house a scenario builds, not part of
the engine's decision state, and it moves with the fixture rather than with the
run.

The separation matters because the sun fallback is the one branch whose input is
neither a device nor a mode. Making the location engine state would put a
constant the user never chose into the same snapshot as the enable flags and
bindings, and would make "the same house at the same time decides the same" true
only as long as no one changed a value the user cannot see.

#### Scenario: The sun branch uses fixture location
- **WHEN** a sun-following branch runs
- **THEN** its input is computed from the fixture's location and the virtual
  clock, and two fixtures at different locations diverge at the same instant

#### Scenario: Location is not engine state
- **WHEN** a snapshot is taken
- **THEN** the fixture's location and zone appear as fixture configuration and
  not among the enumerated engine fields

### Requirement: A freshly built fixture runs nothing and the simulator never originates an unsafe command
Every fixture SHALL build with every behaviour's and every module's enable flag
set off, so a freshly built house runs nothing until a behaviour is enabled, and
the enable flags SHALL be part of the snapshot so a restored house resumes with
exactly the set it had. No operation of the fake SHALL, on its own initiative,
produce a command that unlocks a `lock` or opens a `cover`: an unlock or an
open-cover observable at the house SHALL have come from a `user`-origin change,
and the simulator SHALL be where that is observable, since it is where a command
would physically be applied.

These are the two product rules at the point the simulator touches them. The gate
that refuses a non-user unlock lives in `engine-core` and is stated as a product
invariant by `product-invariants`; what the simulator owes is the substrate half —
inert by default, and never the origin of an unsafe command — so a scenario can
assert both, and so a restored house cannot come back with a behaviour enabled
that the run before it had turned off.

#### Scenario: A fresh fixture does nothing
- **WHEN** any fixture is built and a scenario advances time through a motion
  event
- **THEN** no light changes, because every behaviour starts disabled

#### Scenario: Enablement survives a snapshot
- **WHEN** a behaviour is enabled, a snapshot is taken, and the house is restored
- **THEN** the behaviour is still enabled, and a behaviour that was off is still
  off

#### Scenario: The simulator originates an unsafe command
- **WHEN** a run reaches a state where a `lock` is unlocked or a `cover` opened
  and no `user`-origin change accounts for it
- **THEN** the run fails, naming the change that produced it, because the fake is
  where such a command would be applied
