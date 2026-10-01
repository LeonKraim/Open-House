"""The simulator's deterministic substrate, enforced over the committed tree.

Three checks holding the phase's no-network, no-wall-clock and one-stream
promises (`specs/simulation/spec.md`) over the two packages that must obey them,
`engine/` and `sim/`. Each is a scan rather than a review item because a promise
nothing runs is not a promise (`design.md` D3 and D14): a networking import, a
clock read and a second generator all enter a tree the same way -- invisibly --
and each removes the reproducibility the rest of the phase stands on.

The wall-clock scan reaches one package further than the other two, and the
asymmetry is the requirements' and not this module's. `simulation` binds
`engine/` and `sim/`; `control-surface` binds the face an agent drives and names
the same scan for it -- "a scan over `openhouse/` for a wall-clock read, the same
scan `simulation` runs over its two packages" -- because a surface that consulted
the clock would make a scenario's failure depend on when it ran. The network and
stream rules stay where they were: the composition root opens no socket of its
own and owns no stream, and a `sim/` import of the composition root is
`invariants`' direction rule, not this module's question. The environment read is
the same requirement's third clause, "no default drawn from the environment", and
it is scoped to `openhouse/` for the same reason the rest of that sentence is.

Every scan here is a scan of source text, so what it can see is bounded by what a
spelling puts in the text: an import, an attribute chain, a name an import bound,
and `getattr` with a literal attribute name are all read, and a name computed at
runtime is not. Where that boundary falls is stated at each helper rather than
left to a reader to discover, because a scan whose limits are unknown is one
nobody can tell is working.

Why this is a module of its own and not more of `invariants`. `invariants`
already owns the Home-Assistant and undeclared-dependency half of the purity
rule, for `engine/`. This module owns the halves that requirement does not reach:
the networking rule for both packages, the wall clock, the random stream, and the
whole purity rule (networking, Home Assistant and undeclared imports) for `sim/`,
which the Phase 0 scan never covered because the tree was empty. Keeping them
here leaves the `architecture-invariants` seam as it was and gives the simulation
capability one module for its three enforced promises.

The scan is over source text and the import graph, and it cannot see a socket
reached dynamically; `tools/netguard.no_sockets` is the runtime half that catches
that one at the moment it is opened (`specs/simulation/spec.md`, the hermeticity
requirement).
"""

from __future__ import annotations

import ast
import sys
from typing import TYPE_CHECKING, cast

from . import paths
from .errors import Report
from .invariants import declared_import_names

if TYPE_CHECKING:
    from pathlib import Path

WALL_CLOCK_CHECK = "wall-clock"
RANDOMNESS_CHECK = "randomness"
HERMETICITY_CHECK = "hermeticity"
ENVIRONMENT_CHECK = "environment"

#: The two packages the deterministic substrate binds. `engine/` and `sim/`
#: together are the whole of what must replay: nothing else in the repository
#: decides anything.
PACKAGES: tuple[str, ...] = ("engine", "sim")

#: The composition root. It is in the wall-clock and environment scans and in
#: neither of the other two, because `control-surface` names those two for it and
#: names nothing else -- the facade is the face an agent drives, and a face that
#: consulted the clock or the environment would make a run's outcome depend on
#: when and where it ran.
COMPOSITION_ROOT = "openhouse"

#: What the wall-clock scan reads. The two substrate packages plus the surface
#: (`control-surface`, the determinism requirement).
DETERMINISM_PACKAGES: tuple[str, ...] = (*PACKAGES, COMPOSITION_ROOT)

#: What the environment scan reads. The surface alone: the requirement is that
#: *the surface* draws no default from the environment, and the substrate
#: packages were never in question for it.
ENVIRONMENT_PACKAGES: tuple[str, ...] = (COMPOSITION_ROOT,)

#: The one module allowed to read the wall clock, and the one allowed to touch
#: the `random` module. The exceptions are per path so the rule cannot be widened
#: by a second module claiming to be a clock or a stream.
WALL_CLOCK_EXEMPT: frozenset[str] = frozenset({"sim/clock.py"})
RANDOM_EXEMPT: frozenset[str] = frozenset({"sim/entropy.py"})

#: The wall-clock calls the spec enumerates, as `(module, attribute)` pairs. Only
#: the reading functions are here; `time.sleep` and `datetime` construction are
#: not wall-clock reads and are deliberately absent, because a scan that failed on
#: them would reject working code for no reason.
WALL_CLOCK_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("time", "time"),
        ("time", "monotonic"),
        ("time", "perf_counter"),
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
    }
)

#: The environment *calls* the determinism requirement forbids: `os.getenv(...)`
#: and its siblings. Spelled as `(module, attribute)` pairs for the same reason
#: the clock's are -- `from os import getenv` and `os.getenv` are one read under
#: two spellings, and a scan that knew only the second would be a scan of one
#: coding style.
ENVIRONMENT_CALLS: frozenset[tuple[str, str]] = frozenset(
    {("os", "getenv"), ("os", "getenvb"), ("os", "putenv")}
)

#: The environment *mappings*, which are read as attributes rather than called.
#: A mapping is not a call and would be invisible to the call walk above, so the
#: two walks are separate; `os.environ.get("X")` is caught by this one at the
#: `os.environ` half. `putenv` is here as well as above because the requirement is
#: about a default the surface *takes* and a write is how one is put there for a
#: later read -- either way the run stops being a function of its inputs.
ENVIRONMENT_ATTRIBUTES: frozenset[str] = frozenset({"environ", "environb"})

#: Networking modules neither package may import. The spec names `socket`, `ssl`,
#: `asyncio`, `http`, `urllib`, `smtplib`, `requests` and `aiohttp`; the rest are
#: the same class of module in the same standard library, named so the rule is not
#: one character wider than the spec. `urllib` is broader than the rule strictly
#: needs -- `urllib.parse` is pure string handling and imports no socket -- but the
#: spec lists `urllib` by name, and narrowing it here would be this file quietly
#: disagreeing with the requirement it enforces.
NETWORK_MODULES: frozenset[str] = frozenset(
    {
        "aiohttp",
        "asyncio",
        "ftplib",
        "http",
        "imaplib",
        "poplib",
        "requests",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib",
        "xmlrpc",
    }
)

#: First-party packages, importable from anywhere. The same set the purity scan
#: admits, repeated rather than read from `invariants` because it is small, stable,
#: and used here for a different question (`sim/`'s undeclared-import half).
#: `openhouse` is in it for the same reason it is in the purity scan's set: the
#: question here is "is this module ours?", and it is. A `sim/` import of the
#: composition root is caught by `invariants`' direction rule, which names the
#: import and the file; reporting it here as an undeclared dependency would
#: explain it as the wrong kind of mistake.
FIRST_PARTY: frozenset[str] = frozenset(
    {"custom_components", "engine", "ha_adapter", "openhouse", "sim", "tools"}
)


def _python_files(package: str) -> list[Path]:
    """Every Python file under one package, `__pycache__` excluded."""
    root = paths.ROOT / package
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _relative(path: Path) -> str:
    return path.relative_to(paths.ROOT).as_posix()


def _tree(path: Path) -> ast.Module | None:
    """A file's parse tree, or `None` when it will not parse.

    A file that does not parse is another check's finding; this scan stays silent
    about it rather than reporting a second, misleading complaint.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def imported_top_level(tree: ast.Module) -> set[str]:
    """Top-level module names a file imports, guarded ones included.

    Parsed rather than grepped, and with no exemptions: an import inside `try:` or
    under `if TYPE_CHECKING:` is still an import, and both are exactly how a
    forbidden dependency enters a tree while looking absent to a reader.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            # A relative import has no top-level name and is first-party by
            # construction.
            and node.level == 0
            and node.module
        ):
            names.add(node.module.split(".")[0])
    return names


def _bindings(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    """Local name -> the dotted path it was bound to, for clock detection.

    `import time as t` binds `t`, `from time import time` binds `time` to the
    value `("time", "time")`, and both spellings have to resolve to the same
    forbidden call or the scan is a scan of one coding style.
    """
    bound: dict[str, tuple[str, ...]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = tuple(alias.name.split("."))
                # `import a.b` binds the *root* name, not the leaf: Python binds
                # `a`, and only an `as` clause names the chain itself. Storing the
                # whole chain under the root makes every later `a.x` resolve to
                # `("a", "b", "x")` and miss whatever is being looked for, which
                # is how `import os.path` followed by `os.environ["X"]` escaped
                # the environment scan.
                bound[alias.asname or parts[0]] = parts if alias.asname else (parts[0],)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = tuple(node.module.split("."))
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound[alias.asname or alias.name] = (*module, alias.name)
    return bound


def _literal_getattr_name(node: ast.AST) -> str | None:
    """The attribute name in `getattr(x, "name")`, or `None` for any other node.

    A literal second argument is what keeps this inside a scan of source text: it
    is the only spelling of `getattr` whose attribute name is written in the file
    at all, so it is the only one a scan can follow. `getattr(x, name)` with a
    computed name is a read this check does not see, and cannot.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) == 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _dotted(
    node: ast.expr, bound: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    """The dotted path an expression names, or `None` when it names nothing known.

    Three spellings reach the same path and all three are read: the attribute
    chain (`os.environ`), the bare name an import bound (`environ`), and
    `getattr` with a *literal* attribute name (`getattr(os, "environ")`), which
    is the attribute chain written so that no attribute node carries the name.
    """
    if isinstance(node, ast.Name):
        return bound.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, bound)
        return (*base, node.attr) if base is not None else None
    name = _literal_getattr_name(node)
    if name is not None:
        call = cast("ast.Call", node)
        base = _dotted(call.args[0], bound)
        return (*base, name) if base is not None else None
    return None


def _canonical(dotted: tuple[str, ...]) -> tuple[str, str] | None:
    """Reduce a dotted call target to `(module, attribute)`, or nothing.

    `datetime.datetime.now` and `datetime.now` are the same call spelled two ways
    depending on whether the class or the module was imported, so the repeated
    module name is collapsed -- but only when a third element follows it. The
    length check is load bearing and is not an optimisation: `time.time` is also
    two equal segments, and it is a module and its function, not a module and its
    class. Collapsing it would reduce the pair to a single element, name nothing,
    and let the commonest wall-clock read in the language past the scan.
    """
    if len(dotted) >= 3 and dotted[0] == dotted[1]:
        dotted = (dotted[0], *dotted[2:])
    return (dotted[0], dotted[1]) if len(dotted) == 2 else None


def wall_clock_calls(tree: ast.Module) -> list[str]:
    """The wall-clock functions one module calls, deduplicated and sorted."""
    bound = _bindings(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func, bound)
        if dotted is None:
            continue
        canonical = _canonical(dotted)
        if canonical in WALL_CLOCK_CALLS:
            found.add(".".join(canonical))
    return sorted(found)


def environment_reads(tree: ast.Module) -> list[str]:
    """The environment reads one module makes, deduplicated and sorted.

    Two walks rather than one, because the two ways to reach the environment are
    different node shapes: `os.getenv("X")` is a call, while `os.environ["X"]` and
    `os.environ.get("X")` name an attribute and are not calls at all. A call-only
    scan would miss the mapping, which is the commonest way to read one.
    """
    bound = _bindings(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func, bound)
            if dotted is not None:
                canonical = _canonical(dotted)
                if canonical in ENVIRONMENT_CALLS:
                    found.add(".".join(canonical))
        # Two walks, and the second is not an `elif` of the first: a `Name` as
        # well as an `Attribute`, because `from os import environ` binds the
        # mapping to a bare name and the read no longer looks like an attribute at
        # all -- and a literal `getattr`, which *is* a call node and would never be
        # reached by an `elif` once the first branch has had it.
        candidate: ast.expr | None = None
        if isinstance(node, (ast.Attribute, ast.Name)):
            candidate = node
        elif _literal_getattr_name(node) is not None:
            candidate = cast("ast.expr", node)
        if candidate is not None:
            dotted = _dotted(candidate, bound)
            if (
                dotted is not None
                and len(dotted) == 2
                and dotted[0] == "os"
                and dotted[1] in ENVIRONMENT_ATTRIBUTES
            ):
                found.add(".".join(dotted))
    return sorted(found)


def check_wall_clock(report: Report) -> None:
    """No module under `engine/`, `sim/` or `openhouse/` reads the wall clock.

    `sim/clock.py` is the one exception: the clock's own one read at
    construction, which the spec allows and the module documents. Every other
    read would make time pass without `advance_time` -- the property the whole
    deterministic substrate rests on -- and a read under `openhouse/` would make
    *the surface's* outcome depend on when it ran, which is the determinism
    requirement's own clause. The exemption is a path and not a package, so it
    cannot be widened by a second module calling itself a clock.
    """
    for package in DETERMINISM_PACKAGES:
        for path in _python_files(package):
            relative = _relative(path)
            if relative in WALL_CLOCK_EXEMPT:
                continue
            tree = _tree(path)
            if tree is None:
                continue
            for call in wall_clock_calls(tree):
                report.add(
                    WALL_CLOCK_CHECK,
                    relative,
                    f"calls `{call}`; time advances only through "
                    "`sim/clock.py`'s `VirtualClock.advance`, and no module outside "
                    "`sim/clock.py` may read the wall clock",
                )


def check_environment(report: Report) -> None:
    """No module under `openhouse/` draws a default from the environment.

    The determinism requirement's third clause: the surface reads time through
    the session's clock and randomness through the simulator's stream, and both
    are inputs the caller fixes -- so anything else it read from outside would be
    a second input the caller could not fix, and a replay would stop being a
    function of what was passed in. The requirement names the clock in its
    scenario and the environment in its sentence; this is the sentence's half.
    """
    for package in ENVIRONMENT_PACKAGES:
        for path in _python_files(package):
            relative = _relative(path)
            tree = _tree(path)
            if tree is None:
                continue
            for read in environment_reads(tree):
                report.add(
                    ENVIRONMENT_CHECK,
                    relative,
                    f"reads `{read}`; the surface draws no default from the "
                    "environment, so that a session's result is a function of the "
                    "inputs the caller passed rather than of where it ran",
                )


def check_random_stream(report: Report) -> None:
    """Only `sim/entropy.py` imports `random`, so there is exactly one stream.

    A second generator is the failure this catches: one imported anywhere else
    would consume from a sequence nothing else shares, so the seed would no longer
    determine the run and a replay would be exact only by luck.
    """
    for package in PACKAGES:
        for path in _python_files(package):
            relative = _relative(path)
            if relative in RANDOM_EXEMPT:
                continue
            tree = _tree(path)
            if tree is None:
                continue
            if "random" in imported_top_level(tree):
                report.add(
                    RANDOMNESS_CHECK,
                    relative,
                    "imports `random`; all randomness comes from the single stream "
                    "`sim/entropy.py`'s `RandomStream`, and a second generator "
                    "would take the seed out of control of the run",
                )


def _stdlib() -> frozenset[str]:
    return frozenset(sys.stdlib_module_names)


def check_hermeticity(report: Report) -> None:
    """Neither package reaches the network, Home Assistant or an undeclared import.

    Scoped so the two scans do not report the same line twice. `engine/`'s
    Home-Assistant and undeclared-import halves belong to
    `invariants.check_engine_purity`; this check adds the networking rule to
    `engine/`, and carries the whole rule -- networking, Home Assistant and
    undeclared -- for `sim/`, which no earlier scan reached. A `homeassistant`
    import under `engine/` is therefore left to the purity check and is not named
    here.
    """
    declared = declared_import_names()
    stdlib = _stdlib()
    for package in PACKAGES:
        for path in _python_files(package):
            relative = _relative(path)
            tree = _tree(path)
            if tree is None:
                continue
            for module in sorted(imported_top_level(tree)):
                if module in NETWORK_MODULES:
                    report.add(
                        HERMETICITY_CHECK,
                        relative,
                        f"imports the networking module `{module}`; the engine and "
                        "the simulator are hermetic, so neither may reach the "
                        "network and a scenario run opens no socket",
                    )
                elif package == "sim" and module == "homeassistant":
                    report.add(
                        HERMETICITY_CHECK,
                        relative,
                        "imports `homeassistant`; `sim/` runs with no Home "
                        "Assistant installed, and a guarded or TYPE_CHECKING "
                        "import is still an import",
                    )
                elif (
                    package == "sim"
                    and module not in FIRST_PARTY
                    and module not in declared
                    and module not in stdlib
                ):
                    report.add(
                        HERMETICITY_CHECK,
                        relative,
                        f"imports `{module}`, which is neither stdlib, first-party, "
                        "nor declared in the `dependencies` array of the root "
                        "pyproject.toml",
                    )
