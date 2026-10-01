# Phase 0 verification

This file records the decisions Phase 0 had to make that the change package
delegates rather than settles, and the evidence for each. It is written as the
phase proceeds, not at the end, because two of the entries below are about
things the phase chose **not** to build, and a deferral recorded after the fact
is a deferral nobody could have disagreed with.

The change package is `openspec/changes/phase-0-foundations/`. Where an entry
says "task N", it means the numbered task in that change's `tasks.md`.

## `spec.txt` clause status

`spec.txt` is the input specification, not a checklist, so "ticked off" is
recorded here as a clause-to-evidence mapping rather than as edits to a file
that is an input.

| `spec.txt` clause (Phase 0b) | Status | Evidence |
| --- | --- | --- |
| Freeze versioned schemas for room types and slots, pack manifests, the trigger/condition/action vocabulary, modes, profiles, and the export document | Done | the eight version files under `schemas/<concept>/1.0.0.json`, enforced by the `schema-versioning` and `schema-immutability` checks |
| Repo layout: `engine/`, `ha_adapter/`, `custom_components/`, `panel/`, `packs/official/`, `sim/` | Done | `layout` check, task 1.2 |
| Repo layout: "and a separate `registry/` repo" | **Deferred to Phase 7** | see below |
| CI, linting, typing and pre-commit set up | Done | `pyproject.toml`, `.pre-commit-config.yaml`, tasks 1.1 and 1.8 |
| Exit: every piece of all four repos is classified | see task 4.x | |
| Exit: the slot vocabulary comes from real usage across them | see task 5.x | |
| Exit: a hand-written example house and pack validate against the schemas | see task 7.x | |

## The `registry/` repository is deferred to Phase 7

Task 1.4's decision, recorded here because the phase cannot do otherwise.

`spec.txt` asks the layout to include "a separate `registry/` repo". Phase 0
cannot create it: a separate repository is outside this project root, and
creating one is a publishing act, not a build step. There is also nothing to put
in it — the registry's contents are pointer files (repo, commit SHA, SHA-256,
tier) and a generated `index.json`, and none of those can exist before there is
a pack to point at.

So Phase 0 builds the half it can: the invariant that *this* repository contains
no registry pointer, enforced now so that pointer files cannot accrete here in
the meantime under the assumption that they belong here. Task 1.4's check is
scoped exactly that way, and the requirement in `architecture-invariants/spec.md`
says as much. The other half — the separate repository — is Phase 7's, and
`spec.txt`'s own Phase 7 clause names it.

## The registry scan reads structured files, enumerated from git

Two scope decisions in `tools/catalog/invariants.py::check_registry_boundary`
that are narrower and wider than the requirement's literal words, recorded so
that a reader can disagree with them deliberately.

**Wider: the file list comes from git, not from a walk of the tree.** The
requirement says "committed artifact files". A walk of the working tree is not
that: it reads a virtualenv, a built panel and a container's generated config as
though they were the project, which is slow, machine-specific, and enforces
something the requirement does not say. `tools/catalog/vcs.py` enumerates
`git ls-files --cached --others --exclude-standard` — every committed file, plus
every untracked file that is not ignored. The untracked half is a deliberate
strictness increase: the requirement's stated purpose is that a pointer file
"cannot accrete in the meantime", and accrual happens in the working tree before
it happens in the index.

**Narrower: only structured formats are read.** The requirement exempts Markdown
and gives the reason — "a pointer file is by nature a structured config file that
something loads". That reason describes an allowlist of formats rather than a
denylist of prose, and the difference is load-bearing in both directions: the
requirement's own text names all three pointer keys and lives in a `.md`, and the
check's own source names them and lives in a `.py`. An earlier revision of this
check used a prose denylist and had to add a carve-out for the `tools/` package
so that the checker would not flag itself; with the allowlist, that carve-out is
gone and the check has no special case for its own code.

Neither decision weakens the pointer-filename rule: a file matching
`*.registry.json` is reported by name before its content or its suffix is
considered.

**The allowlist has a second clause for dotfiles, and it is not decoration.**
`ARTIFACT_SUFFIXES` is a list of *suffixes*, and `PurePath(".env").suffix` is
`""` — pathlib does not read a leading dot as an extension separator. So an
`.env` entry in that set matches `config.env` and misses the bare `.env` it was
added for, which is the failure mode an allowlist is supposed to prevent: a
constraint that constrains nothing. `is_artifact_name` therefore matches `.env`
and the `.env.*` family as *names* as well as by suffix, and the two clauses are
both needed because the two spellings are different files and pathlib sees only
one of them. It matters concretely: this repository keeps a `HA_TOKEN` in a
`.env`, and `.gitignore` covers `.env.local` rather than `.env`.

**Two residual holes in the allowlist, both deliberate.** A data file with no
suffix at all and not a named dotfile — a `secrets`, a `HOSTS` — is not read, and
neither is a format outside the set. Both could be closed, and the shape of the
fix is fixed by the decision above: add the format class to `ARTIFACT_SUFFIXES`,
which is a format list, rather than re-inverting to a denylist of prose, which
would put Python source back in scope and fail on this check's own file. A `.js`
or `.ts` config is therefore also skipped, and the answer to a reader who objects
is that the checker's own `.py` is skipped for the same reason and by the same
rule.

**The untracked half of the enumeration is pinned against the machine.** Because
`git ls-files --others` consults `core.excludesFile`, a developer whose global
ignore file covers `*.yaml` would make a fixture file vanish from the scan. The
test fixtures set `core.excludesFile` to a path inside the fixture that does not
exist, and `core.autocrlf` to `false` so that a fixture written with LF is
stored with LF.

## What the immutability check can and cannot see

`tools/catalog/schemas.py::check_immutability` compares each runtime version
file against its content at the commit that introduced it. Two things are worth
recording, because both were found by review rather than by the tests that
existed:

- **Absence is a change, and the candidate set comes from git.** A version that
  a present version names in `supersedes` must be present — and so must any
  `.json` path git has ever recorded as *added* under a concept directory. The
  second clause is the one with teeth: the content comparison globs the working
  tree and cannot see a deleted file, and the `supersedes` links cannot see a
  deleted *current* version because nothing names it. Without the history
  enumeration, deleting the tip of a succession would silently un-publish a
  version and every check would still pass.
- **Both sides of the comparison are byte-exact.** The working-tree side is read
  through `errors.read_text`, and the git side through `vcs.file_at`, which
  decodes bytes explicitly rather than letting `subprocess` translate newlines.
  A text-mode read of the git side would fold `\r\n` to `\n` and make a file
  that was merely checked out differently look unchanged.

## What the catalog data check actually asserts

`tools/catalog/validate.py::check_catalog_data_files` pairs each data file under
`catalog/` with the schema of the same stem. Having a schema beside a file is not
the claim; the claim is that the file **validates** against it, so the instance is
loaded and checked. An earlier revision asserted only that the schema file
existed, which is a different and much weaker thing: every stub passed it while
nothing looked at what any of them contained, and a data file that contradicted
its own schema would have been reported clean.

Three quarters of the check are about not dying, because it runs as the
pre-commit hook and as the CI gate:

- **A schema that cannot be applied is a diagnostic, not a traceback.** The
  document is checked against the 2020-12 meta-schema before it is used, and so
  is every file a `$ref` pulls in. Without the first half, a keyword typo —
  `"type": "objectt"`, `"maxItems": "x"`, an unterminated `pattern`,
  `"multipleOf": 0` — reaches `iter_errors` and raises something that is neither
  a resolution failure nor a `CheckError`, so it escapes `validate_all` and takes
  the hook down with a traceback instead of naming the file. `schemas/catalog/`
  is the one schema directory edited in place as sections 2–7 proceed, so this is
  a live hazard rather than a hypothetical one. Without the second half, a
  malformed *referenced* schema fails later and elsewhere, inside `iter_errors`,
  where the diagnostic would name the data file that happened to reference it
  rather than the schema at fault: `_retrieve` therefore treats a file that
  parses but is not a valid schema as one that is not there, and the walk's
  message says "valid" for exactly that reason.
- **The catch around `iter_errors` keeps a general arm, and an input reaches
  it.** A `$ref` cycle between two files is meta-schema-valid — the meta-schema
  never follows a reference — so the meta-check passes both documents, the walk
  resolves both references because each names a file that is present, and the
  loop only closes when the library applies them, as `RecursionError`. That is
  not a resolution failure and it shares no base with one, which is why the catch
  is not narrowed to the family. This matters beyond the cycle: the four
  malformed keywords above raise `UnknownType`, `TypeError`, `re.error` and
  `ZeroDivisionError`, which share no base with each other either.

- **A resolution failure is a diagnostic, not a traceback.** `$ref` resolution
  raises from *inside* instance validation, and the module catches it there. The
  catch is exactly one class, `Unresolvable`, and that is a measured narrowing
  rather than a reading of the hierarchy. The hierarchy suggests otherwise —
  `NoSuchResource` and `Unretrievable` descend from `KeyError` and
  `CannotDetermineSpecification` from `Exception` directly, so they share no base
  with `Unresolvable` — and an earlier revision of this file caught all six on
  the strength of that reading alone. Review showed the extra five were
  unreachable: every failure this code can provoke comes from `Resolver.lookup`,
  which normalises all of them to `Unresolvable`; `_resource` states its dialect,
  so `CannotDetermineSpecification` cannot arise; and a schema without `$id` is
  refused before any resolver is built, so `NoInternalID` cannot either. A catch
  wide enough to cover classes no input can reach is not safety, it is a claim
  the code does not make good on, and it hides the narrowing from every test.
  The narrow catch is not dead code, either: a `$dynamicRef` has no static
  target, so the pre-walk below cannot see it, and resolving one that names no
  anchor raises `Unresolvable` from inside `iter_errors` — the case
  `test_a_reference_the_static_walk_cannot_see_is_caught_during_validation`
  drives. `validate_all` catches `CheckError` and nothing else, so an uncaught
  one reached the command as a traceback.
- **Every literal `$ref` is resolved ahead of validation**, by walking the
  document rather than by letting an instance find the branches it happens to
  reach. A reference under `$defs` that nothing points at yet is the ordinary
  state of a schema waiting for the task that will use it, and it is still a
  claim that the referenced schema exists. The walk separates out a `$ref` whose
  value is not a string — `42`, `null`, `["#/$defs/x"]` — and reports it as its
  own diagnostic: a non-string reference names nothing, and handing it to
  `jsonschema` raises `AttributeError` from inside a resolver rather than
  producing a diagnostic, which on the pre-commit path is a traceback instead of
  a message. The walk is deliberately blind to exactly one reference form,
  `$dynamicRef`, which has no static target — and that blindness is why the
  instance-validation catch above is not redundant.
- **A broken schema does not abort the loop.** `load_catalog_schema` raises
  `CheckError`, and `validate_all` catches that around the whole check — so a
  single typo in one catalog schema used to skip validation of every other data
  file, in a check whose entire purpose is that no data file goes unvalidated.
  The schema is reported and the loop continues.

Two smaller decisions. `_retrieve` builds its `Resource` with `DRAFT202012`
stated rather than sniffed, because `Resource.from_contents` infers the dialect
from `$schema` and raises for a document without one — a missing field would have
crashed the hook. It also meta-validates whatever it loads before returning it,
which is the second half of the first bullet above. And
`check_catalog_data_files` widens the validator to
`jsonschema.protocols.Validator`, which looks like a no-op: the typeshed stub for
`Draft202012Validator` leaves `iter_errors`' instance parameter unannotated in
both overloads, so strict mode reports every call to it as partially unknown, and
annotating the variable does not help because pyright narrows it back from the
right-hand side.

**A hole this check has, recorded because nothing else records it.** Each data
file is validated against a registry containing only its own document, and every
cross-file `$ref` goes through `_retrieve` to disk by URI. So a catalog schema
that declares the *same* `$id` as another one is never compared with it: the
duplicate resolves to whichever file the reference named, and a schema whose
`$id` collides with its own reference resolves to itself and validates clean.
Nothing in section 1 requires detecting this, and no later task assigns it —
task 7.6 is about a file outside `schemas/<concept>/` restating a room-type, slot
or house *shape*, which is a different failure. It is written down here as open
and unowned rather than attributed to a task that does not cover it, because an
item assigned to the wrong owner is an item nobody picks up.

**Deep nesting, at the depth it actually fails, disclosed rather than defended.**
An earlier version of this paragraph claimed the check could not be made to fail
here, on the reasoning that `load_catalog_schema`'s `json.loads` would exhaust the
stack before `_invalid_schema`'s meta-validation ever ran. Measurement says
otherwise, so the claim is corrected rather than kept. For a schema of nested
single-property objects on this interpreter, with the default limit of 1000,
`json.loads` loads cleanly at every depth tried up to 1000, while `check_schema`
raises `RecursionError` at depth 100 and above — 80 is accepted, 100 is not. The
frame that paragraph called unreachable is reachable, by an input an order of
magnitude smaller than the one it named. The recursive ceiling is per-frame, and
the meta-validator spends far more of it per level than the loader does.

Two paths are now guarded, each with a test that fails when its arm is deleted.
`_invalid_schema` catches `RecursionError` next to `SchemaError`, so a catalog
schema nested that deep is reported rather than escaping; `_load_data_file`
catches it as well, so a *data* file past its parser's limit is reported as
unparsed rather than read as nothing or crashing the hook. That limit differs by
parser, which is why the two sentinels are distinct: `yaml.safe_load` loads a
sequence nested 300 deep and raises at 500, while `json.loads` holds past 1000
and raises between there and 1500. Those are the only two ceilings measured. A
nested YAML *mapping* was probed too and is deliberately not quoted as a third:
the obvious rendering, `a: a: a: 1`, is a syntax error at every depth, so it
raises `ScannerError` — a `YAMLError` the check caught before this change — and
measures nothing about depth.

A third path was guarded and then unguarded again. `_retrieve` — the callable the
registry uses to load a `$ref` target — looked like the same hole, since the
`RecursionError` raised by its `check_schema` is not `SchemaError` and so is not
caught by its `except`. It is not a hole: `Registry.get_or_retrieve` wraps the
entire retrieve callable in `except Exception` and re-raises as `Unretrievable`,
and `Resolver.lookup` maps that to `Unresolvable`, which `_unresolved_references`
already catches. A `RecursionError` arm there was measured against a bare
`check_schema` and produced a byte-identical diagnostic, so it was removed rather
than kept as an unreachable-with-respect-to-behaviour branch. The test stays, and
its docstring says plainly that its guard is the dependency's rather than this
package's — the guarantee is real and this check depends on it, but nothing here
mutates to prove it.

Two paths remain open, and they are the deep end rather than the near end:
`load_catalog_schema`'s own `json.loads` and the static reference walk both
recurse per level with a much higher ceiling than the meta-validator's. A schema
file deep enough to exhaust either — past 1000, not 100 — still raises out of the
command, since the `RecursionError` is neither a `CheckError` nor caught by
anything above.

They are not equally cheap to close, and the difference is worth stating rather
than collapsing into one recommendation. The loader is a one-line fix of exactly
the kind just made in `_load_data_file`: the same `except RecursionError` arm on
`load_catalog_schema`'s `json.loads`, guarding the schema path symmetrically with
the data path. The static walk has no equivalent single site — it recurses
through `_references`, and an arm there would have to decide what a
partially-walked document means. The one fix that covers both, and everything
else a check might raise, is a catch at the outermost boundary in `validate_all`
turning any exception from any check into a diagnostic — which changes all nine
checks' contract rather than this one's. All three are left as decisions rather
than taken as a side effect of section 1; the honest summary is that the
remaining window has more than one door, not that it has only one.

## Deferred scenarios, and where they land

The delta specs contain scenarios Phase 0 implements but does not yet test,
because the artifact they need does not exist yet. Each is named in the module
docstring of the test file that owns the area, so its absence is a decision
rather than an omission. The last two rows are a different kind of entry: they
are hand-offs between later tasks rather than untested scenarios, and no test
file owns them, so they are recorded only here.

| Scenario | Lands in |
| --- | --- |
| A concept is defined twice; a catalog data file restates a runtime concept | task 7.6, the conformance check |
| Vocabulary is closed, and gains terms | tasks 7.1 and 7.4 |
| Vocabulary terms become an enumeration in `1.1.0`, so the generated schema changes | task 7.1 regenerates `schemas/catalog/raw-behaviors.json` |
| Example house, pack and export | tasks 7.3–7.5 |
| Boundary checks run in CI; a CI job needs the reference clones | task 7.8 |
| Exit criterion in CI | task 7.8 |
| A raw record carrying an invented key; a permissive pattern; a free-form string | task 3.5, the allowlist checks |
| An entry in `hardcoded_refs.yaml` resolving to neither a slot nor a constant | task 5.3, where `slots.yaml` exists |
| A raw record carrying `slots`, against a still-empty `slots.yaml` (`{"enum": []}` admits nothing) | task 5.3 lands before or with task 3.8 |
| Two catalog schemas declaring the same `$id`; nothing compares them | **unassigned** — 7.6 covers restated *shapes*, not duplicate ids. See "A hole this check has" above |

## The vocabulary field resolves to a shape, not to a list of terms

Task 3.8 commits `raw-behaviors.json` carrying the trigger, condition and action
terms the extraction derived. Task 7.1 publishes those same terms as
`behavior-vocabulary/1.1.0.json`. The two are in tension in one direction only,
and the resolution is worth writing down because it is not obvious from either
task:

`behavior-vocabulary/1.0.0.json` is **shape-only** — the configuration-schemas
spec says it "is created with its shape in the first pass and gains its derived
terms as a new version file". Its trigger, condition and action fields therefore
carry a pattern, not an `enum`. A generated schema cannot enumerate terms that do
not exist yet, so `facts.py` resolves the vocabulary field to the current
version's declared **element schema** for that axis, dereferenced through
same-document `$ref`s, rather than to a snapshot of its terms. The record's terms
are then shape-constrained until task 7.1 enumerates them, which is the only
reading under which task 3.8 can be implemented before task 7.1 and still be
tightened by it afterwards.

Two consequences. The axes live under `properties` in the runtime vocabulary
schema, and `modes` and `profiles` one level deeper under
`properties.axes.properties`, so a lookup that reads only the top level finds
nothing and reports the axis as missing. And because the resolution is to the
*element* schema rather than to a snapshot of the terms, the generated schema
follows whatever that element schema says. Today it says "a pattern", so the
record's terms are shape-constrained. When task 7.1 publishes
`behavior-vocabulary/1.1.0.json` with its terms enumerated, the element schema
becomes an `enum`, `facts.py` resolves to it unchanged, and the generated
`raw-behaviors.json` schema tightens to the real term set — which is the
tightening task 7.1 is meant to deliver, and it arrives with no code change.

It does need one act, and no task names it: the committed
`schemas/catalog/raw-behaviors.json` is regenerated at that point. A test asserts
the committed file equals a fresh generation, so the moment 7.1 lands an
enumerating version the suite goes red until the file is rewritten. That is the
behaviour we want — a schema change nothing regenerates would be a silent drift
— but it is a hand-off between two tasks, so it is recorded in the deferral table
above rather than left to be rediscovered by whoever runs the suite next.

## The allowlist carries two forms the task did not enumerate

Task 1.6 lists the forms a fact field may take as "an enum, an integer, a
boolean, a list of those, or a named shape". `schemas/catalog/fact-fields.yaml`
carries seven; the two extra are `vocabulary` and `enum_ref`, both live in the
generated schema today. They are recorded here rather than silently added,
because a closed list is only closed if extending it is visible.

`vocabulary: <axis>` resolves to the current `behavior-vocabulary` version's
element schema for an axis. It exists because 1.6's five forms offer no way to
name the vocabulary without restating its pattern a second time, and a restated
pattern is a second definition free to drift from the published one — the exact
failure the conformance check in task 7.6 is written to catch.
`enum_ref: <file>#<field>` names a closed set that lives in another committed
file, which is how a record's `slots` admits only slots `catalog/slots.yaml`
declares without copying that list into the generated schema.

Neither form can hold authored text, which is the property the five original
forms exist to enforce: `vocabulary` dereferences to an element schema that is
itself a pattern or an enum, and `enum_ref` admits a value only by membership in
a committed list. So the deviation widens *where a closed set comes from*, not
*what a field may contain* — and that, rather than the count of forms, is the
thing worth holding.

**A consequence of `enum_ref`, recorded so it is not a surprise.** While
`catalog/slots.yaml` is still the empty stub, the generated `slots` field is
`{"enum": []}`, which admits nothing. That is the fail-closed direction and the
right one — an undeclared vocabulary should reject every slot, not wave them
through — but it means a raw record carrying a non-empty `slots` list cannot
validate until task 5.3 has filled `slots.yaml`. Task 3.8 generates those
records, so 5.3 has to land before or alongside 3.8, or the generated records
must arrive with `slots` empty. Neither task says so; the constraint is recorded
in the deferral table above.

## The two checks that read the clones are local-only

`spec.txt` wants the corpus derived from four third-party repositories that are
`gitignore`d and two of which grant no licence to redistribute. CI must not
depend on them, so the two checks that read them run locally and their **outputs**
are committed:

- the `git ls-files` closure check over each clone, and
- the prose gate comparing a shipped string against the verbatim store.

Everything in `oh-catalog validate` is a pure function of this repository, which
task 7.7 verifies by running the suite in a checkout with `ressources/` and
`.local/` removed.
