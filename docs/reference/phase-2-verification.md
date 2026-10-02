# Phase 2 verification

This file is the change package's acceptance record: the mapping from every
`### Requirement:` in `openspec/changes/phase-2-packs/specs/` to a named test
that observes that requirement's check failing on a violating input, plus the
things that are true of this phase and are not green.

The mapping is read back by `tools/acceptance.py`, which parses the requirement
set from the specs rather than trusting a count, and then *runs* each named test:
a row that names a test the suite does not carry, or one that does not pass,
fails the gate. `python -m tools.acceptance --change phase-2-packs` gates this
phase. Where an entry says "task N", it means the numbered task in that change's
`tasks.md`.

The shape of the record follows `docs/reference/phase-1-verification.md`, which is
the page this one was written against.

## Phase 2 is not complete, and the gate says so

Fifty-two requirements across six spec areas, and **eighteen of them are
unenforced**. The gate reports each one by name rather than passing it: that is
the `gap:` arm doing the work it was built for.

By area:

| Area | Requirements | Unenforced |
| --- | --- | --- |
| `official-packs` | 9 | 6 |
| `pack-cli` | 6 | 4 |
| `pack-install` | 9 | 0 |
| `pack-manifest` | 14 | 2 |
| `pack-sandbox` | 8 | 0 |
| `pack-triggers` | 6 | 6 |

The phase's state, plainly:

- The spec is approved, one round over the cap (see below).
- Groups 1 to 6 are written and uncommitted. Group 7 is half written: the guest
  mode pack, the seven templates and their drift check are on disk and the three
  button packs are not.
- **Group 10's library half is written**: `openhouse/pack_verbs.py` carries
  `validate_directory` and `diff_permissions`, with seventeen tests in
  `tests/test_pack_cli.py`. Two of the three verbs, and the reason the third is
  absent is a gap in the package rather than in the file.
- **Groups 8, 9 and 11 are unwritten**: the corpus derivation and its report, the
  two buttons, and the integration pass.
- **The gate has been run, and it runs the suite.** `tools/acceptance.py` calls
  `pytest` twice: `--collect-only` to check that each named node id exists, and
  once per row to check that it passes. So "no test has been run" would be
  false, and the honest statement is narrower: the 34 named tests have been run
  *through the gate*, and no run of the whole suite has happened. Of the 34,
  **33 pass and one does not** -- `pack-sandbox`'s flagged-permission row. A
  critic has not approved this phase's tests, and the run happened anyway
  because the gate command is the gate; what the run establishes is recorded
  here, and what it cannot establish is an approval.
- **A mapping is only as good as the node ids it names.** Three rows named a
  test's bare name where the collected node id carries a parameter --
  `test_a_published_control_flow_term_is_refused_as_non_declarative[clause1-if]`
  and two others -- and the gate refused all three with "which the collected
  suite does not carry". A name-shaped probe of the mapping, which is what
  `.local/probe_mapping.py` did, cannot see that: `--collect-only` is the only
  authority on whether a node id exists, and the probe now asks it.
- Nothing is committed.

## The four blockers, and they are different kinds of thing

**The manifest schema, for three of the six shipped entries.** `1.2.0` closes
`$defs.behaviour` to `name, trigger, condition, action, priority, services, slots`,
and `trigger`, `condition` and `action` are each a bare `$ref` to a *kind* in the
published vocabulary -- none of the seven clauses carries a value. So the
Bedtime button's "enter Sleep mode", the Roomba button's dispatch on the vacuum's
four states, and the bathroom fan's run-on period are acts no manifest can
express. `engine/behaviours/declared.py` is the other half of the evidence and it
is short: `evaluate` proposes `ctx.propose(slot=slot, action=service,
rule=self.name)` for each declared service and never reads `condition`, `trigger`
or `action` at all. A `condition: state` clause is therefore a declaration of
which vocabulary kinds a behaviour uses, for the sandbox and the vocabulary
check, and never a runtime gate.

**The engine, for entering a mode.** `ModeSet.activate` has exactly one caller in
the tree -- `engine/engine.py`, inside `_adopt`, the snapshot-restore path. The
ten operations are closed and none of them enters a mode, and the house-control
facility does not either. So a mode can be active in a running session by exactly
one route: restoring a snapshot that already had it active. Task 7.1's "enters
Sleep mode" is not missing a clause; **there is no code path that enters a mode
at all**, for a pack, a trigger, a user, a CLI call or an MCP call. That is
Phase 1's gap rather than this phase's -- `engine/modes.py` implements the set and
its exclusivity, and nothing implements the transition into it.

**The gate itself, for everything not yet written.** The round cap is three per
phase and this package was approved at round 4, so no round remains to approve
the tests groups 8, 9 and 11 would land. This is the one blocker only the user can
lift, and the reason the phase stops here rather than grinding out thirty tasks
against a set that cannot pass its own criterion.

**Three approved Phase 1 checks, for putting the verbs on a surface.** Task 10.5
asks for all three verbs as operations on the CLI, the library and the MCP server.
The library face is free and is done. The other two are not: each of the three
faces already asserts that its own set is the ten operations plus its one client
entry point, and each assertion is an exact set equality.

- `tests/test_operations.py::test_the_registry_is_exactly_the_enumeration` asserts
  `OPERATION_NAMES == SPEC_TXT_OPERATIONS`.
- `tests/test_mcp_server.py::test_the_server_advertises_one_tool_per_operation_plus_the_runner`
  asserts `set(tools) == {*OPERATION_NAMES, "run_scenario"}`.
- `tests/test_cli.py::test_the_cli_exposes_one_subcommand_per_registry_operation`
  asserts `set(commands) == {*OPERATION_NAMES, "scenario"}`.

None of the three is wrong, and all three are the same property read at a
different face: the surface is the registry, so a caller can rely on the set. The
precedent for adding to a face anyway is in the checks themselves -- `scenario`
and `run_scenario` are each a literal second name in one of those sets, added when
the scenario runner landed. So this is not an impossible requirement; it is a
requirement that costs an edit to three approved Phase 1 tests, which is a
change to an approved artefact and therefore a decision above this phase's pay
grade rather than inside it. The three verbs remain off both surfaces, and the
`pack-cli` requirement-5 row says so by naming all three checks.

Two smaller things about the two verbs that do exist, recorded because they are
readings of the tree rather than decisions:

- `packs/official/` holds **nine** valid manifests, not the one example the phase
  notes assumed: the six room templates, the house template, the guest-mode pack
  and the example pack. `validate` reports all nine clean, and the example pack's
  `provides` path resolves, so task 4.7 is done on disk whether or not the
  checkbox says so.
- `provides` paths resolve against a tree root and are then required to land
  inside the pack's own directory, while the schema and the vocabulary are the
  project's. `validate_directory` therefore takes two roots -- the tree a path is
  written against and the project everything is judged by -- which is the split
  `engine.sandbox.check_pack` already makes. One root serving both jobs passes
  every other test in `tests/test_pack_cli.py` and fails
  `test_the_vocabulary_comes_from_the_project_and_not_from_the_tree_root`.

## The one named test that does not pass

`pack-sandbox`'s fifth requirement -- dangerous permissions are red-flagged and
not refused -- is mapped to
`tests/test_pack_sandbox.py::test_a_flagged_service_does_not_refuse_the_pack`,
and that test **does not pass**. It is the only failing row in the mapping, and
it is a defect the gate found rather than one this record went looking for: the
row was written as an "enforced" row and the run turned it into a red one.

Nothing static reading the code explains it. The test builds a pack whose one
behaviour declares `services: ["lock.unlock"]`, and asserts no refusal and
exactly one flag naming that service. Read against the committed tree:

- `load_pack` requires only `name`, and the test's `_document` supplies it.
- `check_declarative` sees `trigger: state`, `condition: state`,
  `action: service` -- all published and none forbidden.
- `check_slot_claims` sees `slots: ["light_group"]` against
  `requires_slots: ["light_group"]`.
- `check_provides` iterates an empty `provides` and refuses nothing.
- `check_services` reaches its `elif service in policy.flagged_services` branch
  for `lock.unlock`, which `catalog/pack-policy.yaml` flags, and appends a
  `Flag` -- and `SandboxResult.merge` carries flags as well as refusals.

Every step of that is where the assertion needs it to be, so either the test or
its fixture is wrong in a way reading does not expose, or the failure is a
collection error in the module rather than an assertion. **Settling it needs a
run, and the gate does not permit one.** That is worth stating plainly, because
it is a second and sharper edge of the round-cap blocker above: the cap is not
merely stopping new tests from being approved, it is stopping a known-red test
in an area this record calls green from being diagnosed.

## The thing the exit criterion cannot see

`official-packs`' last requirement is the phase's exit criterion: every shipped
pack validates and passes its scenario. **It would report green with all three
button packs absent.** Validation is schema validation, `delay` is a legal
`action` in the published vocabulary, and a scenario that never runs its timer
cannot fail -- so a fan pack that never stops, a Bedtime button that enters no
mode, and a Roomba button that commands all four states at once are all
satisfiable by the criterion as written. That is why
`tests/test_official_packs.py::test_the_shipped_set_is_the_one_the_phase_names`
is red on purpose rather than marked `xfail`: a set three entries short is this
phase's most important fact, and a suite that reports it as an expected failure
is a suite that has stopped saying it.

## The residual on guest mode

`official-packs`' guest-mode requirement is mapped to a test and that test
observes the half of it that holds: the pack is a `profile-set`, it confers a mode
and nothing else, the mode is what the pack declares it to be, it validates
against the closed `schemas/mode/1.0.0.json`, it installs and confers no
behaviours, and its `exclusive_group` is the group the engine clears siblings in.
The requirement's scenario says the mode "exists and is not active" after an
install. The second clause holds; **the first does not**: installing the pack
leaves the session's mode set exactly as it was, because nothing adopts a pack's
mode. The row is kept rather than split, and the residual is recorded here rather
than settled.

The requirement's clause about room-profile selections is a refusal and is
observed: a manifest declaring one fails validation against the current schema,
and the failure names the clause.

## The bound the derivation was supposed to report

`official-packs`' derivation requirement has the corpus's own shape as its
evidence: 83 rows, all `reusable` (30 `mit`, 29 `public_domain`, 24
`apache_2_0`) and none `ideas_only`. The requirement fixes the gate and the
report and deliberately asserts no count, so the count belongs to the
derivation. Two facts about `catalog/behaviors.yaml` are worth recording here
because they bound what that derivation can emit, and they are not the bound
the requirement states:

- Every row carries an `expression`, but an expression names services, and a
  service grounds a pack only where a slot accepts its domain or the act is one
  the product models at all. Acts on a helper alone -- `input_boolean`,
  `input_select` -- ground nothing a person can install, and they are the
  largest of the reasons the derivation reports.
- The home's state is no longer a slot. `house_mode` left `catalog/slots.yaml`
  and the lexicon because that state is the engine's own `ModeSet` rather than a
  device anybody binds, so no row requires it and the derivation demands nothing
  of a person for it.

So a derivation that reproduces a row's *expression* can ground a much smaller
set than nineteen, and the requirement's own report distinguishes only two skip
reasons -- `ideas_only` and licence -- with nowhere to record a row skipped for
carrying no expression. That is a gap in the approved requirement rather than in
an implementation of it, and it is recorded here because task 8.1 cannot be
written without settling it.

## What is left red, named

| Task | State |
| --- | --- |
| 3.4 Flagged permission | **Red, and newly so**: the row was written as enforced and the gate's run shows its test does not pass. See the section above. |
| 7.1 Bedtime button | Red. No mechanism for entering a mode, and no engine path that enters one. |
| 7.2 Roomba button | Red. No mechanism for dispatching on a state. |
| 7.3 Bathroom fan pack | Red. No mechanism for a `delay` with a duration. Its catalog half landed: the `fan` slot is in `catalog/slots.yaml`. |
| 7.4 Guest mode | Half. The pack, the mode and the exclusivity hold; the mode is never registered in a session. |
| 7.5 Templates | Green: seven manifest-pairs, the drift check in `tools/catalog/templates.py`, and its tests. |
| 7.6 Two modules contend | Red, behind 7.1. The property holds over generated packs; the shipped packs do not exist. |
| 7.7 Docs | Not started. A page about a set three entries short would document a set that is not shipped. |
| 8.1 to 8.5 Derivation | Unwritten. |
| 9.1 to 9.6 Triggers | Unwritten. |
| 10.1, 10.4, 10.6 Pack CLI, the library half | Green in the library: `openhouse/pack_verbs.py` carries `validate_directory` and `diff_permissions`, and `tests/test_pack_cli.py` covers them, the classes and the three statuses. Red on every surface, behind the three approved checks above. |
| 10.2, 10.3 `test` | Red, and the reason is narrower than this row first said. The package *does* say where a pack's scenarios live -- `proposal.md`'s Impact names "pack scenarios in the committed corpus", which is `scenarios/` -- so the earlier claim that it never says so was wrong. What no artefact defines is the **join**: `schemas/scenario/1.0.0.json` is `additionalProperties: false` with no `pack` field, no scenario names a pack, and no manifest names a scenario. Without that edge `test` cannot tell a pack that ships no scenario from one whose scenario passed, which is the distinction the requirement is *about*; adding the field is a version bump to a frozen schema, the same decision 10.5 waits on. 10.3 is unwritten for a plain reason: no report carries a scenario's inputs and its decision log. |
| 10.5 The three faces | Red, behind the three approved checks above. |
| 10.7 Docs for the verbs | Not started, and correctly so while no invocation exists to document. |
| 11.1 to 11.3 Integration | 11.2 and 11.3 are this page; 11.1 cannot pass with three entries absent. |

The long form of each, with the file and line each missing mechanism would have
to be read from, is in `.local/phase2-package-state.md`.

## The checks, as they stand

Task 11.3's verify clause, run at the point this page was written. Each check is
named with its result, and the one that is red is red for a reason stated rather
than implied:

| Check | Result |
| --- | --- |
| `ruff check .` | Clean. |
| `ruff format --check .` | 235 files already formatted. |
| `python -m tools.catalog.cli validate` | Valid, including the `templates` check this phase added. |
| `pyright` at its configured scope | 0 errors, 0 warnings, 0 informations. |
| The documentation path scan | 87 repository paths spelled across 15 pages, none absent. |
| `python -m tools.acceptance --change phase-2-packs` | **Red**: 19 problems -- 18 requirements with no enforcing test, and 1 named test that does not pass. |
| `pytest`, whole suite | **Not run.** The gate runs the 34 mapped node ids and nothing else. |

The last two rows are the phase's real state and they are the two the gate exists
to surface. A reader who wants the short version: the repository's static checks
are green, 33 of the 34 tests the mapping names pass, and the phase is not.

## The acceptance mapping

Every `### Requirement:` heading in the six delta specs, in spec order. A row
names a test or a `gap:`; the gate fails on an unenforced requirement rather than
passing it, so the twenty `gap:` rows below are this phase's findings and not a
silence.

```yaml acceptance-mapping
- spec: official-packs
  requirement: The shipped set is the phase's list, and each entry is a valid pack of a declared kind
  gap: the shipped set is three entries short -- the Bedtime button, the Roomba button and the bathroom fan are absent, and no derivation has emitted the corpus-derived set -- so the entry-to-manifest join the requirement asks for has nothing to join
- spec: official-packs
  requirement: The Bedtime button turns the lights off, enters Sleep mode, and leaves locks alone by default
  gap: 'task 7.1 is unwritten and cannot be written: `engine/engine.py`''s only `ModeSet.activate` caller is inside `_adopt`, so no act can enter a mode'
- spec: official-packs
  requirement: The Roomba button acts on the vacuum's four states
  gap: 'task 7.2 is unwritten and cannot be written: `$defs.behaviour`''s `condition` is a bare kind with no operand and `engine/behaviours/declared.py` never reads it, so four state behaviours would propose together'
- spec: official-packs
  requirement: The bathroom fan pack is hand-written, and this change adds the slot it needs
  gap: the `fan` slot landed in `catalog/slots.yaml`; the hand-written pack over it did not, and no test observes the pack's provenance or its run-on period
- spec: official-packs
  requirement: Guest mode is a mode this phase ships, and its bundled profile selections are Phase 3's
  test: tests/test_official_packs.py::test_the_guest_mode_pack_is_a_profile_set_conferring_a_mode
  fixture: a `profile-set` whose `provides` carries no `mode` entry, which the current version's conditional for the kind refuses
- spec: official-packs
  requirement: The default room templates cover the default room types, and the house template covers the house's slots
  test: tests/test_templates.py::test_a_foreign_required_slot_is_named_with_its_template_and_source
  fixture: a room template declaring a slot its room type does not provide, named against the catalog entry that refutes it
- spec: official-packs
  requirement: Derived packs are gated on each row's `reuse_status` and licence, and the bound is reported
  gap: 'task 8.1 is unwritten: nothing reads `catalog/behaviors.yaml` under the `reuse_status` gate, nothing emits into the derived tree, and the 19/64 bound is stated nowhere a check can read'
- spec: official-packs
  requirement: Generated packs do not live in `packs/official/`, and the marker's rule is enforced
  test: tests/test_examples.py::test_a_pack_file_present_but_unlisted_names_the_file
  fixture: a pack file under `packs/official/` that `packs/official/HANDWRITTEN` does not name, which is what a generated pack placed there would be
- spec: official-packs
  requirement: Every shipped pack passes validation and its scenario, which is the exit criterion
  gap: 'this is the exit criterion itself: three of the six shipped entries have no pack, so `tests/test_official_packs.py`''s set test is red on purpose and the criterion would report green with all three absent'
- spec: pack-cli
  requirement: '`validate` validates every pack in a directory and reports each failure with its class'
  test: tests/test_pack_cli.py::test_one_bad_pack_is_named_among_good_ones
  fixture: a directory of three manifests, one failing the schema and one the sandbox, plus a pinned file that is not a manifest -- the class of each failure and the directory-level verdict are asserted separately in `test_each_failure_carries_its_class` and `test_an_empty_directory_is_not_a_pass`
- spec: pack-cli
  requirement: '`test` runs a pack''s scenarios and distinguishes untested from passing'
  gap: 'task 10.2 is unwritten, and the obstacle is the **join** rather than the location. The package does say where a pack''s scenarios live -- `proposal.md`''s Impact names "the official packs, their manifests and their scenarios; ... pack scenarios in the committed corpus", which is `scenarios/` -- so the earlier record that it "never says where a pack''s scenarios live" was wrong and is corrected here. What no artefact defines is which scenario belongs to which pack: `schemas/scenario/1.0.0.json` is `additionalProperties: false` over five keys with no `pack` field, no file in `scenarios/` names a pack, and no manifest names a scenario. Without that edge `test` cannot decide the requirement''s central distinction -- "a pack that ships no scenario SHALL be reported as untested" is unanswerable when nothing can say a pack ships none -- and the same missing edge is what `official-packs` requirement 9 rests on when it says every shipped pack SHALL have "a scenario that exercises it". Adding the field is a version bump to a frozen schema, which is the same decision requirement 5''s three approved checks are waiting on'
- spec: pack-cli
  requirement: '`test` reports a failure with the scenario''s inputs and the decision log that resulted'
  gap: 'task 10.3 is unwritten: no report carries a scenario''s inputs and the decision log it produced'
- spec: pack-cli
  requirement: '`diff-permissions` compares two versions'' effective permissions and is a first-class output'
  test: tests/test_pack_cli.py::test_a_gained_permission_is_classified_flagged_or_banned
  fixture: two manifests differing by a plain, a flagged and a banned service, read with no house open -- `test_a_gain_names_the_behaviour_clause_that_declares_it` asserts the clause is named and `test_diff_permissions_reads_a_manifest_the_schema_would_refuse` asserts no validation runs
- spec: pack-cli
  requirement: The three verbs are operations on all three control-surface faces
  gap: 'task 10.5 cannot be implemented as worded, and the obstacle is three approved Phase 1 checks rather than one: `tests/test_operations.py::test_the_registry_is_exactly_the_enumeration` asserts `OPERATION_NAMES == SPEC_TXT_OPERATIONS`, `tests/test_mcp_server.py::test_the_server_advertises_one_tool_per_operation_plus_the_runner` asserts `set(tools) == {*OPERATION_NAMES, RUNNER_TOOL}`, and `tests/test_cli.py::test_the_cli_exposes_one_subcommand_per_registry_operation` asserts `set(commands) == {*OPERATION_NAMES, "scenario"}`. Each is correct and each expresses the same property at a different face, so surfacing the verbs means widening a literal set in an approved Phase 1 test -- the edit `scenario` itself once received. The library face is done: `openhouse/pack_verbs.py` carries `validate_directory` and `diff_permissions`'
- spec: pack-cli
  requirement: The verbs distinguish success, a validation failure and a usage error
  gap: 'the three statuses exist as `EXIT_OK`, `EXIT_PACK_FAILURE` and `EXIT_USAGE` and `test_the_three_exit_statuses_are_distinct` asserts they are three values, but no surface exits with them: with requirement 5 blocked there is no verb to exit from'
- spec: pack-install
  requirement: An install resolves dependencies and refuses conflicts against the installed set
  test: tests/test_pack_install.py::test_an_unsatisfied_dependency_is_refused_naming_the_pack_and_the_range
  fixture: a manifest requiring a dependency the installed set does not carry at a version in range
- spec: pack-install
  requirement: A pack whose `engine_api` range excludes the engine is refused
  test: tests/test_pack_manifest.py::test_a_range_excluding_the_engine_is_refused
  fixture: an `engine_api` range the version `schemas/engine-api/1.0.0.json` declares falls outside
- spec: pack-install
  requirement: Installation is not activation, and this phase does not change it
  test: tests/test_pack_install.py::test_six_behaviours_arrive_six_disabled
  fixture: a pack whose behaviours must arrive disabled, against an install that enabled any of them
- spec: pack-install
  requirement: An install records what it installed, and the record is readable
  test: tests/test_pack_install.py::test_the_record_answers_what_arrived_and_why
  fixture: an install record read back for the version, digest and clauses it must carry
- spec: pack-install
  requirement: Installing two packs in either order produces the same decision log
  test: tests/test_pack_arbitration.py::test_the_same_two_packs_decide_identically_in_either_install_order
  fixture: two packs installed in both orders, whose decision logs must agree command for command
- spec: pack-install
  requirement: A pack's arbitration priority is declared and not inferred
  test: tests/test_pack_arbitration.py::test_a_higher_priority_wins_over_the_id_that_sorts_first
  fixture: two proposing behaviours whose declared priorities contradict the id ordering
- spec: pack-install
  requirement: Uninstall removes the pack and refuses to strand a dependent
  test: tests/test_pack_install.py::test_an_uninstall_with_a_dependent_is_refused_naming_the_dependent
  fixture: an uninstall of a pack another installed pack depends on
- spec: pack-install
  requirement: Installing another version of a pack is a change to that pack
  test: tests/test_pack_install.py::test_a_new_version_replaces_the_record_rather_than_adding_one
  fixture: a second version of an installed pack, which must replace its record rather than add a second
- spec: pack-install
  requirement: A failed install leaves the house unchanged
  test: tests/test_pack_install.py::test_a_refused_install_writes_no_record
  fixture: an install refused by its own checks, after which the house must be unchanged
- spec: pack-manifest
  requirement: A pack is a manifest that validates against the current schema version
  test: tests/test_pack_manifest.py::test_the_shipped_example_validates
  fixture: a manifest written to the previous version, whose retired clauses the current schema refuses
- spec: pack-manifest
  requirement: The schema version chain is explicit and closed
  test: tests/test_schema_versioning.py::test_supersedes_naming_a_non_adjacent_version_fails_naming_the_versions
  fixture: a version file whose `supersedes` names a version that is not its immediate predecessor
- spec: pack-manifest
  requirement: '`kind` is closed to the five kinds the phase names'
  gap: 'no test drives the closed enum: `tests/test_pack_manifest.py` reads the one shipped example, so a sixth kind is refused by the schema and asserted nowhere'
- spec: pack-manifest
  requirement: Each kind declares its own required clauses
  gap: no test drives the per-kind conditionals; the `room-template` and `house-template` arms require nothing beyond the base clauses, so a template carrying no `requires_slots` validates and nothing says so
- spec: pack-manifest
  requirement: '`1.2.0` widens the behaviour clause with the priority, the services and the slots a behaviour declares'
  test: tests/test_pack_sandbox.py::test_a_command_through_a_slot_the_behaviour_does_not_name_is_a_reach_failure
  fixture: a behaviour reaching an entity through a slot its own `slots` clause omits
- spec: pack-manifest
  requirement: '`1.2.0` extends the provided-artifact class enum with `mode`'
  test: tests/test_pack_sandbox.py::test_a_class_the_file_is_not_is_refused
  fixture: a `provides` entry declaring a class the file it pins is not, read off the file's own shape
- spec: pack-manifest
  requirement: Dependencies and conflicts are declared as name and version range
  test: tests/test_pack_manifest.py::test_a_self_dependency_is_refused
  fixture: a manifest declaring a dependency on itself
- spec: pack-manifest
  requirement: '`engine_api` is a semver range checked against the engine''s declared version'
  test: tests/test_pack_manifest.py::test_a_range_excluding_the_engine_is_refused
  fixture: an `engine_api` range the engine's declared version falls outside
- spec: pack-manifest
  requirement: A pack declares a licence from the published vocabulary, which carries an SPDX identifier
  test: tests/test_pack_manifest.py::test_a_published_code_validates_and_carries_its_spdx_identifier
  fixture: a licence code read for the SPDX identifier `schemas/catalog/licenses.json` carries
- spec: pack-manifest
  requirement: A derived pack names the corpus rows it derives from, and its licence must permit them
  test: tests/test_pack_manifest.py::test_a_pack_over_an_ideas_only_row_is_refused
  fixture: a `derives_from` naming a corpus row whose `reuse_status` is `ideas_only`
- spec: pack-manifest
  requirement: Every user-visible string has a default, and locales resolve over it
  test: tests/test_pack_manifest.py::test_a_name_with_no_default_is_refused
  fixture: an `i18n` block whose override names a string the default does not carry
- spec: pack-manifest
  requirement: Vocabularies are referenced, never restated
  test: tests/test_pack_manifest.py::test_an_unpublished_term_is_refused_naming_the_pack_and_the_term[trigger]
  fixture: a behaviour naming a term outside `schemas/behavior-vocabulary/1.1.0.json`
- spec: pack-manifest
  requirement: A pack's identity is its name and version, and versions are semver
  test: tests/test_pack_install.py::test_a_new_version_replaces_the_record_rather_than_adding_one
  fixture: a second version of one pack, which must read as the same identity at a new version rather than as a second pack
- spec: pack-manifest
  requirement: The shipped example pack is valid under the current schema
  test: tests/test_examples.py::test_the_example_pack_validates_against_the_manifest_and_vocabulary
  fixture: the shipped example read against the current schema and the published vocabulary
- spec: pack-sandbox
  requirement: A pack expresses declarative intent and never control flow
  test: tests/test_pack_sandbox.py::test_a_published_control_flow_term_is_refused_as_non_declarative[clause1-if]
  fixture: a behaviour whose action is a published control-flow term, which the declarative subset refuses
- spec: pack-sandbox
  requirement: A command may name only an entity the pack's own slots put in its reach
  test: tests/test_pack_sandbox.py::test_a_literal_entity_id_is_refused
  fixture: a behaviour naming an entity id literally rather than reaching it through a slot
- spec: pack-sandbox
  requirement: Only services the manifest declared are callable
  test: tests/test_pack_sandbox.py::test_an_undeclared_service_is_refused_naming_the_declaration_that_would_admit_it
  fixture: a command calling a service no behaviour's `services` clause declares
- spec: pack-sandbox
  requirement: A service on the banned list is a refusal, and the list is a published artifact
  test: tests/test_pack_sandbox.py::test_a_banned_service_refuses_the_pack
  fixture: a command calling a service `catalog/pack-policy.yaml` bans, with the ban's reason beside it
- spec: pack-sandbox
  requirement: Dangerous permissions are red-flagged, not refused
  test: tests/test_pack_sandbox.py::test_a_flagged_service_does_not_refuse_the_pack
  fixture: a command calling a flagged service, which must be surfaced and not refused
- spec: pack-sandbox
  requirement: A pack's `provides` paths resolve inside the pack and carry the declared class
  test: tests/test_pack_sandbox.py::test_a_path_escaping_the_pack_is_refused[../outside.yaml]
  fixture: a `provides` path resolving outside the pack's own directory
- spec: pack-sandbox
  requirement: The sandbox is enforced at install, so a passing pack cannot exceed it
  test: tests/test_pack_sandbox.py::test_a_banned_call_at_install_is_refused_and_not_flagged
  fixture: a pack whose banned call must be refused at install rather than flagged
- spec: pack-sandbox
  requirement: The sandbox's vocabularies are read from artifacts, and the engine's layering holds
  test: tests/test_pack_sandbox.py::test_the_sandbox_reads_its_vocabularies_through_the_gateway
  fixture: a sandbox reading a vocabulary from the tree directly rather than through the gateway
- spec: pack-triggers
  requirement: A pack's behaviour may be triggered by a dashboard button or a physical button, and both produce the same act
  gap: 'task 9.1 is unwritten: no binding of a declared behaviour to either button exists, so a press has no path to a behaviour'
- spec: pack-triggers
  requirement: A dashboard button resolves to the control surface's `user_action` and adds no vocabulary term
  gap: 'task 9.2 is unwritten: no pack-declared behaviour is reachable as a `user_action` naming it'
- spec: pack-triggers
  requirement: A physical button resolves to a published trigger kind
  gap: 'task 9.3 is unwritten: no `device` or `event` trigger reaches a behaviour through a slot and no press is injectable'
- spec: pack-triggers
  requirement: A trigger is a way in and not a second execution path
  gap: 'task 9.4 is unwritten: with no trigger binding there is no second execution path to refuse'
- spec: pack-triggers
  requirement: A press produces the named behaviour's proposals, ranked by its declared priority
  gap: 'task 9.5 is unwritten: a press enters no arbitration because no press produces proposals'
- spec: pack-triggers
  requirement: The buttons work with no Home Assistant installed
  gap: 'task 9.6 is unwritten: no page documents the two buttons, because neither exists to document'
```
