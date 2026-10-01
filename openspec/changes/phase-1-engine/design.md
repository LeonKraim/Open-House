# Design — Phase 1: Engine and full mock house

## Context

See `proposal.md` — Why. The state that shapes this design:

- Phase 0 left four Python packages (`engine/`, `ha_adapter/`, `sim/`,
  `custom_components/`) that are importable and carry `py.typed`, and nothing
  else. The engine-purity invariant is asserted over empty trees, and the
  package comment in `pyproject.toml` says so: `exclude = ["engine",
  "ha_adapter", "sim", "custom_components"]` from strict checking, "its purity
  is asserted rather than exercised." Phase 1 exercises it, which is the first
  time a rule the project committed to in Phase 0 can fail.
- The engine binds to frozen contracts, not to prose. `schemas/slot/1.0.0.json`
  names the 14 slots in `catalog/slots.yaml`; `schemas/room-type/1.0.0.json`
  names the 20 types and the `house` scope; `schemas/house/1.0.0.json` is the
  document a user (or agent) writes; `schemas/mode/1.0.0.json` carries the
  `exclusive_group` that makes "one of these at a time" expressible. A house
  binding is `{entity_id, registry_id}` per slot; a binding may be absent, which
  is how an optional slot is left unbound.
- `catalog/behaviors.yaml` is 83 rows: 38 `generic`, 44 `module_candidate`, 1
  `discard`. Each row carries `required_slots`, `optional_slots`, a `scope` of
  `room` or `house`, and `license`/`reuse_status`/`obligations` derived from the
  corpus. The rows are the *specification* of the first behaviours —
  `lighting.motion_light_on`, `lighting.away_shutdown`, `modes.house_away` — and
  the engine implements them from the row rather than from a paraphrase.
- `catalog/edge_cases.yaml` was written for this phase. Every entry ends in a
  `phase_1:` field naming the simulator seed it becomes; twenty-six of them. They
  are the scenarios the runner must be able to express and the fake house must
  be able to provoke (a device going unavailable and returning, a restart
  mid-absence, a flickering door contact, a sensor that stops reporting).
- The tech stack chooses Typer for the CLI and the official MCP Python SDK for
  the MCP server; both are the outward faces of the same operations, and the
  same phase introduces Hypothesis and mutmut for the suite.

## Goals / Non-Goals

**Goals:**

- An engine whose decisions are *explainable*: every evaluation that does or
  does not act leaves a record naming the rule, the inputs and the reason, so
  the log is the oracle a scenario asserts against and the agent reads.
- A fake house controllable in every axis the phase names — add and remove
  entities, set state, make a user action, make a device unavailable, inject a
  fault, restart — because a scenario that cannot provoke a failure cannot test
  the recovery from it.
- Determinism strong enough to carry the tools built on it: a fixed seed and a
  fixed clock replay a run byte-for-byte, which is what makes Hypothesis
  failures reproducible and mutation testing meaningful.
- A port the engine depends on and *no* implementation it depends on, so the
  fake can be swapped for the real adapter in Phase 4 without the engine
  changing.
- Two product rules held mechanically: nothing runs unless enabled, and no
  command that unlocks a lock or opens a garage is ever emitted except in
  response to a direct user action.

**Non-Goals:**

- **Not** the pack interpreter. `install_pack` in this phase accepts a pack
  manifest that validates and checks its `requires_slots` against the house; the
  capability sandbox, the banned-service list and the conflict resolution that
  `spec.txt` gives Phase 2 are Phase 2's.
- **Not** the user-facing export and re-link. `export_config` and
  `import_config` round-trip the *house configuration* — the house and its
  bindings — which is what the agent builds; the export document with registry
  ids and the dry-run re-link wizard is Phase 3.
- **Not** profiles, their activation rules, or the axes `spec.txt` gives Phase 3.
  The config resolver is built with the layer *order* fixed, and the profile
  layer is reserved and empty.
- **Not** the real adapter, HA Areas, the config flow, or the panel. `ha_adapter/`
  stays empty; the contract suite has one implementation to hold to the port in
  this phase and gains its second in Phase 4.
- **Not** populating the behaviours the corpus already contains beyond the three
  `spec.txt` names. The other 80 rows are Phase 2's pack material; this phase
  builds three behaviours and the machine that runs them.
- No network, no wall clock, no Home Assistant. This is the phase's defining
  constraint rather than a limitation of it.

## Capability decomposition

This is the phase's primary output, and the seams are chosen so that a spec
author can write each capability's requirements from this section alone. Seven
capabilities, each a place the code actually divides.

| Capability | Owns | Code seam | Why it is its own spec |
| --- | --- | --- | --- |
| `engine-core` | The decision engine: binding, config resolution, modes, arbitration, override, rate limits, the log | `engine/` minus the port and the behaviours | The mechanism every later phase reuses; its requirements change only when decision semantics change |
| `house-adapter` | The `HouseAdapter` port: operations, change context, purity, the contract | `engine/adapter.py` (the port) | The engine's dependency edge; two implementations satisfy it across two phases |
| `simulation` | The fake house, clock, randomness, no-network, snapshot, fixtures | `sim/` | The deterministic substrate; it changes for testability reasons, never for decision reasons |
| `first-behaviours` | Motion lighting, override, away shutdown | `engine/behaviours/` | Policy, not mechanism; Phase 2 turns policy into data, and the boundary must exist first |
| `scenario-runner` | The YAML DSL, Hypothesis invariants, mutation gate, JSON failures | `sim/scenario/` | A client of the engine; its concerns are test infrastructure, unrelated to decision semantics |
| `control-surface` | The library facade and the ten operations, CLI and MCP over it | A new top-level composition root | Nothing in `engine/` or `sim/` depends on it, and it is the only place the two are wired together |
| `product-invariants` | OFF by default and everything disableable; no auto-unlock or auto-open | Enforced in `engine/`, owned nowhere in particular | Cross-cutting product promises that bind every other capability and every later phase |

### `engine-core` — the decision engine

The capability every later phase reuses. Its requirements, which the spec will
state normatively:

- **Slot binding is optional and list-capable.** A slot resolves to zero or more
  entities. A behaviour whose *required* slot is unbound is skipped, and the
  skip is logged with its reason; a behaviour whose *optional* slot is unbound
  still runs, degraded — which is the mechanism the lux-then-sun fallback is
  built on. State reads over a multi-entity slot carry a defined reduction
  ("any motion", "all lights off") rather than an implicit one.
- **The layered config resolver** resolves a setting by precedence and returns
  the layer it came from, so the log can name it. The order is fixed here even
  though not every layer is populated: built-in defaults, then house, then room,
  then the reserved profile layer, then a temporary override. Pinned by a test.
- **House modes** are a set the engine tracks, with `exclusive_group` from the
  mode schema making members mutually exclusive. A behaviour may gate on a mode.
- **Arbitration and priorities**: every command a behaviour proposes in a tick
  is collected, and commands to one entity reduce to one by priority, with an
  explicit user action outranking every behaviour and a deterministic tie-break
  (behaviour id). This is Phase 2's "two modules cannot fight over one light,"
  built as mechanism now.
- **Manual-override detection**: the engine reads the change context; an entity
  whose last writer was a user is overridden, and the engine suppresses its own
  commands to it until a reset condition. The reset conditions are named.
- **Rate limits**: a per-entity, per-window bound, applied after arbitration as
  a separate suppression stage, so a rate-limited command is distinguishable
  from a lost arbitration.
- **The decision log**: one record per evaluation with a fixed field set —
  virtual timestamp, actor, the inputs read, the rule matched, the commands
  proposed, the outcome (acted / lost arbitration / overridden / rate-limited /
  skipped: unbound slot / skipped: disabled / refused: unsafe), and the state
  delta. The field set is normative because the scenario runner and the agent
  both parse it.

### `house-adapter` — the port

The engine consumes the port; the fake implements it now and the Home Assistant
adapter implements it in Phase 4. Requirements:

- **The operation set**: read entity state and attributes, write (actuate),
  enumerate entities, add and remove an entity, set availability, inject a
  fault, restart the house, and snapshot the adapter's state. The port is
  minimal by construction: it exposes what the engine needs and nothing that
  would let a behaviour reach around the engine.
- **Change context**: every state change carries whether it came from a user,
  from the engine, from the world (a sensor tripping), or from a fault. This is
  the single field manual-override
  detection depends on, and it is why it is a port requirement rather than a
  fake detail — the real adapter must supply the same signal from Home
  Assistant's context object.
- **Purity**: the port and its types import no `homeassistant` and carry no Home
  Assistant type, so an engine that depends on the port stays importable with no
  HA present. The check is the Phase 0 purity scan, now over real code.
- **The contract**: a contract suite binds every implementation to the port. It
  runs against the fake in this phase; `spec.txt` gives it its second subject in
  Phase 4.
- **Restart semantics**: a restart is an operation that returns the house to a
  defined startup condition. What a device does on restart (unavailable is not
  off) is the adapter's, not the engine's.

### `simulation` — the deterministic substrate

Where the phase's no-HA, no-network, reproducible promise is kept.

- **`FakeHouseAdapter`** implements every port operation over a mock entity
  registry: add and remove entities at will, set state, make a manual user
  action, mark a device unavailable, inject a fault, restart.
- **The virtual clock**: time advances only through `advance_time`; nothing in
  `engine/` or `sim/` reads the wall clock. This is what makes "the light goes
  off after ten minutes" a statement about a number rather than about sleeping.
- **Seeded randomness**: one stream, seeded per run; a scenario that fixes the
  seed replays exactly.
- **No network**: neither package imports a networking module, and a scenario
  run opens no socket. Enforced by a check, not by discipline.
- **Snapshot and restore**: the snapshot captures the entities and their
  attributes and availability, the bindings, the modes, the enable flags, the
  override records, the rate-limit windows, the clock position and the random
  stream position. A restored house continues to decide identically, which is a
  property the runner asserts.
- **Fixture houses**: minimal (a room or two, a handful of devices), messy
  (one slot bound in two rooms, devices reporting unavailable, a
  missing lux sensor), large (150+ devices, to prove the engine is not quadratic
  and the log stays legible), and no-lux (the sun fallback on its own). Fixtures
  are built through the adapter, so a fixture cannot encode a state the adapter
  could not produce.

### `first-behaviours` — the first policy

The three behaviours `spec.txt` names, each derived from a corpus row and each
an independent, individually disableable unit.

- **Motion lighting**: on motion, when the space is dark; off after a quiet
  timeout. "Dark" is a lux reading when the lux slot is bound and the computed
  sun position when it is not. Implements the `lighting.motion_light_on` and
  `lighting.motion_light_off` concepts, with the sun fallback carrying
  `lighting.solar_sun_light`'s idea.
- **Override**: a manual change to a room's lighting suppresses the behaviour
  until the reset condition, which is the engine's override mechanism exercised
  by the behaviour that most needs it (`lighting.room_light_manual_on` and the
  `lighting.room_light_dim` family).
- **Away shutdown**: when the house empties, interior lighting goes off, gated
  on the away mode (`lighting.away_shutdown` under `modes.house_away` and
  `presence.house_emptied`).
- Each behaviour is **OFF by default** and can be turned off independently; the
  mechanism is `engine-core`'s enable flag and the rule is
  `product-invariants`'.

### `scenario-runner` — running the engine

- **The YAML DSL**: a scenario is `given` a house (a fixture or an inline one),
  `when` a sequence of steps, `then` assertions. The steps are the operations the
  control surface exposes — the ten registry operations `advance_time`,
  `set_state`, `user_action`, `inject_fault`, `snapshot`, `restore`,
  `get_decision_log`, `install_pack`, `export_config`, `import_config`, plus the
  house-control verbs `add_entity`, `remove_entity`, `set_availability` and
  `restart` that build and provoke the house — so a scenario and an agent's
  session are written in one vocabulary and cannot drift.
- **Assertions on state and on the log**: `then` may assert a device's state or
  a decision-log record, which is what lets a scenario distinguish "the light is
  off" from "the light is off because the timeout behaviour turned it off."
- **JSON failure output**: naming the failed step, the expected and actual
  values, the seed, and the relevant log slice, so a CI failure is a
  reproducible input rather than a paragraph.
- **Hypothesis invariants** over the running engine: determinism under replay; a
  restore-and-continue yielding identical decisions; no command that unlocks a
  lock or opens a garage from a non-user origin; no action from a behaviour with
  an unbound required slot; advancing time by zero changes nothing; and two
  behaviours commanding one entity producing exactly one resolved command.
- **Mutation testing**: mutmut over `engine/`, with a stated survivor threshold;
  `sim/` is measured separately, because a surviving mutant in the simulator
  means a fixture does not provoke what the simulator can do, a different
  failure from a broken decision.

### `control-surface` — the agent's entry point

- **One library facade** and the ten operations, defined once in an operation
  registry.
- **CLI (Typer) and MCP server (official MCP Python SDK)** are thin adapters over
  the registry, so an operation added once appears on both surfaces, and neither
  surface can offer something the library cannot.
- **Scope stated per operation**: `install_pack` accepts a valid manifest and
  checks its slots; `export_config`/`import_config` round-trip the house
  configuration; `snapshot`/`restore` round-trip runtime state; the rest are the
  driver's controls.
- **The exit criterion is this capability's acceptance**: an agent, with no HA
  installed, builds a house, runs a scenario, reads the log and iterates — and
  CI proves it.

### `product-invariants` — the two rules

A capability of their own, following `architecture-invariants`' precedent: a
cross-cutting rule enforced in more than one place, owned by no single module.

- **Nothing is on unless enabled, and everything can be turned off.** A freshly
  built house runs no behaviour. Every behaviour and module carries an enable
  flag; disabling one leaves the others working. The rule is enforced by the
  engine's evaluation gate and asserted by scenarios.
- **Nothing ever auto-unlocks a door or auto-opens a garage.** No behaviour, no
  fault, no non-user action may emit a command that unlocks a `lock` or opens a
  `cover`. The veto fails closed, appears in the log as `refused: unsafe`, and is
  asserted by a Hypothesis invariant and a scenario.

### Why the seams fall there

Three distinctions do the work. **Port from implementation**: the engine must
not depend on the fake, or Phase 4 changes the engine; so the port is
`house-adapter` and the fake is `simulation`. **Mechanism from policy**: the
engine core is generic and the three behaviours are specific, and Phase 2 turns
policy into pack data — a boundary that has to exist before it is load-bearing.
**Substrate from subject**: the clock, randomness, no-network and snapshot rules
make the engine testable without being part of its decision semantics, and they
change for reasons a decision change would not. The scenario runner and the
control surface are both *clients* of the engine rather than parts of it, and
the product rules are the one cross-cutting concern, given their own home so no
spec author has to infer them from someone else's.

## Decisions

### D1 — The `HouseAdapter` port lives in the engine, not in `ha_adapter/`

The engine consumes the port, the fake implements it, and Phase 4's Home
Assistant adapter implements it. The port therefore sits in `engine/adapter.py`,
the engine's dependency graph has one outward edge (to the port, not to an
implementation), and `ha_adapter/` — whose name describes the HA implementation
and which will import `homeassistant` in Phase 4 — is the *second* implementation
of the port rather than its home.

Alternative considered: define the interface in `ha_adapter/`. Rejected — it
would put the engine's own contract inside a package whose reason to exist is to
depend on Home Assistant, so the engine would import a package that drags HA in
the moment Phase 4 lands, and the fake in `sim/` would import `ha_adapter` to
implement its port. The naming is close enough to mislead on first read, so it is
stated here: `ha_adapter/` stays empty in this phase, and that is the decision,
not an omission.

### D2 — The decision log is a first-class artifact and the test oracle

Every evaluation appends a structured record, and scenarios assert on records as
well as on device state. The record's field set is normative (D1 in the
decomposition). The reason is not observability as a feature — that is Phase 8's
"why did this happen" — but correctness *now*: a state-only assertion passes for
the wrong reason. "The light is off" is true whether the timeout behaviour ran
or the light was never turned on, and a suite that cannot tell the two apart
cannot measure mutation, cannot explain a Hypothesis counterexample, and cannot
give an agent anything to iterate against. The log is what makes the exit
criterion's "read the log" a check rather than a hope.

Alternative considered: assert on final device state only, and log for humans.
Rejected — the oracle would be blind to the difference between *acted* and
*declined*, which is exactly the difference the phase's override, rate-limit and
arbitration requirements are about.

### D3 — Determinism is a substrate property, enforced, not best-effort

The virtual clock, the single seeded random stream and the no-network rule are
requirements of `simulation`, and each is enforced by a check. Determinism is
what makes Hypothesis counterexamples reproducible, a snapshot replay identical,
and a surviving mutant a statement about a missing test rather than about a
flaky run.

Alternative considered: run on the real clock and freeze time in tests with a
library. Rejected — a freezer in the test does not stop the engine holding a
wall-clock dependency that makes one code path untestable by construction, and
the exit criterion wants an agent to advance time explicitly rather than wait.

### D4 — Slot binding is optional and list-capable, and state reads carry a reduction

A slot resolves to a list of entities even though the frozen house schema binds
one `entity_id` per slot in a room: a house-scoped slot collects that slot's
binding from every room, so the corpus's `light_group` is inherently plural (one
room, several lamps; a house scope, several rooms) and the messy fixture binds
the same slot in two rooms. A required slot with no binding skips the behaviour;
an optional slot with no binding degrades it. A multi-entity slot read needs a
declared reduction — "any motion" is not "all motion" — so the reduction is part
of the slot's meaning and is named, not inferred.

Alternative considered: one entity per slot, with groups expressed as a Home
Assistant group entity. Rejected — it pushes a modelling decision into the
house's entity list, makes the fake unable to represent a partially-failed group
without inventing a group entity, and loses the per-entity detail the decision
log needs to explain which member of a group moved.

### D5 — The config resolver's layer order is fixed now, not in Phase 3

The resolver resolves low-to-high as built-in defaults, house, room, reserved
profile layer, temporary override, and returns the layer that decided a value.
Phase 1 populates the first two, the room layer and the override; the profile
layer is present and empty.

Alternative considered: implement the two layers Phase 1 needs and add the rest
when Phase 3 arrives. Rejected — precedence is the one thing every later layer's
meaning depends on, and a refactor that inserts a layer is free to reorder the
others silently. Pinning the order now costs an empty layer and buys a
guarantee: a test fails if the order changes, so Phase 3 inserts the profile
layer *between room and override* as `spec.txt` states and cannot move anything
else.

### D6 — Arbitration reduces commands; override and rate limiting are separate stages

Behaviour commands for one entity in one tick are collected and reduced to one by
priority, with a user action outranking every behaviour and a deterministic
tie-break. Manual override and rate limiting are applied as their own stages, not
folded into priority, because each produces a *different* log outcome: a command
can lose arbitration to a higher-priority behaviour, be suppressed because the
user last touched the entity, or be dropped by the rate limit — three reasons an
agent needs to tell apart.

Alternative considered: last-writer-wins by evaluation order, with no explicit
priority. Rejected — the outcome would depend on the order behaviours happen to
be scheduled in, which is neither explainable nor stable, and Phase 2's exit
criterion ("two modules cannot fight over one light") needs a rule that names a
winner and a reason.

### D7 — The engine binds to the frozen schemas and the corpus, not to a Phase 1 vocabulary

Slot names, room types, the house scope and the modes come from
`catalog/slots.yaml`, `catalog/room_types.yaml` and `schemas/*`, which Phase 0
froze. The engine reads them; it does not restate them.

Alternative considered: a smaller, Phase-1-local slot list to keep the engine
simple. Rejected — it would be the second definition of a concept the schemas
exist to define once, exactly the drift the conformance check in
`configuration-schemas` guards against, and the corpus's `required_slots` values
are drawn from the frozen vocabulary, so a local list would not match them.

### D8 — The first behaviours are policy over the core, specified by the corpus row

Motion lighting, override and away shutdown are written in `engine/behaviours/`
against the core's binding, arbitration, override and log facilities. Each is
derived from the named corpus row, and each is an independently enableable unit.
The lux-then-sun fallback is the corpus's two ideas — a lux-driven dim and a
sun-following schedule — meeting through the engine's optional-slot mechanism.

Alternative considered: implement the three behaviours inside the engine core.
Rejected — it conflates mechanism with policy at the exact boundary Phase 2
formalises, and it makes the "every behaviour can be turned off" rule require
editing the core rather than flipping a flag.

### D9 — The two product rules are enforced at the gate, not by convention

OFF-by-default is an evaluation gate: the engine runs only enabled behaviours,
and enablement is per behaviour and per module. The safety veto is an action
gate: the engine refuses to emit an unlock or an open-cover command from any
non-user origin, fails closed, and records `refused: unsafe`. Both live in
`engine-core`'s evaluation path and are also stated as product rules in
`product-invariants`, because the rule must be visible to the authors of every
other capability, including the pack author of Phase 2.

Alternative considered: enforce the veto in the adapter. Rejected — an adapter
that refuses a command has already let a behaviour *propose* it, so the proposal
is invisible in the log and the compile-time-ish guarantee is lost; a behaviour
should be unable to propose an unlock at all.

### D10 — The scenario DSL's verbs are the control surface's operations

A scenario step is a control-surface call; the runner has no verbs of its own.
A `when` block is a list of `advance_time` / `set_state` / `user_action` /
`inject_fault` and the rest, and a `then` block asserts state or log records.

Alternative considered: a bespoke scenario DSL with its own vocabulary
(`turn_on`, `wait`, `expect`). Rejected — it would be a second vocabulary to keep
in step with the first, and the agent's loop runs through the control surface, so
a scenario that used a different vocabulary would test a path the agent never
takes.

### D11 — Fixture houses are built through the adapter, not baked as snapshots

The four fixtures are constructed by calling the fake adapter's operations. A
fixture therefore cannot hold a state the adapter cannot reach, and the messy
fixture's duplicates and unavailable devices are produced by the same operations
a scenario uses to provoke them.

Alternative considered: fixtures as committed JSON snapshots loaded directly into
the registry. Rejected — they would bypass the adapter, skip the port's change
context, and drift from what the adapter can actually produce; a fixture that
encodes an impossible state is a test that passes for a reason the system cannot
reproduce.

### D12 — The composition root is a new top-level package, and the engine stays a library

The library facade, the CLI and the MCP server live in a new top-level package
(a composition root) rather than in `engine/`, because the facade wires the
engine *and* the simulator together and the engine may not import `sim/`. The new
package depends on both; neither depends on it.

Alternative considered: put the facade in `engine/`. Rejected — it would make the
engine import the simulator, breaking the direction of the port and the purity
rule the phase exists to exercise. The `architecture-invariants` requirement says
the repository SHALL provide the named modules; adding the composition root
alongside them is permitted, and it is recorded here so the addition is a
decision rather than an accretion.

### D13 — Mutation testing is scoped to the engine, and Hypothesis to the engine's invariants

Mutmut runs over `engine/` with a stated survivor threshold; `sim/` is measured
separately because a survivor there means a fixture does not provoke the
simulator, a different finding. Hypothesis drives the invariants named in the
decomposition.

Alternative considered: run mutmut over the whole tree. Rejected — it slows the
gate and dilutes the signal, and a surviving mutant in the CLI or a fixture
builder says nothing about whether the decision logic is tested.

### D14 — The no-network and no-HA promises are checks, and CI runs the agent loop

The purity scan from Phase 0 is extended to the new packages, a check asserts
neither `engine/` nor `sim/` imports a networking module, and a scenario run is
executed under a guard that fails if a socket is opened. CI then runs the whole
agent loop — build a house, run a scenario, read the log — in a checkout where
Home Assistant is not installed.

Alternative considered: state the promises and rely on review. Rejected — the
phase's exit criterion *is* "with no HA installed", so the promise is the
acceptance test, and an assertion nothing runs is not an assertion.

## Risks / Trade-offs

- **The decision log becomes an unbounded memory sink.** A long scenario appends
  a record per evaluation. → The log is a bounded ring by default with the bound
  in the engine's config, snapshot excludes the log (state is restored, history
  is not), and `get_decision_log` reads a window.
- **Snapshot omits a piece of engine state and replay diverges.** → The snapshot
  contents are enumerated normatively (D3 in the decomposition) and a Hypothesis
  invariant asserts restore-then-replay is identical, so an omission is a failing
  property rather than a silent divergence.
- **The sun fallback needs a location and a time zone the fake must supply.** →
  Location and zone are fixture configuration, the sun position is computed from
  the virtual clock, and the no-lux fixture exercises it alone; the corpus
  supplies no location, so the fixture invents one and the spec records it as
  fixture data rather than engine state.
- **The arbitration tie-break is arbitrary and may surprise.** → Ties break by
  behaviour id, the rule is documented, and the same input always yields the same
  winner; the log names the winner and the losers, so a surprising tie is
  inspectable rather than mysterious.
- **"OFF by default" sits awkwardly beside Phase 0's promise that the defaults
  are the distilled behaviour of real houses.** → The two are reconciled and the
  reconciliation is the point: what the corpus distilled is *which* behaviours
  exist, their tuned parameters and their order of precedence; activation is
  opt-in because the product rule says so. The defaults are the content, not the
  consent.
- **Mutation testing thresholds are arbitrary numbers that invite gaming.** →
  The threshold is stated as a floor for this phase, the report names surviving
  mutants, and a survivor is a finding to triage rather than a number to tune;
  the threshold is a ratchet that rises, never a target that is rounded toward.
- **The control surface's `install_pack` is a Phase 1 half of a Phase 2 feature,
  and its scope could be mistaken for the whole.** → Stated in the Non-Goals and
  in the operation's requirement; the capability sandbox, banned services and
  conflict resolution are named as Phase 2's.
- **A new top-level package is an addition to a layout Phase 0 froze.** →
  Recorded as D12: the invariant requires the named modules to exist, not that
  nothing else may; the composition root is named here so the addition is
  reviewed as a decision.
- **The engine may prove too coupled to the corpus's slot vocabulary.** → The
  resolver, the binding and the log all take slot names as strings resolved
  against `slots.yaml`, so a vocabulary version bump is a data change; the
  conformance check already forbids a second shape.

## Migration Plan

Greenfield — nothing migrates. The work order is forced by what each piece
needs to exist, and one edge of it is not the obvious one: the **port and the
fake must land before the engine**, and the **decision log must land before the
behaviours**, because every behaviour's test asserts on the log and every engine
test drives the fake. The order:

declared dependencies and the composition-root skeleton → the `HouseAdapter`
port → the virtual clock, seeded randomness and `FakeHouseAdapter` → snapshot and
restore → the decision log → slot binding and the config resolver → modes →
arbitration, override and rate limits → the first behaviours → the fixture houses
→ the scenario runner's DSL and state assertions → log assertions and the JSON
failure output → Hypothesis invariants → the control surface's facade, then CLI
and MCP over the registry → `install_pack`, `export_config` and `import_config`
at their stated scope → the no-network and no-HA checks → mutation testing and
its threshold → the two product-rule gates → the CI agent-loop acceptance.

The scenario runner's log assertions land after the log, not after the DSL: a
runner that can only assert state cannot test the behaviours it is written for,
which is the phase's own reason for the log being the oracle. `ha_adapter/`,
`custom_components/`, `panel/` and `packs/` are untouched.

## Open Questions

None that a spec author must resolve before writing. The unknowns are content
and measurement, not structure: the concrete tuning of the three behaviours
(the quiet timeout, the lux threshold, each behaviour's arbitrated priority), the
mutation survivor threshold, and the exact fixture device lists are settled by
running the scenarios and the corpus, not by the specs. One hand-off is worth
naming: the phase-0 package's delta specs are not yet archived into
`openspec/specs/`, so applying Phase 1's deltas presupposes that archiving step —
which the coordinator owns and which this package records rather than assumes.
