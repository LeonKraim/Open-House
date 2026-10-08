"""Hosting an imported blueprint as a Home Assistant automation, and wiring it up.

The Dev tab used to *translate* a blueprint into an engine pack
(`pack_authoring.draft_module`), and the translation could not be faithful: the
engine's interpreter gates on one watched slot's state and a duration, and
`catalog/pack-policy.yaml` forbids `choose`, `if`, `repeat`, `parallel`,
`variables`, `stop`, the waits and every template by name. Most real blueprints
are built from exactly those, so a "translated" module was the blueprint with its
logic removed -- and, worse, with the parts the converter did keep firing
unconditionally, because the `match` clause the interpreter actually reads was
never written.

This module takes the other road, the one the house already runs on: an imported
blueprint **stays a Home Assistant automation**. The blueprint's own body is
resolved against the person's inputs -- `!input name` replaced by the chosen value
or the declared default, the `blueprint:` block dropped -- and that is the
automation Home Assistant is asked to run. Nothing is re-expressed, so nothing is
lost: every branch, template, wait and device action arrives exactly as written.

What Open House adds is one thing, and it is an addition rather than a rewrite:
the **make-public step**. A module may declare *outputs* -- values a person ticked
at import out of what the blueprint itself carries (its `variables:`, its entity
inputs, its `response_variable`s) -- and each becomes a real Home Assistant entity
another module can read. The step that copies an output's value into its entity is
appended to the automation, at the position the value is actually in scope.

Pure by construction: this module imports `yaml` and the standard library and
names no Home Assistant type, for the reason the whole `ha_adapter` package gives
-- the integration that runs it is the only place Home Assistant is present.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from . import slot_parts
from .pack_authoring import (
    _TRIGGER_KEYS,
    INPUT_MARKER,
    AuthoringError,
    _Loader,
    blueprint_inputs,
)

__all__ = [
    "HOUSE_SCOPE",
    "ROOM_SCOPE",
    "BoundSlots",
    "HostedSource",
    "InputBinding",
    "Output",
    "OutputCandidate",
    "action_key",
    "answer_with_scripts",
    "bind_inputs",
    "binding_to_entity",
    "cast_answers",
    "config_id_for",
    "declare_outputs",
    "declared_variable_names",
    "derived_entity_id",
    "flow_entity_id",
    "instantiate",
    "output_candidates",
    "output_entity_id",
    "publish_actions",
    "read_module_source",
    "resolve_slots",
    "script_binding",
    "script_calls",
    "script_variable",
    "slot_key",
    "slots_reached",
    "unresolved_slots",
    "unset_settings",
]


#: The one service a hosted automation calls to publish an output. It is the
#: integration's own (`custom_components/open_house`), because a plain
#: `sensor`'s state cannot be written by a service in Home Assistant -- an
#: automation has no `sensor.set_state`. Routing the write through Open House is
#: what lets an output be a real entity *and* be written from inside a running
#: automation, which are the two halves of what a module output is.
PUBLISH_SERVICE = "open_house.publish_output"

#: The shape of an output key, matching the manifest's option keys and every
#: other name the tree writes: lower case, underscored, no leading digit.
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

#: The action-list keys Home Assistant accepts, old spelling first. A blueprint
#: may write either, and the publish step has to be appended to whichever the
#: blueprint used.
_ACTION_KEYS = ("action", "actions")

#: The keys whose value is a list of actions. `sequence` is a branch body -- the
#: actions a `choose`/`if`/`repeat` element runs -- and it is a scope a publisher
#: may need, so it is walked beside the automation's own actions.
#:
#: `then`, `else` and `default` are the same thing under other names: an `if`
#: element carries its two arms under `then`/`else`, and a `choose` branch its
#: fallback under `default`. They are action lists and they are scopes, so a
#: variable set inside one is in scope for that list and nowhere else -- leaving
#: them out is not a missed optimisation, it is a publisher appended to the
#: top-level list for a name that is not defined there, which publishes nothing
#: and logs a template error every time the module runs.
_SEQUENCE_KEYS = ("action", "actions", "sequence", "then", "else", "default")

#: The key whose value is a *mapping* of action lists rather than one list: a
#: `parallel` element runs each of its named branches at once, so each is a scope
#: of its own and none of them is an action list the walk above would see.
_PARALLEL_KEY = "parallel"

#: The value kinds an output may be, and the Home Assistant side each implies.
#: `enum` is a `string` with a fixed membership; the entity carries the members as
#: its options so a dashboard can show a choice rather than free text.
OUTPUT_KINDS = ("number", "boolean", "string", "enum")


@dataclass(frozen=True)
class OutputCandidate:
    """One thing a blueprint carries that could be published, before a person chose.

    The candidates are the blueprint's own: a top-level `variables:` entry (the
    blueprint computed it and named it), an entity input (the module observes it),
    a `response_variable` (the blueprint received it back from a call), or -- the
    last kind, and the one that is about what the blueprint *does* rather than
    what it names -- a service call that acts on a device, offered both as the
    devices it acted on and as each value it set on them. Naming them is what lets
    the screen show "here is what this blueprint carries" rather than asking a
    person to invent an output the source cannot fill.
    """

    #: What the blueprint calls it -- the variable name, `input:<name>`, or
    #: `service:<service_id>[:<data key>]`.
    name: str
    #: `variable`, `entity`, `response` or `service`.
    kind: str
    #: A guess at the value kind, from the shape of what the blueprint set it to.
    value_kind: str
    #: The expression that reads it at publish time, in Home Assistant's language.
    expression: str
    #: Whether the value is set inside a branch, and so only in scope there. A
    #: `True` here is not a refusal: the publisher is appended to the branch that
    #: defines it, and the flag is what the screen shows so a person knows the
    #: output is not reported on every run.
    branch_only: bool = False
    #: Whether `expression` is *already* template text rather than something to be
    #: wrapped in `{{ }}`. A value a call set is written the way the call wrote it:
    #: `'{{ brightness | int }}'` is a template and `60` is a number, and wrapping
    #: the first produces `{{ {{ brightness | int }} }}`, which is not a template
    #: Home Assistant can render. Only the action-derived candidates set this.
    template: bool = False
    #: Where in the action tree the value is read from, when the value only exists
    #: at one action -- the path to the call, as the walk found it. Empty for
    #: everything read from a name, which is in scope at the end of the automation
    #: or of the branch that defines it.
    #:
    #: This is what puts the publisher **immediately after the call** rather than
    #: at the end of the list: what a call set is true at that moment, and a
    #: branch that ran earlier may have set something else since.
    after: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Output:
    """A published value: what a person ticked, named and typed."""

    key: str
    #: The blueprint expression the value is read from, verbatim.
    expression: str
    kind: str
    #: The names of the `variables:` the expression reads, so the publisher knows
    #: which action lists it is in scope in. Empty for a reading, which is in scope
    #: everywhere inside the automation.
    variables: tuple[str, ...] = ()
    #: The path to the action this value is read *from*, for a value a call set:
    #: the publisher is inserted immediately after that action. See
    #: `OutputCandidate.after`, which is where it comes from.
    after: tuple[Any, ...] = ()
    #: Whether `expression` is already template text, as `OutputCandidate.template`.
    template: bool = False


#: The two places a slot answer is looked up. `ROOM_SCOPE` (the default) is the
#: module's own room, with the house's global binding standing behind it; a
#: `HOUSE_SCOPE` answer is the house's own binding and *only* that, so it means
#: the same device in every room even where a room bound the slot itself.
ROOM_SCOPE = "room"
HOUSE_SCOPE = "house"


@dataclass(frozen=True)
class InputBinding:
    """How one of a module's inputs is filled.

    `literal` is a value a person typed, `entity` a device id they picked, and
    `output` another module's published value -- which is the dataflow this whole
    mechanism exists for. `value` carries the first two; `module` and `key` carry
    the third, because an output is named by who published it and what they
    called it rather than by where it happens to live.

    `slot` is the fourth and it is a *promise* rather than an answer: the input
    is filled with whatever device the module's room binds under `slot`, looked
    up when the automation is built rather than written down when it was
    imported. That is the whole difference between it and `entity`, and it is
    what makes the device changeable afterwards -- point the room's `lux_sensor`
    at another sensor and every module reaching through `lux_sensor` is built
    again against the new one. `slot` carries the name; `resolve_slots` is what
    turns it into the `entity` binding it stands for.

    `scope` says *where* that slot is looked up, and it is the whole of what a
    **global slot** is. A `ROOM_SCOPE` answer is the module's own room's binding
    (the house's global one standing behind it), which is the ordinary case and
    the default, so every record written before this field existed still reads
    the same. A `HOUSE_SCOPE` answer names the house's own global binding and
    ignores whatever the room bound for that slot -- "the *house's* lights", in
    every room, even in a room that lights itself with something else. See
    `resolve_slots` for the two mappings the resolution reads.

    `part` narrows the same promise to **one part of a split slot**
    (`ha_adapter.slot_parts`): the person divided `light_group` into `a` and `b`
    so this module could have a device the others do not share, and the answer
    resolves under `light_group__a` rather than under the slot's own key. Empty
    -- the slot itself -- is the default, which is what makes every record written
    before a slot could be split still resolve exactly as it did.
    """

    kind: str
    value: Any = None
    module: str = ""
    key: str = ""
    slot: str = ""
    scope: str = ROOM_SCOPE
    part: str = ""


class BoundSlots(Mapping[str, str]):
    """The two views of a slot's binding, carried as one value.

    `room` is what a `ROOM_SCOPE` binding resolves to: the house's own binding
    with the room's over the top (`host.bound_slots`), which is what a slot has
    always meant. `house` is the house's own binding alone, and it is what a
    `HOUSE_SCOPE` -- **global** -- binding resolves to, because the point of a
    global slot is that it is the house's device in *every* room, including a
    room that bound the same name for itself.

    The two travel together because every caller that can resolve a slot has both
    to hand, and a resolution handed only one of them would get a global slot
    wrong exactly where it matters -- in a room that bound the slot itself. A
    plain mapping is still accepted wherever these two are (`resolve_slots` falls
    back to using it as the house view too), which is right for a house-scoped
    module whose room *is* the house.
    """

    __slots__ = ("house", "room")

    def __init__(self, room: Mapping[str, str], house: Mapping[str, str]) -> None:
        self.room = room
        self.house = house

    def __getitem__(self, key: str) -> str:
        return self.room[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.room)

    def __len__(self) -> int:
        return len(self.room)

    def __repr__(self) -> str:
        return f"BoundSlots(room={self.room!r}, house={self.house!r})"

    def __eq__(self, other: object) -> bool:
        # Compared as the mapping it is, so a caller holding the room-scope view
        # -- which is all a `BoundSlots` *is* to every reader that does not ask
        # for `house` -- can compare it against the plain dict it used to be.
        if isinstance(other, BoundSlots):
            return self.room == other.room and self.house == other.house
        if isinstance(other, Mapping):
            return dict(self) == dict(other)
        return NotImplemented


@dataclass
class HostedSource:
    """A blueprint parsed for hosting: its inputs, and its body.

    Named apart from `pack_authoring.Source` because the two are different
    readings of the same document: that one asks what a *pack* could be made of
    the document, this one asks what the document is.
    """

    document: Mapping[str, Any]
    title: str
    description: str
    inputs: Mapping[str, Mapping[str, Any]]
    #: The action lists, in document order, that this source's publishes may need
    #: to be appended to. Filled by `instantiate`, not here.
    inputs_order: tuple[str, ...] = ()


def read_module_source(text: str) -> HostedSource:
    """Parse a blueprint or automation a person is turning into a module.

    Deliberately more permissive than `pack_authoring.read_source`: a document
    with no trigger or no action is still hosted (a person may be importing a
    script-shaped thing), so the only refusals here are a document that is not a
    mapping and one that will not parse. "Nothing that cannot be imported" is the
    requirement this function answers to.
    """
    try:
        loaded: object = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as error:
        raise AuthoringError(f"the document is not valid YAML: {error}") from error
    if not isinstance(loaded, Mapping):
        raise AuthoringError(
            "the document is not a mapping: a blueprint and an automation are "
            "both `key: value` documents, and this is not one"
        )
    block = loaded.get("blueprint")
    if isinstance(block, Mapping):
        inputs = blueprint_inputs(block)
        return HostedSource(
            document=loaded,
            title=str(block.get("name") or "Blueprint"),
            description=_prose(block.get("description")),
            inputs=inputs,
            inputs_order=tuple(inputs),
        )
    return HostedSource(
        document=loaded,
        title=str(loaded.get("alias") or "Automation"),
        description=_prose(loaded.get("description")),
        inputs={},
    )


# --------------------------------------------------------------------------
# Instantiating the blueprint, so Home Assistant can run it
# --------------------------------------------------------------------------


def instantiate(
    source: HostedSource,
    inputs: Mapping[str, Any],
    *,
    alias: str | None = None,
) -> dict[str, Any]:
    """The automation Home Assistant runs: the blueprint's body, inputs filled.

    Every `!input name` becomes the person's choice for `name`, or the input's
    declared `default` when they named none -- which is exactly what Home
    Assistant's own blueprint editor does when it saves an automation, and the
    reason nothing is lost: the body is copied, not understood.

    The `blueprint:` block is dropped, because an automation has no such block;
    what it named survives in the `alias` and `description` this writes.
    """
    document = source.document
    body = {key: value for key, value in document.items() if key != "blueprint"}
    resolved = _substitute(body, source.inputs, inputs)
    if not isinstance(resolved, Mapping):
        raise AuthoringError("the blueprint body is not a mapping")
    result = dict(resolved)
    result["alias"] = str(alias or source.title)
    if source.description and "description" not in result:
        result["description"] = source.description
    return result


def _substitute(
    node: object,
    declared: Mapping[str, Mapping[str, Any]],
    chosen: Mapping[str, Any],
) -> object:
    """Walk `node`, replacing `!input` markers with the value they stand for.

    An input the person named is their value; one they left is the input's
    `default`, as `declares_default` reads it -- which is the presence of the key,
    except on an input that takes a device, where the author's `default` names a
    device this installation does not have and is not an answer at all. An input
    with no answer either way is a blueprint that cannot run, and it is named
    rather than silently blanked.
    """
    if isinstance(node, Mapping):
        if set(node) == {INPUT_MARKER}:
            name = str(node[INPUT_MARKER])
            if name in chosen:
                return chosen[name]
            block = declared.get(name)
            if declares_default(block):
                return block["default"]
            raise AuthoringError(
                f"the blueprint input {name!r} has no value and no default this "
                "module can use, so the automation cannot be built without one"
            )
        return {
            key: _substitute(value, declared, chosen) for key, value in node.items()
        }
    if isinstance(node, list):
        return [_substitute(item, declared, chosen) for item in node]
    return node


# --------------------------------------------------------------------------
# What a blueprint carries, which is what a person may publish
# --------------------------------------------------------------------------


def output_candidates(
    source: HostedSource,
    chosen: Mapping[str, Any] | None = None,
    bindings: Mapping[str, InputBinding] | None = None,
    casts: Mapping[str, str] | None = None,
) -> tuple[OutputCandidate, ...]:
    """Everything the blueprint itself carries that could become an output.

    Four kinds, in the order a person would think of them:

    - the blueprint's own `variables:`, at the top level and inside branches --
      the values it computed and named, which is what the requirement means by
      "the predefined stuff in the blueprint for this";
    - its entity inputs, read as `states('<the entity you bound>')` -- the module
      observes them and republishing the reading is the simplest useful output;
    - its `response_variable`s, the values a call handed back;
    - **what it does**: every service call that names a device, offered as the
      devices it acted on and as each value it set on them. A blueprint's
      `light.turn_on` with `brightness: '{{ brightness | int }}'` is a device
      being driven, and the brightness it was driven to is a reading somebody
      wants -- `service:light.turn_on` and `service:light.turn_on:brightness`.

    A `variables:` entry that is a plain scalar and not a template is typed from
    its literal, because the value is known; a template is typed `string` and the
    person overrides it, because only they know what it computes.

    `chosen` is the person's current input choices, and it is needed for the
    second and fourth kinds: an entity input's reading is over the entity they
    *bound*, and a call's device is the `!input` they answered, which are not
    known until they have. An entity input still unbound is not offered -- a
    reading of no entity is not a candidate, it is a blank.

    `bindings` is the person's choices as *answers* rather than as values, and it
    is passed for one case: an input answered with a **slot**. A slot is a device
    the room binds, so an input answered that way has no entity to read *yet* --
    and the reading of it is still exactly the kind of value a person wants
    published, because the room that eventually binds the slot is where the
    reading comes from. So it is offered, with no expression, rather than hidden
    until the room has a device: a module is defined before it has a room, and a
    candidate list that changed shape between defining a module and installing it
    would be a screen that offers less than the thing can do. The expression is
    written when the module is built, from the entity the room answered with --
    which is why the empty one is never published: a module with a slot nobody
    has bound has no automation at all.

    `casts` names the inputs answered with *logic* rather than with a value, each
    offered as `cast:<name>` -- see `cast_answers`, which is where the map comes
    from and why a template counts. **This is the whole of "expose it to the rest
    of the house".** A row answered with a condition, a flow, a script or a
    template is already holding the thing: the entity Open House made for the
    condition, the entity the flow writes, the value the script hands back, the
    expression the person wrote. Publishing one is therefore not a new kind of
    output but one more line in the list a person may tick, and it reads the way
    the answer reads -- `states('<the entity it holds>')`, or the template text
    itself.
    """
    found: list[OutputCandidate] = []
    seen: set[str] = set()

    for name, value, branch in _variables(source.document):
        if name in seen:
            continue
        seen.add(name)
        found.append(
            OutputCandidate(
                name=name,
                kind="variable",
                value_kind=_kind_of(value, source.inputs),
                expression=name,
                branch_only=branch,
            )
        )

    for name, block in source.inputs.items():
        if not _is_entity_input(block):
            continue
        entity_id = _bound_entity(chosen, name)
        if entity_id is None and not _answers_with_a_slot(bindings, name):
            continue
        key = f"input:{name}"
        if key in seen:
            continue
        seen.add(key)
        found.append(
            OutputCandidate(
                name=key,
                kind="entity",
                value_kind="string",
                # Empty until a room answers the slot it is bound with. See the
                # note above on why the candidate is offered anyway.
                expression=f"states('{entity_id}')" if entity_id else "",
            )
        )

    # A row answered with a cast: the second thing this module may publish, and
    # the one a person asks for by name. Offered beside the readings above rather
    # than instead of them, because the two are different questions -- an entity
    # input's reading is what the module *observes*, and this is what its own
    # logic *worked out*.
    for name, cast in (casts or {}).items():
        block = source.inputs.get(name)
        if block is None:
            continue
        key = f"cast:{name}"
        if key in seen:
            continue
        binding = (bindings or {}).get(name)
        # **An empty expression here is not a hole, it is a module not built
        # yet.** A person ticking this on a fresh import has named no module, so
        # there is no slug to spell the entity a condition makes or the variable
        # a script fills -- and the reading is written when the module is built,
        # from the answer that carries it. The same is true of a reading over a
        # slot nobody has bound, and it is offered for the same reason: a
        # candidate list that changed shape between importing a blueprint and
        # installing the module would be a screen offering less than the module
        # can do.
        expression, template = ("", False)
        if binding is not None:
            reading = _reading_of(binding)
            if reading is None:
                # An answer with no one state to read, and no template in it
                # either -- a target bound to several entities, say. The row *is*
                # answered; it just has nothing this could publish.
                continue
            expression, template = reading
        seen.add(key)
        found.append(
            OutputCandidate(
                name=key,
                kind="cast",
                # **A condition is a yes-or-no whatever it answers.** The row it
                # sits on may be a number input -- every cast is offered on every
                # row -- but the value published here is the state of the binary
                # sensor Open House made for the condition, and calling that a
                # number would put `on` behind a number sensor.
                value_kind=(
                    "boolean" if cast == "condition" else _kind_from_selector(block)
                ),
                expression=expression,
                template=template,
            )
        )

    for name in _response_variables(source.document):
        if name in seen:
            continue
        seen.add(name)
        found.append(
            OutputCandidate(
                name=name,
                kind="response",
                value_kind="string",
                expression=name,
                branch_only=True,
            )
        )

    for path, call, branch, service in _service_calls(source.document):
        if not _names_a_device(call):
            continue
        devices = _acted_on(call, source, chosen)
        if devices:
            found.append(
                OutputCandidate(
                    name=_unique(seen, f"service:{service}"),
                    kind="service",
                    value_kind="string",
                    # The ids themselves, as text: what it acted on is a fact
                    # about the run rather than a template over a state, and
                    # text with no braces in it renders to itself.
                    expression=", ".join(devices),
                    template=True,
                    branch_only=branch,
                    after=path,
                )
            )
        for key, value in _set_values(call, source, chosen):
            written = _value_expression(value)
            if written is None:
                continue
            expression, template, value_kind = written
            found.append(
                OutputCandidate(
                    name=_unique(seen, f"service:{service}:{key}"),
                    kind="service",
                    value_kind=value_kind,
                    expression=expression,
                    template=template,
                    branch_only=branch,
                    after=path,
                )
            )

    return tuple(found)


def cast_answers(
    bindings: Mapping[str, InputBinding] | None,
    *,
    conditions: Iterable[str] = (),
    flows: Iterable[str] = (),
    scripts: Iterable[str] = (),
) -> dict[str, str]:
    """Which inputs are answered with *logic* rather than with a value, and which.

    The names come from the two places that know them, and both are needed.
    Three of the four casts are recorded beside the answers -- `derived` for a
    condition, `flows`, `scripts` -- because the record has to remember them for
    the build. A **template** is not recorded anywhere, and it does not need to
    be: it *is* the answer, so the binding says it. A row whose answer is
    template text is a row something works out at run time rather than a value,
    and that is the definition rather than a guess about intent.

    Nothing here inspects a binding to work out that it is a *cast* -- it cannot.
    An entity id is what a person's own device pick looks like, and a template is
    what a person's own template box looks like, so the binding map alone cannot
    tell a row somebody cast from a row somebody typed into. That is exactly why
    the other three are recorded.

    Asked by both sides that need it, which is why it is one function and not two
    loops: the build, publishing what a person ticked, and the read, offering the
    candidates to tick.
    """
    named = dict.fromkeys(conditions, "condition")
    named.update(dict.fromkeys(flows, "flow"))
    named.update(dict.fromkeys(scripts, "script"))
    for name, binding in (bindings or {}).items():
        if name not in named and _is_template_text(binding.value):
            named[name] = "template"
    return named


def _reading_of(binding: InputBinding) -> tuple[str, bool] | None:
    """How an answered input is read, as an expression and whether it already is one.

    Two answers, and between them they cover every cast. A **device** the row
    holds -- an entity a person picked, the binary sensor a condition made, the
    entity a flow writes -- is read as its state. Anything else is text, and a
    template among it is handed over as the template it is: wrapping `{{ ... }}`
    again renders to nothing, which is the same rule `_publish` applies.

    `None` for an answer that is neither, which is a target bound to several
    entities: there is no one state a reading could be, and picking the first
    would be inventing a value rather than reporting one.
    """
    value = binding.value
    if binding.kind == "entity":
        return (
            (f"states({value!r})", False) if isinstance(value, str) and value else None
        )
    return (value, True) if _is_template_text(value) else None


def _is_template_text(value: object) -> bool:
    """Whether a value is template text, which Home Assistant renders and this does not.

    Home Assistant's own reading of the question, and the reason it is asked of
    the *text* rather than of a cast mode: a template is not stored as a flag
    anywhere -- the string is the whole of it, and where it came from (a cast, a
    blueprint's default, a person typing `{{ }}` into a text box) makes no
    difference to what has to be done with it.
    """
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def declare_outputs(
    source: HostedSource,
    chosen: Mapping[str, Any] | None,
    picked: Sequence[tuple[str, str]],
    bindings: Mapping[str, InputBinding] | None = None,
    casts: Mapping[str, str] | None = None,
) -> tuple[Output, ...]:
    """The outputs a person ticked, as the declarations the automation publishes.

    `picked` is what the screen sent back: the candidate's own name, and the
    output key the person called it. Both are needed because a candidate's name
    is the blueprint's (`input:lux_sensor` is not an output name, and `min_lux`
    may be a name they want shorter), and the key is what the entity is called
    and what a consumer binds to.

    **A variable's publisher has to know it is a variable.** The expression is
    the variable's own name, and naming it in `variables` is what lets
    `publish_actions` place the step in the branch that defines it rather than at
    the end of an automation where it is no longer in scope. A reading or a
    response hands over a *value*, so it is in scope everywhere and carries no
    names.

    `bindings` is handed on to `output_candidates`, and is what makes a pick on an
    input answered with an unbound slot a pick rather than a refusal: the
    candidate exists, so the person's tick is honoured, and its expression is the
    one the room's device earns when there is one.

    `casts` likewise, and for the same reason: a pick on an input answered with a
    cast is a pick because the candidate is offered. A cast output is appended at
    the end of the action list like any other reading -- it names no `variables:`
    -- and that placement is right for all four. The condition and the flow are
    entities, always readable. A template is rendered where the publisher lands.
    And a **script**'s returned value is in scope from the call (which
    `answer_with_scripts` puts at the top of the action list) to the end of the
    run, which is where the publisher lands too.
    """
    candidates = {
        candidate.name: candidate
        for candidate in output_candidates(source, chosen, bindings, casts)
    }
    declared: list[Output] = []
    seen: set[str] = set()
    for name, key in picked:
        candidate = candidates.get(name)
        if candidate is None:
            raise AuthoringError(
                f"the source carries nothing called {name!r} to publish: an "
                "output has to be one of the values the source itself computes "
                "or reads"
            )
        if not _KEY.match(key):
            raise AuthoringError(f"{key!r} is not an output name")
        if key in seen:
            raise AuthoringError(
                f"two outputs are both called {key!r}, so they would share one "
                "entity and one would overwrite the other"
            )
        seen.add(key)
        declared.append(
            Output(
                key=key,
                expression=candidate.expression,
                kind=candidate.value_kind,
                # A variable and a `response_variable` are both names that come
                # into scope at a particular point in the action tree, so both
                # have to be placed rather than appended blind. A reading is a
                # value, in scope everywhere.
                variables=(name,) if candidate.kind in ("variable", "response") else (),
                # What a call set is in scope right there and nowhere else -- not
                # even at the end of the branch that ran it, because a later
                # action may have changed it. So a service candidate carries the
                # action it belongs to and the publisher goes beside it.
                after=candidate.after,
                template=candidate.template,
            )
        )
    return tuple(declared)


def _bound_entity(chosen: Mapping[str, Any] | None, name: str) -> str | None:
    """The entity id a person bound to an entity input, or `None` when unbound.

    Two shapes, because a `target` input's binding is wrapped (`{entity_id: …}`)
    and a plain `entity` input's is the id itself -- and both are a single entity
    whose state is readable, which is all this answers. A target bound to several
    entities answers `None`: there is no one state a reading could be, and
    picking the first would be inventing an answer rather than reporting one.
    """
    value = (chosen or {}).get(name)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        entity_id = value.get("entity_id")
        if isinstance(entity_id, str) and entity_id:
            return entity_id
    return None


def _answers_with_a_slot(
    bindings: Mapping[str, InputBinding] | None, name: str
) -> bool:
    """Whether an input was answered with a slot rather than with a device.

    An input answered with a slot has no entity only while the slot is unbound --
    once it is bound, `resolve_slots` has already turned it into an entity
    binding and the value is where every other value is. So this asks the
    *bindings*, which are what the person said, rather than the values, which are
    what the house could answer.
    """
    binding = (bindings or {}).get(name)
    return binding is not None and binding.kind == "slot"


def _kind_of(value: object, declared: Mapping[str, Mapping[str, Any]]) -> str:
    """The value kind a variable's own definition implies.

    Three cases, because a `variables:` entry is written in three ways. A literal
    is typed from the literal. A template computes something only the person
    knows, so it stays `string` and they say otherwise. And `value: !input x` is
    typed from *that input's selector* -- the blueprint is naming a number input,
    so the variable is a number once a person has filled it, and reading it as a
    string would put a number output behind a string sensor.
    """
    if isinstance(value, Mapping) and set(value) == {INPUT_MARKER}:
        return _kind_from_selector(declared.get(str(value[INPUT_MARKER])))
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _kind_from_selector(block: object) -> str:
    """The value kind a selector implies, or `string` for one this does not know."""
    selector = _selector(block)
    if "number" in selector:
        return "number"
    if "boolean" in selector:
        return "boolean"
    if "select" in selector:
        return "enum"
    return "string"


def _is_entity_input(block: object) -> bool:
    """Whether a blueprint input names an entity or a target rather than a value."""
    if not isinstance(block, Mapping):
        return False
    selector = block.get("selector")
    if not isinstance(selector, Mapping):
        return False
    return "entity" in selector or "target" in selector


def _variables(
    node: object, *, branch: bool = False
) -> Iterable[tuple[str, object, bool]]:
    """Every `variables:` entry in the document, with whether it sits in a branch.

    A `variables:` block at the top level of the body is in scope for the whole
    automation; one inside a `choose`/`if`/`repeat` is in scope only there. The
    distinction is what `publish_actions` uses to place the make-public step, and
    it is carried out of the walk rather than guessed later.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "variables" and isinstance(value, Mapping):
                for name, entry in value.items():
                    yield str(name), entry, branch
            else:
                yield from _variables(value, branch=branch or key in _BRANCH_KEYS)
    elif isinstance(node, list):
        for item in node:
            yield from _variables(item, branch=branch)


def _response_variables(node: object) -> Iterable[str]:
    """The names calls hand back with `response_variable:`."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "response_variable" and isinstance(value, str):
                yield value
            else:
                yield from _response_variables(value)
    elif isinstance(node, list):
        for item in node:
            yield from _response_variables(item)


#: The keys whose contents are a branch: a variable set inside one is not in
#: scope once the branch ends, which is what makes an output's publisher land
#: inside the branch rather than at the end of the automation.
#:
#: `default` is a branch like any other -- it is the arm a `choose` element runs
#: when no option matched -- and it is a *sibling* of `choose` rather than a child
#: of it, so nothing above it marks the contents as conditional. Leaving it out is
#: why a variable set in a choose element's fallback was offered as a value
#: reported on every run, and published only on the runs that fell through.
_BRANCH_KEYS = frozenset({"choose", "if", "repeat", "parallel", "sequence", "default"})

#: The keys of a service call's data that name *what* to act on rather than a
#: value to set. They are not offered as settings-that-were-set: `entity_id` is
#: the call's device, which is its own candidate, and an output of it would say
#: the same thing twice.
_TARGET_KEYS = frozenset({"entity_id", "device_id", "area_id"})


def input_in_trigger(source: HostedSource, name: str) -> bool:
    """Whether the trigger itself names this input.

    The one place a cast cannot go. A trigger's `entity_id` is **not one of the
    fields Home Assistant renders**: a state trigger is matched against the real
    entities a house has, so text a person wrote there is compared as text and
    never matches the entity it names -- and nothing says so, because the
    automation installs and simply never fires. An action's `entity_id` is the
    other way round: it is rendered on every run, so a template there works, and
    that is where a cast belongs.

    Asked here rather than guessed at on the screen, because it is a fact about
    the document: an input the trigger names gets no cast offered, and one that
    merely reaches an action is offered one wherever the person wants it.

    It is the *matched* fields that count, which is what makes this narrower than
    "any `!input` under `trigger:`". A state trigger's `to:` and `for:` are
    rendered like anything else, so a cast written into one works; only the
    fields that name the devices to watch -- `entity_id`, `device_id`,
    `area_id`, the same three a target call uses -- are matched rather than
    rendered. Reading the document any wider would take a cast away from inputs
    that can take one.

    Both spellings of the clause are read, and for a reason that is not
    tidiness: Home Assistant renames `trigger:` to `triggers:` when it validates
    a blueprint, so the document this is asked about is the *renamed* one
    whenever the source came from the instance's own blueprint list
    (`dev_authoring.source_text` re-emits what Home Assistant holds). Reading
    only the singular answered "no input is watched" for every blueprint chosen
    from the picker, while the pasted text of the same file answered correctly --
    which is the sort of difference that looks like a working feature until a
    condition is written over a trigger's device and the automation never fires.
    """
    document = source.document
    for clause in _TRIGGER_KEYS:
        for _path, node, _branch in _walk_actions(document.get(clause)):
            if not isinstance(node, Mapping):
                continue
            if any(_names_input(node.get(key), name) for key in _TARGET_KEYS):
                return True
    return False


def _names_input(value: object, name: str) -> bool:
    """Whether this field's value is the `!input` marker for that input.

    One or several, because `entity_id:` takes a list of what to watch as
    readily as one thing: a trigger watching this input *and* a fixed entity is
    still a trigger that names this input.
    """
    items = value if isinstance(value, (list, tuple)) else (value,)
    for item in items:
        if (
            isinstance(item, Mapping)
            and set(item) == {INPUT_MARKER}
            and str(item[INPUT_MARKER]) == name
        ):
            return True
    return False


def _walk_actions(
    node: object, path: tuple[Any, ...] = (), branch: bool = False
) -> Iterable[tuple[tuple[Any, ...], object, bool]]:
    """Every node of the action tree, with where it is and whether it is in a branch.

    The path is the one `publish_actions` walks back to place a step: mapping
    keys by name, list entries by index. It survives `instantiate`, which rebuilds
    the tree preserving every list index, so a path found on the blueprint is valid
    on the automation built from it -- which is the whole reason the position can
    be computed here and used there.
    """
    yield path, node, branch
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _walk_actions(value, (*path, key), branch or key in _BRANCH_KEYS)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk_actions(item, (*path, index), branch)


def _service_calls(
    document: Mapping[str, Any],
) -> Iterable[tuple[tuple[Any, ...], Mapping[str, Any], bool, str]]:
    """Every service call in the document, with its path, its branch and its service.

    `service:` and `action:` are both Home Assistant's spelling for the same step
    and a blueprint may use either, so both are read -- the second only when it is
    a string, since `action:` at the top of a document is the action *list*.
    """
    for path, node, branch in _walk_actions(document):
        if not isinstance(node, Mapping):
            continue
        for key in ("service", "action"):
            service = node.get(key)
            if isinstance(service, str) and service.strip():
                yield path, node, branch, service.strip()
                break


def _names_a_device(call: Mapping[str, Any]) -> bool:
    """Whether a call is a call *to* something, as the blueprint wrote it.

    Asked of the document rather than of the answers, deliberately: a person has
    answered nothing on the screen this list appears on, so a call whose device is
    the `!input` they have not picked yet is still a call that drives a device --
    and offering its values only after they pick one would hide the brightness of
    a light from the person who came to publish it.

    What this leaves out is the calls that drive nothing: a `logbook.log`, a
    `persistent_notification`, a `notify`. Offering their `message` and `name` as
    things the module "sets" would fill the screen with prose that no dashboard
    was ever going to read.
    """
    if "target" in call:
        return True
    for key in ("data", "data_template"):
        block = call.get(key)
        if isinstance(block, Mapping) and _TARGET_KEYS & set(block):
            return True
    return False


def _acted_on(
    call: Mapping[str, Any], source: HostedSource, chosen: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """The entity ids one call acts on, or an empty tuple when none can be named.

    A `target: !input lights` is the usual shape, and the ids are the person's
    binding for that input -- the same resolution `instantiate` will do, so the
    output names the devices the call really drives. An unbound input answers
    nothing here, and that is only about the *devices* candidate: the values the
    call sets are still offered, because they do not depend on which light it is.

    A call that acts on an area (`area_id`) names devices but not *which*, so it
    answers nothing: an output reading "the kitchen" would be an entity id where
    every consumer expects a device.
    """
    for key in ("target", "data", "data_template"):
        block = _input_value(call.get(key), source, chosen)
        if not isinstance(block, Mapping):
            continue
        ids = _entity_ids(_input_value(block.get("entity_id"), source, chosen))
        if ids:
            return ids
    return ()


def _set_values(
    call: Mapping[str, Any], source: HostedSource, chosen: Mapping[str, Any] | None
) -> Iterable[tuple[str, object]]:
    """Every value one call sets, by the key the service was given it under."""
    for key in ("data", "data_template"):
        block = _input_value(call.get(key), source, chosen)
        if not isinstance(block, Mapping):
            continue
        for name, entry in block.items():
            if name in _TARGET_KEYS:
                continue
            yield str(name), _input_value(entry, source, chosen)


def _input_value(
    value: object, source: HostedSource, chosen: Mapping[str, Any] | None
) -> object:
    """One `!input` marker replaced by what it will really hold, or left as it is.

    The person's binding first, then the blueprint's own `default`, and `None` for
    an input neither answered -- which is exactly the order `instantiate` resolves
    them in, so a candidate's expression is over the value the automation will
    really carry rather than over the marker.
    """
    if not (isinstance(value, Mapping) and set(value) == {INPUT_MARKER}):
        return value
    name = str(value[INPUT_MARKER])
    if chosen and chosen.get(name) is not None:
        return chosen[name]
    block = source.inputs.get(name)
    if isinstance(block, Mapping) and "default" in block:
        return block["default"]
    return None


def _entity_ids(value: object) -> tuple[str, ...]:
    """The entity ids in a target-ish value, and only the ones that are ids.

    Three shapes, because Home Assistant writes a device three ways: a bare id, a
    list of them, and the `target:` mapping that wraps them under `entity_id`. A
    template is not an id and is dropped -- it names a device only once it has
    rendered, which is at run time and not here.
    """
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, Mapping):
        return _entity_ids(value.get("entity_id"))
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _value_expression(value: object) -> tuple[str, bool, str] | None:
    """A value a call set, as an expression and how to publish it.

    `None` for a value this cannot read: a mapping or a list (an `rgb_color`, say)
    has no single state a sensor could hold, and publishing its Python spelling
    would be inventing a format rather than reporting a value.

    **A number keeps its number and a string keeps its braces.** `60` is published
    as `{{ 60 }}` -- a number, which is what the sensor's `number` kind means --
    while `'{{ brightness | int }}'` is handed over as the template it already is,
    because wrapping template text produces `{{ {{ ... }} }}`, which renders to
    nothing and logs an error on every run. Text with no braces in it renders to
    itself either way, so a plain string is published unwrapped too.
    """
    if isinstance(value, bool):
        return ("true" if value else "false", False, "boolean")
    if isinstance(value, (int, float)):
        return (str(value), False, "number")
    if isinstance(value, str) and value:
        return (value, True, "string")
    return None


def _unique(seen: set[str], name: str) -> str:
    """`name`, or the first numbered spelling of it nothing has taken.

    A blueprint may turn the same light on in two branches, and the second call is
    a second thing a person may want to read -- "which branch ran" is exactly what
    the value of a branch-only output answers. Numbering rather than dropping the
    second keeps every call offerable, and the number is only ever visible on the
    names that collided.
    """
    if name not in seen:
        seen.add(name)
        return name
    number = 2
    while f"{name} #{number}" in seen:
        number += 1
    numbered = f"{name} #{number}"
    seen.add(numbered)
    return numbered


# --------------------------------------------------------------------------
# The make-public step
# --------------------------------------------------------------------------


def publish_actions(
    document: Mapping[str, Any],
    outputs: Sequence[Output],
    *,
    module: str,
) -> dict[str, Any]:
    """Append the step that copies each output into its entity, and return the doc.

    The step is a call to Open House's own `publish_output` service, because a
    running automation cannot write a `sensor`'s state directly in Home
    Assistant. It is appended -- never inserted into a branch, never replacing
    anything -- so the blueprint's behaviour is exactly what it was.

    **Where it is appended is the whole of the correctness.** A top-level
    `variables:` and a reading are in scope at the end of the automation, so their
    publisher goes there. A variable set inside a `choose` branch is in scope only
    in that branch, so its publisher is appended to the branch that defines it --
    and if two branches define it, both get one, because either may be the one
    that ran. And a value a call *set* is in scope nowhere but at that call, so its
    publisher is inserted immediately after it.
    """
    # A deep copy, because the appends below reach into nested lists: returning a
    # shallow copy would leave the caller's document mutated by a call that read
    # like a pure function, and a second publish of the same document would emit
    # every step twice.
    result = _copy(document)
    action_key = _action_key(result)
    if action_key is None:
        # A source with no actions has nothing to publish from; a module with
        # outputs but no actions is caught by `_check_outputs_are_reachable`
        # before this is reached, so an empty list here is honest.
        return result
    sequence = result[action_key]
    if not isinstance(sequence, list):
        sequence = [sequence]
        result[action_key] = sequence

    for output in outputs:
        if output.after:
            # Placed in the second pass below, because an insertion inside a list
            # moves every index after it -- so they are all done at the end, from
            # the last action backwards.
            continue
        if not output.variables:
            sequence.append(_publish(output, module))
            continue
        placed = False
        for targets in _scope_lists(result, output.variables):
            targets.append(_publish(output, module))
            placed = True
        if not placed:
            # The variable is not defined anywhere `_variables` found -- it is
            # likely a blueprint-level `variables:` at the document's top level,
            # which Home Assistant scopes to the whole automation, so the end of
            # the actions is the right place.
            sequence.append(_publish(output, module))

    # The outputs read from an action, each beside the action it reads. Grouped
    # by path first, so two values one call set are one insertion in the order
    # the screen listed them rather than two insertions that swap places.
    beside: dict[tuple[Any, ...], list[Output]] = {}
    for output in outputs:
        if output.after:
            beside.setdefault(output.after, []).append(output)
    if beside:
        found: list[tuple[list[Any], int, list[dict[str, Any]]]] = []
        nowhere: list[dict[str, Any]] = []
        for path, here in beside.items():
            steps = [_publish(output, module) for output in here]
            # Every action is found *before* anything is inserted, because an
            # insertion shifts the index of everything after it in that list and
            # a path found later would land beside the wrong action.
            parent = _at_path(result, path[:-1])
            index = path[-1] if path else None
            if (
                isinstance(parent, list)
                and isinstance(index, int)
                and 0 <= index < len(parent)
            ):
                found.append((parent, index, steps))
            else:
                # The action is not where the walk found it -- which cannot
                # happen for a document this module instantiated, since
                # `_substitute` keeps every list index -- but a publisher at the
                # end of the automation is a reading nobody gets, where raising
                # is a module that will not build at all.
                nowhere.extend(steps)
        # Backwards within each list: inserting after the last action first means
        # the indices of the actions still to be reached for are untouched.
        for parent, index, steps in sorted(found, key=lambda row: row[1], reverse=True):
            parent[index + 1 : index + 1] = steps
        sequence.extend(nowhere)
    return result


def _at_path(node: object, path: Sequence[Any]) -> object:
    """The node one walk path names, or `None` when it is not there."""
    for step in path:
        if isinstance(node, Mapping):
            node = node.get(step)
        elif isinstance(node, list) and isinstance(step, int) and 0 <= step < len(node):
            node = node[step]
        else:
            return None
    return node


def _publish(output: Output, module: str) -> dict[str, Any]:
    """One `publish_output` call, as Home Assistant's action shape.

    An expression is wrapped in `{{ }}` to become a template unless it already is
    one: a value a call set arrives as the text the call wrote, and `'{{ ... }}'`
    wrapped again is `{{ {{ ... }} }}`, which Home Assistant refuses to render --
    publishing nothing, with a template error in the log on every run.
    """
    value = output.expression if output.template else "{{ " + output.expression + " }}"
    return {
        "service": PUBLISH_SERVICE,
        "data": {
            "module": module,
            "key": output.key,
            "value": value,
        },
    }


def action_key(document: Mapping[str, Any]) -> str | None:
    """The key this document keeps its actions under, or `None` when it has none.

    Public because "is this an automation at all" is a question the caller has to
    be able to ask before handing a document to Home Assistant: a script
    blueprint's document is a bare `sequence:`, which is a perfectly good script
    and not an automation, and the validator's own refusal of it says nothing
    about the thing the person actually chose.
    """
    for key in _ACTION_KEYS:
        if key in document:
            return key
    return None


def _action_key(document: Mapping[str, Any]) -> str | None:
    return action_key(document)


def _scope_lists(
    document: Mapping[str, Any], names: Sequence[str]
) -> tuple[list[Any], ...]:
    """The action lists in which every one of `names` is in scope at their end.

    A variable is in scope for the rest of the list it was defined in, so the
    answer for a set of names is the *innermost* list that defines all of them --
    which is the deepest list whose `variables:` mention every name. Walking
    outward from the document's own action list would append too late (after the
    branch closed), and walking inward would append too early; this returns the
    lists a publisher may be safely appended to, one per defining list.
    """
    found: list[list[Any]] = []
    remaining = set(names)
    for sequence in _all_sequences(document):
        defined = _defined_in(sequence)
        if remaining and remaining <= defined:
            found.append(sequence)
    return tuple(found)


def _all_sequences(node: object) -> Iterable[list[Any]]:
    """Every list that is an action sequence, at any depth.

    Both spellings of the automation's own actions and the `sequence:` a branch
    body carries, because a variable defined inside a branch is in scope for that
    branch's list and nowhere else -- so that list is one a publisher may be
    appended to, and the top-level list is not. A `parallel` element's branches
    are lists of the same kind, reached one level differently because they are
    the values of a mapping rather than a list.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in _SEQUENCE_KEYS and isinstance(value, list):
                yield value
            elif key == _PARALLEL_KEY and isinstance(value, Mapping):
                yield from (
                    branch for branch in value.values() if isinstance(branch, list)
                )
            yield from _all_sequences(value)
    elif isinstance(node, list):
        for item in node:
            yield from _all_sequences(item)


def _defined_in(sequence: Sequence[object]) -> set[str]:
    """Every name a list's own elements bring into scope.

    Two ways an element does that, and they are the same question: `variables:`
    introduces a name, and `response_variable:` binds the name a service call
    handed back. Both are in scope for the rest of *this* list, which is why both
    count here.

    Deliberately does *not* descend into a nested `sequence:`. A variable set
    inside one is in scope for that nested list and not for the list around it,
    so counting it here would place a publisher after the scope had closed --
    which is a template referring to a variable that is no longer defined, the
    one way this append can produce a broken automation rather than simply a late
    one.
    """
    found: set[str] = set()
    for item in sequence:
        if not isinstance(item, Mapping):
            continue
        block = item.get("variables")
        if isinstance(block, Mapping):
            found.update(str(name) for name in block)
        returned = item.get("response_variable")
        if isinstance(returned, str):
            found.add(returned)
    return found


# --------------------------------------------------------------------------
# Reading a module's own output
# --------------------------------------------------------------------------


def bind_inputs(
    source: HostedSource, bindings: Mapping[str, InputBinding]
) -> dict[str, Any]:
    """The values a blueprint's inputs take, one binding each.

    The three ways an input is filled, and the reason they are three: a person
    may type a value (a `literal`), point at a device (an `entity`), or point at
    another module's published output (an `output`). The first two are what the
    Home Assistant blueprint editor already does; the third is the dataflow, and
    it is why this module exists.

    **An output binding is resolved to the shape the input can hold.** An input
    that takes an entity is given the output's entity id -- so the automation
    reads it natively. An input that takes a *value* cannot hold an entity, so it
    is given a template that reads the entity's state, which is how one
    automation consumes another's reading in Home Assistant. Where the selector
    is a `target` the binding is wrapped the way `target` expects.

    An input with no binding is left out, and `instantiate` fills it from its own
    `default` -- the same rule the editor follows, and the reason a person does
    not have to answer every input to save.
    """
    values: dict[str, Any] = {}
    for name, block in source.inputs.items():
        binding = bindings.get(name)
        if binding is None:
            continue
        values[name] = _bound_value(block, binding)
    return values


def resolve_slots(
    bindings: Mapping[str, InputBinding],
    bound: Mapping[str, str],
    house_bound: Mapping[str, str] | None = None,
) -> dict[str, InputBinding]:
    """Every `slot` binding turned into the `entity` binding it stands for.

    This is the whole of what a slot *is*: a device the room names, resolved
    when the module is built rather than when it was imported. So a house that
    moves its `lux_sensor` to another sensor does not re-import the modules that
    read it -- it binds the slot again, and every module reaching through that
    name is built again against the new device.

    `bound` is the module's room's view -- the house's own binding with the
    room's over the top (`host.bound_slots`) -- and it answers a `ROOM_SCOPE`
    binding. `house_bound` is the house's own binding alone, and it answers a
    `HOUSE_SCOPE` one: a **global slot** is the house's device in every room, so
    a room that bound the same name itself must not shadow it. `house_bound`
    defaults to `bound`, which is right for a caller that holds only one map (a
    house-scoped module, whose room *is* the house) and keeps every older call
    site reading as it did.

    A slot nothing has bound is **left out of the answer** rather than defaulted.
    Nothing may reach `_bound_value` still slot-shaped -- a slot *name* written
    into the automation where a device id belongs is a document that reads as a
    device nobody has -- so an unresolved slot contributes no value at all. It is
    not lost: `unresolved_slots` reads the *original* bindings, and the caller
    uses it to say which device is missing, by the name the person gave it, and
    to hold the module back until the room has one.
    """
    house = _house_view(bound, house_bound)
    resolved: dict[str, InputBinding] = {}
    for name, binding in bindings.items():
        if binding.kind != "slot":
            resolved[name] = binding
            continue
        source = house if binding.scope == HOUSE_SCOPE else bound
        entity_id = source.get(slot_key(binding))
        if entity_id:
            resolved[name] = InputBinding(kind="entity", value=entity_id)
    return resolved


def unresolved_slots(
    bindings: Mapping[str, InputBinding],
    bound: Mapping[str, str],
    house_bound: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """The slot names these bindings reach through that nothing has bound.

    Read through the same two mappings `resolve_slots` uses, so a `HOUSE_SCOPE`
    slot a room bound but the house did not is reported waiting -- it is the
    house's device that is missing, and naming the room's would be naming one the
    module never reads.
    """
    house = _house_view(bound, house_bound)
    return tuple(
        sorted(
            {
                slot_key(binding)
                for binding in bindings.values()
                if binding.kind == "slot"
                and not (house if binding.scope == HOUSE_SCOPE else bound).get(
                    slot_key(binding)
                )
            }
        )
    )


def _house_view(
    bound: Mapping[str, str], house_bound: Mapping[str, str] | None
) -> Mapping[str, str]:
    """The house's own binding, taken from whichever argument carries it.

    An explicit `house_bound` is a caller being unambiguous; a `BoundSlots`
    carries its own; and a plain mapping is both views at once, which is what a
    caller holding only one map means -- the same reading `resolve_slots` had
    before a scope existed, so an older call site keeps working.
    """
    if house_bound is not None:
        return house_bound
    house = getattr(bound, "house", None)
    return bound if house is None else house


def slots_reached(bindings: Mapping[str, InputBinding]) -> tuple[str, ...]:
    """Every slot *key* these bindings reach through, bound or not.

    The set a room's binding has to be measured against when it changes: what
    matters is not whether *this* slot resolves but whether the one the person
    just rebound is one this module reads -- and a module answering with a part
    reads the *part's* key, so a rebinding of `light_group__a` has to name this
    module and a rebinding of `light_group` has to not, which is what
    `slot_key` answers and why the question is asked of the key.
    """
    return tuple(
        sorted(
            {
                slot_key(binding)
                for binding in bindings.values()
                if binding.kind == "slot"
            }
        )
    )


def slot_key(binding: InputBinding) -> str:
    """The key a `slot` binding resolves under: the slot's, or one of its parts'.

    One function rather than the four-line expression at each of the four readers
    (`resolve_slots`, `unresolved_slots`, `slots_reached`, and the panel's row
    through `live_modules`), because the whole of "a part is a slot of its own
    under the parent's name" is this string: a reader that computed it differently
    would resolve a module against a slot the room never bound.

    Only ever called on a `slot` binding. A `part` on a binding of another kind is
    not read here, and cannot be: an `entity` binding names its device outright,
    and there is no key to narrow.
    """
    return (
        slot_parts.key_of(binding.slot, binding.part) if binding.part else binding.slot
    )


def unset_settings(
    source: HostedSource, bindings: Mapping[str, InputBinding], settings: Iterable[str]
) -> tuple[str, ...]:
    """The inputs a module keeps as options that nothing has answered yet.

    An option is a setting one state earlier. A setting is a value somebody gave
    and may give again; this is a name the module keeps with no value at all,
    which happens when a person answers an input with "Blueprint default"
    rather than with a value, a device or a slot, and there is no default to
    start it from -- which `declares_default` reads to be the case for every
    input that takes a device, however the author's blueprint filled it in.

    Nothing may be blanked into an automation -- `_substitute` refuses an input
    with no value and no default, and rightly: an automation that ran with a
    hole in it would act on nothing and report nothing. So a module with one of
    these is held back exactly as one waiting for a slot is: it is recorded, its
    outputs exist and read unknown, and its automation arrives when somebody
    sets the option. The difference is only what is missing -- a device a room
    has not bound, or a value this module's own settings form has not been given.

    Read in the order the options were kept, which is the order the blueprint
    declares them, so the sentence a screen writes names them as a person read
    them rather than sorted into an order nobody chose.
    """
    return tuple(
        name
        for name in settings
        if name in source.inputs
        and name not in bindings
        and not declares_default(source.inputs[name])
    )


def declares_default(block: object) -> bool:
    """Whether a blueprint input starts with a value of its own.

    Only the *presence* of the key counts -- `default: null` is a default, and
    an input with one may be left empty. So says every selector but one.

    **An input that takes a device and names one has no default.** `default:
    device_tracker.me` is what a blueprint author writes to say where the field
    goes, and it names a device on *their* installation -- filling a hosted
    module with it would point the automation at a stranger's phone, and, worse
    because it is silent, would make the input look answered, so the module would
    be built and run against a device nobody here chose. A device input is
    therefore undefined until somebody answers it, and a module whose answer
    never comes waits (`unset_settings`) rather than being built from the
    placeholder.

    An *empty* default is a different thing and stays one: `default:` with
    nothing after it selects no device, which is a state the blueprint is written
    to run in -- that is what the key being optional means -- so there is no
    device to be wrong about and nothing to hold the module back for. The line is
    between a default that names something and one that names nothing, which is
    the line the automation itself would draw.
    """
    if not isinstance(block, Mapping) or "default" not in block:
        return False
    selector = _selector(block)
    if "entity" not in selector and "target" not in selector:
        return True
    return not block["default"]


def _bound_value(block: object, binding: InputBinding) -> Any:
    """One input's value, in the shape its own selector can hold."""
    selector = _selector(block)
    is_target = "target" in selector
    if binding.kind == "literal":
        return binding.value
    if binding.kind == "entity":
        return {"entity_id": binding.value} if is_target else binding.value
    if binding.kind == "slot":
        # `resolve_slots` turns a slot into the `entity` binding it stands for
        # before anything reaches here. A caller that skipped it would have a
        # slot *name* written into the automation where a device id belongs --
        # a document that reads as a device nobody has.
        raise AuthoringError(
            f"{binding.slot!r} is a slot and not a device yet: resolve it "
            "against the room's bindings before building the module"
        )
    if binding.kind == "output":
        entity_id = output_entity_id(binding.module, binding.key)
        if is_target:
            return {"entity_id": entity_id}
        if "entity" in selector:
            return entity_id
        # A value input cannot hold an entity, so it holds a template that reads
        # the entity's state -- the one way Home Assistant lets one automation
        # consume another's output.
        return _template_for(selector, entity_id)
    raise AuthoringError(f"{binding.kind!r} is not a way to fill an input")


def _template_for(selector: Mapping[str, Any], entity_id: str) -> str:
    """A template reading an output, typed the way the input it fills expects.

    A bare `states()` returns the state as *text*, and text is not the same value
    as the thing it spells: a `number` input holding `"off"` or `""` fails every
    arithmetic the blueprint does with it, and a `boolean` input holding the
    string `"off"` is truthy -- which is the worse failure, because the blueprint
    takes the branch it would have taken for `on` and nothing looks broken. So
    the template does the conversion where the selector says one is needed, and
    the producer's output arrives as the kind of value the input is.
    """
    if "number" in selector:
        return "{{ states('" + entity_id + "') | float(0) }}"
    if "boolean" in selector:
        return "{{ is_state('" + entity_id + "', 'on') }}"
    return "{{ states('" + entity_id + "') }}"


def _selector(block: object) -> Mapping[str, Any]:
    if not isinstance(block, Mapping):
        return {}
    selector = block.get("selector")
    return selector if isinstance(selector, Mapping) else {}


#: The namespace module automation ids are derived in. A fixed, arbitrary UUID
#: -- a namespace's only job is to be the same one every time. It is this
#: integration's own, so two integrations deriving an id from the same name
#: cannot land on the same id.
_NAMESPACE = uuid.UUID("6f5a1f83-6f2c-4c2e-9c72-8a1d0c5b7e41")


def config_id_for(module: str) -> str:
    """The automation configuration id a module is known by, from its name.

    Derived rather than random, which is the whole of "import it again" being a
    replacement instead of a second copy: the id is what the automation is
    matched by -- in `automations.yaml` and in the entity registry -- so the same
    module name always means the same automation, and hosting it a second time
    rewrites that one rather than leaving the first running beside it.

    A name-derived id is also what lets a module be looked up *before* its
    automation has ever loaded, and what makes the id survive a restart without
    anything having to keep a second copy of the mapping. It sits beside
    `output_entity_id` because it is the same kind of fact: the name one of a
    module's pieces of itself is addressed by, spelled once so nothing can
    disagree about it.
    """
    return uuid.uuid5(_NAMESPACE, module).hex


def output_entity_id(module: str, key: str) -> str:
    """The entity an output is published to, and that a consumer binds to.

    One spelling, here, so the publisher, the consumer's template and the panel
    cannot disagree about where an output lives. `sensor` because an output is a
    reading, `open_house` so it groups with the integration's other entities in
    Home Assistant's own lists.
    """
    if not _KEY.match(module):
        raise AuthoringError(f"{module!r} is not a module name")
    if not _KEY.match(key):
        raise AuthoringError(f"{key!r} is not an output key")
    return f"sensor.open_house_{module}_{key}"


def derived_entity_id(module: str, name: str) -> str:
    """The entity Open House makes for an input answered with a *condition*.

    A condition is logic, and an automation's input can be logic -- a template
    is rendered where it lands. A **trigger's `entity_id` cannot**: Home
    Assistant matches it against the entities a house actually has rather than
    rendering it, so a condition written there is compared as text, matches
    nothing, and leaves an automation that installs and never fires, silently.
    Which is why the condition a person writes is not written into the trigger
    at all: Open House evaluates it and publishes the answer as a real entity,
    and *that* entity is what the input -- and the trigger -- is pointed at.

    `binary_sensor` because the answer is yes or no, and the id is spelled here
    for the same reason `output_entity_id` is: the maker, the binding, and the
    panel cannot then disagree about where it lives. The name is the module's
    and the input's, so it reads as the question it answers.
    """
    if not _KEY.match(module):
        raise AuthoringError(f"{module!r} is not a module name")
    if not _KEY.match(name):
        raise AuthoringError(f"{name!r} is not an input name")
    return f"binary_sensor.open_house_{module}_{name}"


def flow_entity_id(module: str, name: str) -> str:
    """The entity a *flow* writes, and that the input answered by it reads.

    The third way an input may be answered -- by a flow of nodes in Node-RED --
    needs a place for the flow to put its answer. Node-RED can only reach Home
    Assistant by calling a service, and every service that writes a value writes
    it into an *entity*, so the answer has to be an entity of the house's own.

    It is Open House's entity rather than a `input_number` helper of the
    instance's, because helpers are made by hand in Home Assistant's UI and this
    has to be made by the import: a cast that asked a person to go and create a
    helper first would be a cast that cannot be used from the panel at all.

    `flow_` and not `sensor.open_house_{module}_{name}`, which is what
    `output_entity_id` spells for a module's *output*: an input and an output of
    one module may share a name, and two entities cannot share an id. A
    `sensor` because the reading is text and a flow may write any text into it;
    the template that reads it casts it to whatever the input expects, which is
    what `output_entity_id`'s consumer does for the same reason.
    """
    if not _KEY.match(module):
        raise AuthoringError(f"{module!r} is not a module name")
    if not _KEY.match(name):
        raise AuthoringError(f"{name!r} is not an input name")
    return f"sensor.open_house_flow_{module}_{name}"


def binding_to_entity(block: object, entity_id: str) -> InputBinding:
    """An input's answer, when the answer is *an entity Open House made*.

    The same decision `_bound_value` makes for every other binding, restated for
    the one case that arrives after the blueprint was read: an input that takes a
    device is given the entity's id, and an input that takes a value is given a
    template reading it -- typed the way `_template_for` types it, so a number
    input holding `"on"` does not fail every arithmetic the blueprint does.

    Here rather than in the caller because it is the *same* rule; a second
    spelling of it in the integration is a second place it can be got wrong.
    """
    selector = _selector(block)
    if "entity" in selector or "target" in selector:
        return InputBinding(kind="entity", value=entity_id)
    return InputBinding(kind="literal", value=_template_for(selector, entity_id))


def _variable_name(taken: set[str], name: str) -> str:
    """`name`, or the first numbered spelling of it nothing has taken.

    Numbered with an underscore rather than the `" #2"` `_unique` uses for output
    *labels*, because this name is written into a running automation: Home
    Assistant's template variables are identifiers, and a variable called
    `oh_brightness #2` is a template referring to a name Home Assistant will not
    bind, which renders to nothing at run time instead of failing where it can be
    seen. The prefix is checked, not assumed, so an input the blueprint itself
    named the same way does not have its variable clobbered.
    """
    if name not in taken:
        taken.add(name)
        return name
    number = 2
    while f"{name}_{number}" in taken:
        number += 1
    numbered = f"{name}_{number}"
    taken.add(numbered)
    return numbered


def script_variable(name: str) -> str:
    """The variable a script's returned value is bound to, from the input's name.

    Prefixed, and the prefix matters: this name is introduced into the person's
    own automation beside every variable the blueprint declares, so it has to be
    recognisable as Open House's -- and unlikely to collide -- rather than
    something a reader might think the blueprint wrote. Anything in the input name
    that a template variable cannot hold becomes an underscore, because blueprint
    input names are free to carry characters variables are not.
    """
    return "oh_" + re.sub(r"\W", "_", name)


def declared_variable_names(source: HostedSource) -> frozenset[str]:
    """Every name the document's own variables already take, at any depth.

    Both spellings a value comes back under: a `variables:` entry the blueprint
    set, and the `response_variable` a call handed back. Read so a script's
    returned value is bound to a name *nothing else in the document uses* -- a
    variable silently overwriting one the blueprint reads later is a bug that
    shows up as the wrong answer from the wrong place, which is the worst kind to
    trace back.
    """
    document = source.document
    names = {name for name, _entry, _branch in _variables(document)}
    names.update(_response_variables(document))
    return frozenset(names)


def script_binding(block: object, variable: str) -> InputBinding:
    """An input's answer, when the answer is *what a script handed back*.

    The same decision `binding_to_entity` makes, and for the same reason: an
    input that takes a device is given the value itself, and an input that takes a
    value is given a template reading it. The difference is only where the value
    comes from -- a run-scoped variable rather than an entity Open House made --
    so an input answered this way is *entity* where a device belongs and
    *literal* (template text) otherwise.
    """
    selector = _selector(block)
    if "entity" in selector or "target" in selector:
        return InputBinding(kind="entity", value="{{ " + variable + " }}")
    return InputBinding(kind="literal", value="{{ " + variable + " }}")


def answer_with_scripts(
    source: HostedSource, scripts: Mapping[str, str]
) -> tuple[dict[str, InputBinding], tuple[tuple[str, str], ...]]:
    """The inputs a person answered with a script, as bindings and as calls.

    Two things come back together because they are one decision: the *variable*
    a script's answer is bound to is chosen here, and the same name is what the
    binding reads and what the call writes it into. Deciding the two separately
    is how a binding could come to read a variable no call ever set -- an input
    filled with the text `{{ oh_x }}`, rendering to an empty string, and an
    automation that runs against nothing with nothing in the log to say why.

    Refused for an input the **trigger** names, which is the one place a script
    cast cannot go: a trigger's `entity_id` is matched against the devices a house
    has rather than rendered (`input_in_trigger`), so a variable written there is
    compared as text and the automation installs and never fires.
    """
    taken = set(declared_variable_names(source))
    bindings: dict[str, InputBinding] = {}
    calls: list[tuple[str, str]] = []
    for name, script in scripts.items():
        if not script:
            # A name with no script behind it: an installation that followed a
            # definition naming this input before the installing house picked a
            # script for it. Not answered, rather than answered with a call to
            # nothing -- the input goes back to being one nothing fills, which is
            # the state the build already knows how to hold a module in.
            continue
        block = source.inputs.get(name)
        if not isinstance(block, Mapping):
            raise AuthoringError(f"{name!r} is not an input of this blueprint")
        if input_in_trigger(source, name):
            raise AuthoringError(
                f"{name!r} is named by the automation's trigger, so it cannot be "
                "answered by a script: a trigger's entity_id is matched against "
                "real devices rather than rendered, and a value written there "
                "would leave the automation never firing"
            )
        variable = _variable_name(taken, script_variable(name))
        bindings[name] = script_binding(block, variable)
        # The person's script, however the panel handed it over: an entity id
        # with its domain (`script.turn_the_lights_on`), a bare object id, or the
        # leading `script.` left off. Normalised here rather than at the call
        # site, so a `script.` written twice cannot become `script.script.x` --
        # a step naming a service that does not exist, in an automation that
        # otherwise installs perfectly.
        calls.append((re.sub(r"^script\.", "", str(script)), variable))
    return bindings, tuple(calls)


def script_calls(
    document: Mapping[str, Any],
    calls: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Prepend the script calls that answer this module's script casts.

    **At the top of the action list, and that position is the whole of the
    correctness.** A script only runs when it is called, and the value it hands
    back with `response_variable:` is in scope for the rest of the list the call
    sits in -- so the call goes first, and every use of the input below it can
    read the variable. Anywhere else and an input used above the call would
    render the variable as nothing.

    Run **after** `publish_actions`, never before: that function inserts each
    output's publisher beside the action that produced it, found by a path into
    the document's lists -- and a step inserted at the front of a list moves every
    index after it, so a path computed before this ran would land beside the wrong
    action. By the time this is reached the publishers are in place and nothing
    reads the document by index again.

    A `script` step rather than a `service` one, because it is a script being run:
    Home Assistant accepts `action: script.<id>` as a step and answers it with
    `response_variable:`, and the old `service:` spelling would say the same thing
    less plainly.
    """
    if not calls:
        return _copy(document)
    result = _copy(document)
    key = _action_key(result)
    if key is None:
        # No actions to run a script from. `answer_with_scripts` already refused
        # an input the trigger names, so a script cast on a document with no
        # actions at all is nothing to build; the callers refuse that earlier.
        return result
    sequence = result[key]
    if not isinstance(sequence, list):
        sequence = [sequence]
        result[key] = sequence
    steps = [
        {"action": f"script.{script}", "response_variable": variable}
        for script, variable in calls
    ]
    sequence[:0] = steps
    return result


def _copy(node: object) -> Any:
    """A deep copy of a YAML-shaped tree, so an append cannot reach the original."""
    if isinstance(node, Mapping):
        return {key: _copy(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_copy(item) for item in node]
    return node


def _prose(value: object) -> str:
    """A document's description as one line, the way `pack_authoring` reads it."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())
