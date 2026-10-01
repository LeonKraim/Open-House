# Spec Delta — reference-catalog

## Purpose

The merged, provenance-tagged corpus of smart-home behaviours, slots, rooms and
room types distilled from four real Home Assistant estates, which every later
phase reads as the source of truth for defaults.

## ADDED Requirements

### Requirement: Repository identification record
Each reference repo SHALL have exactly one record in `catalog/repos.yaml`
carrying `name`, `repo_url`, `author`, `purpose`, `ha_style`, `scale`,
`ha_version` and `best_at`, all non-empty. `ha_style` SHALL be a non-empty,
de-duplicated list of values from a closed set of structural styles (package,
split-include, blueprint-driven, custom-integration, dashboard-strategy,
appdaemon), because a repo can exhibit several at once — renemarc is
split-include, custom-integration and appdaemon — and forcing a single value
would make the choice arbitrary. The list is unordered: an earlier draft
required it "most characteristic first", which nothing could check, and an
unverifiable ordering is decoration. `best_at` SHALL name the one thing this
repo contributes that the others do not.

#### Scenario: Every repo is identified
- **WHEN** the corpus is built
- **THEN** `catalog/repos.yaml` contains one record per reference repo and each
  names its author, purpose, HA style and best-at

#### Scenario: A repo is missing a field
- **WHEN** a repo record has an empty `best_at`, an empty or duplicated
  `ha_style` list, or an `ha_style` value outside the closed set
- **THEN** validation fails and names the repo and the field

### Requirement: Every file is either selected or excluded by a named rule
Each repo's `git ls-files` output SHALL be partitioned into exactly two sets.
A **selected** file is a configuration file, receives one artifact class, and
enters the corpus. An **excluded** file does not enter the corpus and SHALL be
matched by a rule in `catalog/file_rules.yaml`, each rule carrying a `reason`.

Rules are an **ordered list**: every rule carries a unique integer `order`, the
list is applied in ascending `order`, and the first rule matching a path decides
that path's fate. The deciding rule SHALL be recorded on the file and counted.
Because `order` is unique there is no tie to resolve, and a file matching several
rules is normal and expected rather than an error — a vendored bundle matches
both a `custom_components/**` rule and a `**/*.js` rule, and a workflow file
matches both a CI rule and a generic `**/*.ya?ml` rule. Nothing SHALL be dropped
without matching a rule: `selected count + excluded count` SHALL equal the
repo's tracked-file count.

A rule is a tuple `{order, pattern, kind, class, reason}`, where `kind` is
`select` or `exclude`. A **select rule SHALL carry the artifact class it
confers**, and an exclude rule SHALL carry `class: null`. A selected file's
class is therefore the class of its deciding rule and nothing else. The class
SHALL NOT be inferred from file content: inference would make classification
depend on reading the file, and the file lives only in the clone. Carrying the
class on the rule is what makes the inventory a **pure function of
`file_rules.yaml` and a path list**, which is the property the golden-path and
class-pinning checks rely on to run in CI with no clone present.

Authored-path overrides are **themselves rules in this list**, not a side table:
an `authored_paths` entry is a select rule carrying a low `order`, a pattern
naming the path, and the class the path must receive. Otherwise a file selected
past a higher-`order` exclude rule would have no deciding rule to record or
count, and the per-rule counts would not sum. So the override is an ordinary
rule whose class is hand-written rather than derived from a glob convention,
and the counts close over it the same way.

#### Scenario: A select rule carries no class
- **WHEN** `file_rules.yaml` contains a select rule with no `class`, or an
  exclude rule whose `class` is not `null`
- **THEN** validation fails and names the rule and its `order`

#### Scenario: A file's class comes from its deciding rule
- **WHEN** a path matches two select rules carrying different classes
- **THEN** it receives the class of the lower-`order` rule, and that rule is the
  one recorded and counted for it

#### Scenario: Counts close over the whole repo
- **WHEN** the inventory completes for a repo
- **THEN** selected plus excluded equals that repo's `git ls-files` count, and
  excluded is reported per deciding rule

#### Scenario: A file matches no rule
- **WHEN** a tracked file is matched by neither a select nor an exclude rule
- **THEN** validation fails and names the file

#### Scenario: Two rules claim the same precedence
- **WHEN** two rules in `catalog/file_rules.yaml` carry the same `order`
- **THEN** validation fails and names both rules

#### Scenario: Overlapping rules resolve without failing
- **WHEN** a vendored bundle matches both a `custom_components/**` rule and a
  `**/*.js` rule
- **THEN** the lower-`order` rule decides, the file is excluded, and the run
  passes

#### Scenario: An exclude rule silently swallows configuration
- **WHEN** any path in a repo's golden file is not selected
- **THEN** validation fails and names the golden path and the deciding rule

#### Scenario: An authored path overrides a vendored exclusion
- **WHEN** a path is listed in `catalog/file_rules.yaml` under `authored_paths`
  and would otherwise be excluded by a vendored-tree rule
- **THEN** the authored-path rule decides, the path is selected and classified
  as its entry declares, and the deciding rule is counted like any other

### Requirement: Golden files pin each repo's selection
Each repo SHALL have a committed golden file whose entries are paths **each
carrying the artifact class it is expected to receive**. Golden files are
hand-maintained and are the guard against an over-broad exclude rule shrinking
the corpus undetected. The expected class is what makes the entry a check rather
than a list: a path alone says only that something was selected, while a path
with its class fails both when the path stops being selected and when it is
selected as the wrong thing.

The pinned classes are what make that guard independent. A class whose every
file was swallowed by an exclude rule is not "present in the tree" under any
measurement of the inventory, so a check phrased that way would go quiet exactly
when it matters; the pinned class is a hand-written expectation no exclude rule
can erase. Across the four golden files every class in the closed enum **except
`other`** SHALL be pinned by at least one repo, so no class can drop out
entirely unnoticed. `other` is excluded deliberately: it is defined as the
residual a file falls into when no other class fits, so pinning it would commit
the corpus to retaining a file whose only justification is that we could not
classify it — which is the opposite of what that class is for.

#### Scenario: A class is pinned by no repo
- **WHEN** an artifact class in the closed enum other than `other` appears in no
  repo's golden file
- **THEN** validation fails and names the unpinned class

#### Scenario: A pinned path is selected as the wrong class
- **WHEN** a golden path is selected but receives a class other than the one its
  entry declares
- **THEN** validation fails and names the path, the expected class and the
  actual one

#### Scenario: An exclude rule empties a pinned class
- **WHEN** every path pinning a class has been excluded
- **THEN** validation fails naming the repo and the class, even though the
  inventory reports no files of that class

### Requirement: Complete inventory with justified residuals
Every selected file SHALL receive exactly one artifact class from the closed
enum: `package`, `automation`, `script`, `scene`, `helper`, `template`,
`blueprint`, `dashboard`, `custom_integration`, `other`. Parse outcome
(`parsed`, `unparsed`, `not_applicable`) SHALL be a separate field so class
counts still sum to the selected count. A file in `other` SHALL have an entry
in `catalog/inventory_exceptions.yaml` whose `reason` is from the closed set
`unknown_artifact`, `generated_file`; an unjustified `other` SHALL fail the
build. Exclude rules take precedence: a path the ordered rule list excludes
receives no artifact class and needs no exception entry, so a file cannot travel
both routes, and clone tooling is an exclude rule rather than an `other`
reason.

Some classes share their directory with material the repo author did not write.
The observed position, checked in the clones, is:

- `custom_components/` holds both authored integrations and vendored ones.
  CCOSTAN's `tesla_charge_guard` and `alexa_camera_compat` are the author's own,
  each declaring `"codeowners": ["@CCOSTAN"]` in its `manifest.json`; renemarc's
  `github_custom` and `gtfs_custom` are documented in-tree as copies of core
  components altered locally, and `doomsday_clock` as a copy from a different
  renemarc repository — neither authored originals nor third-party vendored,
  alongside genuinely vendored neighbours (`browser_mod`, `hacs`, `fully_kiosk`,
  `unifigateway`, `variable`); johnkoht and fwartner contribute none.
- `blueprints/` holds mostly third-party material, namespaced by author.
  fwartner has 64 files under `blueprints/`: 3 under its own namespace
  (`blueprints/automation/fwartner/`) and 61 spread across 32 other authors'
  (`AlexanderBabel/`, `Blackshome/`, `Blackymas/`, `EPMatt/`, `balloob/`, …),
  and 2 more of its own outside that tree entirely, at
  `custom_configs/blueprints/`. johnkoht has 8, all third-party. The two stray
  paths are why the override list is by path and not by directory: an author's
  work is not where a directory convention would put it.

A path pattern cannot make that distinction, so the paths a repo author wrote
SHALL be listed explicitly in `catalog/file_rules.yaml` under `authored_paths`,
each entry naming a path **and the artifact class it must receive**. `authored_paths`
SHALL override any exclude rule that would otherwise match, including the
vendored-tree rules, and SHALL include at least one entry classed
`custom_integration` and at least one classed `blueprint` — so that
`custom_integration` and `blueprint` are reachable, and the requirement has a
case that fails when the list is empty. Material that is neither the author's
original work nor third-party vendored — the altered core copies — SHALL be
excluded by a named rule with its own reason, and SHALL NOT be attributed to the
repo author.

#### Scenario: Class counts reconcile with selection
- **WHEN** the inventory completes
- **THEN** the sum of class counts equals the selected count

#### Scenario: Unjustified residual fails
- **WHEN** a file is classified `other` without an entry in
  `catalog/inventory_exceptions.yaml`, or with a reason outside the closed set
- **THEN** validation fails and names the file

#### Scenario: The authored-path override list is empty
- **WHEN** `authored_paths` contains no entry classed `custom_integration`, or
  none classed `blueprint`
- **THEN** validation fails and names the missing class

#### Scenario: An authored path does not exist
- **WHEN** a path listed in `authored_paths` is not tracked in its repo
- **THEN** validation fails and names the path

#### Scenario: An authored path is classed as something else
- **WHEN** a path listed in `authored_paths` is not classified as its declared
  class
- **THEN** validation fails and names the path and both classes

#### Scenario: Unparseable file is still counted
- **WHEN** a selected file fails to parse
- **THEN** it keeps its artifact class, is recorded `unparsed` with the error,
  and appears in the class count

### Requirement: References are classified as entity or service
The extraction SHALL classify every referenced `domain.identifier` as either
`entity_ref` or `service_call` from its structural position: a reference that
is the value of a `service`/`action` key is a `service_call`; a reference under
`target`, `entity_id`, or any other position is an `entity_ref`. Service calls
SHALL NOT enter any identifier set used for provenance checks.

**References embedded in Jinja templates are in scope.** A template such as
`{{ states('light.kitchen') }}` or `{{ is_state('binary_sensor.door', 'on') }}`
contains an entity reference, and these repos reference entities that way more
often than any other way, so excluding templates would make the hardcoding audit
incomplete in the majority of cases — which is the one thing `spec.txt` asks the
audit to be. The extraction SHALL scan **every `{{ }}` block** for any
`domain.identifier` literal and apply the same structural classification to it.
The rule is stated over the literal rather than over the call that contains it
because the narrower phrasing — string arguments to the state and service
functions — misses `{{ states.light.kitchen_ceiling.state }}` (attribute access,
no string argument at all) and `expand('light.kitchen')`, both of which carry an
entity reference and both of which appear in these configs. A rule narrower than
its own stated motive would leave the audit incomplete in exactly the cases the
motive names.

#### Scenario: A service name is not an identifier
- **WHEN** the extraction encounters `service: light.turn_on`
- **THEN** it records a `service_call`, and `light.turn_on` appears in no
  entity identifier set

#### Scenario: An entity reference is captured
- **WHEN** the extraction encounters a light referenced under `entity_id`
- **THEN** it records an `entity_ref` carrying the source repo and path

#### Scenario: An entity reference inside a template is captured
- **WHEN** the extraction encounters `{{ states('light.kitchen_ceiling') }}`
- **THEN** it records an `entity_ref` for `light.kitchen_ceiling`, which enters
  the hardcoding audit like any other

#### Scenario: A template reference without a string argument is captured
- **WHEN** the extraction encounters `{{ states.light.kitchen_ceiling.state }}`
  or `expand('light.kitchen')`
- **THEN** both record an `entity_ref`, because the rule is stated over the
  literal inside the `{{ }}` block rather than over the call that contains it

#### Scenario: A service call inside a template is not an identifier
- **WHEN** the extraction encounters `{{ service('light.turn_on') }}`
- **THEN** it records a `service_call`, not an `entity_ref`

### Requirement: Extraction commits facts and withholds expression
The deterministic extraction (stage A) SHALL emit `catalog/raw-behaviors.json`,
which is committed, and SHALL emit the unfiltered extraction to
`.local/raw-verbatim.json`, which is excluded by `.gitignore` and never
committed. The verbatim store is **outside `catalog/`** deliberately: the
`catalog` schema requirement makes every data file in `catalog/` validate
against a schema, and a store the corpus validator is required to reject does
not belong where that validator runs. The two stores differ in exactly one
respect, and this split is the licence position expressed as a mechanism rather
than as a label.

A **fact** is a structural property of the artifact: its path, its id, its
artifact class, its trigger/condition/action vocabulary terms, the slots it
binds, its `unclaimed` reason, and the `domain.object_id` strings it references.
An identifier is a fact about someone's naming scheme, and the hardcoding audit
`spec.txt` requires cannot be complete without one, so identifiers from all four
repos are committed.

**Expression** is authored text, and the committed record has **no field that
can hold it**. That is enforced by three things which together are structural,
and by no one of them alone.

**First, the allowlist is a named committed artifact**, `schemas/catalog/
fact-fields.yaml`, not a prose enumeration and not the schema's own field set.
An earlier draft wrote "the fact fields enumerated above and nothing else" and
then, two sentences later, "an allowlist is checked against the schema's own
field set". Those cannot both be the definition: the first is exactly the
hand-kept list the second disowns, and under the second the check is circular —
the checked set and the checking set are the same object, so adding a field to
the schema adds it to the allowlist in the same instant and the guard can never
fire. The allowlist is therefore a file, and the raw-record schema under
`schemas/catalog/` is **generated from it**, so the two cannot drift in field
*names* — generation is a single-source-of-truth device rather than a barrier,
since editing the allowlist and regenerating moves both together by design, and
the bidirectional check below is what carries the weight.

**Second, the check is bidirectional set-equality** — `set(schema.properties)`
equals `set(fact_fields)`, failing when a schema property is absent from the
allowlist *and* when an allowlist entry is absent from the schema. The record
schema also carries `additionalProperties: false`, so a record with a key
outside the set fails too.

**Third, and this is the part that makes the guarantee real rather than
bookkeeping, every string fact is constrained by one of a small closed set of
committed fact-shapes.** `schemas/catalog/fact-fields.yaml` SHALL declare, for
each field, either an enum, or an integer, or a boolean, or a list of those, or
a **fact-shape** drawn from a named list committed at `schemas/catalog/
fact-shapes.yaml`. A free-form string is not permitted, and neither is an
arbitrary pattern: a field SHALL name a committed shape, and a string field
whose shape admits arbitrary printable text is inadmissible.

A pattern alone is not a constraint on anything, because any pattern satisfies
"has a pattern" — `^.*$` is a pattern. Stating the rule as "pattern-constrained"
would therefore have relocated the guarantee one last time into the regex, and
the failure would not even be adversarial: a later round adds
`template_source: {type: string, pattern: "^.*$"}` to keep the raw Jinja for
pattern-class detection, the bidirectional equality passes because the field is
in the allowlist, the type check passes because the string has a pattern, and
the committed store carries the two non-granting repos' YAML for the whole run.

**A shape is therefore a member of a committed set, not a regex a field author
writes.** The set is closed and small — `entity_ref` (`^[a-z_]+\.[a-z0-9_]+$`),
`dotted_id`, `slug`, and `selected_path` — and adding to it is an edit to a
committed artifact rather than a free choice at the point of use. `selected_path`
is membership rather than a pattern, and it is what carries the `path` fact: a
value is admissible because it is an element of the committed selected-path set
from `catalog/inventory.json`. That distinction is not pedantry — a pattern tight
enough to exclude authored text would also reject real tracked paths, since
fwartner's filenames are German and real paths carry spaces and non-ASCII, so
membership admits every real path without admitting prose. Enums carry `class`,
`scope`, `unclaimed` and the vocabulary terms; integers and booleans carry the
counts and flags.

The guarantee is thus that expression **cannot be expressed** in the committed
record's schema, and it now rests on a closed committed set rather than on a
property that anything can satisfy.

`"distribution": "internal-only"` was the wrong shape for this: a committed file
is distributed, and the marker asserted a boundary that nothing enforced. The
property it was reaching for is the one above — the committed store carries
facts for all four repos and expression for the two that grant it, and the
verbatim store carries everything and is absent from the repository. The shipped
corpus is `catalog/behaviors.yaml`, and every shipped row SHALL cite the raw ids
it was derived from.

The four reference clones are an **input to a local, one-time generation step,
not a build dependency**: the extraction reads them, its outputs are committed,
and CI SHALL validate the committed outputs without requiring the clones. The
reasons are hermeticity and reproducibility — the clones are excluded from this
repository by `.gitignore`, and a CI gate that reads four third-party
repositories fails when they change rather than when we do.

The boundary is drawn at **what actually needs the clones, and no wider**,
because drawing it wider would leave the completeness guarantee unenforced in
the pipeline for no gain. **Exactly two checks are local**, and the package
names each by what it reads: the **closure check** reads `git ls-files` in each
clone, and the **prose gate** reads the gitignored `.local/raw-verbatim.json`,
because the non-granting repos' prose is withheld from the committed store and
there is nothing in the repository to compare a shipped passage against. Their
results are committed. The **identifier gate** — the shipped-row check of 4.6 —
is *not* among them: it compares a row's text against the `entity_ref`s in the
committed `raw-behaviors.json`, so it runs in CI like everything else. Curation-time reads the corpus
generation needs are not gates and no CI job performs them: matching each repo's
`ha_version` against its `.HA_VERSION`, and confirming that an `authored_paths`
path is tracked. Everything else runs in CI on committed artifacts, including
the two checks that carry the completeness guarantee — the golden-path check
applies the committed ordered rule list to a committed path, a pure function
needing no repository, and the class-pinning check is likewise a pure function
of the committed golden files. A file-based check that could have run in CI does
not get excused from it by sitting near one that could not.

#### Scenario: A fact field is added to the record
- **WHEN** a property is added to the committed raw-record schema without a
  matching entry in `schemas/catalog/fact-fields.yaml`
- **THEN** validation fails and names the property and the allowlist, so the
  field set cannot grow past the guarantee by editing the schema alone

#### Scenario: The allowlist and the schema drift
- **WHEN** `schemas/catalog/fact-fields.yaml` names a field the generated
  raw-record schema does not carry, or the regeneration produces a schema other
  than the committed one
- **THEN** validation fails in both directions and names the divergent field

#### Scenario: A free-form string field is proposed
- **WHEN** a field whose type is an unconstrained string — no enum, no shape —
  is added to the allowlist or to the schema
- **THEN** validation fails and names the field, because a free-form string is
  the shape authored text takes and the committed record admits none

#### Scenario: A string fact is admitted under a permissive pattern
- **WHEN** a string field names a shape drawn from `fact-shapes.yaml` whose
  pattern admits arbitrary printable text — `^.*$`, `^[\s\S]*$`, or a
  permissive `^[\w\s.,'!?-]+$` — or carries a pattern of its own rather than
  naming a committed shape
- **THEN** validation fails and names the field and the pattern, so the
  guarantee cannot move from the closed shape set into the regex

#### Scenario: The extraction cannot emit a non-fact field
- **WHEN** a raw record is built for a file whose source repo's
  `reuse_status_code` is not `reusable`
- **THEN** the record's key set equals the fact allowlist exactly, the schema's
  `additionalProperties` is `false`, and a record carrying any other key fails
  validation naming the key

#### Scenario: Committed extraction carries facts for every repo
- **WHEN** the extraction runs over all four repos
- **THEN** `raw-behaviors.json` holds a record for every selected file in every
  repo, including the two that grant nothing, each carrying its identifiers and
  its vocabulary terms

#### Scenario: The verbatim store is never committed
- **WHEN** the set of tracked files is inspected
- **THEN** `.local/` is excluded by `.gitignore` and no file beneath it is
  tracked

#### Scenario: The verbatim store is outside the corpus validator's reach
- **WHEN** `oh-catalog validate` runs on a machine where the extraction has
  produced `.local/raw-verbatim.json`
- **THEN** it passes, because the store is not under `catalog/` and no schema
  under `schemas/catalog/` is expected for it

#### Scenario: A shipped row cites nothing
- **WHEN** a row in `behaviors.yaml` has an empty `raw_ids`
- **THEN** validation fails and names the row

### Requirement: Behaviour records separate concept from expression
Each behaviour SHALL be one row carrying: `id`, `name`, `description`,
`category`, `scope`, `source_repos`, `required_slots`, `optional_slots`,
`concept`, `expression`, `raw_ids`, `reuse_status`, `obligations`, `license`,
`classification`, `retention`, `change_notice`. This enumeration is
exhaustive for the shipped corpus and is what the `behaviors` schema is built
from, so a field another requirement adds to a row SHALL appear here.
`description` SHALL state in our own words what the behaviour does.
`concept` SHALL always be populated in our own words. `expression` SHALL carry
source-derived trigger/condition/action structure and SHALL be empty unless
every source repo's **code** reuse status is `reusable`. `scope` SHALL be
`room` or `house`. `classification` SHALL be `generic`, `module_candidate` or
`discard`. `retention` SHALL be `audit` on `discard` rows and `null` otherwise.
`change_notice` SHALL name what was changed and by whom on every row derived
from a source carrying the `state_changes` obligation, and `null` otherwise.

#### Scenario: Behaviour from granting repos
- **WHEN** every source repo's code grant permits reuse
- **THEN** `expression` may be populated and `concept` is still populated

#### Scenario: Behaviour from a non-granting repo
- **WHEN** any source repo's code grant does not permit reuse
- **THEN** `expression` is empty, `concept` describes the behavior in our own
  words, and validation fails if `expression` is populated

#### Scenario: A behaviour has no description
- **WHEN** `description` is empty
- **THEN** validation fails and names the row

### Requirement: Reuse classification per behaviour
Every behaviour SHALL be classified into exactly one of `generic`,
`module_candidate` or `discard`. Each row's `license`, `reuse_status` and
`obligations` SHALL be derived by the validator from the **code** halves of the
licence records of its `source_repos`, and SHALL NOT be hand-written. The rule,
stated once:

- `license` SHALL be the source **code licence** latest in the permissiveness
  order published by the `attribution` capability. It is a licence value
  (`mit`, `apache_2_0`, …), never a status.
- `reuse_status` SHALL be the status that capability's derivation table gives
  for that licence value — `reusable` or `ideas_only`.
- `obligations` SHALL be the **union** of the obligations those same source
  code licences carry, so merging cannot silently drop a source's obligation.

`discard` rows SHALL be retained with `retention: audit` and excluded from the
shipped default set.

#### Scenario: Merged row derives from the most restrictive code licence
- **WHEN** a row merges a source under `mit` with a source under `apache_2_0`
- **THEN** its `license` is `apache_2_0`, its `reuse_status` is `reusable`, and
  its `obligations` are `attribution` and `state_changes`

#### Scenario: Row drops a source's obligation
- **WHEN** a row's `obligations` omits an obligation carried by one of its
  source code licences
- **THEN** validation fails and names the row and the omitted obligation

#### Scenario: Personal behaviour is discarded but retained
- **WHEN** a behaviour depends on a named household member or a specific
  person's device
- **THEN** it is classified `discard`, retained with `retention: audit`, and
  absent from the shipped default set

#### Scenario: Derived status contradicts the licence record
- **WHEN** a row's `reuse_status` or `license` is more permissive than its
  sources permit
- **THEN** validation fails and names the row and the conflicting licence record

### Requirement: Single-source behaviours are not promoted by default
A behaviour SHALL be classified `generic` only if `source_repos` has two or more
entries, or the behaviour is listed in `catalog/overlap_exceptions.yaml` with a
justification referencing its `id`. Any other single-source `generic` row SHALL
fail validation.

#### Scenario: Single-source behaviour defaults without an exception
- **WHEN** a behaviour with one source repo is classified `generic` and is not
  in `overlap_exceptions.yaml`
- **THEN** validation fails and names the row

#### Scenario: Justified exception is accepted
- **WHEN** a single-source behaviour is listed in `overlap_exceptions.yaml`
  with a justification
- **THEN** it may be `generic` and the justification appears in the overlap
  report

### Requirement: Cross-repo overlap is reported
Behaviours with ≥2 source repos SHALL merge into one row listing all sources,
recording the chosen approach and the rejected ones. A ranked overlap report
SHALL list every multi-source row, ordered **descending by source-repo count,
then ascending by permissiveness** — widest agreement first, and within a tie
the most permissive licence first, because a row that more repos agree on and
fewer licences constrain is the readiest default.

#### Scenario: Same behaviour in two repos merges to one row
- **WHEN** motion lighting appears in two repos
- **THEN** one merged row names both sources, records the chosen approach and
  the rejected ones

#### Scenario: Multi-source row is absent from the report
- **WHEN** a row has ≥2 source repos but does not appear in the report
- **THEN** validation fails and names the row

### Requirement: Hardcoding audit maps every reference to a slot
Stage A SHALL emit `catalog/hardcoded_refs.yaml`: every distinct `entity_ref`
with its source repo, its `domain.object_id`, its inferred scope (a room or
`house`), and its repo's naming-convention label. Every entry SHALL either map
to a slot in `slots.yaml` or carry a `constant` justification from the closed
set `device_id`, `device_tracker`, `person`, `sun`, `time`; an entry with
neither SHALL fail the build. The set is deliberately heterogeneous — `device_id`
is a targeting key while the others are entity domains — because the property
the justification actually needs is a single one: these are the fixed things a
reference may target instead of a slot, and each entry records which kind it is.

The file is committed, and it carries identifiers from all four repos including
the two that grant nothing. That is deliberate: an entry holds a
`domain.object_id`, a scope and a naming-convention label, and none of those is
authored text, which is what the licence position withholds. Treating an
identifier as a fact rather than as expression is a judgement rather than a
settled reading of the licences, and the design records it as one, including the
one-line fallback if an author disagrees. The
file SHALL NOT carry an alias, a display name, a comment or a YAML fragment from
any repo, and SHALL carry none of those from a non-granting repo under any
field.

#### Scenario: The audit carries no expression
- **WHEN** an entry in `hardcoded_refs.yaml` carries an alias, display name,
  comment or YAML fragment from its source repo
- **THEN** validation fails and names the entry

#### Scenario: A reference maps to no slot and no constant
- **WHEN** a hardcoded reference has neither a `slot` nor a `constant` value
- **THEN** validation fails and names the reference and its source repo

#### Scenario: Naming conventions are comparable across repos
- **WHEN** the audit is complete
- **THEN** each entry carries its repo's naming-convention label, so the same
  concept can be compared across repos

### Requirement: Slot vocabulary derived from cross-repo usage
Slot names SHALL be drawn from a controlled vocabulary recorded in
`catalog/slots.yaml`, and every multi-source slot SHALL carry examples from at
least two different source repos. Each slot SHALL record the accepting entity
domains, whether it is required or optional, its `source_repos`, and at least
one example. Every example SHALL name its source repo, that repo's
`reuse_status_code`, and the `raw_ids` of the raw records it was drawn from; an
example from a non-granting repo SHALL be recorded as a description of the
pattern and SHALL NOT contain an `entity_ref` extracted from one of its own
`raw_ids` — the same provenance-resolved rule the gate applies to rows, with the
example's own citations playing the part a row's `raw_ids` plays for a row.

An example MAY carry an empty `raw_ids` when the artifact it comes from yields
no raw records — an unparsed file, a dashboard, or an authored
`custom_components/` tree whose parse outcome is `not_applicable`. Raw records
exist only for parseable selected files, so requiring a citation from every
example would make the rule unsatisfiable for a whole class of artifact. Such an
example SHALL instead carry a `description` of the pattern in our own words, and
SHALL NOT be drawn from a non-granting repo's expression: with no citation there
is nothing to resolve provenance against, so the only safe form is prose we
wrote.

#### Scenario: Multi-source slot has single-source examples
- **WHEN** a slot lists two or more source repos but all its examples come from
  one of them
- **THEN** validation fails and names the slot

#### Scenario: Example transcribes a non-granting repo
- **WHEN** a slot example sourced from a non-granting repo contains an
  `entity_ref` extracted from one of the example's own `raw_ids`
- **THEN** validation fails and names the slot and the identifier

#### Scenario: Uncited example carries no description
- **WHEN** a slot example carries an empty `raw_ids` and no `description`
- **THEN** validation fails and names the slot and the example

#### Scenario: Uncited example from a non-granting repo
- **WHEN** a slot example carries an empty `raw_ids` but names a non-granting
  source repo
- **THEN** validation fails, because with no citation there is nothing the
  provenance rule can resolve against

#### Scenario: Slot name is outside the controlled vocabulary
- **WHEN** a slot name is not present in the controlled vocabulary
- **THEN** validation fails and names the slot

### Requirement: Room-type map covers room and house scope
`catalog/room_types.yaml` SHALL define room types from the union of real rooms,
each naming the source rooms that produced it, plus a `house` scope providing
the house-level slots. A behaviour with `scope: room` SHALL require only slots
some room type provides; a behaviour with `scope: house` SHALL require only
slots the `house` scope provides.

Each room type SHALL carry `default: true | false`. **The union is not
truncated by the corroboration rule**: a room type that exists in one repo is
recorded, so that every real room is represented and no later phase finds a room
it cannot express. What the corroboration rule governs is which types Phase 1
pre-populates for a new user, and there it is a quality gate rather than a
coverage rule — a `default: true` room type SHALL trace to source rooms in at
least two different repos, and a room type tracing to one repo SHALL be
`default: false`. The distinction is the difference between "this exists and we
must be able to represent it" and "this is safe to assume on a stranger's behalf",
and collapsing the two is what would silently drop a real room from the map.

#### Scenario: Room-scoped behaviour needs an unavailable slot
- **WHEN** a `scope: room` behaviour requires a slot no room type provides
- **THEN** validation fails and names the behaviour and the missing slot

#### Scenario: House-scoped behaviour is accepted
- **WHEN** an away-shutdown behaviour has `scope: house` and requires only
  house-level slots
- **THEN** it validates without being forced through a room type

#### Scenario: Room type traces to real rooms
- **WHEN** a room type is defined
- **THEN** it names the concrete source rooms that produced it

#### Scenario: A default room type is corroborated
- **WHEN** a room type carries `default: true` but its source rooms come from a
  single repo
- **THEN** validation fails and names the room type and the single repo

#### Scenario: A single-repo room type is still recorded
- **WHEN** a room type exists in one repo only and is marked `default: false`
- **THEN** validation passes, so the union of real rooms is not truncated by the
  corroboration rule


### Requirement: Solved edge cases become scenario seeds
Every edge case a repo already handles — flaky device, restart, false trigger,
notification spam, unavailable sensor — SHALL be recorded in
`catalog/edge_cases.yaml` with the guard used and its source repo, and SHALL
have a corresponding placeholder recorded for Phase 1.

#### Scenario: Edge case is captured with its handling
- **WHEN** a repo guards against a false trigger with a debounce
- **THEN** a seed records the trigger, the guard and the source repo

#### Scenario: Seed has no Phase 1 placeholder
- **WHEN** an edge-case seed has no Phase 1 placeholder recorded
- **THEN** validation fails and names the seed

### Requirement: Pain points are recorded
Friction that makes a reference setup hard for a newcomer to replicate SHALL be
recorded in `catalog/pain_points.yaml` with `repo`, `friction`, `why_hard` and
`our_answer` — what Open House must do instead. `our_answer` SHALL be non-empty
for every entry and SHALL name the downstream phase that will answer it.

#### Scenario: Pain point without an answer
- **WHEN** a pain-point entry has an empty `our_answer` or names no downstream
  phase
- **THEN** validation fails and names the entry

#### Scenario: Every repo contributes pain points
- **WHEN** the register is complete
- **THEN** each of the four repos contributes at least one entry

### Requirement: Integration dependency ledger
Each integration, add-on or bridge a repo relies on SHALL be recorded in
`catalog/integrations.yaml` with the repos that use it and a disposition of
`require`, `replace` or `avoid`. An integration used by three or more repos
SHALL be `require`. A `replace` or `avoid` entry SHALL name its replacement.

#### Scenario: Widely used dependency is required
- **WHEN** an integration is used by three or more repos
- **THEN** its disposition is `require`

#### Scenario: Dead dependency is avoided with a successor
- **WHEN** an integration is abandoned upstream or superseded by a core feature
- **THEN** its disposition is `avoid` and the successor is named

### Requirement: Corpus data files are machine-readable and validated
Every data file (`.yaml` or `.json`) in `catalog/` SHALL validate against a
schema under `schemas/catalog/` with a `$id` and a `schema_version`. Markdown
artifacts (`README.md`, `overlap.md`) are prose and are exempt. The cross-file
rules in this capability SHALL be enforced by one validator, which SHALL fail
on a duplicate behaviour `id`, a missing required field, a `required_slots`
entry unavailable to the row's scope, a data file with no schema, or an
`unclaimed` reason outside the closed set (`non_behavior_file`, `duplicate_of`,
`vendor_config`, `malformed`). The count of unclaimed raw behaviours SHALL be
reported on every run.

The unclaimed register lives **in `catalog/raw-behaviors.json`, as a field on
each raw record**, not in a file of its own: the marker exists only in relation
to a raw record, and a separate file would be a second copy of the raw id set
that could drift from the first. A record claimed by a shipped row carries
`unclaimed: null`; a record no shipped row claims SHALL carry a non-null reason
from the closed set. The schema for `raw-behaviors.json` under
`schemas/catalog/` is therefore the schema for this register too.

#### Scenario: Duplicate id fails validation
- **WHEN** two behaviour rows share an `id`
- **THEN** validation fails and names the duplicated id

#### Scenario: Data file has no schema
- **WHEN** a `.yaml` or `.json` file exists under `catalog/` with no matching
  schema in `schemas/catalog/`
- **THEN** validation fails and names the file

#### Scenario: Raw behaviour is unclaimed
- **WHEN** a raw behaviour is not cited by any shipped row
- **THEN** it is listed `unclaimed` with a reason from the closed set, and the
  run reports the unclaimed count

#### Scenario: Unclaimed reason is invented
- **WHEN** an `unclaimed` reason is outside the closed set
- **THEN** validation fails and names the raw behaviour and the reason
