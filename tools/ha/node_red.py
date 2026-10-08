#!/usr/bin/env python3
"""Tell a headless Home Assistant where its Node-RED is.

**Why this is a config-entry option and not a YAML block.** The address is not
derivable. Node-RED runs *beside* Home Assistant in this compose stack and
*behind the add-on's ingress* on a Home Assistant OS install, and the address the
panel links a person to is the one that reaches the editor from **their
browser** -- which is neither the container-to-container name nor anything the
integration could work out. So it is configured, and this script puts it into the
one place the integration reads it from, exactly as the options dialog does.

**The address is the compose service name.** `nodered` is what resolves between
the two containers and survives a rebuild. `localhost` inside the Home Assistant
container is that container, and `localhost` in the *browser* is the host -- so
the defaults are the pair that works for both halves: `http://nodered:1880` for
the push and `http://localhost:1880` for the link. (Both are overridable; on
Home Assistant OS the link is the add-on's ingress path instead.)

**It is idempotent**, for the reason `tools/ha/broker.py` is: it is run against a
container that may already be configured, and an option already set to the same
address is reported rather than rewritten -- a rewrite is a config-entry reload,
and a reload takes the rooms and the engine down to change a string that was
already right.

Usage:
    python tools/ha/node_red.py [--base http://localhost:8123]
                                [--url http://nodered:1880]
                                [--editor-url http://localhost:1880]
                                [--token ""] [--env .env.local]
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

__all__ = ["ensure_node_red", "main"]

#: How many forms the options flow may ask before this gives up. `OpenHouseOptionsFlow`
#: asks once; the ceiling is there so a frontend change that makes it ask forever
#: fails with a sentence rather than hanging a CI job.
_MAX_STEPS = 4

#: The two option keys, spelled the way the integration's own `node_red` module
#: spells them. Restated rather than imported because this runs on the host, where
#: the integration is not on the import path -- and a rename that breaks this is a
#: rename that breaks with a sentence naming the entry, which is the loud half of
#: the trade.
OPTION_URL = "node_red_url"
OPTION_TOKEN = "node_red_token"
OPTION_EDITOR_URL = "node_red_editor_url"


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


def _open_house_entry(entries: object) -> str | None:
    """The `open_house` config entry's id, or `None` when the house has none."""
    if not isinstance(entries, list):
        return None
    for entry in cast("list[object]", entries):
        if _text(entry, "domain") == "open_house":
            return _text(entry, "entry_id")
    return None


def ensure_node_red(
    base: str,
    token: str,
    url: str,
    *,
    admin_token: str = "",
    editor_url: str = "",
) -> tuple[bool, str]:
    """Point this house's Node-RED at `url`, if it does not already point there.

    `url` is the address **Home Assistant** pushes flows to and `editor_url` the
    one a **browser** opens the editor at, and on this stack they are two names
    for the same container: `http://nodered:1880` resolves between the two compose
    services and nowhere else, and `http://localhost:1880` is the published port
    that resolves on the host and nowhere else. Passing one for both would make
    either the push or the link wrong, so both are taken here.

    Returns `(changed, detail)`: whether this call set the option, and a sentence
    naming what happened. A caller recording what a run changed wants the
    distinction; one that only wanted the stack ready does not.
    """
    status, body = _request(base, "/api/config/config_entries/entry", None, token)
    if status != 200:
        raise SystemExit(f"listing config entries failed with status {status}: {body}")
    entry_id = _open_house_entry(body)
    if entry_id is None:
        raise SystemExit(
            "this instance has no open_house config entry: set the house up in "
            "Home Assistant first, then run this"
        )

    status, flow = _request(
        base, "/api/config/config_entries/options/flow", {"handler": entry_id}, token
    )
    if status != 200:
        raise SystemExit(
            f"starting the options flow failed with status {status}: {flow}"
        )
    flow_id = _text(flow, "flow_id")
    if flow_id is None:
        raise SystemExit(f"the options flow did not name itself: {flow}")

    payload: Mapping[str, object] = {
        OPTION_URL: url,
        OPTION_EDITOR_URL: editor_url,
        OPTION_TOKEN: admin_token,
    }
    if _already_set(flow, payload):
        return False, f"the house already casts through {url}; nothing to set"
    for _ in range(_MAX_STEPS):
        status, step = _request(
            base, f"/api/config/config_entries/options/flow/{flow_id}", payload, token
        )
        if status not in (200, 201):
            raise SystemExit(f"the options flow failed with status {status}: {step}")
        kind = _text(step, "type")
        if kind == "create_entry":
            return True, f"the house now casts through {url}"
        if kind == "abort":
            reason = _text(step, "reason") or "no reason given"
            raise SystemExit(f"the options flow aborted: {reason}")
        if kind != "form":
            raise SystemExit(f"the options flow asked something unexpected: {step}")
        # A second form is Home Assistant confirming a choice it could not take
        # from the first; the same answer accepts it.
        payload = {}

    raise SystemExit(f"the options flow did not settle after {_MAX_STEPS} steps")


def _already_set(flow: object, wanted: Mapping[str, object]) -> bool:
    """Whether this house's options already are what this run would write.

    Read out of the **form the options flow just showed**, and not out of the
    entry listing -- which is the trap this function exists because of. The REST
    listing reports every entry without its options: `options` is `null` for all
    of them, so a check against *that* matches nothing and never has, and every
    run went on to rewrite the options anyway. A rewrite is a config-entry
    reload, and a reload takes the rooms and the engine down to change a string
    that was already right -- which is precisely what this tool's docstring
    promises does not happen.

    A form carries the current options as its fields' `default`s, which is the
    same state by another route. The started flow is left unfinished when this
    answers `True`: Home Assistant expires an unfinished options flow by itself,
    so aborting it would be a second call saying what the first already said.
    """
    rows = _field(flow, "data_schema")
    if not isinstance(rows, list):
        return False
    for key, value in wanted.items():
        default: object = None
        for row in cast("list[object]", rows):
            if _text(row, "name") == key:
                default = _field(row, "default")
                break
        else:
            return False
        if str(default or "") != str(value):
            return False
    return True


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
    """Point the house's Node-RED option at an address, and say what it did."""
    parser = argparse.ArgumentParser(
        description="Tell a headless Home Assistant where its Node-RED is."
    )
    parser.add_argument("--base", default="http://localhost:8123")
    parser.add_argument(
        "--url",
        default="http://nodered:1880",
        help="the Node-RED address as this stack resolves it (the compose "
        "service name), which is also what the panel links to",
    )
    parser.add_argument(
        "--editor-url",
        default="http://localhost:1880",
        help="the Node-RED address the *browser* opens the editor at, which is "
        "the published port here and the add-on's ingress path on Home "
        "Assistant OS; the panel links to this one",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Node-RED's admin password, only for a Node-RED with `adminAuth` "
        "set; the dev container has none",
    )
    parser.add_argument("--env", default=".env.local")
    arguments = parser.parse_args(argv)

    changed, detail = ensure_node_red(
        arguments.base,
        _token(arguments.env),
        arguments.url,
        admin_token=arguments.token,
        editor_url=arguments.editor_url,
    )
    print(f"{'set' if changed else 'present'}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
