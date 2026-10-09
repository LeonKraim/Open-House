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

**Where the Node-RED is, and the one rule about it.** An instance has one when a
person has named one in the integration's options -- the community add-on, or
their own, at an address only they can know. **That address always wins, and it
is looked for first at every call site**, which is what keeps Open House from
ever fighting a Node-RED a person already runs: nothing here scans for a second
one, nothing here reaches for the community add-on, and nothing here starts
anything. Only when *no* address is set does this fall through to the Node-RED
this repository ships as its own add-on (`BUNDLED_SLUG`), discovered by asking
the Supervisor about that one slug -- a read, never a start, and a house with a
Node-RED of its own never gets that far. The bundled add-on is reached at its
internal name for a push and through ingress for the editor, and publishes no
host port, so it cannot collide with a Node-RED on the host's 1880 either.

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
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

__all__ = [
    "BUNDLED_SLUG",
    "Bundled",
    "NodeRed",
    "NodeRedError",
    "async_bundled",
    "async_client",
    "async_delete_flow",
    "async_editor_url",
    "async_push_flow",
    "async_server_node",
    "bundled_from_info",
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

#: The Supervisor add-on this repository ships *beside* the integration: Node-RED
#: itself, so a house that has never installed it can still answer an input with
#: a flow. It is named here and in `open_house_nodered/config.yaml`, and the two
#: spellings are one fact -- the integration cannot find the add-on without it,
#: and it is deliberately not guessed by scanning whatever a person's Supervisor
#: happens to have installed.
BUNDLED_SLUG = "open_house_nodered"

#: The port Node-RED listens on inside that add-on, which is also its
#: `ingress_port`. Nothing publishes it on the host and that is the whole point:
#: a browser reaches the editor through ingress, and Home Assistant reaches the
#: Admin API at `http://<add-on-hostname>:1880` -- the internal name the
#: Supervisor gives the add-on -- so this can never collide with a Node-RED a
#: person already runs on their own 1880.
BUNDLED_PORT = 1880

#: Where the Supervisor answers from inside the Home Assistant container, and the
#: header *core* authenticates with. `X-Hassio-Key` and not `Authorization:
#: Bearer`, which is the header an *add-on* uses; core presents the value of its
#: `SUPERVISOR_TOKEN` as its own key. Both are the Supervisor's convention and
#: both are needed to read one add-on's info from here.
_SUPERVISOR = "http://supervisor"
_SUPERVISOR_HEADER = "X-Hassio-Key"

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


@dataclass(frozen=True)
class Bundled:
    """The Node-RED this repository ships, as one instance reaches it.

    Two addresses for one add-on and they are not the same string, for the
    reason the two options are not either: `push_url` is the internal name the
    Supervisor gives the add-on, which Home Assistant reaches on the Supervisor's
    network and nothing else does, and `editor_url` is the relative ingress path,
    which a browser on the Home Assistant page opens and Home Assistant reaches
    by no other means. A single address would make one of the two wrong.
    """

    push_url: str
    editor_url: str


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


def supervisor_token() -> str:
    """The Supervisor token core carries, or `""` off a supervised install.

    Read from the environment and not through Home Assistant's `hassio`
    integration, because this repository's own dev stack runs Home Assistant as
    a plain container with no Supervisor and no `hassio` component to import --
    and a house that is not supervised has no add-ons at all. `""` is therefore
    the ordinary answer off Home Assistant OS, and it is what makes everything
    below degrade to "no bundled Node-RED" rather than raise: a person on a
    container install configures their own address exactly as they do today.
    """
    return os.environ.get("SUPERVISOR_TOKEN", "").strip()


def bundled_from_info(info: object) -> Bundled | None:
    """The bundled add-on's two addresses, out of a Supervisor `.../info` answer.

    **Pure, so the half of this that is not a network call is a thing a test can
    hold.** The Supervisor answers `{"result": ..., "data": {...}}`, and every
    field read here may be absent, `null`, or of another type -- an add-on that is
    installed but stopped, or one that is not installed at all -- so each is read
    defensively and a missing one means "not this add-on" rather than a crash.

    **Running is part of the question.** A stopped add-on still reports an
    `ingress_url` and still names itself, but there is no editor behind either
    address, and treating a stopped add-on as the house's Node-RED would make the
    integration push to something the person has not started and call it theirs.
    An add-on that was never installed and one that is stopped answer the same
    way here, which is the honest answer to both.
    """
    data = info.get("data") if isinstance(info, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    if str(data.get("state") or "") != "started":
        return None
    hostname = str(data.get("hostname") or "").strip()
    ingress = str(data.get("ingress_url") or "").strip()
    if not hostname or not ingress:
        return None
    return Bundled(
        push_url=f"http://{hostname}:{BUNDLED_PORT}",
        editor_url=ingress,
    )


async def async_bundled(hass: HomeAssistant) -> Bundled | None:
    """The Node-RED this repository ships, when the house is running it.

    **A read and never a start.** This asks the Supervisor about one add-on, by
    the slug this repository chose; it does not install it, start it, or touch it
    in any other way, and an add-on that was never installed answers exactly as a
    stopped one does. So the conflict rule is real rather than intended: a person
    who already runs Node-RED -- the community add-on, or their own -- has it
    reached only through the options they set, and nothing here reaches for the
    community add-on or for any port, because nothing here is looking for one.

    Every failure is `None`: no Supervisor, a refused request, an answer that is
    not the shape above. Not having a bundled Node-RED is the ordinary state of
    every install that is not this one, and a screen must not break over it.
    """
    token = supervisor_token()
    if not token:
        return None
    session = async_get_clientsession(hass)
    url = f"{_SUPERVISOR}/addons/{BUNDLED_SLUG}/info"
    try:
        async with session.get(
            url, headers={_SUPERVISOR_HEADER: token}, timeout=_TIMEOUT_SECONDS
        ) as response:
            if response.status >= 400:
                return None
            answer = await response.json(content_type=None)
    except Exception:
        # Absence is ordinary; see the docstring. A refused connection, a
        # Supervisor that is not there, an answer that is not JSON -- all of them
        # mean the same thing here, and none of them is worth a screen breaking.
        return None
    return bundled_from_info(answer)


async def async_editor_url(hass: HomeAssistant, options: Mapping[str, Any]) -> str:
    """The Node-RED address a *browser* opens, configured or bundled.

    **The person's own address wins whenever there is one**, and only when there
    is not does this fall through to the Node-RED this repository ships. That
    order *is* the conflict rule: a house that already has Node-RED names it in
    the integration's settings and never sees ours, and a house that does not gets
    ours without having to name anything. `""` is still an answer -- neither
    configured nor bundled -- and the row that offers the cast says so.
    """
    configured = editor_url(options)
    if configured:
        return configured
    bundled = await async_bundled(hass)
    return "" if bundled is None else bundled.editor_url


async def async_client(
    hass: HomeAssistant, options: Mapping[str, Any]
) -> NodeRed | None:
    """The Node-RED to push a flow to: the person's, else the bundled one.

    Same precedence as `async_editor_url`, and for the same reason. The bundled
    add-on is reached at its **internal** name -- `http://<hostname>:1880`, out of
    the Supervisor's own answer -- and not at its ingress path, which is the
    browser's route into it and no route at all for an Admin API call. The
    bundled Node-RED sets no `adminAuth` (it is reachable on the Supervisor's
    network and through ingress, and nowhere else), so no token travels with it;
    a token is what the person's own `adminAuth` Node-RED would carry.

    `None` and not an error, because not having a Node-RED is the ordinary state
    of a house that does not use one: the cast is offered and the row says where
    to set the address, which is a thing a person can act on, where an exception
    on a screen they are only looking at is not.
    """
    url = str(options.get(OPTION_URL) or "").strip()
    if url:
        return NodeRed(url=url, token=str(options.get(OPTION_TOKEN) or ""), hass=hass)
    bundled = await async_bundled(hass)
    if bundled is None:
        return None
    return NodeRed(url=bundled.push_url, token="", hass=hass)


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
