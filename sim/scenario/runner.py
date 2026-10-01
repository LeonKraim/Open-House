"""Running a scenario against a session, and returning what the run was.

`run_scenario(scenario, surface)` executes the `when` block in order, taking each
assertion's read of the house and the log after the last step, and returns a
`RunResult` carrying the run's replay inputs (the seed and the starting instant),
its final virtual instant, its final snapshot and its decision-log window. A
failure raises `ScenarioFailed` with a `FailureReport` rather than returning a
half-filled result: a run that did not pass is not a run with a field set.

Two properties decide the shape of everything here. **The runner is a client**:
every step is a control-surface call (`dsl.compile_step`) and every assertion
reads the house or the log, so nothing in this package derives what the engine
*should* have decided (`scenario-runner`). **The runner reproduces**: the run's
inputs are the session's -- the seed and the start instant the session was opened
at -- and they are read back off the session for the result rather than repeated
from the scenario, so a result cannot claim inputs the run did not use.

A session is *given* to the runner rather than opened by it, and that is the one
seam `scenario-runner` does not spell out because `design.md` D12 settles it
instead: opening a session means choosing a repository root and reaching both the
engine and the simulator, and `sim/` may do neither. So `run_scenario` takes the
surface, and the composition root opens it from the scenario's `given`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from tools.netguard import no_sockets

from .assertions import LogExpectation, Mismatch, StateExpectation
from .dsl import compile_step
from .loader import load_directory
from .model import Scenario
from .report import FailureReport, ScenarioFailed, plain

if TYPE_CHECKING:
    from pathlib import Path

    from .surface import DecisionRecordLike, ScenarioSurface

__all__ = ["RunResult", "SessionFactory", "run_directory", "run_scenario"]

#: How a caller hands the runner a session it opened: a callable, because an
#: invariant has to be exercised at several seeds in one property, and because a
#: factory that is called twice is how "the same inputs give the same run" is
#: expressed without the runner owning the inputs.
SessionFactory = Callable[[], "ScenarioSurface"]


@dataclass(frozen=True, slots=True)
class RunResult:
    """What a passing run was: its inputs, its end state, and its log.

    Every field is a document or a scalar, so two runs compare equal when they
    decided the same thing -- which is the equality `deterministic_replay` is
    made of. `log` is the run's whole retained window, oldest first, in the
    engine's own record form: the runner reshapes nothing, so what a scenario
    asserts against and what an agent reads are the same records.
    """

    scenario: str
    name: str
    seed: int
    started_at: str
    final_instant: str
    final_snapshot: Mapping[str, object]
    log: tuple[Mapping[str, object], ...]

    def to_document(self) -> dict[str, object]:
        """The result as one JSON-serialisable mapping, built deterministically."""
        return {
            "scenario": self.scenario,
            "name": self.name,
            "seed": self.seed,
            "started_at": self.started_at,
            "final_instant": self.final_instant,
            "final_snapshot": plain(self.final_snapshot),
            "log": [plain(record) for record in self.log],
        }

    def to_json(self) -> str:
        """The result as stable JSON text: same run, same string."""
        return json.dumps(self.to_document(), sort_keys=True)


def run_scenario(scenario: Scenario, surface: ScenarioSurface) -> RunResult:
    """Run `scenario` against `surface`, or raise `ScenarioFailed` with the report.

    The run executes under `tools.netguard.no_sockets`, which is `simulation`'s
    runtime half of the hermeticity rule: the import scan catches a networking
    module named at the top of a file and cannot catch a socket reached
    dynamically, so a run that opens one fails here, naming where it did.
    """
    with no_sockets():
        documents: dict[str, Mapping[str, object]] = {}
        # Where a failure's log slice starts: the length *before* the step about
        # to run, so the slice is the records that step wrote. Taken before rather
        # than after because the slice the requirement asks for is "the decision
        # records from the last step up to the failure" -- and for an assertion
        # failure, which happens once every step has run, the records after the
        # last step are none at all. A slice taken after each step would make the
        # report empty in exactly the case it is read most.
        mark = 0
        for index, step in enumerate(scenario.when):
            mark = len(_records(surface))
            try:
                call = compile_step(step)
                result = call.invoke(surface, documents)
            except Exception as error:
                raise ScenarioFailed(
                    _step_failure(scenario, surface, index, step.verb, error, mark)
                ) from error
            if isinstance(result, Mapping):
                documents[call.save or f"step_{index}"] = cast(
                    "Mapping[str, object]", result
                )

        records = _documents(surface)
        for index, expectation in enumerate(scenario.then.expectations):
            mismatch = _evaluate(expectation, surface, records)
            if mismatch is None:
                continue
            raise ScenarioFailed(
                FailureReport(
                    scenario=scenario.path,
                    seed=surface.seed,
                    started_at=surface.started_at.isoformat(),
                    instant=surface.now().isoformat(),
                    expectation=mismatch.expectation,
                    expected=mismatch.expected,
                    actual=mismatch.actual,
                    log_slice=records[mark:],
                    assertion=index,
                )
            )

        return RunResult(
            scenario=scenario.path,
            name=scenario.name,
            seed=surface.seed,
            started_at=surface.started_at.isoformat(),
            final_instant=surface.now().isoformat(),
            final_snapshot=surface.snapshot(),
            log=records,
        )


def run_directory(
    directory: str | Path, open_surface: Callable[[Scenario], ScenarioSurface]
) -> tuple[RunResult, ...]:
    """Run every scenario in `directory`, oldest name first.

    Everything is loaded before anything runs, so a corpus with a scenario that
    does not load fails as a load error naming the file rather than as a partial
    run whose results a caller has to sort out. Each scenario gets its own session
    from `open_surface`, because the `given` block is per-scenario: a directory is
    a corpus of runs and not one run continued.
    """
    return tuple(
        run_scenario(scenario, open_surface(scenario))
        for scenario in load_directory(directory)
    )


def _documents(surface: ScenarioSurface) -> tuple[Mapping[str, object], ...]:
    """The retained log, as records, in the engine's own document form."""
    return tuple(record.to_document() for record in surface.get_decision_log())


def _records(surface: ScenarioSurface) -> Sequence[DecisionRecordLike]:
    """The retained log, as the engine's own records. Read for a length only."""
    return surface.get_decision_log()


def _evaluate(
    expectation: object,
    surface: ScenarioSurface,
    records: Sequence[Mapping[str, object]],
) -> Mismatch | None:
    """One assertion's verdict, as `None` or the mismatch that says why not.

    A log assertion reads the records the engine wrote and nothing else. A state
    assertion reads the entity through the port's own read, and a read that fails
    -- an assertion naming an entity the house does not hold -- becomes a mismatch
    naming the port's reason rather than an exception through the runner: the
    assertion is what was wrong, and a scenario that fails should say so in the
    terms it wrote.
    """
    if isinstance(expectation, LogExpectation):
        return expectation.evaluate(records)
    state = cast("StateExpectation", expectation)
    # The port's failure is the port's to word: whatever it raises for an id it
    # does not hold is the reason, and catching everything here is what keeps a
    # scenario that named the wrong entity a mismatch rather than a traceback.
    try:
        entity = surface.read_entity(state.entity_id)
    except Exception as error:
        return Mismatch(state.describe(), state.expected, f"unreadable: {error}")
    return state.evaluate(entity)


def _step_failure(
    scenario: Scenario,
    surface: ScenarioSurface,
    index: int,
    verb: str,
    error: Exception,
    mark: int,
) -> FailureReport:
    """The report for a step that could not be performed.

    A step failure has no expected value the way an assertion does -- the
    expectation *is* that the step be performed -- so `expected` says that and
    `actual` carries the error's own words, which is what a reader needs to see
    and what no reshaping here could improve on.
    """
    return FailureReport(
        scenario=scenario.path,
        seed=surface.seed,
        started_at=surface.started_at.isoformat(),
        instant=surface.now().isoformat(),
        expectation=f"{scenario.when[index].describe()} to be performed",
        expected=f"the step {verb!r} to succeed",
        actual=f"{type(error).__name__}: {error}",
        log_slice=_documents(surface)[mark:],
        step=index,
        verb=verb,
    )
