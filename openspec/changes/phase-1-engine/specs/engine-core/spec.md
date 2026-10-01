# Spec Delta — engine-core

## Purpose

The deterministic decision engine: the mechanism that binds placeholder slots to
entities, resolves a setting to the layer that decided it, tracks house modes,
reduces the commands a tick proposes to the one command an entity receives, and
leaves a record for every evaluation it makes. `engine-core` owns no policy —
the three behaviours that exercise it in this phase are `first-behaviours` and
the fake house it drives is `simulation` — and it owns no port: the engine
depends on `HouseAdapter` in `engine/adapter.py`, which is `house-adapter`'s.
What this capability owns is the *decision path itself*, so that Phase 2's pack
interpreter, Phase 3's profile axes and Phase 4's real adapter all change the
engine's inputs rather than its rules. The mechanism divides by concern:
`engine/binding.py` (slot resolution and the declared reduction),
`engine/config.py` (the layered resolver), `engine/modes.py`,
`engine/arbitration.py`, `engine/overrides.py`, `engine/rate_limit.py`,
`engine/decision_log.py`, `engine/safety.py` (the veto), and `engine/engine.py`
(the `Engine` that ticks and evaluates).

Three properties run through every requirement below. **The decision log is the
test oracle, not logging**: it is the artifact a scenario asserts against and an
agent reads, so its field set and its outcome vocabulary are normative in exactly
the way a device's state is. **Absence is stated**: a behaviour that could not
run, a command that was suppressed, and an action that was vetoed are each
recorded with the reason, and a behaviour that ran and found nothing to do is
recorded as having *declined* rather than being absent from the log — because a
state-only suite cannot tell "off because the timeout behaved" from "off because
it was never on", which is the difference every override, rate-limit and
arbitration rule in this capability is about (`design.md` D2). **The engine
carries no vocabulary of its own**: slot names, room types, the house scope and
the modes are read from `schemas/` and `catalog/`, which Phase 0 froze, so a
later vocabulary version bump is a data change rather than an engine change.

The engine is pure — no `homeassistant` import, no declared dependency outside
the list `architecture-invariants` reads, no wall clock — and that is asserted by
`architecture-invariants` and `simulation`, not restated here. This spec states
what the engine must *decide*; the two product rules that must gate every
decision it makes are stated at the end, because `design.md` D9 puts their
enforcement in this capability's evaluation path while `product-invariants` puts
their visibility to every other capability's author in its own spec.

## ADDED Requirements

### Requirement: Slot binding resolves a slot name to zero or more entities
`engine/binding.py` SHALL define `resolve_slot(house, scope, slot)`, returning a
`SlotBinding` that resolves a slot name to an ordered list of zero or more bound
entities. For a room-scoped behaviour the list is that room's `bindings` entry
for the slot; for a house-scoped one it is the slot's binding collected from
every room of the house, in room order — `house_scope.slots` names the slots a
house makes available at house scope and the entities are the ones the house's
rooms bind to that slot name. Resolution SHALL validate the house against
`schemas/house/1.0.0.json` and SHALL resolve every slot name against the
controlled vocabulary in `catalog/slots.yaml`; a binding naming a slot no slot
file defines SHALL fail resolution naming the room and the slot, because the
corpus's `required_slots` values are drawn from that vocabulary and a second,
engine-local list would be the drift the `configuration-schemas` conformance
check exists to forbid (`design.md` D7).

The list is plural by construction, not by convenience: the frozen schema binds
one `entity_id` per slot in a room, so a slot becomes plural exactly by being
bound in more than one room and read at house scope — a house-scoped
`light_group` collects several rooms' light groups behind the one role, and the
messy fixture (`simulation`) binds the same slot in two rooms. A model of one
entity per slot could not represent a house the fixture is required to build
(`design.md` D4), and validating against the frozen schema is what the list is
built from rather than something it departs from.

A slot a room type provides may be absent from `bindings`, and that absence is
the mechanism by which a slot is left unbound. The engine SHALL treat the
consequence of an unbound slot by the slot's own `required` flag in
`catalog/slots.yaml`:

- a behaviour whose **required** slot resolves to an empty list SHALL be
  **skipped**, and the skip SHALL be recorded with outcome `skipped: unbound
  slot` naming the behaviour and the slot;
- a behaviour whose **optional** slot resolves to an empty list SHALL still run,
  **degraded** — the unbound optional slot is observable to the behaviour as
  empty, which is the mechanism the lux-then-sun fallback in `first-behaviours`
  is built on.

An unbound slot SHALL NOT be silently treated as a bound-but-empty entity, and a
required slot's absence SHALL NOT be promoted to an exception: skipping with a
recorded reason is what lets a scenario answer "why did nothing happen".

#### Scenario: A required slot is unbound
- **WHEN** a behaviour requires `motion_sensor` and the room's `bindings` omit it
- **THEN** the behaviour is skipped and a decision record names the behaviour and
  the slot with outcome `skipped: unbound slot`

#### Scenario: An optional slot is unbound
- **WHEN** a behaviour optionally reads `lux_sensor` and no lux entity is bound
- **THEN** the behaviour runs, observes the slot as empty, and its record does
  not carry a skip outcome

#### Scenario: A binding names an undefined slot
- **WHEN** a house binds a slot name absent from `catalog/slots.yaml`
- **THEN** resolution fails and names the room and the slot

#### Scenario: A list-capable slot is populated
- **WHEN** two rooms each bind the same `light_group` slot and it is resolved at
  house scope
- **THEN** resolution returns both, in room order, and neither is dropped

### Requirement: A multi-entity slot read carries a declared reduction
A read over a slot bound to more than one entity SHALL carry a `Reduction` drawn
from the closed set `ANY` and `ALL` in `engine/binding.py`, and the reduction
SHALL be a **declared property of the `SlotRead`** rather than an inference from
the entities or the caller. A read that names no reduction SHALL fail rather than
choose a default. The reduction SHALL be recorded in the decision record's
`inputs`, so a record naming `motion_sensor: any` is distinguishable from one
naming `motion_sensor: all`. `ANY` yields true when at least one member satisfies
the predicate; `ALL` yields true when every member does, and is the reduction a
write confirmation reads.

The reason is that "any motion" is not "all motion" and there is no default that
is safe for both — a motion lighting service reads `ANY` while a confirmation
that a group is dark reads `ALL` — so an implicit reduction would be a bug that
looks like a correct answer whichever way it fell. Folding the reduction into the
slot read and naming it in the log is what makes a surprising reading inspectable
rather than mysterious (`design.md` D4).

#### Scenario: The same two entities read differently under each reduction
- **WHEN** one of two entities bound to a house-scoped slot is on and the other
  is off
- **THEN** the read is true under `ANY` and false under `ALL`

#### Scenario: The reduction is absent from a read
- **WHEN** a read over a multi-entity slot names no reduction
- **THEN** the read fails rather than choosing a default

#### Scenario: The reduction is visible in the log
- **WHEN** a behaviour reads a multi-entity slot and acts
- **THEN** its decision record's `inputs` name the slot and its reduction

### Requirement: The layered config resolver fixes its order and reports the deciding layer
`engine/config.py` SHALL define a `ConfigResolver` whose `resolve(key, scope)`
returns a `ResolvedSetting` carrying both the value and the `Layer` that decided
it. The layer order SHALL be fixed, lowest precedence first: **built-in defaults
< house < room < reserved profile layer < temporary override**. The deciding
layer SHALL be recordable in the decision log's `inputs`, so a resolved setting
is explainable without re-deriving the stack. Every behaviour tunable — the quiet
timeout, the lux threshold, the sun-elevation threshold, an arbitrated
`priority`, an override's duration and its reset conditions — SHALL resolve
through this resolver, so no tunable is a module constant.

The order is pinned in this phase even though only four of its five layers are
populated — the built-in defaults, the house layer, the room layer and the
temporary override are; the profile layer is present and **empty**. The
alternative, implementing the two layers Phase 1 needs and adding the rest when
Phase 3 arrives, was rejected because precedence is the one thing every later
layer's meaning depends on: a refactor that inserts a layer is free to reorder
the others silently, whereas pinning the order now costs an empty layer and buys
a guarantee that Phase 3 inserts the profile layer *between room and override*,
as `spec.txt` states, and cannot move anything else (`design.md` D5).

#### Scenario: A higher layer overrides a lower one
- **WHEN** the same key is set in the house layer and in the room layer
- **THEN** resolution returns the room value and reports `layer: room`

#### Scenario: A behaviour enables at one layer and disables at a higher one
- **WHEN** `behaviour.<id>.enabled` is `true` at the house layer and `false` at
  the room layer for one room
- **THEN** the behaviour acts in the other rooms, is skipped `skipped: disabled`
  in that one, and the record names the deciding layer

#### Scenario: The empty profile layer changes nothing
- **WHEN** the profile layer is present and empty and a setting is resolved
- **THEN** the result and its reported layer are unchanged from a run without it

#### Scenario: The layer order is reordered
- **WHEN** the declared layer order is changed, for example by moving the override
  below the room layer
- **THEN** a test fails naming the order, because the order is a checked artifact
  rather than a comment

#### Scenario: No layer sets the key
- **WHEN** a setting is set by no layer
- **THEN** resolution returns the built-in default and reports `layer: builtin`

### Requirement: House modes are mutually exclusive within an exclusive group
`engine/modes.py` SHALL define a `ModeSet` tracking the house's active modes. A
mode SHALL validate against `schemas/mode/1.0.0.json`, and two modes sharing an
`exclusive_group` SHALL NOT be active at once: activating a mode SHALL clear
every other active mode in the same group, while leaving active modes in any
other group untouched. A behaviour SHALL be able to gate its evaluation on a
mode; a mode gate that is unmet means the behaviour evaluates and **declines**
(see the decision-log requirement) rather than being skipped.

Modelling exclusivity as a group rather than as a rule enumerating forbidden
pairs is `schemas/mode/`'s decision and the engine reads it: `home`, `away`,
`sleep` and `holiday` do not each need a rule naming the other three, and a group
added later needs no engine change.

#### Scenario: A second mode in a group clears the first
- **WHEN** `home` and `away` share an exclusive group and the engine sets `away`
  while `home` is active
- **THEN** `away` is active, `home` is not, and the set contains no other member
  of that group

#### Scenario: A mode in another group is untouched
- **WHEN** a mode with no exclusive group, or a different one, is active and the
  engine sets a grouped mode
- **THEN** the ungrouped mode remains active

#### Scenario: A behaviour gates on a mode
- **WHEN** an away-gated behaviour is evaluated while the house is not away
- **THEN** it proposes no command and its record has outcome `declined`

### Requirement: Arbitration reduces one entity's tick commands to one, by priority
`engine/arbitration.py` SHALL collect every `ProposedCommand` a tick produces and
SHALL reduce the commands targeting one entity to exactly one command. The
reduction SHALL be by priority in a fixed order: an explicit **user action
outranks every behaviour**, and between two behaviours the higher declared
`priority` wins. When priorities tie, the winner SHALL be chosen by a
**deterministic total order** — ascending behaviour `id` — so the same input
always yields the same winner regardless of the order behaviours were evaluated
in.

The losing commands SHALL be recorded, with outcome `lost arbitration`, on the
record of the behaviour that proposed them, naming the winning behaviour and its
`rule`; the winner's record carries the outcome of its own resolution. Evaluating
last-writer-wins by scheduling order was rejected because the outcome would
depend on the order behaviours happened to be visited in, which is neither
explainable nor stable, and Phase 2's exit criterion — "two modules cannot fight
over one light" — needs a rule that names a winner and a reason (`design.md`
D6). A behaviour SHALL NOT implement its own conflict resolution.

#### Scenario: Two behaviours command one light
- **WHEN** two enabled behaviours both propose a command to one entity in one tick
- **THEN** exactly one command is applied, and the log names the winner and the
  loser, the loser with outcome `lost arbitration`

#### Scenario: The winner is stable
- **WHEN** the same tick is replayed with the same inputs
- **THEN** the same behaviour wins, because the tie-break is a total order and
  not the evaluation order

#### Scenario: A user action beats every behaviour
- **WHEN** a user action and a behaviour command target one entity in one tick
- **THEN** the user action is applied and the behaviour command is recorded
  `lost arbitration`

### Requirement: Manual override suppresses commands to an entity the user last touched
`engine/overrides.py` SHALL define an `OverrideRegistry` that reads the change
context the port attaches to every state change (`house-adapter`) and SHALL mark
an entity whose **last writer was a user** as overridden. While an entity is
overridden, the engine SHALL suppress its own behaviour commands to it, and each
suppression SHALL be recorded with outcome `overridden`, naming the behaviour,
the entity and **the reset condition being awaited** — suppressed commands are
recorded, not dropped in silence, because "the behaviour ran and was overridden"
and "the behaviour did not run" are different facts an agent must be able to tell
apart.

An override SHALL lapse only through a named `ResetCondition`, from the closed
set: **`override_timeout`** (the configured duration elapses on the virtual
clock), **`room_emptied`** (the room's motion is clear for the quiet timeout),
**`mode_changed`** (the room or house leaves the mode the override was recorded
under), and **`explicit_clear`** (a `user_action` or a scenario step clears it).
Each SHALL have its own test, and the condition that ended an override SHALL
appear in the record that resumes acting. An `engine`-, `world`- or `fault`-origin
change SHALL NOT create an override; only a `user`-origin change does.

#### Scenario: A user touch overrides a behaviour
- **WHEN** a user turns a room's light off and a motion behaviour proposes to turn
  it on before any reset condition holds
- **THEN** the behaviour's command is suppressed and recorded `overridden`,
  naming the reset condition being awaited

#### Scenario: Each reset condition lifts the override
- **WHEN** `override_timeout` elapses, the room empties for the quiet timeout, the
  mode changes, or `explicit_clear` is called
- **THEN** the override is cleared, the engine's commands to the entity resume,
  and the resuming record names which condition released it

#### Scenario: An engine write does not override
- **WHEN** the engine writes an entity and immediately re-evaluates it
- **THEN** no override exists, because the change's context was the engine's

### Requirement: Rate limits bound commands per entity per window, after arbitration
`engine/rate_limit.py` SHALL apply a per-entity, per-window rate limit as the
engine's **own suppression stage, ordered after arbitration**, so a command
dropped by the limit is recorded with outcome `rate-limited` and is
distinguishable from one dropped by a lost arbitration or an override. The bound
and the window SHALL resolve through the `ConfigResolver`, so they are layered
settings rather than constants. A command suppressed by the limit SHALL be
recorded, naming the behaviour and the entity.

Folding the limit into arbitration priority was rejected because the three
suppressions produce three different log outcomes, and an agent that cannot tell
"a higher-priority behaviour won" from "the limit was reached" cannot diagnose a
house whose lights will not follow a rule (`design.md` D6).

#### Scenario: A burst is bounded
- **WHEN** a behaviour proposes more commands to one entity within one window
  than the bound allows
- **THEN** the commands within the bound are applied and the remainder are
  recorded `rate-limited`

#### Scenario: A rate-limited command is not a lost arbitration
- **WHEN** one entity receives a rate-limited command and, in the same tick, a
  behaviour proposes a lower-priority command to it
- **THEN** the two suppressions appear with distinct outcomes, `rate-limited` and
  `lost arbitration`

#### Scenario: The window is a virtual-clock window
- **WHEN** the virtual clock is advanced past the window with no wall-clock time
  elapsing
- **THEN** the limit admits a fresh command

### Requirement: The decision log records one normative record per evaluation
`engine/decision_log.py` SHALL define a `DecisionRecord`, an `Outcome`, and a
`DecisionLog`; `engine/engine.py`'s `Engine` SHALL append **exactly one record
per behaviour evaluation in a tick** — a behaviour that is enabled evaluates, a
behaviour that is disabled is skipped, and both leave a record naming the
behaviour's `id` as the `actor`, so no behaviour can act or decline off the
record. Each record SHALL carry this **normative field set**, and no field
outside it may be required for the log to serve as oracle:

- `at` — the virtual timestamp of the evaluation, never the wall clock;
- `actor` — the behaviour unit `id` whose evaluation produced the record;
- `inputs` — every slot read and every resolved setting the evaluation consulted,
  each naming its slot or key, the entities involved, the read's `reduction`
  where the slot is multi-entity, and the deciding `layer` for a resolved
  setting;
- `rule` — the corpus concept id the evaluation matched, for example
  `lighting.motion_light_on`, or `null` when a skip preceded any rule;
- `commands` — the commands the evaluation proposed, each naming its target slot,
  the entities, the action, and its origin context;
- `outcome` — exactly one value from the closed set **`acted`, `declined`, `lost
  arbitration`, `overridden`, `rate-limited`, `skipped: unbound slot`, `skipped:
  disabled`, `refused: unsafe`**;
- `state_delta` — the change the record's resolution actually applied, as entity
  → before → after, empty when nothing changed.

`declined` is the disposition of an evaluation that reached no command because
its condition was unmet or its target was already in the commanded state. The
design's enumeration names the *act* and *suppression* outcomes; `declined` is
added because `design.md` D2 itself turns on the oracle distinguishing *acted*
from *declined*, and because a behaviour that runs and finds nothing to do must
be observable as something other than an absent record.

Every outcome in the closed set SHALL be producible by some evaluation, and a
test SHALL assert that, so an outcome nothing emits is caught rather than
tolerated. The field set is normative because the scenario runner and the agent
both parse it; it is what makes "read the log" the exit criterion's check rather
than a hope.

#### Scenario: An evaluation that acts leaves one record
- **WHEN** a behaviour turns a light on
- **THEN** exactly one record is appended, with outcome `acted`, the `rule`
  named, and the light's before and after in `state_delta`

#### Scenario: An evaluation that decides not to act still records
- **WHEN** a behaviour is evaluated and its condition is unmet, or its target is
  already in the commanded state
- **THEN** a record is appended with outcome `declined`, an empty `commands`, and
  an empty `state_delta`

#### Scenario: Every outcome is reachable
- **WHEN** the test suite exercises the engine
- **THEN** each of the eight outcome values is produced by some evaluation, and
  an outcome that no evaluation emits fails the check

#### Scenario: The oracle distinguishes acted from declined
- **WHEN** a scenario asserts a light is off *because the timeout behaved*
- **THEN** it passes only when a record with `rule` `lighting.motion_light_off`
  and outcome `acted` exists, and fails when the light was never on so the record
  is `declined` or absent

#### Scenario: A record omits a normative field
- **WHEN** a record is written without `outcome`, or with an `outcome` outside
  the closed set
- **THEN** the write fails rather than persisting a record the oracle cannot read

### Requirement: The decision log is bounded, and only history is excluded from snapshots
The `DecisionLog` SHALL be bounded by default, with the bound supplied through
the `ConfigResolver`, and SHALL retain the most recent records in order. A read
SHALL return a **window** of the most recent records, which is the operation the
control surface exposes as `get_decision_log`. Because the records are retained
for assertion rather than for display, an unbounded log would be a memory sink on
a long scenario; the bound is the mitigation, and the log SHALL be excluded from
`sim/snapshot.py`'s snapshot so that a restored house restores *state* while
history stays where it happened — restoring history would make a replayed run's
log depend on the run that preceded it, which is exactly what the
restore-then-replay invariant forbids.

#### Scenario: A long run does not grow the log past its bound
- **WHEN** a scenario drives more evaluations than the configured bound
- **THEN** the log holds at most the bound, and the oldest records are the ones
  dropped

#### Scenario: The read window returns the most recent records in order
- **WHEN** `get_decision_log` is read with a window size
- **THEN** it returns the most recent records, oldest first within the window

#### Scenario: A restore does not restore the log
- **WHEN** a snapshot is taken and restored and the engine continues
- **THEN** the pre-snapshot records are absent and the evaluations after restore
  are identical to those a fresh run from the snapshot makes

### Requirement: The engine carries no vocabulary of its own
The engine SHALL read slot names, room types, the house scope and mode
definitions from the Phase 0 artifacts — `catalog/slots.yaml`,
`catalog/room_types.yaml`, `schemas/slot/`, `schemas/room-type/`, `schemas/house/`
and `schemas/mode/` — and SHALL NOT declare a private list, enum or constant that
restates any of them. A check SHALL fail when `engine/` declares a slot,
room-type or mode name that is absent from the corresponding catalog file, and
when a catalog slot name the engine references cannot be resolved.

The alternative — a smaller, phase-local vocabulary to keep the engine simple —
was rejected because it would be the second definition of a concept the schemas
exist to define once, and because the corpus's `required_slots` values are drawn
from the frozen vocabulary, so a local list would not match the rows the
behaviours are specified from (`design.md` D7).

#### Scenario: The engine restates a slot name
- **WHEN** `engine/` contains a slot name not present in `catalog/slots.yaml`
- **THEN** the conformance check fails and names the slot and the file

#### Scenario: A catalog vocabulary bump requires no engine change
- **WHEN** a slot is renamed in `catalog/slots.yaml` and the schemas version
- **THEN** the engine resolves the new name without an engine code change, because
  the name is data it reads

### Requirement: Nothing is on unless enabled, and every behaviour can be turned off
`engine/engine.py`'s evaluation gate SHALL evaluate a behaviour's policy only
when that behaviour is enabled, and SHALL carry a per-behaviour and per-module
enable flag, keyed `behaviour.<id>.enabled` and resolved through the
`ConfigResolver`, defaulting to **off**. A freshly built house SHALL run no
behaviour until something enables one. A behaviour that is disabled when it would
otherwise be evaluated SHALL be recorded with outcome `skipped: disabled` naming
the behaviour as `actor`, and disabling one behaviour SHALL leave every other
behaviour working.

OFF-by-default is an **evaluation gate**, not a convention: the check is inside
the engine's evaluation path, so it cannot be bypassed by a behaviour that
forgets it, and the module-level flag gates a whole behaviour family by the same
mechanism. This requirement is the engine-side enforcement of the first product
rule, stated as a product rule in `product-invariants` so that a Phase 2 pack
author sees it before writing a behaviour (`design.md` D9).

#### Scenario: A fresh house runs nothing
- **WHEN** a house is built and driven across a motion, a quiet timeout and an
  emptying with no enable flag set
- **THEN** no behaviour proposes a command, and every behaviour's record carries
  outcome `skipped: disabled`

#### Scenario: A disabled behaviour records why it did not run
- **WHEN** an enabled and a disabled behaviour would both be evaluated
- **THEN** the disabled one's record carries outcome `skipped: disabled` and the
  enabled one evaluates normally

#### Scenario: Disabling one leaves the others working
- **WHEN** one of several enabled behaviours is disabled
- **THEN** every other enabled behaviour still acts

### Requirement: No non-user origin may unlock a lock or open a cover
`engine/safety.py` SHALL define the action gate the engine applies before a
command reaches the port: it SHALL refuse to emit any command that unlocks a
`lock` or opens a `cover` from any origin other than a direct user action, and
the refusal SHALL **fail closed** — when the origin of a command is unknown or
ambiguous, the command is refused. A refused command SHALL be recorded with
outcome `refused: unsafe`, naming the behaviour and the entity. A direct
`user_action` unlock or open SHALL be applied, because the rule is a ban on the
*system* acting, not on the user.

The veto is an **action gate in the engine**, exercised before the command
reaches the port, and it is deliberately not enforced only in the adapter: an
adapter that refuses a command has already let a behaviour *propose* it, so the
proposal would be invisible in the log and the guarantee would become a runtime
surprise rather than a recorded refusal (`design.md` D9; `house-adapter` states
the same boundary from the port's side). This requirement is the engine-side
enforcement of the second product rule, stated as a product rule in
`product-invariants`.

#### Scenario: A behaviour tries to unlock
- **WHEN** a behaviour proposes to unlock a `lock` entity
- **THEN** the command is refused and recorded `refused: unsafe`, and no port
  write occurs

#### Scenario: A behaviour tries to open a cover
- **WHEN** a behaviour proposes to open a `cover` entity
- **THEN** the command is refused and recorded `refused: unsafe`

#### Scenario: A user unlocks directly
- **WHEN** a `user_action` unlocks a `lock` entity
- **THEN** the command is applied, because a user origin is permitted

#### Scenario: The origin is ambiguous
- **WHEN** an unlock command arrives whose origin cannot be established as a user
- **THEN** the engine refuses it and records `refused: unsafe`

### Requirement: The engine's decisions are a deterministic function of its inputs
Given the same house, the same virtual clock position and the same store, the
`Engine` SHALL reach the same decisions and produce the same records, and SHALL
therefore never depend on the wall clock or on the iteration order of any
unordered collection. Time SHALL be read only through the virtual clock injected
into the `Engine` — `simulation`'s `sim/clock.py`'s `VirtualClock`, supplied by
the composition root — never through the wall clock, and the `HouseAdapter` port
(`house-adapter`) carries no time source of its own. The arbitration tie-break,
the binding order, the layer order and the
log's ordering SHALL all be total orders, so replay is identical.

This is what makes a Hypothesis counterexample reproducible, a restored run
identical to a fresh one, and a surviving mutant a statement about a missing test
rather than about a flaky run — determinism is a property the scenario runner and
the mutation gate are built on, not a nicety of the engine (`design.md` D3).

#### Scenario: Replay is identical
- **WHEN** a tick sequence is run twice from the same seed and clock
- **THEN** the two decision logs are identical record for record

#### Scenario: The wall clock is never read
- **WHEN** a scan inspects `engine/` for a wall-clock read
- **THEN** no engine module reads `time.time`, `datetime.now` or `monotonic`

#### Scenario: Advancing the clock by zero changes nothing
- **WHEN** the engine is advanced by a zero interval
- **THEN** no tick is produced, so no evaluation is recorded and no command is
  emitted
