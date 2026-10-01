# Tasks

Order is the dependency order. Every group lands the tests and the documentation
its own work calls for; the last group is integration only. Nothing here is run
before a harsh critic has approved this change package.

## 1. The schema chain and the two published versions

- [x] 1.1 Add `schemas/pack-manifest/1.2.0.json` superseding `1.1.0`: close
  `kind` to the five kinds, add `dependencies`, `conflicts`, `engine_api`,
  `license` and `i18n`, add `derives_from` (the corpus row ids a derived pack
  reproduces expression from), extend `$defs.provided.class` with `mode` while
  the other ten values are unchanged, retire `min_engine_version`, add the
  per-kind conditionals, and widen `$defs.behaviour` with `priority`, `services`
  and `slots`.
  Verify with a test that the five kinds validate and `lighting` does not, that
  `min_engine_version` is refused under `additionalProperties: false`, that a
  behaviour declaring none of the three takes the published default priority, an
  empty service set and no slot of its own, that a `slots` naming a slot the pack
  does not declare is refused, that a `provides` entry of class `mode` validating
  against `schemas/mode/1.0.0.json` is admitted while a non-mode file declaring
  that class is refused, that the other ten class values still validate, and that
  a `derives_from` on a pack listed in `HANDWRITTEN` is refused.
- [x] 1.2 Add the engine API version artifact and read it through
  `engine/vocabulary.py`; verify a test reads the declared version and that
  `pyproject.toml`'s `0.0.0` is not it.
- [x] 1.3 Add `catalog/pack-policy.yaml` with its four sections (declarative
  subset, banned services, flagged services, the default arbitration priority),
  each carrying its reason; verify a test reads each section through the
  vocabulary gateway, that a service is not bannable from code, and that a
  behaviour omitting `priority` takes the value that file publishes.
- [x] 1.4 Add the schema-chain check: each published version names its
  predecessor, exactly one head exists, and each published schema's digest matches
  the recorded one. Verify by amending a published schema and watching the check
  fail.
- [x] 1.5 Add each published licence code's SPDX identifier to
  `schemas/catalog/licenses.json`, leaving the `licenceValue` enum and the
  published order unchanged. Verify a test reads the identifier for `mit` and
  `apache_2_0`, and that the five codes and their order are what they were.
- [x] 1.6 Document the chain, the two versions, the API version policy and the
  licence codes with their SPDX identifiers in `docs/reference/`; verify the
  documented paths exist as written.

## 2. The fan role and slot (a Phase 0 artifact, amended on evidence)

- [ ] 2.1 Add `fan` to `ROLE_VOCABULARY` in `tools/catalog/lexicon.py` and the
  matching slot to `catalog/slots.yaml`, with `accepts_domains: [fan]` and
  examples citing the `fan.*` entity references recorded in
  `catalog/raw-behaviors.json`. Verify `tools/catalog/slots.py` recomputes both
  and reports no drift.
- [ ] 2.2 Verify the slot addition does not disturb the other fourteen: run the
  catalog's own validation and confirm the existing slots and room types are
  unchanged.
- [ ] 2.3 Record in `docs/reference/` why the role is derivable rather than
  invented, naming the observed references; verify the referenced paths exist.

## 3. The sandbox

- [ ] 3.1 Implement the declarative-subset check, reading the forbidden terms
  from `catalog/pack-policy.yaml`; verify a test refuses `if`, `choose`,
  `parallel`, `repeat`, `variables`, `stop` and `template` with the
  "published but not declarative" class, and refuses an unpublished term with the
  "unknown" class.
- [ ] 3.2 Implement the entity-reach check: a command may name only an entity
  bound to one of the pack's own slots. Verify a test admits a slot-bound entity,
  refuses a literal entity id, and leaves a behaviour reaching through an unbound
  optional slot inert.
- [ ] 3.3 Implement the declared-service check, computing the effective
  permission set from the manifest's behaviour clauses. Verify a test shows an
  added behaviour clause widens the set with no second list edited.
- [ ] 3.4 Implement the banned-service refusal and the flagged-permission
  surfacing, distinguished from each other and from an undeclared service. Verify
  tests for each of the three outcomes, including that a flagged pack still
  installs.
- [ ] 3.5 Implement the `provides` check: each path resolves inside the pack,
  escapes nothing, and the file is of the declared class. Verify a test refuses a
  dangling path, a traversal path and a class mismatch, and that
  `packs/official/example-pack.yaml`'s current dangling path is caught.
- [ ] 3.6 Add the layering check for the new engine modules and verify it fails
  when a sandbox module imports `sim/` or `openhouse/`.
- [ ] 3.7 Document the sandbox's four rules and the three failure classes in
  `docs/reference/`; verify every class named in the doc is one a test produces.

## 4. The manifest validator

- [ ] 4.1 Implement validation against the current schema version only, with the
  failing instance path and constraint named; verify a `1.1.0`-valid document is
  refused with the current version named.
- [ ] 4.2 Implement the `engine_api` range check with its own failure class;
  verify a test distinguishes it from a schema failure and from a dependency
  failure.
- [ ] 4.3 Implement the licence check against the published SPDX list and the
  derivation gate over `catalog/behaviors.yaml`. Verify a test refuses a pack
  naming an `ideas_only` row, refuses an incompatible licence, and accepts a pack
  over the reusable rows alone.
- [ ] 4.4 Implement the i18n resolution: default strings required, locale
  overrides resolved over the default, override-without-default refused. Verify
  tests for fallback, override and the refusal.
- [ ] 4.5 Implement the vocabulary reference for behaviour terms: terms resolve
  against `behavior-vocabulary/1.1.0.json`, the manifest schema restates no term.
  Verify a test refuses an unpublished term and a check confirms the schema
  contributes no terms of its own.
- [ ] 4.6 Implement dependent/conflict declaration parsing at the manifest
  level (each a name and a semver range, self-dependency refused). Verify tests
  for a malformed range and a self-dependency.
- [ ] 4.7 Correct `packs/official/example-pack.yaml` to a real kind and a
  resolving `provides` path; verify it validates and is still listed in
  `packs/official/HANDWRITTEN`.
- [ ] 4.8 Document the manifest's clauses with a worked example in
  `docs/reference/`; verify the example validates as written.

## 5. The install lifecycle

- [ ] 5.1 Implement dependency resolution against the installed set and
  conflict refusal, with the refusing pack named on both sides; verify tests for a
  satisfied dependency, an unsatisfied one, and a symmetric conflict.
- [ ] 5.2 Implement cycle detection over the dependency edges; verify a test on
  an A↔B pair reports the cycle rather than an unsatisfiable dependency.
- [ ] 5.3 Implement the install record (name, version, manifest digest, bound
  slots and entities, satisfied dependencies, flags) as session state. Verify a
  test reads it back, and that an edited manifest without a version bump makes the
  digest mismatch reportable.
- [ ] 5.4 Verify the record round-trips a snapshot and a restore, and that a
  restore cannot resurrect a house whose packs are gone.
- [ ] 5.5 Verify installation is not activation for a pack: six behaviours
  arrive disabled, an enabling act is separate and named, and a tick after install
  produces no proposal of the pack's.
- [ ] 5.6 Implement uninstall with the dependent refusal; verify tests for a
  refused uninstall naming the dependent and an uninstall leaving no behaviour,
  record or later-tick proposal.
- [ ] 5.7 Implement the version change (upgrade/downgrade) with the
  breaking-downgrade refusal; verify tests for a replacement rather than an
  accumulation and for a refused downgrade.
- [ ] 5.8 Verify install atomicity: a failure at each check in turn leaves the
  house unchanged and a later valid install unaffected.
- [ ] 5.9 Document the lifecycle and the record's fields in
  `docs/reference/`; verify the fields the doc names are the fields a test reads.

## 6. The exit criterion: two modules cannot fight over one light

- [ ] 6.1 Extend arbitration so two installed packs' proposals for one entity
  reduce to one command by the declared priorities, with a published default and
  the tie broken by ascending behaviour `id`. Verify a test where two packs
  propose for one light yields exactly one command and records the losers.
- [ ] 6.2 Verify the install-order independence as a property: the same two
  packs installed in either order, replayed over the same inputs, produce
  identical decision logs. Verify with a property test over the order pair.
- [ ] 6.3 Verify a user action still outranks both packs, and that no tie-break
  consults install order, pack name or evaluation order. Verify by a test that
  would fail if any of the three were consulted.
- [ ] 6.4 Document the arbitration rule and the published default in
  `docs/reference/`; verify the doc's rule matches the test that enforces it.

## 7. The official packs

- [ ] 7.1 Write the Bedtime button pack (lights off, Sleep mode, optional
  thermostat, lock clause disabled by default) and its scenario; verify one act
  turns the light off and enters Sleep mode, the thermostat clause is inert
  without a climate zone, and the lock is untouched by default.
- [ ] 7.2 Write the Roomba button pack with the four states and the
  notification, and its scenario; verify idle starts, cleaning sends home, paused
  resumes, stuck notifies, and the fifth reported state is inert.
- [ ] 7.3 Write the bathroom fan pack as a hand-written pack over the new `fan`
  slot, and its scenario; verify the fan runs on and stops after the declared
  run-on period, and that the pack reproduces no expression of
  `modes.room_mode_off`.
- [ ] 7.4 Write guest mode as a `profile-set` pack conferring a mode with an
  `exclusive_group`, and its scenario; verify it validates against
  `schemas/mode/1.0.0.json`, installs disabled, and that exclusivity refuses a
  conflicting pair — and that a manifest declaring room-profile selections is
  refused against the current schema, the boundary with Phase 3 stated as a
  refusal.
- [ ] 7.5 Write the six default room templates and the house template, and their
  scenarios; verify each declares only slots its `provides_slots` (or the `house`
  entry) carries, and that a foreign slot is refused.
- [ ] 7.6 Verify two shipped modules cannot fight over one light, reachable by
  installing the two packs in either order.
- [ ] 7.7 Document the shipped set and each pack's scenario in
  `docs/reference/`; verify every pack named in the doc has a scenario that
  passes.

## 8. The corpus-derived packs

- [ ] 8.1 Implement the derivation over `catalog/behaviors.yaml`, gated on
  `reuse_status` and licence, emitting into `packs/derived/` only, each emitted
  pack carrying its `derives_from` clause and emitting the files its own
  `provides` entries name so each path resolves inside the pack. Verify it
  produces no pack for an `ideas_only` row, and that an emitted pack validates
  under the current schema.
- [ ] 8.2 Verify the report names every skipped row with its reason and does not
  cap its output; verify the 19-reusable / 64-ideas-only bound is stated in the
  report.
- [ ] 8.3 Add the check that refuses a generated pack under `packs/official/`,
  and verify `HANDWRITTEN`'s rule still holds in both directions.
- [ ] 8.4 Verify the derivation is reproducible: two runs from the same corpus
  produce byte-identical packs.
- [ ] 8.5 Document the derivation, its gate and its bound in
  `docs/reference/`; verify the documented command reproduces the shipped tree.

## 9. Triggers

- [ ] 9.1 Implement trigger binding to a declared behaviour for both buttons,
  with a trigger that names an undeclared behaviour refused. Verify tests for both.
- [ ] 9.2 Implement the dashboard button as the control surface's `user_action`
  and verify the vocabulary is unchanged by this phase.
- [ ] 9.3 Implement the physical button over the published `device`/`event`
  trigger kinds, reached through a slot; verify a pack naming a device id is
  refused and that a press is injectable with no HA installed.
- [ ] 9.4 Verify a trigger is not a second execution path: a trigger carrying an
  action is refused, and a triggered act passes the sandbox unchanged.
- [ ] 9.5 Verify a press enters arbitration as the behaviour's proposals by
  declared priority, does not outrank as a user action would, and that a
  disabled behaviour's button does nothing.
- [ ] 9.6 Document the two buttons and how each resolves in `docs/reference/`;
  verify the doc's resolution matches the vocabulary's published kinds.

## 10. The pack CLI

- [ ] 10.1 Implement `validate` over a directory with the per-failure class and
  a non-zero exit on any failure; verify a clean directory passes, one bad pack is
  named among good ones, and an empty directory exits non-zero.
- [ ] 10.2 Implement `test` with the untested-versus-passing distinction and the
  fixture-error class; verify a pack with no scenario is reported untested.
- [ ] 10.3 Verify a failing scenario's report carries its inputs and the
  decision log in the scenario runner's own form, and that a passing scenario's
  report is quiet.
- [ ] 10.4 Implement `diff-permissions` over two manifests, reporting gained and
  lost permissions with a gained one classified flagged or banned, without an
  install. Verify it runs with no house and that `validate` emits no diff.
- [ ] 10.5 Make all three verbs operations on the CLI, the library and the MCP
  server; verify each face reports equivalently and that the control-surface check
  fails for a CLI-only verb.
- [ ] 10.6 Verify the three exit statuses are distinguishable (success, pack
  failure, usage error) and that a skipped manifest is never counted as passing.
- [ ] 10.7 Document the three verbs, their outputs and their statuses in
  `docs/reference/`; verify each documented invocation runs as written.

## 11. Integration: the phase's exit criterion and the gate

- [ ] 11.1 Verify the exit criterion against the shipped set: every official and
  derived pack validates, every scenario passes, and the two-modules-one-light
  property holds over shipped packs in either install order.
- [ ] 11.2 Add the `phase-2-packs` mapping to the acceptance tool and verify
  `python -m tools.acceptance --change phase-2-packs` reports zero unenforced
  requirements and names a test for every requirement in all six specs.
- [x] 11.3 Verify the docs build and the repository's checks pass
  (`ruff check`, `ruff format --check`, the pyright probe at its baseline, the
  catalog checks), and record any check left red with its reason rather than
  leaving it implied.
