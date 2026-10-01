# Spec Delta — pack-install

## Purpose

The lifecycle: what happens to a house when a pack arrives, and what happens to
the pack when the house changes under it. Phase 1 shipped `install_pack` at a
scope it stated in full — a manifest is validated against the current schema, its
`requires_slots` are resolved through `catalog/slots.yaml` against the house, and
nothing else happens — and named this phase as the one that completes it. Four
things complete it: the clauses a manifest may now state (dependencies,
conflicts, an `engine_api` range), the record of what was installed, the
operations that change an installed pack's state, and the exit criterion.

That criterion is the reason this capability is the phase's centre rather than a
step in it. **Two modules cannot fight over one light** is already half-built:
Phase 1's `engine/arbitration.py` reduces a tick's proposals to one command per
entity by a *total order* — a user action outranks every behaviour, behaviours
rank by declared priority, ties break by ascending behaviour `id` — and its own
docstring says why, citing this phase's exit criterion by name and rejecting
last-writer-wins because the outcome would depend on the order the evaluation
loop happened to visit behaviours in. Phase 2's obligation is to keep that
property true when the two proposals come from two *installed packs* rather than
two behaviours of one: installing the same two packs in the opposite order must
produce the same decision log, and that is a property a test can attack rather
than a claim a document can make.

Two rules survive this phase untouched and are restated here so that the
completion of `install_pack` is not read as licence to change them. **Installation
is not activation** — `product-invariants` fixes it, and a pack that arrives with
six behaviours arrives with six disabled behaviours. And **the house is the unit
of resolution**: dependencies and conflicts are answered against what is installed
in *this* house, never against what the repository or the store offers.

## ADDED Requirements

### Requirement: An install resolves dependencies and refuses conflicts against the installed set

Installation SHALL resolve each declared dependency against the packs installed in
the house, SHALL refuse the install when a dependency's range is unsatisfied or a
conflict's is satisfied, and SHALL name what it refused and the pack that caused
it. Resolution SHALL consult the house and not a repository, a store or a
directory listing, so a pack that installs here is installable wherever the same
packs are installed.

#### Scenario: A satisfied dependency is written into the record

- **WHEN** a pack requiring `guest_mode >=1.2.0 <2.0.0` installs in a house
  holding `guest_mode 1.4.2`
- **THEN** the install succeeds and the installed record names the dependency and
  the version that satisfied it

#### Scenario: An unsatisfied dependency refuses, naming both sides

- **WHEN** the same pack installs in a house holding `guest_mode 1.0.0`
- **THEN** installation is refused, naming the pack, the required range, the
  version found, and the pack that carries it

#### Scenario: An installed pack's conflict refuses the arriving pack

- **WHEN** an installed pack declares a conflict the arriving pack's range
  satisfies
- **THEN** installation is refused, naming the arriving pack, the installed pack
  and the range — a conflict is symmetric and is checked from both sides

### Requirement: A pack whose `engine_api` range excludes the engine is refused

Installation SHALL check the manifest's `engine_api` range against the engine's
declared API version and SHALL refuse a pack whose range excludes it, naming the
range and the version. This refusal SHALL be distinguishable from a schema
failure and from a dependency failure, because a range mismatch is a pack that
was written for another engine and an author has to be able to tell that apart
from a pack that was written wrong.

#### Scenario: An excluding range refuses with its own message

- **WHEN** a pack declares `engine_api: ">=1.0.0 <2.0.0"` and the engine
  declares `2.1.0`
- **THEN** installation is refused, naming both versions, and the failure is
  reported as an incompatibility rather than as a validation error or an
  unsatisfied dependency

#### Scenario: The check runs before the pack is recorded

- **WHEN** a pack's range excludes the engine
- **THEN** nothing about the pack is written to the house, and a subsequent
  install of a compatible pack behaves as though the refused one never arrived

### Requirement: Installation is not activation, and this phase does not change it

A successful install SHALL leave every behaviour the pack declares disabled, and
enabling SHALL be a separate operation that names what it enables. Installation
SHALL NOT run a behaviour, schedule one, or mark one enabled as a side effect of
arriving. This is `product-invariants`' rule and the phase SHALL NOT weaken it:
the reason it matters more here than in Phase 1 is that a pack is third-party
data, so "arriving" and "acting" being two acts is the difference between
installing a pack and being acted on by one.

#### Scenario: A pack with six behaviours arrives with six disabled behaviours

- **WHEN** a pack declaring six behaviours installs successfully
- **THEN** the house holds the pack and six behaviours, every one of them
  disabled, and no command has been proposed

#### Scenario: Enabling is a separate, named act

- **WHEN** a behaviour is enabled
- **THEN** the enabling names the behaviour, and a pack's arrival alone never
  produces that act

#### Scenario: The decision log is empty of the pack's doing

- **WHEN** a pack is installed and the session is advanced a tick without an
  enabling act
- **THEN** the pack contributes no proposal, and the tick's log carries no record
  attributable to it

### Requirement: An install records what it installed, and the record is readable

A successful install SHALL write a record naming the pack's name and version, the
digest of the manifest it validated, the slots it bound and the entities the house
supplied for them, the dependencies that satisfied it, and the red flags it
raised. The record SHALL be obtainable through the control surface, so
"what is installed here, and why" is answerable from the house rather than from
the file a person remembers installing.

#### Scenario: The record answers what and why

- **WHEN** the installed packs are read back
- **THEN** each carries its name, version, the entities its slots bound, the
  dependencies that satisfied it and its flags

#### Scenario: The digest is the manifest that was validated

- **WHEN** a pack's manifest is edited after install without a version change
- **THEN** the stored digest no longer matches the file, and the mismatch is
  reportable — the version says no change and the digest says otherwise

#### Scenario: A refused install writes no record

- **WHEN** an install fails for any reason
- **THEN** no record is written, so the installed set never contains a pack that
  is not fully there

### Requirement: Installing two packs in either order produces the same decision log

The engine SHALL reduce commands proposed by two installed packs in one tick to
exactly one command per entity by Phase 1's total order, and the outcome SHALL
depend on the proposals and not on the order the packs were installed. Installing
the same two packs in the opposite order and replaying the same inputs SHALL
produce the same decision log. No tie SHALL be broken by install order, by pack
name, or by anything that a different installation sequence would change.

#### Scenario: Two packs commanding one light produce one command

- **WHEN** two installed packs each propose a command for the same entity in one
  tick
- **THEN** exactly one command reaches the entity, and the record names both the
  winner and the losers

#### Scenario: The winner is determined by the order, not the installation

- **WHEN** the same two packs are installed in the opposite order and the same
  tick is replayed
- **THEN** the same command wins, and the two decision logs are identical

#### Scenario: A user action still outranks both

- **WHEN** the user commands the entity in the same tick as two packs
- **THEN** the user's command wins, and both packs are recorded as losers

#### Scenario: A deterministic tie between two packs

- **WHEN** two packs propose for one entity at equal priority
- **THEN** the tie breaks by the ascending behaviour `id` Phase 1 fixed, so a
  replay of the same tick names the same winner, and the losing pack is recorded
  rather than dropped silently

### Requirement: A pack's arbitration priority is declared and not inferred

A behaviour that competes for an entity SHALL carry the priority its manifest
declares, and the engine SHALL NOT infer a priority from the pack's kind, its
name, its install order or the order its behaviours were evaluated. A behaviour
without a declared priority SHALL take the manifest's default, which SHALL be
published, so that two authors comparing packs compare a stated number.

#### Scenario: A declared priority orders the two

- **WHEN** two packs propose for one entity with different declared priorities
- **THEN** the higher priority wins, whatever order they were installed in

#### Scenario: The default is published and not a code constant

- **WHEN** a behaviour omits a priority
- **THEN** it takes the published default, and that default is readable from the
  artifact rather than from the arbitration module

### Requirement: Uninstall removes the pack and refuses to strand a dependent

Uninstalling SHALL remove the pack's record, its behaviours and its contributions,
and SHALL refuse when another installed pack depends on it, naming the dependents
rather than removing the pack and leaving them unsatisfied. Uninstalling a pack
with no dependents SHALL leave the house as though the pack had never arrived —
no behaviour, no record, no bound slot, and no residue in the decision log's
later ticks.

#### Scenario: A depended-on pack refuses to uninstall

- **WHEN** pack B, which depends on pack A, is installed and A is uninstalled
- **THEN** the uninstall is refused, naming B as the dependent

#### Scenario: An uninstall leaves no residue

- **WHEN** a pack with no dependents is uninstalled
- **THEN** its behaviours no longer appear, its record is gone, and a tick after
  the uninstall carries no proposal of its

#### Scenario: Uninstall is not a disable

- **WHEN** a pack is uninstalled
- **THEN** the act is distinguished from disabling its behaviours, so a person
  who disabled a pack's behaviour does not discover that uninstalling it was the
  same act, or the reverse

### Requirement: Installing another version of a pack is a change to that pack

Installing a pack whose name matches an installed pack's SHALL be an upgrade or a
downgrade of that pack rather than a second pack beside it, and SHALL be recorded
as a change. A downgrade that would leave another installed pack's dependency
unsatisfied SHALL be refused, naming the dependent and the range it needs. The
change SHALL NOT activate the new version's behaviours, because installation is
not activation applies to a version change as it does to a first install.

#### Scenario: An upgrade replaces rather than accumulates

- **WHEN** `example_pack 1.1.0` installs over `example_pack 1.0.0`
- **THEN** the house holds one pack of that name, at `1.1.0`, and the record
  names the change

#### Scenario: A breaking downgrade is refused

- **WHEN** a downgrade would leave an installed pack's dependency unsatisfied
- **THEN** the downgrade is refused, naming the dependent and the range it needs

#### Scenario: An upgrade does not switch anything on

- **WHEN** a pack is upgraded to a version declaring new behaviours
- **THEN** the new behaviours are disabled, as the old ones were

### Requirement: A failed install leaves the house unchanged

Installation SHALL be all-or-nothing: a failure at any check SHALL leave no
record, no bound slot, no behaviour and no partial state, so a house can never
hold a pack that nothing can see and nothing can remove. The order in which the
checks run SHALL be observable only through which failure is reported first, never
through what the house holds afterwards.

#### Scenario: A failure late leaves nothing

- **WHEN** an install fails at the last of its checks
- **THEN** the house is byte-for-byte what it was before, and a later install of
  a valid pack is unaffected

#### Scenario: A refused pack is not half-removable

- **WHEN** the installed packs are listed after a refused install
- **THEN** the refused pack does not appear, so there is nothing for an uninstall
  to find
