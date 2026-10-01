# Proposal — Phase 1: Engine and full mock house

## Why

Phase 0 shipped data and contracts: a merged 83-row behaviour corpus, the frozen
versioned schemas, the slot and room-type vocabularies, and four empty Python
packages whose purity is asserted but never exercised. Nothing in the repository
yet *decides* anything. The product's central claim — bind devices to
placeholder slots and the home behaves — is unfalsifiable until there is an
engine that decides and a house for it to decide against. Phase 1 is where the
project becomes executable, and its exit criterion says the specific way it must
become executable: **an AI agent can build a house, run scenarios, read the log,
and iterate with no HA installed.**

That criterion is why this phase is a whole and not a pile of parts. Two things
force the parts to be built together. First, the **decision log is the test
oracle**: a scenario asserts not only that a light is off but that it is off
*because the engine turned it off and not because it never ran* — so the engine
and the simulator must agree on what a decision *is* before either is testable.
Second, `no HA installed` is a property of the whole stack at once: the engine
must be pure, the port it drives must carry no Home Assistant type, the fake
must satisfy the same port a real adapter will, and the agent's entry point must
reach all of it without a running instance. Delivering the fake without the log,
or the engine without the port, produces something no agent can iterate against.

Phase 1 consumes Phase 0 rather than extending it. The engine binds to
`schemas/slot/`, `schemas/room-type/`, `schemas/house/` and `schemas/mode/`; it
matches a house's bound slots against the 14-slot vocabulary in
`catalog/slots.yaml`, against the 20 room types plus the `house` scope in
`catalog/room_types.yaml`, and it writes its first behaviours from the corpus
rows in `catalog/behaviors.yaml` (83 rows, 44 of them `module_candidate`). It is
bound by `architecture-invariants` and may not weaken it: the engine stays free
of `homeassistant`, and the checks that keep it so now have real code to police.
Phase 1 adds no requirement to Phase 0's capabilities; it is written inside
them.

Two product rules bind this phase and are written into the specs so that no
later phase can rediscover them by surprise: **modules and behaviours are OFF by
default and every one of them can be turned off**, and **nothing in the system
may ever auto-unlock a door or auto-open a garage**. The first is the reason a
freshly built house runs nothing until the agent or the user enables a
behaviour; the second is a hard veto at the point an action would reach the
house.

## What Changes

### The port and the fake house

- **`HouseAdapter` interface** — the operations the engine needs of a house:
  read and write entity state, enumerate entities, add and remove them, mark
  them unavailable, inject faults, restart the house, and snapshot its state.
  Every change carries a **context** (a user, the engine, the world, or a
  fault), which is what manual-override detection later reads. The port is
  pure: no Home Assistant type crosses it, and the engine depends on the port and
  not on any
  implementation of it.
- **`FakeHouseAdapter`** — a fully controllable mock in which every entity is
  mock: add and remove entities, set state, inject manual user actions, mark
  devices unavailable, inject faults, and restart.

### The deterministic substrate

- **Virtual clock** — time advances only when asked, never from the wall.
- **Seeded randomness** — one seeded stream; the same seed replays the same run.
- **No network**, anywhere in the engine or the simulator, enforced rather than
  assumed.
- **Snapshot and restore** of the full observable state, the clock and the
  random stream, so a restored house continues to decide identically.

### The engine core

- **Slot binding**, optional and list-capable: a slot resolves to zero or more
  entities, a behaviour with an unbound required slot is skipped and logged, and
  an unbound optional slot degrades the behaviour rather than disabling it.
- **Layered config resolver** with its precedence order fixed now, so Phase 3
  can insert the profile layer without reordering anything.
- **House modes** with exclusivity groups.
- **Arbitration and priorities**: commands to one entity in one tick reduce to
  one command; an explicit user action outranks every behaviour; ties break
  deterministically.
- **Manual-override detection**: an entity the user last touched is suppressed
  until a reset condition.
- **Rate limits** per entity per time window.
- **A decision log that doubles as the test oracle**: one structured record per
  evaluation, naming the rule, the inputs, the commands, and — critically — why
  a command was *not* issued.

### The first behaviours

- **Motion lighting**, on when dark and off on a quiet timeout, using a lux
  reading when one is bound and the computed sun position when it is not.
- **Override** of a room's lighting by a manual change.
- **Away shutdown** of interior lighting when the house empties.

### The scenario runner

- A YAML DSL whose steps are the same operations the control surface exposes, so
  a scenario and an agent's session are the same vocabulary.
- **Hypothesis invariants** over the running engine, **mutation testing** to
  measure whether the suite would notice a broken decision, and **JSON failure
  output** naming the failed step, the expected and actual values, the seed and
  the relevant log slice.

### Fixture houses

Minimal, messy (one slot bound in two rooms and devices that report
unavailable), large (150+ devices), and one with no lux sensors at all so the
sun fallback is exercised on its own.

### The control surface

A Python library, a CLI, and an MCP server exposing one set of operations:
`advance_time`, `set_state`, `user_action`, `inject_fault`, `snapshot`,
`restore`, `get_decision_log`, `install_pack`, `export_config`, `import_config`.

### Deliverable

A running engine and fake house, drivable and inspectable end to end, with the
scenario runner and control surface layered on top and CI exercising the whole
agent loop with no Home Assistant present. The Python packages `engine/`, `sim/`
and the new composition root are typed, importable and covered by the phase's
own gate; `ha_adapter/` remains empty until Phase 4 supplies the real adapter
for the port.

## Capabilities

### New Capabilities

The phase's headline output is a decomposition of its specs into capabilities;
`design.md` carries the reasoning for where each seam falls. Seven:

- `engine-core`: the deterministic decision engine — slot binding, the layered
  config resolver, house modes, arbitration and priorities, manual-override
  detection, rate limits, and the decision log that is the test oracle.
- `house-adapter`: the `HouseAdapter` port — its operation set, its change
  context, its purity, and the contract every implementation (the fake now, the
  Home Assistant adapter in Phase 4) must satisfy.
- `simulation`: the `FakeHouseAdapter`, the virtual clock, seeded randomness,
  the no-network rule, snapshot and restore, and the fixture houses.
- `first-behaviours`: motion lighting with the lux-then-sun fallback, manual
  override, and away shutdown — the first policy written against the core.
- `scenario-runner`: the YAML DSL, the Hypothesis invariants, the mutation
  testing gate, and the JSON failure output.
- `control-surface`: the library facade and the ten operations, with the CLI
  and the MCP server as thin adapters over the same registry.
- `product-invariants`: the two binding product rules — nothing is on unless
  enabled and everything can be turned off, and nothing ever auto-unlocks a door
  or auto-opens a garage.

### Modified Capabilities

_None. Phase 1 is bound by `architecture-invariants` and consumes the schemas
and catalog from `phase-0-foundations`; it adds no requirement to any Phase 0
capability. Phase 0's delta specs are not yet archived into `openspec/specs/`,
which the archiving step settles before this change is applied._

## Impact

- **New artifacts:** code under `engine/` (the port and the core), `sim/` (the
  fake house, clock, randomness, snapshot and fixtures), the scenario runner,
  the fixture houses, and a new top-level composition root holding the library,
  CLI and MCP server. New tests under `tests/`. New declared dependencies
  (the MCP Python SDK, and the test-only Hypothesis and mutmut) added to the
  root `pyproject.toml` — which is a change to the declared dependency list the
  engine-purity invariant reads, and therefore to an enforced boundary rather
  than to a convenience.
- **Consumed by:** Phase 2 (the pack interpreter installs modules and behaviours
  through this engine, and its "two modules cannot fight over one light" exit
  criterion is this phase's arbitration exercised on data), Phase 3 (the export
  round-trip is built on this config resolver and this snapshot), Phase 4 (the
  `HAAdapter` implements the port this phase defines, and the contract suite
  starts here against the fake).
- **Constraints created:** the engine-purity invariant stops being a claim about
  an empty tree and becomes load-bearing; the two product rules become gates
  that every later behaviour, pack and configuration must pass.
- **No Home Assistant anywhere in the loop.** `ha_adapter/` stays empty; nothing
  this phase delivers imports `homeassistant` or opens a socket, and CI proves
  it by running the whole agent loop in a checkout where Home Assistant is not
  installed.
- **Unresolved by this proposal:** the concrete tuning of the first behaviours
  (the quiet timeout, the lux threshold, the arbitrated priority of each
  behaviour) is corpus- and fixture-derived *content*, settled by running the
  scenarios rather than by the specs.
