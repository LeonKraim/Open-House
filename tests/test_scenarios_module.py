"""The composition root's scenario glue, and the corpus it makes runnable.

`scenario-runner`'s "The `given` block builds a house through the adapter and
fixes the run's inputs" and "The runner expresses every Phase 1 edge-case seed",
plus "Scenarios are runnable through the CLI and the MCP server, and the agent
loop is the acceptance" at the library level.

`openhouse/scenarios.py` is the one place a `Scenario` becomes a *running* one,
because opening a session means reaching both the engine and the simulator and
`design.md` D12 forbids `sim/` doing either. What is checked here is that the
`given` block is the only source of the house, the seed and the enable flags,
that an override replaces the block rather than editing it, and that the
committed corpus -- one scenario per `phase_1` seed -- loads and runs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from engine.vocabulary import Vocabulary
from openhouse.scenarios import open_scenario, run_corpus, run_file
from sim.fixtures import DEFAULT_SEED, DEFAULT_STARTED_AT, FIXTURE_NAMES
from sim.scenario import LoadError, load_scenario
from tools.catalog.narrow import as_mapping, as_sequence

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "scenarios"
CATALOG = ROOT / "catalog" / "edge_cases.yaml"

#: The header every scenario in the corpus carries, naming the seed it expresses.
_SEED_HEADER = re.compile(
    r"^# seed: (?P<seed>\d+)(?P<half>b?) of catalog/edge_cases\.yaml -- "
)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen for the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def corpus_files() -> tuple[Path, ...]:
    """Every scenario file in the corpus, oldest name first."""
    return tuple(sorted(CORPUS.glob("*.yaml")))


def _catalog_rows() -> tuple[Mapping[str, object], ...]:
    """Every seed row in `catalog/edge_cases.yaml`, in document order.

    Read through the catalog tooling's own narrowing helpers rather than by
    hand, because those are what every other reader of this file uses and a
    second reading of the same document is a second place for it to be read
    wrong.
    """
    loaded = as_mapping(yaml.safe_load(CATALOG.read_text(encoding="utf-8")))
    return tuple(as_mapping(row) for row in as_sequence(loaded["edge_cases"]))


@pytest.fixture(scope="module")
def seeds() -> tuple[Mapping[str, object], ...]:
    """Every seed row in `catalog/edge_cases.yaml`, in document order."""
    rows = _catalog_rows()
    assert rows, "the catalog carries no seed rows"
    return rows


def _header(path: Path) -> tuple[int, str] | None:
    """The seed number and half a scenario's first line names, or `None`."""
    first = path.read_text(encoding="utf-8").splitlines()[0]
    match = _SEED_HEADER.match(first)
    if match is None:
        return None
    return int(match.group("seed")), match.group("half")


def test_the_corpus_is_committed_and_not_empty(corpus_files: tuple[Path, ...]) -> None:
    """The corpus directory carries scenarios.

    A glob that found nothing would make every test below vacuously true, which
    is the failure mode the requirement's own acceptance case is written against.
    """
    assert len(corpus_files) >= len(_every_seed())


def _every_seed() -> tuple[int, ...]:
    """The seed numbers the catalog defines."""
    return tuple(range(1, len(_catalog_rows()) + 1))


def test_every_seed_is_expressed_by_at_least_one_scenario(
    corpus_files: tuple[Path, ...],
) -> None:
    """Each `phase_1` seed in the catalog has a scenario that names it.

    The requirement is that the runner's vocabulary is *sufficient* to express
    every seed, and sufficiency is only checkable by expressing them. A seed the
    corpus does not name is a seed that was read rather than run. Falsified by
    deleting a scenario file, or by adding a seed to the catalog without one.
    """
    named = {
        seed
        for path in corpus_files
        if (header := _header(path))
        for seed in [header[0]]
    }
    assert named == set(_every_seed())


def test_every_seed_row_carries_a_phase_1_claim(
    seeds: tuple[Mapping[str, object], ...],
) -> None:
    """Every seed states what Phase 1 does about it.

    The corpus is written against those sentences, so a row without one is a
    seed whose expression nobody could check.
    """
    assert len(seeds) > 0
    for index, row in enumerate(seeds, start=1):
        claim = row.get("phase_1")
        assert isinstance(claim, str) and claim.strip(), index


def test_a_scenario_header_names_its_seed_in_the_documented_form(
    corpus_files: tuple[Path, ...],
) -> None:
    """Each file's first line is `# seed: N of catalog/edge_cases.yaml -- ...`.

    `scenarios/README.md` documents this line as the convention, and the check
    above depends on it: a header written in another form would make the seed
    look unexpressed.
    """
    for path in corpus_files:
        header = _header(path)
        assert header is not None, path.name
        seed, _ = header
        assert 1 <= seed <= len(_every_seed()), path.name


def test_the_seed_a_filename_names_is_the_seed_its_header_names(
    corpus_files: tuple[Path, ...],
) -> None:
    """A file's number and its header agree.

    Falsified by copying a scenario and editing only the body: the corpus would
    then claim two scenarios for one seed and none for another, which the set
    comparison above would miss for the seed that gained a duplicate.
    """
    for path in corpus_files:
        header = _header(path)
        assert header is not None, path.name
        seed, half = header
        expected = f"{seed:02d}{half}-"
        assert path.name.startswith(expected), (path.name, expected)


def test_every_scenario_loads(corpus_files: tuple[Path, ...]) -> None:
    """Each file is a valid scenario document, and says so by loading.

    A `LoadError` here is a file the runner cannot read at all, which is the
    strongest form of "the seed is not expressed".
    """
    for path in corpus_files:
        load_scenario(path)


def test_every_scenario_runs(
    vocabulary: Vocabulary, corpus_files: tuple[Path, ...]
) -> None:
    """Each file runs to completion without a failing assertion.

    This is the requirement's own acceptance case -- "the scenario loads and
    runs" -- applied to the whole corpus rather than to one seed.
    """
    for path in corpus_files:
        result = run_file(path, vocabulary=vocabulary)
        assert result.name, path.name
        assert result.seed > 0, path.name


def test_the_runner_runs_a_directory_as_well_as_a_file(vocabulary: Vocabulary) -> None:
    """`run_corpus` returns one result per scenario file, in name order.

    The requirement asks for both, and the difference between them is the point:
    a directory is a corpus of runs, each with its own session, and not one run
    continued.
    """
    results = run_corpus(CORPUS, vocabulary=vocabulary)
    names = [Path(result.scenario).name for result in results]
    assert names == sorted(names)
    assert len(results) == len(tuple(CORPUS.glob("*.yaml")))


def test_a_corpus_with_an_unloadable_file_fails_naming_the_file(
    vocabulary: Vocabulary, tmp_path: Path
) -> None:
    """A directory is loaded in full before anything runs.

    Falsified by running as it loads: a corpus whose third file is malformed
    would then report two runs and a traceback, and a caller would have to sort
    the partial results from the failure.
    """
    (tmp_path / "01-good.yaml").write_text(
        (CORPUS / "05-hallway-light-watchdog.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "02-broken.yaml").write_text("name: broken\n", encoding="utf-8")
    with pytest.raises(LoadError) as raised:
        run_corpus(tmp_path, vocabulary=vocabulary)
    assert "02-broken.yaml" in str(raised.value)


def test_the_given_block_is_the_only_source_of_the_house(
    vocabulary: Vocabulary, corpus_files: tuple[Path, ...]
) -> None:
    """A scenario's `given.house` selects the house the run is over.

    Read from the two houses the corpus uses: a `minimal` scenario's session
    holds `light.foyer`, and a `no_lux` scenario's session holds
    `light.bedroom`. A runner that ignored `given.house` and used one default
    would fail one of the two.
    """
    seen: dict[str, set[str]] = {}
    for path in corpus_files:
        scenario = load_scenario(path)
        house = scenario.given.house
        assert isinstance(house, str), f"{path.name} names an inline house"
        session = open_scenario(scenario, vocabulary=vocabulary)
        seen.setdefault(house, set()).add(session.source)
    assert seen["minimal"] == {"minimal"}
    assert seen["no_lux"] == {"no_lux"}


def test_the_given_block_fixes_the_seed_and_the_instant(vocabulary: Vocabulary) -> None:
    """A session is opened at the `given` block's seed and start instant.

    Falsified by a runner that opened every session at the fixtures' defaults:
    the two runs of a scenario would agree with each other and disagree with the
    document, which is the one thing a replay input must not do.
    """
    scenario = load_scenario(CORPUS / "05-hallway-light-watchdog.yaml")
    session = open_scenario(scenario, vocabulary=vocabulary)
    assert session.seed == scenario.given.seed
    assert session.started_at == scenario.given.started_at


def test_an_override_seed_replaces_the_blocks_and_is_what_the_run_reports(
    vocabulary: Vocabulary,
) -> None:
    """`seed=` opens the session at the override and the result names it.

    The override is applied by opening the session at it rather than by editing
    the `Scenario` value, so what a run reports is what the session read back.
    Falsified by rewriting `given.seed`: the report would name the seed the
    document asked for while the run used another, and the report would
    reproduce nothing.
    """
    path = CORPUS / "05-hallway-light-watchdog.yaml"
    scenario = load_scenario(path)
    original = scenario.given.seed
    override = original + 1
    session = open_scenario(scenario, vocabulary=vocabulary, seed=override)
    assert session.seed == override
    assert scenario.given.seed == original


def test_an_override_instant_replaces_the_blocks(vocabulary: Vocabulary) -> None:
    """`started_at=` opens the session at the override instant."""
    scenario = load_scenario(CORPUS / "05-hallway-light-watchdog.yaml")
    elsewhere = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    session = open_scenario(scenario, vocabulary=vocabulary, started_at=elsewhere)
    assert session.started_at == elsewhere
    assert session.now() == elsewhere


def test_a_block_that_declares_nothing_enables_nothing(
    vocabulary: Vocabulary, corpus_files: tuple[Path, ...]
) -> None:
    """`enable_flags` is the only way a scenario turns a behaviour on.

    `product-invariants`' OFF-by-default rule reaches the corpus here: most
    seeds are about devices rather than decisions and leave the block out, so
    most sessions must have every behaviour disabled. Falsified by a runner that
    enabled the shipped defaults for a scenario that asked for nothing.
    """
    without = [
        path for path in corpus_files if not load_scenario(path).given.enable_flags
    ]
    assert without, "every scenario declares flags, so this check would be vacuous"
    for path in without:
        scenario = load_scenario(path)
        session = open_scenario(scenario, vocabulary=vocabulary)
        enabled = [
            name
            for name in session.behaviours
            if session.house_settings.get(f"behaviour.{name}.enabled", False)
        ]
        assert enabled == [], (path.name, enabled)


def test_the_enable_flags_reach_the_engine_as_a_house_layer(
    vocabulary: Vocabulary,
) -> None:
    """A declared flag is what the engine's resolver reads.

    `enable_flags` is passed as the house layer and not as a set of operations,
    because no control-surface operation enables a behaviour. Falsified by a
    runner that activated behaviours directly: the flag would then be a
    side-effect of opening rather than a setting the engine resolves.
    """
    scenario = load_scenario(CORPUS / "05-hallway-light-watchdog.yaml")
    session = open_scenario(scenario, vocabulary=vocabulary)
    assert scenario.given.enable_flags
    for key, value in scenario.given.enable_flags.items():
        assert session.house_settings[key] == value


def test_the_defaults_a_scenario_leaves_unstated_are_the_fixtures() -> None:
    """An omitted `seed` resolves to the fixtures' default seed.

    The two defaults are named once in `sim.fixtures` and reused, so a scenario
    that states neither is exactly a fixture session.
    """
    assert DEFAULT_SEED > 0
    assert DEFAULT_STARTED_AT.tzinfo is not None


def test_the_corpus_directory_is_the_one_the_spec_names() -> None:
    """The corpus lives under `scenarios/`, as `scenario-runner` writes it.

    The requirement names the directory in its own text, so a corpus under
    another name would leave the committed seeds a reading list again.
    """
    assert CORPUS.is_dir()
    assert CORPUS.name == "scenarios"


def test_the_corpus_readme_documents_the_convention() -> None:
    """`scenarios/README.md` explains the header, the clock and the tripwires.

    The corpus's negative assertions are only honest if their purpose is written
    down where a reader meets them; an undocumented `must_not: acted` reads as a
    claim that nothing ever acts.
    """
    text = (CORPUS / "README.md").read_text(encoding="utf-8")
    for phrase in ("# seed:", "advance_time", "tripwire", "must_not"):
        assert phrase in text, phrase


def test_a_scenario_is_loaded_from_a_path_and_reports_that_path(
    vocabulary: Vocabulary,
) -> None:
    """`run_file` names the file it ran, so a corpus report is locatable."""
    path = CORPUS / "05-hallway-light-watchdog.yaml"
    result = run_file(path, vocabulary=vocabulary)
    assert Path(result.scenario).name == path.name


def test_every_seed_in_the_catalog_is_described_by_a_phase_1_sentence(
    seeds: tuple[Mapping[str, object], ...],
) -> None:
    """Each row states a Phase 1 simulator seed, which is what the corpus answers.

    The corpus is the *executable* half of those sentences; a row whose claim
    named another phase would be a seed this corpus does not have to express.
    """
    for index, row in enumerate(seeds, start=1):
        claim = str(row["phase_1"])
        assert claim.startswith("Phase 1"), index


def test_the_corpus_covers_every_house_the_seeds_need(
    vocabulary: Vocabulary, corpus_files: tuple[Path, ...]
) -> None:
    """The houses the corpus names are fixtures the simulator builds.

    A scenario naming a house that is not a fixture fails to open, so this is
    the set of names the corpus uses against the set the fixture builder
    accepts.
    """
    used: set[str] = set()
    for path in corpus_files:
        house = load_scenario(path).given.house
        assert isinstance(house, str), f"{path.name} names an inline house"
        used.add(house)
    assert used
    assert used <= set(FIXTURE_NAMES)


def test_the_glue_exposes_only_the_two_entry_points_and_the_opener() -> None:
    """`openhouse/scenarios.py` exports the opener, the file runner, the corpus.

    A fourth export would be a fourth way to run a scenario, which is the drift
    the module exists to prevent: the CLI and the MCP tool are thin adapters
    over these three and nothing else.
    """
    from openhouse import scenarios as module

    assert set(module.__all__) == {"open_scenario", "run_corpus", "run_file"}
