# Spec Delta — product-invariants

## Purpose

The two product promises that bind every capability in this phase and every phase
after it: **modules and behaviours are OFF by default and every one of them can be
turned off**, and **nothing in the system may ever auto-unlock a door or
auto-open a garage**. They are a capability of their own, following
`architecture-invariants`' precedent — a cross-cutting rule enforced in more than
one place and owned by no single module — because they are the one concern an
author of any other spec must be able to find without inferring it from
`engine-core`'s or `first-behaviours`' requirements. `engine-core` enforces them
mechanically (its evaluation gate and its `engine/safety.py` action gate),
`first-behaviours` writes the first units the first rule governs, `simulation` is
where an unsafe command would physically be applied, and `scenario-runner` is
where both rules are asserted; this spec is the version each of those authors
reads first, and its requirements are stated as product rules so that the Phase 2
pack author meets them before writing a module.

Two things shape every requirement below. **Both rules are gates, not
conventions**: the enable check sits inside `engine/engine.py`'s evaluation path
so a behaviour cannot forget it, and the veto sits in `engine/safety.py` before a
command reaches the port so no behaviour can propose around it (`design.md` D9).
**The decision log is the oracle for both**: a freshly built house's lights are
off whether a behaviour was disabled or merely never triggered, and a lock is
locked whether an unlock was never proposed or was refused — so each rule is
assertable only because `engine/decision_log.py`'s `Outcome` carries a value
naming it, `skipped: disabled` for the first and `refused: unsafe` for the
second. A state-only suite cannot tell either rule apart from its opposite, which
is why these two outcomes are load-bearing for testing rather than being
logging (`design.md` D2).

One reconciliation is stated before the requirements, because it is the shape the
second rule is easiest to get wrong. "Everything can be turned off" governs the
units that *act*; it does not license an always-on *tier*. The safety rule is not
a module that runs above the modes — it is a **prohibition on what any unit may
propose**, enforced whether or not anything is enabled, and it creates no state,
no schedule and no command of its own. It has no enable flag because there is
nothing of it to enable; the one thing it does is refuse. A reader who finds the
words "always applied" in this capability must read them as a restriction and not
as a resurrected safety tier.

## ADDED Requirements

### Requirement: Behaviours are OFF by default, enforced as an evaluation gate
A freshly built house SHALL run no behaviour: `engine/engine.py`'s `Engine` SHALL
evaluate a behaviour's policy only when that behaviour is enabled, and the enable
state SHALL default to **off** for every behaviour. Enablement SHALL be a
per-behaviour config key — `behaviour.<id>.enabled` — resolved through
`engine/config.py`'s `ConfigResolver`, so a house or a room can turn a behaviour
on without editing code, and the deciding layer SHALL be recordable. A behaviour
that is disabled when it would otherwise be evaluated SHALL append a record whose
`actor` is the behaviour's `id` and whose `outcome` is `skipped: disabled`.
Modules carry the same rule once Phase 2 defines them (`design.md` Non-Goals); no
module exists in this phase, so the gate is scoped to behaviours here and the
`module.<id>.enabled` key is Phase 2's.

The check is inside the evaluation path rather than in each behaviour, so a
behaviour that omits it cannot run anyway, and a behaviour that is skipped leaves
a record rather than an absence — because "the behaviour was off" and "the
behaviour ran and found nothing to do" are different facts an agent reads the log
to tell apart (`design.md` D9).

#### Scenario: A fresh house runs nothing
- **WHEN** any fixture house is built and time is advanced across a motion, a
  quiet timeout and an emptying, no enable flag having been set
- **THEN** no behaviour proposes a command, and every behaviour's evaluation
  records outcome `skipped: disabled`

#### Scenario: A disabled behaviour records why it did not run
- **WHEN** one behaviour is enabled and another is not, and an event both would
  evaluate on occurs
- **THEN** the disabled one's record carries outcome `skipped: disabled` with its
  `id` as `actor`, and the enabled one evaluates normally

#### Scenario: Disabling one leaves the others working
- **WHEN** two behaviours are enabled and one is then disabled
- **THEN** the disabled one records `skipped: disabled` and the other still acts

#### Scenario: The enable check is bypassed
- **WHEN** a path in `engine/engine.py` reaches a unit's `evaluate()` without
  reading its enable flag
- **THEN** the gate check fails and names the path and the unit

### Requirement: Every behaviour can be turned off, and there is no always-on tier
Every unit the engine runs SHALL carry an enable flag, and no unit SHALL be
immutable: there SHALL be no behaviour, no module, and no privileged "safety" or
"core" unit that the enable gate exempts. The behaviour set returned by
`engine/behaviours/__init__.py:default_behaviours()` SHALL be enumerable — the
only unit registry this phase has, since modules are Phase 2's (`design.md`
Non-Goals) — and a check SHALL assert that the set of behaviour enable keys
(`behaviour.<id>.enabled`) equals the set of registered behaviours, so a behaviour
cannot be added without an enable flag and a flag cannot be added without a
behaviour. When Phase 2 adds the module registry the same check SHALL extend to
`module.<id>.enabled` keys and registered modules; naming it here is what stops a
module shipping later without a flag. Disabling a unit SHALL leave every other
unit's behaviour unchanged.

"Everything can be turned off" is stated as a structural rule rather than as a
UI promise because the failure it prevents is an exemption introduced for a good
reason: a unit that is "too important to disable" is exactly the always-on tier
this product has decided not to have, and the check that every unit has a flag is
what makes an exemption a failing test rather than a design conversation. The
second product rule is not the exception — see below; it carries no flag because
it is not a unit.

#### Scenario: A unit has no enable flag
- **WHEN** a behaviour is registered and carries no `enabled` key resolvable as
  `behaviour.<id>.enabled`
- **THEN** the enable-flags check fails and names the unit

#### Scenario: A privileged unit skips the gate
- **WHEN** `engine/engine.py` carries a branch that evaluates a named unit
  regardless of its enable flag
- **THEN** the check fails and names the branch and the unit, so no always-on
  tier can be introduced

#### Scenario: A flag names a nonexistent unit
- **WHEN** a config layer sets `behaviour.<id>.enabled` for an `id` no registered
  behaviour declares
- **THEN** validation fails and names the key

### Requirement: No non-user origin may unlock a lock or open a cover
`engine/safety.py` SHALL define the action gate the engine applies before a
command reaches the port, and it SHALL refuse to emit any command that **unlocks
a `lock` entity** or **opens a `cover` entity** from any origin other than a
**direct user action**. The origin SHALL be read from `house-adapter`'s
`ChangeContext` (`user`, `engine`, `world`, `fault`), and the gate SHALL **fail closed**: a
command whose origin is absent, unknown or ambiguous SHALL be refused. A refusal
SHALL prevent the command from reaching the port — no actuation occurs — and
SHALL be recorded with outcome `refused: unsafe`, naming the behaviour and the
entity. A command that **locks** a lock or **closes** a cover SHALL NOT be
refused, because the rule is a ban on the system *opening* egress and not on it
making a house safer.

The two domains the veto names — `lock` and `cover` — SHALL be read from the
frozen vocabulary rather than restated: the `lock` slot in `catalog/slots.yaml`
accepts the `lock` domain and the `cover` slot accepts `cover`, so the gate's
subject is the vocabulary Phase 0 froze and not an engine-local list
(`design.md` D7). The veto applies to **every** proposer and every non-user
origin without exception, including a fault-origin change, a restart, and a
command a Phase 2 pack will propose, because it is stated over the command's
origin and never over who proposed it.

#### Scenario: A behaviour proposes to unlock
- **WHEN** a behaviour proposes a command that unlocks a `lock`-domain entity
- **THEN** the command is refused and recorded `refused: unsafe`, and no port
  write reaches the house

#### Scenario: A behaviour proposes to open a cover
- **WHEN** a behaviour proposes a command that opens a `cover`-domain entity
- **THEN** the command is refused and recorded `refused: unsafe`

#### Scenario: A fault-origin unlock is refused
- **WHEN** an unlock arrives whose change origin is `fault`
- **THEN** it is refused and recorded `refused: unsafe`, so a fault is not a
  loophole

#### Scenario: The origin is ambiguous
- **WHEN** an unlock or open arrives whose origin cannot be established as a
  direct user action
- **THEN** the gate refuses it and records `refused: unsafe`

#### Scenario: A user unlocks directly
- **WHEN** the control surface's `user_action` unlocks a `lock` entity
- **THEN** the command is applied, because the ban is on the system acting and
  not on the user

#### Scenario: Locking and closing are not refused
- **WHEN** a behaviour proposes to lock a lock or close a cover
- **THEN** the gate admits it, so the veto restricts opening and not securing

### Requirement: The safety veto is a prohibition, not a feature or an always-on tier
The veto SHALL be enforced unconditionally in the engine's command path and SHALL
NOT be gated by any enable flag: there SHALL be no config key that disables it,
and it SHALL hold when every behaviour and every module is disabled. It SHALL
create no state, no command and no schedule of its own — its only effect is to
withhold a command another unit proposed — so it is a **restriction on what any
unit may do** rather than a unit itself, and is therefore not the always-on tier
the previous requirement forbids. A reader SHALL NOT find in this capability a
"safety" behaviour, mode or module: the hazard-alert behaviours `spec.txt` names
are ordinary opt-in units like any other, and only the prohibition survives.

The distinction is stated as a requirement because the two things are easy to
conflate and the product has decided which one it wants: a safety *tier* would be
something that runs, that can fail, and that someone would eventually ask to
disable, whereas the prohibition is a check that either holds or does not and
that has nothing to disable. The checks below make the conflation a failing test
rather than a matter of reading.

#### Scenario: A key would disable the veto
- **WHEN** a config key such as `safety.enabled` or `veto.<id>.enabled` is
  introduced to turn the prohibition off
- **THEN** the check fails and names the key, because the veto is not a unit and
  carries no flag

#### Scenario: The veto holds with everything disabled
- **WHEN** every behaviour and every module is disabled and a non-user unlock
  command is proposed through the gate
- **THEN** it is refused and recorded `refused: unsafe`, so the veto does not
  depend on any unit being enabled

#### Scenario: A safety tier reappears
- **WHEN** a behaviour, module or mode is registered whose stated purpose is to
  enforce the veto or to "always run" for safety
- **THEN** the check fails and names it, directing the enforcement to
  `engine/safety.py` where it is a restriction and not a unit

### Requirement: Both rules are enforced at the gate, before the command reaches the port
Both product rules SHALL be enforced in `engine/` and SHALL NOT be enforced only
in an adapter: the enable gate and the veto SHALL sit on the path a command takes
from a behaviour to the port, so a refused unlock is visible in the decision log
as `refused: unsafe` rather than appearing as an adapter-level error after a
behaviour has already proposed it. The port (`house-adapter`) SHALL relay the
gate's verdict and SHALL NOT re-decide it, and `simulation` SHALL NOT originate
an unlock or open-cover command; the simulator is where such a command would
physically be applied and is therefore the place the rule is observed to hold.

Enforcing at the proposal boundary rather than at the house is what makes the
guarantee strong: an adapter that refuses a command has already let a behaviour
propose it, so the proposal would be invisible and the guarantee a runtime
surprise, whereas a gate that refuses before the port makes "a behaviour cannot
propose an unlock" a property the log and a Hypothesis invariant can both check
(`design.md` D9).

#### Scenario: The port re-decides the gate
- **WHEN** an adapter refuses a lock-unlock or cover-open actuation on safety
  grounds rather than relaying the gate's verdict
- **THEN** the contract check fails, because the refusal belongs in the engine's
  log as `refused: unsafe`

#### Scenario: A gate is added in the simulator
- **WHEN** `sim/` carries a safety refusal of its own instead of the engine's
  veto
- **THEN** the check fails and directs the enforcement to `engine/safety.py`

#### Scenario: An unsafe command reaches the house
- **WHEN** any run reaches a state where a lock is unlocked or a cover opened by
  a non-user origin
- **THEN** the run fails, naming the proposal and its origin

### Requirement: The decision log is the oracle for both product rules
Each rule SHALL be assertable from `engine/decision_log.py`'s record alone.
`Outcome` SHALL carry `skipped: disabled` and `refused: unsafe` in its closed set
(the set `engine-core` states normatively), the first-rule outcome SHALL name the
disabled unit as `actor`, and the second-rule outcome SHALL name the behaviour
and the entity refused. A scenario SHALL assert each rule by its outcome and rule
name, and a claim about either rule that could be verified only from device state
SHALL be treated as unspecified — because a freshly built house whose lights are
off and a lock left locked are each indistinguishable in state from a house where
the rules did not hold. This is the phase's decision-log-as-oracle property
applied to its two product promises: the outcomes are records a test reads, not
records a human reads (`design.md` D2).

#### Scenario: The first rule is asserted from the log
- **WHEN** a scenario asserts a freshly built house ran nothing
- **THEN** it passes only when records with outcome `skipped: disabled` exist,
  and fails when a state-only assertion that the lights are off would pass for
  the wrong reason

#### Scenario: The second rule is asserted from the log
- **WHEN** a scenario asserts a lock was not unlocked by the system
- **THEN** it passes only when a record with outcome `refused: unsafe` exists
  for the proposal, and fails when the lock is merely locked because no unlock
  was proposed

#### Scenario: A rule outcome is missing from the closed set
- **WHEN** `Outcome` no longer carries `skipped: disabled` or `refused: unsafe`
- **THEN** the outcome-closure check (`engine-core`) fails and names the missing
  value, so the oracle a product rule depends on cannot be removed

### Requirement: A pack cannot bypass the rules, and every rule is bound to a named check
`install_pack` at its Phase 1 scope (`control-surface`) SHALL accept a manifest
that validates and checks its `requires_slots` against the house, and SHALL NOT
claim to enforce the veto — because the veto is enforced at the action gate
whatever proposed the command, a pack needs no vetting for the rule to hold. The
pack-author-facing enforcement of the ban is Phase 2's banned-service list, which
SHALL include the unlock and open-cover services and SHALL be the place a module
that would auto-unlock is rejected before it is installed (`design.md`
Non-Goals). Until that list exists, the action gate is the guarantee, and a
command a pack proposes is refused exactly as a first behaviour's is.

A rule stated in this spec SHALL be bound to a named check and a named scenario,
so it cannot be a promise nothing runs: the enable rule maps to the
evaluation-gate check and to `scenario-runner`'s invariant that no behaviour acts
off its own initiative, and the veto maps to the requirement→check mapping
(`phase-1-engine` task 12.1) and to `scenario-runner`'s Hypothesis invariant that
no non-user origin emits an unlock or an open-cover command.

#### Scenario: A pack proposes an auto-unlock
- **WHEN** an installed module proposes an unlock outside a direct user action
- **THEN** the action gate refuses it and records `refused: unsafe`, so the veto
  does not depend on the pack having been vetted

#### Scenario: A product rule has no mapped check
- **WHEN** a requirement in this capability is not mapped to a named check and a
  named scenario in the requirement→check mapping
- **THEN** the mapping check fails and names the requirement, so a rule cannot
  ship unenforced

#### Scenario: install_pack is mistaken for the enforcement point
- **WHEN** a test asserts the veto by way of `install_pack` rejecting a manifest
  rather than by way of the action gate refusing a command
- **THEN** the check fails and names the assertion, because the gate is the
  guarantee and the Phase 1 `install_pack` does not speak to the ban

### Requirement: The control surface exposes the rules and offers no bypass
The rules SHALL be observable and drivable through the control surface and SHALL
admit no bypass there. Enablement SHALL be supplied as `behaviour.<id>.enabled`
in the house configuration an agent builds — through the library facade's house
builder or `import_config` — and the effect SHALL be visible in
`get_decision_log` as `skipped: disabled` before and `acted` after. A direct
`user_action` SHALL be the one operation that can unlock a lock or open a cover,
and no other operation in the registry — CLI subcommand or MCP tool — SHALL be
able to produce such a change. The CLI and the MCP server SHALL expose the same
operations over the same registry, so the rule holds identically on both faces
and neither can offer a way round it.

The surface is where an agent exercises the phase's exit criterion, so the two
product rules must be reachable and checkable there rather than only in the
engine's internals: an agent that can build a house, run a scenario and read the
log must be able to *see* that nothing ran by default and that the system never
opened a door, and must be unable to make it do so except as a user.

#### Scenario: An agent enables a behaviour through the surface
- **WHEN** an agent supplies `behaviour.<id>.enabled` in the configuration it
  builds and reads the log before and after
- **THEN** the record changes from `skipped: disabled` to `acted`, with no CLI- or
  MCP-only path involved

#### Scenario: An agent unlocks as a user
- **WHEN** the agent calls the `user_action` operation on a `lock` entity
- **THEN** the command is applied, and the record shows a user-origin change and
  not `refused: unsafe`

#### Scenario: An operation bypasses the veto
- **WHEN** an operation other than `user_action` — on the library, the CLI or the
  MCP tool set — completes a lock-unlock or cover-open
- **THEN** the registry check fails and names the operation, so no surface offers
  a way round the rule
