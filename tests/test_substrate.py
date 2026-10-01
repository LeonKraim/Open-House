"""The deterministic substrate's scans -- tasks 3.0, 3.1, 3.3 and 10.x.

Four checks hold the phase's promises: no wall clock, one random stream, no
network or Home Assistant, and no default drawn from the environment. The first
and the last reach `openhouse/` as well as the two substrate packages, because
`control-surface`'s determinism requirement names the surface for both -- a face
that consulted the clock or the environment would make a scenario's outcome
depend on when and where it ran. Every rejection is tested on a fixture we build,
because the committed tree is by construction the one that passes and a check
proven only against it has never been seen to fail; every rejection is paired
with the nearest legal construct that must survive it, because a scan that
rejected everything would pass every violating fixture here and enforce nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.catalog import substrate
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

WALL_CLOCK = substrate.WALL_CLOCK_CHECK
RANDOMNESS = substrate.RANDOMNESS_CHECK
HERMETICITY = substrate.HERMETICITY_CHECK
ENVIRONMENT = substrate.ENVIRONMENT_CHECK


def _report(check: Callable[[Report], None]) -> Report:
    report = Report()
    check(report)
    return report


def _wheres(report: Report, check: str) -> list[str]:
    return [d.where for d in report.diagnostics if d.check == check]


# --------------------------------------------------------------------------
# The committed tree
# --------------------------------------------------------------------------


def test_the_committed_tree_is_hermetic_and_deterministic(real_root: Path) -> None:
    """The committed tree passes all four scans over every package they read.

    Run on the real root, not a fixture, because a fixture that merely resembles
    the repository cannot tell us the repository is green.
    """
    report = Report()
    substrate.check_wall_clock(report)
    substrate.check_random_stream(report)
    substrate.check_hermeticity(report)
    substrate.check_environment(report)
    assert report.diagnostics == [], report.render()


# --------------------------------------------------------------------------
# The wall clock -- task 3.0
# --------------------------------------------------------------------------


def test_a_module_calling_time_time_is_rejected(fake_root: Path) -> None:
    """`time.time()` outside the clock module is a wall-clock read."""
    write(fake_root, "engine/decider.py", "import time\nstarted = time.time()\n")
    report = _report(substrate.check_wall_clock)
    assert _wheres(report, WALL_CLOCK) == ["engine/decider.py"]
    assert "time.time" in report.render()


def test_a_module_calling_datetime_now_is_rejected(fake_root: Path) -> None:
    write(
        fake_root,
        "sim/tick.py",
        "from datetime import datetime\nstamp = datetime.now()\n",
    )
    report = _report(substrate.check_wall_clock)
    assert _wheres(report, WALL_CLOCK) == ["sim/tick.py"]
    assert "datetime.now" in report.render()


def test_a_directly_imported_clock_function_is_rejected(fake_root: Path) -> None:
    """`from time import monotonic` is the same read by a shorter name.

    Falsified by a scan that only recognises `time.monotonic` spelled as an
    attribute: the direct import binds the function to a bare name and the call
    looks like an ordinary one.
    """
    write(fake_root, "sim/tick.py", "from time import monotonic\nvalue = monotonic()\n")
    assert "time.monotonic" in _report(substrate.check_wall_clock).render()


def test_an_aliased_clock_module_is_rejected(fake_root: Path) -> None:
    """`import time as clock` still reads the wall clock."""
    write(fake_root, "engine/tick.py", "import time as clock\nvalue = clock.time()\n")
    assert "time.time" in _report(substrate.check_wall_clock).render()


def test_a_bare_time_call_is_rejected(fake_root: Path) -> None:
    """`from time import time` binds the read to the module's own name.

    The reduction that turns `datetime.datetime.now` into `datetime.now` must not
    collapse this one: `time.time` is two equal segments but a module and its
    function, not a module and its class, and a scan that treated it as the
    latter would name nothing and let the commonest wall-clock read through.
    """
    write(fake_root, "engine/tick.py", "from time import time\nvalue = time()\n")
    assert "time.time" in _report(substrate.check_wall_clock).render()


def test_a_guarded_clock_read_is_still_rejected(fake_root: Path) -> None:
    """An import and a call inside `try:` are still an import and a call."""
    write(
        fake_root,
        "engine/tick.py",
        "import time\ntry:\n    value = time.time()\nexcept OSError:\n    value = 0.0\n",
    )
    assert _wheres(_report(substrate.check_wall_clock), WALL_CLOCK) == [
        "engine/tick.py"
    ]


def test_the_clock_module_may_read_the_wall_clock(fake_root: Path) -> None:
    """`sim/clock.py` is the one permitted reader, at construction.

    Falsified by a scan with no exemption, which would report the module the spec
    allows to seed a live run's start instant.
    """
    write(
        fake_root,
        "sim/clock.py",
        "from datetime import datetime\nvalue = datetime.now()\n",
    )
    assert _report(substrate.check_wall_clock).diagnostics == []


def test_constructing_a_datetime_is_not_a_clock_read(fake_root: Path) -> None:
    """`datetime(...)` names an instant; it does not read the clock.

    The admitting case for the check: a scan that failed on any use of `datetime`
    would reject a fixture that fixes its own timestamps.
    """
    write(
        fake_root,
        "engine/stamps.py",
        "import datetime\nepoch = datetime.datetime(2026, 1, 1)\n",
    )
    assert _report(substrate.check_wall_clock).diagnostics == []


# --------------------------------------------------------------------------
# The random stream -- task 3.1
# --------------------------------------------------------------------------


def test_a_module_importing_random_is_rejected(fake_root: Path) -> None:
    write(fake_root, "sim/tick.py", "import random\nvalue = random.random()\n")
    report = _report(substrate.check_random_stream)
    assert _wheres(report, RANDOMNESS) == ["sim/tick.py"]
    assert "random" in report.render()


def test_a_second_generator_is_rejected(fake_root: Path) -> None:
    """`from random import Random` seeds a generator the run does not own."""
    write(fake_root, "engine/tick.py", "from random import Random\ngen = Random(7)\n")
    assert _wheres(_report(substrate.check_random_stream), RANDOMNESS) == [
        "engine/tick.py"
    ]


def test_the_entropy_module_may_use_the_random_module(fake_root: Path) -> None:
    """`sim/entropy.py` owns the one stream, so it is the one importer allowed.

    Falsified by a scan with no exemption, which would report the module that
    *is* the stream it requires.
    """
    write(fake_root, "sim/entropy.py", "import random\nvalue = random.Random(1)\n")
    assert _report(substrate.check_random_stream).diagnostics == []


def test_a_module_using_no_randomness_is_allowed(fake_root: Path) -> None:
    write(fake_root, "engine/plain.py", "import json\nvalue = json.dumps({})\n")
    assert _report(substrate.check_random_stream).diagnostics == []


# --------------------------------------------------------------------------
# Hermeticity -- task 3.3
# --------------------------------------------------------------------------


def test_a_module_importing_socket_is_rejected(fake_root: Path) -> None:
    write(fake_root, "engine/transport.py", "import socket\n")
    report = _report(substrate.check_hermeticity)
    assert _wheres(report, HERMETICITY) == ["engine/transport.py"]
    assert "socket" in report.render()


def test_a_sim_module_importing_urllib_is_rejected(fake_root: Path) -> None:
    write(fake_root, "sim/fetch.py", "import urllib.request\n")
    assert _wheres(_report(substrate.check_hermeticity), HERMETICITY) == [
        "sim/fetch.py"
    ]


def test_a_guarded_networking_import_is_rejected(fake_root: Path) -> None:
    """The guard that hides a network import from a reader does not hide it here."""
    write(
        fake_root,
        "sim/fetch.py",
        "try:\n    import socket\nexcept ImportError:\n    socket = None\n",
    )
    assert _wheres(_report(substrate.check_hermeticity), HERMETICITY) == [
        "sim/fetch.py"
    ]


def test_a_sim_module_importing_homeassistant_is_rejected(fake_root: Path) -> None:
    """`sim/` runs with no Home Assistant, so it may not import it either.

    `engine/` is covered by the Phase 0 purity check; `sim/` is not, which is the
    half this check adds.
    """
    write(
        fake_root,
        "sim/state.py",
        "try:\n    import homeassistant\nexcept ImportError:\n    homeassistant = None\n",
    )
    report = _report(substrate.check_hermeticity)
    assert _wheres(report, HERMETICITY) == ["sim/state.py"]
    assert "homeassistant" in report.render()


def test_homeassistant_under_engine_is_left_to_the_purity_check(
    fake_root: Path,
) -> None:
    """The two scans must not both report `engine/`'s Home Assistant import.

    `invariants.check_engine_purity` owns that clause; this check naming it too
    would double every such finding, so the overlap is pinned here rather than
    left to chance.
    """
    write(fake_root, "engine/state.py", "import homeassistant\n")
    assert _report(substrate.check_hermeticity).diagnostics == []


def test_a_sim_module_importing_an_undeclared_package_is_rejected(
    fake_root: Path,
) -> None:
    """`numpy` is neither stdlib nor declared, and is not a networking module.

    Distinct from the socket fixture: this is the undeclared-import half of the
    `sim/` rule, which the network list would never reach.
    """
    write(fake_root, "sim/array.py", "import numpy\n")
    report = _report(substrate.check_hermeticity)
    assert _wheres(report, HERMETICITY) == ["sim/array.py"]
    assert "numpy" in report.render()


def test_a_sim_module_importing_a_declared_dependency_is_allowed(
    fake_root: Path,
) -> None:
    """The fixture declares `pyyaml`, so `import yaml` under `sim/` survives."""
    write(fake_root, "sim/config.py", "import yaml\nvalue = yaml.safe_load('')\n")
    assert _report(substrate.check_hermeticity).diagnostics == []


def test_a_sim_module_importing_stdlib_and_first_party_is_allowed(
    fake_root: Path,
) -> None:
    """The admitting case: `sim/` may import stdlib, `engine/` and itself."""
    write(
        fake_root,
        "sim/plain.py",
        "import json\nfrom engine import adapter\nfrom sim import clock\n",
    )
    assert _report(substrate.check_hermeticity).diagnostics == []


def test_a_non_python_file_is_not_scanned(fake_root: Path) -> None:
    """The scans read Python source; a note that mentions a socket is not code."""
    write(fake_root, "sim/notes.txt", "import socket\n")
    assert _report(substrate.check_hermeticity).diagnostics == []


# --------------------------------------------------------------------------
# The surface -- `control-surface`'s determinism requirement
# --------------------------------------------------------------------------


def test_an_openhouse_module_calling_the_wall_clock_is_rejected(
    fake_root: Path,
) -> None:
    """The requirement's own scenario: a file under `openhouse/` reads `time.time`.

    "THEN the determinism scan fails and names the file and the call" -- so both
    halves are asserted, not just that something was reported.
    """
    write(fake_root, "openhouse/facade.py", "import time\nstarted = time.time()\n")
    report = _report(substrate.check_wall_clock)
    assert _wheres(report, WALL_CLOCK) == ["openhouse/facade.py"]
    assert "time.time" in report.render()


def test_an_openhouse_module_calling_datetime_now_is_rejected(
    fake_root: Path,
) -> None:
    """The requirement names `datetime.now` beside `time.time`."""
    write(
        fake_root,
        "openhouse/operations.py",
        "from datetime import datetime\nstamp = datetime.now()\n",
    )
    assert _wheres(_report(substrate.check_wall_clock), WALL_CLOCK) == [
        "openhouse/operations.py"
    ]


def test_a_monotonic_read_under_openhouse_is_rejected(fake_root: Path) -> None:
    """The requirement names "a monotonic clock" as the third spelling."""
    write(
        fake_root,
        "openhouse/results.py",
        "import time\nelapsed = time.monotonic()\n",
    )
    assert "time.monotonic" in _report(substrate.check_wall_clock).render()


def test_the_clock_exemption_is_a_path_and_does_not_reach_the_surface(
    fake_root: Path,
) -> None:
    """`sim/clock.py` is exempt by path; a `clock.py` under `openhouse/` is not.

    Falsified by an exemption written as the file's *name* rather than its
    repository path: the surface may read the clock no more than the engine may,
    and the one module the spec allows to seed a live run's start instant is
    named exactly -- `sim/clock.py` and nothing else.
    """
    write(
        fake_root,
        "openhouse/clock.py",
        "from datetime import datetime\nvalue = datetime.now()\n",
    )
    assert _wheres(_report(substrate.check_wall_clock), WALL_CLOCK) == [
        "openhouse/clock.py"
    ]


def test_an_openhouse_module_reading_the_environment_is_rejected(
    fake_root: Path,
) -> None:
    """`os.environ[...]` is a second input the caller never passed.

    The admitting contrast is `test_constructing_a_datetime_is_not_a_clock_read`'s
    shape: a value the caller *did* pass is fine, and this is the one that came
    from outside.
    """
    write(
        fake_root,
        "openhouse/facade.py",
        'import os\nvalue = os.environ["OH_HOUSE"]\n',
    )
    report = _report(substrate.check_environment)
    assert _wheres(report, ENVIRONMENT) == ["openhouse/facade.py"]
    assert "os.environ" in report.render()


def test_an_environment_mapping_read_through_get_is_rejected(
    fake_root: Path,
) -> None:
    """`os.environ.get("X")` names an attribute, not a call.

    The reason the scan has two walks: a call-only walk sees `os.getenv` and is
    blind to the mapping, which is the commoner spelling of the two.
    """
    write(
        fake_root,
        "openhouse/packs.py",
        'import os\nvalue = os.environ.get("OH_PACKS")\n',
    )
    assert "os.environ" in _report(substrate.check_environment).render()


def test_a_directly_imported_getenv_is_rejected(fake_root: Path) -> None:
    """`from os import getenv` is the same read bound to a bare name."""
    write(
        fake_root,
        "openhouse/cli.py",
        'from os import getenv\nvalue = getenv("OH_LOG")\n',
    )
    assert "os.getenv" in _report(substrate.check_environment).render()


def test_a_directly_imported_environ_is_rejected(fake_root: Path) -> None:
    """`from os import environ` binds the mapping, and the read is no attribute."""
    write(
        fake_root,
        "openhouse/cli.py",
        'from os import environ\nvalue = environ["OH_LOG"]\n',
    )
    assert "os.environ" in _report(substrate.check_environment).render()


def test_a_guarded_environment_read_is_still_rejected(fake_root: Path) -> None:
    """The guard that makes the read look optional does not make it absent."""
    write(
        fake_root,
        "openhouse/facade.py",
        'import os\ntry:\n    value = os.environ["OH_HOUSE"]\n'
        "except KeyError:\n    value = None\n",
    )
    assert _wheres(_report(substrate.check_environment), ENVIRONMENT) == [
        "openhouse/facade.py"
    ]


def test_a_surface_module_reading_neither_clock_nor_environment_is_allowed(
    fake_root: Path,
) -> None:
    """The admitting case: the surface may read whatever the caller passed it."""
    write(
        fake_root,
        "openhouse/facade.py",
        "from datetime import datetime\n"
        "def stamp(instant: datetime) -> str:\n    return instant.isoformat()\n",
    )
    assert _report(substrate.check_wall_clock).diagnostics == []
    assert _report(substrate.check_environment).diagnostics == []


def test_the_environment_clause_is_not_widened_to_the_substrate_packages(
    fake_root: Path,
) -> None:
    """The environment clause is the surface's; this check does not scan `engine/`.

    The requirement's subject is the face an agent drives -- "the surface SHALL
    introduce no nondeterminism of its own" -- while `simulation` binds the two
    substrate packages to the network, the clock and the stream and names the
    environment nowhere. The boundary is pinned here so that widening it is a
    decision someone makes, rather than a side effect of appending a package to a
    tuple. It is a statement about this check's scope and not a claim that an
    environment read under `engine/` would be harmless.
    """
    write(
        fake_root,
        "engine/config.py",
        'import os\nvalue = os.environ["OH_HOME"]\n',
    )
    assert _report(substrate.check_environment).diagnostics == []


def test_a_dotted_import_does_not_hide_an_environment_read(fake_root: Path) -> None:
    """`import os.path` binds the name `os`, and the mapping is read through it.

    The binding is what the scan reads a name by, and Python binds the *root* of
    an imported chain: recording the whole chain under that root makes every
    later `os.something` resolve to a path three segments long, which neither
    walk matches. A file can therefore evade the scan by importing a submodule it
    does not use -- and only by accident, which is worse, because the evasion
    needs no intent.
    """
    write(
        fake_root,
        "openhouse/packs.py",
        'import os.path\nvalue = os.environ["OH_PACKS"]\n',
    )
    assert _wheres(_report(substrate.check_environment), ENVIRONMENT) == [
        "openhouse/packs.py"
    ]


def test_a_dotted_import_does_not_hide_an_environment_call(fake_root: Path) -> None:
    """The other walk, over the same binding: `os.getenv` beside `os.path`."""
    write(
        fake_root,
        "openhouse/cli.py",
        'import os.path\nvalue = os.getenv("OH_LOG")\n',
    )
    assert "os.getenv" in _report(substrate.check_environment).render()


def test_a_submodule_used_but_the_environment_untouched_is_allowed(
    fake_root: Path,
) -> None:
    """The admitting case for the binding fix: `os.path` is not the environment.

    A scan that resolved the root binding to the mapping rather than to the
    module would reject every file that splits a path, which is the failure mode
    that gets a check deleted rather than fixed.
    """
    write(
        fake_root,
        "openhouse/paths.py",
        'import os.path\nvalue = os.path.join("a", "b")\n',
    )
    assert _report(substrate.check_environment).diagnostics == []


def test_a_getattr_with_a_literal_name_is_read(fake_root: Path) -> None:
    """`getattr(os, "environ")["X"]` names the mapping with no attribute node.

    The reason `_dotted` reads a literal `getattr` as well as a chain: the name
    reaches the expression as a string, so no `Attribute` carries it and the
    walk that looks for attributes is blind to it.
    """
    write(
        fake_root,
        "openhouse/facade.py",
        'import os\nvalue = getattr(os, "environ")["OH_HOUSE"]\n',
    )
    assert "os.environ" in _report(substrate.check_environment).render()


def test_a_getattr_clock_read_is_rejected(fake_root: Path) -> None:
    """The same reading applies to the wall clock, which shares `_dotted`."""
    write(
        fake_root,
        "openhouse/operations.py",
        'import time\nvalue = getattr(time, "time")()\n',
    )
    assert "time.time" in _report(substrate.check_wall_clock).render()


def test_a_getattr_whose_name_is_computed_is_beyond_the_scan(fake_root: Path) -> None:
    """The boundary this check does not cross, pinned rather than left to be assumed.

    `getattr(os, name)` with a computed name is an environment read, and this is
    a scan of source text: the name is not in the text, so no scan of this kind
    sees it. The test asserts the miss so that the boundary is a fact somebody
    recorded rather than a hole somebody discovers -- the same reason the module
    docstring says it of the network rule, whose runtime half
    (`tools/netguard.no_sockets`) is the answer there and has no analogue here.
    """
    write(
        fake_root,
        "openhouse/facade.py",
        'import os\nname = "environ"\nvalue = getattr(os, name)["OH_HOUSE"]\n',
    )
    assert _report(substrate.check_environment).diagnostics == []
