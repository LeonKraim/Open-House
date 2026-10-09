"""Structural invariants: module layout, engine purity, registry boundary.

These are the checks that hold the repository's shape rather than its contents.
They read source text and file names, and they run in CI on a checkout with no
reference clones present.

One of them imports, and it is the exception worth naming. `check_layout` runs
`import <package>` for each of the five Python packages in a subprocess, because
"the package is importable" is not a property of the files: a package can carry
`__init__.py` and `py.typed` and still raise the moment it is loaded, and every
later phase loads them before doing anything else. The consequences are that
`oh-catalog validate` needs a working interpreter for the tree it is checking,
and that a check which hangs on import is reported as a failure rather than
hanging the run -- `IMPORT_TIMEOUT_SECONDS` bounds it.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from . import paths, vcs
from .errors import CheckError, Report

PURITY_CHECK = "engine-purity"
ENGINE_LAYERING_CHECK = "engine-layering"
COMPOSITION_ROOT_CHECK = "composition-root-purity"
LAYOUT_CHECK = "layout"
REGISTRY_CHECK = "registry-boundary"
VERSION_CONTROL_CHECK = "version-control"

#: The modules whose absence from `engine/` is the whole point of the check.
FORBIDDEN_IN_ENGINE = frozenset({"homeassistant"})

#: The composition root -- the package that wires the engine to the simulator.
#:
#: It is named here rather than derived because the check has to know which
#: package's files *must not* be imported by the engine and the simulator, and
#: "the package beside `engine/` whose name is not one of the frozen four" is a
#: description, not a name. `control-surface` fixes it as `openhouse/`.
COMPOSITION_ROOT = "openhouse"

#: The packages the composition root may import but that may not import it.
#:
#: The direction is the whole requirement (`design.md` D12): the engine's graph
#: has one outward edge -- the `HouseAdapter` port -- and an engine that imported
#: the facade would acquire a second one, to the simulator, which the facade
#: itself pulls in.
COMPOSITION_ROOT_FORBIDDEN_IN: tuple[str, ...] = ("engine", "sim")

#: Packages the engine's own modules may not import.
#:
#: `sim` only, and the omission of `openhouse` is the point rather than an
#: oversight: the facade direction is `check_composition_root_purity`'s, and a
#: second check reporting it would turn one import into two diagnostics.
#: `pyproject.toml`'s `dependencies` array would admit a third-party name; a
#: first-party package refusing another is a layering rule and lives here.
ENGINE_LAYERING_FORBIDDEN: tuple[str, ...] = ("sim",)

#: First-party packages, which are allowed by the "is this third-party?" test.
#:
#: Membership answers only whether a module is ours, so `openhouse` belongs
#: here: it is a first-party package and not a third-party distribution. The
#: *direction* rule -- that `engine/` and `sim/` may not import `openhouse/` --
#: is orthogonal to this question and is one first-party package refusing
#: another, so it is enforced by `check_composition_root_purity` rather than by
#: leaving `openhouse` out of this set. Leaving it out would make the engine's
#: generic scan report an `openhouse` import as "undeclared", which is the wrong
#: diagnosis for a direction violation and would fire twice for one problem.
FIRST_PARTY = frozenset(
    {"engine", "ha_adapter", "sim", "custom_components", "tools", "openhouse"}
)

#: Modules that must exist for the layout to be the one the spec froze.
#:
#: `packs/` is deliberately not here. The shipped pack corpus is a working-tree
#: artifact rather than a source directory -- it is untracked and a fresh
#: checkout has none -- so requiring it would fail the layout check on exactly
#: the checkout this repository ships, for a directory the product runs without.
REQUIRED_DIRECTORIES: tuple[str, ...] = (
    "engine",
    "ha_adapter",
    "custom_components",
    "panel",
    "sim",
)

#: Directories that are Python packages and therefore carry `py.typed`.
#:
#: `openhouse/` is the fifth, added in Phase 1: `control-surface` makes it a
#: top-level Python package "beside `engine/`, `sim/` and `ha_adapter/`,
#: carrying a `py.typed` marker and importable on its own", which is exactly
#: what this list checks for. The frozen four's layout is not loosened by the
#: addition -- the fifth package is held to the same rule.
TYPED_PACKAGES: tuple[str, ...] = (
    "engine",
    "ha_adapter",
    "sim",
    "custom_components",
    "openhouse",
)

REGISTRY_KEYS: tuple[str, ...] = ("registry_url", "registry_index", "registry_key")
REGISTRY_FILENAME = re.compile(r"^.*\.registry\.json$")

#: How long a package import may take before it is reported as not importable.
IMPORT_TIMEOUT_SECONDS = 60

#: Suffixes the registry scan reads. This is an allowlist of structured formats
#: rather than a denylist of prose, because the requirement's reason for
#: exempting Markdown is not that Markdown is prose -- it is that "a pointer file
#: is by nature a structured config file that something loads". That description
#: admits YAML, JSON, TOML and their neighbours, and it excludes Python source,
#: which is code. The distinction matters in both directions: the requirement's
#: own text names all three keys and lives in a `.md`, and this package defines
#: the recogniser and lives in a `.py`, so a scan over "everything that is not
#: prose" would fail on the two files that have to be green for the check to
#: exist at all.
#:
#: Two residual holes are known and deliberate. A data file with no suffix at all
#: -- a `secrets`, a `HOSTS` -- is not read, because deciding whether an
#: extension-less file is structured requires parsing it and guessing; and a
#: format class outside this set is not read. The list is closed by *format*, so
#: widening it means naming a format here rather than re-inverting to a denylist,
#: which would put Python source back in scope and fail the checker's own file.
ARTIFACT_SUFFIXES = frozenset(
    {
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".xml",
        ".csv",
        ".env",
    }
)

#: Dotfile artifacts, matched by whole name rather than by suffix.
#:
#: `.env` appears in `ARTIFACT_SUFFIXES` as well, and both are needed, because
#: the two spellings are genuinely different files and pathlib only sees one of
#: them. `PurePath("config.env").suffix` is `".env"`, so the set entry covers
#: that name; `PurePath(".env").suffix` is `""`, because pathlib does not read a
#: leading dot as an extension separator, so the bare `.env` is invisible to the
#: set however it is spelled. An allowlist entry that matched nothing would be
#: the failure mode an allowlist is supposed to prevent -- a constraint that
#: constrains nothing -- so the dotfile is named here as a name.
#:
#: The prefix covers the `.env.local` and `.env.example` spellings a real project
#: commits. It matters concretely here: this repository keeps a `HA_TOKEN` in a
#: `.env`, and `.gitignore` covers `.env.local` rather than `.env`, so a bare
#: committed `.env` is exactly the file that would otherwise slip past.
DOTFILE_ARTIFACTS: tuple[str, ...] = (".env",)
DOTFILE_PREFIXES: tuple[str, ...] = (".env.",)


def is_artifact_name(name: str) -> bool:
    """Whether the registry scan reads a file of this name.

    A named function rather than an inline test, because the dotfile clause is
    the kind of special case that gets lost the next time the set is edited, and
    because the test that guards it needs to call the same predicate the scan
    does rather than restate it.
    """
    lowered = name.lower()
    if lowered in DOTFILE_ARTIFACTS:
        return True
    if lowered.startswith(DOTFILE_PREFIXES):
        return True
    return Path(lowered).suffix in ARTIFACT_SUFFIXES


#: Top-level directories the registry scan never reads. The clones and the
#: withheld-expression store are ignored by `.gitignore` and so are absent from
#: the list git gives back; naming them here as well means the scope survives a
#: later change to the ignore rules, and it is what the test asserting their
#: exclusion actually exercises.
_OUT_OF_SCOPE_ROOTS = frozenset(
    {"ressources", ".local", ".git", "node_modules", "__pycache__"}
)


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_top_level(path: Path) -> set[str]:
    """Top-level module names imported by a file, guards included.

    Parsed rather than grepped, and with no exemptions: an import inside `try:`
    or under `TYPE_CHECKING` is still an import, and both are exactly how a
    forbidden dependency gets into an engine while looking absent to a reader.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        # A file that will not parse is a different check's problem; this one
        # should not also fire and produce a second, misleading diagnostic.
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            # `level > 0` is a relative import, which is first-party by
            # construction and has no top-level name.
            and node.level == 0
            and node.module
        ):
            names.add(node.module.split(".")[0])
    return names


def declared_dependencies() -> set[str]:
    """The `dependencies` array of the root `pyproject.toml`.

    This is the single declared list the purity check reads. A dependency named
    here may be imported; one that is not, may not. Normalisation strips the
    version specifier and folds case and `_`/`-` so that `PyYAML` and `pyyaml`
    are the same dependency, which they are.
    """
    pyproject = paths.ROOT / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError as exc:
        # A `CheckError` rather than the `FileNotFoundError`: the validator is
        # the pre-commit hook, and a missing `pyproject.toml` is a fact about the
        # tree that should be named, not a traceback out of the command users run
        # before every commit.
        raise CheckError(
            PURITY_CHECK,
            "pyproject.toml",
            f"cannot be read, so the declared dependency list is unknown: {exc}",
        ) from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CheckError(
            PURITY_CHECK,
            "pyproject.toml",
            f"is not valid TOML, so the declared dependency list is unknown: {exc}",
        ) from exc
    raw = data.get("project", {}).get("dependencies", [])
    out: set[str] = set()
    for entry in raw:
        name = re.split(r"[<>=!\[; ]", entry, maxsplit=1)[0].strip()
        if name:
            out.add(_fold(name))
    return out


def check_layout(report: Report) -> None:
    for relative in REQUIRED_DIRECTORIES:
        directory = paths.ROOT / relative
        if not directory.is_dir():
            report.add(LAYOUT_CHECK, relative, "required directory is missing")
    for relative in TYPED_PACKAGES:
        package = paths.ROOT / relative
        has_init = (package / "__init__.py").is_file()
        if not has_init:
            report.add(LAYOUT_CHECK, relative, "python package has no `__init__.py`")
        if not (package / "py.typed").is_file():
            report.add(LAYOUT_CHECK, relative, "python package has no `py.typed`")
        if not has_init:
            # Importing a directory that is not a package would report a second,
            # misleading fault for the same missing file.
            continue
        # Importability is the clause the markers only stand in for. A package
        # can carry `__init__.py` and `py.typed` and still raise on import, and
        # every later phase imports these packages before it does anything else,
        # so "the layout is right" has to mean "the modules load" and not merely
        # "the files are shaped like modules".
        error = _import_error(relative)
        if error is not None:
            report.add(
                LAYOUT_CHECK,
                relative,
                f"python package does not import: {error}",
            )
    # `panel/` and `packs/official/` are checked for existence only: the panel is
    # a TypeScript build and a pack is data, so requiring Python markers of them
    # would be requiring the wrong thing.


def _import_error(module: str) -> str | None:
    """Import a package in a subprocess and return the error, or `None`.

    A subprocess rather than `importlib` in-process, for two reasons. Importing
    the fixture's `engine` into this process would bind it in `sys.modules`
    against the real `engine`, so the second tree a test builds would silently
    receive the first one's package -- and the check would stop being about the
    tree it was pointed at. And an import runs arbitrary code, which a hook that
    fires on every commit should not execute inside whatever process invoked it.

    The subprocess runs with its working directory set to the root, which puts
    that root first on `sys.path`, so the answer is about *this* tree rather than
    about whatever `site-packages` the machine happens to carry. The timeout is
    a backstop against a package whose import blocks; a package that hangs is not
    importable in any useful sense.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=paths.ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=IMPORT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return None
    lines = [line.strip() for line in (proc.stderr or proc.stdout).splitlines()]
    detail = [line for line in lines if line]
    return detail[-1] if detail else f"`import {module}` exited {proc.returncode}"


def declared_import_names() -> set[str]:
    """Import names the declared distributions provide, plus the names themselves.

    A `dependencies` array names **distributions**, and a distribution's name is
    not always its import name: `PyYAML` is imported as `yaml`. Comparing the
    array against import names directly would report `import yaml` as undeclared
    on a project that declares `pyyaml>=6.0` -- the opposite of what the
    requirement says, and a false positive on the very first third-party import
    a Phase 1 engine is likely to make.

    The mapping is read from installed metadata rather than kept as a table here,
    because a table would be the same hand-maintained list the requirement exists
    to avoid, and it would drift from what is actually importable. A declared
    distribution that is not installed contributes nothing beyond its own name,
    which is correct: an import of it would fail before purity was the question.

    This does read the installed environment, which the requirement's "one list,
    one file" does not invite, so the defence is worth stating: the environment
    can only ever **widen** the set of names the declared list resolves to, never
    admit a name the list does not contain. A module that is not a declared
    distribution and not stdlib is rejected whether or not it happens to be
    installed. The dependency is also not new -- the tooling imports `yaml` from
    a declared distribution to run at all -- so a declared-but-uninstalled
    dependency is a configuration this project cannot operate under anyway.
    """
    declared = declared_dependencies()
    try:
        provided = importlib.metadata.packages_distributions()
    except (ImportError, OSError, ValueError):
        # Metadata can be unreadable on a broken environment. Falling back to the
        # declared names alone is the conservative answer: it may reject an
        # import that would have resolved, but it can never admit one that is
        # genuinely undeclared.
        return set(declared)

    names = set(declared)
    for module, distributions in provided.items():
        for distribution in distributions:
            if _fold(distribution) in declared:
                names.add(module)
    return names


def _fold(name: str) -> str:
    """Normalise a distribution name the way the declared list is normalised."""
    return name.lower().replace("-", "_")


def check_engine_purity(report: Report) -> None:
    """Engine imports only stdlib, first-party code and declared dependencies.

    Every rejection is a `Diagnostic`, and the diagnostic is the whole result.
    An earlier draft also returned the rejected pairs so tests could assert on a
    tidier shape; that put the test's convenience into the production signature,
    and the tests now read the diagnostics they are actually meant to be about.
    """
    engine = paths.ROOT / "engine"
    if not engine.is_dir():
        return

    declared = declared_import_names()

    for path in _python_files(engine):
        relative = path.relative_to(paths.ROOT).as_posix()
        for module in sorted(_imported_top_level(path)):
            if module in FORBIDDEN_IN_ENGINE:
                report.add(
                    PURITY_CHECK,
                    relative,
                    f"imports `{module}`; the engine must be free of Home "
                    "Assistant imports, guarded ones and TYPE_CHECKING ones "
                    "included",
                )
                continue
            if (
                module not in FIRST_PARTY
                and module not in declared
                and module not in _stdlib()
            ):
                report.add(
                    PURITY_CHECK,
                    relative,
                    f"imports `{module}`, which is neither stdlib, first-party, "
                    "nor declared in the `dependencies` array of the root "
                    "pyproject.toml",
                )


def _stdlib() -> frozenset[str]:
    import sys

    return frozenset(sys.stdlib_module_names)


def check_composition_root_purity(report: Report) -> None:
    """The composition root's two import rules, in opposite directions.

    `engine/` and `sim/` may not import `openhouse/`, and `openhouse/` may
    import only the standard library, first-party packages (the engine and the
    simulator among them) and declared dependencies. The spec states both halves
    as one requirement -- "the composition root may import the engine and the
    simulator ... and neither the engine nor the simulator may import it" -- and
    they are one function because they are one boundary seen from its two sides.

    The forbidden-direction half is a scan over `engine/` and `sim/`, and it is
    a *name* test rather than the generic third-party test in
    `check_engine_purity`. That scan would not catch this at all: `openhouse` is
    a first-party package, so `FIRST_PARTY` admits it, and the direction is a
    first-party package refusing another rather than a third-party question.
    The check that owns it is this one, and it names the import and the file
    that makes it.

    The allowing-direction half is the same shape as the engine scan, run over
    `openhouse/` instead of `engine/`: a top-level import that is neither
    stdlib, first-party, nor declared is rejected. It is a separate half rather
    than a call to `check_engine_purity` with a different root because that
    function also forbids `homeassistant` and reports under the engine's check
    name; the composition root's only third-party rule is the declared list.

    Both halves read imports the way `check_engine_purity` does, through
    `_imported_top_level`, so a guarded import or one under `TYPE_CHECKING` is
    caught rather than missed -- the requirement says "directly or under a
    guarded import" and this is the same AST walk that already satisfies that
    clause for the engine.
    """
    for package in COMPOSITION_ROOT_FORBIDDEN_IN:
        directory = paths.ROOT / package
        if not directory.is_dir():
            continue
        for path in _python_files(directory):
            if COMPOSITION_ROOT not in _imported_top_level(path):
                continue
            relative = path.relative_to(paths.ROOT).as_posix()
            report.add(
                COMPOSITION_ROOT_CHECK,
                relative,
                f"imports `{COMPOSITION_ROOT}`; the composition root may import "
                f"`{package}/`, but `{package}/` may not import the composition "
                "root, directly or under a guarded import",
            )

    composition_root = paths.ROOT / COMPOSITION_ROOT
    if not composition_root.is_dir():
        return

    declared = declared_import_names()

    for path in _python_files(composition_root):
        relative = path.relative_to(paths.ROOT).as_posix()
        for module in sorted(_imported_top_level(path)):
            if module in FIRST_PARTY or module in declared or module in _stdlib():
                continue
            report.add(
                COMPOSITION_ROOT_CHECK,
                relative,
                f"imports `{module}`, which is neither stdlib, first-party, "
                "nor declared in the `dependencies` array of the root "
                "pyproject.toml",
            )


def check_engine_layering(report: Report) -> None:
    """The engine may not import the simulator -- task 3.6.

    `design.md` D12 gives the engine one outward edge, the `HouseAdapter` port,
    and `check_composition_root_purity` already refuses the other direction: an
    engine that imported `openhouse/` would reach the simulator through the
    facade. This check is the remaining half, and it is the one Phase 2 needs:
    the sandbox, the pack validator and the install lifecycle are engine modules
    whose whole point is that they run unchanged against a real Home Assistant
    adapter in Phase 4, and a module that imported `sim/` would be a module that
    only works on the fake house.

    The `openhouse` direction is deliberately *not* restated here. One import
    would then be reported twice, under two check names, which reads as two
    faults and buries the second diagnostic that is genuinely different. The
    two checks together are the rule, and each owns one direction of it.

    A guarded import and one under `TYPE_CHECKING` are caught like any other,
    because the scan reads the same AST `check_engine_purity` reads rather than
    the module's executed globals: `sim` is first-party, so nothing else in this
    package's scans would notice it at all.
    """
    directory = paths.ROOT / "engine"
    if not directory.is_dir():
        return
    for path in sorted(_python_files(directory)):
        imported = _imported_top_level(path)
        for package in ENGINE_LAYERING_FORBIDDEN:
            if package not in imported:
                continue
            report.add(
                ENGINE_LAYERING_CHECK,
                path.relative_to(paths.ROOT).as_posix(),
                f"imports `{package}`; the engine's only outward edge is the "
                "`HouseAdapter` port, so a module that reaches into the "
                "simulator is a module that only runs against the fake house",
            )


def check_registry_boundary(report: Report) -> None:
    """No committed artifact file names or links an external registry index.

    Scoped to machine-read artifacts, and enumerated from git rather than from
    the working tree. The requirement says *committed* files, and the difference
    is not academic: a walk of the tree reads a virtualenv and a built panel as
    though they were the project, which is slow, produces machine-specific
    results, and enforces something other than what the requirement states.

    Only structured formats are read. A pointer file is by nature a config file
    that something loads, so a Markdown document that *describes* the keys is not
    one -- and neither is Python source, which is why the scope is an allowlist
    of structured suffixes rather than a denylist of prose. The requirement's own
    text and this package both name all three keys.
    """
    for relative in sorted(vcs.listed_files()):
        parts = relative.split("/")
        if parts[0] in _OUT_OF_SCOPE_ROOTS:
            continue
        if "__pycache__" in parts:
            continue

        # The name is decisive before the content is read: a file called
        # `*.registry.json` is a registry index whatever it holds.
        if REGISTRY_FILENAME.match(parts[-1]):
            report.add(
                REGISTRY_CHECK,
                relative,
                "filename matches `*.registry.json`; the pack registry is a "
                "separate repository and no pointer to it belongs here in Phase 0",
            )
            continue

        if not is_artifact_name(parts[-1]):
            continue

        found = _registry_keys_in(paths.ROOT / relative)
        if found:
            report.add(
                REGISTRY_CHECK,
                relative,
                f"carries the registry pointer key(s) {sorted(found)}; the pack "
                "registry is deferred to Phase 7 and no pointer may accrete here",
            )


def check_version_control(report: Report) -> None:
    """The project is a git repository, and the clones are not part of it.

    Two clauses, and the second is the one with teeth. History is what the
    schema-immutability check reads; without it that check has nothing to compare
    a published version against and would pass on a tree it never read, which is
    why its absence is a failure here rather than a skip. And the four reference
    clones are separate repositories with their own history and their own
    licences -- reading them as input is the whole method, and committing them
    would put four more `.git` trees inside this one while relicensing nothing.
    """
    if not vcs.is_repository():
        report.add(
            VERSION_CONTROL_CHECK,
            paths.ROOT.as_posix(),
            "the project root has no git history; every history-based check "
            "would pass on a tree it cannot read",
        )
        return

    for relative in sorted(vcs.tracked_files()):
        if relative.split("/")[0] == "ressources":
            report.add(
                VERSION_CONTROL_CHECK,
                relative,
                "tracked in this repository; the reference clones are separate "
                "repositories and are read as input only",
            )


def _registry_keys_in(path: Path) -> set[str]:
    """Registry pointer keys present in an artifact file.

    Read as text rather than parsed, because the point is to catch a pointer
    wherever it appears -- a JSON key, a YAML key, or a value in a file whose
    format this check does not know. The key set is explicit rather than "a
    URL" precisely so that legitimate `repo:` entries in
    `.pre-commit-config.yaml` and the `registry` field of `package.json` do not
    trip it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return set()
    return {key for key in REGISTRY_KEYS if re.search(rf"\b{key}\b", text)}


def check_declared_dependency_names(report: Report) -> None:
    """`package.json` and `.pre-commit-config.yaml` must not be seen as pointers.

    Rather than a separate check, this is the assertion the spec states as a
    scenario: the registry recogniser is narrow enough that this project's own
    ordinary tooling passes it. It runs the recogniser over those two files and
    reports if either is flagged, so the scenario is enforced rather than
    assumed.
    """
    for relative in (".pre-commit-config.yaml", "package.json"):
        path = paths.ROOT / relative
        if not path.is_file():
            continue
        if _registry_keys_in(path):
            report.add(
                REGISTRY_CHECK,
                relative,
                "ordinary tooling was mistaken for a registry pointer; the "
                "recogniser is too broad",
            )
