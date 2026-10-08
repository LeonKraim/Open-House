"""What Open House hands Node-RED, and what it refuses to guess.

**These do not prove the flow runs.** Nothing here can: whether a node deploys
is a fact about a real Node-RED and a real palette, and the answer to it is in
`tools/ha/probe_flows.py`, which pushes a flow into a container and drives the
loop with a real light. What is checked here is the half that *is* this
repository's: that the payload is written in the palette's current schema, that
the service node names the module and the input, and that a house with no
Node-RED and a Node-RED with no server node are refused in words a person can
act on rather than pushed at.

The payload checks read like a transcription of the palette's field names, and
that is the point of them. `POST /flow` does not migrate what it is given -- see
`_TRIGGER_VERSION` -- so a rename in the palette is not a loud failure here but
a flow that deploys and never fires, in somebody else's house. Written out, the
rename fails a test instead.

**The module is loaded against a stub.** This suite deliberately does not have
Home Assistant installed -- nothing in the repository's checks imports the
integration, because the integration only means anything inside a running home
-- and `node_red.py` imports three names from it. So those three are stubbed and
the module is loaded from its path, which also keeps the package's `__init__`
(and everything *it* imports, which is most of the integration) from running.
Nothing under test is stubbed: the payloads are built out of plain strings and
dictionaries, and the stub is only what a type annotation and a session helper
would have been.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

#: What the palette calls a trigger's watched entities, and what it calls the
#: field the service node's domain and service were merged into. Named here so
#: the assertions below read as the schema rather than as magic strings.
_ENTITIES = "entities"
_ACTION = "action"


#: The names `custom_components/open_house/node_red.py` imports at its own top
#: level, and what stands in for each while it is loaded. Nothing under test is
#: stubbed: the payloads are built out of plain strings and dictionaries, and
#: what is here is only what a type annotation and a session helper would have
#: been.
_STANDING_IN: tuple[tuple[str, dict[str, object]], ...] = (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": object}),
    ("homeassistant.helpers", {}),
    (
        "homeassistant.helpers.aiohttp_client",
        {"async_get_clientsession": lambda hass: None},
    ),
    ("custom_components", {}),
    ("custom_components.open_house", {}),
    ("custom_components.open_house.const", {"DOMAIN": "open_house"}),
)


def _load_node_red() -> types.ModuleType:
    """`custom_components/open_house/node_red.py`, without the package around it.

    Loaded by path so the package's `__init__` never runs: it registers the
    panel and the websocket commands, and importing it would make this file fail
    on whatever the *next* Home Assistant API it reaches for is called.

    **`sys.modules` is put back exactly as it was found.** pytest imports every
    test module before running any test, so these stubs would still be in place
    when `tests/test_acceptance_loop.py` runs -- and that module's autouse guard
    calls `importlib.util.find_spec("homeassistant")` over the *checkout*, which
    raises `ValueError: homeassistant.__spec__ is None` when a stub is there
    rather than answering the question it asks. The module is loaded with the
    stubs in place and the previous state restored in a `finally`, which is
    enough: `node_red` binds these names into its own namespace while it loads,
    so it does not read `sys.modules` again afterwards.
    """
    before = {name: sys.modules.get(name) for name, _ in _STANDING_IN}
    for name, attributes in _STANDING_IN:
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module

    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "open_house"
        / "node_red.py"
    )
    try:
        spec = importlib.util.spec_from_file_location(
            "custom_components.open_house.node_red", path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # Registered while it loads, because `@dataclass` reads the defining
        # module back out of `sys.modules` -- and taken out again below, so a
        # module this file loaded by hand is not one an import would find.
        before[spec.name] = sys.modules.get(spec.name)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        for name, previous in before.items():
            if previous is None:
                del sys.modules[name]
            else:
                sys.modules[name] = previous
    return module


node_red = _load_node_red()


class FakeAdmin:
    """A Node-RED that answers from a script, and remembers being asked.

    Duck-typed rather than a real `NodeRed`, because what is under test is the
    *payload* and a real client would need a `HomeAssistant` to build a session
    out of -- none of which the payload depends on.
    """

    def __init__(self, *, flows: list[dict[str, Any]] | None = None) -> None:
        self.held = flows if flows is not None else [{"id": "ohsrv1", "type": "server"}]
        self.calls: list[tuple[str, str, Any]] = []

    async def request(
        self, method: str, path: str, payload: Any = None, *, version: str | None = "v2"
    ) -> Any:
        self.calls.append((method, path, payload))
        if method == "GET":
            # The v2 shape, because `async_server_node` reads it: a bare array is
            # what a v1 answer is and what makes this look like a Node-RED with
            # no server node at all.
            return {"rev": 1, "flows": self.held}
        if method == "POST":
            return {"id": "minted_by_node_red"}
        return {}

    def body(self) -> dict[str, Any]:
        """The flow this fake was last handed."""
        return next(payload for method, _, payload in self.calls if method == "POST")


def _client(**kwargs: Any) -> Any:
    """A fake Node-RED, spelled so the callee's type is satisfied."""
    return FakeAdmin(**kwargs)


def _push(admin: Any, **overrides: Any) -> str:
    """Push one ordinary flow through `admin`, with the arguments a caller gives.

    `asyncio.run` rather than a plugin: this suite has no async runner installed,
    and the coroutine under test is a sequence of plain calls into a fake with
    nothing to await -- so an event loop here is a way of starting the function
    and not a thing the test is about.
    """
    arguments: dict[str, Any] = {
        "flow_id": "",
        "label": "Flow probe",
        "source_entity": "light.living_room",
        "output_entity": "sensor.open_house_flow_dynamic_bypass_light",
        "module": "dynamic_lighting",
        "name": "bypass_light",
    }
    arguments.update(overrides)
    flow_id: str = asyncio.run(node_red.async_push_flow(admin, **arguments))
    return flow_id


def _nodes(admin: Any) -> dict[str, dict[str, Any]]:
    """The pushed tab's nodes, by type."""
    return {node["type"]: node for node in admin.body()["nodes"]}


def test_the_trigger_is_written_in_the_palette_s_current_schema() -> None:
    """A trigger in an older shape deploys and then refuses to run.

    `POST /flow` stores what it is given without running the palette's migration
    chain, so a node claiming a version below the current one is loaded with
    every field that version lacked simply *absent* -- and absent `for` is a
    `ConfigError` at deploy naming a field nobody wrote.
    """
    admin = _client()
    _push(admin)
    trigger = _nodes(admin)["server-state-changed"]

    assert trigger["version"] == node_red._TRIGGER_VERSION
    # The three the palette's own migrations add and its controller then reads.
    assert trigger["for"] == "0"
    assert trigger["forType"] == "num"
    assert trigger["forUnits"] == "minutes"
    # The watched entity, in the shape this version names it -- not the
    # `entityidfilter` of the versions before.
    assert trigger[_ENTITIES] == {
        "entity": ["light.living_room"],
        "substring": [],
        "regex": [],
    }
    assert "entityidfilter" not in trigger


def test_the_service_node_uses_the_merged_action_field() -> None:
    """`domain` and `service` were one field from version 6, and it is `action`.

    A node posted with the two old ones has neither, so it calls nothing -- and
    a flow that fires and calls nothing is the failure this whole file exists to
    catch, because it looks exactly like a flow that works.
    """
    admin = _client()
    _push(admin)
    service = _nodes(admin)["api-call-service"]

    assert service["version"] == node_red._SERVICE_VERSION
    assert service[_ACTION] == "open_house.set_flow_value"
    assert "domain" not in service
    assert "service" not in service


def test_the_service_writes_this_module_s_input_and_nothing_else() -> None:
    """The two facts only Open House knows, spelled into the node it hands over.

    The value is handed back whole -- `payload`, evaluated as JSONata -- so a
    flow's answer keeps the type its nodes produced instead of arriving as the
    string a mustache template would make of it.
    """
    admin = _client()
    _push(admin)
    service = _nodes(admin)["api-call-service"]

    assert service["dataType"] == "jsonata"
    assert '"module": "dynamic_lighting"' in service["data"]
    assert '"input": "bypass_light"' in service["data"]
    assert '"value": payload' in service["data"]


def test_the_two_nodes_are_wired_so_the_flow_runs_with_nothing_added() -> None:
    """The wiring is the whole of what Open House contributes; it has to be there.

    A person given two unconnected nodes has been given the wiring to do by hand
    in the one place a mistake is silent: a flow that is drawn, deployed, and
    never fires.
    """
    admin = _client()
    _push(admin)
    nodes = _nodes(admin)

    assert nodes["server-state-changed"]["wires"] == [[nodes["api-call-service"]["id"]]]


def test_a_later_push_replaces_the_flow_rather_than_making_a_second() -> None:
    """The id is passed in on every push after the first, and it is what is returned.

    Node-RED mints a tab id on `POST` and reassigns it, so a push that made a
    second tab would leave the first running and the module answered by
    whichever of the two wrote last.
    """
    admin = _client()
    assert _push(admin, flow_id="8bd567c1b81870db") == "8bd567c1b81870db"
    assert [call[:2] for call in admin.calls][-1] == (
        "PUT",
        "/flow/8bd567c1b81870db",
    )


def test_the_first_push_answers_with_the_id_node_red_minted() -> None:
    """The id is Node-RED's to give, so the record keeps its answer and not ours."""
    admin = _client()
    assert _push(admin) == "minted_by_node_red"


def test_a_node_red_with_no_server_node_is_refused_rather_than_guessed() -> None:
    """The sentence names what to do, because there is nothing Open House can do.

    A server node carries the address of the house and a token, and a token is
    not something this integration may mint and write into another program's
    config. So a flow with no server behind it is a flow that connects to
    nothing, and it is refused instead of pushed.
    """
    admin = _client(flows=[])
    with pytest.raises(node_red.NodeRedError, match="no Home Assistant server node"):
        _push(admin)
    assert not [call for call in admin.calls if call[0] != "GET"]


def test_the_server_node_this_integration_pushed_is_the_one_used() -> None:
    """A Node-RED with several is a Node-RED somebody has configured twice.

    Ours is preferred when it is there, and any other is accepted -- a person who
    set Node-RED up by hand has already told it where their house is, which is a
    better answer than one guessed at here.
    """
    mine = {"id": node_red._SERVER_ID, "type": "server"}
    theirs = {"id": "ohsrv1", "type": "server"}
    admin = _client(flows=[theirs, mine])
    _push(admin)
    assert _nodes(admin)["server-state-changed"]["server"] == node_red._SERVER_ID


def test_a_server_node_is_preferred_over_any_other_kind_of_node() -> None:
    """`type == "server"` and nothing else, because every other node is a node.

    Read out of a v2 answer, where `flows` holds every node in the editor rather
    than only the server nodes -- so a filter that was looser than this would
    hand a trigger's id to the two pushed nodes and the flow would reference
    itself.
    """
    admin = _client(
        flows=[{"id": "a-tab", "type": "tab"}, {"id": "srv", "type": "server"}]
    )
    _push(admin)
    assert _nodes(admin)["server-state-changed"]["server"] == "srv"


def test_a_row_with_nothing_to_watch_still_gets_its_output_node() -> None:
    """**The case the cast was asked for.** A row answered by a number or a piece
    of text has no entity for a state trigger to watch, and the answer to that is
    not to refuse: the output node is the half Open House promises -- it carries
    the module, the input and the service, none of which a person should have to
    spell -- and it is pushed on its own, with an empty left-hand side for
    whatever they build to wire into.

    Refusing here would take the cast away from exactly the inputs worth
    programming.
    """
    admin = _client()
    _push(admin, source_entity=None)
    nodes = admin.body()["nodes"]

    assert [node["type"] for node in nodes] == ["api-call-service"]
    service = nodes[0]
    assert service["version"] == node_red._SERVICE_VERSION
    assert service["action"] == "open_house.set_flow_value"
    # Nothing wired in, and nothing pretending to be: a trigger on nothing would
    # be a node that fires never and looks like one that fires.
    assert service["wires"] == [[]]
    assert '"input": "bypass_light"' in service["data"]


def test_an_empty_source_is_a_flow_without_a_trigger_rather_than_an_error() -> None:
    """`""` and `None` are the same answer here, and both are the ordinary one.

    The server passes whatever the row resolved to, which is `None` for a literal
    and a string for a device; a caller that spelled "no entity" as an empty
    string means the same thing and gets the same flow.
    """
    for empty in ("", None):
        admin = _client()
        _push(admin, source_entity=empty)
        assert [node["type"] for node in admin.body()["nodes"]] == ["api-call-service"]


def test_the_tab_says_which_half_is_the_persons() -> None:
    """The description is read by whoever opens this tab in Node-RED directly.

    It is the only place that says the output node is regenerated on the next
    build and everything else is kept -- which is the difference between a person
    putting their work between the two ends and putting it beside them.
    """
    wired = _client()
    _push(wired)
    assert "will replace this tab's two ends" in wired.body()["info"]

    bare = _client()
    _push(bare, source_entity=None)
    assert "open_house_flow_dynamic_bypass_light" in bare.body()["info"]
    assert "Nothing is wired into it yet" in bare.body()["info"]
