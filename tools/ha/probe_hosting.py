"""The live proof that importing a blueprint really becomes a working module.

This is the acceptance check for hosting, and it runs against a real Home
Assistant rather than against the pure simulator -- because the two things it
proves are exactly the two things the simulator cannot: that Home Assistant
accepts the automation a module builds, and that a value one module publishes is
read by another module *through Home Assistant's own template engine*.

**It runs inside the container.** The websocket API is the only door to the
`open_house/*` commands, and the checkout's virtualenv has no websocket client
(and is not meant to grow one for a test). Home Assistant's image has `aiohttp`,
so the script is piped in rather than installed:

    set -a && . ./.env.local && set +a
    docker exec -i -e HA_TOKEN="$HA_TOKEN" open-house-ha \\
        python - < tools/ha/probe_hosting.py

**What it walks.** Two modules from one blueprint, and the value that crosses
between them:

1. `MarqBarq/dynamic-lighting.yaml` is read as something to host. Its inputs are
   bound to the mock fleet's own entities, and two of its computed variables --
   `brightness` and `color_temp_kelvin` -- are ticked as outputs.
2. It is hosted, and the automation Home Assistant created is looked for by name
   before anything else happens: a module that is not running is not a module.
3. It is triggered, and its `brightness` output is watched until it publishes.
   The value is not asserted to be any particular number -- it is computed from
   the mock lux sensor -- only that *something* arrived.
4. The same blueprint is hosted a second time, with `min_lux_clear` -- a numeric
   input -- bound to the first module's output. That is the dataflow: a value
   that was private to one automation's run is now another automation's input.
5. The second module is triggered, and its `min_lux` output is compared with the
   first module's `brightness`. They must be equal, because the second module
   computes `min_lux` from `min_lux_clear`, which is a template reading the first
   module's entity. Nothing else could make them agree.
6. One of the blueprint's own inputs -- `max_brightness_percent` -- was ticked to
   keep as a *setting*, and one was not. The listing is checked for both, the
   kept one is changed to 50, and the module is checked to have been rebuilt
   rather than replaced: same automation, same output entities, one entry in the
   file, and the new number inside the automation Home Assistant is running.
7. The automation file is read back and checked for the thing that would be
   invisible from the outside: that the blueprint's own actions are still there,
   and that the publisher is the *last* step rather than a replacement for any of
   them.
8. The same module holds a second configuration -- a copy of the first -- and is
   switched between them. The proof is the automation file again: the ceiling in
   the document Home Assistant is running moves from 50 to 25 and back, in the
   same entry, with the same output entities. The names are renamed and dropped,
   the ones a module is not running are read out of `open_house/modules.json`,
   and dropping the last one is refused.

Every check prints `ok` or `FAILED` with what it saw, and the exit code is the
number of failures, so this is usable from a script as well as by eye.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import aiohttp

BASE = os.environ.get("HA_URL", "http://127.0.0.1:8123")
SOCKET = (
    BASE.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
)

#: The blueprint both modules are made from. It is already in the instance's
#: `blueprints/automation/` -- put there by the repository, so a fresh container
#: has it without anyone importing anything by hand.
BLUEPRINT = "MarqBarq/dynamic-lighting.yaml"

#: The other blueprint the instance carries, read here for the one thing it says
#: that the first does not: `default: device_tracker.me` on an input that takes a
#: device. That is an author's placeholder rather than a device this installation
#: has, and the reading below is the live proof it is not taken for an answer.
OTHER = "gist.githubusercontent.com/advanced_circadian_lighting.yaml"

#: The mock fleet's living room, which is what the inputs are pointed at. The
#: lux sensor is the one reading the blueprint computes from; the two lights are
#: the targets, and they are different lights so the two modules are visibly two
#: modules rather than one hosted twice.
LUX = "sensor.open_house_mock_fleet_minimal_living_room_lux"
LIGHT_A = "light.open_house_mock_fleet_minimal_living_room"
LIGHT_B = "light.open_house_mock_fleet_demo_bedroom"

#: The house's own slot word for "the lights" -- what a module names instead of a
#: device. It is the catalog's, not a pack's, so every house with a catalog has
#: it; a word invented at the import screen is refused, because nothing would ever
#: bind it and the module would wait forever.
LIGHTS = "light_group"

#: A weather entity this instance does not have. The blueprint only lowercases
#: its state, and an absent entity reads `unknown`, which is not one of the
#: cloudy words -- so the blueprint takes its clear-weather branch and computes a
#: real number, which is what this probe needs. Binding it to a literal is also
#: the one way to fill an entity input when no device is there to point at.
NO_WEATHER = "weather.nowhere"

#: Home Assistant's own editor file, inside the container. Two checks read it:
#: the document checks at the end, and the identity check around a settings
#: change -- which is the thing the outside cannot show, because a rebuild that
#: quietly added a second automation would look identical over the websocket.
AUTOMATIONS = Path("/config/automations.yaml")

#: The file the integration keeps the placed modules in, beside the house's own
#: directory. Read once, for the one thing the websocket cannot show: that the
#: configurations a module is not running are *in the file* rather than in the
#: reply -- a reply could list a name whose answers were lost on the way out.
RECORDS = Path("/config/open_house/modules.json")

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    """Print one check's verdict, remembering a failure rather than raising.

    `detail` is whatever did not match -- a list, a mapping, a number -- rather
    than a string, because the verdict line is read by a person looking at a
    failure and the whole value is the answer they want.
    """
    print(f"{'ok  ' if ok else 'FAIL'} {label}{f' -- {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(label)


def parsed(text: str) -> object:
    """`text` read as JSON, as an opaque value the readers below narrow.

    `json.loads` is typed `Any`, and an `Any` narrowed by `isinstance` or by
    `or {}` is `dict[Unknown, Unknown]` -- which is every `Unknown` diagnostic
    this file would otherwise raise. Handing it back as `object` makes each read
    say what it expects to find, the same way `tools/ha/node_red.py` does.
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


def items(value: object) -> list[Any]:
    """`value` when it is a list, else an empty one."""
    return cast("list[Any]", value) if isinstance(value, list) else []


def _automations() -> list[dict[str, Any]]:
    """The entries in Home Assistant's own automation file, or none if it is absent."""
    if not AUTOMATIONS.is_file():
        return []
    import yaml

    with AUTOMATIONS.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or []


def _records() -> dict[str, dict[str, Any]]:
    """The modules the integration has written down, by slug.

    Absent is an empty mapping rather than a failure: every check that reads
    this runs after a command that wrote it, and the check's own verdict is the
    thing worth printing, not a traceback about the file.
    """
    try:
        with RECORDS.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for item in items(mapping(document).get("modules")):
        row = mapping(item)
        if isinstance(item, dict):
            found[str(row.get("slug"))] = row
    return found


def _holds(node: object, key: str, value: object) -> bool:
    """Whether `key: value` appears anywhere inside a loaded YAML document.

    A blueprint's numbers are substituted into the action's own `variables:` --
    nested inside `choose` branches as often as not -- so the check that a
    setting reached the automation has to look where the document put it rather
    than at any one place.
    """
    if isinstance(node, dict):
        held = cast("dict[str, object]", node)
        if held.get(key) == value:
            return True
        return any(_holds(item, key, value) for item in held.values())
    if isinstance(node, list):
        return any(_holds(item, key, value) for item in cast("list[object]", node))
    return False


def _steps(node: object) -> list[Any]:
    """One action list, however the document spelled it."""
    if isinstance(node, list):
        return cast("list[Any]", node)
    return [node] if node is not None else []


def _publishes(entry: dict[str, Any], module: str) -> bool:
    """Whether this entry is the automation that carries `module`'s publisher.

    The publisher step is how an entry says which module it belongs to from the
    outside: it is the only line the module added to the blueprint's document,
    and it names the module on it.
    """
    actions = _steps(entry.get("action") or entry.get("actions"))
    return any(
        text_of(step, "service") == "open_house.publish_output"
        and mapping(field(step, "data")).get("module") == module
        for step in actions
    )


def _nothing(state: str | None) -> bool:
    """Whether a state says no value has ever been published into it.

    `None` is an entity Home Assistant does not have at all and `"unknown"` is
    one it has with no value. Both are "nothing has been published", and the
    difference between them is whether the entity has been made yet -- not
    whether anyone wrote to it.
    """
    return state is None or state == "unknown"


def _entry_for(module: str) -> dict[str, Any] | None:
    """The automation file's entry for one module, or `None` if it has none."""
    return next((row for row in _automations() if _publishes(row, module)), None)


class Home:
    """One authenticated connection to the instance, and the calls it makes."""

    def __init__(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        self.socket = socket
        self.next_id = 1

    async def exchange(self, message: dict[str, Any]) -> tuple[bool, Any]:
        """Send one command and answer `(succeeded, result or error)`.

        The one place a message is sent, so the three readings of an answer below
        -- `call`, `refusable` and `maybe` -- differ only in what they do with it
        rather than in how they wait for it.

        **`not_ready` is waited out rather than answered.** Every command that
        changes the house rebuilds it, and the host refuses the next command while
        that is happening -- which is the correct behaviour for a person clicking
        two things at once and the wrong answer for a script that has just made a
        change it now wants to read back. There is nothing to distinguish here:
        the refusal says when to ask again, and asking again is exactly what
        waiting means.
        """
        for attempt in range(100):
            message = {**message, "id": self.next_id}
            self.next_id += 1
            await self.socket.send_json(message)
            while True:
                answer = parsed(await self.socket.receive_str())
                if field(answer, "id") != message["id"]:
                    # A `result` from an earlier command, or a pushed event: this
                    # connection answers in order, so anything else is not ours.
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

    async def refusable(self, message: dict[str, Any]) -> str:
        """Send one command expecting a refusal, and answer what it said.

        The other half of `call`, for the checks that are about a house saying
        no: a refusal is an answer here, not a crash. It is a separate method
        rather than a flag on `call` so that a command expected to *succeed* and
        silently refused cannot be mistaken for one that worked.
        """
        ok, answer = await self.exchange(message)
        if ok:
            raise RuntimeError(
                f"{message['type']} was expected to be refused and was not"
            )
        return str(answer.get("message") or answer)

    async def maybe(self, message: dict[str, Any]) -> Any:
        """Send one command that may legitimately be refused, and answer either way.

        For the commands a second run of this probe finds already in the state the
        first one left them -- taking a definition away when there may not be one.
        It answers the result, or the refusal's message as a string, and the caller
        says which of those it was willing to get.
        """
        ok, answer = await self.exchange(message)
        return answer if ok else str(answer.get("message") or answer)

    async def state(self, entity_id: str) -> str | None:
        """One entity's state, or `None` for an entity that is not there."""
        for row in await self.call({"type": "get_states"}):
            if row["entity_id"] == entity_id:
                return str(row["state"])
        return None

    async def until(self, entity_id: str, timeout: float = 20.0) -> str | None:
        """Wait for an entity to stop being unknown, and answer what it said.

        An output whose producer has not run is `unknown` -- the sensor exists
        from the moment the module is hosted, and its state only becomes a
        reading when the automation's publisher first runs.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.state(entity_id)
            if state not in (None, "unknown", "unavailable"):
                return state
            await asyncio.sleep(0.5)
        return None

    async def until_not(
        self, entity_id: str, old: str, timeout: float = 25.0
    ) -> str | None:
        """Wait for an entity to take a *different* value from the one it has.

        `until` answers the same question about a module that has never run;
        this is the one about a module that has -- after a setting is changed
        and the module runs again, the value it published last time is still
        there until the new one arrives, so waiting for "not unknown" would
        return immediately and prove nothing.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.state(entity_id)
            if (
                state is not None
                and state not in ("unknown", "unavailable")
                and state != old
            ):
                return state
            await asyncio.sleep(0.5)
        return None

    async def until_state(
        self, entity_id: str, wanted: str, timeout: float = 25.0
    ) -> str | None:
        """Wait for an entity to reach a particular state, and answer what it is.

        A derived sensor is re-evaluated when the thing it reads changes and on
        its own tick, so the new answer is not there the instant the condition is
        changed -- `until` would return the *old* reading and prove nothing,
        because both readings are real states. This waits for the one asked for.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            state = await self.state(entity_id)
            if state == wanted:
                return state
            await asyncio.sleep(0.5)
        return await self.state(entity_id)

    async def settings(self, module: str) -> dict[str, dict[str, Any]]:
        """One module's settings rows, keyed by input name.

        Read off `modules/hosted` rather than `modules/settings`: that command
        answers with the house, and the rows a screen draws come with it.
        """
        hosted = (await self.call({"type": "open_house/modules/hosted"}))["modules"]
        for row in hosted:
            if row["slug"] == module:
                return {item["name"]: item for item in row["settings"]}
        return {}

    async def trigger(self, automation: str) -> None:
        """Run an automation now, without waiting for its trigger to happen."""
        await self.call(
            {
                "type": "call_service",
                "domain": "automation",
                "service": "trigger",
                "service_data": {"entity_id": automation, "skip_condition": True},
            }
        )


async def main() -> int:
    token = os.environ.get("HA_TOKEN", "")
    if not token:
        print("HA_TOKEN is not set; see this file's docstring", file=sys.stderr)
        return 2

    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(SOCKET, max_msg_size=0) as socket,
    ):
        hello = json.loads(await socket.receive_str())
        if hello.get("type") != "auth_required":
            print(f"unexpected greeting: {hello}", file=sys.stderr)
            return 2
        await socket.send_json({"type": "auth", "access_token": token})
        answer = json.loads(await socket.receive_str())
        if answer.get("type") != "auth_ok":
            print(f"authentication refused: {answer}", file=sys.stderr)
            return 2
        home = Home(socket)
        return await walk(home)


async def walk(home: Home) -> int:
    """The whole journey, one check at a time."""
    # -- the source is there, and it reads ---------------------------------
    sources = await home.call({"type": "open_house/dev/sources"})
    keys = {row["key"] for row in sources["blueprints"]}
    check(f"{BLUEPRINT} is offered to import", BLUEPRINT in keys, sorted(keys))

    producer_inputs = {
        "lux_sensor": {"kind": "entity", "value": LUX},
        "weather_entity": {"kind": "literal", "value": NO_WEATHER},
        "lights": {"kind": "entity", "value": LIGHT_A},
        # The blueprint's ceiling on how bright the light may go. The blueprint
        # names the *input* `max_brightness_percent` and copies it into a
        # variable called `..._value`; the binding is by input name.
        "max_brightness_percent": {"kind": "literal", "value": 100},
    }
    reading = await home.call(
        {
            "type": "open_house/modules/read",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "bindings": producer_inputs,
        }
    )
    candidates = {row["name"]: row for row in reading["candidates"]}
    check(
        "the blueprint's own variables are offered as outputs",
        {"brightness", "color_temp_kelvin"} <= set(candidates),
        sorted(candidates)[:12],
    )
    # And what it *does*: every call that drives the lights is offered as the
    # devices it acted on and as each value it set on them. This is the half of
    # the list that is not a name in the document -- `{{ brightness | int }}` is
    # what the light was driven to, and nothing in the blueprint calls it that.
    driven = candidates.get("service:light.turn_on:brightness")
    check(
        "a value a service call sets is offered as an output",
        driven is not None
        and driven["expression"] == "{{ brightness | int }}"
        and driven["branch_only"] is True,
        driven or sorted(candidates)[:12],
    )
    check(
        "and the devices a call acts on, resolved to the entities bound",
        candidates.get("service:light.turn_on", {}).get("expression") == LIGHT_A,
        candidates.get("service:light.turn_on", {}).get("expression"),
    )
    check(
        "an input with no default is reported unsatisfied until it is filled",
        all(row["satisfied"] for row in reading["inputs"]),
        [row["name"] for row in reading["inputs"] if not row["satisfied"]],
    )

    # -- a named device is not an answer, whichever blueprint wrote it --------
    # The other blueprint in the instance is the case this rule was written for:
    # it declares `default: device_tracker.me` on an input that takes a device,
    # which is the author saying where the field goes and not a device this
    # installation has. Read with no bindings of its own -- this is a reading,
    # nothing is hosted by it -- the two rows below are the whole of the rule:
    # one names a device and is not answered by it, the other is an empty default
    # and still is, because an optional device input the blueprint is written to
    # run without must not hold an import back.
    circ = await home.call(
        {"type": "open_house/modules/read", "kind": "blueprint", "key": OTHER}
    )
    rows = {row["name"]: row for row in circ["inputs"]}
    check(
        "a default that names a device is not an answer for one",
        all(
            rows.get(name, {}).get("selector") == "entity"
            and rows[name].get("satisfied") is False
            and rows[name].get("has_default") is False
            and rows[name].get("default") is None
            for name in ("presence_entity", "sleep_entity")
        ),
        {name: rows.get(name) for name in ("presence_entity", "sleep_entity")},
    )
    # `bypass_light` is the other half: an entity input whose default names
    # nothing, unbound above and reported satisfied -- an "(Optional)" device the
    # blueprint is written to run without must not hold the import back.
    bypass = next(row for row in reading["inputs"] if row["name"] == "bypass_light")
    check(
        "and a default that names nothing is still an answer",
        bypass["has_default"] and bypass["satisfied"] and bypass["default"] is None,
        bypass,
    )
    # **Which inputs a cast cannot be written over**, read off a blueprint that
    # has both kinds, and this is the file a person hit it with. It watches the
    # sleep, presence and light entities -- they are trigger `entity_id`s, the
    # fields Home Assistant matches against real entities rather than rendering,
    # so a condition written over one is compared as text and matches nothing,
    # while the automation installs and never fires and nothing anywhere says
    # why. The sun *elevations* are the same trigger's `above:`/`below:`, which
    # are rendered like anything else, and the temperatures reach a service call
    # -- so those can be cast, and reporting the fact per input is what lets the
    # screen take away one answer without taking away the rest.
    check(
        "the inputs a trigger watches are told apart from the ones it only reads",
        all(
            rows[n].get("in_trigger") is True
            for n in ("presence_entity", "sleep_entity", "lights_entities")
        )
        and all(
            rows.get(n, {}).get("in_trigger") is False
            for n in ("elevation_sunrise_start", "circadian_temperature_midday")
        ),
        {
            n: rows[n].get("in_trigger")
            for n in sorted(rows)
            if n in ("presence_entity", "sleep_entity", "lights_entities")
            or rows[n].get("in_trigger") is True
        },
    )

    # -- what an import of that blueprint actually becomes -------------------
    # The journey of the bug this rule was written for, end to end: it is hosted,
    # and the module does not come out pointed at `device_tracker.me`. It comes
    # out *waiting* -- not running, with the device inputs it has no answer for
    # named as things to set on it, which is what a person can act on. Nothing is
    # bound here, so this is the shallowest an import can be: the reading above
    # is what says the rest.
    circ_hosted = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": OTHER,
            "title": "Circadian",
            "bindings": {},
        }
    )
    circ_slug = circ_hosted["module"]
    circ_row = next(row for row in circ_hosted["modules"] if row["slug"] == circ_slug)
    waiting_for = {
        row["name"]
        for row in circ_row["settings"]
        if not row["has_default"] and not row["bound"]
    }
    check(
        "an imported module is not pointed at the author's own device",
        circ_row["automation_id"] == ""
        and "device_tracker.me" not in json.dumps(circ_row["settings"]),
        circ_row["automation_id"],
    )
    check(
        "it waits instead, naming the device inputs as options to set",
        {"presence_entity", "sleep_entity"} <= waiting_for,
        sorted(waiting_for),
    )
    await home.call({"type": "open_house/modules/unhost", "module": circ_slug})

    # -- host the producer, and prove it is running -------------------------
    #: Whether this output is new to the instance, read before anything is
    #: hosted. The check below is about an output nothing has *ever* published,
    #: and an output restored from an earlier run of this same probe is not that
    #: -- it is a module that has already run, which is the state the sensor's
    #: own restore is meant to preserve. Asking before hosting is what tells the
    #: two apart, so a second run reports the check rather than failing it.
    new_instance = (
        await home.state("sensor.open_house_dynamic_lighting_brightness") is None
    )
    hosted = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Dynamic Lighting",
            "bindings": producer_inputs,
            "outputs": [
                {"name": "brightness", "key": "brightness"},
                {"name": "color_temp_kelvin", "key": "color_temp_kelvin"},
                # The value the `light.turn_on` call set, which is the same number
                # as `brightness` and comes from a different place: the call's own
                # data, published beside the call rather than at the end.
                {"name": "service:light.turn_on:brightness", "key": "set_brightness"},
            ],
            # The blueprint's own setting for how bright the light may go, filled
            # by hand and kept as a *setting* -- the dial this module offers
            # afterwards, with the value the person gave it as where it starts.
            # `lights` and `lux_sensor` above are answered and then fixed: the
            # difference between the two lists is the whole of what a person
            # decides when they import.
            "settings": ["max_brightness_percent"],
        }
    )
    slug = hosted["module"]
    check("the module is hosted under a slug", slug == "dynamic_lighting", slug)
    record = next(row for row in hosted["modules"] if row["slug"] == slug)
    automation = record["automation_id"]
    check(
        "Home Assistant is running it as an automation",
        automation.startswith("automation."),
        automation,
    )
    check(
        "its outputs are real entities",
        [row["entity_id"] for row in record["outputs"]]
        == [
            "sensor.open_house_dynamic_lighting_brightness",
            "sensor.open_house_dynamic_lighting_color_temp_kelvin",
            "sensor.open_house_dynamic_lighting_set_brightness",
        ],
        [row["entity_id"] for row in record["outputs"]],
    )
    check(
        "an output nothing has published yet reads as unknown",
        not new_instance
        or await home.state("sensor.open_house_dynamic_lighting_brightness")
        in ("unknown", None),
        "" if new_instance else "not a fresh instance: already restored a value",
    )

    # -- run it, and watch what it publishes --------------------------------
    await home.trigger(automation)
    brightness = await home.until("sensor.open_house_dynamic_lighting_brightness")
    check(
        "the automation runs and publishes its brightness output",
        brightness is not None,
        f"brightness={brightness}",
    )
    colour = await home.state("sensor.open_house_dynamic_lighting_color_temp_kelvin")
    check(
        "the second output published too, from the same run",
        colour not in (None, "unknown"),
        f"color_temp_kelvin={colour}",
    )

    # The output read from the call itself. It is published only on the runs that
    # take the branch the call is in -- the blueprint turns the lights *on* in the
    # `choose` element's `default` arm -- so a run that turned them off instead
    # leaves it unknown, and that is a report rather than a failure. On a run that
    # took the arm it is the number the light was driven to, which is what makes
    # it a reading rather than a copy of `brightness`: both are the same value
    # computed once and published from two places in the tree.
    set_brightness = await home.state(
        "sensor.open_house_dynamic_lighting_set_brightness"
    )
    check(
        "the value the call set is published beside the call",
        _nothing(set_brightness) or set_brightness == brightness,
        f"set_brightness={set_brightness} brightness={brightness}",
    )

    # -- host a consumer, with an input bound to the producer's output ------
    consumer_inputs = {
        "lux_sensor": {"kind": "entity", "value": LUX},
        "weather_entity": {"kind": "literal", "value": NO_WEATHER},
        "lights": {"kind": "entity", "value": LIGHT_B},
        "min_lux_clear": {"kind": "output", "module": slug, "key": "brightness"},
    }
    second = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Dynamic Lighting Bedroom",
            "bindings": consumer_inputs,
            "outputs": [{"name": "min_lux", "key": "min_lux"}],
        }
    )
    bedroom = second["module"]
    check(
        "a second module hosts from the same blueprint",
        bedroom == "dynamic_lighting_bedroom",
        bedroom,
    )
    consumer = next(row for row in second["modules"] if row["slug"] == bedroom)
    filled = {row["name"]: row["value"] for row in consumer["inputs"]}
    check(
        "its numeric input holds the template that reads the other module",
        isinstance(filled.get("min_lux_clear"), str)
        and "sensor.open_house_dynamic_lighting_brightness" in filled["min_lux_clear"],
        f"min_lux_clear={filled.get('min_lux_clear')!r}",
    )

    await home.trigger(consumer["automation_id"])
    min_lux = await home.until(f"sensor.open_house_{bedroom}_min_lux")
    check(
        "the consumer runs and publishes its own output",
        min_lux is not None,
        f"min_lux={min_lux}",
    )
    if brightness is not None and min_lux is not None:
        check(
            "what it published is what the other module published",
            float(min_lux) == float(brightness),
            f"min_lux={min_lux} brightness={brightness}",
        )

    # -- the blueprint's settings: which are kept, and changing one ---------
    rows = {row["name"]: row for row in record["settings"]}
    check(
        "the input the creator ticked to keep is offered as a setting",
        "max_brightness_percent" in rows,
        sorted(rows),
    )
    check(
        "an input they answered and did not tick is not one",
        "lux_sensor" not in rows and "lights" not in rows,
        sorted(rows),
    )
    check(
        "and a kept setting starts at the value the creator gave it",
        str(rows.get("max_brightness_percent", {}).get("value")) == "100",
        f"value={rows.get('max_brightness_percent', {}).get('value')!r}",
    )

    ids_before = [row.get("id") for row in _automations()]
    # What changed is the *value* the creator gave the setting, so it travels as
    # a binding -- the same row the import screen filled the first time. The
    # module does not grow a second way to say "answer this input differently".
    changed = await home.call(
        {
            "type": "open_house/modules/settings",
            "module": slug,
            "bindings": {"max_brightness_percent": {"kind": "literal", "value": 50}},
        }
    )
    rebuilt = next(row for row in changed["modules"] if row["slug"] == slug)
    check(
        "changing a setting keeps the module, its automation and its outputs",
        rebuilt["automation_id"] == automation
        and [row["entity_id"] for row in rebuilt["outputs"]]
        == [row["entity_id"] for row in record["outputs"]],
        f"{rebuilt['automation_id']} vs {automation}",
    )
    answers = {row["name"]: row["value"] for row in rebuilt["inputs"]}
    check(
        "and the module now carries the value it was given",
        str(answers.get("max_brightness_percent")) == "50",
        f"max_brightness_percent={answers.get('max_brightness_percent')!r}",
    )
    entry = _entry_for(slug)
    held = entry is not None and _holds(entry, "max_brightness_percent_value", 50)
    check(
        "the automation Home Assistant runs was rebuilt with the new value",
        held,
        "" if held else f"the entry for {slug} does not carry 50",
    )
    ids_after = [row.get("id") for row in _automations()]
    check(
        "and the rebuild replaced its own entry rather than adding one",
        ids_after == ids_before,
        f"{ids_before} -> {ids_after}",
    )

    await home.trigger(automation)
    # The value the ceiling bounds, read again after the module ran with the new
    # one. Which branch of the blueprint's own `if` produced it is the lux
    # sensor's business, and `brightness` is `min_brightness` whenever the room
    # is already bright -- so what is asked is the thing that holds in every
    # branch: a lower ceiling cannot produce a brighter value.
    if brightness is None:
        # Nothing was published, so every reading below would be a comparison
        # against nothing. The check above has already recorded that, and it is
        # the one sentence worth printing about it.
        return len(FAILURES)
    lowered = await home.until_not(
        "sensor.open_house_dynamic_lighting_brightness", brightness, timeout=15.0
    )
    if lowered is None:
        lowered = await home.state("sensor.open_house_dynamic_lighting_brightness")
    check(
        "the module still runs, and the lower ceiling did not brighten it",
        lowered is not None and float(lowered) <= float(brightness),
        f"{brightness} -> {lowered} (the ceiling moved, the room did not)",
    )

    # -- several configurations of one module -------------------------------
    # Everything so far has been one set of answers. This is the same module
    # holding two and being *switched* between them, and the proof is the one the
    # settings change above used, because it is the only thing a screen cannot
    # produce: the ceiling in the document Home Assistant is actually running. A
    # switch that moved a name in a file and nothing else would leave 50 in there.
    added = await home.call(
        {
            "type": "open_house/modules/configs/add",
            "module": slug,
            "config": "Evening",
        }
    )
    row = next(one for one in added["modules"] if one["slug"] == slug)
    check(
        "a module can hold a second configuration, copied from the running one",
        row["config"] == "Evening" and row["configs"] == ["Default", "Evening"],
        f"config={row['config']!r} configs={row['configs']!r}",
    )
    check(
        "and it starts as the answers it was copied from",
        str(
            {one["name"]: one["value"] for one in row["inputs"]}.get(
                "max_brightness_percent"
            )
        )
        == "50",
        {one["name"]: one["value"] for one in row["inputs"]}.get(
            "max_brightness_percent"
        ),
    )

    # The second configuration answers one input differently, through the
    # ordinary settings command -- a setting travels as a binding, the same row
    # the import screen fills. What makes it interesting is which configuration
    # it lands in, and that is only visible once the module has been switched.
    await home.call(
        {
            "type": "open_house/modules/settings",
            "module": slug,
            "bindings": {"max_brightness_percent": {"kind": "literal", "value": 25}},
        }
    )
    entry = _entry_for(slug)
    check(
        "an edit made while a configuration is running lands in that one",
        entry is not None and _holds(entry, "max_brightness_percent_value", 25),
        "" if entry is not None else f"no entry for {slug}",
    )

    ids_before = [one.get("id") for one in _automations()]
    switched = await home.call(
        {
            "type": "open_house/modules/configs/switch",
            "module": slug,
            "config": "Default",
        }
    )
    row = next(one for one in switched["modules"] if one["slug"] == slug)
    entry = _entry_for(slug)
    check(
        "switching to a configuration rebuilds the module into it",
        entry is not None and _holds(entry, "max_brightness_percent_value", 50),
        "" if entry is not None else f"no entry for {slug}",
    )
    check(
        "into the same automation and the same outputs, not a second module",
        row["automation_id"] == automation
        and [one["entity_id"] for one in row["outputs"]]
        == [one["entity_id"] for one in record["outputs"]]
        and [one.get("id") for one in _automations()] == ids_before,
        f"{row['automation_id']} vs {automation}; {ids_before}",
    )
    check(
        "and the module is now running the configuration it was switched to",
        row["config"] == "Default" and row["configs"] == ["Default", "Evening"],
        f"config={row['config']!r} configs={row['configs']!r}",
    )

    replayed = await home.call(
        {
            "type": "open_house/modules/configs/switch",
            "module": slug,
            "config": "Evening",
        }
    )
    row = next(one for one in replayed["modules"] if one["slug"] == slug)
    entry = _entry_for(slug)
    check(
        "switching back brings back the answers that one was given",
        entry is not None and _holds(entry, "max_brightness_percent_value", 25),
        "" if entry is not None else f"no entry for {slug}",
    )

    renamed = await home.call(
        {
            "type": "open_house/modules/configs/rename",
            "module": slug,
            "config": "Evening",
            "to": "Night",
        }
    )
    row = next(one for one in renamed["modules"] if one["slug"] == slug)
    check(
        "a configuration can be renamed, and stays the running one",
        row["config"] == "Night" and row["configs"] == ["Default", "Night"],
        f"config={row['config']!r} configs={row['configs']!r}",
    )
    stored = mapping(_records().get(slug, {}).get("variants"))
    check(
        "both configurations are in the file, with the answers they hold",
        set(stored) == {"Default", "Night"}
        and _records().get(slug, {}).get("variant") == "Night"
        and bool(mapping(stored.get("Night", {})).get("bindings")),
        sorted(stored),
    )

    refusal = await home.refusable(
        {"type": "open_house/modules/configs/add", "module": slug, "config": "night"}
    )
    check(
        "a name already taken is refused, whichever case it is written in",
        "already has a configuration" in refusal,
        refusal,
    )

    dropped = await home.call(
        {
            "type": "open_house/modules/configs/remove",
            "module": slug,
            "config": "Default",
        }
    )
    row = next(one for one in dropped["modules"] if one["slug"] == slug)
    entry = _entry_for(slug)
    check(
        "dropping a configuration the module is not running leaves it running",
        row["config"] == "Night"
        and row["configs"] == ["Night"]
        and entry is not None
        and _holds(entry, "max_brightness_percent_value", 25),
        f"configs={row['configs']!r}",
    )

    refusal = await home.refusable(
        {
            "type": "open_house/modules/configs/remove",
            "module": slug,
            "config": "Night",
        }
    )
    check(
        "and the last configuration cannot be dropped at all",
        "only one configuration" in refusal,
        refusal,
    )

    # -- answering an input with a *slot* rather than a device --------------
    # The fourth answer, and the one that names a role: "the room's lux sensor"
    # instead of one particular sensor. Two things have to hold for it to be
    # worth anything, and neither can be seen from the pure tests: that an input
    # can be named as a slot *before* the room has a device for it -- the natural
    # order, import then bind -- and that binding the slot afterwards is what
    # makes the automation, because nothing else would have.
    rooms = (await home.call({"type": "open_house/rooms/list"}))["rooms"]
    check("the house has rooms to import into", len(rooms) > 0, len(rooms))
    room = rooms[0]
    slot_reading = await home.call(
        {"type": "open_house/modules/read", "kind": "blueprint", "key": BLUEPRINT}
    )
    words = {row["name"] for row in slot_reading.get("slots", [])}
    check(
        "the reading offers the house's own slot names to choose from",
        "ambient_light_sensor" in words,
        sorted(words)[:6],
    )
    check(
        "and the rooms a module can be imported into",
        any(row["id"] == room["id"] for row in slot_reading.get("rooms", [])),
        [row["id"] for row in slot_reading.get("rooms", [])][:4],
    )

    # Bound the slot first, to nothing: whatever a previous run of this probe
    # left in the room would make the "waiting" state below unreachable.
    await home.call(
        {
            "type": "open_house/rooms/unbind",
            "room_id": room["id"],
            "slot": "ambient_light_sensor",
        }
    )
    waiting_inputs = {
        "lux_sensor": {"kind": "slot", "slot": "ambient_light_sensor"},
        "weather_entity": {"kind": "literal", "value": NO_WEATHER},
        "lights": {"kind": "entity", "value": LIGHT_A},
    }
    # Read *before* the import, because the claim below is about what the import
    # does: a module whose slot nothing has bound must publish nothing into its
    # outputs. The state is compared with itself rather than asserted to be
    # `unknown`, because a house that has run this probe before has this entity
    # already restored by Home Assistant -- and a restored 255 is a previous
    # run's reading, not something this import wrote. (The same carve-out the
    # first module's "unknown" check makes, for the same reason.)
    output_entity = "sensor.open_house_dynamic_lighting_by_slot_brightness"
    was = await home.state(output_entity)
    third = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Dynamic Lighting By Slot",
            "room_id": room["id"],
            "bindings": waiting_inputs,
            "outputs": [{"name": "brightness", "key": "brightness"}],
        }
    )
    by_slot = third["module"]
    waiting = next(row for row in third["modules"] if row["slug"] == by_slot)
    check(
        "a module whose slot nothing has bound is hosted rather than refused",
        waiting["slug"] == by_slot,
        by_slot,
    )
    check(
        "it is recorded as belonging to the room it was imported into",
        waiting["room_id"] == room["id"],
        f"{waiting['room_id']!r} vs {room['id']!r}",
    )
    check(
        "and it says which device it is waiting for",
        [row["name"] for row in waiting["slots"]] == ["ambient_light_sensor"]
        and waiting["slots"][0]["bound"] == "",
        waiting["slots"],
    )
    check(
        "nothing is running yet: no automation, and no entry in the file",
        waiting["automation_id"] == "" and _entry_for(by_slot) is None,
        f"automation={waiting['automation_id']!r} entry={_entry_for(by_slot)}",
    )
    # The output exists before anything runs it, because the record is what the
    # sensor platform makes entities from -- and a module that is not running
    # publishes nothing into it.
    now_state = await home.state(output_entity)
    check(
        "its output entity exists anyway, and nothing was published into it",
        now_state is not None and (_nothing(now_state) or now_state == was),
        f"{was!r} -> {now_state!r}",
    )

    await home.call(
        {
            "type": "open_house/rooms/replace",
            "room_id": room["id"],
            "slot": "ambient_light_sensor",
            "entity_id": LUX,
        }
    )
    after = (await home.call({"type": "open_house/modules/hosted"}))["modules"]
    running = next(row for row in after if row["slug"] == by_slot)
    check(
        "binding the slot is what makes the module's automation",
        running["automation_id"].startswith("automation.")
        and _entry_for(by_slot) is not None,
        f"automation={running['automation_id']!r}",
    )
    check(
        "and the slot now answers with the device the room bound",
        running["slots"] == [{"name": "ambient_light_sensor", "bound": LUX}],
        running["slots"],
    )
    resolved = {row["name"]: row["value"] for row in running["inputs"]}
    check(
        "the input it named as a slot holds the entity the room binds",
        resolved.get("lux_sensor") == LUX,
        f"lux_sensor={resolved.get('lux_sensor')!r}",
    )
    await home.trigger(running["automation_id"])
    published = await home.until(
        f"sensor.open_house_{by_slot}_brightness", timeout=15.0
    )
    check(
        "and the module runs and publishes, now that the role has a device",
        published is not None,
        f"brightness={published}",
    )

    # Unbinding is the other half: the device the automation was built from is
    # gone, so the automation has to go with it rather than keep running against
    # a device nobody points it at any more.
    await home.call(
        {
            "type": "open_house/rooms/unbind",
            "room_id": room["id"],
            "slot": "ambient_light_sensor",
        }
    )
    unbound = next(
        row
        for row in (await home.call({"type": "open_house/modules/hosted"}))["modules"]
        if row["slug"] == by_slot
    )
    check(
        "unbinding the slot takes the automation back out again",
        unbound["automation_id"] == "" and _entry_for(by_slot) is None,
        f"automation={unbound['automation_id']!r} entry={_entry_for(by_slot)}",
    )

    # -- the store: a module kept for any room, and the file it travels as ---
    # The import screen's last step is now saving rather than installing, so what
    # a person made is a *module this house offers* and installing it is a
    # separate act with a room attached. Four things hold here that no pure test
    # can show: that saving installs nothing anywhere, that one definition runs
    # in a room through that room's own binding, that the file it exports carries
    # the blueprint so another house needs neither it nor the network, and that
    # removing the definition leaves what was installed from it running.
    store = await home.call({"type": "open_house/modules/store"})
    check(
        "the store names the rooms a module can be installed into",
        any(row["id"] == room["id"] for row in store["rooms"]),
        [row["id"] for row in store["rooms"]][:4],
    )
    # A previous run of this probe leaves both a definition and the room it was
    # installed into behind, and this section is written to read the same way
    # twice -- the definition is put back by re-defining it with `replace`, and
    # the installation is taken back out at the end of it. What it checks is the
    # *change* each act makes rather than a house that has never seen any of it:
    # the store is put back to having no such module, and "nothing was installed"
    # is asked as "what runs did not change" rather than "nothing runs".
    await home.maybe(
        {"type": "open_house/modules/remove", "module": "store_round_trip"}
    )
    before_hosted = [
        row["slug"]
        for row in (await home.call({"type": "open_house/modules/hosted"}))["modules"]
    ]

    defined = await home.call(
        {
            "type": "open_house/modules/define",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Store Round Trip",
            "description": "Saved to the store rather than put in a room.",
            "author": "the probe",
            "version": "2.1.0",
            "licence": "mit",
            "bindings": {
                "lux_sensor": {"kind": "slot", "slot": "ambient_light_sensor"},
                "weather_entity": {"kind": "literal", "value": NO_WEATHER},
                # The house's own word for "the lights", not a device: this and the
                # lux slot above are what make the definition runnable in a room
                # other than the one it was written in.
                "lights": {"kind": "slot", "slot": LIGHTS},
            },
            "outputs": [{"name": "brightness", "key": "store_brightness"}],
            # Re-importing the blueprint a person has since edited is an update
            # to the module; here it is what makes a second run of this probe the
            # same journey as the first.
            "replace": True,
        }
    )
    definition = defined["module"]
    check(
        "defining saves a module under its own slug",
        definition == "store_round_trip",
        definition,
    )
    offer = next(row for row in defined["store"] if row["slug"] == definition)
    check(
        "its row carries what somebody else reads before installing it",
        (
            offer["author"],
            offer["version"],
            offer["licence"],
            offer["description"],
        )
        == (
            "the probe",
            "2.1.0",
            "mit",
            "Saved to the store rather than put in a room.",
        ),
        f"{offer['author']} {offer['version']} {offer['licence']} {offer['description']!r}",
    )
    check(
        "answers that name slots make it a module any house could run",
        offer["pinned"] is False and offer["slots"] == ["ambient_light_sensor", LIGHTS],
        f"pinned={offer['pinned']} slots={offer['slots']}",
    )
    # The verdict a row carries is the placement's, not the module's: "can this
    # room answer this module" is a different question from "can the house", and
    # it is the server's answer rather than the panel's -- which is what makes
    # "Add module to room" able to say a module will wait for a device without
    # the panel re-implementing the engine's rule. Two things are asked of every
    # room: that a slot the room has *bound* is never called missing in that
    # room's verdict, which is exactly what would happen if the house's answer
    # were sent back for a room, and that the house is missing only what every
    # room is missing, which is what a house-scoped slot means.
    per_room: dict[str, set[str]] = {}
    wrong: list[tuple[str, list[str]]] = []
    for candidate in store["rooms"]:
        asked = await home.call(
            {"type": "open_house/modules/store", "room_id": candidate["id"]}
        )
        row = next((r for r in asked["store"] if r["slug"] == definition), None)
        if row is None:
            continue
        detail = await home.call(
            {"type": "open_house/rooms/get", "room_id": candidate["id"]}
        )
        bound = {b["slot"] for b in detail["bindings"] if b["entity_id"]}
        missing = set(row["missing_slots"])
        per_room[candidate["id"]] = missing
        if missing & bound:
            wrong.append((candidate["id"], sorted(missing & bound)))
    check(
        "a slot a room has bound is never missing from that room's own verdict",
        not wrong,
        str(wrong),
    )
    house_missing = set(
        next(
            row
            for row in (await home.call({"type": "open_house/modules/store"}))["store"]
            if row["slug"] == definition
        )["missing_slots"]
    )
    check(
        "and the house's verdict is missing only what every room is missing",
        all(house_missing <= missing for missing in per_room.values()),
        f"house={sorted(house_missing)} rooms={ {k: sorted(v) for k, v in per_room.items()} }",
    )

    # A word nothing will ever bind is a module that waits forever rather than a
    # module that waits: `rooms/bind` refuses a name outside the house's
    # vocabulary, so there is no act available that would ever start it. Typing a
    # slot name is a shortcut past scrolling, not a way to invent one.
    invented = await home.refusable(
        {
            "type": "open_house/modules/define",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Invented Slot",
            "bindings": {
                "lux_sensor": {"kind": "slot", "slot": "the_room_s_lux_sensor"},
                "weather_entity": {"kind": "literal", "value": NO_WEATHER},
                "lights": {"kind": "entity", "value": LIGHT_A},
            },
            "replace": True,
        }
    )
    check(
        "a slot name this house does not have is refused, naming the ones it does",
        LIGHTS in invented and "the_room_s_lux_sensor" in invented,
        invented,
    )
    hosted_rows = (await home.call({"type": "open_house/modules/hosted"}))["modules"]
    check(
        "nothing was installed by saving it, and no automation was made",
        [row["slug"] for row in hosted_rows] == before_hosted
        and _entry_for(definition) is None,
        f"before={before_hosted} after={[row['slug'] for row in hosted_rows]}",
    )
    check(
        "and the row accounts for every installation of it, and for no other",
        sorted(where["slug"] for where in offer["deployed"])
        == sorted(
            row["slug"] for row in hosted_rows if row.get("definition") == definition
        ),
        f"deployed={offer['deployed']}",
    )

    # Install it into a room that has the roles bound -- which is the whole reason
    # to answer an input with a slot. The answers are the definition's own; only
    # the room is sent. Both roles the module reaches through are bound here, so
    # the automation is expected to be made on the spot rather than waited for,
    # and the ones this probe *had* to bind are remembered so the room is left as
    # it was found rather than with this probe's devices in it.
    room_before = (
        await home.call({"type": "open_house/rooms/get", "room_id": room["id"]})
    )["bindings"]
    already = {row["slot"]: row["entity_id"] for row in room_before}
    this_probe_bound: list[tuple[str, Any]] = []
    for slot, entity in (("ambient_light_sensor", LUX), (LIGHTS, LIGHT_A)):
        if already.get(slot) == entity:
            continue
        this_probe_bound.append((slot, already.get(slot)))
        # `rooms/replace`, not `rooms/bind`: a slot that already holds a device is
        # swapped through the command that says so, because a rebind that
        # overwrote a binding silently would be indistinguishable from an initial
        # one in the decision log.
        await home.call(
            {
                "type": "open_house/rooms/replace",
                "room_id": room["id"],
                "slot": slot,
                "entity_id": entity,
            }
        )
    deployed = await home.call(
        {
            "type": "open_house/modules/deploy",
            "module": definition,
            "room_id": room["id"],
        }
    )
    installed = deployed["module"]
    check(
        "installing it into a room names the installation after both",
        installed == f"{definition}_{room['id']}",
        installed,
    )
    placed = next(row for row in deployed["modules"] if row["slug"] == installed)
    check(
        "the installation remembers the definition it came from",
        placed["definition"] == definition and placed["room_id"] == room["id"],
        f"definition={placed['definition']!r} room={placed['room_id']!r}",
    )
    check(
        "and it is running, because the room binds the roles it reaches through",
        placed["automation_id"].startswith("automation.")
        and placed["slots"]
        == [
            {"name": "ambient_light_sensor", "bound": LUX},
            {"name": LIGHTS, "bound": LIGHT_A},
        ],
        f"automation={placed['automation_id']!r} slots={placed['slots']}",
    )
    # The name Home Assistant shows, which is what tells one room's installation
    # from another's. The room has to be in it: Home Assistant derives an
    # automation's entity id from its alias and makes it unique by numbering, so
    # two aliases alike are two rooms told apart by load order.
    check(
        "the installation's own name in Home Assistant says which room it is",
        placed["title"].endswith(f"({room['name']})")
        and placed["title"].startswith("Store Round Trip"),
        placed["title"],
    )
    await home.trigger(placed["automation_id"])
    published = await home.until(
        f"sensor.open_house_{installed}_store_brightness", timeout=15.0
    )
    check(
        "and it publishes under the installation's own name",
        published is not None,
        f"brightness={published}",
    )
    check(
        "the store row now says where it went",
        [
            (where["slug"], where["room_id"], where["running"])
            for where in next(
                row for row in deployed["store"] if row["slug"] == definition
            )["deployed"]
        ]
        == [(installed, room["id"], True)],
        next(row for row in deployed["store"] if row["slug"] == definition)["deployed"],
    )

    # The file. It is what a person sends somebody else, so the check is that it
    # carries everything: the attributes, the answers, and the document itself.
    document = (
        await home.call({"type": "open_house/modules/export", "module": definition})
    )["document"]
    inner: dict[str, Any] = mapping(document.get("definition"))
    check(
        "the export is a wrapped module document",
        document.get("open_house_module") == 1 and inner.get("slug") == definition,
        list(document),
    )
    check(
        "the file carries the blueprint itself, so no other house needs it",
        # The source is the blueprint's own file, verbatim -- including the
        # `blueprint:` block that names it, which is what `instantiate` drops when
        # it makes an automation and what nothing else here would keep.
        isinstance(inner.get("source"), str)
        and "trigger" in inner["source"]
        and "blueprint:" in inner["source"],
        str(inner.get("source"))[:60],
    )
    check(
        "and the answers it holds are the ones it was saved with",
        inner.get("bindings", {}).get("lux_sensor", {}).get("slot")
        == "ambient_light_sensor",
        inner.get("bindings", {}).get("lux_sensor"),
    )
    check(
        "its provenance does not travel with it, only the document",
        inner.get("blueprint") == BLUEPRINT and "deployed" not in inner,
        f"blueprint={inner.get('blueprint')!r}",
    )

    # Reading the file back is how a second house gets it -- and how this one
    # takes an update. A name already here is refused unless the caller says to
    # replace it, because silently overwriting somebody's module is not an
    # import.
    refused = await home.refusable(
        {"type": "open_house/modules/import", "document": document, "replace": False}
    )
    check(
        "importing a module this house already offers is refused",
        "already" in refused.lower() or "replace" in refused.lower(),
        refused,
    )
    reimported = await home.call(
        {"type": "open_house/modules/import", "document": document, "replace": True}
    )
    check(
        "re-importing it over itself says that is what happened",
        reimported["imported"] == definition and reimported["replaced"] is True,
        f"imported={reimported['imported']!r} replaced={reimported['replaced']}",
    )
    still_there = next(
        row
        for row in (await home.call({"type": "open_house/modules/hosted"}))["modules"]
        if row["slug"] == installed
    )
    check(
        "and the room installed from it is untouched by re-importing",
        still_there["automation_id"] == placed["automation_id"],
        f"automation={still_there['automation_id']!r}",
    )

    # Removing a definition is not uninstalling: an installation keeps its own
    # copy of the document, so the room running it keeps running it, and the
    # store says how many that is.
    removed = await home.call(
        {"type": "open_house/modules/remove", "module": definition}
    )
    check(
        "removing the definition names it and counts what still runs it",
        removed["removed"] == definition and removed["installed"] == 1,
        f"removed={removed['removed']!r} installed={removed['installed']}",
    )
    check(
        "and it is gone from the store",
        not any(row["slug"] == definition for row in removed["store"]),
        [row["slug"] for row in removed["store"]],
    )
    after_removal = next(
        row
        for row in (await home.call({"type": "open_house/modules/hosted"}))["modules"]
        if row["slug"] == installed
    )
    check(
        "the room installed from it is still installed and still running",
        after_removal["automation_id"] == placed["automation_id"]
        and _entry_for(installed) is not None,
        f"automation={after_removal['automation_id']!r}",
    )

    # Taking an installation out is the other half of installing it, and a
    # different act from removing a definition: this one touches one room's copy
    # and nothing else. Three things have to come away with it -- the record, the
    # automation Home Assistant runs, and the entity it publishes into -- because
    # a module that is gone from every list while its entities live on is a
    # module still writing to somebody's dashboard.
    entity = f"sensor.open_house_{installed}_store_brightness"
    unhosted = await home.call(
        {"type": "open_house/modules/unhost", "module": installed}
    )
    check(
        "taking it out of the room answers with the module and the house as it now is",
        unhosted["module"] == installed
        and not any(row["slug"] == installed for row in unhosted["modules"]),
        [row["slug"] for row in unhosted["modules"]],
    )
    check(
        "and the automation Home Assistant was running is gone with it",
        _entry_for(installed) is None,
        str(_entry_for(installed))[:80],
    )
    check(
        "and so is the entity it published into",
        (await home.state(entity)) is None,
        str(await home.state(entity)),
    )
    # The definition went first and the installation outlived it; that is what
    # made the two acts different. This half is about the copy, so the module it
    # came from is still whatever it was -- gone from the store, and nothing here
    # depends on it.
    check(
        "the room is left holding no module of that name",
        not any(
            row["slug"] == installed
            for row in (await home.call({"type": "open_house/modules/hosted"}))[
                "modules"
            ]
        ),
        "still listed",
    )
    refused = await home.refusable(
        {"type": "open_house/modules/unhost", "module": installed}
    )
    check(
        "and taking it out twice is refused rather than quietly answered",
        "no module called" in refused,
        refused,
    )

    # Put the room's bindings back where this probe found them, so a second run
    # is the same journey as the first and the house is left as it was -- the
    # installation is taken back out above, which is what an installation is for
    # and what this section used to have no way of doing.
    for slot, was in this_probe_bound:
        if was is None:
            await home.call(
                {
                    "type": "open_house/rooms/unbind",
                    "room_id": room["id"],
                    "slot": slot,
                }
            )
        else:
            await home.call(
                {
                    "type": "open_house/rooms/replace",
                    "room_id": room["id"],
                    "slot": slot,
                    "entity_id": was,
                }
            )

    # -- an input answered with a condition ---------------------------------
    # The one answer that is not a binding. A condition cannot travel into the
    # automation where an entity id goes: Home Assistant matches a trigger's
    # `entity_id` against the entities a house actually has and never renders it,
    # so a condition written there installs and then never fires -- silently, and
    # on the input a person most wants to ask a question about. Open House works
    # the condition out itself, publishes the answer as a real `binary_sensor`,
    # and binds the input to *that*. Which is what makes a condition work on
    # every input, including the ones a trigger watches.
    cast_slug = "cast_probe"
    condition_true = {
        "condition": "numeric_state",
        "entity_id": [LUX],
        "above": 0,
    }
    cast_hosted = await home.call(
        {
            "type": "open_house/modules/host",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Cast Probe",
            "bindings": {
                # Deliberately *also* a device, and a device this house does not
                # have: the condition is the answer, and a binding that quietly
                # won over it would be the automation reading a rubbish sensor
                # while the screen said the condition was in force.
                "lux_sensor": {"kind": "entity", "value": "sensor.nowhere"},
                "weather_entity": {"kind": "literal", "value": NO_WEATHER},
                "lights": {"kind": "entity", "value": LIGHT_B},
            },
            "casts": {"lux_sensor": condition_true},
            # Kept as a setting as well as cast, which is the shape the panel
            # actually produces: the cast is offered *on* a row, and the row is
            # a setting -- so the condition has to survive being a setting and
            # come back out of the settings listing for the card to open on it.
            "settings": ["lux_sensor"],
        }
    )
    cast_record = next(
        row for row in cast_hosted["modules"] if row["slug"] == cast_slug
    )
    derived = f"binary_sensor.open_house_{cast_slug}_lux_sensor"
    check(
        "an input answered with a condition gets an entity of its own",
        (await home.state(derived)) in ("on", "off"),
        str(await home.state(derived)),
    )
    check(
        "the condition is evaluated against the entity it names, not carried as text",
        (await home.state(derived)) == "on",
        f"{LUX} is over 0, so the condition is met and the entity is on",
    )
    filled_cast = {row["name"]: row["value"] for row in cast_record["inputs"]}
    check(
        "the input is bound to that entity, and the device sent beside it lost",
        derived in str(filled_cast.get("lux_sensor")),
        f"lux_sensor={filled_cast.get('lux_sensor')!r}",
    )
    check(
        "and the automation Home Assistant is running is built from it",
        str(cast_record["automation_id"]).startswith("automation."),
        str(cast_record["automation_id"]),
    )

    # The other half of "evaluated": the same question asked of the same entity
    # with an answer nothing could satisfy. A reading that was merely defaulted,
    # or a condition only ever parsed and never run, would give the same answer
    # twice -- these two must disagree.
    impossible = {"condition": "numeric_state", "entity_id": [LUX], "above": 99999999}
    await home.call(
        {
            "type": "open_house/modules/settings",
            "module": cast_slug,
            "casts": {"lux_sensor": impossible},
        }
    )
    check(
        "a condition nothing can satisfy switches the entity off",
        (await home.until_state(derived, "off")) == "off",
        str(await home.state(derived)),
    )

    # Put it back to a condition that holds, then take the module out -- the
    # entity that was made for it has no reason to outlive it.
    await home.call(
        {
            "type": "open_house/modules/settings",
            "module": cast_slug,
            "casts": {"lux_sensor": condition_true},
        }
    )
    check(
        "a condition put back turns the entity on again",
        (await home.until_state(derived, "on")) == "on",
        str(await home.state(derived)),
    )
    check(
        "and the setting reads back as a condition, not as an unknown entity",
        (await home.settings(cast_slug)).get("lux_sensor", {}).get("bound_kind")
        == "condition",
        json.dumps((await home.settings(cast_slug)).get("lux_sensor")),
    )
    await home.call({"type": "open_house/modules/unhost", "module": cast_slug})
    check(
        "taking the module out takes the entity it made with it",
        (await home.state(derived)) is None,
        str(await home.state(derived)),
    )

    # -- editing a module: the answers, and every room running them ----------
    # The card's Edit is the *import* screen opened on a module rather than on a
    # source nobody has chosen yet, and what it saves is the module rather than
    # the room's copy of it. Proving that needs a module with more than one
    # installation, so this one is put into two rooms and edited once -- and every
    # check below is about the second room, the one nobody was looking at when
    # Edit was pressed.
    edit_slug = "edit_probe"
    edit_installs = [f"{edit_slug}_{where['id']}" for where in rooms[:2]]
    # A previous run of this probe leaves both an installation and a definition
    # behind, so it is taken apart before it is put back: what is checked below is
    # the *change* each act makes, not a house that has never seen any of it.
    for leftover in edit_installs:
        await home.maybe({"type": "open_house/modules/unhost", "module": leftover})
    await home.maybe({"type": "open_house/modules/remove", "module": edit_slug})
    # Devices named outright rather than slots, so this section needs nothing of
    # either room: what is being proved is what an edit reaches, not what a room
    # binds.
    edit_bindings = {
        "lux_sensor": {"kind": "entity", "value": LUX},
        "weather_entity": {"kind": "literal", "value": NO_WEATHER},
        # An *entity* and not a literal, because this input is a `target`: what
        # the server writes into the document is the mapping Home Assistant's own
        # editor writes, and a bare string there is an automation Home Assistant
        # refuses.
        "lights": {"kind": "entity", "value": LIGHT_A},
        "max_brightness_percent": {"kind": "literal", "value": 100},
    }
    await home.call(
        {
            "type": "open_house/modules/define",
            "kind": "blueprint",
            "key": BLUEPRINT,
            "title": "Edit Probe",
            "bindings": edit_bindings,
            # One input kept as a dial and one output ticked, because both are
            # things an edit can take away again -- and taking them away is the
            # half of this that nothing outside the module would notice.
            "settings": ["max_brightness_percent"],
            "outputs": [{"name": "brightness", "key": "edit_brightness"}],
            "replace": True,
        }
    )
    for where in rooms[:2]:
        await home.call(
            {
                "type": "open_house/modules/deploy",
                "module": edit_slug,
                "room_id": where["id"],
            }
        )
    before_edit = {
        row["slug"]: row
        for row in (await home.call({"type": "open_house/modules/hosted"}))["modules"]
        if row["slug"] in edit_installs
    }
    check(
        "a module installed into two rooms is two automations, not one",
        len(before_edit) == 2
        and len({row["automation_id"] for row in before_edit.values()}) == 2,
        {slug: row["automation_id"] for slug, row in before_edit.items()},
    )

    # What the screen is handed when Edit is pressed, and the two things that make
    # the menu the same menu: the module's own document rather than anything the
    # caller sent, and the answers the module already has.
    read_back = await home.call(
        {"type": "open_house/modules/read", "kind": "text", "module": edit_installs[0]}
    )
    seed = mapping(read_back.get("editing"))
    check(
        "reading a module hands back the module's own document, not the caller's",
        "blueprint:" in str(seed.get("text"))
        and seed.get("module") == edit_installs[0],
        f"module={seed.get('module')!r} text={str(seed.get('text'))[:30]!r}",
    )
    check(
        "and the answers it was given, rather than a fresh blueprint's defaults",
        seed.get("settings") == ["max_brightness_percent"]
        and str(seed.get("bindings", {}).get("max_brightness_percent", {}).get("value"))
        == "100"
        and [row.get("key") for row in seed.get("picks", [])] == ["edit_brightness"],
        f"settings={seed.get('settings')} bindings={seed.get('bindings')} "
        f"picks={seed.get('picks')}",
    )
    check(
        "and it names every room the save is about to change, not just this one",
        {row["slug"] for row in seed.get("installs", [])} == set(edit_installs),
        seed.get("installs"),
    )
    check(
        "the inputs the module answered read as answered, so Edit opens on them",
        all(
            row["bound"] for row in read_back["inputs"] if row["name"] in edit_bindings
        ),
        {row["name"]: row["bound"] for row in read_back["inputs"]},
    )

    # The edit itself: the ceiling moved, the dial taken off, one output dropped
    # and another ticked, and the module renamed. Sent the way the *import* screen
    # sends it -- a source kind, the module's name, and the answers.
    edited = await home.call(
        {
            "type": "open_house/modules/edit",
            "module": edit_installs[1],
            "kind": "text",
            "text": str(seed.get("text")),
            "title": "Edit Probe Renamed",
            "bindings": {
                **seed.get("bindings", {}),
                "max_brightness_percent": {"kind": "literal", "value": 25},
            },
            "settings": [],
            "outputs": [{"name": "color_temp_kelvin", "key": "edit_kelvin"}],
            # The module answers none of its inputs with a condition or a flow,
            # so these are empty rather than absent -- which is the whole point of
            # the two being required.
            "casts": {},
            "flows": [],
        }
    )
    after_edit = {
        row["slug"]: row for row in edited["modules"] if row["slug"] in edit_installs
    }
    check(
        "one edit reaches both installations, naming each of them",
        set(after_edit) == set(edit_installs)
        and sorted(row["room_id"] for row in after_edit.values())
        == sorted(where["id"] for where in rooms[:2]),
        {slug: row["room_id"] for slug, row in after_edit.items()},
    )
    check(
        "and it rebuilds them rather than replacing them: same automation, same room",
        all(
            after_edit[slug]["automation_id"] == before_edit[slug]["automation_id"]
            for slug in edit_installs
        ),
        {
            slug: (
                before_edit[slug]["automation_id"],
                after_edit[slug]["automation_id"],
            )
            for slug in edit_installs
        },
    )
    check(
        "the new name reaches them too, and each keeps the room it is in",
        all(
            after_edit[slug]["title"] == f"Edit Probe Renamed ({rooms[at]['name']})"
            for at, slug in enumerate(edit_installs)
        ),
        {slug: row["title"] for slug, row in after_edit.items()},
    )
    # **The answer moved, in the document Home Assistant is running.** Everything
    # above is the module's own bookkeeping; this is the automation, which is the
    # only place the change is worth anything.
    check(
        "the answer the edit changed is inside both automations",
        all(
            _holds(_entry_for(slug) or {}, "max_brightness_percent_value", 25)
            for slug in edit_installs
        ),
        {
            slug: _entry_for(slug) is not None
            and _holds(_entry_for(slug) or {}, "max_brightness_percent_value", 25)
            for slug in edit_installs
        },
    )
    # The two halves of "what it exposes": the output the edit dropped stops being
    # an entity in *both* rooms, and the one it ticked starts being one in both.
    # The first is the half a rebuild that only ever added would get wrong, and it
    # is invisible from the module's own reply.
    dropped = [f"sensor.open_house_{slug}_edit_brightness" for slug in edit_installs]
    offered = [f"sensor.open_house_{slug}_edit_kelvin" for slug in edit_installs]
    # Read into a dict first: a generator holding an `await` is an async
    # generator, which `all` cannot walk.
    gone = {entity: await home.state(entity) for entity in dropped}
    there = {entity: await home.state(entity) for entity in offered}
    check(
        "an output the edit took off is gone from every room, not just this one",
        all(state is None for state in gone.values()),
        gone,
    )
    check(
        "and the one it ticked exists in every room",
        all(state is not None for state in there.values()),
        there,
    )
    dials = {slug: sorted(await home.settings(slug)) for slug in edit_installs}
    check(
        "the dial the edit took off is not a setting on either installation",
        all("max_brightness_percent" not in names for names in dials.values()),
        dials,
    )

    # And an answer a room made for itself outlives an edit. This is what makes an
    # edit a change to the *module* rather than a reset of everything built from
    # it: the two installations are made to disagree, the module is edited under
    # them, and the one that answered for itself must still say so. The dial has to
    # go back on first, for a room to be able to answer for itself at all -- which
    # is the other half of the same rule, and is asked here rather than assumed.
    # Sent the way the screen sends it: the five answers, whole. They are required
    # rather than defaulted because an *absent* set and an *empty* one are the
    # same bytes on the wire, and reading them as the same would take every answer
    # off a module installed in five rooms.
    again = {
        "type": "open_house/modules/edit",
        "module": edit_installs[0],
        "kind": "text",
        "text": str(seed.get("text")),
        "title": "Edit Probe Renamed",
        "bindings": mapping(seed.get("bindings")),
        "settings": ["max_brightness_percent"],
        "outputs": [{"name": "color_temp_kelvin", "key": "edit_kelvin"}],
        "casts": {},
        "flows": [],
    }
    await home.call(again)
    dials_back = {slug: sorted(await home.settings(slug)) for slug in edit_installs}
    check(
        "a setting an edit puts back is a dial on both installations",
        all(names == ["max_brightness_percent"] for names in dials_back.values()),
        dials_back,
    )
    await home.call(
        {
            "type": "open_house/modules/settings",
            "module": edit_installs[1],
            "bindings": {"max_brightness_percent": {"kind": "literal", "value": 40}},
        }
    )
    await home.call(
        {
            **again,
            "bindings": {
                **seed.get("bindings", {}),
                "max_brightness_percent": {"kind": "literal", "value": 60},
            },
        }
    )
    # **Read off the installation rather than the module**, which is the whole
    # difference being checked: `editing` on a read is the *module's* answers, and
    # what a room has made of its own is on the room. The automation is asked as
    # well, because the answers' bookkeeping agreeing is not the same thing as the
    # document Home Assistant is running being right.
    answered = {
        slug: str(
            (await home.settings(slug)).get("max_brightness_percent", {}).get("value")
        )
        for slug in edit_installs
    }
    check(
        "a room that answered for itself keeps its answer through an edit",
        answered[edit_installs[1]] == "40",
        answered,
    )
    check(
        "and the room that took the module as it came follows it",
        answered[edit_installs[0]] == "60",
        answered,
    )
    running = {
        slug: _holds(_entry_for(slug) or {}, "max_brightness_percent_value", 60)
        if slug == edit_installs[0]
        else _holds(_entry_for(slug) or {}, "max_brightness_percent_value", 40)
        for slug in edit_installs
    }
    check(
        "and each room's own automation carries the ceiling that room is running",
        all(running.values()),
        running,
    )

    for slug in edit_installs:
        await home.call({"type": "open_house/modules/unhost", "module": slug})
    await home.maybe({"type": "open_house/modules/remove", "module": edit_slug})

    # -- the document, for the part the outside cannot show -----------------
    await check_document()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for line in FAILURES:
            print(f"  - {line}")
        return len(FAILURES)
    print("every check passed")
    return 0


async def check_document() -> None:
    """Read `automations.yaml` back and check what the outside cannot show.

    Inside the container, so the file is simply there. Three things are read: that
    the automation is a first-class entry in the file Home Assistant's own editor
    reads, that the blueprint's own actions survived -- the `choose` branches and
    the `light.turn_on` carrying the brightness the blueprint computes -- and that
    the publisher is the *last* step rather than a replacement for one of them.
    """
    if not AUTOMATIONS.is_file():
        check("automations.yaml was written", False, AUTOMATIONS)
        return
    entries = _automations()
    entry = _entry_for("dynamic_lighting")
    check(
        "the module is a first-class entry in automations.yaml",
        entry is not None,
        [row.get("alias") for row in entries],
    )
    if entry is None:
        return
    check("the entry carries an id, as the editor's own do", bool(entry.get("id")))
    check(
        "so does the second module, under the same file",
        _entry_for("dynamic_lighting_bedroom") is not None,
    )

    actions = _steps(entry.get("action") or entry.get("actions"))
    services = [
        text_of(step, "service") for step in actions if text_of(step, "service")
    ]
    check(
        "the blueprint's own actions are all still there",
        "light.turn_on" in str(actions)
        or any("choose" in mapping(step) for step in actions),
        f"services={services}",
    )
    check(
        "the publisher for each variable is appended, never woven in",
        _publishes(entry, "dynamic_lighting"),
    )
    check(
        "a variable's publisher sits at the end of the automation that defines it",
        text_of(actions[-1], "service") == "open_house.publish_output",
        f"last={text_of(actions[-1], 'service')!r}",
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
