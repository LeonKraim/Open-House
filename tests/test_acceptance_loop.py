"""The Phase 1 exit criterion, as one loop that runs -- task 12.0, `control-surface`.

The criterion is stated as something an agent *does*: "an AI agent can build a
house, run scenarios, read the log, and iterate with no HA installed."
`control-surface`'s acceptance turns it into something a checkout can be graded
on -- CI "SHALL run the end-to-end agent loop in a checkout where Home Assistant
is not installed: open a session through the facade, build a house from a
fixture, drive the operations to provoke a decision, read a decision record with
`get_decision_log`, and iterate -- change an input and observe a changed record --
using the library, and again once through the CLI and once through the MCP
server." The quote stops there; the requirement runs on, and its three remaining
clauses are not all this module's. It adds that the loop "SHALL also run the
scenario runner and read its JSON failure output" -- the runner runs here, as the
CLI face, and a failing run's report is asserted once, by `tests/test_cli.py` --
and that the check "SHALL fail when the no-HA guard is removed", which
`test_the_guard_fails_when_home_assistant_is_importable` is. The third,
"`openspec validate phase-1-engine --strict` SHALL pass", is run by nothing in
this repository: the validator is a node CLI on a developer's PATH, no job in
`.github/workflows/ci.yml` invokes it and no test shells out to it. That is a
recorded gap in the acceptance mapping, not a claim made here.

So this module is the criterion rather than a test of one part of it: one drive,
driven three times. The drive is the committed corpus's own -- motion arrives, a
minute passes, the motion clears, and a tail of minutes passes with the light
still on -- because a loop written against a house where nothing ever acts would
satisfy every assertion below while proving nothing. A tail of six minutes is
`scenarios/05b-hallway-light-watchdog-fires.yaml` (the window has passed, the
watchdog switches the light off) and a tail of four is
`scenarios/05-hallway-light-watchdog.yaml` (the window has not, and the rule must
not act); both are green, so what the drive decides is known independently of
this module.

That pair *is* the iteration, and it is the corpus's own input difference: one
number -- how far the tail advances -- changed, and the watchdog's record present
or absent accordingly. Taking the pair from two committed scenarios rather than
inventing a differing input keeps this a test of the loop rather than of a house
nobody ships.

The second face is the runner's and not the facade's, and that is a limitation
being named rather than a reading being taken. `openhouse/cli.py` opens a session
per invocation, so no agent can `set-state` through the CLI and then
`advance-time` and have the state survive between the two calls: the loop closes
through the CLI only by way of the runner, which drives a scenario's own steps in
one process. `scenario-runner`'s acceptance describes its CLI face exactly this
way, and the two readings of `control-surface` -- the runner as part of the loop,
or as a clause beside it -- differ on whether that face proves the criterion. The
mapping keeps a residual against this requirement rather than settling it here.

The claims are separate tests because they fail for different reasons: a loop
that reaches a decision, a loop that decides the same thing twice, and a loop
whose answer moves when an input does. The last two are the "and iterate" half
together -- a face that replayed but never varied would satisfy neither, and a
face that varied but never replayed would be a different system on every run.

`test_the_loop_runs_through_the_mcp_server` compares what it reads to the
library's records value for value, because `control-surface` requires the MCP
server to be a thin adapter over the same registry: two faces that drove one
session to one decision and reported different documents would be two answers to
"what did this operation return", which is the drift the phase removes
everywhere else. The CLI's face is not compared that way, and deliberately: a
`scenario run` reports *the run's* log, whose equality with `run_file` is already
asserted by `tests/test_cli.py::test_scenario_run_over_a_file_runs_that_scenario`,
so comparing it again here would be a second test of one clause.

The environment is the last claim and it is not about this code at all. "With no
HA installed" is a property of the checkout, and a job that happens to have
`homeassistant` importable -- pulled in as somebody else's dependency, say --
would run every test here and prove a weaker thing than it reports. The autouse
guard raises in every test above in such a checkout, so the run reports eight
errors rather than eight passes, and it is exercised against a finder that always
answers, because a guard proven only against the checkout that passes it has
never been seen to bite.

A failing run's JSON report is not asserted here either. It is part of the same
criterion -- a scenario disagreeing is how an agent learns what to change -- but
it belongs to the runner's own suite, where
`tests/test_cli.py::test_a_failing_scenario_exits_non_zero_and_writes_the_failure_report`
already holds it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from mcp import Client
from mcp.types import TextContent
from typer.testing import CliRunner

from engine.vocabulary import Vocabulary
from openhouse.cli import app
from openhouse.facade import open_session
from openhouse.mcp_server import build_server
from openhouse.results import jsonable

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from mcp.types import CallToolResult

ROOT = Path(__file__).resolve().parents[1]

#: The corpus's matched pair. Both name `minimal`, seed 11, the same start
#: instant and the same enable flag, and their drives differ in one number: the
#: tail that follows the motion clearing. Six minutes is past the five-minute
#: window `lighting.motion_light_off` watches, so the light is switched off and
#: the record is written; four is inside it, so the rule must leave the light
#: alone -- a watchdog that switched it off there would be switching off lights
#: somebody is still standing under.
WATCHDOG_FIRES = ROOT / "scenarios" / "05b-hallway-light-watchdog-fires.yaml"
WATCHDOG_HELD = ROOT / "scenarios" / "05-hallway-light-watchdog.yaml"

#: The house and the inputs both scenarios open at, so the drive here is the
#: scenarios' drive and not a second set of inputs that happens to resemble them.
HOUSE = "minimal"
SEED = 11
STARTED_AT = datetime.fromisoformat("2026-01-01T23:00:00+00:00")
MOTION_LIGHTING = "behaviour.motion_lighting.enabled"

#: The sensor the drive moves, and the two tails, as the pair drives them.
MOTION = "binary_sensor.living_room_motion"
FIRES = 6
HELD = 4

#: The rule the drive reaches, and the outcome that says it acted rather than
#: declined. Both are asserted: a record that reached the rule and declined reads
#: the same off the light as the timeout not having fired yet, which is why the
#: corpus asserts the citation rather than the state.
RULE = "lighting.motion_light_off"
ACTED = "acted"


@pytest.fixture(autouse=True)
def _no_home_assistant() -> None:
    """Fail every test in this module in a checkout that has Home Assistant.

    Autouse rather than called from each test: the requirement is about the
    *checkout*, so one importable `homeassistant` makes all of these weaker than
    they read, and the honest result is eight errors rather than eight passes.
    """
    _guard_no_home_assistant(importlib.util.find_spec)


def _guard_no_home_assistant(installed: Callable[[str], object | None]) -> None:
    """Fail when Home Assistant can be imported into this run.

    `installed` is `importlib.util.find_spec` at every real call site, and a
    parameter rather than a closed-over name so that
    `test_the_guard_fails_when_home_assistant_is_importable` can hand it a finder
    that always answers and watch the guard fire.
    """
    if installed("homeassistant") is not None:
        raise AssertionError(
            "Home Assistant is importable in this run. The Phase 1 exit "
            "criterion is that the loop runs in a checkout where it is not "
            "installed, so a visit from a job that has it proves a weaker thing "
            "than it reports."
        )


def _acted(records: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """The records showing the watchdog acting, out of everything that was read."""
    return [
        record
        for record in records
        if record.get("rule") == RULE and record.get("outcome") == ACTED
    ]


def _without_instants(
    records: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """The same records with each one's `at` dropped.

    Every record carries the instant it was written, and the two drives in the
    iteration test end at *different* instants by construction -- that is the
    input that changed. Comparing the raw lists would therefore hold whether or
    not the watchdog behaved differently, so the iteration assertion compares the
    records with the timestamps removed and the difference has to be a decision.
    """
    return [
        {key: value for key, value in record.items() if key != "at"}
        for record in records
    ]


# --------------------------------------------------------------------------
# The environment the criterion names
# --------------------------------------------------------------------------


def test_the_guard_fails_when_home_assistant_is_importable() -> None:
    """The guard's own falsifier: a finder that answers fails the loop.

    Without this the autouse guard would be an assertion about a checkout that
    could not be wrong -- the shape of guard that gets carried across a refactor
    and quietly stops being one.
    """
    with pytest.raises(AssertionError, match="Home Assistant is importable"):
        _guard_no_home_assistant(lambda name: object())


def test_home_assistant_is_not_importable_here() -> None:
    """The environment positively, so a reader sees it checked.

    The autouse guard already fails the claims below in a checkout that has Home
    Assistant; this names the same fact where a reader looks for it, rather than
    leaving the environment to be inferred from a fixture nobody read.
    """
    _guard_no_home_assistant(importlib.util.find_spec)


# --------------------------------------------------------------------------
# The loop, through the library
# --------------------------------------------------------------------------


def _drive(vocabulary: Vocabulary, *, tail: int) -> list[Mapping[str, object]]:
    """Open a house through the facade, drive it to a decision, read the log.

    The steps are the committed pair's, verb for verb, so what this reads is what
    a run over those files reads. `jsonable` rather than `to_document` because
    that is the normaliser both surfaces report through -- comparing a raw
    document against a face's would be two normalisations of one record, which is
    the second opinion `openhouse/results.py` exists to remove.
    """
    session = open_session(
        house=HOUSE,
        vocabulary=vocabulary,
        seed=SEED,
        started_at=STARTED_AT,
        house_settings={MOTION_LIGHTING: True},
    )
    session.set_state(MOTION, "on")
    session.advance_time(minutes=1)
    session.set_state(MOTION, "off")
    session.advance_time(minutes=tail)
    return cast("list[Mapping[str, object]]", jsonable(session.get_decision_log()))


@pytest.fixture(scope="module")
def vocabulary() -> Vocabulary:
    """The catalog's vocabulary, loaded once for the module."""
    return Vocabulary.load(ROOT)


def test_the_loop_runs_with_no_home_assistant(vocabulary: Vocabulary) -> None:
    """Build a house, drive it, provoke a decision, read the record.

    The criterion's first four clauses in one test, because a loop that completed
    any three of them is not a loop: an agent that could not reach the log has
    nothing to iterate on, and one that reached it without the drive having
    happened would be reading somebody else's run.
    """
    records = _drive(vocabulary, tail=FIRES)
    assert _acted(records), records


def test_the_same_inputs_give_the_same_records(vocabulary: Vocabulary) -> None:
    """Opened twice at one seed and driven the same way, the house decides alike.

    The first half of "and iterate": replay is what makes a change in the log
    attributable to a change in the input rather than to the run being different
    the second time. Falsified by any wall clock, stream or set iteration that
    reached the surface -- the determinism `control-surface` requires and the
    substrate's scans enforce.
    """
    assert _drive(vocabulary, tail=FIRES) == _drive(vocabulary, tail=FIRES)


def test_changing_an_input_changes_the_records(vocabulary: Vocabulary) -> None:
    """One number changed -- the tail -- and the watchdog's answer with it.

    The second half of "and iterate", and the half that makes the first half mean
    something: a face that returned the same log whatever it was asked would pass
    a replay check trivially. The change is the corpus's own, so the assertion is
    the one `scenarios/05-hallway-light-watchdog.yaml` already makes.

    The inequality is asserted over the records with their instants dropped, and
    the two claims are asserted on their own too: the drives end at different
    instants whatever the watchdog does, so a raw list comparison would pass on
    the clock alone, and what has to differ is the decision -- a record present
    in one run with the rule acting and absent from the other.
    """
    fired = _drive(vocabulary, tail=FIRES)
    held = _drive(vocabulary, tail=HELD)
    assert _acted(fired), fired
    assert _acted(held) == [], held
    assert _without_instants(fired) != _without_instants(held)


# --------------------------------------------------------------------------
# The same loop, through the CLI
# --------------------------------------------------------------------------


def _cli_run(path: Path) -> list[Mapping[str, object]]:
    """One `scenario run` over one file, and the log the run reports.

    The CLI opens a session per invocation, so a drive spread over several
    commands could not carry state between them: the CLI's face of this loop is
    the runner's entry point, which drives the scenario's steps in one process
    and reports the records that came back.
    """
    result = CliRunner().invoke(app, ["scenario", "run", str(path), "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["scenario"] == str(path)
    runs = document["runs"]
    assert len(runs) == 1, document
    return cast("list[Mapping[str, object]]", runs[0]["log"])


def test_the_loop_runs_through_the_cli() -> None:
    """The committed scenario, run by the entry point an agent would call."""
    records = _cli_run(WATCHDOG_FIRES)
    assert _acted(records), records


def test_the_cli_iterates_when_an_input_changes() -> None:
    """The pair again, at the other face: four minutes instead of six.

    A CLI shown only to run one scenario proves the face can be called; it does
    not prove an agent could learn anything from it. The pair is what an agent
    iterating actually does -- run, read, change an input, run again -- and the
    two scenarios differ in exactly the number an agent would change.
    """
    assert _acted(_cli_run(WATCHDOG_FIRES)), "the watchdog did not fire"
    assert _acted(_cli_run(WATCHDOG_HELD)) == [], "the watchdog fired inside the window"


# --------------------------------------------------------------------------
# The same loop, through the MCP server
# --------------------------------------------------------------------------


def _mcp_records(call: CallToolResult) -> list[Mapping[str, object]]:
    """The records one `get_decision_log` call reported, out of its document."""
    assert len(call.content) == 1, call.content
    block = call.content[0]
    assert isinstance(block, TextContent)
    document = json.loads(block.text)
    assert document["operation"] == "get_decision_log", document
    return cast("list[Mapping[str, object]]", document["result"])


def _mcp_drive(vocabulary: Vocabulary, *, tail: int) -> list[Mapping[str, object]]:
    """The same drive, over one session held by one server.

    One `build_server` call and one client context, because the server holds one
    session the way the CLI holds one and the *loop* is the point: a session
    opened per tool call could reach no decision, since nothing would carry from
    the arrival to the minute that follows it.
    """
    session = open_session(
        house=HOUSE,
        vocabulary=vocabulary,
        seed=SEED,
        started_at=STARTED_AT,
        house_settings={MOTION_LIGHTING: True},
    )

    async def drive() -> list[Mapping[str, object]]:
        async with Client(build_server(session)) as client:
            await client.call_tool("set_state", {"entity_id": MOTION, "state": "on"})
            await client.call_tool("advance_time", {"minutes": 1})
            await client.call_tool("set_state", {"entity_id": MOTION, "state": "off"})
            await client.call_tool("advance_time", {"minutes": tail})
            read = await client.call_tool("get_decision_log", {})
        return _mcp_records(read)

    return asyncio.run(drive())


def test_the_loop_runs_through_the_mcp_server(vocabulary: Vocabulary) -> None:
    """The face an agent is expected to drive, driving the same loop.

    Beyond reaching the record, this is where "a thin adapter over the same
    registry" is asserted: the records the tool reported are the library's, value
    for value. Both sides end in `jsonable(session.get_decision_log())` on the
    same session, so the equality is guaranteed *provided* the tool forwards to
    the handler it advertises, and that proviso is the thing worth pinning: a tool
    that built its own house, normalised a record its own way or dropped a field
    would reach the same decision and report a document no other face produces,
    and would fail here.
    """
    records = _mcp_drive(vocabulary, tail=FIRES)
    assert _acted(records), records
    assert records == _drive(vocabulary, tail=FIRES)
