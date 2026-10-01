# Spec Delta — pack-sandbox

## Purpose

What a pack may **do**, once it has said what it is. `spec.txt:55` asks for a
"declarative-only interpreter with a capability sandbox: only bound entities, only
declared services, a banned-service list, and red-flagged dangerous
permissions", and the four clauses are one claim seen from four sides: a pack is
a *description of intent* that the engine carries out, never a program the engine
runs. The difference is not stylistic. A pack that can branch can loop, a pack
that can loop can be wrong in ways no validation enumerates, and a pack that can
reach one entity beyond its grant has a grant in name only.

Two tensions shape the requirements and are stated rather than smoothed over.
**The vocabulary is wider than the sandbox.** `behavior-vocabulary/1.1.0.json`
publishes fourteen actions and several of them are control flow — `if`, `choose`,
`parallel`, `repeat`, `variables`, `stop` are all terms the extraction observed
across the four estates, so they are published, and a declarative-only pack may
not use them. A term that is published and forbidden is not an *unknown* term and
must not be reported as one; the two failures are different and a pack author has
to be able to tell which they have hit. **The sandbox is data, not code.** The
banned service list and the dangerous-permission flags do not exist anywhere in
the repository today — this phase creates them, and they are published artifacts
the validator reads, so banning a service is an edit to a file rather than a
release of the engine.

What the sandbox deliberately does **not** own is the binding layer's business.
Phase 1 left `catalog/slots.yaml`'s `accepts_domains` unread on purpose —
`engine/vocabulary.py` says no Phase 1 requirement makes the engine check a
binding's domain against the slot it is bound to — and this phase does not
reverse that: a `light_group` slot bound to a `cover` is a mis-binding, and the
sandbox's question is narrower, which is whether a command names an entity the
pack's own slots put in its reach at all.

## ADDED Requirements

### Requirement: A pack expresses declarative intent and never control flow

A pack SHALL declare, for each behaviour, a trigger, an optional condition and an
action drawn from the vocabulary's **declarative subset**, and SHALL NOT express
branching, iteration, ordering or variable assignment. The declarative subset
SHALL be published as data that *references* the vocabulary's terms — never as a
second copy of them and never as a list in the interpreter — and a pack using a
term published outside that subset SHALL fail validation, naming the pack, the
term, and the fact that the term is published but not declarative.

#### Scenario: A published control-flow term is refused as non-declarative

- **WHEN** a pack declares an action of `repeat` — a term
  `behavior-vocabulary/1.1.0.json` publishes
- **THEN** validation fails, naming the pack and `repeat`, and the message says
  the term is published but outside the declarative subset, so the author is not
  told the vocabulary lacks a term it has

#### Scenario: A template is refused because a template is an expression language

- **WHEN** a pack declares a `template` condition — published in
  `behavior-vocabulary/1.1.0.json` alongside `state`, `time` and `zone` — or a
  `wait_template` action
- **THEN** validation fails, naming the term and the reason: a template evaluates
  an expression over the house, which is the thing the declarative subset exists
  to exclude, and it is excluded for the same reason `variables` is. A condition
  that reads state is declarative; a condition that computes is not

#### Scenario: A term the vocabulary does not publish is refused as unknown

- **WHEN** a pack declares an action of `while_light_is_off`
- **THEN** validation fails, naming the pack and the term as absent from the
  published vocabulary — a different message from the one above

#### Scenario: The subset is the policy artifact's, not the interpreter's

- **WHEN** the declarative subset is needed by the validator, the CLI or a
  documentation surface
- **THEN** it is read from `catalog/pack-policy.yaml`, the artifact whose
  declarative-terms section names the vocabulary's terms a pack may not use, and
  adding a term to it is a change to that file rather than to engine code

#### Scenario: Branching cannot be expressed by composition

- **WHEN** a pack declares two behaviours whose conditions are logical negations
  of each other, aiming to reconstruct a branch
- **THEN** both are valid — the sandbox forbids the *expression* of control flow,
  and two declarative behaviours that each stand alone are not that expression

### Requirement: A command may name only an entity the pack's own slots put in its reach

The sandbox SHALL admit a command naming an entity only when that entity is
bound to a slot the pack declares — in `requires_slots` or `optional_slots`, and
named by the command's own behaviour in its `slots` clause — for the room the pack
is installed in. The behaviour's clause is what makes the reach unambiguous when
a pack declares several slots: without it, "the pack's slots" would be a set and a
command would name no member of it. A pack SHALL NOT be able to name an entity id, a device
id or a room literally; a pack that does SHALL fail validation, naming the pack
and the literal reference, because a literal is what makes a pack
non-portable and what makes it able to reach past its grant.

#### Scenario: A command through a bound slot is admitted

- **WHEN** a pack declaring `requires_slots: [light_group]` declares a behaviour
  whose `slots` names `light_group`, and that behaviour commands the entity bound
  to `light_group` in the room it is installed in
- **THEN** the command is admitted

#### Scenario: A command through a slot the behaviour does not name is refused

- **WHEN** a pack declares `light_group` and `motion_sensor`, and a behaviour
  whose `slots` names only `motion_sensor` commands the `light_group` entity
- **THEN** the refusal is the reach failure and not a service failure, because the
  pack declared the slot and this behaviour did not claim it

#### Scenario: A literal entity id is refused

- **WHEN** a pack's action names `light.kitchen_ceiling` rather than a slot
- **THEN** validation fails, naming the pack and the literal

#### Scenario: An unbound slot is not a way through

- **WHEN** a pack lists a slot in `optional_slots` and the house binds that slot
  nowhere in the room
- **THEN** the pack installs — the slot is optional — and any behaviour reaching
  through it is inert rather than admitted, because the sandbox binds reach to
  the entities the house actually supplied

### Requirement: Only services the manifest declared are callable

A manifest SHALL declare the services its behaviours may call, and the declared
set SHALL be the pack's effective permissions. The sandbox SHALL refuse a service
call the manifest did not declare, naming the pack, the service and the
declaration that would have admitted it. The declared set SHALL be computed from
the manifest's behaviour clauses rather than written a second time by hand, so
the declaration cannot disagree with the behaviours it describes.

#### Scenario: A declared service is admitted

- **WHEN** a pack whose manifest declares `light.turn_off` calls it
- **THEN** the call is admitted

#### Scenario: An undeclared service is refused

- **WHEN** a pack calls a service its declared set does not contain
- **THEN** validation fails, naming the pack, the service and the declaration
  that would have admitted it

#### Scenario: The declared set is derived and not restated

- **WHEN** a behaviour clause is added to a manifest without the declared set
  being edited
- **THEN** the effective permissions include the new service, because the set is
  computed from the behaviours — a hand-maintained second list would have gone
  stale here and this requirement exists to make that impossible

### Requirement: A service on the banned list is a refusal, and the list is a published artifact

The project SHALL publish a banned-service list as a data artifact, and a pack
whose behaviours call a service on it SHALL fail validation, naming the pack and
the service. The list SHALL contain services no legitimate pack needs — the ones
that would let a pack act on the system that hosts it rather than on the house —
and the exact membership SHALL be the design's to fix and the artifact's to
carry. No such list exists in the repository today, so this phase creates it; a
service SHALL NOT be bannable by a code change to the interpreter.

#### Scenario: A banned service refuses the pack

- **WHEN** a pack's behaviour calls a service the banned list publishes
- **THEN** validation fails, naming the pack and the service, and the message
  distinguishes a ban from an undeclared service

#### Scenario: Banning a service is a data change

- **WHEN** a service is added to the banned list
- **THEN** no engine code changes, and the next validation run refuses the packs
  that call it

#### Scenario: The list is bounded by an argument, not a vibe

- **WHEN** the banned list is reviewed
- **THEN** each entry carries the reason it is banned, and the reason is that the
  service acts on the host rather than on a device — a service a pack might
  plausibly want for a legitimate home behaviour is a red flag and not a ban

### Requirement: Dangerous permissions are red-flagged, not refused

A service that is dangerous but legitimate SHALL be recorded as a red flag on the
pack rather than refused, and installation SHALL surface the flags it found. The
flags SHALL be published as data alongside the banned list. The distinction SHALL
be observable: a pack whose only dangerous permission is flagged SHALL install,
and the same pack with a banned service SHALL not.

#### Scenario: A flagged pack installs and its flags are surfaced

- **WHEN** a pack calls an unlock, which is dangerous and legitimate, and no
  banned service
- **THEN** the pack installs and the install result names the flag

#### Scenario: Locking is not an unlocking

- **WHEN** a pack whose behaviours only *lock* is validated
- **THEN** no flag is raised, because the pair is judged by what a pack can do
  and not by the domain it touches — locking a door on a schedule is an ordinary
  pack and unlocking is the one that needs saying out loud

#### Scenario: A flag is not a refusal

- **WHEN** installation is asked for a pack carrying flags
- **THEN** it proceeds unless a ban or a conflict refuses it, so a flag can never
  silently become a ban through a code path that treats the two alike

### Requirement: A pack's `provides` paths resolve inside the pack and carry the declared class

Every `provides` entry SHALL name a path that the schema calls **repo-relative**
and that resolves to a file inside the pack's own directory, and the file SHALL
be of the class the entry declares, drawn from the schema's closed class enum. A
path that names a missing file, resolves outside the pack's directory by
traversal or by an absolute path, or declares a class the file is not SHALL fail
validation, naming the entry. The base is the repository root, which is the
schema's own wording — `$defs.provided.path` is described there as a "Repo-relative
path" and the shipped example's value is of that form — and the containment rule
is what makes a repo-relative path safe: resolving against the root is only
sound if the answer is still inside the pack. This is the class-pinning rule the
`provided` definition states in its own description — the class is what stops a
pack smuggling an artifact nobody can pin — and it is not enforced today.

#### Scenario: A dangling provides path is refused

- **WHEN** a pack's `provides` entry names a path no file occupies
- **THEN** validation fails, naming the entry and the path, and the shipped
  `packs/official/example-pack.yaml` — whose one entry names
  `packs/official/example_pack/motion_light.yaml`, which the repository does not
  contain — is caught by this and corrected in this change

#### Scenario: A path escaping the pack is refused

- **WHEN** a `provides` path resolves outside the pack's own directory, by
  relative traversal or by an absolute path
- **THEN** validation fails, naming the entry and the path

#### Scenario: A class the file is not is refused

- **WHEN** an entry declares class `blueprint` and the file is a scene
- **THEN** validation fails, naming the entry, the declared class and the class
  the file is

### Requirement: The sandbox is enforced at install, so a passing pack cannot exceed it

Sandbox checks SHALL run when a pack is validated and installed, and no check
SHALL depend on a runtime path that the interpreter could reach around. A pack
that validates and installs SHALL be unable to exceed its permissions *because
the interpreter has no facility to express the excess* — not because a runtime
guard declines it. A sandbox failure SHALL therefore be a validation failure
whose message names the pack, and never an incident in the decision path.

#### Scenario: A sandbox violation is a validation failure

- **WHEN** a pack names a literal entity or an undeclared service
- **THEN** the failure is reported by validation, before install, and the engine
  never sees the pack

#### Scenario: There is no interpreter feature to abuse

- **WHEN** the interpreter's capability surface is enumerated
- **THEN** it consists of resolving a slot to a bound entity and calling a
  declared service on it, and it has no branch, loop, variable or expression
  evaluation for a pack to reach through

### Requirement: The sandbox's vocabularies are read from artifacts, and the engine's layering holds

The banned-service list, the dangerous-permission flags and the declarative
subset SHALL be read through `engine/vocabulary.py`, the one module that opens a
frozen artifact, so the sandbox has no second definition of anything the project
publishes. The sandbox SHALL live in `engine/` and SHALL import nothing from
`sim/` or `openhouse/`, because the engine is the part that must run unchanged
against a real Home Assistant adapter in Phase 4, and a sandbox that reached into
the fake house would be a sandbox that only works on the fake house.

#### Scenario: The engine's layering holds

- **WHEN** the imports of the sandbox module are checked
- **THEN** it imports nothing from `sim/` or `openhouse/`, and a check fails if
  it does

#### Scenario: A vocabulary is read through the gateway

- **WHEN** the sandbox needs the banned list, the flags or the declarative subset
- **THEN** it obtains them from `engine/vocabulary.py` rather than opening the
  artifact itself, so the artifacts have one reader in the engine

#### Scenario: A missing artifact fails loudly

- **WHEN** an artifact the sandbox reads is absent
- **THEN** the failure names the missing path and is not reported as a pack
  validation failure, because a missing vocabulary is the checkout's fault and
  not the pack's
