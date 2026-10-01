"""Running scenarios, as the composition root: a session opened from a `given` block.

`sim/scenario/` is a client of the control surface and may not open a session --
opening one means choosing a repository root and reaching both the engine and the
simulator, and `design.md` D12 forbids `sim/` doing either. So the package that
*may* do both is the one that turns a loaded `Scenario` into a running one, and
this is it. `open_scenario` is `open_session` with the scenario's `given` block
bound to the arguments, and `run_scenario` / `run_corpus` are the two entry points
the CLI and the MCP tool are both thin adapters over (`scenario-runner`).

**The override is honest.** `--seed` and `--started-at` on the CLI are here as
`seed` and `started_at`, and an override is applied by opening the session at it
rather than by editing the `Scenario` value: what a `RunResult` reports is what the
session reads back, so an overridden run's report names the seed the run actually
used rather than the one the document asked for. The alternative -- rewriting the
scenario's `given` -- would leave a report that reproduced nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sim.scenario import load_scenario, run_directory, run_scenario

from .facade import open_session

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from engine.behaviours import Behaviour
    from engine.solar import Location
    from engine.vocabulary import Vocabulary
    from sim.scenario import RunResult, Scenario

    from .facade import OpenHouse

__all__ = [
    "open_scenario",
    "run_corpus",
    "run_file",
]


def open_scenario(
    scenario: Scenario,
    *,
    vocabulary: Vocabulary,
    seed: int | None = None,
    started_at: datetime | None = None,
    location: Location | None = None,
    modes: Sequence[Mapping[str, object]] | None = None,
    behaviours: Mapping[str, Behaviour] | None = None,
) -> OpenHouse:
    """Open the session `scenario`'s `given` block describes.

    The `given` block is the only source of the house, the seed, the instant and
    the enable flags; the three arguments beyond the vocabulary are overrides and
    are `None` when nothing overrides. `enable_flags` is passed as the *house
    layer* of the resolver and not as a set of operations, because no
    control-surface operation enables a behaviour -- activation is the
    composition root's act, and a scenario declares it before the first step the
    way an operator would (`product-invariants`' OFF-by-default rule is what this
    keeps true: a block that declares nothing enables nothing).
    """
    given = scenario.given
    return open_session(
        house=given.house,
        vocabulary=vocabulary,
        seed=given.seed if seed is None else seed,
        started_at=given.started_at if started_at is None else started_at,
        location=location,
        modes=modes,
        house_settings=given.enable_flags,
        behaviours=behaviours,
    )


def run_file(
    path: str | Path,
    *,
    vocabulary: Vocabulary,
    seed: int | None = None,
    started_at: datetime | None = None,
) -> RunResult:
    """Load the scenario at `path`, run it, and return what the run was."""
    scenario = load_scenario(path)
    return run_scenario(
        scenario,
        open_scenario(
            scenario, vocabulary=vocabulary, seed=seed, started_at=started_at
        ),
    )


def run_corpus(
    directory: str | Path,
    *,
    vocabulary: Vocabulary,
    seed: int | None = None,
    started_at: datetime | None = None,
) -> tuple[RunResult, ...]:
    """Run every scenario in `directory`, and fail naming the file that does not load.

    Each scenario gets its own session, because a `given` block is per-scenario
    and a corpus is a set of runs rather than one run continued. An override
    applies to all of them, which is what a caller replaying a whole corpus at one
    seed means by it.
    """
    return run_directory(
        directory,
        lambda scenario: open_scenario(
            scenario, vocabulary=vocabulary, seed=seed, started_at=started_at
        ),
    )
