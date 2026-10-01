# Tasks

Two things about the order are not the obvious ones, and both are load-bearing.
The **port and the fake house land before the engine**, because every engine test
drives the fake through the port and the engine must be written against the port
rather than against the fake. And the **decision log lands before the first
behaviours**, because every behaviour's test asserts on the log — a runner that
can only assert device state cannot tell "acted" from "never ran", which is the
phase's own reason for the log being the oracle. The scenario runner's log
assertions therefore follow the log, not the DSL.

The capability names below are the seven in `design.md`'s decomposition; where a
task belongs to a capability the capability is named, so the task list and the
spec set stay aligned.

## 1. Surface, dependencies and the composition root

- [ ] 1.0 Declare the phase's dependencies in the root `pyproject.toml`: the MCP
  Python SDK as a runtime dependency, and Hypothesis and mutmut as dev
  dependencies. This is a change to the declared dependency list the
  engine-purity invariant reads, so it is the first task, not a convenience.
  Verify `oh-catalog validate` still passes (the list is extended, not
  rewritten) and the MCP SDK version is pinned against the current release as
  `spec.txt` instructs.
- [ ] 1.1 Create the composition root package (a new top-level directory beside
  `engine/`, `sim/`, `ha_adapter/`) with `py.typed` and an empty `__init__`.
  `design.md` D12 records why the facade lives here and not in `engine/`. Verify
  the layout check in `architecture-invariants` still passes with the extra
  package present, and that `engine/` gains no import of it.
- [ ] 1.2 Extend the purity scan to the composition root: the new package may
  import `engine/` and `sim/`, and neither may import it. Verify with a failing
  fixture — an `engine/` file importing the composition root — which SHALL fail
  naming the file, and a passing fixture where the composition root imports
  `engine/`.

## 2. The `HouseAdapter` port (`house-adapter`)

- [ ] 2.0 Define the port in `engine/adapter.py` as a `Protocol` with: read state
  and attributes, write (actuate), enumerate entities, add and remove an entity,
  set availability, inject a fault, restart, and snapshot the adapter's state.
  `design.md` D1 records why the port is here rather than in `ha_adapter/`.
  Verify the port imports no `homeassistant` and carries no HA type, and that an
  `engine/` file importing the port adds no undeclared dependency.
- [ ] 2.1 Define the **change context** every state change carries — user,
  engine, world, or fault — as part of the port, since manual-override detection
  reads it. Verify a write with no context is rejected by the port's
  construction, and a user-origin write, an engine-origin write and a world-origin
  write are distinguishable.
- [ ] 2.2 Write the contract suite that binds an implementation to the port, and
  run it against the fake in 3.x. The suite has one subject this phase and gains
  its second in Phase 4. Verify the suite is a pure function of the port (it
  names no fake-specific method) so Phase 4 can point it at the real adapter
  unchanged.

## 3. The deterministic substrate (`simulation`)

- [ ] 3.0 Implement the virtual clock: time advances only through
  `advance_time`, and nothing in `engine/` or `sim/` reads the wall clock.
  Verify with a test that advances time and asserts the engine's observed clock
  moved, and a scan that no module in either package imports `time.time`,
  `datetime.now` or `monotonic` outside the clock module.
- [ ] 3.1 Implement the seeded random stream as one stream owned by the
  simulator, restored with the snapshot. Verify two runs at the same seed produce
  identical outputs and two runs at different seeds do not.
- [ ] 3.2 Implement `FakeHouseAdapter` over a mock entity registry: add and
  remove entities, set state, make a manual user action, mark a device
  unavailable, inject a fault, restart. Every change carries its context (2.1).
  Verify each operation with the contract suite (2.2), and a restart returning
  the house to its defined startup condition.
- [ ] 3.3 Enforce **no network**: only stdlib-free-of-sockets imports in
  `engine/` and `sim/`, and a scenario run under a guard that fails if a socket
  opens. Verify with a fixture that opens a socket, which SHALL fail the check,
  and a passing committed tree.
- [ ] 3.4 Implement snapshot and restore over the enumerated contents: entities,
  attributes, availability, bindings, modes, enable flags, override records,
  rate-limit windows, clock position and random stream position. The log is not
  restored (state is history-independent; history is not). Verify with a test
  that restore-then-replay produces identical decisions for the same seed.

## 4. The decision log (`engine-core`)

- [ ] 4.0 Implement the decision record with the normative field set: virtual
  timestamp, actor, inputs read, rule matched, commands proposed, outcome (acted
  / declined / lost arbitration / overridden / rate-limited / skipped: unbound
  slot / skipped: disabled / refused: unsafe), and state delta. Verify a test asserts
  every outcome value is producible by some evaluation, so an outcome nothing
  emits is caught.
- [ ] 4.1 Make the log bounded by default, with the bound in the engine's
  config, and expose a read window. Verify a long run does not grow the log past
  the bound and the read window returns the most recent records in order.

## 5. Slot binding and the config resolver (`engine-core`)

- [ ] 5.0 Implement slot binding: resolve a slot name to zero or more entities
  from a house's `bindings`, against `schemas/house/` and the slot vocabulary in
  `catalog/slots.yaml`. A required slot with no binding skips the behaviour and
  logs the reason; an optional slot with no binding degrades it. Verify with a
  fixture house with an unbound required slot (behaviour skipped, reason logged)
  and one with an unbound optional slot (behaviour runs, degraded).
- [ ] 5.1 Implement the multi-entity state reduction ("any motion", "all off")
  as a declared property of the slot read, not an inference. Verify a two-entity
  slot reads differently under "any" and "all".
- [ ] 5.2 Implement the layered config resolver with the order fixed:
  built-in defaults < house < room < reserved profile < temporary override, and
  return the deciding layer. Verify with a table-driven test per layer, a test
  that the empty profile layer changes no resolution, and a test that reordering
  the layer list fails naming the order.

## 6. Modes, arbitration, override and rate limits (`engine-core`)

- [ ] 6.0 Implement house modes with `exclusive_group` from `schemas/mode/`, so
  setting one mode clears its group siblings. Verify with a test that setting a
  second mode in a group clears the first and a mode in another group is
  untouched.
- [ ] 6.1 Implement arbitration: collect the tick's proposed commands, reduce
  commands to one entity by priority with a user action outranking every
  behaviour and a deterministic tie-break by behaviour id. Verify a test where
  two behaviours command one light yields one command and a log naming the
  winner and the loser, and a test that the winner is identical across runs of
  the same input.
- [ ] 6.2 Implement manual-override detection: an entity whose last writer was a
  user is overridden and the engine suppresses its commands to it until a named
  reset condition. Verify each reset condition with its own test, and that a
  suppression appears in the log as `overridden` rather than as inaction.
- [ ] 6.3 Implement rate limits as a post-arbitration suppression stage, per
  entity per window. Verify a burst is bounded and the suppression appears as
  `rate-limited`, distinct from a lost arbitration.

## 7. The first behaviours (`first-behaviours`)

- [ ] 7.0 Implement motion lighting: on motion when dark, off after the quiet
  timeout. "Dark" is a lux reading when the lux slot is bound and the computed
  sun position when it is not; the sun position is computed from the virtual
  clock and fixture location data. Derived from `lighting.motion_light_on`,
  `lighting.motion_light_off` and `lighting.solar_sun_light`. Verify both
  branches, and that the no-lux fixture (8.x) takes the sun branch and the lux
  branch is never consulted.
- [ ] 7.1 Implement override of a room's lighting by a manual change, using
  6.2's mechanism. Derived from `lighting.room_light_manual_on`. Verify a user
  action suppresses the behaviour until the reset condition and the log says
  `overridden`.
- [ ] 7.2 Implement away shutdown: when the house empties, interior lighting
  goes off, gated on the away mode. Derived from `lighting.away_shutdown`,
  `modes.house_away` and `presence.house_emptied`. Verify the shutdown fires on
  the house emptying and not while any person is home, and the log names the
  mode.
- [ ] 7.3 Make each behaviour independently enableable and OFF by default: a
  freshly built house runs none of them, and disabling one leaves the others
  working. Verify with a test per behaviour that a fresh house runs nothing, and
  one that disables a single behaviour and asserts the others still act.

## 8. Fixture houses (`simulation`)

- [ ] 8.0 Build the four fixtures through the adapter: minimal; messy (the same
  slot bound in two rooms, so a house-scoped slot resolves to two entities,
  devices reporting unavailable, a missing lux
  sensor); large (150+ devices); and no-lux. Verify each is built through the
  adapter, the large fixture has at least 150 entities, the messy one binds one
  slot in two rooms and contains at least one unavailable device, and the
  no-lux one binds no lux slot anywhere.

## 9. The scenario runner (`scenario-runner`)

- [ ] 9.0 Implement the YAML DSL: `given` a house (a fixture or an inline one),
  `when` a list of steps, `then` assertions. The step verbs are the control
  surface's operations and nothing else. Verify a scenario using an unknown verb
  fails naming it, and every operation in 10.x has a corresponding step verb.
- [ ] 9.1 Implement state assertions in `then`. `design.md` D2 records why state
  assertions alone are insufficient; 9.2 supplies the other half. Verify a
  scenario asserting a device state passes and fails as expected.
- [ ] 9.2 Implement decision-log assertions in `then`, so a scenario can assert
  an outcome and a rule by name. Verify a scenario that asserts a light is off
  *because the timeout behaviour acted* passes when it did and fails when the
  light was never on.
- [ ] 9.3 Implement JSON failure output: failed step, expected and actual
  values, seed, and the relevant log slice. Verify a deliberately failing
  scenario emits valid JSON naming all four, and the same seed reproduces the
  same failure.
- [ ] 9.4 Implement the Hypothesis invariants over the running engine:
  deterministic replay; restore-then-continue identical; no non-user unlock or
  open-cover command; no action from a behaviour with an unbound required slot;
  advancing time by zero changes nothing; two behaviours on one entity produce
  one command. Verify each invariant has a property test and a falsifying input
  that makes it fail.
- [ ] 9.5 Wire mutation testing with mutmut over `engine/`, with a stated
  survivor threshold and a report naming survivors; measure `sim/` separately.
  Verify the gate fails when the threshold is exceeded and the report names a
  surviving mutant's location.

## 10. The control surface (`control-surface`)

- [ ] 10.0 Implement the library facade as the composition root wiring `engine/`
  and `sim/`: build a house, drive it, read the log, snapshot and restore, with
  no CLI or MCP present. Verify the facade is importable and drivable in a test
  that never touches the CLI or MCP.
- [ ] 10.1 Define the ten operations once in a registry —
  `advance_time`, `set_state`, `user_action`, `inject_fault`, `snapshot`,
  `restore`, `get_decision_log`, `install_pack`, `export_config`, `import_config`
  — and drive the facade from it. Verify every operation in the registry is
  callable through the facade, and no operation exists outside the registry.
- [ ] 10.2 Implement the CLI (Typer) as a thin adapter over the registry.
  Verify every registry operation has a subcommand and `--help` runs for each,
  with no CLI-only behaviour.
- [ ] 10.3 Implement the MCP server (official MCP Python SDK) as a thin adapter
  over the same registry. Verify every registry operation is exposed as a tool,
  driven by an in-process client in the test, with no MCP-only behaviour.
- [ ] 10.4 Implement `install_pack` at its Phase 1 scope: accept a manifest that
  validates against the current `pack-manifest` schema and check its
  `requires_slots` against the house; the sandbox, banned services and conflict
  resolution are Phase 2's (`design.md` Non-Goals). Verify a manifest whose
  required slot the house cannot supply fails naming the slot, and installing
  the example pack succeeds.
- [ ] 10.5 Implement `export_config` and `import_config` over the **house
  configuration** (house and bindings, validated against the frozen house
  schema). Verify a house round-trips to an identical configuration, and that
  this is not the Phase 3 export document (which carries registry ids for
  re-link).
- [ ] 10.6 Expose the scenario runner on both surfaces as a **client entry
  point** rather than a registry operation: the CLI's `scenario run` command and
  the MCP `run_scenario` tool, both thin adapters over
  `sim/scenario/runner.py:run_scenario`. Verify one scenario run through each at
  the same seed produces the same result, and that neither entry point is counted
  as a registry operation.

## 11. The product rules (`product-invariants`)

- [ ] 11.0 Implement the OFF-by-default gate: the engine evaluates only enabled
  behaviours, and every behaviour carries an enable flag (the module registry is
  Phase 2's). Verify a
  fresh house runs nothing and a disabled behaviour is skipped with reason
  `skipped: disabled`.
- [ ] 11.1 Implement the safety veto: the engine refuses to emit any unlock or
  open-cover command from a non-user origin, fails closed, and records
  `refused: unsafe`. Verify with a fixture behaviour that tries to unlock, which
  SHALL be refused and logged, and a direct `user_action` unlock, which SHALL be
  allowed. The matching Hypothesis invariant is 9.4's.

## 12. Acceptance

- [ ] 12.0 Extend the CI workflow to run the engine and scenario suites, the
  no-network and no-HA checks, the purity scan over the new packages, and the
  mutation gate — all of which read only this repository and need no Home
  Assistant. Verify CI passes on the committed tree and fails when the no-HA
  guard is removed.
- [ ] 12.1 Build the requirement→check mapping from the seven capability specs
  by parsing `### Requirement:` rather than trusting a count, mapping each to a
  named test and a violating fixture, in the manner of `phase-0-foundations`
  task 8.1. Verify by running the gate and by deleting one mapped test, which
  SHALL fail the gate.
- [ ] 12.2 Confirm `spec.txt`'s Phase 1 exit criterion: an AI agent can build a
  house, run scenarios, read the log and iterate with no HA installed. Verify by
  running the whole loop in a checkout where Home Assistant is not installed —
  build a house through the facade, run a scenario, read a decision record, and
  iterate — plus `openspec validate phase-1-engine --strict`.
