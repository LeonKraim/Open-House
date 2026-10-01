"""The MCP server: one tool per registry operation, and the runner's entry point.

`control-surface`'s "The MCP server is a thin adapter over the same registry" --
task 10.3 -- with the runner's own entry point on this face (10.6) and the two
things a client reads off a failed call: a refusal naming the tool, and a failing
scenario's summary and report.

The server is driven in-process, through `mcp.Client` over the `Server` object
`build_server` returns, rather than over a transport: what is under test is the
adapter and not the SDK's stdio framing, and a process boundary would only add a
way for the check to fail for a reason the module does not claim. Every check is
made through the client, so the schema a tool *advertises* and the schema its
arguments are *validated* against cannot be shown to be one thing unless they
really are. Each test names the implementation that would falsify it: a tool set
declared beside the registry, a schema re-derived through pydantic, a refusal
returned as a raised exception or as a result that does not name the tool, or a
`run_scenario` tool that opened its own house instead of the one its `given`
block names.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from mcp import Client
from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from engine.vocabulary import Vocabulary
from openhouse.facade import open_session
from openhouse.mcp_server import SCENARIO_ENTRY, SERVER_NAME, build_server
from openhouse.operations import OPERATION_NAMES, OPERATIONS, input_schema
from openhouse.results import result_document
from openhouse.scenarios import run_file

if TYPE_CHECKING:
    from openhouse.facade import OpenHouse

ROOT = Path(__file__).resolve().parents[1]

#: The tool the requirement sets aside by name: the runner's entry point, which
#: is composed over the ten operations rather than being an eleventh.
RUNNER_TOOL = "run_scenario"

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


@pytest.fixture
def session(vocabulary: Vocabulary) -> OpenHouse:
    """A session per test: a tool call is allowed to change the house it drives."""
    return open_session(house="minimal", vocabulary=vocabulary)


def _tools(session: OpenHouse) -> dict[str, Tool]:
    """What the server advertises, by tool name, as a client sees it."""

    async def main() -> list[Tool]:
        async with Client(build_server(session)) as client:
            return (await client.list_tools()).tools

    return {tool.name: tool for tool in asyncio.run(main())}


def _call(
    session: OpenHouse, name: str, arguments: dict[str, object]
) -> CallToolResult:
    """One tool call, made in-process against a freshly built server."""

    async def main() -> CallToolResult:
        async with Client(build_server(session)) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(main())


def _text(result: CallToolResult) -> str:
    """The single text block of a result that carries exactly one."""
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def _scenario(tmp_path: Path, text: str, *, name: str = "scenario.yaml") -> Path:
    """Write one scenario into the test's own directory and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# -- The server is the registry -------------------------------------------------


def test_build_server_returns_the_low_level_server_under_the_fixed_name(
    session: OpenHouse,
) -> None:
    """`build_server` hands back the SDK's own `Server`, named for the product.

    Falsified by a returned wrapper -- the low-level `Server` is what
    `mcp.Client` can be handed directly, which is what makes this surface
    testable without a transport -- or by a server named something the client
    cannot recognise.
    """
    server = build_server(session)
    assert isinstance(server, Server)
    assert server.name == SERVER_NAME == "openhouse"


def test_the_server_advertises_one_tool_per_operation_plus_the_runner(
    session: OpenHouse,
) -> None:
    """The tool set is the registry's ten, plus `run_scenario` and nothing else.

    Falsified by a tool for anything outside `OPERATIONS`, by the runner's entry
    point missing, and by an operation no tool advertises -- a caller could not
    then reach an operation this face claims to expose.
    """
    tools = _tools(session)
    assert set(tools) == {*OPERATION_NAMES, RUNNER_TOOL}
    assert len(tools) == len(OPERATION_NAMES) + 1


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_each_tools_input_schema_is_the_registrys_own(
    name: str, session: OpenHouse
) -> None:
    """A tool's advertised input schema *equals* `input_schema(operation)`.

    Falsified by a schema derived a second time -- through pydantic, or by hand
    -- which is the drift the registry exists to prevent: a client reading the
    tool and a caller reading the descriptor would be reading two schemas.
    """
    assert _tools(session)[name].input_schema == input_schema(OPERATIONS[name])


def test_the_runners_tool_schema_is_the_entry_points_not_an_operations(
    session: OpenHouse,
) -> None:
    """`run_scenario`'s schema is the entry point's, and it is not in the registry.

    Falsified by the runner's tool being generated from a made-up eleventh
    operation, or by its being added to `OPERATIONS`: the registry the CLI and
    the facade read would then have grown a member the spec does not give it.
    """
    assert RUNNER_TOOL not in OPERATIONS
    assert _tools(session)[RUNNER_TOOL].input_schema == input_schema(SCENARIO_ENTRY)


@pytest.mark.parametrize("name", OPERATION_NAMES)
def test_each_tools_description_is_the_descriptors_summary_and_scope(
    name: str, session: OpenHouse
) -> None:
    """A tool's description carries the operation's boundary, not only its name.

    Falsified by a description invented at the server: an agent reading a tool
    would not meet the scope note -- including the three operations whose scope
    names a later phase -- which is the only place a tool says what it is not.
    """
    operation = OPERATIONS[name]
    described = _tools(session)[name].description
    assert described == f"{operation.summary}\n\n{operation.scope}"


# -- Calling a tool -------------------------------------------------------------


def test_a_read_returns_the_result_document_as_one_text_block(
    session: OpenHouse,
) -> None:
    """A read is one text block holding the same JSON object the CLI writes.

    Falsified by a result rendered as structured content, or as a document with
    an extra field: a client's `jq` over this face and over the CLI's output
    would then be parsing two shapes for one answer.
    """
    result = _call(session, "get_decision_log", {})
    assert result.is_error is False
    assert _text(result) == json.dumps(
        result_document("get_decision_log", ()), sort_keys=True
    )


def test_a_write_drives_the_session_the_server_was_built_over(
    session: OpenHouse,
) -> None:
    """A write reaches the server's own session, and reports the facade's view.

    Falsified by a server that opened a session per call: the write would come
    back looking right and be gone by the next one, which is the failure a
    surface that is "a face of a session" rather than a factory cannot have.
    """
    result = _call(session, "set_state", {"entity_id": "light.foyer", "state": "on"})
    assert result.is_error is False
    assert _text(result) == json.dumps(
        result_document("set_state", session.read_entity("light.foyer")), sort_keys=True
    )
    assert session.read_entity("light.foyer").state == "on"


def test_an_unknown_tool_is_refused_by_name(session: OpenHouse) -> None:
    """A call to a tool that does not exist fails, naming the tool it asked for.

    Falsified by an unknown name reaching an operation, or by a refusal that did
    not say which name was wrong: the caller's next step is to correct the name,
    and it can only do that if the answer carries it.
    """
    result = _call(session, "no_such_tool", {})
    assert result.is_error is True
    assert _text(result) == "Error: no_such_tool: no tool is named 'no_such_tool'"


def test_a_missing_required_argument_is_refused_naming_the_argument(
    session: OpenHouse,
) -> None:
    """A call omitting a required parameter fails, naming the parameter.

    Falsified by the omission reaching the handler -- a `TypeError` about a
    keyword would say nothing about the operation's inputs, which is what the
    tool advertises and what a caller reads.
    """
    result = _call(session, "set_state", {"entity_id": "light.foyer"})
    assert result.is_error is True
    text = _text(result)
    assert text.startswith("Error: set_state: ")
    assert "'state' is a required property" in text


def test_an_argument_outside_the_schema_is_refused_naming_the_argument(
    session: OpenHouse,
) -> None:
    """A parameter the schema does not declare fails, and the schema is why.

    Falsified by an unknown keyword being dropped and the call proceeding: a
    caller whose typo was silently ignored would take the untouched house for
    the one it asked to change.
    """
    result = _call(
        session, "set_state", {"entity_id": "light.foyer", "state": "on", "bogus": 1}
    )
    assert result.is_error is True
    text = _text(result)
    assert text.startswith("Error: set_state: ")
    assert "Additional properties are not allowed ('bogus' was unexpected)" in text
    assert session.read_entity("light.foyer").state == "off"


# -- The runner's entry point ---------------------------------------------------


def test_run_scenario_runs_one_file(
    tmp_path: Path, session: OpenHouse, vocabulary: Vocabulary
) -> None:
    """The runner's tool runs a scenario file and returns the library's own run.

    Falsified by the tool running the file through anything but `run_file`: a
    scenario proven through this face would not be the scenario the CLI ran.
    """
    path = _scenario(tmp_path, PASSING_SCENARIO)
    result = _call(session, RUNNER_TOOL, {"path": str(path)})
    assert result.is_error is False
    assert json.loads(_text(result)) == result_document(
        RUNNER_TOOL, run_file(path, vocabulary=vocabulary)
    )


def test_run_scenario_runs_a_directory_as_a_corpus(
    tmp_path: Path, session: OpenHouse, vocabulary: Vocabulary
) -> None:
    """A directory is every scenario in it, as it is on the CLI.

    Falsified by a directory treated as one scenario -- a corpus that answered
    with one run would be a corpus only in appearance, and the set of seeds it
    exists to be would go unrun.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _scenario(corpus, PASSING_SCENARIO, name="one.yaml")
    _scenario(corpus, PASSING_SCENARIO, name="two.yaml")
    result = _call(session, RUNNER_TOOL, {"path": str(corpus)})
    assert result.is_error is False
    document = json.loads(_text(result))
    assert document["operation"] == RUNNER_TOOL
    assert len(document["result"]) == 2


def test_a_failing_scenario_returns_the_summary_and_the_report(
    tmp_path: Path, session: OpenHouse
) -> None:
    """A failed run is an error result of two blocks: the summary, then the report.

    Falsified by one block, by a raised exception, or by a report missing the
    decision the scenario disagreed with -- `expected` and `actual` are what a
    caller replays, and a summary alone does not carry them.
    """
    path = _scenario(tmp_path, FAILING_SCENARIO, name="failing.yaml")
    result = _call(session, RUNNER_TOOL, {"path": str(path)})
    assert result.is_error is True
    assert len(result.content) == 2
    summary, report = result.content
    assert isinstance(summary, TextContent)
    assert isinstance(report, TextContent)
    # The report names the scenario by the loader's own spelling, which is
    # `as_posix()` and not the platform's -- a document that carried a backslash
    # on Windows and a slash elsewhere would replay to two different paths.
    assert path.as_posix() in summary.text
    assert "failed" in summary.text
    document = json.loads(report.text)
    assert {"scenario", "expectation", "expected", "actual"} <= set(document)
    assert document["expected"] == "off"
    assert document["actual"] == "on"


def test_run_scenario_overrides_the_seed_the_given_block_names(
    tmp_path: Path, session: OpenHouse
) -> None:
    """The tool's `seed` is passed through, and the run reports the one it used.

    Falsified by a seed accepted and dropped: a caller replaying a corpus at one
    seed would get the block's, and the report would name a seed the run did not
    use -- which is the one thing an override must not do.
    """
    path = _scenario(tmp_path, PASSING_SCENARIO)
    result = _call(session, RUNNER_TOOL, {"path": str(path), "seed": 7})
    assert result.is_error is False
    assert json.loads(_text(result))["result"]["seed"] == 7


def test_an_operation_that_raises_is_refused_rather_than_transported(
    session: OpenHouse,
) -> None:
    """An operation that raised comes back as a failed result naming the tool.

    Falsified by the exception escaping the handler: a client would see a
    transport error naming nothing, which is the failure a caller cannot act on
    and the reason a refusal is a result.
    """
    document = dict(session.snapshot())
    document["snapshot_version"] = "0.0.1"
    result = _call(session, "restore", {"document": document})
    assert result.is_error is True
    text = _text(result)
    assert text.startswith("Error: restore: ")
    assert "0.0.1" in text


def test_a_result_is_not_a_document_of_some_other_shape(
    session: OpenHouse,
) -> None:
    """Every successful result is one JSON object keyed by operation and result.

    Falsified by a face that returned the operation's value bare: the document
    is the contract, and a client that had to know which operation it called to
    read the answer would be remembering what the answer should have said.
    """
    for name in OPERATION_NAMES:
        result = _call(session, name, _arguments_for(name, session))
        assert result.is_error is False, _text(result)
        document = json.loads(_text(result))
        assert set(document) == {"operation", "result"}
        assert document["operation"] == name


def _arguments_for(name: str, session: OpenHouse) -> dict[str, object]:
    """The smallest valid arguments for each operation, for the sweep above."""
    if name == "advance_time":
        return {"minutes": 0}
    if name in {"set_state", "user_action"}:
        return {"entity_id": "light.foyer", "state": "on"}
    if name == "inject_fault":
        return {"entity_id": "light.foyer", "fault": "unavailable"}
    if name == "restore":
        return {"document": session.snapshot()}
    if name == "install_pack":
        return {"manifest": str(ROOT / "packs" / "official" / "example-pack.yaml")}
    if name == "import_config":
        return {"document": session.export_config()}
    return {}


def test_the_runner_tool_is_advertised_and_is_not_one_of_the_ten(
    session: OpenHouse,
) -> None:
    """`run_scenario` is advertised, and is not one of the ten.

    Falsified by `run_scenario` appearing in `OPERATIONS`, which would make the
    registry eleven and the CLI's subcommand set wrong by one -- the two faces
    read the same tuple, so the drift would be silent on both.
    """
    assert RUNNER_TOOL in _tools(session)
    assert RUNNER_TOOL not in OPERATION_NAMES
