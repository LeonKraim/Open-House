"""The CLI: a thin adapter over the registry, adding no behaviour of its own.

`control-surface`'s "The CLI is a thin adapter over the registry and adds no
behaviour of its own" -- task 10.2 -- plus the runner's own entry point on this
face (10.6) and the failure reporting a scenario needs (11.1 is the veto; here it
is only that a refusal reaches a shell as a non-zero exit with the operation's
name in it).

The adapter claim is checked twice and each check is a different claim. *Coverage*
is structural: the subcommand set is compared to `OPERATIONS`, and every
subcommand's parameters and help text to the descriptor's, so a subcommand that
had drifted from the registry -- a missing `--entity-id`, a defaulted parameter,
a second spelling -- fails without anything having to be tried. *Behaviour* is
comparative: the same call made through the CLI and through the library is
compared for equality, so a subcommand that quietly computed something would
produce a document the facade's own is not. Every test names the implementation
that would falsify it; a hand-written set of ten subcommands, a `--json` that
shaped the document instead of only its whitespace, or a bad argument that
reached the operation before it was refused, is what each is written against.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from engine.vocabulary import Vocabulary
from openhouse.cli import app
from openhouse.facade import open_session
from openhouse.operations import HOUSE_CONTROL_NAMES, OPERATION_NAMES, OPERATIONS
from openhouse.results import jsonable, result_document
from openhouse.scenarios import run_file

if TYPE_CHECKING:
    from typer.testing import Result

ROOT = Path(__file__).resolve().parents[1]

#: A scenario that passes, and one that does not, written out per test rather
#: than read from `scenarios/`: the two have to differ in exactly one line, and
#: a corpus read from the repository would change underneath this module each
#: time a scenario was edited. The shape is the loader's own -- `given`, `when`,
#: `then` -- and the house is a fixture so the module needs no house file.
PASSING_SCENARIO = """\
name: a passing scenario
given:
  house: minimal
  seed: 1
when:
  - set_state:
      entity_id: light.foyer
      state: "on"
then:
  - state:
      entity_id: light.foyer
      is: "on"
"""

FAILING_SCENARIO = PASSING_SCENARIO.replace('is: "on"', 'is: "off"').replace(
    "a passing scenario", "a failing scenario"
)


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The committed catalog, frozen for the module."""
    return Vocabulary.load(ROOT)


@pytest.fixture(scope="module")
def commands() -> dict[str, Any]:
    """The Typer application as Click sees it: name to command, groups included.

    Typed as `Any` rather than as `click.Group` because `get_command` declares a
    plain `Command`, which has no `.commands` -- the group is what it returns for
    a `Typer` with subcommands, and the annotation on the callable does not say
    so.
    """
    group: Any = get_command(app)
    return dict(group.commands)


@pytest.fixture
def runner() -> CliRunner:
    """A runner per test, so no invocation reads state another one left."""
    return CliRunner()


def _document(result: Result) -> Any:
    """The JSON document a successful invocation wrote to stdout."""
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def _scenario(tmp_path: Path, text: str, *, name: str = "scenario.yaml") -> Path:
    """Write one scenario into the test's own directory and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# -- The subcommands are the registry ----------------------------------------


def test_the_cli_exposes_one_subcommand_per_registry_operation(
    commands: dict[str, Any],
) -> None:
    """The command set is the registry's, plus the scenario group and no more.

    Falsified by a subcommand for anything outside `OPERATIONS` -- a hand-written
    extra, or the scenario entry point spelled as an eleventh operation -- and by
    a missing one, which would leave an operation unreachable from a shell.
    """
    assert set(commands) == {*OPERATION_NAMES, "scenario"}


def test_the_cli_does_not_expose_the_house_control_facility(
    commands: dict[str, Any],
) -> None:
    """The port's four control operations are not subcommands.

    Falsified by `add_entity`, `remove_entity`, `set_availability` or `restart`
    appearing as a subcommand: "exactly one subcommand per registry operation"
    would then be false, and "the registry" would mean two things at the surface
    an agent is told to read.
    """
    assert set(HOUSE_CONTROL_NAMES).isdisjoint(commands)
    assert "run_scenario" not in commands


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_a_subcommand_takes_the_descriptors_parameters_and_json(
    name: str, commands: dict[str, Any]
) -> None:
    """A subcommand's parameters *are* the descriptor's, plus `--json`.

    Falsified by a parameter that is optional here and required in the registry,
    or by a missing one: the registry is the single declaration, and a
    subcommand's arguments are it read through Typer rather than restated.
    """
    command = commands[name]
    assert {parameter.name: parameter.required for parameter in command.params} == {
        parameter.name: parameter.required for parameter in OPERATIONS[name].parameters
    } | {"json": False}


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_each_parameter_becomes_a_long_option_spelled_from_its_name(
    name: str, commands: dict[str, Any]
) -> None:
    """Every parameter is a long option, named and described by the registry.

    Falsified by a parameter that arrived positionally, by `entity_id` spelled
    `--entity_id` by a hand-written command, or by help text invented at the CLI:
    the description an operator reads would then be a second statement of what
    the parameter is.
    """
    command = commands[name]
    for parameter in OPERATIONS[name].parameters:
        option = next(item for item in command.params if item.name == parameter.name)
        assert option.opts == [f"--{parameter.name.replace('_', '-')}"]
        assert option.help == parameter.description


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_every_subcommand_answers_help_with_its_own_options(
    name: str, runner: CliRunner
) -> None:
    """`--help` exits zero for every operation, and prints that command's options.

    Falsified by a generated subcommand whose signature Typer could not render,
    or one whose help was shared between commands: the one thing an operator has
    to be able to do with a command they have never seen is ask it what it takes.
    """
    result = runner.invoke(app, [name, "--help"])
    assert result.exit_code == 0, result.stderr
    for parameter in OPERATIONS[name].parameters:
        assert f"--{parameter.name.replace('_', '-')}" in result.stdout
    assert "--json" in result.stdout


def test_the_scenario_group_takes_a_path_a_seed_and_json(
    commands: dict[str, Any],
) -> None:
    """`scenario run` is the runner's entry point: a path, an optional seed, `--json`.

    Falsified by the entry point taking a house or a window option: the house
    comes from each scenario's `given` block, and an entry point that also named
    one would be a second way to say what the run is.
    """
    run = commands["scenario"].commands["run"]
    assert {parameter.name for parameter in run.params} == {
        "path",
        "seed",
        "json_output",
    }
    seed = next(item for item in run.params if item.name == "json_output")
    assert seed.opts == ["--json"]


# -- Driving the CLI ---------------------------------------------------------


def test_a_read_reports_the_result_document(runner: CliRunner) -> None:
    """A read writes `{"operation": ..., "result": ...}` and exits zero.

    Falsified by a result written as anything but that one document -- a bare
    array, or the operation's name left out -- which would make a caller parse
    the operation out of what it asked for rather than read it off the answer.
    """
    assert _document(
        runner.invoke(app, ["--fixture", "minimal", "get_decision_log", "--json"])
    ) == result_document("get_decision_log", ())


def test_advancing_on_the_cli_is_what_the_library_does(
    runner: CliRunner, vocabulary: Vocabulary
) -> None:
    """The same advance through both faces returns the same records.

    Falsified by the CLI opening its session over anything but the house, seed
    and instant it was told -- a different default seed would show up here as a
    different set of records, which is the drift a second default produces.
    """
    library = open_session(house="minimal", vocabulary=vocabulary)
    expected = result_document("advance_time", library.advance_time(minutes=5))
    assert (
        _document(
            runner.invoke(
                app,
                ["--fixture", "minimal", "advance_time", "--minutes", "5", "--json"],
            )
        )
        == expected
    )


def test_a_write_is_what_the_library_does(
    runner: CliRunner, vocabulary: Vocabulary
) -> None:
    """A write through the CLI is the facade's own write, entity view and all.

    Falsified by a subcommand that shaped or filtered the result -- reporting
    only the state, say -- which would make the CLI's answer a different answer
    from the library's to the same call.
    """
    library = open_session(house="minimal", vocabulary=vocabulary)
    expected = result_document("set_state", library.set_state("light.foyer", "on"))
    assert (
        _document(
            runner.invoke(
                app,
                [
                    "--fixture",
                    "minimal",
                    "set_state",
                    "--entity-id",
                    "light.foyer",
                    "--state",
                    "on",
                    "--json",
                ],
            )
        )
        == expected
    )


def test_the_json_flag_shapes_the_output_and_not_the_document(
    runner: CliRunner,
) -> None:
    """`--json` changes the whitespace; both forms carry one same document.

    Falsified by a `--json` that dropped or added a field, or by the default
    form writing anything but the same object: two renderings of one result are
    allowed to differ in their indentation and in nothing else.
    """
    plain = runner.invoke(app, ["--fixture", "minimal", "get_decision_log"])
    as_json = runner.invoke(app, ["--fixture", "minimal", "get_decision_log", "--json"])
    assert plain.exit_code == as_json.exit_code == 0
    assert (
        json.loads(plain.stdout)
        == json.loads(as_json.stdout)
        == {
            "operation": "get_decision_log",
            "result": [],
        }
    )
    assert len(as_json.stdout.strip().splitlines()) == 1
    assert len(plain.stdout.strip().splitlines()) > 1


# -- Refusals ----------------------------------------------------------------


def test_a_missing_required_argument_is_refused_before_the_operation(
    runner: CliRunner,
) -> None:
    """A required parameter left out is refused, and the option is named.

    Falsified by a required option quietly defaulting -- the handler would then
    run with a value nobody gave it, which is the failure the registry's
    `required` exists to prevent.
    """
    result = runner.invoke(app, ["set_state", "--entity-id", "light.foyer"])
    assert result.exit_code != 0
    assert "Missing option '--state'" in result.stderr
    assert result.stdout == ""


def test_an_invalid_argument_is_refused_before_the_session_is_opened(
    tmp_path: Path, runner: CliRunner
) -> None:
    """A malformed argument never reaches the handler, and says which option.

    Falsified by a command that accepted the text and failed later, from inside
    the session: the `--root` here points at a directory no session can open, so
    an invocation that reached the handler is one whose message says `session`
    and this one's does not.
    """
    empty = tmp_path / "nothing"
    empty.mkdir()
    reached = runner.invoke(
        app, ["--root", str(empty), "advance_time", "--minutes", "5"]
    )
    assert reached.exit_code == 1
    assert reached.stderr.startswith("Error: session:")
    refused = runner.invoke(
        app, ["--root", str(empty), "advance_time", "--minutes", "abc"]
    )
    assert refused.exit_code == 2
    assert "Invalid value for '--minutes'" in refused.stderr
    assert "Error: session:" not in refused.stderr


def test_a_document_argument_that_is_not_json_names_the_operation(
    runner: CliRunner,
) -> None:
    """A document parameter arrives as text and is parsed, naming the operation.

    Falsified by a parse failure reported only as a traceback or as a failure of
    the session: the operation is what could not take the argument, and the
    message has to say so for a caller to know which one to fix.
    """
    result = runner.invoke(app, ["restore", "--document", "{"])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: restore:")
    assert "not JSON" in result.stderr


def test_a_fixture_and_a_house_file_together_are_refused(runner: CliRunner) -> None:
    """A session is opened over one house: naming two is refused.

    Falsified by a command that silently preferred one of the two -- the run
    would then be of a house the caller did not ask for, and nothing in the
    output would say so.
    """
    result = runner.invoke(
        app, ["--fixture", "minimal", "--house", "somewhere.yaml", "snapshot"]
    )
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: session:")
    assert "one house" in result.stderr


# -- The scenario runner's entry point ---------------------------------------


def test_scenario_run_over_a_file_runs_that_scenario(
    tmp_path: Path, runner: CliRunner, vocabulary: Vocabulary
) -> None:
    """One file is one run, and the run is the library's own.

    Falsified by a CLI that ran the file through anything but `run_file` -- a
    different seed, a house opened differently -- which would mean a scenario
    proven at one face was not the scenario the other face ran.
    """
    path = _scenario(tmp_path, PASSING_SCENARIO)
    document = _document(runner.invoke(app, ["scenario", "run", str(path), "--json"]))
    assert document["scenario"] == str(path)
    assert document["runs"] == [jsonable(run_file(path, vocabulary=vocabulary))]


def test_scenario_run_over_a_directory_runs_every_scenario_in_it(
    tmp_path: Path, runner: CliRunner
) -> None:
    """A directory is a corpus: every scenario in it runs, each of its own.

    Falsified by a directory run that stopped at the first file, or that ran one
    scenario once and reported it as the corpus: a corpus is a set of seeds and
    running one of them is not running it.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _scenario(corpus, PASSING_SCENARIO, name="one.yaml")
    _scenario(corpus, PASSING_SCENARIO, name="two.yaml")
    document = _document(runner.invoke(app, ["scenario", "run", str(corpus), "--json"]))
    assert document["scenario"] == str(corpus)
    assert len(document["runs"]) == 2


def test_a_failing_scenario_exits_non_zero_and_writes_the_failure_report(
    tmp_path: Path, runner: CliRunner
) -> None:
    """A failure is a document on stdout, a summary on stderr, and a non-zero exit.

    Falsified by a failure reported only as a message, or as an exit code with
    nothing on stdout: a CI job cannot replay a disagreement it was not told the
    inputs of, which is what the report's `expected` and `actual` are for.
    """
    path = _scenario(tmp_path, FAILING_SCENARIO, name="failing.yaml")
    result = runner.invoke(app, ["scenario", "run", str(path), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert {"scenario", "expectation", "expected", "actual"} <= set(report)
    # The failure report is the runner's document, so it names the scenario the
    # way the loader does -- `as_posix()` -- where the entry point's own
    # `scenario` key echoes the argument exactly as the shell passed it. This is
    # the one place the two spellings differ, and asserting the wrong one here
    # would pass on the CI runner and fail on the platform this is developed on.
    assert report["scenario"] == path.as_posix()
    assert report["expected"] == "off"
    assert report["actual"] == "on"
    assert path.as_posix() in result.stderr
    assert "failed" in result.stderr


def test_a_failure_report_is_the_same_document_in_both_renderings(
    tmp_path: Path, runner: CliRunner
) -> None:
    """The failing run's document is written the same way with and without `--json`.

    Falsified by a failure path that ignored `--json` -- which would leave the
    one output a machine has to read as the one shaped for a person.
    """
    path = _scenario(tmp_path, FAILING_SCENARIO, name="failing.yaml")
    plain = runner.invoke(app, ["scenario", "run", str(path)])
    as_json = runner.invoke(app, ["scenario", "run", str(path), "--json"])
    assert plain.exit_code == as_json.exit_code == 1
    assert json.loads(plain.stdout) == json.loads(as_json.stdout)


def test_scenario_run_refuses_a_session_that_also_names_a_house(
    tmp_path: Path, runner: CliRunner
) -> None:
    """`scenario run` with `--fixture` is refused rather than resolved.

    Falsified by the runner preferring one of the two houses: the run would be
    of a house no scenario asked for, and the report would name the block that
    was not used.
    """
    path = _scenario(tmp_path, PASSING_SCENARIO)
    result = runner.invoke(app, ["--fixture", "minimal", "scenario", "run", str(path)])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: scenario run:")
    assert "second one" in result.stderr


def test_scenario_run_refuses_a_path_that_does_not_exist(
    tmp_path: Path, runner: CliRunner
) -> None:
    """A path that is not there is refused by name, before anything is loaded.

    Falsified by a missing path reported as a load failure or a traceback: the
    caller needs to know the runner never found the file rather than that the
    file was wrong.
    """
    missing = tmp_path / "absent.yaml"
    result = runner.invoke(app, ["scenario", "run", str(missing)])
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: scenario run:")
    assert str(missing) in result.stderr


def test_the_surfaces_session_is_the_one_the_callback_built(
    runner: CliRunner,
) -> None:
    """`--seed` reaches the session the subcommand acts on, not only the document.

    Falsified by a group callback whose options were parsed and dropped: the
    seed would silently be the default and two runs asked to differ would not.
    """
    first = _document(
        runner.invoke(
            app, ["--fixture", "minimal", "--seed", "1", "snapshot", "--json"]
        )
    )
    second = _document(
        runner.invoke(
            app, ["--fixture", "minimal", "--seed", "2", "snapshot", "--json"]
        )
    )
    assert first["result"]["random_seed"] == 1
    assert second["result"]["random_seed"] == 2
