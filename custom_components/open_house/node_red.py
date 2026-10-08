"""Node-RED, as the fourth way an input may be answered.

An input's answer can be a value a person typed, a device they picked, a slot
their room binds, a condition Open House evaluates, or a template. **A flow** is
the one that is not an answer at all: it is *code*, living in another program,
and all Open House can do with it is give it a place to put its result and wire
the two ends up. So it creates an entity for the flow to write
(`ha_adapter.module_host.flow_entity_id`), pushes a flow that writes it, and
binds the input to it.

**Offered on every input, and the two ends are made differently.** The output
node is made every time, because everything about it is a fact of *this* module
-- which module, which input, which service -- and a person guessing at it is a
person whose flow runs and never arrives. The **input** node is made only when
the row's own answer resolves to an entity, because a trigger watches one entity
and a row holding a number has nothing to watch; there Open House pushes the
output node alone and leaves the left-hand side empty for the person to build
into. That is the case the whole cast was asked for: add an input node, do
anything with the data, and let the output take care of getting it home.

**Why push a flow at all rather than link to an empty editor.** A person who
opens an empty Node-RED and has to find the right service, the right module and
the right input by hand is being asked to do the wiring twice -- once in their
head and once in the editor -- and any of the three spelled slightly differently
is a flow that runs and never arrives. Open House knows all three. So it builds
that much and hands over the rest.

**Nothing here is translated and nothing is hidden.** The flow the person is
given is ordinary Node-RED -- Home Assistant's own nodes, drawn in Node-RED's own
editor, editable with every node Node-RED has. What Open House adds is the two
ends, which is exactly the part that is a fact about *this* module and not a
decision about the house.

**The Admin API, not the editor's sockets.** `POST /flow` and `PUT /flow/<id>`
are documented, stable, and answer with the id of what was written; the editor's
own protocol is neither. Every call is made with `async_get_clientsession`, so
the connections are Home Assistant's to pool and to close.

**A server node has to exist and Open House will not invent one.** A Home
Assistant node in Node-RED is useless without the `server` config node that
carries the instance's address and a token -- and a token is not something this
integration has any business minting and writing into another program's config.
An instance that has ever used Node-RED with Home Assistant has one already;
this reuses it. When there is none the cast is refused with one sentence naming
what to do, rather than pushing a flow that connects to nothing and says nothing
while it does not.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

__all__ = [
    "NodeRed",
    "NodeRedError",
    "async_client",
    "async_delete_flow",
    "async_push_flow",
    "async_server_node",
    "editor_url",
    "entry_options",
]

_LOGGER = logging.getLogger(__name__)

#: The key the Node-RED address lives under in a config entry's options.
OPTION_URL = "node_red_url"
#: The key an optional bearer token lives under, for a Node-RED with `adminAuth`
#: set. The dev container has none; the Home Assistant add-on does.
OPTION_TOKEN = "node_red_token"
#: The key the *browser's* address for Node-RED lives under, when it differs from
#: `OPTION_URL`. **The two really are different addresses and there is no way to
#: derive one from the other.** Home Assistant reaches Node-RED over the docker
#: network, by a service name that means nothing outside it -- `nodered` -- while
#: the person's browser reaches the same editor through the host's published
#: port or through the add-on's ingress path. Pushing and linking are therefore
#: two questions, and a single option would make one of them wrong: an address
#: the house can reach is a dead link, and an address the browser can reach is a
#: push that times out.
OPTION_EDITOR_URL = "node_red_editor_url"

#: How long any one Admin API call may take. Short, because every one of these is
#: on the path of a person waiting for a module to be hosted, and a Node-RED that
#: is not answering should be a refusal in a second rather than a form that hangs.
_TIMEOUT_SECONDS = 10

#: The name Open House looks for a server node under and, when it makes the tab
#: it pushes, the id of the node it references. Node-RED preserves a node's id
#: and reassigns only a tab's, which is what makes a fixed id usable here.
_SERVER_ID = "open_house_server"

#: The palette schema versions the pushed nodes claim, and the reason they are
#: written out rather than left off. `POST /flow` stores whatever it is given:
#: the migration chain runs on a node's config when the *editor* creates it, not
#: when the Admin API does, so a node posted in an older shape is loaded as-is
#: with every field that version did not have simply absent -- and a field like
#: `for` being absent is a `ConfigError` at deploy, not a default. These are the
#: two node types' current versions in
#: `node-red-contrib-home-assistant-websocket`; a node pushed below them would
#: need every field those migrations would have added, written by hand anyway.
#: `tests/test_node_red.py` holds the payload to the shape these versions
#: require, and the *palette itself* is what `tools/ha/probe_flows.py` checks,
#: because only a real Node-RED can say whether a node it was handed deploys.
_TRIGGER_VERSION = 6
_SERVICE_VERSION = 7


class NodeRedError(Exception):
    """Node-RED could not be reached, or refused what was asked of it."""


@dataclass(frozen=True)
class NodeRed:
    """One instance's Node-RED, as everything here reaches it."""

    url: str
    token: str
    hass: HomeAssistant

    async def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        version: str | None = "v2",
    ) -> Any:
        """One Admin API call, with the answer parsed, or a refusal raised.

        Every failure is a `NodeRedError` carrying what Node-RED said, because
        the sentence a person reads has to be about their Node-RED and not about
        an HTTP status: "connection refused" and "no Home Assistant server node"
        are the two things that actually happen, and both are things they can go
        and fix.
        """
        session = async_get_clientsession(self.hass)
        headers = {"Accept": "application/json"}
        if version is not None:
            headers["Node-RED-API-Version"] = version
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = self.url.rstrip("/") + path
        try:
            async with session.request(
                method, url, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    raise NodeRedError(
                        f"Node-RED refused {method} {path} "
                        f"({response.status}): {_said(body)}"
                    )
                if not body:
                    return None
                try:
                    return await response.json(content_type=None)
                except ValueError:
                    # An Admin API path that answers with the editor's own HTML
                    # is a Node-RED that does not have the API this expects --
                    # an old one, or something else answering on that port.
                    raise NodeRedError(
                        f"Node-RED answered {method} {path} with something that "
                        "is not the Admin API: check that this is a Node-RED"
                    ) from None
        except NodeRedError:
            raise
        except Exception as failure:
            raise NodeRedError(
                f"Node-RED at {self.url} could not be reached: {failure}"
            ) from failure


def entry_options(hass: HomeAssistant) -> Mapping[str, Any]:
    """The Node-RED settings of the instance's Open House entry, or none.

    Read off the *entry* rather than the runtime, because the two callers that
    want it want different halves of it: a push wants the client, and the panel's
    capabilities want the address to build a link out of. Both ask the same
    question, so both read it here rather than each following the entry's
    lifecycle on its own.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        return dict(entry.options)
    return {}


def editor_url(options: Mapping[str, Any]) -> str:
    """The Node-RED address a *browser* can reach, or `""` when none is set.

    The editor's own address when one was given, and the push address when one
    was not -- which is right for the ordinary case of a Node-RED the browser
    and the house both reach by the same name, and wrong only for a stack like
    the dev container's, where one of them resolves a docker service name. That
    case is why the second option exists; it is not the case a default should
    be built around.
    """
    chosen = str(options.get(OPTION_EDITOR_URL) or "").strip()
    if chosen:
        return chosen
    return str(options.get(OPTION_URL) or "").strip()


def async_client(hass: HomeAssistant, options: Mapping[str, Any]) -> NodeRed | None:
    """The instance's Node-RED, or `None` when nobody has said where it is.

    `None` and not an error, because not having a Node-RED is the ordinary state
    of a house that does not use one: the cast is offered and the row says where
    to set the address, which is a thing a person can act on, where an exception
    on a screen they are only looking at is not.
    """
    url = str(options.get(OPTION_URL) or "").strip()
    if not url:
        return None
    return NodeRed(url=url, token=str(options.get(OPTION_TOKEN) or ""), hass=hass)


async def async_server_node(client: NodeRed) -> str:
    """The id of the Home Assistant server node in this Node-RED.

    Looked for on every push rather than remembered, because it is Node-RED's
    state and a person may rename or replace it between one import and the next;
    a remembered id would then be a reference to a node that is gone.

    `_SERVER_ID` is preferred when it is there -- it is the one this integration
    pushed, if it pushed one -- and any other `server` node is accepted, because
    a person who configured Node-RED by hand has already told it where their Home
    Assistant is and what token to use, and that answer is better than any this
    could write.

    **Asked for as v2, which is not the default.** Without the version header
    `GET /flows` answers with a bare array of every node; with it, with
    `{"rev": ..., "flows": [...]}`. Reading the v2 shape out of a v1 answer is
    not a parse error -- `flows` is simply absent from an array, so the answer
    looks like a Node-RED that has no server node at all, and the cast is refused
    with a sentence telling the person to go and add one they already have.
    """
    answer = await client.request("GET", "/flows")
    nodes: list[Mapping[str, Any]] = []
    rows = answer.get("flows") if isinstance(answer, Mapping) else None
    for item in rows if isinstance(rows, list) else []:
        if isinstance(item, Mapping) and item.get("type") == "server":
            nodes.append(item)
    for node in nodes:
        if node.get("id") == _SERVER_ID:
            return _SERVER_ID
    if nodes:
        return str(nodes[0]["id"])
    raise NodeRedError(
        "this Node-RED has no Home Assistant server node, so a flow it was given "
        "would connect to nothing: open Node-RED, add any Home Assistant node and "
        "point it at your Home Assistant once, then import the module again"
    )


async def async_push_flow(
    client: NodeRed,
    *,
    flow_id: str,
    label: str,
    source_entity: str | None,
    output_entity: str,
    module: str,
    name: str,
) -> str:
    """Create or replace one flow, and answer with the id Node-RED gave it.

    **The output node is always made, and the input node only when there is
    something for it to watch.** That split is the whole shape of this: the
    output end is a fact about *this* module -- which module, which input, which
    service -- and none of it is the person's to guess; the input end is a
    question about the house, and Open House can only answer it when the row's
    own binding names an entity. So a row answered by a device gets both ends
    wired together, and a row answered by a number or a piece of text gets the
    output node and an empty left-hand side for the person to build into,
    which is exactly what "add an input node and do anything with the data in
    there" asks for.

    What goes between the ends is the person's, and this never touches it:
    `flow_id` is passed in on every *later* push and the tab is replaced whole,
    so the nodes they added are kept by Node-RED under the same tab rather than
    discarded by this.

    Nothing is written into the input when the push succeeds -- the record is
    what binds it, and the caller writes that -- so a push that fails leaves the
    module exactly as it was.
    """
    server = await async_server_node(client)
    tab = flow_id or _tab_id(module, name)
    nodes: list[dict[str, Any]] = []
    if source_entity:
        nodes.append(_trigger_node(tab, module, name, server, source_entity))
    nodes.append(_output_node(tab, label, server, module, name))
    body = {
        "id": tab,
        "type": "tab",
        "label": label,
        "disabled": False,
        "info": _flow_info(module, name, output_entity, bool(source_entity)),
        "nodes": nodes,
    }
    if flow_id:
        await client.request("PUT", f"/flow/{flow_id}", body)
        return flow_id
    answer = await client.request("POST", "/flow", body)
    pushed = str((answer or {}).get("id") or "")
    if not pushed:
        raise NodeRedError("Node-RED accepted the flow but did not say what it was")
    return pushed


def _trigger_node(
    tab: str, module: str, name: str, server: str, source_entity: str
) -> dict[str, Any]:
    """The input end: a state trigger on the entity the row resolved to.

    **Written in the palette's current schema and not in an older one.** Node-RED
    migrates a node's config when the *editor* creates it, and `POST /flow`
    creates nodes without ever handing them to the migration chain -- so a payload
    in an older shape is stored as it stands, with every field the newer versions
    added simply absent. That is not a loud failure: the trigger throws a
    `ConfigError` naming a field nobody wrote, and the flow deploys and never
    fires. What is written here is what the editor itself writes on a deploy of
    this palette version, and `tests/test_node_red.py` holds it to that.
    """
    return {
        "id": f"{tab}_in",
        "type": "server-state-changed",
        "z": tab,
        "name": f"{module}: {name}",
        "server": server,
        "version": _TRIGGER_VERSION,
        "entities": {
            "entity": [source_entity],
            "substring": [],
            "regex": [],
        },
        # `true`, so the flow writes the input once as soon as it is deployed: a
        # module whose answer only ever arrives on the *next* change of its source
        # would sit unanswered until something moved, which is a module that looks
        # broken until it looks fine.
        "outputInitially": True,
        "outputOnlyOnStateChange": True,
        "stateType": "str",
        # No wait, and this is a choice rather than a default: a person who wants
        # a delay can put this node's output into a node of their own that waits,
        # and Open House writing a delay nobody chose would be a flow that answers
        # late for a reason nothing on the screen says.
        "for": "0",
        "forType": "num",
        "forUnits": "minutes",
        "ifState": "",
        "ifStateType": "str",
        "ifStateOperator": "is",
        "ignorePrevStateNull": False,
        "ignorePrevStateUnknown": False,
        "ignorePrevStateUnavailable": False,
        "ignoreCurrentStateUnknown": False,
        "ignoreCurrentStateUnavailable": False,
        "exposeAsEntityConfig": "",
        "outputProperties": [
            {
                "property": "payload",
                "propertyType": "msg",
                "value": "",
                "valueType": "entityState",
            },
            {
                "property": "data",
                "propertyType": "msg",
                "value": "",
                "valueType": "eventData",
            },
            {
                "property": "topic",
                "propertyType": "msg",
                "value": "",
                "valueType": "triggerId",
            },
        ],
        "outputs": 1,
        "x": 180,
        "y": 180,
        "wires": [[f"{tab}_out"]],
    }


def _output_node(
    tab: str, label: str, server: str, module: str, name: str
) -> dict[str, Any]:
    """The output end: the node that hands a value back to the module.

    **This is the node the whole cast exists for** -- everything else is what a
    person builds around it -- so it is written whichever way the row was
    answered, and it is the one node Open House promises.
    """
    return {
        "id": f"{tab}_out",
        "type": "api-call-service",
        "z": tab,
        "name": f"{label} output",
        "server": server,
        "version": _SERVICE_VERSION,
        # `action` and not `domain`/`service`: those two became one field at
        # version 6, and a node posted with the old pair has neither.
        "action": "open_house.set_flow_value",
        "floorId": [],
        "areaId": [],
        "deviceId": [],
        "entityId": [],
        "labelId": [],
        # The whole message, and `jsonata` rather than a template: what a person's
        # nodes produce is a value of whatever type those nodes produce, and a
        # mustache template would stringify every one of them on the way through.
        "data": (
            '{"module": "' + module + '", "input": "' + name + '", "value": payload}'
        ),
        "dataType": "jsonata",
        "mergeContext": "",
        "mustacheAltTags": False,
        "blockInputOverrides": False,
        "outputProperties": [],
        "queue": "none",
        "x": 460,
        "y": 180,
        "wires": [[]],
    }


def _flow_info(module: str, name: str, output_entity: str, wired: bool) -> str:
    """What the tab says about itself, in Node-RED's own tab description.

    Read by a person who opened Node-RED directly rather than through the panel,
    and by whoever finds this tab six months later -- so it says who made it, what
    the two ends are, which half is theirs, and what happens to it on the next
    build. The last sentence is the one that matters: everything between the ends
    survives a rebuild, and nothing else does.
    """
    if wired:
        return (
            f"Pushed by Open House for the module {module}, input {name}. The two "
            "ends are the wiring; the nodes in between are yours, and Open House "
            "will replace this tab's two ends and keep the rest when the module "
            f"is built again. Writes {output_entity}."
        )
    return (
        f"Pushed by Open House for the module {module}, input {name}. The output "
        "node on the right is the one Open House made, and it is the whole of the "
        "contract: whatever reaches it is the module's answer. Nothing is wired "
        "into it yet, because the row that answers this input is not a device and "
        "there was nothing for a trigger to watch -- build whatever starts it and "
        "wire that to it. Open House replaces the output node and keeps the rest "
        f"when the module is built again. It writes {output_entity}."
    )


async def async_delete_flow(client: NodeRed, flow_id: str) -> None:
    """Take a flow away; a module that is gone leaves nothing running.

    A missing flow is the ordinary case and not a failure -- a house whose
    Node-RED was reset, or a module hosted while Node-RED was down, has a record
    naming a flow that is not there -- so a 404 is swallowed and anything else is
    not. Deleting leaves the entity alone: what the entity holds is the last value
    the flow wrote, and taking the module out is what takes the entity away.
    """
    try:
        await client.request("DELETE", f"/flow/{flow_id}")
    except NodeRedError as failure:
        if "404" not in str(failure):
            raise
        _LOGGER.debug("the flow %s was already gone from Node-RED", flow_id)


def _tab_id(module: str, name: str) -> str:
    """A tab id for a flow that does not exist yet.

    Prefixed so it reads as Open House's in `/flows`, and derived from the module
    and the input so the same input pushed twice lands on the same tab rather
    than making a second one. Node-RED guarantees an id is unique and will
    reassign it if it collides with something else, which is why the id it
    answers with is the one that is recorded and not this.
    """
    return f"open_house_{module}_{name}"


def _said(body: str) -> str:
    """What Node-RED said, as one line short enough for a panel row."""
    text = " ".join(str(body).split())
    return text[:200] if text else "no reason given"
