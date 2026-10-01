# Spec Delta — scenario-runner

## Purpose

The test infrastructure that drives the engine the way an agent does, and reads
back what it decided. It owns one package, `sim/scenario/`, and inside it the
YAML DSL a scenario is written in, the two halves of a `then` block (state and
decision-log assertions), the runner that executes a scenario against the
control surface, the JSON failure document a CI job consumes, the Hypothesis
invariants over the running engine, and the mutation-testing gate that measures
whether the suite would notice a broken decision. It is a **client of the
engine**, not a part of it (`design.md`'s decomposition puts it beside
`control-surface`, and both are clients): a scenario step is a control-surface
call and nothing else, so a scenario and an agent's session speak one vocabulary
and cannot drift (`design.md` D10).

This capability exists because of the phase's most unusual property, and the best
way to read every requirement below is as a consequence of it: **the decision log
doubles as the test oracle** (`design.md` D2; the log itself is
`engine-core`'s). "The light is off" is true whether the timeout behaviour turned
it off or the light was never switched on, and a suite that cannot tell the two
apart cannot measure mutation, cannot explain a Hypothesis counterexample, and
cannot give an agent anything to iterate against. So the runner asserts on the
*record* an evaluation left — its `rule`, its `outcome`, its inputs — and not only
on the device's final state, and that is why the log's field set had to be
normative before this capability could be written. The exit criterion's "read the
log" is this capability's acceptance test: an agent builds a house, runs a
scenario, reads a record and iterates, with no Home Assistant installed.

Three properties run through the requirements. **The runner reproduces**: a
scenario names its seed and its starting clock, and a failure carries both, so a
red run is an input rather than a paragraph — determinism is the substrate's
(`simulation`, `design.md` D3) and the runner is where it is cashed. **The runner
cannot be a second engine**: it never re-derives a decision from the rules to
predict it, because a prediction that disagrees with the engine is a bug in the
predictor rather than a detection of one; it observes what the engine wrote.
**The runner is where a behaviour that could only be verified by reading device
state is treated as unspecified** (`first-behaviours`), because the
acted-versus-never-ran distinction is invisible in state and this is the only
capability positioned to see it.

What this capability does *not* own: the decision record and its outcome
vocabulary (`engine-core`), the operations a step names (`control-surface`), the
fixtures a `given` block selects (`simulation`), and the mutation *threshold* as a
number (`design.md` Open Questions settles it by measurement, not by this spec).
The Phase 1 seeds in `catalog/edge_cases.yaml` — a device that goes unavailable
and returns, a restart mid-absence, a flickering door contact, a sensor that stops
reporting — are the scenarios this DSL must be able to *express*; each names the
simulator seed it becomes, and the runner's step vocabulary plus its two
assertion kinds are what make each expressible.

## ADDED Requirements

### Requirement: A scenario is a validated YAML document with three blocks
`sim/scenario/model.py` SHALL define the scenario document types — `Scenario`,
`Given`, `Step`, `Then`, and `Expectation` — and `sim/scenario/loader.py`'s
`load_scenario(path)` SHALL load a scenario from a YAML file using a safe loader
and validate it against a versioned schema at `schemas/scenario/1.0.0.json`,
carrying an `$id` and a `schema_version` in the manner
`configuration-schemas` fixes for every schema. A scenario SHALL have exactly
three top-level blocks: `given` (the house to build and the run's fixed inputs),
`when` (an ordered list of steps), and `then` (an ordered list of assertions). A
block outside those three, a missing required block, or a step key outside the
step vocabulary SHALL fail loading and name the offending key.

The document is validated rather than parsed defensively because a scenario is
data an agent writes, and the failure a schema prevents is a scenario that
"passes" because a misspelled key was silently ignored — a `then: []` that
asserts nothing is a green run that proves nothing. Fixing the schema at
`schemas/scenario/1.0.0.json` rather than accepting any YAML is the same choice
`configuration-schemas` made for the house and pack documents: one shape, one
version, and a second one is a deliberate act.

#### Scenario: A scenario carries an unknown block
- **WHEN** a scenario's top level names a key other than `given`, `when` or
  `then`
- **THEN** loading fails and names the key

#### Scenario: A required block is absent
- **WHEN** a scenario has no `when` block
- **THEN** loading fails and names the missing block

#### Scenario: A `then` block asserts nothing
- **WHEN** a scenario's `then` block is empty and the schema requires at least
  one assertion
- **THEN** loading fails, so an empty assertion set cannot be mistaken for a
  passing run

### Requirement: Scenario step verbs are the control surface's verbs, and no others
The verb of a `Step` SHALL be drawn from the control surface's verbs and nothing
else. Those verbs are the ten operations of the `control-surface` operation
registry — `advance_time`, `set_state`, `user_action`, `inject_fault`,
`snapshot`, `restore`, `get_decision_log`, `install_pack`, `export_config`,
`import_config` — together with the **house-control** verbs `add_entity`,
`remove_entity`, `set_availability` and `restart`, which build and provoke the
house. The house-control verbs are **not** registry operations: they are the
control face of the `HouseAdapter` port (`house-adapter`) that the composition
root exposes as a facility, and `spec.txt` fixes the registry at its ten
decision-driving operations, so a restart mid-absence is expressible without an
eleventh registry operation. `sim/scenario/dsl.py` SHALL compile a step to a
control-surface call and SHALL have **no verb of its own**: a `when` block is a
list of control-surface calls. Building a house is the `given` block's job and
the session's entry, and running a scenario is the runner's own composed entry
point (`run_scenario`) — neither is a step verb. The set SHALL be checkable
against the control surface, so a control-surface verb without a matching step
fails, and a step whose verb names no control-surface member fails.

A bespoke DSL vocabulary — `turn_on`, `wait`, `expect` — was rejected (`design.md`
D10): it would be a second vocabulary to keep in step with the first, and the
agent's loop runs through the control surface, so a scenario written in a
different vocabulary would test a path the agent never takes. Deriving the verbs
from the control surface is what makes "a scenario is what an agent did" a
checked property rather than a resemblance.

#### Scenario: A scenario uses an unknown verb
- **WHEN** a `when` step names a verb such as `turn_on` that the registry does
  not carry
- **THEN** loading fails and names the verb

#### Scenario: An operation has no step verb
- **WHEN** an operation exists in the registry and no step verb resolves to it
- **THEN** the DSL-to-registry conformance check fails and names the operation,
  so the two vocabularies cannot drift

#### Scenario: A step reaches an engine interior
- **WHEN** a step's compilation calls an `engine/` function directly instead of
  the control surface
- **THEN** the boundary check fails and names the step and the call

### Requirement: The `given` block builds a house through the adapter and fixes the run's inputs
The `given` block SHALL name either a fixture from `simulation`'s
`build_fixture` registry (`minimal`, `messy`, `large`, `no_lux`) or an **inline
house** that validates against `schemas/house/1.0.0.json`, and the house SHALL be
built through the fake adapter's own operations, never loaded as a baked snapshot
(`design.md` D11 — a fixture or an inline house cannot hold a state the adapter
could not produce). The `given` block SHALL additionally fix the run's **seed**,
its **starting virtual instant**, and any **enable flags** set before the first
step, because those are the inputs a replay needs and a scenario that omitted
them would be reproducible only by luck. An unknown fixture name, or an inline
house failing its schema, SHALL fail the run before the first step executes.

#### Scenario: An unknown fixture is named
- **WHEN** a `given` block names a fixture that `build_fixture` does not provide
- **THEN** the run fails before any step executes and names the fixture

#### Scenario: An inline house fails the house schema
- **WHEN** a `given` block carries an inline house whose bindings name a slot
  outside `catalog/slots.yaml`, or whose shape fails `schemas/house/1.0.0.json`
- **THEN** the run fails and names the house and the fault

#### Scenario: The run's fixed inputs are recorded
- **WHEN** a scenario is loaded
- **THEN** its seed, its starting instant and its initial enable flags are part
  of the `Scenario` value and appear on the `RunResult`, so a failure can be
  replayed from the document

### Requirement: State assertions read the house, and availability is asserted apart from state
`sim/scenario/assertions.py` SHALL define `StateExpectation`, naming an
`entity_id`, the **field** it asserts (the entity's `state`, a named attribute,
or its `availability`), and the expected value, and SHALL evaluate it against a
read of the house taken at the assertion's point in the step sequence. A state
assertion SHALL NOT re-derive what the value *should* be from the engine's rules;
it compares the read to the expectation and reports a mismatch. Because
`house-adapter` keeps availability a separate field, an assertion on availability
SHALL be expressible on its own — an entity that is `unavailable` SHALL be
assertable as such rather than only as "not off".

Reading rather than predicting is the whole discipline of this capability, and it
is what makes an assertion a statement about the system instead of about a second
implementation of it. Keeping availability assertable apart from state is what
lets a scenario for the corpus's "a plug goes unavailable and returns" seed assert
that the plug was never reported switched off, which is the fact the seed exists
to protect (`catalog/edge_cases.yaml`).

#### Scenario: A state assertion passes and fails as expected
- **WHEN** a `then` block asserts an entity's state equals the value the steps
  produced, and a second scenario asserts a different value
- **THEN** the first passes and the second fails naming the entity, the expected
  value and the actual one

#### Scenario: An unavailable entity is asserted as unavailable
- **WHEN** a scenario marks an entity unavailable and asserts its `availability`
  is `false`
- **THEN** the assertion passes without the entity's state being asserted as
  `off`, and an assertion that the entity's state is `off` fails

### Requirement: Decision-log assertions make the log the oracle
`sim/scenario/assertions.py` SHALL define `LogExpectation`, which asserts over
the records `engine-core` wrote, and SHALL be able to assert on any field of the
normative record — `actor`, `rule`, `outcome`, `inputs` (including a read's
`reduction` and a resolved setting's `layer`), `commands` and `state_delta` — both
positively (`must`) and negatively (`must_not`). A `LogExpectation` SHALL be able
to assert **because**: that a record exists matching a given `rule` and
`outcome`, which is the form that distinguishes an outcome the engine produced
from a state that happens to match. `LogExpectation` SHALL read the log the
engine appended and SHALL NOT re-evaluate a behaviour to predict whether it would
have acted.

This is `design.md` D2 stated as a test: an assertion on final device state alone
is blind to the difference between *acted* and *declined*, which is exactly the
difference the override, rate-limit and arbitration rules are about, and the
"because" form is what makes the exit criterion's "read the log" a check rather
than a hope. Without it a scenario cannot answer "why did nothing happen", which
is the question an agent iterating against the engine asks most.

#### Scenario: The oracle distinguishes acted from never-ran
- **WHEN** a scenario asserts a light is off *because the timeout behaved*, as a
  `LogExpectation` for `rule: lighting.motion_light_off` and `outcome: acted`
- **THEN** it passes when the behaviour turned the light off and fails when the
  light was never on, at which point no such record — or a `declined` one —
  exists, so the two cases are told apart

#### Scenario: An outcome is asserted by name
- **WHEN** a scenario asserts that a behaviour's command was suppressed with
  `outcome: overridden`
- **THEN** it passes only when a record carries that outcome, and fails when the
  behaviour instead recorded `lost arbitration`, so the two suppressions are
  distinguishable from a scenario

#### Scenario: The runner predicts instead of reading
- **WHEN** an assertion is computed by re-running a behaviour's rule to deduce
  the expected record rather than reading the log the engine wrote
- **THEN** the boundary check fails, because a prediction that disagrees with the
  engine is a second implementation and not an oracle

### Requirement: A run is deterministic and carries its own replay inputs
`sim/scenario/runner.py`'s `run_scenario(scenario, surface) -> RunResult` SHALL
execute a scenario's steps against the control surface in order, taking each
assertion's read of the house at its point in the sequence, and SHALL return a
`RunResult` carrying the seed, the starting instant, the final virtual instant,
the final **snapshot** (the `snapshot` operation's document, so a resumed run is
possible), and the decision-log window. Given the same scenario, the same seed
and the same starting instant, two runs SHALL produce identical device states,
identical decision records and identical `RunResult` values; a scenario whose
behaviour consumes randomness SHALL produce different `RunResult` values at two
different seeds, so the first property is not vacuous. The runner SHALL NOT read
the wall clock and SHALL advance time only through `advance_time`, because a
scenario that waited would not be a scenario an agent can iterate against.

#### Scenario: The same seed replays a run exactly
- **WHEN** one scenario is run twice at the same seed and starting instant
- **THEN** both runs' final states, decision records and `RunResult` documents are
  identical

#### Scenario: Different seeds diverge where randomness is consumed
- **WHEN** a scenario whose result depends on the random stream is run at two
  seeds
- **THEN** the two `RunResult` values differ, proving the equality above is a
  property and not a constant

#### Scenario: The runner waits on the wall clock
- **WHEN** any module under `sim/scenario/` reads a wall-clock function instead of
  advancing the virtual clock
- **THEN** the clock scan fails and names the file and the call

### Requirement: A failure is a JSON document a CI job can consume
`sim/scenario/report.py` SHALL define `FailureReport`, and a failed run SHALL
serialise to **one JSON object** carrying at least: the scenario's path, the
index and verb of the **failed step** (or the failed assertion), the
**expectation** that failed, the **expected** and **actual** values, the **seed**
and the starting instant, the virtual instant at the failure, and the **relevant
log slice** — the decision records from the last step up to the failure. The same
scenario at the same seed SHALL serialise the same failure document, so a red CI
job is a reproducible input rather than a paragraph to interpret. The document
SHALL be emitted by the composition root's CLI when asked and SHALL be the
machine-readable form of a non-zero exit, not prose a human must parse.

Naming the seed and the log slice together is what closes the loop with the two
requirements above: the seed replays the run and the log slice says which
evaluation the scenario disagreed with, so the failure points at a decision rather
than at a line of a scenario file.

#### Scenario: A deliberately failing scenario emits valid JSON
- **WHEN** a scenario is run that fails an assertion, and the runner is asked for
  JSON output
- **THEN** the output is a single JSON object naming the failed step, the
  expected and actual values, the seed and the log slice, and it parses as JSON

#### Scenario: The same seed reproduces the same failure
- **WHEN** the same failing scenario is run twice at the same seed
- **THEN** the two failure documents are identical

#### Scenario: A failure names no log slice
- **WHEN** a failed assertion is reported without the decision records up to the
  failure
- **THEN** the report check fails and names the omission, because without the
  slice the failure cannot be attributed to a decision

### Requirement: The runner is a client of the control surface, never a second engine
The runner SHALL reach the engine only through the control surface's library
facade, and no module under `sim/scenario/` SHALL import `engine/` or `sim/`
internals other than the control surface, the fixtures `simulation` provides and
the runner's own package. A step SHALL NOT actuate an adapter, call an engine
method, or mutate engine state except through an operation the control surface
exposes. A scenario's assertions SHALL read the house and the log, not the
engine's object graph.

The rule is the counterpart of `simulation`'s "the fake carries no private door".
A runner that could reach into the engine would test the path the agent does not
take, and the whole point of deriving the step verbs from the control surface
is that the scenario and the agent's session are the same code path. Keeping the
runner a client of the facade is what makes the scenario suite evidence about the
surface the agent actually uses.

#### Scenario: A runner module imports an engine interior
- **WHEN** a file under `sim/scenario/` imports an `engine/` module other than
  through the control surface's facade
- **THEN** the boundary check fails and names the file and the import

#### Scenario: A step mutates engine state directly
- **WHEN** a step sets an enable flag, a mode or a binding on the engine without
  going through a control-surface operation
- **THEN** the boundary check fails and names the step and the mutation

### Requirement: Invariants are properties over the running engine, each with a falsifying input
`sim/scenario/invariants.py` SHALL define the phase's **Hypothesis invariants** as
named properties over a running engine driven through the control surface, and
each SHALL be stated so that it is falsifiable — the suite SHALL carry, for each
invariant, an input that makes it fail, so an invariant that cannot fail is
caught rather than trusted. The set SHALL be:

- **`deterministic_replay`** — a run replayed at its seed and starting instant
  produces identical device states and identical decision records (`engine-core`'s
  determinism requirement, exercised end to end);
- **`restore_replay_identity`** — a snapshot taken mid-run, restored and
  continued produces the decisions the uninterrupted run produced (`simulation`'s
  restore-resume property, exercised through the runner);
- **`no_non_user_egress`** — no command that unlocks a `lock` or opens a `cover`
  is applied unless a `user`-origin change accounts for it (`engine-core`'s veto
  and `product-invariants`' second rule);
- **`no_unbound_required_action`** — no behaviour acts while a slot it requires is
  unbound, so an action with a `skipped: unbound slot` actor is not observable;
- **`zero_advance_is_noop`** — an `advance_time` of zero produces no tick, no
  command and no decision record;
- **`single_command_per_entity`** — two behaviours proposing commands to one
  entity in one tick yield exactly one applied command and a record naming the
  winner and the loser (`engine-core`'s arbitration requirement).

The invariants are properties over the *running engine* rather than over a unit,
because each is a claim about the whole stack agreeing — the engine, the fake and
the log — and a unit test of any one of them would assert the piece and miss the
seam. The falsifying input per invariant is required because a property test that
cannot fail is indistinguishable from one that is not run: the phase treats an
invariant as specified only when the suite demonstrates it rejecting a
counterexample, in the manner the phase's own mutation gate establishes for the
engine.

#### Scenario: An invariant is falsified by its named input
- **WHEN** each invariant's falsifying input is applied — a second generator
  seeded differently, a snapshot missing the random-stream position, a behaviour
  proposing a non-user unlock, an unbound required slot, a zero advance expected
  to tick, two behaviours on one entity
- **THEN** the corresponding property fails and names the invariant, so no
  invariant is present without a demonstrated counterexample

#### Scenario: A snapshot omits the stream position
- **WHEN** `restore_replay_identity` runs against a snapshot missing the random
  stream's position and a random-consuming behaviour runs after the restore
- **THEN** the property fails, catching the omission rather than tolerating it

#### Scenario: An invariant is removed from the set
- **WHEN** a named invariant from the set above has no corresponding property test
- **THEN** the requirement-to-check mapping fails and names the missing invariant

### Requirement: Mutation testing measures the engine against this suite's oracle
The mutation gate SHALL run mutmut over `engine/` and SHALL fail the build when
the surviving-mutant count exceeds a **stated survivor threshold**, and it SHALL
produce a report naming each surviving mutant's location. `sim/` SHALL be
measured **separately**, because a surviving mutant in the simulator means a
fixture or a scenario does not provoke what the simulator can do — a different
finding from a broken decision, and one that would be diluted by a single
tree-wide number. A mutant that removes a decision record, changes an `outcome`
value, or drops a command SHALL be **killed** by this suite, because a suite whose
oracle is the log must notice when the log lies; a survivor of that shape SHALL be
reported as a missing assertion rather than tolerated.

The scope and the split are `design.md` D13's: mutating the whole tree slows the
gate and dilutes the signal, and the log-as-oracle property is exactly what makes
an engine mutant killable by an assertion rather than only by a device state. The
threshold is a floor for this phase, settled by measurement (`design.md` Open
Questions), and the report names survivors so a survivor is triaged as a finding
rather than tuned into a target.

#### Scenario: The gate fails above the threshold
- **WHEN** mutmut over `engine/` leaves more survivors than the configured
  threshold
- **THEN** the gate fails and names the count and the threshold

#### Scenario: A survivor is named
- **WHEN** a mutant survives the suite
- **THEN** the report names the mutant's file and line, so the missing assertion
  can be written

#### Scenario: An oracle mutant is killed
- **WHEN** a mutant changes a decision record's `outcome`, drops a record, or
  drops a proposed command, and the runner's `LogExpectation` assertions run
  against it
- **THEN** at least one assertion fails and the mutant is killed, so the
  log-as-oracle property is measured rather than assumed

### Requirement: The runner expresses every Phase 1 edge-case seed
The runner's step vocabulary and its two assertion kinds SHALL be sufficient to
express every `phase_1` seed in `catalog/edge_cases.yaml` — a device going
unavailable and returning, a restart mid-absence, a state that flickers, a sensor
that stops reporting, a manual change while maintenance mode is on — by composing
the control surface's operations and house-control verbs and asserting on device
state and on the decision log. A seeded scenario SHALL be committable under a scenario corpus directory
(`scenarios/`), and the runner SHALL be able to run a directory of scenarios as
well as one file, so the seeds are executable rather than a reading list.

The seeds are Phase 0's answer to the corpus's solved edge cases and each names
the simulator seed it becomes; if the DSL cannot express one, the seed is
aspirational rather than runnable, which is the state the phase exists to leave
behind. Expressing a seed means provoking the failure and asserting the *record*
that shows the engine handled it — an unavailable device that returns is
asserted by the absence of a false switched-off record, not merely by a state.

#### Scenario: An unavailable-and-return seed is expressible
- **WHEN** a scenario marks a plug unavailable, advances time, marks it available
  again, and asserts no switched-off record was produced
- **THEN** the scenario loads and runs, and the assertion is a `LogExpectation`
  over the absence of a false record

#### Scenario: A restart-mid-absence seed is expressible
- **WHEN** a scenario sets away mode, advances time, restarts the house through a
  step, and asserts the mode and its timers survived
- **THEN** the scenario loads and runs, so the seed is executable

#### Scenario: A directory of scenarios is run
- **WHEN** the runner is pointed at the scenario corpus directory
- **THEN** every scenario in it is loaded and run, and a load failure in any one
  fails the run naming the file

### Requirement: Scenarios are runnable through the CLI and the MCP server, and the agent loop is the acceptance
The scenario runner SHALL be reachable through the composition root on both
surfaces: the CLI SHALL expose a run subcommand (`oh-house scenario run <path>`,
with `--seed`, `--json` and directory arguments), and the MCP server SHALL expose
the same entry point as the tool **`run_scenario`**, both being thin adapters over
`sim/scenario/runner.py:run_scenario` so neither surface offers behaviour the
library does not. `run_scenario` is a **client entry point** — a composed driver
over the ten operations the `control-surface` registry holds — and SHALL NOT be
added to that registry or to the `HouseAdapter` port, because it drives the
engine rather than being an operation on a house.

The exit criterion is this requirement's acceptance test, and it is runnable with
no Home Assistant present: an agent SHALL be able to build a house (a `given`
block), run a scenario (the CLI subcommand or the MCP tool), read a decision
record (`get_decision_log`, or a `LogExpectation`'s failure output), and iterate.
CI SHALL execute the whole loop in a checkout where Home Assistant is not
installed, and the loop SHALL be the same code path whichever surface invoked it,
which is why `run_scenario` is defined once and exposed twice.

#### Scenario: The CLI and the MCP tool agree
- **WHEN** one scenario is run through the CLI and through the MCP `run_scenario`
  tool at the same seed
- **THEN** both produce the same `RunResult`, and the same `FailureReport` when
  the scenario fails

#### Scenario: The CLI offers behaviour the library lacks
- **WHEN** the CLI's scenario subcommand or the MCP tool performs a step the
  registry cannot express
- **THEN** the surface-agreement check fails and names the step, so neither
  surface can be the only way to do something

#### Scenario: The agent loop runs with no Home Assistant
- **WHEN** CI builds a house, runs a committed scenario, reads a decision record
  and iterates, in a checkout where Home Assistant is not installed
- **THEN** the loop succeeds, and it fails when the no-HA guard is removed
