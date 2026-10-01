# Spec Delta — pack-cli

## Purpose

The three verbs `spec.txt:60` names — "validate, test, diff-permissions" — and the
reason there are three rather than one. `validate` answers "is this pack
well-formed", `test` answers "does it do what it says", and `diff-permissions`
answers a question neither of the others asks: "what can this pack do that the
last one could not". The third is the one a person updating a pack needs and the
one that has no home in the other two, which is why the proposal fixes it as a
first-class output rather than a by-product of validation. An update that quietly
widens what a pack may do is the failure the verb exists to make visible, and a
widening that is *derivable* from two manifests should be a command a person runs
rather than a diff a person performs.

Two rules from Phase 1 carry into the verbs. The control surface has three faces —
CLI, library and MCP, `spec.txt:52` — and an operation available on one face and
not the others is the defect Phase 1's `control-surface` spec checks for, so the
three verbs are operations with three faces rather than CLI-only commands. And a
verb reports what it checked, because a validator that returns success for a pack
it never read is worse than one that fails: the failure classes below are
distinct so that "this pack is fine" and "this pack was not looked at" cannot be
the same output.

## ADDED Requirements

### Requirement: `validate` validates every pack in a directory and reports each failure with its class

`validate` SHALL accept a directory, find every pack manifest in it, validate each
against the current schema and the sandbox, and report per manifest the outcome
and every failure with the failing instance path. It SHALL exit non-zero when any
manifest fails. Each failure SHALL carry its **class** — schema, non-declarative
term, unknown term, literal entity, undeclared service, banned service, licence,
derivation, or a `provides` failure — because the classes have different
remedies and a single "invalid" collapses them. The list SHALL be the classes
this phase distinguishes and not a claim that its checks produce no others: a
structural failure that no listed class claims — a behaviour whose `slots` names
a slot the pack does not declare, an entry declaring a class the file is not —
SHALL still be reported with a class and its constraint named, rather than
arriving unlabelled.

#### Scenario: A clean directory validates

- **WHEN** `validate` runs over a directory of valid packs
- **THEN** every manifest is reported as valid and the exit status is zero

#### Scenario: One bad pack among good ones is named, not swallowed

- **WHEN** `validate` runs over a directory in which one manifest fails
- **THEN** the failing manifest is named with its instance path and class, the
  others are reported valid, and the exit status is non-zero

#### Scenario: An empty directory is not a success

- **WHEN** `validate` runs over a directory containing no manifest
- **THEN** it reports that it checked nothing and exits non-zero, so "no packs
  found" is never indistinguishable from "every pack is fine"

#### Scenario: The failure class is machine-readable

- **WHEN** the report is requested as JSON
- **THEN** each failure carries its class as a stable field alongside the path,
  so a caller can branch on the class without parsing the message

### Requirement: `test` runs a pack's scenarios and distinguishes untested from passing

`test` SHALL run the scenarios a pack ships against the fixture houses and report
each scenario's outcome, and a pack that ships no scenario SHALL be reported as
**untested** rather than as passing. A scenario that cannot run because its
fixture house does not bind the pack's required slots SHALL be reported as a
fixture error and not as a pack failure, because the two have different authors.

#### Scenario: A pack's scenarios run and report individually

- **WHEN** `test` runs over a pack with three scenarios
- **THEN** three outcomes are reported, each naming its scenario

#### Scenario: A pack with no scenario is untested, and that is not a pass

- **WHEN** `test` runs over a pack that ships no scenario
- **THEN** the pack is reported as untested, and the run does not report it as
  passing — the distinction is the point of the verb

#### Scenario: A fixture that cannot host the pack says so

- **WHEN** a scenario's fixture house binds none of the pack's required slots
- **THEN** the result names the fixture, the missing slot and the pack, and is
  reported as a fixture error

### Requirement: `test` reports a failure with the scenario's inputs and the decision log that resulted

A failing scenario SHALL be reported with the deterministic inputs that produced
it and the decision log the engine wrote, so a failure can be replayed and does
not have to be re-described by hand. The report SHALL be the same artefact the
scenario runner produces — a failure is not reformatted by the CLI, because two
formats for one failure is two claims about what happened.

#### Scenario: A failure arrives with its replay

- **WHEN** a scenario fails
- **THEN** the report carries the scenario's inputs and the decision log up to
  the failing tick, in the form the scenario runner emits

#### Scenario: A pass is quiet

- **WHEN** a scenario passes
- **THEN** the report names the scenario and does not carry a log, so a passing
  run is not padded with records nothing reads

### Requirement: `diff-permissions` compares two versions' effective permissions and is a first-class output

`diff-permissions` SHALL take two pack manifests — the same pack at two versions —
compute each one's effective permissions from its own manifest, and report the
**gained** and **lost** permissions between them. It SHALL report a gained
permission that is red-flagged as flagged and one that is banned as banned, and it
SHALL NOT require the pack to be installed, because the question is asked before
installation is decided. Its output SHALL exist independently of `validate`:
running the diff SHALL NOT require a validation run and a validation run SHALL NOT
emit a diff unless asked.

#### Scenario: A widened permission set is visible

- **WHEN** version 1.1.0 of a pack declares a service 1.0.0 did not
- **THEN** the diff reports the service as gained, and names the behaviour clause
  that declares it

#### Scenario: A narrowed set is reported too

- **WHEN** a service is declared by 1.0.0 and not by 1.1.0
- **THEN** the diff reports it as lost, so the verb describes a change and not
  only a regression

#### Scenario: A gain is classified before it is installed

- **WHEN** the gained service is red-flagged
- **THEN** the diff marks the gain flagged; **WHEN** the gained service is
  banned, the diff marks it banned and the pack would fail validation

#### Scenario: The diff runs without an install

- **WHEN** `diff-permissions` is asked about two manifests in a directory
- **THEN** it reports the difference without a house, an install or a validation
  run

### Requirement: The three verbs are operations on all three control-surface faces

`validate`, `test` and `diff-permissions` SHALL each be reachable from the CLI,
the library and the MCP server, with the same inputs and the same report, so no
verb is CLI-only. A verb whose report differs between faces SHALL fail the
control-surface check that already requires a Phase 1 operation's `scope` note to
be reachable from every face.

#### Scenario: The same verb on each face reports the same thing

- **WHEN** `validate` is invoked through the CLI, the library and the MCP server
  for one directory
- **THEN** the three reports are equivalent, and a difference fails the check

#### Scenario: A CLI-only verb fails the check

- **WHEN** a verb is added to the CLI and not to the library and the MCP server
- **THEN** the control-surface check fails, naming the verb and the faces it is
  missing from

### Requirement: The verbs distinguish success, a validation failure and a usage error

Each verb SHALL exit with a status that distinguishes success, a pack that failed
its checks, and a usage error such as a path that does not exist or an unreadable
argument, so a caller can tell "this pack is broken" from "I called this wrong".
A usage error SHALL NOT be reported as a pack failure, and a pack failure SHALL
NOT be reported as success.

#### Scenario: A missing path is a usage error

- **WHEN** `validate` is given a path that does not exist
- **THEN** the status is a usage error, distinct from a pack failure, and no pack
  is reported as invalid

#### Scenario: A broken pack is a pack failure

- **WHEN** a manifest fails validation
- **THEN** the status is a pack failure and the usage is reported as correct

#### Scenario: A verb never reports a pack it did not read

- **WHEN** a verb skips a manifest for any reason
- **THEN** it says so, and the skipped manifest is not counted as passing
