"""Running a scenario: the replay inputs, the failure document, and the boundary.

Tasks 9.0, 9.1, 9.2 and 9.3, and the `scenario-runner` requirements "The `given`
block builds a house through the adapter and fixes the run's inputs", "A run is
deterministic and carries its own replay inputs", "A failure is a JSON document a
CI job can consume", "The runner is a client of the control surface, never a
second engine" and "A directory of scenarios is run".

Three properties shape the module. **The runner reproduces**: the same scenario
at the same seed and instant is run twice and the two `RunResult` documents are
compared as strings, so a runner that read the wall clock or opened a fresh stream
per call would be caught rather than trusted. **A failure is a document**: the
report's `step` and `assertion` indices locate the failure and at most one is set,
and the same failing scenario serialises byte-identically twice. **The runner is a
client**: a source scan proves no module under `sim/scenario/` imports an `engine/`
interior or a `sim/` module other than the fixtures the `given` block selects.

A falsifying implementation is one that reports a seed it did not use, that omits
the failed step or the assertion index, that runs a directory partially, or that
reaches the engine except through the control surface. Each test names it.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from engine.vocabulary import Vocabulary
from openhouse.scenarios import open_scenario, run_corpus, run_file
from sim.fixtures import DEFAULT_SEED, DEFAULT_STARTED_AT
from sim.scenario import (
    LoadError,
    RunResult,
    Scenario,
    ScenarioFailed,
    load_scenario,
    run_directory,
)

if TYPE_CHECKING:
    from openhouse.facade import OpenHouse
    from sim.scenario import FailureReport

ROOT = Path(__file__).resolve().parents[1]

#: The proven-green corpus exemplar: it asserts a light is off *because the
#: timeout acted*, so a run of it both passes and leaves a decision record.
_WATCHDOG_FIRES = ROOT / "scenarios" / "05b-hallway-light-watchdog-fires.yaml"

_PASSING = """\
given:
  house: minimal
when: []
then:
  - state:
      entity_id: light.foyer
      is: "off"
"""

#: The light is never switched on, so asserting it is on fails with actual "off".
_FAILING_STATE = """\
given:
  house: minimal
when:
  - advance_time:
      minutes: 1
then:
  - state:
      entity_id: light.foyer
      is: "on"
"""

#: The `restore` step names a document no step saved, so the step cannot be
#: performed and the assertion that follows is never evaluated.
_STEP_FAILURE = """\
given:
  house: minimal
when:
  - restore:
      document:
        from: missing
  - advance_time:
      minutes: 1
then:
  - state:
      entity_id: light.foyer
      is: "off"
"""

_GIVEN_BLOCK = """\
name: the pinned run
given:
  house: minimal
  seed: 7
  started_at: "2026-03-04T05:06:07+00:00"
  enable_flags:
    behaviour.motion_lighting.enabled: true
when: []
then:
  - state:
      entity_id: light.foyer
      is: "off"
"""

_DEFAULTS = """\
given:
  house: minimal
when: []
then:
  - availability:
      entity_id: light.foyer
      is: true
"""

_UNKNOWN_FIXTURE = """\
given:
  house: attic
when: []
then:
  - state:
      entity_id: light.foyer
      is: "off"
"""

_INLINE_HOUSE = """\
given:
  house:
    name: broken
when: []
then:
  - state:
      entity_id: light.foyer
      is: "off"
"""

#: A `because` citation over a house with every behaviour off: nothing ran, so no
#: record can satisfy it, where the same claim passes for `05b`.
_NEVER_RAN = """\
given:
  house: minimal
when:
  - advance_time:
      minutes: 6
then:
  - log:
      because:
        rule: lighting.motion_light_off
        outcome: acted
"""

_MISSING_WHEN = """\
given:
  house: minimal
then:
  - state:
      entity_id: light.foyer
      is: "off"
"""


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen, so one load serves the module."""
    return Vocabulary.load(ROOT)


def _write(tmp_path: Path, text: str, name: str = "scenario.yaml") -> Path:
    """A scenario document on disk, LF, no translation."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _run_failing(path: Path, vocabulary: Vocabulary) -> FailureReport:
    """Run `path` and return the failure report it raised, or fail the test."""
    with pytest.raises(ScenarioFailed) as info:
        run_file(path, vocabulary=vocabulary)
    return info.value.report


# --------------------------------------------------------------------------
# Determinism and the replay inputs
# --------------------------------------------------------------------------


def test_a_passing_run_replays_exactly(vocabulary: Vocabulary) -> None:
    """Two runs of one scenario at its seed and instant serialise identically.

    A falsifying implementation that read the wall clock, opened a fresh random
    stream per call, or carried a set or a hash-ordered mapping into the result
    would make the two `to_json()` strings differ. The log is asserted non-empty
    so the equality cannot hold by the run having done nothing at all.
    """
    first = run_file(_WATCHDOG_FIRES, vocabulary=vocabulary)
    second = run_file(_WATCHDOG_FIRES, vocabulary=vocabulary)
    assert isinstance(first, RunResult)
    assert first.log, "the run decided nothing, so equal results would prove nothing"
    assert first.to_json() == second.to_json()
    assert first.to_document() == second.to_document()


def test_a_passing_result_carries_its_replay_inputs(vocabulary: Vocabulary) -> None:
    """The result names the scenario, its seed, its start, its end and its snapshot.

    A falsifying implementation that read `started_at` back off the moving clock
    would report the *end* of the run as its input, and a failure built from it
    would replay nothing; one that omitted the final snapshot would make a
    resumed run impossible.
    """
    result = run_file(_WATCHDOG_FIRES, vocabulary=vocabulary)
    assert result.scenario.endswith("05b-hallway-light-watchdog-fires.yaml")
    assert result.name == "hallway light watchdog -- past the window"
    assert result.seed == 11
    assert result.started_at == "2026-01-01T23:00:00+00:00"
    assert datetime.fromisoformat(result.final_instant) > datetime.fromisoformat(
        result.started_at
    )
    assert isinstance(result.final_snapshot, Mapping)
    assert result.final_snapshot


# --------------------------------------------------------------------------
# The failure document
# --------------------------------------------------------------------------


def test_a_failed_assertion_fails_with_indexed_evidence(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A failed assertion sets `assertion`, leaves `step` unset, and serialises stably.

    A falsifying implementation that set both indices, or neither, would leave a
    CI job unable to say whether a step or an assertion failed; one that rendered
    the values as a sentence rather than as JSON would be a paragraph to
    interpret rather than an input to replay.
    """
    path = _write(tmp_path, _FAILING_STATE)
    first = _run_failing(path, vocabulary)
    second = _run_failing(path, vocabulary)

    assert first.step is None
    assert first.assertion == 0
    assert first.verb is None
    assert first.expectation
    assert first.expected == "on"
    assert first.actual == "off"
    assert "light.foyer" in first.expectation
    assert first.seed == DEFAULT_SEED
    assert first.to_json() == second.to_json()

    document = json.loads(first.to_json())
    assert set(document) == {
        "scenario",
        "seed",
        "started_at",
        "instant",
        "step",
        "verb",
        "assertion",
        "expectation",
        "expected",
        "actual",
        "log",
    }
    assert document["step"] is None
    assert document["assertion"] == 0


def test_a_step_that_cannot_be_performed_fails_before_any_assertion(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A raising step sets `step` and the verb, and leaves `assertion` unset.

    The assertion in this scenario would pass, so the run can only have failed on
    the step; a falsifying implementation that evaluated assertions anyway, or
    that reported the failure as an assertion, would attribute the fault to the
    wrong half of the document.
    """
    report = _run_failing(_write(tmp_path, _STEP_FAILURE), vocabulary)
    assert report.step == 0
    assert report.verb == "restore"
    assert report.assertion is None
    assert "to be performed" in report.expectation
    assert "missing" in str(report.actual)
    assert report.log_slice == ()


# --------------------------------------------------------------------------
# The `given` block and the composition root's binding
# --------------------------------------------------------------------------


def test_the_given_block_resolves_its_defaults(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """An omitted seed, instant and flag set resolve to the fixture's own defaults.

    A falsifying implementation that left them unset would make the `Scenario`
    value carry inputs a replay has to guess at, and a `RunResult` built from it
    would name a seed the run did not use.
    """
    scenario = load_scenario(_write(tmp_path, _DEFAULTS))
    assert scenario.given.seed == DEFAULT_SEED
    assert scenario.given.started_at == DEFAULT_STARTED_AT
    assert dict(scenario.given.enable_flags) == {}


def test_the_given_blocks_fixed_inputs_are_recorded(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A `given` block's seed, instant and enable flags are part of the `Scenario`.

    A falsifying implementation that dropped the enable flags would make a
    scenario's behaviour depend on the flags in effect when it was opened rather
    than on the document, which is exactly the input a replay cannot recover.
    """
    scenario = load_scenario(_write(tmp_path, _GIVEN_BLOCK))
    assert scenario.given.seed == 7
    assert scenario.given.started_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    assert scenario.given.enable_flags == {"behaviour.motion_lighting.enabled": True}


def test_open_scenario_binds_the_given_block(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A session opened from a scenario is fixed at the block's own inputs.

    A falsifying implementation that opened the fixture at its defaults instead
    of the block's seed would make two runs of one document diverge for a reason
    the document does not contain, and the enable flag would be set as a step
    rather than before the first one.
    """
    scenario = load_scenario(_write(tmp_path, _GIVEN_BLOCK))
    session = open_scenario(scenario, vocabulary=vocabulary)
    assert session.seed == 7
    assert session.started_at == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    assert session.house_settings["behaviour.motion_lighting.enabled"] is True
    assert session.house.name == "the minimal fixture"


def test_an_explicit_seed_override_wins_and_is_reported(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The override opens the session at the new seed, and the result names it.

    A falsifying implementation that rewrote the `Scenario`'s `given` would leave
    a failure report that reproduces nothing, and one that opened the session at
    the document's seed while reporting the override would name a seed the run
    did not use.
    """
    path = _write(tmp_path, _GIVEN_BLOCK)
    scenario = load_scenario(path)
    assert scenario.given.seed == 7
    assert run_file(path, vocabulary=vocabulary).seed == 7
    assert run_file(path, vocabulary=vocabulary, seed=99).seed == 99


# --------------------------------------------------------------------------
# The house is built before the first step, or not at all
# --------------------------------------------------------------------------


def test_an_unknown_fixture_fails_before_any_step_runs(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """A `given` block naming a fixture nothing provides fails at load, naming it.

    A falsifying implementation that deferred the fixture lookup to the first
    `read` would let a scenario with an empty `when` "pass" against a house that
    was never built.
    """
    with pytest.raises(LoadError) as info:
        run_file(_write(tmp_path, _UNKNOWN_FIXTURE), vocabulary=vocabulary)
    assert "attic" in str(info.value)
    assert info.value.location == "given/house"


def test_an_inline_house_that_fails_its_schema_is_refused(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """An inline house failing the house schema fails the load, naming where.

    A falsifying implementation that accepted any mapping as a house would let a
    scenario carry a house `House.from_document` later refuses, so the failure
    would arrive after the run had begun rather than before it.
    """
    with pytest.raises(LoadError) as info:
        load_scenario(_write(tmp_path, _INLINE_HOUSE))
    assert info.value.location == "given/house"


# --------------------------------------------------------------------------
# The log is the oracle, end to end
# --------------------------------------------------------------------------


def test_the_log_tells_acted_apart_from_never_ran(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """The same `because` citation passes where the timeout acted and fails where it did not.

    `05b` asserts the light is off *because the timeout acted* and is green, so an
    acted record exists; the same citation over a house with nothing enabled finds
    no such record and fails. A falsifying implementation that asserted on device
    state alone could not tell the two apart, which is `design.md` D2's whole
    point.
    """
    fired = run_file(_WATCHDOG_FIRES, vocabulary=vocabulary)
    assert fired.log, "the acted record this comparison turns on is absent"
    report = _run_failing(_write(tmp_path, _NEVER_RAN), vocabulary)
    assert report.assertion == 0
    assert "because" in report.expectation
    assert report.expected == {"rule": "lighting.motion_light_off", "outcome": "acted"}


# --------------------------------------------------------------------------
# A directory of scenarios
# --------------------------------------------------------------------------


def test_a_directory_is_loaded_whole_before_anything_runs(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """One bad file fails as a load error naming it, and no scenario is opened.

    The bad file sorts last, so a falsifying implementation that ran each
    scenario as it loaded it would have opened and run the first one before the
    load error arrived; the recording factory is what catches that.
    """
    _write(tmp_path, _PASSING, "01-good.yaml")
    _write(tmp_path, _MISSING_WHEN, "99-bad.yaml")
    opened: list[str] = []

    def factory(scenario: Scenario) -> OpenHouse:
        opened.append(scenario.path)
        return open_scenario(scenario, vocabulary=vocabulary)

    with pytest.raises(LoadError) as info:
        run_directory(tmp_path, factory)
    assert info.value.path.endswith("99-bad.yaml")
    assert opened == []


def test_a_directory_of_scenarios_runs_each_in_its_own_session(
    tmp_path: Path, vocabulary: Vocabulary
) -> None:
    """Every scenario in the directory is run, oldest name first, one result each.

    A falsifying implementation that ran only the first file, or that reused one
    session across the corpus, would return fewer results than files -- and a
    reused session would carry one scenario's clock and log into the next.
    """
    _write(tmp_path, _PASSING, "01-first.yaml")
    _write(tmp_path, _PASSING, "02-second.yaml")
    results = run_corpus(tmp_path, vocabulary=vocabulary)
    assert [result.name for result in results] == ["01-first", "02-second"]


# --------------------------------------------------------------------------
# The boundary: a client of the control surface
# --------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    """Every absolute module a file imports, `TYPE_CHECKING` blocks included.

    Relative imports are skipped: they stay inside the package, which is the
    thing the scan is checking has no door out of.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _package_modules() -> list[Path]:
    """Every module of the scenario package, so the scan cannot skip one."""
    return sorted((ROOT / "sim" / "scenario").glob("*.py"))


def test_no_scenario_module_imports_an_engine_interior() -> None:
    """No module under `sim/scenario/` imports `engine/`, however it is spelled.

    The runner is a client of the control surface and not a part of it; a module
    importing an `engine/` interior would test a path an agent never takes, which
    is the drift deriving the step verbs from the surface exists to remove. A
    falsifying implementation is a single `import engine...` anywhere in the
    package, and the scan reads every module rather than one.
    """
    modules = _package_modules()
    assert modules
    for path in modules:
        for module in _imports(path):
            assert module.split(".")[0] != "engine", f"{path.name} imports {module}"


def test_the_package_reaches_the_fixtures_and_no_composition_root() -> None:
    """The package's imports reach `sim.fixtures`, and none of them is `openhouse`.

    The requirement is a whitelist rather than only an engine ban: a `sim/`
    interior other than the fixtures the `given` block selects would be a second
    door to the engine that the engine-import check alone would not see, and the
    positive assertion is what keeps the whitelist from being satisfied by a
    package that reaches nothing.

    `openhouse` is refused whole rather than allowed down to the facade. The
    composition root is the one package `sim/` may not import, directly or under
    a guarded import (`design.md` D12; `check_composition_root_purity`), so an
    allowance for `openhouse.facade` here would be this test sanctioning exactly
    the violation that check exists to catch. What the runner needs from the
    facade is stated on this side instead, as `ScenarioSurface` in `.surface`.
    """
    reached: set[str] = set()
    for path in _package_modules():
        reached |= _imports(path)
    assert "sim.fixtures" in reached
    for module in reached:
        root = module.split(".")[0]
        if root == "sim":
            assert module == "sim.fixtures" or module.startswith("sim.scenario."), (
                module
            )
        assert root != "openhouse", (
            f"{module} is the composition root; `sim/` may not import it, directly "
            "or under a guarded import"
        )
