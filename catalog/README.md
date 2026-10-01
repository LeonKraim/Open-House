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
| `golden_<repo>.yaml` | Four files. Hand-picked paths per repo, each with the class it is expected to receive -- the only check that fails when the rule list starts excluding too much | task 3.2 |
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

The three derived fields are **derived**, not typed. `reuse_status_code` comes
from `license_code` and `reuse_status_prose` from `license_prose`; `obligations`
comes from `license_code`, since an obligation attaches to the code grant rather
than to the documentation. The check recomputes all three and fails any that
disagrees, naming the record, and rejects an obligation outside the four the spec
names. `author_contact` is `channel`, `attempted_on`, `outcome`, `reason`.

`repo` is the repository's owner handle, lowercased -- `ccostan`, `renemarc`,
`fwartner`, `johnkoht`. It is the token `docs/reference/<repo>.md` is named for
and the name `repos.yaml` carries; the full URL lives there, in `repo_url`.

The same check also reads `behaviors.yaml`, because a row's `license`,
`reuse_status` and `obligations` are derived from the code licences of the repos
it cites. Its findings carry the `row-license` category rather than `licenses`,
since the file to open is `behaviors.yaml`. The file is empty until section 4,
and the rule is silent on an empty one rather than reporting every absence.

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

**`golden_<repo>.yaml`** -- `repo`, `entries[]`:

`path`, `class`. One file per source repo, named for it: `golden_ccostan.yaml`
and so on, with `repo` repeating the name so the two can be checked against each
other. An entry is a path **with the class it is expected to receive**, which is
what makes it a check rather than a line of a list -- a path alone says only that
something was selected.

These are the one place a path is written down twice, once by `file_rules.yaml`
and once by hand, and therefore the only check that fails in the *excluding*
direction. An exclude rule broad enough to swallow a directory takes a pinned
path with it and the check names the path and the deciding rule; without them the
corpus would simply be smaller, and nothing defined as "whatever the rules
select" can be noticed to be missing from itself. They do **not** guard the other
direction: a select rule broad enough to pull new files in changes no pinned
path, and is caught by the diff in `inventory.json` and the counts below.

Across the four, every class in the closed enum except `other` must be pinned,
so a class cannot drop out of the corpus entirely without something saying so.
`other` is the residual -- what a file falls into when no class fits -- and
pinning it would commit the corpus to keeping a file whose only justification is
that we could not classify it.

The entries are a sample and not the corpus: a file listing every selected path
would be a copy of `inventory.json`, which is the file nobody reads. They are
chosen so that a rule change broad enough to matter takes one of them with it.

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
`selected`, `class`, `parse`, `rule`, `error`; and each entry of `by_rule` is a
rule's `order`, as a string, mapped to that rule's `decided`, `selected` and
`excluded`.

`by_rule` counts **every** file its rule decided and not only the excluded ones,
because a map restricted to exclusions would leave every select rule out, and a
rule missing from the map reads exactly like a rule that decided nothing. That
makes `decided` the count that closes over `git ls-files`. `excluded` rides
beside it rather than being left to be reconstructed, since the closure clause
names the per-deciding-rule excluded count. All three are checked against the
file list individually: `decided = selected + excluded` is true of an entry
whatever the entry says, so comparing the three to each other would pass a map
whose split had been guessed.

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

- the `git ls-files` closure check over each clone, which also confirms that
  every `authored_paths` pattern matches a tracked file somewhere -- the rule
  list cannot decide that, since a glob conforms to its declared class whether
  or not anything matches it -- and
- the prose gate comparing a shipped string against the verbatim store.

So a green `oh-catalog validate` in CI means every invariant that can be checked
without the clones holds, and no more than that.

## Per-repo counts

Recorded by task 3.8, from the committed `catalog/inventory.json`. The
per-rule column lists each deciding rule as `order (decided/selected/excluded)`,
which is the breakdown with the rules that decided nothing left out rather than
listed as zeroes -- a rule absent from `by_rule` is a rule that decided no file,
and the two are the same observation.

| Repo | Tracked | Selected | Excluded | Unparsed | Per deciding rule |
| --- | --- | --- | --- | --- | --- |
| `ccostan` | 709 | 218 | 491 | 0 | 5 (26/0/26), 6 (1/0/1), 10 (4/4/0), 11 (2/2/0), 31 (1/0/1), 32 (376/0/376), 33 (1/0/1), 39 (1/0/1), 40 (2/0/2), 41 (13/0/13), 42 (1/0/1), 43 (13/0/13), 45 (2/0/2), 48 (19/0/19), 49 (6/0/6), 52 (2/0/2), 53 (24/0/24), 55 (3/0/3), 80 (5/5/0), 83 (54/54/0), 100 (42/42/0), 101 (16/16/0), 102 (2/2/0), 103 (1/1/0), 106 (78/78/0), 107 (1/1/0), 109 (1/1/0), 111 (6/6/0), 112 (5/5/0), 113 (1/1/0) |
| `renemarc` | 472 | 144 | 328 | 0 | 5 (30/0/30), 6 (1/0/1), 20 (3/0/3), 21 (3/0/3), 22 (3/0/3), 30 (114/0/114), 32 (124/0/124), 33 (2/0/2), 34 (31/0/31), 43 (3/0/3), 45 (1/0/1), 48 (5/0/5), 49 (5/0/5), 56 (1/0/1), 57 (1/0/1), 58 (1/0/1), 80 (6/6/0), 100 (79/79/0), 101 (8/8/0), 105 (1/1/0), 107 (1/1/0), 111 (24/24/0), 112 (25/25/0) |
| `fwartner` | 3964 | 129 | 3835 | 0 | 5 (12/0/12), 12 (3/3/0), 13 (2/2/0), 31 (61/0/61), 32 (3689/0/3689), 33 (9/0/9), 35 (35/0/35), 36 (1/0/1), 41 (10/0/10), 43 (1/0/1), 44 (1/0/1), 45 (1/0/1), 46 (1/0/1), 48 (5/0/5), 49 (6/0/6), 50 (2/0/2), 57 (1/0/1), 80 (7/7/0), 81 (21/21/0), 82 (3/3/0), 103 (1/1/0), 104 (1/1/0), 105 (1/1/0), 109 (25/25/0), 110 (65/65/0) |
| `johnkoht` | 3217 | 1300 | 1917 | 0 | 5 (96/0/96), 6 (12/0/12), 31 (8/0/8), 32 (666/0/666), 33 (6/0/6), 35 (21/0/21), 36 (1/0/1), 37 (6/0/6), 38 (1/0/1), 39 (19/0/19), 41 (1048/0/1048), 42 (6/0/6), 45 (1/0/1), 48 (7/0/7), 49 (3/0/3), 50 (1/0/1), 52 (13/0/13), 54 (1/0/1), 56 (1/0/1), 80 (2/2/0), 83 (870/870/0), 100 (85/85/0), 101 (9/9/0), 102 (2/2/0), 103 (1/1/0), 104 (1/1/0), 105 (2/2/0), 106 (267/267/0), 107 (1/1/0), 108 (1/1/0), 111 (3/3/0), 112 (52/52/0), 113 (4/4/0) |
| **total** | **8362** | **1791** | **6571** | 0 | |

No selected file in any of the four repositories failed to parse, which is why
the unparsed column is zero throughout: the rule list selects the YAML shapes it
can read, and a file it selects but cannot parse is counted and recorded with its
error rather than dropped (task 3.7).
