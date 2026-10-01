# Tasks

Two things about the order are not the obvious ones, and both are load-bearing.
The runtime schemas land before the catalog data and the catalog schemas,
because every catalog data file validates against a schema and the catalog
schemas must reference the runtime `slot` and `room-type` shapes rather than
restate them. And the clone-reading steps run locally with their outputs
committed, because CI must not depend on four third-party repositories that are
`gitignore`d and two of which grant no licence to redistribute.

## 1. Version control, tooling, runtime schemas and module skeletons

- [ ] 1.0 Initialise the project repository: `git init`, commit the existing
  `openspec/`, `spec.txt`, `tools/` and `docker/` state, and confirm
  `ressources/` is ignored so the four reference clones are not tracked. This
  comes first because the schema-immutability check reads git history and cannot
  be evaluated without it. Verify `git ls-files` lists no path under
  `ressources/` and that the repo has at least one commit.
- [ ] 1.1 Create the root `pyproject.toml` with a `tools/catalog/` Python 3.12+
  package, ruff + pyright config, and a console script `oh-catalog` whose entry
  point runs the validator. Use `tools/__init__.py` so the package is
  unambiguous against the `catalog/` data directory, and never invoke it as
  `python -m catalog`. Verify `oh-catalog --help` runs and `ruff check` and
  `pyright` pass.
- [ ] 1.2 Create the module skeletons. `engine/`, `ha_adapter/`, `sim/` and
  `custom_components/` are Python packages with `py.typed`; `panel/` and
  `packs/official/` are created with a marker file and are not Python packages.
  Verify each Python package imports on its own and the layout check passes
  with `panel/` and `packs/official/` checked for existence only.
- [ ] 1.3 Implement the layout and engine-purity checks — no `homeassistant`
  import anywhere under `engine/` including guarded and `TYPE_CHECKING`
  imports; no third-party import outside the declared dependency list. Verify
  with a failing fixture per invariant — an `engine/` file importing
  `homeassistant` inside a `try`, and one importing an undeclared package —
  each asserting the check names the offending file. The registry-boundary
  check belongs to 1.4, not here; an earlier draft implemented it in both.
- [ ] 1.4 Implement the registry-boundary check — pattern-based over committed
  **artifact** files, not Markdown, so the requirement text that names the three
  keys does not trip it — and record the decision to defer creating the separate
  `registry/` repository to Phase 7, since Phase 0 cannot create a repository
  outside this project root, noting it in
  `docs/reference/phase-0-verification.md`. Verify the check passes on the
  committed tree including `openspec/`; fails on a fixture carrying
  `registry_url`, `registry_index`, `registry_key`, and on one matching
  `*.registry.json`; and passes over `.pre-commit-config.yaml` and
  `package.json`. Verify `spec.txt`'s layout clause is ticked off with this
  deferral stated.
- [ ] 1.5 Author the eight runtime schemas as **version files** under
  `schemas/<concept>/1.0.0.json` — `room-type`, `slot`, `house`,
  `pack-manifest`, `behavior-vocabulary`, `mode`, `profile`, `export-document` —
  each with `$id`, `title`, `schema_version` and `supersedes: null`. The
  vocabulary is created here with the terms known at this point and gains its
  derived terms later as `1.1.0.json` declaring `supersedes: 1.0.0`; it is the
  one runtime schema expected to have two versions in Phase 0. Verify with a
  test asserting all eight concepts have exactly one current version with their
  metadata; a test that two versions neither of which the other supersedes fails
  naming both; and a test that `supersedes` naming a non-adjacent version, or
  two versions superseding the same one, fails naming the versions involved.
- [ ] 1.6 Create `catalog/` data stubs (`licenses.yaml`, `repos.yaml`,
  `file_rules.yaml`, `rooms.yaml`, `room_types.yaml`, `behaviors.yaml`,
  `slots.yaml`, `hardcoded_refs.yaml`, `edge_cases.yaml`, `pain_points.yaml`,
  `integrations.yaml`, `inventory_exceptions.yaml`, `overlap_exceptions.yaml`)
  and `schemas/catalog/` with one schema per data
  file — including the JSON artifacts `inventory.json` and `raw-behaviors.json`
  produced in §3 — each carrying `$id` and `schema_version`, referencing the
  runtime schemas rather than restating them, and **exempt from immutability**
  so fields the extraction teaches us can be added as §2–§7 proceed. The
  `raw-behaviors.json` schema also covers the unclaimed register, which lives as
  an `unclaimed` field on each raw record rather than in a file of its own.
  Also author `schemas/catalog/fact-fields.yaml`, the named allowlist the
  `raw-behaviors.json` schema is **generated from**, declaring each field's
  permitted type — an enum, an integer, a boolean, a list of those, or a
  **named shape** — and `schemas/catalog/fact-shapes.yaml`, the closed set of
  shapes those references resolve to: `entity_ref`, `dotted_id`, `slug` and
  `selected_path`. No field carries a pattern of its own, and no shape admits
  arbitrary printable text. Verify every stub passes
  `oh-catalog validate` on the empty state, that a data file with no schema
  fails naming the file, that a catalog schema redefining the slot shape fails
  the conformance check, and that editing a catalog schema in place passes the
  immutability check. The three allowlist checks belong to 3.5, where the record
  shape is built.
- [ ] 1.7 Write `catalog/README.md` documenting the file set, the field
  contracts, the exact validation command, and which checks are local-only
  because they read the clones. Verify the documented command runs as written
  and exits 0 on the committed catalog.
- [ ] 1.8 Create `.pre-commit-config.yaml` running ruff, pyright and
  `oh-catalog validate`, and install the hooks. Placed after 1.6 so the catalog
  it validates exists. Verify `pre-commit run --all-files` exits 0 on the
  committed tree and fails on a deliberately unformatted file.

## 2. Licence settlement (gates everything downstream)

- [ ] 2.1 Populate `catalog/licenses.yaml` for all four repos: CCOSTAN
  `mit` code; renemarc `apache_2_0` code with `cc_by_nc_sa` prose; fwartner
  `no_licence` with the README-claim discrepancy recorded; johnkoht
  `no_licence`. Do not type `reuse_status_code` or `reuse_status_prose` — they
  are derived. Verify `oh-catalog validate` passes and each record names a
  licence file path or explicitly `null`.
- [ ] 2.2 Implement the derivation table and the published order
  (`public_domain < mit < apache_2_0 < cc_by_nc_sa < no_licence`), applied
  separately to code and prose. Verify with a table-driven test per row, a test
  that every value in the order has a derivation row, and a test that a
  hand-written status contradicting the table fails naming the record.
- [ ] 2.3 Write `docs/reference/<repo>.md` licence records stating author,
  licence, what may be reused and what may not, with the vendored-third-party
  carve-out. Verify a test asserts exactly one record per repo and that each
  names the author and licence path.
- [ ] 2.4 Add the `author_contact` block for the two unlicensed repos with
  `outcome: not_attempted`, `attempted_on: null` and a reason, and write
  `docs/reference/author-contact.md` listing the exact ask for each. Verify
  `oh-catalog validate` fails when an unlicensed repo has no `author_contact`
  block, and fails when `outcome: not_attempted` carries a date. Do not send any
  contact without the user's authorisation.
- [ ] 2.5 Confirm from the derived statuses which repos may donate expression —
  CCOSTAN and renemarc — and record it in `docs/reference/phase-0-
  verification.md`. Verify the count matches the derived `reuse_status_code`
  values and that the proposal and design agree with it.
- [ ] 2.6 Implement the row-level derivation on behaviour rows: `license` is the
  most restrictive source **code licence** under the published order (a licence
  value, not a status); `reuse_status` is the status the table gives that
  licence; `obligations` is the **union** of the source code licences'
  obligations. Verify with tests that a `mit`/`apache_2_0` merge yields
  `license: apache_2_0`, `reuse_status: reusable`, and both `attribution` and
  `state_changes`; and that a hand-written row omitting a source's obligation
  fails naming the row.

## 3. File rules and deterministic inventory (stage A)

- [ ] 3.1 Write `catalog/file_rules.yaml` as a single ordered list — every rule
  carrying a unique integer `order`, a pattern, a select-or-exclude kind, a
  `reason`, and **the artifact class it confers** (required on a select rule,
  `null` on an exclude rule). A selected file's class is the class of its
  deciding rule, never inferred from file content, because inference would need
  the file and the file lives only in a clone. `authored_paths` entries are
  rules **in this list**, carrying a low `order` and the artifact class they must
  receive, covering at least one `custom_integration` (CCOSTAN's
  `config/custom_components/tesla_charge_guard` and `alexa_camera_compat`,
  evidenced by their `codeowners`) and at least one `blueprint` (fwartner's
  `blueprints/automation/fwartner/` and `custom_configs/blueprints/`), plus a
  named exclude rule for the altered core copies that are neither the author's
  original work nor third-party vendored. Modelling the override as a rule is
  what gives it a deciding rule to record and count. Verify with tests that a
  file matching no rule fails naming the file; two rules sharing an `order` fail
  naming both; a select rule with no `class`, or an exclude rule whose `class` is
  not `null`, fails naming the rule and its `order`; a path matching two select
  rules with different classes receives the lower-`order` rule's class and
  records that rule; an overlapping case (a vendored `.js` matched by a
  vendored-tree rule, a `custom_components/**` rule and a `**/*.js` rule) is
  decided by the lower `order` without failing; `authored_paths` lacking a
  `custom_integration` or a `blueprint` entry fails naming the missing class; an
  `authored_paths` entry which is not tracked fails; an entry classified as
  anything but its declared class fails; and the per-deciding-rule counts sum to
  the excluded count with no rule unseen.
- [ ] 3.2 Add a golden file per repo whose entries are paths **each carrying the
  class it is expected to receive**. This is hand-written data, so it lands
  before the code that checks it. Verify structurally that every class in the
  closed enum other than `other` is pinned by at least one repo, failing and
  naming the unpinned class otherwise.
- [ ] 3.3 Implement the walker over `git ls-files`, partitioning each repo into
  selected and excluded by first-match rule order, recording the deciding rule
  per file, taking each selected file's class **from its deciding rule**, plus a
  separate parse outcome, and emitting `catalog/inventory.json` with per-class,
  per-deciding-rule and parse-outcome counts. Verify with a test that selected +
  excluded equals the repo's `git ls-files` count. The golden-path and
  class-pinning checks need nothing from the clones — they are pure functions of
  `file_rules.yaml` and the golden files — and are therefore not verified here;
  they belong to 7.7, on the CI side of the boundary.
- [ ] 3.4 Require a justification entry in `catalog/inventory_exceptions.yaml`
  for every `other` classification, with a reason from the closed set
  (`unknown_artifact`, `generated_file`). Verify with a fixture landing in `other`
  and not listed, one with an invented reason, and one excluded by a rule that
  is also listed in `inventory_exceptions.yaml` — each asserting the failure or
  the precedence names the file.
- [ ] 3.5 Implement the normaliser emitting **two stores**: the committed
  `catalog/raw-behaviors.json`, which carries facts for every selected file in
  every repo — path, id, class, trigger/condition/action vocabulary terms, slot
  bindings, `unclaimed`, and `domain.object_id` references — built so that each
  record's key set equals the **fact allowlist exactly** and the record's schema
  under `schemas/catalog/` carries `additionalProperties: false`, making the
  normaliser structurally incapable of emitting authored text; and the
  gitignored `.local/raw-verbatim.json` outside `catalog/`, carrying the same
  records unfiltered including the withheld text. Classify every referenced
  `domain.identifier` as `entity_ref` or `service_call` by structural position,
  **including references embedded in Jinja templates** — `{{ states('light.kitchen') }}`
  carries an entity reference and these repos use templates more than any other
  style. Verify with fixtures per style asserting identical normalised output; a
  test that `service: light.turn_on` yields a `service_call` and enters no
  entity identifier set; a test that a template reference yields an `entity_ref`
  and a templated service call does not; a test that a record built for a
  non-granting source has a key set equal to the allowlist and that a record
  carrying any extra key fails validation naming the key; a test that adding a
  property to the raw-record schema without an allowlist entry fails naming the
  property; a test that an allowlist entry missing from the regenerated schema
  fails in the other direction, and that the committed schema equals a fresh
  generation from the allowlist; a test that an unconstrained string field is
  rejected from the allowlist and from the schema, with an `entity_ref`
  shape-named field **passing**; a test that a field carrying its own pattern
  rather than naming a committed shape fails, and one that a shape admitting
  arbitrary printable text — `^.*$` — fails naming the field and the pattern;
  and a test that a `path` fact passes because its value is an element of the
  committed selected-path set while a path-shaped string that is not a selected
  path fails; and a test that `.local/` is gitignored and holds no tracked file.
- [ ] 3.6 Emit `catalog/hardcoded_refs.yaml` from the same pass: every distinct
  `entity_ref` with source repo, `domain.object_id`, inferred scope and naming
  convention label — facts only, for all four repos. Verify with a test that no
  `service_call` appears in the file, that every entry carries a scope and a
  naming-convention label, and that no entry carries an alias, display name,
  comment or YAML fragment from its source. The assertion that each entry
  resolves to a `slot` or a justified `constant` belongs to 5.3, where
  `slots.yaml` exists. This is the second committed facts store, so route its
  entry schema through `fact-fields.yaml` and `fact-shapes.yaml` exactly as
  3.5 does for `raw-behaviors.json` — the same guarantee, since a later round
  could otherwise add an expression-bearing field to its entry schema the same
  way. Its entry field set is closed in the requirement; make the schema say so.
- [ ] 3.7 Record unparsed files with their error while keeping their class.
  Verify with intentionally malformed YAML that the file keeps its class,
  appears as `unparsed`, still counts in the class total, and the run exits 0.
- [ ] 3.8 Run the sweep over all four repos and commit `inventory.json`,
  `raw-behaviors.json` and `hardcoded_refs.yaml` — and **not** `.local/`, which
  is added to `.gitignore` in this task. Verify by re-running and asserting
  byte-identical output for **all four** extraction outputs by name —
  `inventory.json`, `raw-behaviors.json`, `hardcoded_refs.yaml` and
  `.local/raw-verbatim.json`, the last being the one the prose gate reads and
  therefore the one whose determinism matters most; by asserting `git ls-files`
  tracks no path under
  `.local/` while the directory exists on disk after a run, so `git add -f`
  cannot slip one through; by asserting `oh-catalog validate` exits 0 with
  `.local/raw-verbatim.json` present, since the store sits outside `catalog/`
  and no catalog schema is expected for it; and by recording each repo's
  selected, excluded, per-rule and parse-failure counts in `catalog/README.md`.

## 4. Repo identification and curated corpus (stage B)

- [ ] 4.1 Populate `catalog/repos.yaml` with name, repo_url, author, purpose,
  ha_style, scale, ha_version and best_at for all four repos, `ha_style` as a
  non-empty de-duplicated list from the closed set (renemarc is split-include,
  custom-integration and appdaemon). Verify with tests asserting one record per
  repo, all fields non-empty, every `ha_style` value inside the closed set with
  no duplicates, and `ha_version` matching each repo's `.HA_VERSION` (CCOSTAN's
  is at `config/.HA_VERSION`).
- [ ] 4.2 Build the role lexicon mapping `(domain, name pattern, room context)`
  to slot candidates, seeded from naming conventions in at least three of the
  four repos. Verify with unit tests per role and a test asserting every
  lexicon entry cites its source repo.
- [ ] 4.3 Extract behaviour rows into `catalog/behaviors.yaml` for lighting,
  presence/occupancy, modes and climate, each with `concept` in our own words
  and `raw_ids` citing stage A. Verify a test fails on a dangling `raw_id`.
- [ ] 4.4 Extract rows for security/safety, media, laundry, cleaning,
  notifications and system categories, same rules. Verify every raw id in
  `raw-behaviors.json` is claimed by exactly one shipped row or listed
  `unclaimed` with a reason from the closed set, and that the unclaimed count is
  reported.
- [ ] 4.5 Set `description`, `scope`, `classification`, `retention` and
  `expression` on every row, leaving `expression` empty wherever any source's
  code status is not `reusable` and `retention: audit` only on `discard` rows.
  Verify with tests that no `description` is empty, every `scope` is `room` or
  `house`, every `classification` is from the closed set, and no row with a
  non-granting source has a populated `expression`.
- [ ] 4.6 Implement the provenance-resolved identifier gate: for each row
  citing a repo with `reuse_status_code: ideas_only`, reject any free-text field
  the field carries — `name`, `concept`, `description`, `change_notice`, or a
  slot example it supplies — containing an `entity_ref` extracted from a raw
  record that row cites. The list is illustrative and the check SHALL iterate the
  row's schema's string fields rather than a hand-kept list. Verify with six
  tests — a non-granting identifier in `concept` fails naming it; one in `name`
  fails; one in `change_notice` fails; a granting-only row using an identifier
  that also exists in an uncited non-granting repo passes; a row whose
  `expression` mentions `light.turn_on` passes; and an example from a
  non-granting repo is checked against its own `raw_ids`.
- [ ] 4.7 Classify every row `generic | module_candidate | discard` with
  `discard` rows retained `retention: audit`. Verify with tests that no
  `discard` row appears in the shipped default set, and that no single-source
  row is `generic` unless listed in `overlap_exceptions.yaml`.
- [ ] 4.8 Produce `catalog/overlap.md` ordered descending by source-repo count
  then ascending by permissiveness, recording for each merged row the chosen and
  rejected approaches. Verify tests that every multi-source row appears, each
  entry names ≥2 source repos, and the entries are in the specified order — a
  fixture out of order fails.

## 5. Slots, rooms, room types and dependencies

- [ ] 5.1 Populate `catalog/rooms.yaml` with every room across the four repos,
  mapping each to its source repo and path. Verify with tests asserting each
  repo contributes at least one room and each room names its source path.
- [ ] 5.2 Define room types plus the `house` scope in
  `catalog/room_types.yaml`, each room type naming the source rooms that
  produced it and carrying `default: true | false`, validating against the
  current `room-type` schema. Verify with a test asserting every room type names
  its source rooms, that a `default: true` room type tracing to one repo fails
  naming the type and the repo, that a single-repo type marked `default: false`
  passes, and that the `house` scope exists.
- [ ] 5.3 Populate `catalog/slots.yaml` validating against the current `slot`
  schema, from the role lexicon and observed usage, with a controlled vocabulary
  of slot names, accepting domains, and examples each naming source repo,
  `reuse_status_code` and either the `raw_ids` it was drawn from or, for an
  artifact that yields no raw records, a `description` in our own words. Verify
  with tests that every slot has ≥1 example; every example carries `raw_ids`, or
  carries none together with a `description` and a granting source repo; every
  multi-source slot has examples from ≥2 repos; every slot name is in the
  controlled vocabulary; and — the assertion 3.6 defers here — every
  `hardcoded_refs` entry resolves to a slot or a justified constant from the
  closed set (`device_id`, `device_tracker`, `person`, `sun`, `time`), failing
  otherwise naming the reference and its source repo.
- [ ] 5.4 Populate `catalog/integrations.yaml` with every integration, add-on
  and bridge plus a `require | replace | avoid` disposition. Verify with tests
  that every integration used by ≥3 repos is `require` and every `replace` or
  `avoid` names its replacement.

## 6. Edge cases, pain points, scope checks and attribution

- [ ] 6.1 Capture solved edge cases from all four repos as entries in
  `catalog/edge_cases.yaml` with the guard used, the source repo, and a Phase 1
  placeholder. Verify with tests that each entry names a source repo and a
  guard, and that each has a Phase 1 placeholder.
- [ ] 6.2 Capture pain points in `catalog/pain_points.yaml` with repo,
  friction, why_hard and a non-empty `our_answer` naming a downstream phase.
  Verify with tests that every entry has a non-empty `our_answer` naming a
  phase, and that each of the four repos contributes at least one entry.
- [ ] 6.3 Implement the scope-aware slot-availability check: `scope: room` rows
  against room types, `scope: house` rows against the house scope. Verify with
  tests that an unavailable room-scoped slot fails, and that a house-scoped away
  behaviour validates.
- [ ] 6.4 Generate `docs/attribution.md` from `catalog/licenses.yaml` and add
  the drift check. Verify with tests that regeneration is byte-identical and
  that every incorporated repo appears with author, licence and obligations.
- [ ] 6.5 Implement the `state_changes` check at both units: every row derived
  from a `state_changes` source carries a `change_notice` naming what was
  changed and by whom — Open House, in our own words — and every file
  containing such a row carries a file-level notice. Add both to
  `catalog/behaviors.yaml` and any other adapted artifact. Verify with tests
  that a row without a `change_notice` fails naming the row, that a file with
  such a row and no file-level notice fails naming the file, and that the
  committed files pass.
- [ ] 6.6 Implement the prose gate: no shipped artifact, `docs/` included, may
  contain prose quoted from a repo with `reuse_status_prose: ideas_only`.
  Local-only, since it reads the gitignored `.local/raw-verbatim.json` rather
  than a clone. Verify with a fixture quoting a passage
  from renemarc into a document, asserting the failure names the artifact and
  the repo, and a fixture restating the same idea in our own words, asserting it
  passes.

## 7. Vocabulary version, immutability, examples and CI gate (0b)

- [ ] 7.1 Publish `schemas/behavior-vocabulary/1.1.0.json` carrying the trigger,
  condition and action terms the extraction derived and declaring
  `supersedes: 1.0.0`, **writing nothing into `1.0.0.json`**. Verify with a test
  that the new version is current, the old one is present and byte-identical to
  its state after 1.5, and the successor names it; and a test that a pack using
  an out-of-vocabulary action fails naming the pack and the term.
- [ ] 7.2 Implement the schema immutability check over the eight runtime
  schemas: a version file's content must equal its content at the commit that
  first introduced that path, read from git history, and `schemas/catalog/` is
  out of scope. Verify with a test that edits `schemas/<concept>/<version>.json`
  in place and asserts the failure names the file; a test that publishing a
  successor **by writing a pointer into the retired version** fails naming the
  edited file, since backwards succession is what makes publication possible
  without an edit; a test that **deleting** a version named in a present
  version's `supersedes` fails naming the missing path, since absence is a
  change and a content comparison has nothing to compare against; a test that
  editing a catalog schema in place passes; and a test that the committed tree,
  after 7.1, is green.
- [ ] 7.3 Hand-write `packs/official/example-house.yaml` exercising ≥2 room
  types with one optional slot bound and one left unbound, and add
  `packs/official/HANDWRITTEN`. Verify it validates against the current house,
  room-type and slot schemas, and a test fails when the file is absent from
  `HANDWRITTEN`.
- [ ] 7.4 Hand-write `packs/official/example-pack.yaml` declaring kind, required
  and optional slots, and behaviours in the frozen vocabulary, listed in
  `HANDWRITTEN`. Verify it validates against the current manifest and vocabulary
  schemas, and a test fails when a behaviour term outside the vocabulary is
  used.
- [ ] 7.5 Hand-write `packs/official/example-export.yaml` validating against the
  current `export-document` schema, with bindings keyed by registry id plus
  `entity_id`. Verify a test fails on a binding carrying only `entity_id`. The
  round-trip property belongs to Phase 3; record that deferral in
  `docs/reference/phase-0-verification.md`.
- [ ] 7.6 Implement the conformance check that no file outside a runtime version
  directory `schemas/<concept>/` declares its own room-type, slot or house
  shape. `schemas/catalog/` is a **catalog** data directory and is in scope, not
  exempt: it is the one place a concept shape could be restated, which is why
  the check exists. Verify with a failing fixture in a pack and one in
  `schemas/catalog/`, and a passing fixture that is a runtime version file.
- [ ] 7.7 Implement the two completeness checks as pure functions of committed
  artifacts, so they run in CI rather than only on a machine that has the
  clones: the golden-path check applies the committed rule list to each committed
  golden path and fails if it is not selected, or is selected as a class other
  than its entry declares; and the class-pinning check fails if any class other
  than `other` is pinned by no repo, or if every path pinning a class has been
  excluded. Both read the class from each path's deciding rule, which is why
  3.1 requires every select rule to carry one. Verify with a fixture exclude rule
  that swallows a golden path (failure names the path and its deciding rule), a
  golden entry whose declared class is wrong (failure names both classes), and a
  class removed from every golden file's pinning (failure names the class).
  Verify the CI claim directly: run the **CI check suite** — every check 7.8
  wires, excluding the two named local ones — in a checkout with `ressources/`
  and `.local/` both absent, and assert it passes, so the "pure function" claim
  is tested rather than asserted.
- [ ] 7.8 Wire CI: `oh-catalog validate`, `pre-commit run --all-files`, the
  invariant checks, the schema validation of the examples, the attribution
  drift check, the 4.6 identifier gate (it reads the committed
  `raw-behaviors.json`, so it belongs here and not with the local pair), and the
  7.7 completeness checks — every one of which reads only this repository. Add a
  guard that no job reads from `ressources/` or `.local/`. Verify CI
  passes on the committed tree, fails when a schema, the example house, the
  example pack or the example export is removed, fails when a golden path is
  excluded, and fails when a job is made to read the clones.

## 8. Acceptance

- [ ] 8.1 Build the requirement→check mapping by parsing `### Requirement:` from
  the four delta spec files rather than hard-coding a count, mapping each to at
  least one **named test** and a **violating fixture**, and writing it to
  `docs/reference/phase-0-verification.md`. The verifying script SHALL, for every
  requirement, resolve the named test in the collected suite, run it against its
  violating fixture, and fail if the test is missing **or passes** — presence in
  a hand-written file is not evidence that a check exists, and this gate is the
  one that has to catch an unenforced clause. Verify by running the script and
  by deliberately deleting one mapped test, which SHALL fail the script.
- [ ] 8.2 Confirm the `spec.txt` Phase 0 exit criterion: all four repos
  classified, the slot vocabulary derived from real usage across them, and the
  hand-written example house and pack validating against the schemas. Verify by
  running the full suite plus `openspec validate phase-0-foundations --strict`
  and reporting each of the three clauses with its evidence.
