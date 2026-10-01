# Design — Phase 0: Foundations

## Context

See `proposal.md` — Why. The state that shapes this design:

- Four repos under `ressources/`, all Home Assistant YAML estates, cloned with
  full history. ~8,400 tracked files; roughly three quarters are not config
  (vendored components, `www/community` card bundles, `.venv`, binaries), so a
  raw file walk is not a useful unit of work and "configuration file" has to be
  defined rather than assumed.
- The four use **mutually incompatible conventions**. CCOSTAN: YAML packages,
  modern syntax, `.HA_VERSION` `2026.9.4` (at `config/.HA_VERSION`, not repo
  root), opaque fixture IDs (`light.m1_front_left`, `binary_sensor.mcu1_gpio5`)
  whose meaning lives only in `customize:` blocks. johnkoht: YAML packages,
  room-as-state-machine, uniform `{domain}.{room}_{fn}` naming, `2026.7.4`.
  renemarc: no packages, publish/subscribe via helpers, `0.108.8` (2020) —
  syntax that no longer loads. fwartner: packages plus German IDs and ~90 HACS
  integrations, monolithic files, `2026.2.1`.
- **Licensing is not uniform, and two of four grant nothing.** CCOSTAN is MIT.
  renemarc is Apache-2.0 for code, with README stating CC BY-NC-SA 4.0 for
  prose — non-commercial and share-alike. fwartner *claims* MIT in its README
  but ships no licence file: a claim is not a grant. johnkoht ships no licence
  at all, which is all rights reserved. Any design assuming "four open-source
  repos" is wrong and would produce a legally contaminated corpus.
- Range of validity: renemarc targets HA 0.108 and uses syntax removed years
  ago. Its *patterns* are the most instructive of the four (publish/subscribe
  modes, sensor calibration pipeline, notify groups); its *YAML* is unusable.
  This forces the corpus to separate concept from expression.

## Goals / Non-Goals

**Goals:**

- A completeness guarantee that is measured against something other than
  itself: selected and excluded must close over `git ls-files`, every excluded
  file names the rule that excluded it, and per-repo golden files fail an
  over-broad exclude.
- A provenance guarantee: every behaviour row traces to the repos and licence
  terms it came from.
- A licence gate enforced mechanically — by deriving each repo's code and prose
  status from its licence records, and by checking a row's text against the
  entity references extracted from the raw records that row cites — rather than
  by discipline.
- A vocabulary that provably comes from more than one author.
- A frozen schema set and module layout that later phases cannot silently
  diverge from, carrying only the invariants an empty tree can actually fail.

**Non-Goals:**

- No engine logic, integration, panel or simulator behaviour. Phase 0 emits
  data, schemas and empty module skeletons; the layout and purity rules are
  asserted on empty trees, and become load-bearing in Phase 1.
- **Not** the adapter contract suite or the registry client. Both are
  unfalsifiable with empty trees; `spec.txt` assigns them to Phases 1, 4 and 7.
- **Not** the export round-trip. Phase 0 freezes the export document schema and
  ships a static example that validates; serialise/re-import is Phase 3.
- **Not** the separate `registry/` repository itself — Phase 0 cannot create a
  repository outside this project root, so it defers creation to Phase 7 and
  holds only the "no pointer files here" half of the invariant.
- No attempt to *run* a reference config. renemarc cannot load on modern HA;
  johnkoht and fwartner need ~40 and ~90 external integrations. Booting them is
  neither possible nor necessary.
- Not a translation of the four configs into one `configuration.yaml`. The
  output is a corpus of behaviours and slot needs.
- Not the full behaviour vocabulary *content* — 0b freezes the schema that
  defines the vocabulary and proves it with an example pack; populating the
  official pack set is Phase 2.

## Decisions

### D1 — Two-stage extraction: deterministic sweep, then curated classification

An LLM read of 8,400 files is slow, non-reproducible and silently lossy —
exactly the failure the completeness requirement forbids. A parser cannot
classify `generic` vs `personal`, which is judgement.

**Stage A** walks each repo, partitions every tracked file into *selected*
(configuration, which enters the corpus and gets an artifact class) or
*excluded* (matched by an ordered exclusion rule with a reason), parses the
parseable selected files into normalised records (`id/alias`, triggers,
conditions, actions, and referenced `domain.identifier` strings classified as
`entity_ref` or `service_call` by structural position) without interpreting
them, and records the parse outcome as a field separate from the class so class
counts still sum. It emits `catalog/inventory.json`, `catalog/hardcoded_refs.yaml`
and `catalog/raw-behaviors.json` — all three committed and fact-only — plus
`.local/raw-verbatim.json`, gitignored, holding the unfiltered
extraction. See D3 for why the split falls where it does.

The rules are an **ordered list, first match wins**, and the deciding rule is
recorded per file. Each select rule **carries the artifact class it confers**, so
a file's class is read off its deciding rule rather than inferred from its
contents — the contents live in a clone and the rule list does not, which is
what lets the golden-path and class-pinning checks run in CI with no clone
present. An earlier draft left the class origin unstated, and the gap was
load-bearing: without it the completeness checks silently needed the clones
back. This is not a stylistic choice: the patterns these repos
actually contain overlap unavoidably — a vendored `custom_components/**/*.js`
bundle matches a vendored-tree rule, a `custom_components/**` rule and a
`**/*.js` rule at once, and every `.github/workflows/*.yml` matches both a CI
rule and a generic `**/*.ya?ml` rule. A "matches exactly one rule" constraint,
which an earlier draft imposed, would fail on every real repo or force the rules
to be written as mutually exclusive — paying a real cost in rule readability for
no property anyone needs. Ordering gives the same guarantee for free: the
partition still closes, and the rule that decided each file is still recorded,
so an over-broad exclude is still visible.

Two things keep completeness from being circular. First, the partition must
*close*: selected plus excluded equals the repo's `git ls-files` count, counted
independently, and every excluded file names the rule that decided it — so
nothing can be dropped silently by an over-broad exclude. Second, each repo has
a committed **golden file** of representative paths that must be selected; an
exclude rule that swallows real configuration fails on a golden path rather
than quietly shrinking the corpus, and a golden file that has fallen behind its
repo's artifact classes fails the coverage check rather than merely reporting.

**Stage B** takes the raw extraction and writes the shipped corpus: merging
duplicates, choosing among competing approaches, assigning slots and scope, and
classifying. Stage B never reads the repos directly — every shipped row cites
the raw ids it came from, so an unclassified artifact is visible as a raw row
no shipped row claims.

Alternative considered: agents read the repos and write the corpus. Rejected —
no completeness proof, and re-running gives silent divergence with no diff.

Alternative considered: generate the corpus fully mechanically. Rejected —
renemarc's 2020 syntax and fwartner's German aliases cannot be merged without
judgement, and `personal` vs `generic` is inherently semantic.

### D2 — Concept and expression are separate fields, not separate documents

The two non-granting repos still carry the best structural ideas, so the corpus
needs to record them without transcribing them, and the boundary has to be
mechanical. So each behaviour row carries `concept` (our own prose, always
populated, always permitted) and `expression` (source-derived structure,
populated only when every source's *code* status is `reusable`).

The check that backs this has to be precise or it rejects its own output. A
naive "no shipped text contains any identifier from any non-granting repo"
fails immediately: `light.turn_on` appears dozens of times in both unlicensed
repos and would match every lighting behaviour, and `light.kitchen_lights`
exists in both a granting and a non-granting repo, so a legitimate CCOSTAN
expression would be rejected for a name johnkoht happens to share. So the check
is **provenance-resolved and scoped**: service calls are never identifiers
(stage A classifies each reference structurally), and the comparison is only
against `entity_ref`s extracted from the raw records *that row cites*. The gate
guards every free-text field a row carries — `name`, `concept`, `description`,
`change_notice`, slot examples — and iterates the row's schema's string fields
rather than a hand-kept list, since the field the list forgot is the field the
identifier leaks through.

Alternative considered: keep one `triggers/conditions/actions` block and mark
rows `ideas_only`. Rejected — that transcribes first and asks permission later;
the guard becomes a comment rather than a check.

### D3 — Licences are derived per artifact kind, not typed

`catalog/licenses.yaml` is written before any corpus row. Status and
obligations are *computed* from `license_code` and `license_prose` by the
validator using a normative table published in the `attribution` spec, and a
hand-written status the table contradicts fails the build. The same spec
publishes the total order over licences (`public_domain < mit < apache_2_0 <
cc_by_nc_sa < no_licence`) that every "most restrictive" rule reads. Nothing in
the corpus defines its own ordering.

**Status is per artifact kind, not per repo.** An earlier draft collapsed each
repo to one status by taking the stricter of code and prose, which wrongly
quoted renemarc out of the corpus: its *code* is Apache-2.0 and its *prose* is
CC BY-NC-SA, and what we reuse from it is code. So the record carries
`reuse_status_code` and `reuse_status_prose`, behaviour rows — which reuse code
— follow the code status, and quoted documentation follows the prose status.

Consequence stated plainly: **two of four repos contribute concepts only.**
johnkoht (`no_licence` for both) and fwartner (`no_licence` for both, despite
the README claim) cannot donate expression — an alias, a display name, a
comment, a block of YAML. CCOSTAN (`mit`) and renemarc (`apache_2_0` code) can,
with attribution — and renemarc additionally incurs the `state_changes`
obligation, so adapted files carry a change notice. That still shrinks the licit
corpus, but it is the honest position and it is visible in the derived statuses
rather than quietly padded with paraphrase.

**An earlier draft of this decision said the two repos "cannot donate
identifiers" as well, and that was wrong in a way that contradicted the phase's
own deliverables.** An entity identifier — `light.kitchen_ceiling` — is a fact
about a naming scheme, not authored text, and the hardcoding audit `spec.txt`
requires cannot be performed at all without recording identifiers from every
repo. What a licence withholds is expression: the alias a person typed, the
comment they wrote, the YAML they arranged. So the split is not
"granting repos versus non-granting" applied to everything a repo contains; it
is **facts for all four repos, expression for the two that grant it**, and that
is exactly the line `.local/` draws in the repository:

- the committed `catalog/raw-behaviors.json` and `catalog/hardcoded_refs.yaml`
  carry facts — paths, ids, classes, vocabulary terms, slot bindings,
  `domain.object_id` strings — for every repo, with no field that can hold
  authored text;
- the gitignored `.local/raw-verbatim.json` carries everything,
  including the withheld text, and exists only so the local prose gate can
  compare a shipped passage against its source.

That is why the earlier `"distribution": "internal-only"` marker was the wrong
shape: it labelled a committed file as non-distributed, and nothing enforced
the label. A boundary that is a `.gitignore` entry is a boundary; a boundary
that is a string in the file it is meant to restrict is a wish.

**Enforcing "no field that can hold authored text" took three attempts, and the
first two failed the same way — by moving the guarantee rather than establishing
it.** The first said the committed store's `name`, `alias`, `description` and
comment fields are `null` for non-granting sources: a hand-typed list of four
names on a schema that is deliberately mutable, which is the exact defect this
package had just cured on the shipped-row side. The second replaced the list
with an allowlist — but defined it as the raw-record schema's own field set, so
the checked set and the checking set were one object and the guard could never
fire; adding a field to the schema added it to the allowlist in the same
instant. The third attempt is what is specified now, and it is three things
together: the allowlist is a **named committed artifact**
(`schemas/catalog/fact-fields.yaml`), the schema is **generated from it** so the
field names cannot drift and the check can fail in both directions, and the
allowlist constrains **types** — every field is an enum, an integer, a boolean,
a list of those, or a string naming a shape from a closed committed set, and a
free-form string is inadmissible.
The third clause is the one that carries weight: it is what stops a later round
adding `description: {type: string}` to the mutable schema, which the first two
attempts would have waved through.

The third attempt's own first draft failed for the same reason as the two before
it: it said "a pattern-constrained string", and any pattern satisfies that —
`^.*$` is a pattern — so the guarantee had moved into the regex. What closes it
is that a string fact names a shape from a **closed committed set**
(`schemas/catalog/fact-shapes.yaml`: `entity_ref`, `dotted_id`, `slug`,
`selected_path`) rather than carrying a pattern its author writes. The `path`
fact is membership rather than a pattern for a concrete reason: a regex tight
enough to exclude authored text also rejects real tracked paths, because
fwartner's filenames are German and real paths carry spaces and non-ASCII, so
membership in the committed selected-path set admits every real path while
admitting no prose. That is the fourth version of this paragraph, and the one
difference that matters is that the constraint is now a committed artifact a
reviewer can read, so there is no fifth level for it to move to.

### D4 — Normalise to a shared vocabulary, not to either repo's shape

Every extracted behaviour is rewritten into our own field names regardless of
whether the source used `trigger:` or `triggers:`, `service:` or `action:`,
packages or top-level files. This is what makes "the same behaviour in two
repos" comparable, which is the only way the overlap requirement can be
satisfied at all.

### D5 — Entity references become slot candidates via a role lexicon

Stage A emits `entity_ref`s — never service calls, which are classified out —
into `catalog/hardcoded_refs.yaml`, each with an inferred scope (a room, or
`house`). A role lexicon maps
`(domain, name pattern, room context)` to a slot candidate such as
`motion_sensor`, `lux_sensor`, `light_group`, `house_mode`. Slot names come
from a controlled vocabulary; multi-source slots must carry examples from ≥2
repos, so the vocabulary cannot be one author's spelling.

Alternative considered: derive slots from entity domains alone. Rejected —
`binary_sensor` is a motion sensor in one place and a door contact in another;
the role is not recoverable from the domain.

### D6 — Rooms and houses both need a scope

House-level behaviours (away shutdown, house modes, alarm, notification
routing) are the product's core, and their required slots are not provided by
any room. So the room-type map defines room types *and* a `house` scope, each
behaviour carries `scope: room | house`, and the slot-availability check is
scope-aware. Without this the validator would reject exactly the behaviours the
product needs.

### D7 — One schema home; immutability measured against git, not a manifest

0b's schema set is the contract later phases write against, so it must not move
under them and it must not fork. `schemas/` is the single home: each of the
eight runtime concepts gets a directory, `schemas/<concept>/`, holding one file
per version, and the catalog's own data schemas live under `schemas/catalog/`
and *reference* the current runtime `slot` and `room-type` versions rather than
restating them. The conformance check scans every file outside the version
directories — packs, engine code, the panel, and **`schemas/catalog/`
included** — because that directory is the likeliest hiding place for a second
shape definition and is exactly the case the check exists for. An earlier draft
said "every file outside `schemas/`", which excluded the one place its own
motivation named.

**Per-version filenames are forced, not stylistic.** An earlier draft required
"exactly one versioned schema per runtime concept" *and* "the superseded version
remains present with a `superseded_by` pointer". Those two cannot both hold when
a concept is one file: overwriting the file destroys the superseded version, and
not overwriting it leaves two current schemas for one concept. Version
directories resolve it — the superseded version is a file that is simply no
longer the one any successor names.

**Succession points backwards, and that direction is the whole point.** A second
draft kept `superseded_by` and merely moved the schemas into version
directories, which relocated the contradiction instead of resolving it: the
pointer would have to be written into the version being retired, which is a
content change to a file the immutability rule freezes, so publishing any
successor would fail the check that publishing is supposed to be able to
satisfy. The fix is to put `supersedes` in the **new** file and define the
current version as the one no other version names. Publishing then writes to no
existing file, retiring a version costs nothing, and the retired version stays
byte-identical to the day it landed. Two attempts failed here before the
direction of the arrow turned out to be the defect, which is worth recording:
the rule looked like a formatting choice and was a soundness one.

Immutability is enforced against **git history**, not a committed hash
manifest: the check compares a schema file's current content with its content at
the commit that first introduced that path. A manifest committed alongside an
edit would update itself and pass, which is no check at all. That premise has a
consequence worth stating: the project root must actually be a git repository,
which it currently is not, so putting it under version control is the first task
of the phase, and `ressources/` is ignored rather than committed because the
four clones are separate repositories with their own histories and licences.

Immutability covers the eight runtime schemas and **explicitly not**
`schemas/catalog/`. The runtime schemas are a published contract that Phases 1–8
write against; the catalog schemas describe this repository's own intermediate
artifacts, which nothing outside it consumes. Freezing those would buy no
contract for anyone and would turn every later task that adds a field to a
catalog file into a version bump — and the catalog files are exactly the ones
whose fields the extraction teaches us as it runs. A fix that makes the gate
satisfiable by exempting the thing the gate was never for is a scope decision,
so it is written down rather than left implicit.

The schemas therefore land **first**, before any catalog data or catalog
schema. Every catalog data file validates against a schema, and the catalog
schemas must reference the runtime `slot` and `room-type` shapes rather than
restate them, so a task list that authors the schemas last cannot be executed in
order. The vocabulary schema is created with the other seven, carrying its
shape and the terms known at that point, and gains its derived terms later as a
**new version file** — which is what the version directories are for, and why
the vocabulary is the one runtime schema expected to have more than one version
in Phase 0.

One deviation from `spec.txt` is recorded here rather than glossed. `spec.txt`
line 41 describes the room-type and slot schemas as "derived from the catalog";
this design authors them *before* the catalog, because the catalog's own files
must validate against them and the catalog schemas must reference them. The
derivation is real, but it runs the other way in time: the corpus is written to
the schema, and where the corpus turns out to need a shape the schema lacks, the
schema gains a version rather than the corpus gaining an exception.

The example house and pack are hand-written because a generated example proves
only that the generator and the schema agree, not that a human can write to the
schema. Provenance is a committed `HANDWRITTEN` allowlist — a marker, not
detection, and named as such so it is not mistaken for a guarantee.

Two 0b clauses are deliberately deferred and recorded rather than faked: the
export document is frozen as a schema plus a static example, with the
round-trip and re-link behaviour left to Phase 3 as `spec.txt` assigns it; and
the separate `registry/` repository is created in Phase 7, because Phase 0
cannot create a repository outside this project root. Phase 0 holds only the
part of the registry invariant it can test — that no pointer files accrete
here.

### D8 — Module skeletons land now, and only the invariants they can carry

`engine/ ha_adapter/ custom_components/ panel/ packs/official/ sim/` are
created empty in Phase 0. Empty is enough to enforce the rules that are cheap
now and expensive later: the layout exists; the four *Python* packages are
importable and typed (`panel/` is TypeScript, `packs/official/` is YAML, so
neither is subject to that rule); the engine imports no `homeassistant`,
including guarded and `TYPE_CHECKING` imports; and no registry pointer files
are committed here.

What is deliberately *not* asserted in Phase 0: the `HouseAdapter` contract
suite and the registry client. Both were in an earlier draft and both were
unfalsifiable — with empty trees there are no adapters to test and no client to
inspect. `spec.txt` assigns the adapter contract to Phases 1 and 4, and the
registry client to Phase 7; asserting them here would install a gate that
cannot fail, which is worse than no gate.

## Risks / Trade-offs

- **Over-merging.** Two similar behaviours may merge into a row whose
  `required_slots` is the union of both, producing a behaviour no real house can
  satisfy. → Every merged row records the chosen and rejected approaches, and
  the scope-aware slot check fails any row whose required slots its scope
  cannot supply.
- **The licit corpus is much smaller than it looks.** → Stated in the proposal
  and D3; the corpus marks provenance so the shrinkage is visible.
- **Stage A silently drops what it cannot parse.** → Parse outcome is a field,
  not a filter: unparsed files keep their class and their error, appear in the
  class counts, and surface in the completeness report.
- **`other` becomes a dumping ground, and `unclaimed` an escape hatch.** → Both
  require an explicit justification from a closed set — `other` reasons are
  `unknown_artifact`, `generated_file`; unclaimed reasons are
  `non_behavior_file`, `duplicate_of`, `vendor_config`, `malformed` — both are
  counted on every run, and a large count is a visible smell, not a hidden one.
  Exclude rules take precedence over classification, so a file cannot reach
  `other` by one route and the exclude list by another; clone tooling is an
  exclude rule, not an `other` reason, which is what removed `clone_tooling`
  from that set.
- **`custom_integration` and `blueprint` would be unreachable classes**, and
  an earlier draft got the reason wrong. It claimed every `custom_components/`
  tree is vendored third-party code; that is false. CCOSTAN's
  `tesla_charge_guard` and `alexa_camera_compat` are the author's own, each
  declaring `"codeowners": ["@CCOSTAN"]`. renemarc's `github_custom` and
  `gtfs_custom` are locally altered copies of core components and
  `doomsday_clock` a copy from another of the author's repositories — neither
  authored originals nor third-party vendored. And `blueprints/` has the same
  shape: fwartner carries 64 blueprint files, 61 under 32 other authors'
  namespaces and 3 under its own. → The authored paths are listed in `file_rules.yaml`
  under `authored_paths`, each naming the class it must receive, overriding the
  vendored excludes; the list must contain at least one `custom_integration` and
  at least one `blueprint` entry, so "reachable" has a case that fails when it
  is not; and material that is neither the author's original nor third-party
  vendored — the altered core copies — is excluded by a rule with its own
  reason rather than being attributed to the repo author.
- **Non-permissive prose leaks through the code-shaped gate.** The reuse gate
  is about identifiers and expression, and renemarc's prose terms are
  non-commercial and share-alike, so prose copied into `docs/` would trigger
  virality the code check never looks for. → A second gate rejects verbatim
  prose from any repo with `reuse_status_prose: ideas_only`.
- **Vendored third-party code carries its own licences** and must not be
  attributed to the repo author nor treated as covered by the repo licence. →
  Vendored trees are excluded by a named rule with a reason and never enter the
  corpus, and the licence records carry the carve-out explicitly.
- **An over-broad exclude rule shrinks the corpus undetected.** → The partition
  must close over `git ls-files`, every excluded file names its rule, per-rule
  counts are reported, and each repo's golden file fails any configuration path
  that stops being selected. The pinned class list is what stops the check going
  quiet exactly when it matters: a class whose every file was swallowed is not
  "present in the tree" under any measurement of the inventory, so a check
  phrased that way would find nothing to complain about.
- **CI cannot reach the reference clones.** They are `gitignore`d, two of the
  four grant no licence to redistribute, and pinning CI to four third-party
  repositories means the gate fails when they change rather than when we do. →
  The clones are an input to a local, one-time generation step whose outputs are
  committed; CI validates the committed corpus, schemas, examples and
  invariants and requires no network. Exactly two checks are local — the closure
  check over `git ls-files`, and the prose gate against the gitignored
  `.local/raw-verbatim.json` — and they are named as local in the specs rather
  than left for a maintainer to discover from a red pipeline. The
  golden-path and class-pinning checks are **not** among them: they are pure
  functions of the committed rule list and the committed golden files, so they
  run in CI. An earlier draft called them local because the walker they grew out
  of is local, which excused a check from the pipeline for sitting near one that
  genuinely could not run there.
- **The identifier check only catches exact identifiers.** A sufficiently
  paraphrased transcription would pass. → Accepted limitation; it is a floor
  raised above "nothing", not a proof. Stated so it is not mistaken for one.
- **The fact/expression line is a judgement, not settled law, and the package
  states it as though it were settled.** Treating an entity identifier as a fact
  is what lets `hardcoded_refs.yaml` be a deliverable at all, but identifiers are
  also the author's own choices — fwartner's are German, which is a selection
  someone made — and selecting and arranging identifiers is the kind of thing
  that can attract protection in some jurisdictions independently of copyright.
  → The position is taken deliberately and for a stated reason: `spec.txt`
  requires a complete hardcoding audit, and an audit that may not record the
  identifiers is not an audit. But it is written in the specs as the boundary
  rather than as a contested reading of it, and that overstates it. What is
  actually established is narrower: the non-granting repos' **authored text** is
  withheld, and their identifiers are recorded and confined to fact fields. If
  either author objects, the remedy is to delete the identifiers for that repo
  from the committed corpus and keep the audit local — which the `.local/` split
  already makes a one-line change rather than a redesign. Recorded here so the
  judgement is visible as a judgement, and so the fallback is not discovered
  under pressure.
- **The `HANDWRITTEN` allowlist is a marker, not detection.** A generator that
  declines to add itself passes. → Named as a marker in the spec and here, so
  it is not relied on for more than it does.
- **Frozen schemas may prove wrong once Phase 1 exercises them.** → The
  supersede-not-edit rule makes the correction cheap and visible; the cost of
  freezing is bounded by that rule.
- **Author contact is outward-facing.** → Recorded as `not_attempted` pending
  the user's authorisation rather than sent unilaterally.

## Migration Plan

Greenfield — nothing to migrate. Rollback is deleting the Phase 0 artifacts;
only the invariants would then be re-added, so the true rollback cost is the
corpus. Work order is forced by what each check needs to exist, and one edge of
it runs backwards from the obvious reading: the runtime schemas must be authored
**before** the catalog data and the catalog schemas, because every catalog data
file validates against a schema and the catalog schemas must reference the
runtime `slot` and `room-type` shapes rather than restating them. So:

version control → module skeletons and tooling → runtime schemas (version 1.0.0)
→ catalog schemas and data stubs → pre-commit and CI wiring → licences → file
rules, `authored_paths` and golden files → inventory and stage A extraction →
curated rows → the identifier gate (it needs both the rows and stage A's
identifier sets, and being a function of committed artifacts it runs in CI) →
the prose gate → slots and rooms → edge cases, pain points and attribution →
the vocabulary's derived terms as a new schema version, the immutability check
and the example artifacts → the final CI gate.

Two boundaries are worth restating because they cut across that order. The
clone-reading steps — the stage A sweep and the prose gate, which compares
shipped text against the gitignored verbatim store — run locally and their
outputs are committed; everything after them, and CI itself, reads only this
repository. The golden-path and class-pinning checks are deliberately on the far
side of that line, because nothing about them needs a clone. And the vocabulary's terms arrive late
enough that publishing them means adding `schemas/behavior-vocabulary/1.1.0.json`
declaring `supersedes: 1.0.0` and never touching `1.0.0.json` — which is the
mechanism the version directories exist to make possible, exercised once, in
Phase 0, on the one schema guaranteed to need it.

## Open Questions

None. The remaining unknowns — the concrete slot vocabulary, and which
behaviours end up `generic` — are corpus *content*, settled by the rules the
specs fix, not by the specs themselves. `discard` retention is settled: rows
are kept with `retention: audit`.
