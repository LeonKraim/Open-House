"""A cast, detached from its row into a module of its own.

The requirement, in the user's words: "fully detach a cast from the input and make
it its own module, with a button next to it". A cast is a small piece of logic
attached to a row, so detaching it means writing that same logic into a document
of its own -- which is what lets the source row stop holding the logic and point
at *it* instead, with the `output` binding that already exists for "the value
another module published".

Three things about a cast, and only two of them travel:

* **The logic travels.** A template is its text, a condition is its condition, a
  script is the call, and a flow is the entity it writes.
* **The name and the room are new**, and they are the person's- because a module
  is placed, and the placement is the whole of what makes it a module rather than
  a value in a record.
* **When it runs does not travel, and this is the part that is easy to miss.** A
  cast is worked out *as part of the run it sits in*: the row's value is computed
  whenever its host automation happens to run, which is why a cast costs nothing
  to add. A module of its own has no such run, so a detached cast has to be told
  what starts it. Where the cast names the entities itself -- a condition carries
  the `entity_id`s it decides about, and a flow writes an entity Open House made
  -- the trigger is *derived* from the cast. Where it does not -- a template, a
  script -- the person is asked, because nothing here can work out what a
  sequence of their own should watch.

Nothing in this module talks to Home Assistant. A cast goes in and a document
comes out, and `modules.async_host` hosts it: the same path an import takes. A
detached cast is not a new kind of thing, which is what makes it show up in the
room's module list, the store, and every screen that already lists modules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ha_adapter.pack_authoring import AuthoringError

__all__ = [
    "CASTS",
    "VALUE",
    "Detached",
    "detached_document",
    "watched_by",
]

#: The four casts a row may hold, in the spelling the panel and the record use.
CASTS = ("condition", "template", "flow", "script")

#: The variable a detached cast's value is written into, and so the candidate its
#: output is picked from.
#:
#: One name for all four kinds, because a person reading the new module should see
#: the same thing whatever the logic came from -- and the publisher's placement
#: rule is about *where a name comes into scope*, which is the top of the action
#: list for every one of them. Prefixed like a script's own variable
#: (`module_host.script_variable`) so a reader of the document can see which name
#: Open House added, and so a `variables:` the blueprint wrote cannot collide with
#: it.
VALUE = "oh_value"

#: The response variable a detached script's answer comes back under, before it is
#: named into `VALUE`. Separate from `VALUE` because the two are separate acts --
#: the call hands a value back, and the document then names it the way the output
#: reads it -- and because a call's `response_variable` is Home Assistant's own
#: field rather than anything in the document's own `variables:`.
RESPONSE = "oh_response"

#: The shape an output key may take, the same one `module_host` enforces.
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Detached:
    """A cast as a document of its own, and what the new module publishes.

    `pick` is the candidate to tick and the name to publish it under, and it is
    answered rather than left to the screen because the document is written here:
    the variable it names is one this module put there, so a screen guessing it
    from the outside would be guessing at the thing that was just decided.
    """

    document: Mapping[str, Any]
    pick: tuple[str, str]
    #: The entities the document watches, whether derived from the cast or given
    #: by the person. Reported so the screen can say what it did rather than
    #: leaving the person to open the module to find out.
    watched: tuple[str, ...]


def watched_by(condition: object) -> tuple[str, ...]:
    """The entities a condition config names, which is what a trigger can watch.

    **A walk for `entity_id`, and that is a heuristic rather than a reading.**
    Home Assistant decides a condition by asking the condition's own class, which
    lives in the instance; what this has instead is the config a person built in
    the panel's condition editor. Every condition Home Assistant ships names what
    it decides about under `entity_id` -- one id or several, at whatever depth the
    builder nested it -- so walking for that key finds them.

    What it does not find is a condition that names no entity at all: a template
    condition, a `numeric_state` over an attribute of nothing in particular, a
    `time` condition. Those answer with nothing, and the person is asked for a
    trigger instead of being handed a module that would never run.
    """
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key == "entity_id":
                    items = value if isinstance(value, (list, tuple)) else (value,)
                    for item in items:
                        if isinstance(item, str) and item and item not in found:
                            found.append(item)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(condition)
    return tuple(found)


def detached_document(
    *,
    title: str,
    input_name: str,
    cast: str,
    template: str = "",
    condition: object = None,
    script: str = "",
    flow_entity: str = "",
    trigger: Iterable[str] = (),
) -> Detached:
    """One cast as an automation of its own, with the output it publishes.

    `trigger` is the person's answer where the cast cannot supply one -- and it is
    *added to* what a condition or a flow derives rather than replacing it, so a
    row watching three entities of its own can be given a fourth thing to watch
    without losing the three it had.
    """
    if cast not in CASTS:
        raise AuthoringError(
            f"{cast!r} is not a cast: a row is answered with a condition, a "
            "template, a flow or a script, and there is nothing to detach from "
            "a row that holds a value"
        )
    actions: list[Any] = []
    derived: tuple[str, ...] = ()
    if cast == "condition":
        if condition is None or condition == {} or condition == []:
            raise AuthoringError(
                "the condition cast is empty: a condition nobody has written "
                "decides nothing, so there is nothing to detach"
            )
        derived = watched_by(condition)
        # **Two arms, because a condition decides rather than computes.** What
        # Home Assistant gets is the person's own condition config, unchanged,
        # inside an `if` -- nothing here reads it. Both arms name the value,
        # because an output is a value and "false" is one; the publisher goes
        # into whichever arm ran (`module_host.publish_actions` places a
        # branch-local variable in its own branch).
        actions.append(
            {
                "if": condition,
                "then": [{"variables": {VALUE: "on"}}],
                "else": [{"variables": {VALUE: "off"}}],
            }
        )
    elif cast == "template":
        text = template.strip()
        if not text:
            raise AuthoringError(
                "the template cast is empty: there is nothing written to detach"
            )
        actions.append({"variables": {VALUE: text}})
    elif cast == "flow":
        if not flow_entity:
            raise AuthoringError(
                "this row's flow has no entity yet: the flow is pushed when the "
                "module is saved, so save it first and detach it after"
            )
        derived = (flow_entity,)
        # A flow already runs on its own and writes its entity, so nothing starts
        # this module -- it *follows* the flow. That is the whole difference
        # between a flow and the other three, and it is why detaching one needs no
        # answer from the person.
        actions.append({"variables": {VALUE: f"{{{{ states('{flow_entity}') }}}}"}})
    else:
        call = _script_id(script)
        actions.append({"action": f"script.{call}", "response_variable": RESPONSE})
        actions.append({"variables": {VALUE: "{{ " + RESPONSE + " }}"}})

    watched = tuple(dict.fromkeys((*derived, *trigger)))
    if not watched:
        raise AuthoringError(
            "a detached cast needs something to start it, and this one names no "
            "entity of its own: pick what should run it -- the cast is worked out "
            "whenever its module runs, and a module with nothing to run it never "
            "does"
        )
    return Detached(
        document={
            "alias": title,
            "trigger": [{"trigger": "state", "entity_id": list(watched)}],
            "action": actions,
        },
        pick=(VALUE, key_for(input_name)),
        watched=watched,
    )


def key_for(input_name: str) -> str:
    """The input's name as an output key: the shape every name in the tree takes.

    An input name is free to carry characters an output key cannot -- a `min_lux_%`
    is a perfectly good input and not a key -- so runs of anything outside the key
    alphabet become one underscore, and the underscores at either end go. A name
    that begins where a key cannot (a digit) is prefixed rather than refused, and
    a name with nothing a key can hold at all becomes `value`: the person is naming
    a value they already have, and there is no second name to ask them for.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", input_name.lower()).strip("_")
    if not slug:
        slug = "value"
    if not _KEY.match(slug):
        slug = f"detached_{slug}"
    return slug


def _script_id(script: str) -> str:
    """The id a `script.` call takes, from however the panel handed it over."""
    call = re.sub(r"^script\.", "", script.strip())
    if not call:
        raise AuthoringError(
            "this row's script cast names no script: pick the script the row is "
            "answered by, then detach it"
        )
    return call


def flow_entity_for(module: str, input_name: str) -> str:
    """The entity a detached flow cast is read from.

    Spelled here rather than passed in, so a caller cannot invent a second
    spelling of it: `module_host.flow_entity_id` is the one authority on what the
    entity is called, and this is the one place a *detach* asks it -- for the
    module the cast came from, which is the module whose flow wrote the entity.
    """
    from ha_adapter import module_host

    return module_host.flow_entity_id(module, input_name)


def triggers_from(entities: Sequence[str]) -> list[dict[str, Any]]:
    """One state trigger over every entity, as the document's own `trigger:`.

    Kept out of `detached_document` so the shape has one spelling: a module
    watching four entities watches them with one trigger listing four, which is
    how a person reading the document would write it and how Home Assistant
    reports it back.
    """
    return [{"trigger": "state", "entity_id": list(entities)}]
