# Spec Delta — configuration-schemas

## Purpose

The frozen, versioned schema set that is the single source of truth for rooms,
slots, houses, packs, the behaviour vocabulary, modes, profiles and export,
plus the example house and pack that prove the schemas usable.

## ADDED Requirements

### Requirement: One schema home, and a schema for every concept
`schemas/` SHALL be the only home for schemas in this repository, and a runtime
schema SHALL live at exactly the path `schemas/<concept>/<schema_version>.json`.
It SHALL contain exactly one **current** version per runtime concept, and no
concept SHALL be expressible without one: `room-type`, `slot`, `house`,
`pack-manifest`, `behavior-vocabulary`, `mode`, `profile`, `export-document`.
Per-version filenames are forced rather than stylistic: the requirement that
exactly one version exist and the requirement that a superseded version remain
present are only compatible if each version is its own file.

Succession points **backwards**, from the new file to the old, and nothing is
ever written into a file that already exists. A version file carries
`supersedes: <version>` when it replaces one and `supersedes: null` when it is
the first for its concept; a version is **current** when no other version
present for that concept names it in `supersedes`. The pointer cannot run the
other way — a `superseded_by` field would have to be written into the version
being retired, which is a content change to a frozen file, and the immutability
rule below would fire on the very act of publishing its successor. Each schema
SHALL carry a `$id`, a `title` and a `schema_version`.

Schemas for the catalog's own data files SHALL live under `schemas/catalog/`,
and where a catalog data file describes a runtime concept it SHALL reference the
runtime schema rather than restating it. **`schemas/catalog/` is exempt from
immutability and may be revised in place.** It describes this repository's own
intermediate artifacts, which nothing outside this repository consumes; freezing
it would buy no contract for anyone, and would turn every later task that adds a
field to a catalog file into a version bump. The published contract, and the
only thing the immutability rule below governs, is the eight runtime schemas.

The conformance check below draws its scope around **runtime version
directories** — `schemas/<concept>/<schema_version>.json` — and not around
`schemas/` as a directory, because `schemas/catalog/` sits inside `schemas/` and
is the one place a concept shape could plausibly be restated. A scope of
"outside `schemas/`" would therefore exempt the single location the check exists
to guard.

#### Scenario: A concept has no schema
- **WHEN** any of the eight runtime concepts is absent from `schemas/`
- **THEN** validation fails and names the missing concept

#### Scenario: A schema lacks version metadata
- **WHEN** a schema has no `$id` or no `schema_version`
- **THEN** validation fails and names the schema

#### Scenario: A concept has two current versions
- **WHEN** two versions of a concept are each unnamed by the other's
  `supersedes`, so both are current
- **THEN** validation fails and names the concept and both versions

#### Scenario: Succession is not a chain
- **WHEN** `supersedes` names a version that is not the immediately preceding
  one, or two versions present both supersede the same one
- **THEN** validation fails and names the concept and the versions involved

#### Scenario: A concept is defined twice
- **WHEN** any file outside a runtime version directory `schemas/<concept>/`
  declares a room-type, slot or house shape of its own
- **THEN** the conformance check fails and names the file and the schema it
  duplicates

#### Scenario: A catalog data file restates a runtime concept
- **WHEN** a schema under `schemas/catalog/` redefines the slot or room-type
  shape instead of referencing the current `schemas/slot/` or
  `schemas/room-type/` version
- **THEN** the conformance check fails and names the catalog schema

### Requirement: Behaviour vocabulary is defined, not inferred
The current `behavior-vocabulary` schema SHALL define the closed vocabulary of
triggers, conditions and actions a behaviour may use, including the mode and
profile axes. A behaviour expressed with a term outside the vocabulary SHALL
fail validation. The term set is learned from the extraction rather than known
in advance, so the vocabulary is the one runtime schema expected to gain a
version: it is created with its shape in the first pass and gains its derived
terms as a **new version file**, never by editing the version already published.

#### Scenario: Pack uses an unknown action
- **WHEN** a pack references an action not in the vocabulary
- **THEN** validation fails and names the pack and the unknown action

#### Scenario: Vocabulary is closed
- **WHEN** the vocabulary schema is loaded
- **THEN** it enumerates its terms exhaustively rather than permitting
  arbitrary strings

#### Scenario: Vocabulary gains terms
- **WHEN** the extraction yields trigger, condition or action terms the current
  vocabulary lacks
- **THEN** a new version file is published carrying them and declaring
  `supersedes`, and the earlier version's content is unchanged because nothing
  is ever written into it

### Requirement: Published schema versions are immutable
A committed version of a runtime schema SHALL NOT be edited in place, and this
rule does not extend to `schemas/catalog/`. Its content SHALL be compared
against the content at the commit that first introduced that file, read from git
history rather than from a manifest committed alongside it, so that a schema
edit and its bookkeeping cannot land together. A change to a runtime schema
SHALL be published as a **new version file** — `schema_version` is a field
inside the document, so changing it is an edit, and the only way to publish a
change is to add `schemas/<concept>/<new-version>.json`. Because succession
points backwards, publishing a change writes to no existing file, and the
superseded version remains present and byte-identical to the day it landed. Any
bookkeeping needed to record a successor lives in the successor. **Absence is a
change**: a version file introduced at some commit and missing from the working
tree SHALL fail the same check, because a content comparison has nothing to
compare against a deleted file and "retained" would otherwise be unenforced.

#### Scenario: Committed schema is edited
- **WHEN** the content of `schemas/<concept>/<version>.json` changes after the
  commit that first introduced that path
- **THEN** the immutability check fails and names the file

#### Scenario: Superseded version is retained
- **WHEN** a concept gains a new version file declaring `supersedes` on the
  previous version
- **THEN** the previous version remains present and byte-identical, and it is no
  longer the current version

#### Scenario: A superseded version is deleted
- **WHEN** a path that was introduced at some commit and is named in a present
  version's `supersedes` is absent from the working tree
- **THEN** the immutability check fails and names the missing path, because
  absence is a change to a frozen file and the content comparison above has
  nothing to compare against a deleted file

#### Scenario: Publishing a successor edits the retired version
- **WHEN** a new version is published by writing a pointer into the version it
  replaces
- **THEN** the immutability check fails and names the edited file, because the
  pointer direction is what makes publishing possible without an edit

#### Scenario: A catalog schema is revised
- **WHEN** a schema under `schemas/catalog/` is edited in place
- **THEN** the immutability check passes, because that directory is out of its
  scope

### Requirement: Hand-written example house validates
`packs/official/example-house.yaml` SHALL be hand-written and SHALL validate
against the current `house` schema, which composes the room-type and slot
schemas. It SHALL exercise at least two room types, with at least one optional
slot bound and at least one optional slot left unbound. The file SHALL be listed
in `packs/official/HANDWRITTEN`, a committed allowlist. This is a marker, not
detection: a generator that declines to add itself would pass.

#### Scenario: Example house validates
- **WHEN** validation runs
- **THEN** `example-house.yaml` passes against the house, room-type and slot
  schemas

#### Scenario: Example house is not listed as hand-written
- **WHEN** the example house is absent from `packs/official/HANDWRITTEN`
- **THEN** the provenance check fails and names the file

#### Scenario: Unbound optional slot is exercised
- **WHEN** the example house is inspected
- **THEN** it contains at least one bound optional slot and at least one
  optional slot left unbound

### Requirement: Hand-written example pack validates
`packs/official/example-pack.yaml` SHALL be hand-written, listed in
`packs/official/HANDWRITTEN`, and SHALL validate against the current
`pack-manifest` and `behavior-vocabulary` schemas, declaring its kind, its
required and optional slots, and its behaviours in the frozen vocabulary.

#### Scenario: Example pack validates
- **WHEN** validation runs
- **THEN** `example-pack.yaml` passes against the manifest and vocabulary
  schemas

#### Scenario: Pack declares an out-of-vocabulary behaviour
- **WHEN** the example pack uses a behaviour term not in the vocabulary
- **THEN** validation fails and names the pack and the term

### Requirement: Export document is schema-frozen with a static example
The current `export-document` schema SHALL describe a document containing room
bindings keyed by registry identifier plus `entity_id`, and
`packs/official/example-export.yaml` SHALL be a static, hand-written export
document that validates against it. Phase 0 freezes the shape only; the
round-trip, dry-run diff and re-link behaviour is Phase 3.

#### Scenario: Static example export validates
- **WHEN** validation runs
- **THEN** `example-export.yaml` passes against the export-document schema

#### Scenario: Binding lacks a registry identifier
- **WHEN** an export binding carries only `entity_id`
- **THEN** validation fails and names the binding

### Requirement: Exit criterion is enforced in CI
CI SHALL run the full schema validation, and SHALL fail if any of `schemas/`,
the example house, the example pack or the example export is missing or
invalid. It SHALL NOT require the reference clones: they are an input to the
one-time extraction, not a build dependency, and their outputs are committed.
This is `spec.txt`'s Phase 0 exit criterion and SHALL be the last gate before
Phase 0 is considered complete.

#### Scenario: A schema artifact is missing in CI
- **WHEN** CI runs with any schema, an example house, pack or export absent
- **THEN** the pipeline fails and names the missing artifact

#### Scenario: Green on the committed tree
- **WHEN** CI runs on the committed tree
- **THEN** the schema gate passes
