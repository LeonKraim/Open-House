# Design — Phase 2: The pack system

## Context

See `proposal.md` — Why. The constraints this design works inside, and nothing
else:

- **Phase 1 shipped the engine and the machinery a pack plugs into**: slot
  binding (`engine/binding.py`), the layered config resolver (`engine/config.py`),
  modes with exclusive groups (`engine/modes.py`), arbitration by a total order
  (`engine/arbitration.py`), the decision log (`engine/decision_log.py`), the
  scenario runner, and `install_pack` at a scope its own spec states in full.
  `engine/arbitration.py`'s docstring already names this phase's exit criterion
  and already rejects last-writer-wins by scheduling order, so the exit criterion
  is a property to *preserve* rather than a mechanism to build.
- **Phase 0 froze the artifacts this phase reads**: `schemas/pack-manifest/1.0.0`
  and `1.1.0`, `schemas/behavior-vocabulary/1.1.0` (15 triggers, 11 conditions,
  14 actions), `schemas/mode/1.0.0` (`name`, `description`, `exclusive_group`),
  `catalog/behaviors.yaml` (83 rows), `catalog/slots.yaml` (14 slots),
  `catalog/room_types.yaml` (20 types, 6 marked `default`, plus a `house` entry
  with 8 slots), `catalog/licenses.yaml`.
- **`engine/vocabulary.py` is the engine's one reader of those artifacts.** Its
  module docstring fixes why, and warns against a second definition of anything
  already published.
- **Sequencing**: 1.0.0 → 1.1.0 added `kind`, `optional_slots` and `behaviours`
  and declared `supersedes: 1.0.0`; both schemas set
  `additionalProperties: false`, so a clause the current schema does not carry is
  refused rather than ignored. That is the machinery the `engine_api` retirement
  depends on.

## Goals / Non-Goals

**Goals:**

- A manifest complete enough to state what a pack needs (`requires_slots`,
  `optional_slots`, `dependencies`, `conflicts`, `engine_api`) and what it may do
  (`provides`, `behaviours`, `license`), validated against a versioned chain.
- A pack format a hostile pack cannot exceed: declarative only, slot-reachable
  entities only, declared services only, with a banned list that refuses and a
  flagged list that warns.
- The exit criterion as a property: the same two packs in either installation
  order produce the same decision log.
- The official packs as shipped artefacts with scenarios, and the corpus-derived
  set bounded by the corpus's own licence data.

**Non-Goals** (design-level boundaries; `proposal.md` — Impact carries the
phase-level ones):

- No pack-supplied code, templates, expressions or JS at any point. A pack is
  data and the interpreter has nothing that evaluates a string a pack supplied.
- No store, registry, signing, tiers or revocation — Phase 7.
- No profile activation, hysteresis or dwell — Phase 3. A `profile-set` pack
  confers a **mode**, whose schema is Phase 0's; the room-profile *selections* it
  bundles need a selection schema Phase 3 publishes, so this phase neither carries
  nor activates them, and the current schema's `additionalProperties: false`
  refuses a clause for one rather than letting a later phase's format be invented
  here. The frozen `schemas/profile/1.0.0.json` is the whole-house bundle
  (`name`/`description`/`modes`/`enabled_behaviours`) a house activates — the
  shape `spec.txt:65` gives Phase 3 — and it has no section for a room selection,
  which is why the pack declares the mode and not the bundle.
- No panel, no websocket, no browser requirement — Phase 5. A dashboard button is
  an operation, not a view.
- No Home Assistant dependency in this phase. Everything here runs on the fake
  house; Phase 4 supplies the real adapter to the same interface.

## Decisions

### D1 — The engine's API version is a published artifact, not the packaging version

`engine_api` needs something to be checked *against*, and nothing in the
repository declares it: `pyproject.toml` says `version = "0.0.0"`, which moves for
packaging reasons and would make every pack range a lie. **Decision:** the engine
publishes its API version in an artifact of its own, read by both the engine (for
its own value) and any validator (for a pack's range), so there is one source and
no importer. **Alternatives:** *package metadata* — rejected, it is `0.0.0` and is
a packaging value, not a promise about what a manifest may declare; *a constant in
`engine/`* — rejected, a validator outside the checkout would have to import the
engine to check a range, which inverts the dependency; *a git tag* — rejected,
tags do not travel with a wheel or a copy.

The version moves only when what a manifest may *declare* changes
incompatibly — not when the engine's implementation changes. The `kind` closure
below is the first such change, which is why the phase must publish a version
before it can ship a pack that names one.

### D2 — Pack policy lives in one data artifact that references the vocabulary

The declarative subset, the banned-service list, the dangerous-permission flags
and the default arbitration priority have no home today. **Decision:** one
artifact, `catalog/pack-policy.yaml`, with a section per concern, each naming
vocabulary terms or Home Assistant services and never restating the vocabulary
itself; read through `engine/vocabulary.py`, the one gateway. **Alternatives:** *three files* — rejected as three things to keep in
sync for one reader; *a new vocabulary version that marks each term declarative* —
rejected because `behavior-vocabulary/1.1.0.json`'s own terms are what the
extraction *observed* across the four estates, and "may a pack use this" is a
judgement about those terms rather than an observation of them — the artifact that
records observations should not be amended to carry a policy; *the banned list in
the interpreter* — rejected outright, because banning a service would then be an
engine release.

`catalog/` rather than a new directory because it already holds both kinds of
file: `slots.yaml` and `licenses.yaml` are derived vocabularies and
`file_rules.yaml` is a policy, so a policy file among policy files is the
established shape.

The licence clause takes the same route rather than opening a second list.
`spec.txt:56` asks for "SPDX license", and the repository's published licence
vocabulary is `schemas/catalog/licenses.json`'s `licenceValue` enum —
`public_domain`, `mit`, `apache_2_0`, `cc_by_nc_sa`, `no_licence`, "ordered least
to most restrictive" — which is what `catalog/behaviors.yaml`'s rows already
speak (`license: mit`, `license: apache_2_0`). **Decision:** the manifest's
`license` takes those codes, and this change adds each code's SPDX identifier to
that artifact, so the clause is an SPDX licence *through* the published
vocabulary. **Alternatives:** *a manifest carrying `MIT`/`Apache-2.0` directly* —
rejected, it makes a third spelling of one fact and leaves the derivation gate
comparing an SPDX string against a corpus code; *a new SPDX list file* — rejected
for the same reason as the three-policy-files alternative above, with less
excuse, since the vocabulary already exists.

### D3 — `1.2.0` closes `kind`, supersedes `1.1.0`, and is the only version install accepts

`1.1.0`'s `kind` is a pattern whose own description defers the closure: "A pattern
and not an enum because no corpus of packs exists to derive a closed set of kinds
from, and inventing one here would freeze…". Phase 2 ships the corpus (the
official packs are the pack corpus the sentence was waiting for), so **Decision:**
`1.2.0` supersedes `1.1.0`, closes `kind` to the five, adds `dependencies`,
`conflicts`, `engine_api`, `license` and `i18n`, retires `min_engine_version`,
adds per-kind conditionals, adds `derives_from` (the corpus row ids a derived
pack reproduces expression from), **extends `$defs.provided.class`** — which
`1.1.0` closes at ten values, none of which a mode file could honestly be
declared as, while a `profile-set` pack confers exactly that: guest mode is a
mode in `schemas/mode/1.0.0.json`'s sense and neither a `helper` nor a `scene`
nor any of the other eight, and `mode` alone rather than `profile` beside it,
because the frozen `schemas/profile/1.0.0.json` is the whole-house bundle Phase 3
activates and carries no room-selection section — see Non-Goals — and **widens `$defs.behaviour`** — which `1.1.0` closes
to `name`/`trigger`/`condition`/`action` with `additionalProperties: false` — with
`priority`, `services` and `slots`. The three are not optional extras: the exit
criterion ranks two packs' competing behaviours by a priority the manifest must be
able to state, the sandbox admits only services the manifest declared, and the
sandbox's reach rule plus the physical-button trigger both need the behaviour to
say *which* slot it acts through — without them, three requirements in this
package would be asking a closed clause for data it cannot carry, and a pack
declaring two slots could not say which one a behaviour reads. They are clauses of
the behaviour rather than of the pack because all three are per-behaviour facts:
one pack's two behaviours may compete at different priorities, call different
services and read different slots. The class extension is a fourth act and a
different kind of one — not a clause added to a definition but one value added to
a closed enum, which is why the requirement says the other ten are unchanged. It
is not optional either: a `profile-set` pack has to declare what it provides as
one of the ten, and the honest answer for a mode file is a class the enum does not
have — a pack would otherwise be forced to declare a mode as a `helper`, which is
the kind of mislabel the `provided` definition's own description exists to
prevent. **Install validates against the current version only**;
superseded versions stay on disk and are not accepted, which is what makes the
closure a visible act. **Alternatives:** *accept any published version* — rejected,
the closure would then be unenforced for every pack written yesterday and the
"BREAKING" claim would be false; *edit `1.1.0` in place* — rejected, a pack that
validated yesterday must be re-checkable against the schema it was written for.

`min_engine_version` is retired rather than kept beside `engine_api` because it
pins a lower bound and cannot express an upper one, so a pack broken by the next
major version could not say so. Two clauses answering one question differently is
how a format acquires a clause nobody can explain.

### D4 — The declarative subset is a list of forbidden terms, not a list of allowed ones

The vocabulary's 14 actions include `if`, `choose`, `parallel`, `repeat`,
`variables`, `stop`, `wait_for_trigger` and `wait_template` as well as `service`,
`scene`, `delay`, `event`, `condition` and `device`. **Decision:** the subset names
the terms a pack may **not** use, and the reason each is excluded is part of the
artifact, so the failure message can say "published but not declarative" instead
of "unknown". **Alternatives:** *an allowed-list* — rejected, an allowed-list
silently admits any term a future vocabulary version adds, which is the wrong
default for a sandbox; *no list, interpreter checks terms by shape* — rejected, a
shape rule ("no term containing `if`") is a heuristic where a published list is a
fact.

`template` and `wait_template` are excluded for a reason worth stating: a Jinja
template is an expression language over the house, so admitting it would give a
pack exactly the evaluation surface D5 says the interpreter does not have.

### D5 — The sandbox is enforced at validation, and there is no runtime guard

A pack that names a literal entity or an undeclared service fails **validation**;
the engine never sees it. **Decision:** the interpreter's capability surface is
"resolve a slot to a bound entity" and "call a declared service on it", and it has
no branch, loop, variable or expression evaluation — so an installed pack *cannot*
exceed its grant, rather than being *stopped* from exceeding it.
**Alternatives:** *a runtime guard that declines an out-of-grant command* —
rejected, because a guard implies the interpreter can express the thing being
guarded; the failure would be an incident in the decision path and a bug in the
guard would be a security bug rather than a validation bug.

### D6 — Dependencies resolve against the installed set, by name and range, with a cycle check

**Decision:** a dependency is a pack name and a semver range; resolution consults
the house's installed set; a cycle is detected by depth-first traversal over the
dependency edges and refused naming the cycle; conflicts are checked
symmetrically, from the arriving pack to the installed set and from each installed
pack to the arriving one. **Alternatives:** *a general constraint solver* —
rejected as disproportionate for name-plus-range pairs, and it would make a
refusal's reason hard to state; *transitive installation from a repository* —
rejected, this phase has no store (Phase 7) and a pack that pulls another in
silently is the opposite of "installation is an act".

### D7 — The install record is session state, so a snapshot round-trips it

**Decision:** the installed set, with each pack's name, version, manifest digest,
bound slots and entities, satisfied dependencies and flags, is part of the state a
session holds and a snapshot serialises, so `snapshot`/`restore` (Phase 1's
operation) round-trips an install and a restore cannot resurrect a house whose
packs are gone. **Alternatives:** *a file beside the checkout* — rejected, it
would be a second store the session does not know about and a restore would not
touch; *deriving installed packs from the pack directory* — rejected, "what is
installed here" is house state and not what happens to be on disk.

The manifest digest is stored because the version is not enough: a manifest edited
without a version bump is exactly the case a digest catches and a version cannot.

### D8 — The exit criterion is a determinism property over the install order

**Decision:** the property is stated as *the same two packs in either installation
order, replayed over the same inputs, produce the same decision log*, and it is
tested as a property over the pair rather than asserted. Nothing in arbitration
may consult install order, pack name or evaluation order; a tie breaks by the
ascending behaviour `id` Phase 1 fixed, and a pack's competing priority is what
its manifest declares, with a published default for a behaviour that omits one.
**Alternatives:** *install order as the tie-break* — rejected, it is
last-writer-wins by another name and `engine/arbitration.py` already rejects that;
*pack name as the tie-break* — rejected, it is install-order-independent but makes
a rename change behaviour, which is a worse surprise.

### D9 — Generated packs live in `packs/derived/`, and `packs/official/` stays hand-written

`packs/official/HANDWRITTEN` fixes its rule in its own comment: *"every pack file
in this directory to be named here, and every name here to be a file that
exists"*. A generated pack in `packs/official/` would therefore have to be listed
as a file a person wrote, which is false. **Decision:** derived packs live in
`packs/derived/`, and a check refuses a generated pack under `packs/official/`.
**Alternatives:** *extend `HANDWRITTEN` with an origin column* — rejected as a
change to a Phase 0 artifact that buys nothing the directory split does not, and
it would make one file carry two meanings; *omit the generated packs from the repo
and generate on demand* — rejected, the phase's exit criterion says the official
packs pass validation, and a pack that is not in the repository cannot be a
shipped pack.

### D10 — The `fan` role is added on observed evidence, and the drift check proves it

`spec.txt:58` requires a bathroom fan pack; `ROLE_VOCABULARY` is a closed
fourteen-tuple with no fan, and `tools/catalog/slots.py` fails a slot outside it;
the corpus does record `fan.mudroom_bathroom_fan` and `fan.powder_room_fan`, as
entity references of raw records in `catalog/raw-behaviors.json`.
**Decision:** add `fan` to `ROLE_VOCABULARY` and
`catalog/slots.yaml` with `accepts_domains: [fan]`, citing the observed
references, and let `tools/catalog/slots.py`'s recomputation prove the two agree.
The addition is **derived** — the domain was observed — which is the only kind of
addition the controlled vocabulary exists to admit. **Alternatives:** *bind the fan
through an existing slot* — rejected, none fits and a `fan` bound to a
`light_group` is a lie the binding layer would later have to unlearn; *ship the
bathroom fan without a slot* — rejected, a pack whose device it cannot legally
name is not a pack.

The fan pack is hand-written because the only row citing a fan is
`modes.room_mode_off`, `ideas_only` and `no_licence`, so its expression may not be
reproduced — the licence gate, applied to a real case rather than a hypothetical.

### D11 — The derivation is deterministic and reports its bound

**Decision:** the derivation over `catalog/behaviors.yaml` sorts by row id, emits
sorted keys, embeds no timestamp, and produces byte-identical output on a rerun;
and it reports every skipped row with its reason, so the set's size is a printed
consequence of the corpus (19 reusable rows: 10 `mit`, 9 `apache_2_0`, against 64
`ideas_only`) and not a number a reader must trust. **Alternatives:** *a generation
timestamp in the output* — rejected, it makes every rerun a diff and hides a real
corpus change among noise; *capping the output silently* — rejected, a silent cap
reads as "everything derivable was derived".

### D12 — New engine modules, and the layering that keeps the engine portable

The sandbox, the pack validator, the dependency resolver and the trigger
resolution are new modules in `engine/`, importing nothing from `sim/` or
`openhouse/`; the CLI and MCP faces live with the other faces the way Phase 1 put
them, and `openhouse/` composes. **Alternatives:** *the sandbox in `openhouse/`* —
rejected, the engine must run unchanged against a real adapter in Phase 4, and a
sandbox that reached into the fake house would only work on the fake house; *the
validator in `tools/`* — rejected, the CLI is a face over the library, so the
check must be in the library the CLI calls.

## Risks / Trade-offs

- **[Closing `kind` invalidates every pack written to `1.1.0`, including the
  shipped example]** → intended and visible: the example is corrected in this
  change, the version bump is the announcement, and the refusal names the current
  version and the superseding clause. The migration cost is one file today because
  no other pack exists.
- **[A published API version becomes a promise the project can forget to
  move]** → the version moves only on a change to what a manifest may declare, and
  the check that compares a schema's digest against the recorded one is where that
  decision is forced rather than remembered.
- **[The `fan` role amendment touches a Phase 0 artifact whose checks recompute
  it]** → `tools/catalog/slots.py` recomputing the vocabulary and the domains is
  the mitigation: the two files cannot disagree silently.
- **[Deriving from the corpus yields less than a reviewer expects, because 64 of
  83 rows grant nothing]** → the derivation reports the bound and each skipped
  row; the honest small number is the point, and the alternative is an official
  pack built on expression the project may not reproduce.
- **[Determinism is easy to assert and easy to break]** → the property is tested
  as a property over the install-order pair rather than as an example, and
  arbitration's total order already forbids the inputs that would break it.
- **[The install record must survive a snapshot, and it is new state]** → it is
  part of the session's state rather than a file, so the existing `snapshot` /
  `restore` round-trip is the test rather than a new mechanism.
- **[A trigger is a plausible place to smuggle an action]** → a trigger carries a
  behaviour name and nothing else, and the check that a trigger declares no action
  of its own is what makes a button a way in.

## Migration Plan

No deployment and no data migration: nothing in the repository installs a pack
yet, so there is no installed set to migrate. The steps, in order, are the tasks'
order: publish `1.2.0` and the engine API version; add `catalog/pack-policy.yaml`;
add the `fan` role and slot; write the sandbox and the validator; grow
`install_pack`; write the official packs and the derivation; add the CLI verbs and
the triggers. **Rollback** is the revert of the change package: `1.1.0`, `1.0.0`
and `behavior-vocabulary/1.1.0.json` are untouched by design, so the schema chain
reverts to a head that still validates every pack that existed before this phase.

## Open Questions

- **The banned list's exact membership.** The design fixes the rule (a service
  acts on the host rather than on a device) and the artifact carries the entries;
  which entries satisfy the rule is deferrable without changing the approach, the
  specs or the task breakdown.
- **The first published API version literal.** The ranges in this phase's own
  packs are written against whatever the artifact declares; the literal does not
  change any requirement.
- **How many derived packs the 19 reusable rows group into.** The gate and the
  reporting are fixed; the grouping is a property of the corpus and is answered by
  running the derivation.
