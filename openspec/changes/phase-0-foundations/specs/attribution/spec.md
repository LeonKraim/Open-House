# Spec Delta — attribution

## Purpose

The licence record, obligation model and reuse gate that govern what may be
taken from the four reference repos, and the attribution owed to them.

## ADDED Requirements

### Requirement: Per-repo licence record split by artifact kind
Each reference repo SHALL have exactly one record in `catalog/licenses.yaml`
stating `repo`, `author`, `license_code`, `license_prose`, `license_file` (or
`null`), `reuse_status_code` and `reuse_status_prose`. A repo with no licence
file SHALL be recorded `no_licence` for that artifact kind, regardless of what
its README claims, and the discrepancy SHALL be recorded. Code and prose status
are separate because a repo may grant one and not the other.

#### Scenario: Unfiled licence claim is not a grant
- **WHEN** a repo's README claims a licence but no licence file exists
- **THEN** `license_code` is `no_licence`, `reuse_status_code` is `ideas_only`,
  and the claim-versus-file discrepancy is recorded

#### Scenario: Split licence does not collapse to the stricter half
- **WHEN** a repo licenses code permissively and prose restrictively
- **THEN** `reuse_status_code` reflects the code grant and
  `reuse_status_prose` the prose terms, and behaviour rows — which reuse code —
  follow `reuse_status_code`

### Requirement: Statuses are derived from the licence, not asserted
`reuse_status_code` and `reuse_status_prose` and their `obligations` SHALL be
derived by the validator from `license_code` and `license_prose` using the
normative table below, and SHALL NOT be hand-written. `obligations` SHALL be a
subset of `attribution`, `state_changes`, `share_alike`, `non_commercial`.

| `license_code` / `license_prose` | status | obligations |
| --- | --- | --- |
| `public_domain` | `reusable` | none |
| `mit` | `reusable` | attribution |
| `apache_2_0` | `reusable` | attribution, state_changes |
| `cc_by_nc_sa` | `ideas_only` | attribution, share_alike, non_commercial |
| `no_licence` | `ideas_only` | none |

Cells hold the licence value for the artifact kind being recorded: the table is
applied once to `license_code` giving `reuse_status_code`, and once to
`license_prose` giving `reuse_status_prose`. A derived status that disagrees
with a hand-written one SHALL fail validation.

#### Scenario: Contradictory hand-written status fails
- **WHEN** a record asserts `reuse_status_code: reusable` while its
  `license_code` derives `ideas_only`
- **THEN** validation fails and names the record and the licence value

#### Scenario: Share-alike material is not incorporated
- **WHEN** a licence derives `ideas_only` with a `share_alike` or
  `non_commercial` obligation
- **THEN** material under it may not populate any `expression`

#### Scenario: Every licence value has a derivation row
- **WHEN** the derivation table is loaded
- **THEN** it has a row for every value permitted in `license_code` and
  `license_prose`

### Requirement: Published permissiveness order
The attribution capability SHALL publish one total order over licence values,
least to most restrictive: `public_domain`, `mit`, `apache_2_0`, `cc_by_nc_sa`,
`no_licence`. `catalog/licenses.yaml` SHALL use exactly these values. The order
ranks **licence values only** — statuses are derived, not ranked, because three
licences share the status `reusable`. Every use of "most restrictive" or
"permissiveness" elsewhere in the corpus SHALL mean this order, and the
validator SHALL read it from here rather than defining its own.

#### Scenario: Merged row takes the most restrictive code licence
- **WHEN** a behaviour merges sources whose code licences differ
- **THEN** the row's `license` is the one latest in this order, and its
  `reuse_status` is the status the derivation table gives for that licence

#### Scenario: Overlap ranking uses this order
- **WHEN** the overlap report ranks rows by permissiveness
- **THEN** the ranking uses exactly this order

#### Scenario: An unknown licence value appears
- **WHEN** a record uses a licence value outside this order
- **THEN** validation fails and names the record and the unknown value

### Requirement: Reuse gate is provenance-resolved and scoped
**Expression** from a reference repo — a `name`, an alias, a `description`, a
comment, a block of YAML, or any other authored text — SHALL NOT be incorporated
into a shipped artifact unless that repo's `reuse_status_code` is `reusable`, and
SHALL NOT be incorporated from a repo whose status is `ideas_only` under any
circumstance. **Identifiers are treated as recorded facts rather than as
expression, and this rule does not reach them.** An identifier is a recorded fact about a naming scheme, the extraction
commits identifiers from all four repos, and `catalog/hardcoded_refs.yaml` — a
Phase 0 deliverable — would violate this requirement on every run if the word
covered it. The two categories are defined once in the `reference-catalog` spec
and this requirement reads that definition rather than restating it.

For each shipped row whose `source_repos` include a repo
with `reuse_status_code: ideas_only`, the validator SHALL reject any text in
that row — `name`, `concept`, `description`, `change_notice`, or a slot example
it supplies — that contains an `entity_ref` extracted from a raw record the row
cites. **Every free-text field a row carries is in scope**, and the enumeration
above is an illustration of that rule rather than a limit on it: an identifier
leaks through whichever field is overlooked, so the check SHALL be driven by the
row's schema, not by a hand-kept list that can fall behind it. The check
SHALL be resolved per row, never matched globally against all repos, so an
identifier that happens to also exist in a non-granting repo is not a failure.

#### Scenario: Adaptation precedes the record
- **WHEN** a row cites a repo that has no record in `catalog/licenses.yaml`
- **THEN** the build fails, naming the row and the unsettled repo

#### Scenario: Non-granting identifier reaches a shipped row
- **WHEN** a row citing an `ideas_only` repo contains in its `concept` an
  `entity_ref` extracted from a raw record that row cites
- **THEN** the build fails and names the row, the identifier and the repo

#### Scenario: Coincidental identifier collision is not a failure
- **WHEN** a row cites only granting repos, but an identifier it uses also
  exists in an `ideas_only` repo it does not cite
- **THEN** the check passes

#### Scenario: A service name is never a provenance failure
- **WHEN** a row's `expression` mentions a service such as `light.turn_on`
- **THEN** the check passes, because service calls are not entity references

#### Scenario: Concept from a non-granting repo is still allowed
- **WHEN** a behaviour is taken from an `ideas_only` repo
- **THEN** its `concept` describes the idea in our own words and `expression`
  is empty

### Requirement: Non-permissive prose is never quoted
No shipped artifact, including anything under `docs/`, SHALL contain prose
quoted from a repo whose `reuse_status_prose` is `ideas_only`. Ideas from such a
repo are restated in our own words, never reproduced. This closes the same gap
on the prose side that the expression rule closes on the code side, and it
matters because renemarc's prose terms are non-commercial and share-alike.

#### Scenario: Prose from a non-permissive repo is quoted
- **WHEN** a shipped artifact contains a verbatim passage from a repo whose
  `reuse_status_prose` is `ideas_only`
- **THEN** validation fails and names the artifact and the repo

#### Scenario: Idea is restated in our own words
- **WHEN** a document describes an idea taken from a repo with
  `reuse_status_prose: ideas_only`
- **THEN** the check passes, provided no verbatim passage is reproduced

### Requirement: State-change notices are honoured
Where a source carries the `state_changes` obligation, every **adapted row**
derived from it SHALL carry a `change_notice` naming what was changed and by
whom — Open House, in our own words; there is no "adapter" component in Phase 0
to name — and the file containing any such row SHALL also carry a file-level
notice stating that it contains material adapted from that source. Both units
are required because they do different jobs: a row is the unit of adaptation and
carries the precise record, while a file is the unit of distribution and is what
a reader actually opens — a row-level field alone would be invisible, and a
file-level notice alone would over-attribute rows that came from elsewhere.

#### Scenario: Adapted row lacks a change notice
- **WHEN** a row derived from a `state_changes` source carries no
  `change_notice`
- **THEN** the check fails and names the row

#### Scenario: Adapted file lacks a file-level notice
- **WHEN** a file contains a row derived from a `state_changes` source but the
  file itself carries no file-level notice
- **THEN** the check fails and names the file

#### Scenario: Adapted row carries a notice
- **WHEN** the row carries a `change_notice` naming what changed and by whom in
  our own words, and its file carries the file-level notice
- **THEN** the check passes

### Requirement: Author contact is attempted and recorded
For each repo with no licence file, an outbound contact attempt SHALL be
recorded in `catalog/licenses.yaml` under `author_contact` with `channel`,
`attempted_on` and `outcome`. Where no contact has been made, `outcome` SHALL be
`not_attempted`, `attempted_on` SHALL be `null` — there is no date on which
nothing happened — and a reason SHALL be recorded, and the repo SHALL remain
`ideas_only`. Making the contact itself is an outward-facing action and
requires the user's authorisation.

#### Scenario: Unlicensed repo has no contact record
- **WHEN** a repo with no licence file has no `author_contact` block
- **THEN** validation fails and names the repo

#### Scenario: Contact is not authorised
- **WHEN** contact has not been authorised by the user
- **THEN** `outcome` is `not_attempted`, `attempted_on` is `null`, the reason is
  recorded, and the repo stays `ideas_only`

#### Scenario: Author grants reuse
- **WHEN** an author replies granting reuse
- **THEN** the record's licence value is updated with the grant and the
  evidence, and the derived status and obligations follow the table

### Requirement: Attribution is surfaced and regenerated
Attribution for every repo whose material is incorporated SHALL appear in
`docs/attribution.md`, naming repo, author, licence, obligations, and what was
reused or adapted. The file SHALL be regenerated from `catalog/licenses.yaml`
rather than hand-maintained, and a drift check SHALL fail when the committed
file differs from a fresh regeneration.

#### Scenario: Incorporated repo appears in attribution
- **WHEN** material from a `reusable` repo ships
- **THEN** `docs/attribution.md` names that repo, its author, its licence, its
  obligations and the reused material

#### Scenario: Attribution drifts from the records
- **WHEN** the committed `docs/attribution.md` differs from a fresh
  regeneration
- **THEN** the build fails and names the divergent repo
