"""The live proof that a Node-RED cast is a *closed loop* and not a link.

This is the acceptance check for the fourth kind of answer, and it runs against
the real stack -- a real Home Assistant, a real Node-RED beside it, and the real
Admin API in between. Reading the code proves none of it: the two things that
could be wrong are both about other programs agreeing with this one.

**What it walks.**

1. A module is hosted from `MarqBarq/dynamic-lighting.yaml` with its
   `bypass_light` input bound to a mock light and answered by a **flow**. The
   module is a real automation Home Assistant runs, so nothing here is a
   simulation of the feature.
2. `Node-RED` is asked over its own Admin API what it holds: exactly one tab
   named for this module, whose trigger filters on the light the row resolved to,
   and whose only other node calls `open_house.set_flow_value` with this module
   and this input. That is the wiring, and it is read out of Node-RED rather than
   out of the reply that claims to have written it.
3. **The flow runs by itself the moment it is deployed.** The light is turned
   off and waited for first, so the state it will report is one this probe chose;
   the trigger's `outputInitially` then fires once, and the module's input is
   watched until it reads `off`. That is a module that is answered the instant it
   is hosted rather than one that waits for the light to move -- and, because a
   trigger pushed in an older schema does not deploy at all, it is also what says
   the node Node-RED was handed is one Node-RED accepts.
4. The module's own listing says the input is bound to a **flow** at that entity.
5. **The loop is driven.** The mock light is turned on with Home Assistant's own
   service, and the flow's entity is watched until it reads `on`; then off, and
   watched until it reads `off`. Nothing but Node-RED receiving a state change,
   running a flow, and calling back into Home Assistant can make that happen --
   it is the whole feature in one assertion, and it is the reason this probe
   exists rather than a unit test.
6. The module is unhosted. The flow is gone from Node-RED and the entity is gone
   from Home Assistant: taking the module out takes the wiring with it, and a
   flow left running would go on calling into a module that no longer reads it.
7. **And the same cast on an input that is a number.** `transition_time` resolves
   to no entity, so there is nothing for a state trigger to watch -- and the flow
   pushed for it is the output node alone, with an empty left-hand side for the
   person to build into. That is the case Open House used to refuse, and refusing
   it took the cast away from exactly the inputs worth programming.
8. **The address the panel is given is one a browser can open.** `capabilities`
   carries the editor address to the screens that have no flow yet, and the
   address Home Assistant *pushes* to is a compose service name: a browser that
   followed it would open nothing. Everything else links from a settings row, so
   this is the one place that address is read from anywhere but a row.

Every check prints `ok` or `FAILED` with what it saw, and the exit code is the
number of failures, so this is usable from a script as well as by eye.

Run it with the whole stack up:

    set -a && . ./.env.local && set +a
    docker exec -i -e HA_TOKEN="$HA_TOKEN" open-house-ha \\
        python - < tools/ha/probe_flows.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from typing import Any, cast

import aiohttp

BASE = os.environ.get("HA_URL", "http://127.0.0.1:8123")
SOCKET = (
    BASE.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
)

#: Node-RED's Admin API, as the Home Assistant container reaches it. The compose
#: service name, which is what `tools/ha/node_red.py` puts in the entry's options
#: and what the integration therefore pushes to.
NODE_RED = os.environ.get("NODE_RED_URL", "http://nodered:1880")

#: The same editor, at the address a **browser** opens it at. Different from the
#: one above and it has to be: `nodered` is a compose service name that resolves
#: between the two containers and nowhere else, so a link built from the address
#: Home Assistant pushes to is a link that works for nobody who clicks it. The
#: panel is given this one, and the check below holds it to that.
NODE_RED_EDITOR = os.environ.get("NODE_RED_EDITOR_URL", "http://localhost:1880")

#: The blueprint the module is hosted from, and the mock light its flow watches.
#: `bypass_light` is the input chosen because it takes **one entity** -- the shape
#: a flow needs -- and because the mock fleet's lights can be turned on and off
#: with an ordinary service call, which is what makes step 5 possible at all.
BLUEPRINT = "MarqBarq/dynamic-lighting.yaml"
WATCHED = "light.open_house_mock_fleet_minimal_living_room"
LUX = "sensor.open_house_mock_fleet_minimal_living_room_lux"

#: The title the module is hosted under, and the name that follows from it. The
#: integration derives a module's name from its title, and every entity id in
#: this probe is spelled from that name -- so the two are written here and the
#: dependency between them is stated rather than assumed.
TITLE = "Flow probe"
MODULE = "flow_probe"

#: The entity the flow writes, spelled the way `module_host.flow_entity_id` does.
#: Written out rather than imported for the reason this file runs inside the
#: container: a probe that imported the thing it is checking would agree with a
#: broken spelling.
FLOW_ENTITY = f"sensor.open_house_flow_{MODULE}_bypass_light"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    """Print one check's verdict, remembering a failure rather than raising."""
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f' -- {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def parsed(text: str) -> object:
    """`text` read as JSON, as an opaque value the readers below narrow.

    `json.loads` is typed `Any`, and `Any` narrowed by `isinstance` is
    `dict[Unknown, Unknown]` -- which is every `Unknown` diagnostic this file
    would otherwise raise. Handing it back as `object` makes each read say what
    it expects to find, the same way `tools/ha/node_red.py` does.
    """
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}


def field(value: object, key: str) -> object:
    """`value[key]` when `value` is a mapping that has it, else `None`."""
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value).get(key)


def text_of(value: object, key: str) -> str | None:
    """The string at `key`, or `None` when it is absent or another type."""
    found = field(value, key)
    return found if isinstance(found, str) else None


def mapping(value: object) -> dict[str, Any]:
    """`value` when it is a mapping, else an empty one.

    For the `(reply or {}).get(…)` chains below: an `or {}` on an `Any` gives
    `Any | dict[Unknown, Unknown]`, and it is the *unknown* half of that union
    pyright refuses. This says which of the two it meant.
    """
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def flows() -> list[dict[str, Any]]:
    """Everything Node-RED holds, straight from its Admin API."""
    request = urllib.request.Request(
        f"{NODE_RED.rstrip('/')}/flows", headers={"Node-RED-API-Version": "v2"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        document = parsed(response.read().decode())
    rows = field(document, "flows")
    if not isinstance(rows, list):
        return []
    return [
        cast("dict[str, Any]", row)
        for row in cast("list[object]", rows)
        if isinstance(row, dict)
    ]


def tab_for(flow_id: str) -> list[dict[str, Any]]:
    """Every node on one flow's tab, by the id the record holds, or none.

    Found by the id the *record* holds and not by the one this probe would derive
    for it: Node-RED reassigns a tab's id on `POST /flow` (it preserves node ids
    and mints tab ids), so the derived spelling is a name the tab never has. The
    id in the reply is the only spelling that names it.
    """
    if not flow_id:
        return []
    return [row for row in flows() if row.get("z") == flow_id]


class Home:
    """One authenticated connection to the instance, and the calls it makes."""

    def __init__(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self.socket = socket
        self.next_id = 1

    async def exchange(self, message: dict[str, Any]) -> tuple[bool, Any]:
        """Send one command and answer `(succeeded, result or error)`."""
        for attempt in range(100):
            message = {**message, "id": self.next_id}
            self.next_id += 1
            await self.socket.send_json(message)
            while True:
                answer = parsed(await self.socket.receive_str())
                if field(answer, "id") != message["id"]:
                    continue
                if text_of(answer, "type") == "result" and field(answer, "success"):
                    return True, field(answer, "result")
                error: object = field(answer, "error") or {}
                if field(error, "code") == "not_ready" and attempt < 99:
                    break
                return False, error
            await asyncio.sleep(0.5)
        raise RuntimeError(f"{message['type']} never stopped reloading")

    async def call(self, message: dict[str, Any]) -> Any:
        """Send one command that is expected to work, or raise with the refusal."""
        ok, answer = await self.exchange(message)
        if not ok:
            raise RuntimeError(
                f"{message['type']} refused: {json.dumps(answer, ensure_ascii=False)}"
            )
        return answer

    async def state(self, entity_id: str) -> str | None:
        """One entity's state, or `None` for an entity that is not there."""
        for row in await self.call({"type": "get_states"}):
            if row["entity_id"] == entity_id:
                return str(row["state"])
        return None

    async def until_state(
        self, entity_id: str, wanted: str, timeout: float = 30.0
    ) -> str | None:
        """Wait for an entity to reach a particular state, and answer what it is.

        The node the flow's trigger is *deployed* with `outputinitially` set, so
        the flow writes the input once as soon as it exists -- which means the
        value asked for below may already be there before the service call that
        would produce it. This waits for the state named rather than for a
        *change*, so a check that passes is a check that found the right value
        and not merely a value.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.state(entity_id)
            if state == wanted:
                return state
            await asyncio.sleep(0.5)
        return await self.state(entity_id)

    async def modules(self) -> list[dict[str, Any]]:
        """The house's hosted modules, as the panel reads them."""
        return (await self.call({"type": "open_house/modules/hosted"}))["modules"]

    async def until_absent(self, entity_id: str, timeout: float = 30.0) -> str | None:
        """Wait for an entity to stop being there at all, and answer what it is."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.state(entity_id)
            if state is None:
                return None
            await asyncio.sleep(0.5)
        return await self.state(entity_id)


async def walk(home: Home) -> int:
    """The whole journey, one check at a time."""
    # -- nothing is left over from a run that was interrupted ---------------
    # A flow pushed by an earlier run would make the "exactly one tab" check
    # below pass for the wrong reason, and a module already hosted would make the
    # hosting refuse for a reason that says nothing about this code.
    await home.exchange({"type": "open_house/modules/unhost", "module": MODULE})
    await asyncio.sleep(1.0)

    # -- the address the panel is handed is the browser's, not the house's ----
    # **The two addresses are one bug apart.** `capabilities` is what the screens
    # with no flow on them build a link from -- the import screen, before the flow
    # exists -- and the address Home Assistant *pushes* to is a compose service
    # name that resolves inside this stack and nowhere else. A capabilities
    # answer carrying that one is a link that opens nothing for the person who
    # clicks it, and it is exactly what this check is here to catch: everything
    # else that links to Node-RED takes its address from a settings row, so
    # nothing else would notice.
    capabilities = await home.call({"type": "open_house/capabilities"})
    check(
        "capabilities hands the panel the address a browser can open",
        capabilities["node_red_url"] == NODE_RED_EDITOR,
        capabilities["node_red_url"],
    )

    # -- the source is there, and reads --------------------------------------
    sources = await home.call({"type": "open_house/dev/sources"})
    keys = {row["key"] for row in sources["blueprints"]}
    check(f"{BLUEPRINT} is offered to import", BLUEPRINT in keys, sorted(keys)[:6])

    bindings = {
        "lux_sensor": {"kind": "entity", "value": LUX},
        "weather_entity": {"kind": "literal", "value": "weather.nowhere"},
        "lights": {"kind": "entity", "value": WATCHED},
        # **The row the flow hangs on.** A device, and one device: what Open House
        # hands Node-RED as the input node is this entity, and a flow's trigger
        # watches exactly one.
        "bypass_light": {"kind": "entity", "value": WATCHED},
    }
    reading = await home.call(
        {
            "type": "open_house/modules/read",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "bindings": bindings,
        }
    )
    bypass = next(
        (row for row in reading["inputs"] if row["name"] == "bypass_light"), None
    )
    check(
        "the input the flow answers is an entity row, satisfied by a device",
        bypass is not None
        and bypass["selector"] == "entity"
        and bypass["multiple"] is False
        and bypass["satisfied"] is True,
        bypass,
    )

    # -- host it, with the input answered by a flow --------------------------
    # The light is put **off** first and waited for, so that the state the flow
    # reads on the way up is a state this probe chose rather than the one the last
    # run happened to leave behind. Without that, the check below -- that the flow
    # answers once by itself, before anything changes -- would be asserting
    # something true by luck whenever the light was already off.
    await home.call(
        {
            "type": "call_service",
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": WATCHED},
        }
    )
    check(
        "the watched light starts off, so the flow's first answer is a known value",
        await home.until_state(WATCHED, "off") == "off",
        await home.state(WATCHED),
    )
    listed = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": TITLE,
            "bindings": bindings,
            "outputs": [],
            # The input's row asked for too: binding a flow without asking for
            # the row is a module hosted with an input no one can see, and the
            # check further down reads the row back.
            "settings": ["bypass_light"],
            "flows": ["bypass_light"],
        }
    )
    check(
        "hosting it accepted the flow and named the module",
        listed["module"] == MODULE,
        listed["module"],
    )

    module: dict[str, Any] | None = next(
        (row for row in listed["modules"] if row["slug"] == MODULE), None
    )
    check(
        "the module is listed, with an automation Home Assistant is running",
        module is not None and bool(module["automation_id"]),
        mapping(module).get("automation_id") or module,
    )
    if module is None:
        return len(FAILURES)

    # -- what the reply says about the input ---------------------------------
    flow_row: dict[str, Any] = (
        mapping(mapping(module).get("flows")).get("bypass_light") or {}
    )
    flow_id = str(flow_row.get("flow_id") or "")
    check(
        "the listing says the input is answered by a flow, at the entity it writes",
        flow_row.get("entity_id") == FLOW_ENTITY and bool(flow_id),
        flow_row,
    )
    # The row a person edits. It is bound to the entity the flow writes, and the
    # `flow_id` and the editor link are what the card opens Node-RED with -- so
    # the link is checked here too rather than only in the browser round: this is
    # the server's half of it, and a link built from an empty address is a link
    # that goes nowhere.
    setting: dict[str, Any] | None = next(
        (row for row in module["settings"] if row["name"] == "bypass_light"), None
    )
    # **The link's address is the browser's, not the house's.** The two are
    # different strings for the same editor on this stack, so a link built from
    # the address the push used passes every other check here and still opens
    # nothing for the person who clicks it -- which is the whole reason the
    # second option exists.
    check(
        "and the settings row reads back as flow-bound, with a link to the flow",
        setting is not None
        and setting.get("bound_kind") == "flow"
        and setting.get("bound_to") == FLOW_ENTITY
        and setting.get("flow_id") == flow_id
        and setting.get("flow_url") == f"{NODE_RED_EDITOR}/#flow/{flow_id}",
        mapping(setting).get("flow_url") or setting,
    )

    # -- what Node-RED actually holds ----------------------------------------
    nodes = tab_for(flow_id)
    check(
        "Node-RED holds a tab for this module and this input", bool(nodes), len(nodes)
    )
    trigger: dict[str, Any] | None = next(
        (row for row in nodes if row["type"] == "server-state-changed"), None
    )
    service: dict[str, Any] | None = next(
        (row for row in nodes if row["type"] == "api-call-service"), None
    )
    # Read out of the node the way the palette's *current* schema spells it --
    # `entities.entity`, not the `entityidfilter` of the versions before it. The
    # two are the same fact in two shapes, and reading the old one here would be
    # a probe that passes against a node Node-RED refuses to deploy.
    watched = mapping(mapping(trigger).get("entities")).get("entity")
    check(
        "its trigger watches the entity the row resolved to",
        trigger is not None and watched == [WATCHED],
        watched,
    )
    check(
        "its other node writes this module's input back into Home Assistant",
        service is not None
        and service.get("action") == "open_house.set_flow_value"
        and f'"module": "{MODULE}"' in str(service.get("data") or "")
        and '"input": "bypass_light"' in str(service.get("data") or ""),
        mapping(service).get("data"),
    )
    check(
        "and the two are wired to each other, so the flow runs with nothing added",
        trigger is not None
        and service is not None
        and trigger.get("wires") == [[service.get("id")]],
        mapping(trigger).get("wires"),
    )

    # -- the entity the flow writes ------------------------------------------
    # **The flow answers on its own, without anything changing.** The pushed
    # trigger is deployed with `outputInitially` set, so it fires once as soon as
    # the tab lands and the light's current state travels all the way back into
    # the module's input. That is a module that reads correctly the moment it is
    # hosted rather than sitting unanswered until something moves -- and it is
    # also the proof that the trigger deployed at all, which is the thing a node
    # in an older schema fails to do.
    check(
        "the flow ran once by itself and wrote the light's state",
        await home.until_state(FLOW_ENTITY, "off", timeout=45.0) == "off",
        await home.state(FLOW_ENTITY),
    )

    # -- **the loop, driven** -------------------------------------------------
    # The one check that is the feature. Home Assistant is asked to turn the
    # watched light on, the mock fleet actuates it, Node-RED's trigger fires and
    # its service node calls back into Home Assistant, and the value lands in the
    # entity the module's input reads. No single step of that is this
    # integration's; the wiring is, and this is what says it is wired.
    await home.call(
        {
            "type": "call_service",
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": WATCHED},
        }
    )
    check(
        "the watched light came on",
        await home.until_state(WATCHED, "on") == "on",
        await home.state(WATCHED),
    )
    on = await home.until_state(FLOW_ENTITY, "on", timeout=45.0)
    check("the flow ran and wrote the module's input", on == "on", on)

    await home.call(
        {
            "type": "call_service",
            "domain": "light",
            "service": "turn_off",
            "service_data": {"entity_id": WATCHED},
        }
    )
    off = await home.until_state(FLOW_ENTITY, "off", timeout=45.0)
    check("and it writes again, following the light back down", off == "off", off)

    # -- taking the module out takes the wiring with it ----------------------
    await home.call({"type": "open_house/modules/unhost", "module": MODULE})
    await asyncio.sleep(2.0)
    check("unhosting takes the flow out of Node-RED", tab_for(flow_id) == [])
    check(
        "and the entity the flow wrote with it",
        await home.until_absent(FLOW_ENTITY) is None,
        await home.state(FLOW_ENTITY),
    )

    # -- **the other half: an input a trigger cannot be built for** -----------
    # `transition_time` takes a *number*, so the row it sits on resolves to no
    # entity and there is nothing for a state trigger to watch. The flow Open
    # House pushes for it is the output node alone, with an empty left-hand side
    # for the person to build into -- which is exactly "add an input node and do
    # anything with the data in there". This is the case the cast used to be
    # refused for, on the reasoning that a flow that could not be wired end to end
    # was not worth offering; the person who wanted it disagreed, and was right.
    number_bindings = {
        **bindings,
        "transition_time": {"kind": "literal", "value": 3},
    }
    listed = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": TITLE,
            "bindings": number_bindings,
            "outputs": [],
            "settings": ["transition_time"],
            "flows": ["transition_time"],
        }
    )
    module = next((row for row in listed["modules"] if row["slug"] == MODULE), None)
    bare_row: dict[str, Any] = (
        mapping(mapping(module).get("flows")).get("transition_time") or {}
    )
    bare_id = str(bare_row.get("flow_id") or "")
    check(
        "a flow is accepted on an input that is a number and not a device",
        module is not None and bool(bare_id),
        bare_row or module,
    )
    bare_nodes = tab_for(bare_id)
    check(
        "and its flow is the output node alone, with nothing pretending to trigger it",
        [node["type"] for node in bare_nodes] == ["api-call-service"],
        [node["type"] for node in bare_nodes],
    )
    check(
        "and the settings row says what the input reads, so it is not left unexplained",
        any(
            row["name"] == "transition_time"
            and row.get("bound_kind") == "flow"
            and row.get("flow_url") == f"{NODE_RED_EDITOR}/#flow/{bare_id}"
            for row in mapping(module).get("settings", [])
        ),
        [
            row
            for row in mapping(module).get("settings", [])
            if row["name"] == "transition_time"
        ],
    )
    await home.call({"type": "open_house/modules/unhost", "module": MODULE})
    return len(FAILURES)


async def main() -> int:
    token = os.environ.get("HA_TOKEN", "")
    if not token:
        print("HA_TOKEN is not set; see this file's docstring", file=sys.stderr)
        return 2

    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(SOCKET, max_msg_size=0) as socket,
    ):
        hello = parsed(await socket.receive_str())
        if text_of(hello, "type") != "auth_required":
            print(f"unexpected greeting: {hello}", file=sys.stderr)
            return 2
        await socket.send_json({"type": "auth", "access_token": token})
        answer = parsed(await socket.receive_str())
        if text_of(answer, "type") != "auth_ok":
            print(f"authentication refused: {answer}", file=sys.stderr)
            return 2
        return await walk(Home(socket))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
