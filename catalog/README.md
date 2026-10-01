# The catalog

The committed record of what the four reference repositories contain and of what
Open House takes from them. Everything here is **data**, and every data file is
validated against a schema in `schemas/catalog/`. A file that has no schema fails
the build: `catalog/` is a directory of checked files, not a directory of YAML,
and the alternative to failing -- skipping the file -- would let a new file
arrive unvalidated simply by nobody adding a schema for it.

Markdown is the one exemption. `README.md` and `overlap.md` are documents, not
data, and requiring a schema for prose would mean inventing one to satisfy the
checker.

## Validating

```
oh-catalog validate
```

or, without the console script on `PATH`:

```
python -m tools.catalog.cli validate
```

Add `--quiet` (`-q`) to print nothing on success; that is the form the
pre-commit hook uses, and a hook that prints on every passing commit is a hook
people learn to ignore. On failure the command names each file and the JSON path
within it, and exits non-zero:

```
[catalog-schema] catalog/behaviors.yaml: behaviors: 3 is not of type 'array'
```

## The file set

One schema per data file, paired by filename stem: `catalog/<name>.yaml` is
validated by `schemas/catalog/<name>.json`. The schemas carry `$id` and
`schema_version`, and `$id` is load bearing rather than decorative -- it is the
base a relative `$ref` resolves against, which is how `room_types.json` and
`slots.json` reference the runtime schemas instead of restating them.

| File | Holds | Filled by |
| --- | --- | --- |
| `licenses.yaml` | One licence record per source repo, plus the author-contact block for the two that grant nothing | task 2.1, 2.4 |
| `repos.yaml` | One identification record per source repo: author, purpose, HA style, scale, version | task 4.1 |
| `file_rules.yaml` | The **ordered** select/exclude rule list, the authored paths among it, and the altered core copies | task 3.1 |
| `rooms.yaml` | Every room across the four repos, with the repo and path it came from | task 5.1 |
| `room_types.yaml` | The room types, and the `house` scope | task 5.2 |
| `slots.yaml` | The slot vocabulary, derived from cross-repo usage rather than invented | task 5.3 |
| `behaviors.yaml` | The shipped behaviour corpus, one row per behaviour merged across repos | tasks 4.3–4.7 |
| `hardcoded_refs.yaml` | Every distinct `entity_ref` the extraction found, with its scope and naming convention | task 3.6 |
| `edge_cases.yaml` | Solved edge cases, each with the guard that solved it | task 6.1 |
| `pain_points.yaml` | Friction found in the sources, and our answer to it | task 6.2 |
| `integrations.yaml` | Every integration, add-on and bridge, with a `require`/`replace`/`avoid` disposition | task 5.4 |
| `inventory_exceptions.yaml` | Every selected file that received the residual class `other`, with a reason | task 3.4 |
| `overlap_exceptions.yaml` | The one way a single-source behaviour may be classified `generic` | task 4.7 |
| `inventory.json` | **Generated.** The deterministic file inventory over all four repos | tasks 3.3, 3.8 |
| `raw-behaviors.json` | **Generated.** The fact store: one record per selected file in every repo | tasks 3.5, 3.8 |
| `overlap.md` | Prose. Which merged rows exist, and which approach each one chose | task 4.8 |

The two `.json` files are produced by a run, committed, and never hand-edited.
`raw-behaviors.json` has a further property: its schema is **generated** from
`schemas/catalog/fact-fields.yaml`, the named allowlist, and a test asserts the
committed file equals a fresh generation. That is what makes the normaliser
structurally incapable of emitting authored text -- adding a field to the record
means editing the allowlist, which somebody reviews.

## Field contracts

Every record's field set is **closed**: each schema carries
`additionalProperties: false`, so a field that could hold an alias, a display
name, a comment or a YAML fragment cannot be added without editing the schema and
the allowlist together. Several records are empty until the task named above; the
schema is written first, so the data cannot be written into an unchecked gap.

**`licenses.yaml`** -- `repos[]`:

`repo`, `author`, `license_code`, `license_prose`, `license_file`,
`reuse_status_code`, `reuse_status_prose`, `obligations`,
`readme_licence_claim`, `licence_claim_discrepancy`, `author_contact`.

The two `reuse_status_*` fields are **derived**, not typed: a hand-written status
that contradicts the derivation table fails naming the record. `author_contact`
is `channel`, `attempted_on`, `outcome`, `reason`.

**`repos.yaml`** -- `repos[]`:

`name`, `repo_url`, `author`, `purpose`, `ha_style`, `scale`, `ha_version`,
`best_at`.

**`file_rules.yaml`** -- three top-level keys:

- `rules[]`: `order`, `pattern`, `kind`, `class`, `reason`. Applied in ascending
  `order`, first match wins -- an ordered list rather than a map, because
  precedence is the whole semantics. A selected file's class is the class of its
  deciding rule, never inferred from file content; inference would need the file,
  and the file lives only in a clone.
- `authored_paths[]`: `path`, `class`. Entries that are rules **in the same
  list**, listed again here so the requirement that at least one
  `custom_integration` and at least one `blueprint` be covered is checkable
  without re-deriving which rules those are.
- `altered_core_copies[]`: `path`, `reason`. Copies of Home Assistant's own core
  files that a repo has modified -- excluded by a named rule, and never
  attributed to the repo's author, who changed them but did not write them.

**`rooms.yaml`** -- `rooms[]`: `name`, `repo`, `path`.

**`room_types.yaml`** -- `room_types[]` validates against the runtime `room-type`
schema; `house.slots[]` names the slots that belong to the house. A room-scoped
behaviour may require only slots some room type provides, a house-scoped one only
slots named in `house.slots`.

**`slots.yaml`** -- `slots[]` validates against the runtime `slot` schema.

**`behaviors.yaml`** -- `behaviors[]`, plus a file-level `change_notice`:

`id`, `name`, `description`, `category`, `scope`, `source_repos`,
`required_slots`, `optional_slots`, `concept`, `expression`, `raw_ids`,
`license`, `reuse_status`, `obligations`, `classification`, `retention`,
`change_notice`.

A row separates what the behaviour **is** -- `concept`, always populated and
always in our own words -- from how a particular repo **spelled** it, which is
`expression` and is populated only when every source repo's code grant permits
reuse. Every row cites the raw ids it was derived from, so a row with no
provenance cannot be shipped.

**`hardcoded_refs.yaml`** -- `refs[]`:

`entity_ref`, `repo`, `scope`, `naming_convention`, `slot`, `constant`.

The only place a raw identifier of this kind is committed outside
`raw-behaviors.json`. `constant` is one of `device_id`, `device_tracker`,
`person`, `sun`, `time`. The schema enforces the half that can be enforced with
no other file present: every entry carries a non-null `slot` or a non-null
`constant`, so an entry resolving to neither fails the build. The other half —
that a named `slot` is one `catalog/slots.yaml` actually declares — needs that
file to exist and lands in task 5.3.

**`edge_cases.yaml`** -- `edge_cases[]`: `scenario`, `guard`, `repo`, `phase_1`.

**`pain_points.yaml`** -- `pain_points[]`: `repo`, `friction`, `why_hard`,
`our_answer`.

**`integrations.yaml`** -- `integrations[]`: `name`, `repos`, `disposition`,
`replacement`. A `replace` or `avoid` must name its successor; a `require` must
name none.

**`inventory_exceptions.yaml`** -- `exceptions[]`: `repo`, `path`, `reason`,
where `reason` is `unknown_artifact` or `generated_file`. Exclude rules take
precedence: a path the rule list excludes receives no class and needs no entry,
so a file cannot travel both routes.

**`overlap_exceptions.yaml`** -- `exceptions[]`: `id`, `justification`. One
repo's opinion is not corroboration, so a row with a single source defaults only
when somebody has written down why -- and the justification is reproduced in
`overlap.md` rather than left here to be found.

**`inventory.json`** (generated) -- `repos[]` and `totals`:

`repo` is `repo`, `counts`, `files`, `by_class`, `by_rule`, `by_parse`;
`counts` is `tracked`, `selected`, `excluded`; each entry of `files` is `path`,
`selected`, `class`, `parse`, `rule`, `error`.

**`raw-behaviors.json`** (generated) -- `records[]` and a `change_notice`:

`id`, `path`, `class`, `triggers`, `conditions`, `actions`, `slots`,
`entity_refs`, `unclaimed`.

Each record's key set equals the fact allowlist **exactly**, and the schema is
generated from it. `path` is admitted by membership in the committed
selected-path set rather than by a pattern, because a pattern tight enough to
exclude authored text would also reject real tracked paths -- one repo's
filenames are German, and real paths carry spaces and non-ASCII characters.

## Which checks are local-only

Everything in `oh-catalog validate` is a pure function of this repository, and
CI runs it. Two checks are deliberately **not** included, because they read the
four reference clones, which are `gitignore`d and absent in CI -- and two of
which grant no licence to redistribute. They run locally and their **outputs**
are committed:

- the `git ls-files` closure check over each clone, and
- the prose gate comparing a shipped string against the verbatim store.

So a green `oh-catalog validate` in CI means every invariant that can be checked
without the clones holds, and no more than that.

## Per-repo counts

Recorded by task 3.8, when there is an extraction to count: for each repo, how
many tracked files were **selected**, how many **excluded**, the per-deciding-rule
breakdown, and how many selected files failed to **parse**. Empty until then.
