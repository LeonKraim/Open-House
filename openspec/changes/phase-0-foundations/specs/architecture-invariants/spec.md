# Spec Delta — architecture-invariants

## Purpose

The repository module layout and the purity rules installed in Phase 0 — while
the trees are still empty and the rules are cheap to hold — so later phases
build against a shape that cannot silently erode.

## ADDED Requirements

### Requirement: Fixed repository module layout
The repository SHALL provide the top-level modules `engine/`, `ha_adapter/`,
`custom_components/`, `panel/`, `packs/official/` and `sim/`, plus `schemas/`
and `catalog/`. The Python packages — `engine`, `ha_adapter`, `sim` and
`custom_components` — SHALL each be importable on their own and carry a
`py.typed` marker. `panel/` is a TypeScript and Lit tree and `packs/official/`
is YAML data; each SHALL exist with its own marker file and is not subject to
the Python importability rule.

#### Scenario: A required module is absent
- **WHEN** any required top-level module is missing
- **THEN** the layout check fails and names the missing module

#### Scenario: A Python package is not importable
- **WHEN** one of the four Python packages cannot be imported on its own
- **THEN** the layout check fails and names the package and the import error

#### Scenario: A non-Python module is checked as Python
- **WHEN** the layout check runs
- **THEN** `panel/` and `packs/official/` are checked only for existence, and
  are not required to be importable or to carry `py.typed`

### Requirement: Engine core is free of Home Assistant imports
No file under `engine/` SHALL import `homeassistant`, directly or transitively
through a guarded import, and the engine SHALL import no third-party module
outside its declared dependency list. **The declared dependency list is the
`dependencies` array of the root `pyproject.toml`** — one list, one file, named
here rather than left to an implementer, because two implementations (root
versus per-module) would disagree on the same tree. Both halves are one import
scan over the same tree. This rule is installed in Phase 0 on an empty tree so
that the first Phase 1 commit cannot violate it.

The requirement deliberately says nothing about the engine being async. An
earlier draft asserted it; whether the engine's shape is async is a Phase 1
design question, and on an empty tree the assertion cannot be violated by
anything, which makes it decoration rather than a rule.

#### Scenario: Engine imports Home Assistant
- **WHEN** any `engine/` file imports `homeassistant` or a submodule
- **THEN** the purity check fails and names the file and the import

#### Scenario: Home Assistant import is hidden behind a guard
- **WHEN** a `homeassistant` import appears inside a `try`, `if TYPE_CHECKING`
  or function body within `engine/`
- **THEN** the purity check still fails and names the file

#### Scenario: Engine imports an undeclared dependency
- **WHEN** an `engine/` file imports a third-party module absent from the
  declared dependency list
- **THEN** the purity check fails and names the file and the module

#### Scenario: Purity holds on the committed tree
- **WHEN** the purity check runs on the committed tree
- **THEN** it passes

### Requirement: Registry stays out of this repository
This repository SHALL NOT contain a registry pointer file or a generated
registry index. The check is **pattern-based over committed artifact files**,
because the pack registry does not exist until Phase 7 and there is nothing else
to recognise against. A file is a registry pointer when it carries any of the
keys `registry_url`, `registry_index` or `registry_key`, or matches the filename
pattern `*.registry.json`. Naming an explicit key set rather than "a URL" is
deliberate: a broader recogniser would flag `.pre-commit-config.yaml`'s `repo:`
entries and the `registry` field of `package.json`, both of which this phase
commits legitimately.

**The scan covers machine-read artifacts and not prose.** A pointer file is by
nature a structured config file that something loads, so Markdown documents are
out of scope — otherwise the check would fail on the text that defines it, since
this very requirement names the three keys and lives in a committed Markdown
file. The scope is stated in the check rather than left to a reader to infer.

The registry is to be a separate repository consumed over the network; its
creation is deferred to Phase 7, because Phase 0 cannot create a repository
outside this project root. This is therefore the one half of the registry
invariant Phase 0 can actually test, and it is enforced now so pointer files
cannot accrete in the meantime.

#### Scenario: Registry files appear locally
- **WHEN** a committed file carries one of the pointer keys or matches
  `*.registry.json`
- **THEN** the boundary check fails and names the offending path

#### Scenario: A local pack reference is not a pointer
- **WHEN** a file names a pack that lives in `packs/official/`
- **THEN** the boundary check passes

#### Scenario: Ordinary tooling is not mistaken for a pointer
- **WHEN** the check runs over `.pre-commit-config.yaml` and `package.json`
- **THEN** it passes, because neither carries a pointer key

#### Scenario: Prose that names the keys is not a pointer
- **WHEN** the check runs over the committed Markdown that specifies it,
  including this requirement's own text naming `registry_url`,
  `registry_index` and `registry_key`
- **THEN** it passes, and the committed tree of this phase is green

### Requirement: The project is under version control
The project root SHALL be a git repository, so that CI, `pre-commit` and the
history-based schema-immutability check have a history to read; without one,
the immutability check cannot be evaluated at all. The four reference clones
under `ressources/` are separate repositories with their own history and their
own licences: they SHALL be read as input and SHALL NOT be tracked here.

#### Scenario: The project is not a git repository
- **WHEN** the project root has no git history
- **THEN** the immutability check fails and names the project root, rather than
  silently passing

#### Scenario: A reference clone is committed
- **WHEN** any file under `ressources/` is tracked in this repository
- **THEN** the boundary check fails and names the path

### Requirement: Boundary checks run in CI
The layout, engine-purity and registry-boundary checks SHALL run in CI on every
change and SHALL fail the build. CI SHALL depend only on this repository: the
reference clones under `ressources/` are an input to a local generation step,
and no CI job SHALL require them or the network.

#### Scenario: A boundary is crossed
- **WHEN** any invariant check fails
- **THEN** CI fails and names the invariant and the offending file

#### Scenario: CI job needs the reference clones
- **WHEN** a CI job reads from `ressources/`
- **THEN** the CI configuration check fails and names the job
