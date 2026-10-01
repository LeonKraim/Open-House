# Spec Delta — pack-manifest

## Purpose

The pack document and what it is allowed to say. A pack is portable because it
names **placeholders, never a house, a room or an entity**: `requires_slots` is
what it must be plugged into, `optional_slots` what it may use if the house has
it, and both are names `catalog/slots.yaml` declares. Phase 0 froze that idea at
`schemas/pack-manifest/1.0.0.json` and `1.1.0.json`; this capability is the
version that carries everything `spec.txt`'s Phase 2 requires of a pack, which is
the part the frozen schemas deliberately left open.

Three things shape every requirement below. **The schema is the contract, not
this document**: a manifest is valid because it validates, so the requirements
here are about what the *schema* must carry and what a validator must do with it,
and a term this spec names is always a reference to a frozen artifact rather than
a restatement of one — the same device `1.1.0` uses when it points `behaviours`
at `behavior-vocabulary/1.1.0.json` instead of copying the vocabulary in.
**Superseded is not invalid**: `1.2.0` supersedes `1.1.0`, both stay on disk, and
an *install* validates against the current version only, so a schema change is a
deliberate act with a visible consequence rather than a silent re-reading of
documents already written. **The licence gate is about provenance, not
politeness**: the corpus is 83 rows of which **19 are `reusable`** (10 `mit`, 9
`apache_2_0`) and **64 are `ideas_only` with no licence at all**, so a derived pack
is bounded by what it derives from, and that bound is a validation rule rather
than an editorial habit.

## ADDED Requirements

### Requirement: A pack is a manifest that validates against the current schema version

A pack SHALL be a single YAML document that validates against the **current**
`pack-manifest` schema (`schemas/pack-manifest/1.2.0.json`) and against every
schema it references. A document that does not validate SHALL be refused, naming
the failing instance path and the constraint it violated. No pack SHALL be
installed, executed or trusted on the strength of having validated against a
superseded schema version.

#### Scenario: A manifest valid against the current schema validates

- **WHEN** a manifest is validated that carries every clause `1.2.0` requires
- **THEN** validation succeeds and the manifest is accepted as a pack

#### Scenario: A manifest missing a required clause is refused, naming it

- **WHEN** a manifest omits `provides`, which `1.2.0` requires
- **THEN** validation fails, the message names `provides`, and the manifest is
  not accepted

#### Scenario: A manifest valid only against a superseded version is refused

- **WHEN** a document that satisfies `1.1.0` but not `1.2.0` — for example one
  whose `kind` is outside the closed set — is offered for install
- **THEN** it is refused, and the message names the current schema version it was
  checked against and the clause that supersedes it

### Requirement: The schema version chain is explicit and closed

`schemas/pack-manifest/` SHALL contain the current version, each superseded
version it replaced, and each version SHALL carry `supersedes` naming its
predecessor, so the chain is read rather than assumed. The chain this phase
continues is `1.0.0 → 1.1.0 → 1.2.0`: `1.1.0` already declares
`supersedes: 1.0.0` and added `kind`, `optional_slots` and `behaviours` to the
five clauses `1.0.0` fixed, so a version bump is a practice the phase follows
rather than one it introduces. A bump SHALL be a new file: no published schema
SHALL be edited in place, because a pack that validated yesterday must be
re-checkable against the schema it was written for.

#### Scenario: Every published version supersedes its predecessor

- **WHEN** `schemas/pack-manifest/` is read
- **THEN** `1.2.0` names `1.1.0`, `1.1.0` names `1.0.0`, `1.0.0` names nothing,
  and the chain has exactly one head — the current version

#### Scenario: A published schema is immutable

- **WHEN** the current schema is amended in place rather than superseded
- **THEN** the change is rejected by the check that compares each published
  schema's digest against the recorded one

### Requirement: `kind` is closed to the five kinds the phase names

`kind` SHALL be an enum with exactly five values — `module`, `room-template`,
`behavior`, `profile-set`, `house-template` — and a manifest whose `kind` is not
one of them SHALL be refused, naming the value and the accepted set. This closes
a deferral that `1.1.0` states in its own words: *"A pattern and not an enum
because no corpus of packs exists to derive a closed set of kinds from, and
inventing one here would freeze..."* — the corpus is this phase's, so the closure
is this phase's.

#### Scenario: The five kinds are the accepted set

- **WHEN** a manifest declares each of the five kinds in turn
- **THEN** all five validate

#### Scenario: A category is refused where a kind is required

- **WHEN** a manifest declares `kind: lighting`, a corpus *category* rather than
  a kind
- **THEN** validation fails, naming `lighting` and listing the five accepted
  kinds — and the shipped `packs/official/example-pack.yaml`, which does exactly
  this, is corrected in this change

#### Scenario: The enum is the schema's, not the validator's

- **WHEN** the accepted kinds are read from the schema rather than from the
  validator
- **THEN** adding a sixth kind requires a schema version bump and not a code
  change

### Requirement: Each kind declares its own required clauses

`1.2.0` SHALL carry a per-kind conditional — for each kind, the clauses a
manifest of that kind must carry and the clauses it may not — so that a
`house-template` and a `module` are the same document shape only where they
genuinely are. A manifest whose declared clauses contradict its kind SHALL be
refused, naming the kind, the clause and the kind that clause belongs to.

#### Scenario: A clause belonging to another kind is refused

- **WHEN** a manifest of kind `module` carries a clause `1.2.0` admits only for
  `house-template`
- **THEN** validation fails, naming the clause and the kind that admits it

#### Scenario: The clauses a kind requires are the schema's, read not inferred

- **WHEN** the required clauses for a kind are needed by a validator, a CLI or a
  documentation surface
- **THEN** they are read from `1.2.0`'s conditional for that kind, and no list of
  them is maintained in code

### Requirement: `1.2.0` widens the behaviour clause with the priority, the services and the slots a behaviour declares

`1.1.0`'s `$defs.behaviour` is closed — `name`, `trigger`, `condition`, `action`,
`additionalProperties: false` — and carries neither the priority arbitration
ranks a competing behaviour by, nor the services the behaviour may call, nor the
slot it reaches entities through. `1.2.0`
SHALL add three: `priority`, an integer a behaviour declares and whose omission
takes the default `catalog/pack-policy.yaml` publishes; `services`, the services
that behaviour may call and from which the pack's effective permission set is
computed; and `slots`, the pack-declared slots that behaviour reaches entities
through — which a trigger also reads, so that a physical button is reached
through a *named* slot rather than through whichever one a pack with several
happened to bind. None SHALL be
readable from anywhere but the manifest, so a pack's permissions, its reach and
its competing rank are clauses of the pack and not properties of its file name,
its directory or the interpreter.

#### Scenario: A declared priority, services and slots validate

- **WHEN** a behaviour declares `priority: 10`, `services: [light.turn_off]` and
  `slots: [light_group]`
- **THEN** it validates under the widened clause

#### Scenario: A behaviour that declares none of them takes the defaults

- **WHEN** a behaviour declares none of the three clauses
- **THEN** it takes the published default priority, an empty service set and no
  slot of its own, so a manifest may be minimal and is not required to state what
  the default says

#### Scenario: A slot the pack does not declare is refused

- **WHEN** a behaviour declares in `slots` a slot the pack's `requires_slots` and
  `optional_slots` do not contain
- **THEN** validation fails, naming the behaviour and the slot, because a
  behaviour cannot reach through a slot the pack never declared

#### Scenario: A service outside the declared set is still undeclared

- **WHEN** a behaviour's action calls a service its own `services` clause omits
- **THEN** the sandbox refuses it, because the clause is the declaration of
  permissions and not a comment on them

#### Scenario: The clause is closed against a second spelling

- **WHEN** a manifest carries the priority, the service list or the slot list
  under a key `1.2.0` does not name
- **THEN** validation fails under `additionalProperties: false`, so a manifest
  cannot carry a permission the sandbox does not read

### Requirement: `1.2.0` extends the provided-artifact class enum with `mode`

`1.1.0`'s `$defs.provided.class` is a closed ten-value enum — `package`,
`automation`, `script`, `scene`, `helper`, `template`, `blueprint`, `dashboard`,
`custom_integration`, `other` — and carries no class for a **mode**, which is
what a `profile-set` pack confers: guest mode is a mode with an
`exclusive_group`, and without a class for it the pack that `spec.txt:58` requires
could not state what it provides, because the class-pinning rule admits only the
declared classes. `1.2.0` SHALL add `mode` to that enum, and the file an entry
pins SHALL validate against `schemas/mode/1.0.0.json`, which already fixes a
mode's `name`, `description` and `exclusive_group`. `profile` SHALL NOT be added
beside it, and the omission is deliberate rather than an oversight:
`schemas/profile/1.0.0.json` does exist and is current, but what it fixes —
`name`, `description`, `modes`, `enabled_behaviours`, described there as "the
'premade and preintegrated' idea applied to a whole house rather than one button"
— is a *bundle a house activates*, which is the thing `spec.txt:65` puts in
Phase 3 with the profiles that bundle room-profile selections and the activation
rules, hysteresis and dwell that drive them. What the guest pack contributes in
this phase is the state a behaviour is gated on, which is what a mode is and what
Phase 1's `engine/modes.py` already enforces exclusivity over; a `profile` class
would let a Phase 2 pack ship a bundle whose activation this phase does not
implement.

#### Scenario: A pack confers a mode

- **WHEN** a `profile-set` pack declares a `provides` entry of class `mode` whose
  path resolves to a file validating against `schemas/mode/1.0.0.json`
- **THEN** it validates, and the mode it confers is read from that file

#### Scenario: A mode file that is not a mode is refused

- **WHEN** an entry declares class `mode` and the file does not carry the mode
  schema's required fields
- **THEN** validation fails, naming the entry and the missing field, because the
  class pin is what stops a pack conferring an artifact of a class it did not
  name

#### Scenario: The other ten classes are unchanged

- **WHEN** the enum is compared with `1.1.0`'s
- **THEN** the ten values are present, in their order, with `mode` added, and no
  value has been removed or renamed — a class removed would invalidate every pack
  that pinned it

### Requirement: Dependencies and conflicts are declared as name and version range

A manifest SHALL be able to declare `dependencies` — each an installed pack's
name and a semver range its version must satisfy — and `conflicts`, each a pack
name and a range it must not satisfy. A pack SHALL NOT depend on itself, and a
set of packs whose dependency graph contains a cycle SHALL be refused, naming the
cycle. Dependency and conflict clauses SHALL be resolved against the set of packs
installed in the house, not against the repository.

#### Scenario: A satisfied dependency is recorded

- **WHEN** a pack requiring `guest_mode >=1.2.0 <2.0.0` is installed in a house
  holding `guest_mode 1.4.2`
- **THEN** installation proceeds and the dependency it was satisfied by is
  recorded with the install

#### Scenario: An unsatisfied dependency refuses the install

- **WHEN** the same pack is installed in a house holding `guest_mode 1.0.0`
- **THEN** installation is refused, naming the pack, the required range and the
  version found

#### Scenario: A conflicting pack refuses the install

- **WHEN** a pack declares a conflict with a range satisfied by an installed pack
- **THEN** installation is refused, naming both packs and the range

#### Scenario: A dependency cycle is refused

- **WHEN** pack A depends on B and B depends on A
- **THEN** the install is refused and the message names the cycle rather than
  reporting one unsatisfiable dependency of the two

### Requirement: `engine_api` is a semver range checked against the engine's declared version

A manifest SHALL declare `engine_api` as a semver range, and installation SHALL
check it against the engine's own declared API version. A manifest whose range
excludes that version SHALL be refused, naming the range and the version. The
version SHALL be published where a caller and a validator read it without
importing engine internals, and it SHALL NOT be `pyproject.toml`'s `version`,
which is `0.0.0` and is a packaging value rather than an API promise.

`engine_api` supersedes `min_engine_version`, which `1.0.0` and `1.1.0` carry as
a lower-bounded exact-version string. A pack that said `min_engine_version:
"2.0.0"` meant "2.0.0 or later" and could say nothing else, so a pack broken by
3.0.0 could not state its own upper bound; `engine_api` is a range because the
clause it replaces had no way to express one, and `1.2.0` retires it rather than
carrying two clauses that answer the same question differently.

#### Scenario: A range containing the engine's version installs

- **WHEN** a manifest declares `engine_api: ">=2.0.0 <3.0.0"` and the engine
  declares its API version as `2.1.0`
- **THEN** the check passes and installation proceeds

#### Scenario: A range excluding the engine's version refuses

- **WHEN** a manifest declares `engine_api: ">=1.0.0 <2.0.0"` and the engine
  declares `2.1.0`
- **THEN** installation is refused, naming the declared range and the engine's
  version, and distinguishing this refusal from a schema failure

#### Scenario: The retired clause is refused where the current one belongs

- **WHEN** a manifest declares `min_engine_version: "2.0.0"` and no `engine_api`
- **THEN** validation fails under `1.2.0` — whose clauses are closed, both
  schemas declare `additionalProperties: false` — naming `min_engine_version` as
  retired and `engine_api` as the range that replaced it

#### Scenario: A missing or malformed range fails validation, not the check

- **WHEN** a manifest omits `engine_api`, or declares `engine_api: "two-ish"`
- **THEN** validation fails under the schema and the range check is never
  reached — the failure is reported as a schema failure and not as an
  incompatibility

### Requirement: A pack declares a licence from the published vocabulary, which carries an SPDX identifier

A manifest SHALL declare `license` as one of the codes
`schemas/catalog/licenses.json` publishes — `public_domain`, `mit`,
`apache_2_0`, `cc_by_nc_sa`, `no_licence` — and a code outside that enum SHALL be
refused, naming it. `spec.txt:56` asks for an SPDX licence, and this change
satisfies that by giving each published code its SPDX identifier **in that
artifact** rather than by introducing a second list of identifiers: the corpus
already spells a licence as `mit` and `apache_2_0`, so a manifest that said `MIT`
would be a third spelling of one fact. The licence SHALL be a property of the
pack document rather than a field inferred from the repository the pack lives in,
because a pack is portable and its provenance stops travelling with it.

#### Scenario: A published code validates and carries its SPDX identifier

- **WHEN** a manifest declares `license: mit`
- **THEN** it validates, and the SPDX identifier the artifact records for `mit`
  is what a consumer reports

#### Scenario: A code outside the enum is refused

- **WHEN** a manifest declares `license: Free-To-Use-4U`, or `MIT` in the SPDX
  spelling rather than the published code
- **THEN** validation fails, naming the value and the five published codes

#### Scenario: The restrictive codes are the ones a pack cannot derive through

- **WHEN** a pack's code is `cc_by_nc_sa` or `no_licence`
- **THEN** it is a valid manifest licence and it may not be the licence of a pack
  derived from a row, because the derivation gate is about what a source grants
  and not about what a pack asserts

### Requirement: A derived pack names the corpus rows it derives from, and its licence must permit them

A derived pack SHALL carry a `derives_from` clause naming the corpus row ids it
derives from — the clause travels with the pack, because provenance that lives
only in a report on the machine that ran the derivation stops travelling the
moment the pack is copied. `1.2.0` SHALL carry that clause as an array of row
ids, and each named row's `reuse_status` and `license` SHALL be read from the
corpus. A row whose `reuse_status` is `ideas_only` SHALL NOT be named as the
source of reproduced expression; and a derived pack's `license` code SHALL be no
more restrictive than each named row's, judged in the order
`schemas/catalog/licenses.json` publishes — a more restrictive code SHALL be
refused, because claiming a narrower licence over expression than its source
grants is a claim the source does not support.

`derives_from` is what distinguishes the two trees and the marker:
a pack listed in `packs/official/HANDWRITTEN` SHALL NOT carry it, because a file
a person wrote reproduces no row's expression; a pack the derivation produces
SHALL carry it and SHALL NOT be listed there. The requirement is bounded by a
measured fact and not by intent: the corpus holds **19 `reusable` rows (10 `mit`,
9 `apache_2_0`) and 64 `ideas_only` rows whose `license` is `no_licence`**, so at
most those 19 rows can ground a derived pack.

#### Scenario: A derived pack over reusable rows is accepted

- **WHEN** a pack names only rows whose `reuse_status` is `reusable` and whose
  licence its own code is compatible with
- **THEN** the derivation gate passes and the named rows are recorded with the
  pack, in its own `derives_from` clause

#### Scenario: A hand-written pack carries no derivation clause

- **WHEN** a pack listed in `packs/official/HANDWRITTEN` carries `derives_from`
- **THEN** validation fails, naming the pack and the clause, because a file a
  person wrote reproduces no row's expression and a derivation clause on one is
  a claim about provenance that is false

#### Scenario: A derived pack names at least one row and names it correctly

- **WHEN** a pack carries an empty `derives_from`, or names an id no corpus row
  has
- **THEN** validation fails, naming the clause and the id — an empty list is not
  a derivation and a misspelled id is not a row

#### Scenario: A derived pack over an ideas-only row is refused

- **WHEN** a pack names a row whose `reuse_status` is `ideas_only`
- **THEN** the derivation is refused, naming the row and its status, and the
  message states that the row may inform a hand-written pack but may not be a
  source of reproduced expression

#### Scenario: A more restrictive licence is refused

- **WHEN** a pack derived from a row whose code is `mit` declares
  `cc_by_nc_sa`, which the published order places as more restrictive
- **THEN** the derivation is refused, naming the pack's code, the row's code and
  the order the comparison used

#### Scenario: The corpus is the source of the status, not the manifest

- **WHEN** a manifest asserts a row is reusable and `catalog/behaviors.yaml` says
  `ideas_only`
- **THEN** the corpus governs and the derivation is refused

### Requirement: Every user-visible string has a default, and locales resolve over it

A manifest SHALL carry an `i18n` block giving a default string for every
user-visible name it declares — the pack's name and description, and each
declared behaviour's — and MAY carry per-locale overrides. Resolution SHALL fall
back to the default when a locale has no override, and a manifest that carries
an override without the default it overrides SHALL be refused.

#### Scenario: A locale without an override falls back to the default

- **WHEN** a pack's strings are read in a locale it does not override
- **THEN** the default string is returned

#### Scenario: An override wins where it exists

- **WHEN** a pack overrides one behaviour's name for a locale
- **THEN** that locale reads the override and every other string reads the
  default

#### Scenario: An override without a default is refused

- **WHEN** a manifest carries a locale override for a string whose default is
  absent
- **THEN** validation fails, naming the string and the locale

### Requirement: Vocabularies are referenced, never restated

A manifest's behaviour terms — trigger, condition and action — SHALL resolve
against the published `behavior-vocabulary/1.1.0.json` by `$ref`, and a term
outside that vocabulary SHALL fail validation, naming the pack and the term. The
vocabulary SHALL NOT be copied into the manifest schema, into a validator, or
into documentation, so that the vocabulary has one published form.

#### Scenario: A term outside the vocabulary is refused

- **WHEN** a behaviour declares a trigger the vocabulary does not publish
- **THEN** validation fails, naming the pack and the term

#### Scenario: The vocabulary is the only copy

- **WHEN** the set of admissible terms is compared between the schema and the
  vocabulary artifact
- **THEN** the schema contributes no terms of its own — it references the
  vocabulary and every term resolves there

### Requirement: A pack's identity is its name and version, and versions are semver

A pack SHALL declare a `name` of the lower-snake-case form the schema fixes and a
`version` in semver, and the pair SHALL be its identity: two documents with the
same name are the same pack at two versions, and installing a second version
SHALL be an upgrade or a downgrade of that pack rather than a second pack beside
it. A version that is not semver SHALL be refused, naming it.

#### Scenario: A non-semver version is refused

- **WHEN** a manifest declares `version: "1.2"` or `version: latest`
- **THEN** validation fails, naming the version as not semver

#### Scenario: A second version of the same name is the same pack

- **WHEN** `example_pack 1.0.0` is installed and `example_pack 1.1.0` is
  installed over it
- **THEN** the house holds one pack of that name at the higher version, and the
  install records the change rather than two packs

### Requirement: The shipped example pack is valid under the current schema

`packs/official/example-pack.yaml` SHALL validate against `1.2.0` and SHALL
declare one of the five kinds. Its current `kind: lighting` is a corpus category
and is corrected in this change; the example SHALL keep its purpose — a
hand-written manifest proving a person can write to the schema, as opposed to a
generated one proving only that the generator and the schema agree.

#### Scenario: The shipped example validates

- **WHEN** the pack CLI validates `packs/official/example-pack.yaml` against the
  current schema
- **THEN** validation succeeds and the declared kind is one of the five

#### Scenario: The example is listed as hand-written, and the listing is checked both ways

- **WHEN** the `handwritten-examples` check reads `packs/official/`
- **THEN** every pack file in the directory is named in `packs/official/
  HANDWRITTEN` and every name there is a file that exists, so an example cannot
  arrive unlisted or a listed one rot away while the check stays green

#### Scenario: The marker is a marker and not detection, and the requirement says so

- **WHEN** the provenance of a generated file is asked about
- **THEN** the answer is that a generator which declines to list its own output
  in `HANDWRITTEN` is not caught — the marker file states this itself, and the
  requirement does not claim more for the marker than the marker claims for
  itself
