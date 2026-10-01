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

## Deferred scenarios, and where they land

The delta specs contain scenarios Phase 0 implements but does not yet test,
because the artifact they need does not exist yet. Each is named in the module
docstring of the test file that owns the area, so its absence is a decision
rather than an omission:

| Scenario | Lands in |
| --- | --- |
| A concept is defined twice; a catalog data file restates a runtime concept | task 7.6, the conformance check |
| Vocabulary is closed, and gains terms | tasks 7.1 and 7.4 |
| Example house, pack and export | tasks 7.3–7.5 |
| Boundary checks run in CI; a CI job needs the reference clones | task 7.8 |
| Exit criterion in CI | task 7.8 |

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
