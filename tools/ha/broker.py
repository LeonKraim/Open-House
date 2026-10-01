#!/usr/bin/env python3
"""Point a headless Home Assistant at the mock broker, before anything runs.

**Why this is not a `configuration.yaml` block.** It was one, and it failed
loudly, which is how the fact got recorded here. Home Assistant removed YAML
configuration for the MQTT integration: a modern instance loads it as a *config
entry* created through the config flow, and a `mqtt:` mapping in
`configuration.yaml` is not a deprecated form of that -- it is parsed as a list
of something else and rejected. The container's log says it exactly:

    Invalid config for 'mqtt' at configuration.yaml, line 37:
    'broker' is an invalid option for 'mqtt', check: mqtt->0->broker
    Setup failed for 'mqtt': Invalid config.

The `mqtt->0->broker` path is the tell: the value at `mqtt` was read as a
sequence and the checker was looking at element zero. There is no spelling of the
YAML block that works, so the broker address lives here instead, and this script
is what puts it into the instance.

**What it does.** Drives `/api/config/config_entries/flow` with the `mqtt`
handler, exactly as the frontend does when a person adds the integration from the
UI: start a flow, answer the form with the broker's address, and let Home
Assistant create the entry. It is **idempotent** for the same reason
`tools/ha/onboard.py` is -- it is run against a container that may already be
configured -- so an existing `mqtt` entry is reported and left alone rather than
made a second time.

**The address is the compose service name.** Home Assistant and the broker are
two containers on one compose network, so `mocks` is the name that resolves and
survives a rebuild. `localhost` inside the Home Assistant container is that
container; the published `1883:1883` is for the fleet on the host, which is the
other side of the same broker.

**Protocol 3.1.1**, not the flow's default of MQTT 5. Both ends of this broker
speak either -- `eclipse-mosquitto:2` supports both and `tools/ha/mqtt.py`
implements 3.1.1 -- and the older version is chosen so the Home Assistant side
and the fleet's side are the same protocol, which is one fewer difference to
account for when a message does not arrive.

**Why the HTTP calls are written here rather than imported.** `tools/ha/onboard.py`
has a request helper, and this module does not use it: that one is private to the
onboarding flow, the project's type check refuses a private reach across a module
boundary (`reportPrivateUsage`), and the two have different jobs -- onboarding
posts form-encoded bodies to `/auth/`, this posts JSON to `/api/config/`. What is
shared between them is twenty lines of `urllib`, and copying twenty lines is the
honest price of not pretending one module's internals are another's interface.

Usage:
    python tools/ha/broker.py [--base http://localhost:8123]
                              [--broker mocks] [--port 1883]
                              [--env .env.local]
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ensure_broker", "main"]

#: The protocol the Home Assistant side connects with, and the transport it
#: connects over. Both are the flow's own fields, and the transport is its
#: default written out: the form marks it required, and a field marked required
#: that arrives absent is a re-ask rather than a default.
_PROTOCOL = "3.1.1"
_TRANSPORT = "tcp"

#: The three answers the flow's `other_settings` section requires, and the reason
#: they are here rather than left out: Home Assistant's form marks each required
#: with no default, so a payload that omits the section is refused with
#: `other_settings: required key not provided`. No client certificate, no CA
#: certificate check, plain TCP -- the three things a broker on a local compose
#: network with `allow_anonymous true` makes unnecessary, and the only values
#: this script has an opinion about.
_OTHER_SETTINGS: Mapping[str, object] = {
    "set_client_cert": False,
    "set_ca_cert": "off",
    "transport": _TRANSPORT,
}

#: How many forms the flow may ask before this gives up. The `mqtt` flow asks
#: once; the ceiling exists so a frontend change that makes it ask forever fails
#: with a sentence rather than hanging a CI job.
_MAX_STEPS = 4


def _request(
    base: str,
    path: str,
    payload: Mapping[str, object] | None,
    token: str,
) -> tuple[int, object]:
    """POST `payload` as JSON (or GET when it is `None`), returning status and body."""
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base}{path}", data=body, method="POST" if body else "GET"
    )
    if body:
        request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode() or "{}"
            return response.status, json.loads(text)
    except urllib.error.HTTPError as error:
        raw = error.read().decode() or "{}"
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw


def _field(value: object, key: str) -> object:
    """`value[key]` when `value` is a mapping that has it, else `None`."""
    if not isinstance(value, dict):
        return None
    return cast("dict[str, object]", value).get(key)


def _text(value: object, key: str) -> str | None:
    """The string at `key`, or `None` when it is absent or another type."""
    found = _field(value, key)
    return found if isinstance(found, str) else None


def _entries(body: object) -> list[object]:
    """A body that should be a list of config entries, as one."""
    # A cast rather than a comprehension: `isinstance` narrows to `list[Any]`,
    # which the project's strict type check reports as an unknown element type,
    # and the only thing this function can honestly promise about elements it has
    # not looked at is that they are objects.
    return cast("list[object]", body) if isinstance(body, list) else []


def _has_mqtt(entries: list[object]) -> bool:
    """Whether an `mqtt` config entry already exists."""
    return any(_text(entry, "domain") == "mqtt" for entry in entries)


def ensure_broker(
    base: str, token: str, broker: str, *, port: int = 1883
) -> tuple[bool, str]:
    """Create the `mqtt` config entry if it is absent.

    Returns `(created, detail)`: whether this call made the entry, and a sentence
    naming what happened. A caller that only wanted a container ready does not
    need the distinction; a caller recording what a run changed does.
    """
    status, body = _request(base, "/api/config/config_entries/entry", None, token)
    if status != 200:
        raise SystemExit(f"listing config entries failed with status {status}: {body}")
    if _has_mqtt(_entries(body)):
        return False, "an mqtt config entry already exists; nothing to create"

    status, flow = _request(
        base, "/api/config/config_entries/flow", {"handler": "mqtt"}, token
    )
    if status != 200:
        raise SystemExit(f"starting the mqtt flow failed with status {status}: {flow}")
    flow_id = _text(flow, "flow_id")
    if flow_id is None:
        raise SystemExit(f"the mqtt flow did not name itself: {flow}")

    payload: Mapping[str, object] = {
        "broker": broker,
        "port": port,
        "protocol": _PROTOCOL,
        # The broker is `allow_anonymous true`, and an empty username posted as a
        # value is what leaves the optional field unset -- omitting it entirely
        # makes the flow re-ask for it.
        "username": "",
        "password": "",
        "other_settings": dict(_OTHER_SETTINGS),
    }
    for _ in range(_MAX_STEPS):
        status, step = _request(
            base, f"/api/config/config_entries/flow/{flow_id}", payload, token
        )
        if status not in (200, 201):
            raise SystemExit(f"the mqtt flow failed with status {status}: {step}")
        kind = _text(step, "type")
        if kind == "create_entry":
            return True, f"created the mqtt config entry for {broker}:{port}"
        if kind == "abort":
            reason = _text(step, "reason") or "no reason given"
            raise SystemExit(f"the mqtt flow aborted: {reason}")
        if kind != "form":
            raise SystemExit(f"the mqtt flow asked something unexpected: {step}")
        # A second form is Home Assistant asking to confirm a choice it could not
        # take from the first -- the empty answer accepts the defaults it offered.
        payload = {}

    raise SystemExit(f"the mqtt flow did not settle after {_MAX_STEPS} steps")


def _token(path: str) -> str:
    """The stored long-lived token, or a sentence saying which file to look in."""
    env = Path(path)
    if not env.is_file():
        raise SystemExit(f"{env} does not exist; run tools/ha/onboard.py first")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{env} carries no HA_TOKEN; run tools/ha/onboard.py first")


def main(argv: list[str] | None = None) -> int:
    """Ensure the broker config entry exists, and say what it did."""
    parser = argparse.ArgumentParser(
        description="Point a headless Home Assistant at the mock broker."
    )
    parser.add_argument("--base", default="http://localhost:8123")
    parser.add_argument(
        "--broker",
        default="mocks",
        help="the broker address as Home Assistant resolves it (the compose "
        "service name)",
    )
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--env", default=".env.local")
    arguments = parser.parse_args(argv)

    created, detail = ensure_broker(
        arguments.base,
        _token(arguments.env),
        arguments.broker,
        port=arguments.port,
    )
    print(f"{'created' if created else 'present'}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
