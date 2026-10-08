"""Turning Home Assistant automations and blueprints into packs, and back.

**Why this exists.** A pack is a manifest a person writes; an automation is a
document Home Assistant runs. The two are the same idea in two languages -- a
trigger, a condition, the services to call on the entities a room supplies --
and the distance between them was a person hand-translating one into the other.
This module is that translation, done once, so the panel can offer it as a
screen rather than as advice.

**The direction that needs care is the import**, because Home Assistant's
language is strictly larger. An automation may branch (`choose`, `if`,
`repeat`), compute (`variables`, templates) and wait; a declared behaviour may
not -- `catalog/pack-policy.yaml` refuses exactly those terms, because the
engine's whole claim is that what a house does is decidable from a manifest
rather than from an interpreter. So an import is not a transcription: it is a
*reading* of the source that names what can be carried across and reports what
cannot. `analyse` produces that reading, and `draft_module` is the second half
-- it takes the reading plus a person's decisions and writes the manifest.

**Nothing here guesses.** Every row `analyse` reports is a thing the source
actually contains, found by walking it, and every slot `draft_module` writes is
a decision the caller made. The one place a suggestion appears is
`suggest_slot`, which is advice the panel pre-fills and a person overrides; it
is named `suggest` rather than `decide` so that a caller reading the manifest
cannot mistake it for a judgement this module made on their behalf.

**The export direction is the same walk backwards.** `automation_documents`
takes a pack's behaviours, a room's bindings and a module's option values and
emits the automations that would do, in Home Assistant, what the pack declares
in the engine. It is what a person asks for when they want to leave -- and the
honest answer is that some packs export exactly and some do not, because a
declared behaviour that acts through a house-scope role has no single
`entity_id` to name. Those become automations that act on every entity the role
reached, which is what the engine would have done.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

__all__ = [
    "Analysis",
    "AuthoringError",
    "BehaviourRow",
    "EntityRow",
    "ValueRow",
    "analyse",
    "automation_documents",
    "automation_text",
    "draft_module",
    "module_text",
    "read_source",
    "suggest_slot",
]


class AuthoringError(Exception):
    """A source a person asked to import and that cannot be read.

    Named rather than a bare `ValueError` for the reason `LiveSessionError` is:
    the caller is a websocket handler that turns it into an error code the panel
    renders, and a failure the panel cannot name is one it can only show as a
    traceback.
    """


# --------------------------------------------------------------------------
# Reading the source
# --------------------------------------------------------------------------


class _Loader(yaml.SafeLoader):
    """A `SafeLoader` that understands one Home Assistant tag.

    `!input name` is how a blueprint names a value that is filled in per install,
    and a loader that did not know the tag would refuse every blueprint in
    existence -- which is most of what this module is for. It becomes a mapping
    with a single `__input__` key rather than a Python object of its own, because
    everything downstream walks plain mappings and a sentinel class would be a
    second kind of node every walk would have to know about.
    """


def _input_node(loader: _Loader, node: yaml.Node) -> Mapping[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        return {INPUT_MARKER: loader.construct_scalar(node)}
    raise AuthoringError("`!input` names one input, and this tag names more")


_Loader.add_constructor("!input", _input_node)

#: The key an `!input name` tag becomes. Doubled underscores because a Home
#: Assistant document may legitimately carry any other single word as a key, and
#: a marker a source could collide with is a marker that would be read as data.
INPUT_MARKER = "__input__"

#: The clause keys an automation or a blueprint body may carry, and the only
#: ones this module walks. Anything else -- `id`, `alias`, `description`,
#: `mode`, `max_exceeded` -- is carried nowhere: a pack has no clause for them,
#: and inventing one would be a promise the engine does not keep.
_TRIGGER_KEYS = ("trigger", "triggers")
_CONDITION_KEYS = ("condition", "conditions")
_ACTION_KEYS = ("action", "actions")


@dataclass(frozen=True)
class Source:
    """A document a person handed over, and what kind of thing it is."""

    document: Mapping[str, Any]
    #: Whether the document declares `blueprint.input`, which is what makes it a
    #: blueprint rather than an automation.
    blueprint: bool
    #: The blueprint's `name`, or the automation's `alias`.
    title: str
    description: str
    #: The blueprint's inputs, by input name, in declaration order. Empty for an
    #: automation, which has no inputs -- its values are literals in its body.
    inputs: Mapping[str, Mapping[str, Any]]


def read_source(text: str) -> Source:
    """Parse a document a person pasted or uploaded.

    Refuses anything that is neither an automation nor a blueprint rather than
    importing it as an empty one: "this document has no trigger, no condition and
    no action" is a true statement about a shopping list, and a module made from
    one would be a module that does nothing, silently.
    """
    try:
        loaded: object = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as error:
        raise AuthoringError(f"the document is not valid YAML: {error}") from error
    if not isinstance(loaded, Mapping):
        raise AuthoringError(
            "the document is not a mapping: an automation and a blueprint are "
            "both `key: value` documents, and this is not one"
        )
    document = loaded
    block = document.get("blueprint")
    if isinstance(block, Mapping):
        return Source(
            document=document,
            blueprint=True,
            title=str(block.get("name") or "Blueprint"),
            description=_prose(block.get("description")),
            inputs=blueprint_inputs(block),
        )
    body = _body(document)
    if not any(key in body for key in _TRIGGER_KEYS + _ACTION_KEYS):
        raise AuthoringError(
            "the document has neither a trigger nor an action, so there is "
            "nothing to import: an automation that does nothing is not a "
            "module"
        )
    return Source(
        document=document,
        blueprint=False,
        title=str(document.get("alias") or "Automation"),
        description=_prose(document.get("description")),
        inputs={},
    )


def blueprint_inputs(block: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """A blueprint's inputs, by name, with sections flattened into their members.

    Home Assistant groups inputs under a collapsible **section** -- a mapping that
    carries `name`/`icon`/`collapsed` beside a nested `input:` of its own. The
    section is not itself an input: nobody fills in "What (optional)", and the
    body never writes `!input what_section`. It only *contains* inputs, and those
    are ordinary ones, so a reader that stopped at the top level would both offer
    a person a section to fill and fail to find the declaration for a marker like
    `!input update_exclusions` -- refusing a blueprint that is perfectly fillable.

    This is the one spelling of "what does this blueprint ask for", shared by both
    readers so a marker can always be matched to its declaration.
    """
    declared = block.get("input")
    if not isinstance(declared, Mapping):
        return {}
    found: dict[str, Mapping[str, Any]] = {}
    for name, spec in declared.items():
        if isinstance(spec, Mapping) and isinstance(spec.get("input"), Mapping):
            found.update(blueprint_inputs(spec))
        else:
            found[str(name)] = spec if isinstance(spec, Mapping) else {}
    return found


def _body(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """The trigger/condition/action half of a document.

    A blueprint carries them at the top level beside its `blueprint:` block; an
    automation carries them at the top level too. The one difference is
    `blueprint:`, and `read_source` has already looked at it.
    """
    return document


def _prose(value: object) -> str:
    """A block of text as one line, or the empty string.

    YAML's `>` folds newlines into spaces but keeps a trailing one, and Home
    Assistant's blueprints are written in it, so every description this module
    reads is one strip away from being usable as a pack's description clause.
    """
    return " ".join(str(value).split()) if isinstance(value, str) else ""


# --------------------------------------------------------------------------
# The reading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityRow:
    """One entity the source names, and where it named it."""

    #: The stable id the panel sends back. `input:<name>` for a blueprint's
    #: entity input, `entity:<entity_id>` for a literal one.
    key: str
    #: What to show a person: the input's title, or the entity id.
    label: str
    #: The entity id, when the source names one. A blueprint input names none --
    #: it is filled in per install -- and the value there is empty.
    entity_id: str
    #: The domain the source implies, from the input's selector or from the
    #: entity id's own prefix. What the slot suggestion is drawn from.
    domain: str
    #: How many places in the document name it.
    count: int
    #: Whether the source offers it as optional.
    #:
    #: `default` is what makes a blueprint input optional, and its *presence* is
    #: the test -- not its value. An input with `default: null` may be left
    #: empty and one with `default: 5500` may be omitted and take 5500; an input
    #: with no `default` key at all is one Home Assistant refuses to create the
    #: automation without. Reading the value instead of the key marked the
    #: Dynamic Lighting blueprint's lux sensor and lights -- neither of which
    #: carries a default, because neither can have one -- as optional, which made
    #: the module require no slots at all and install into a room that binds
    #: nothing.
    optional: bool
    #: Where it was found, as breadcrumbs -- `actions/1/target/entity_id`.
    places: tuple[str, ...]


@dataclass(frozen=True)
class ValueRow:
    """One scalar the source carries -- a blueprint input, or a literal."""

    key: str
    label: str
    #: `boolean`, `integer`, `number`, `string` or `time`, which is the pack
    #: option vocabulary's own set plus Home Assistant's `time` selector.
    kind: str
    default: Any
    #: The input's `description`, or the key it was found under.
    description: str
    minimum: float | None
    maximum: float | None
    unit: str | None
    #: The choice set, for a selector that offers one.
    choices: tuple[str, ...]
    #: Where it was found.
    places: tuple[str, ...]


@dataclass(frozen=True)
class BehaviourRow:
    """One service call the source makes, as a candidate behaviour."""

    key: str
    service: str
    #: Whether the engine's service vocabulary has this service. `None` when the
    #: caller had no vocabulary to check against.
    #:
    #: This is not a suggestion: `catalog/services.yaml` is a closed list and the
    #: sandbox refuses a pack that calls outside it, so a call the engine cannot
    #: make is a row a person may read and may not keep. The panel greys those
    #: out, and the news arrives here -- at the reading -- rather than at save
    #: time, where it would arrive as a refusal about a module already written.
    supported: bool | None
    #: The slots this call writes through, as entity-row keys -- the reading's
    #: guess at what it acts on, from its `target`.
    acts_on: tuple[str, ...]
    #: The literal `data:` keys the call carries, in document order.
    data_keys: tuple[str, ...]
    #: Where it was found, so the panel can point at it.
    where: str
    #: How deeply nested it was. A call inside a `choose` is still a call, and
    #: the panel says so rather than hiding it.
    depth: int


@dataclass(frozen=True)
class Analysis:
    """Everything an import decision can be made about, and nothing else."""

    title: str
    description: str
    blueprint: bool
    entities: tuple[EntityRow, ...]
    values: tuple[ValueRow, ...]
    services: tuple[BehaviourRow, ...]
    #: The trigger platforms the document uses, in document order, deduplicated.
    triggers: tuple[str, ...]
    #: The condition kinds the document uses. `template` is listed here even
    #: though a pack may not carry it: the panel has to be able to say that the
    #: source decided something the engine will not.
    conditions: tuple[str, ...]
    #: What the reading could not carry, in the source's own words. Every entry
    #: is a thing the source does and a pack has no clause for, and a person is
    #: owed the list before they save.
    dropped: tuple[str, ...]


def analyse(
    source: Source, *, known_services: Collection[str] | None = None
) -> Analysis:
    """Read a document into the rows a person decides on.

    The walk is the point. Everything downstream -- the panel's tables, the
    manifest `draft_module` writes -- is a restatement of what this found, so a
    row that is wrong here is wrong everywhere and in one place.

    `known_services` is the engine's closed service list, when the caller has
    it, and turns each service row's `supported` from `None` into an answer.
    """
    known = frozenset(known_services) if known_services is not None else None
    if source.blueprint:
        entities, values = _blueprint_inputs(source)
        body: Mapping[str, Any] = {
            key: source.document[key]
            for key in _TRIGGER_KEYS
            + _CONDITION_KEYS
            + _ACTION_KEYS
            + ("variables", "trigger_variables")
            if key in source.document
        }
    else:
        entities, values = _literal_rows(source.document)
        body = source.document

    services = _services(body, known)
    triggers = _terms(body, _TRIGGER_KEYS)
    conditions = _terms(body, _CONDITION_KEYS)
    dropped = _dropped(body, source)

    # Whatever the body names outright is a row too, even when the document is a
    # blueprint: a blueprint may reference a concrete entity beside its inputs,
    # and a reading that listed only the inputs would leave that entity with
    # nowhere to be decided about.
    named = {row.key for row in entities}
    entities = entities + tuple(
        row for row in _literal_rows(body)[0] if row.key not in named
    )
    values = values + tuple(
        row
        for row in _literal_values(body)
        if row.key not in {existing.key for existing in values}
    )
    return Analysis(
        title=source.title,
        description=source.description,
        blueprint=source.blueprint,
        entities=entities,
        values=values,
        services=services,
        triggers=triggers,
        conditions=conditions,
        dropped=dropped,
    )


# --- inputs ---------------------------------------------------------------


def _blueprint_inputs(
    source: Source,
) -> tuple[tuple[EntityRow, ...], tuple[ValueRow, ...]]:
    """A blueprint's inputs as rows, and the entities its body names.

    **An input is an entity or a value, and the selector says which.** Home
    Assistant's `entity`, `target` and `device` selectors name devices; every
    other selector names a value, and its bounds are the input's own. That split
    is the whole reason a blueprint imports cleanly: the two halves of a pack --
    slots and options -- are already the two halves of a blueprint's input list.
    """
    entities: list[EntityRow] = []
    values: list[ValueRow] = []
    for name, declared in source.inputs.items():
        block = declared if isinstance(declared, Mapping) else {}
        selector = block.get("selector")
        selector = selector if isinstance(selector, Mapping) else {}
        title = str(block.get("name") or name)
        places = tuple(_input_places(source.document, name))
        if "target" in selector:
            entities.append(
                EntityRow(
                    key=f"input:{name}",
                    label=title,
                    entity_id="",
                    domain=_target_domain(selector["target"]),
                    count=len(places),
                    optional="default" in block,
                    places=places,
                )
            )
            continue
        if "entity" in selector:
            entities.append(
                EntityRow(
                    key=f"input:{name}",
                    label=title,
                    entity_id="",
                    domain=_entity_domain(selector["entity"]),
                    count=len(places),
                    optional="default" in block,
                    places=places,
                )
            )
            continue
        row = _value_row(f"input:{name}", title, block, selector, places)
        if row is not None:
            values.append(row)
    return tuple(entities), tuple(values)


def _input_places(node: object, name: str, path: str = "") -> list[str]:
    """Every path in `node` where `!input name` appears."""
    found: list[str] = []
    if isinstance(node, Mapping):
        if len(node) == 1 and node.get(INPUT_MARKER) == name:
            found.append(path or "/")
            return found
        for key, child in node.items():
            found.extend(_input_places(child, name, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found.extend(_input_places(child, name, f"{path}/{index}"))
    return found


def _value_row(
    key: str,
    title: str,
    block: Mapping[str, Any],
    selector: Mapping[str, Any],
    places: tuple[str, ...],
) -> ValueRow | None:
    """One non-entity input as a value row, or `None` when it is not a value.

    `None` for a selector this module does not know -- a `device`, an `area`, a
    `media` picker. Refusing those silently is the wrong half of the choice: the
    panel lists what it could not read under `dropped` instead, which is the
    same list the body's unreadable clauses go on.
    """
    default = block.get("default")
    if "number" in selector:
        number = selector["number"] if isinstance(selector["number"], Mapping) else {}
        step = number.get("step")
        kind = "integer" if isinstance(default, int) or step in (1, None) else "number"
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            kind = "number"
        return ValueRow(
            key=key,
            label=title,
            kind=kind,
            default=default,
            description=_prose(block.get("description")),
            minimum=_number(number.get("min")),
            maximum=_number(number.get("max")),
            unit=str(number["unit_of_measurement"])
            if "unit_of_measurement" in number
            else None,
            choices=(),
            places=places,
        )
    if "boolean" in selector:
        return ValueRow(
            key=key,
            label=title,
            kind="boolean",
            default=bool(default),
            description=_prose(block.get("description")),
            minimum=None,
            maximum=None,
            unit=None,
            choices=(),
            places=places,
        )
    if "time" in selector:
        return ValueRow(
            key=key,
            label=title,
            kind="time",
            default=str(default) if default is not None else "00:00:00",
            description=_prose(block.get("description")),
            minimum=None,
            maximum=None,
            unit=None,
            choices=(),
            places=places,
        )
    if "select" in selector:
        options = selector["select"]
        options = options if isinstance(options, Mapping) else {}
        choices = options.get("options")
        return ValueRow(
            key=key,
            label=title,
            kind="string",
            default=str(default) if default is not None else "",
            description=_prose(block.get("description")),
            minimum=None,
            maximum=None,
            unit=None,
            choices=tuple(str(choice) for choice in choices)
            if isinstance(choices, list)
            else (),
            places=places,
        )
    if "text" in selector or ("number" not in selector and default is not None):
        return ValueRow(
            key=key,
            label=title,
            kind="string",
            default=str(default),
            description=_prose(block.get("description")),
            minimum=None,
            maximum=None,
            unit=None,
            choices=(),
            places=places,
        )
    return None


def _first_domain(holder: object) -> str:
    """The first domain a selector's entity block accepts, or the empty string.

    **A block is a mapping, or a list of one, and both mean the same selector.**
    The documented shape is `entity: {domain: [light]}`; what the frontend writes
    when it saves a blueprint is `entity: [{domain: [light]}]`. Reading only the
    mapping was a real defect and not a hypothetical: the Dynamic Lighting
    blueprint's `lights` input -- the one its `light.turn_on` actually writes
    through -- is the list form, so it produced no domain, and the panel offered
    no slot for the single input the module exists to act through.

    The first domain is taken because the selector's own order is the author's,
    and an author who lists lights first means lights.
    """
    if isinstance(holder, (list, tuple)):
        holder = holder[0] if holder else None
    if not isinstance(holder, Mapping):
        return ""
    domains = holder.get("domain")
    if isinstance(domains, str):
        return domains
    if isinstance(domains, list) and domains:
        return str(domains[0])
    return ""


def _target_domain(selector: object) -> str:
    """The domain a `target` selector accepts, or the empty string.

    A `target` may name several domains -- a blueprint may offer lights *and*
    switches -- and a slot accepts a set of domains, so the suggestion needs one.
    """
    if not isinstance(selector, Mapping):
        return ""
    return _first_domain(selector.get("entity"))


def _entity_domain(selector: object) -> str:
    """The domain an `entity` selector accepts, or the empty string."""
    if not isinstance(selector, Mapping):
        return ""
    return _first_domain(selector)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# --- literals -------------------------------------------------------------


def _literal_rows(
    document: Mapping[str, Any],
) -> tuple[tuple[EntityRow, ...], tuple[ValueRow, ...]]:
    """An automation's own entities and literals.

    An automation names its entities outright, so there is nothing to infer at
    the top: every `entity_id` string in the document is an entity row, and
    every literal in an action's `data:` or in a `for:` is a value row. What the
    reading cannot know is *which* role each entity plays, and it does not
    pretend to -- `suggest_slot` offers the domain's own slot and the panel
    shows it as a suggestion.
    """
    entities: dict[str, list[str]] = {}
    for path, value in _walk(document):
        if path.endswith("/entity_id"):
            for entity_id in _entity_ids(value):
                entities.setdefault(entity_id, []).append(path)
    rows = tuple(
        EntityRow(
            key=f"entity:{entity_id}",
            label=entity_id,
            entity_id=entity_id,
            domain=entity_id.split(".", 1)[0],
            count=len(places),
            optional=False,
            places=tuple(places),
        )
        for entity_id, places in entities.items()
    )
    return rows, _literal_values(document)


def _literal_values(document: Mapping[str, Any]) -> tuple[ValueRow, ...]:
    """The scalars an automation writes into its actions.

    Only `data:` blocks and `for:` durations are read, and deliberately: a
    literal in a `to:` is the state a condition tests for, not a value a person
    would want as a setting, and offering every scalar in the document would
    bury the two that are real in a list of the ones that are not.
    """
    rows: list[ValueRow] = []
    seen: set[str] = set()
    for path, value in _walk(document):
        if not path.endswith("/for") or not isinstance(value, Mapping):
            continue
        key = f"value:{path}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ValueRow(
                key=key,
                label="For",
                kind="duration",
                default=_seconds(value),
                description="How long the reading must hold.",
                minimum=None,
                maximum=None,
                unit="seconds",
                choices=(),
                places=(path,),
            )
        )
    for service_path, data in _data_blocks(document):
        if not isinstance(data, Mapping):
            continue
        for name, value in data.items():
            kind = _scalar_kind(value)
            # Numbers and booleans only. A string inside a `data:` block is
            # overwhelmingly a label -- a log name, a mode, a scene -- and a
            # reading that offered every one of them as a setting would bury the
            # two real ones (a brightness, a colour temperature) under a column
            # of sentences. The real string settings are the blueprint inputs,
            # and those are read from the declarations rather than guessed at.
            if kind not in ("integer", "number", "boolean"):
                continue
            key = f"value:{service_path}/{name}"
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                ValueRow(
                    key=key,
                    label=str(name),
                    kind=kind,
                    default=value,
                    description=f"Written by {service_path.rsplit('/', 2)[0]}",
                    minimum=None,
                    maximum=None,
                    unit=None,
                    choices=(),
                    places=(f"{service_path}/{name}",),
                )
            )
    return tuple(rows)


def _scalar_kind(value: object) -> str | None:
    """The option kind a literal maps to, or `None` when it maps to none.

    A string holding a template is not a value a person sets -- it is a
    computation -- so it is refused here and reported under `dropped` instead.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        if "{{" in value or "{%" in value or _ENTITY_SHAPE.match(value.lstrip("!")):
            return None
        return "string"
    return None


#: `domain.object_id`, which inside a `data:` block is an entity reference
#: written where a literal belongs. Refused as a setting for the reason a
#: template is: it is not a value a person chooses.
_ENTITY_SHAPE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def _data_blocks(document: Mapping[str, Any]) -> Iterable[tuple[str, object]]:
    """Every `data:` mapping in the document, with the path of its owner."""
    for path, value in _walk(document):
        if path.endswith("/data") and isinstance(value, Mapping):
            yield path.rsplit("/", 1)[0], value


def _seconds(value: Mapping[str, Any]) -> int:
    """A Home Assistant `for:` mapping as whole seconds."""
    scale = {
        "milliseconds": 0.001,
        "seconds": 1,
        "minutes": 60,
        "hours": 3600,
        "days": 86400,
    }
    total = 0.0
    for unit, factor in scale.items():
        amount = value.get(unit)
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            total += float(amount) * factor
    return int(total)


def _entity_ids(value: object) -> tuple[str, ...]:
    """The entity ids in a value that may be one, a list, or a template.

    A template is not an entity id, and returning nothing for one is right: the
    reading cannot know what `{{ states('light.' ~ room) }}` names, and a row
    invented from it would be a slot bound to nothing.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text or "{{" in text or "{%" in text or "/" in text:
            return ()
        return tuple(
            part.strip()
            for part in text.split(",")
            if _ENTITY_SHAPE.match(part.strip())
        )
    if isinstance(value, list):
        return tuple(entity_id for child in value for entity_id in _entity_ids(child))
    return ()


# --- triggers, conditions, services ---------------------------------------


def _walk(node: object, path: str = "") -> Iterable[tuple[str, object]]:
    """Every node in a document, with its path. Depth-first, document order."""
    if isinstance(node, Mapping):
        for key, child in node.items():
            child_path = f"{path}/{key}"
            yield child_path, child
            yield from _walk(child, child_path)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            child_path = f"{path}/{index}"
            yield child_path, child
            yield from _walk(child, child_path)


def _terms(body: Mapping[str, Any], keys: Sequence[str]) -> tuple[str, ...]:
    """The `platform:`/`condition:` terms the body's clauses name, deduplicated.

    Both spellings are read -- `trigger.platform` and the newer top-level
    `triggers` list's `trigger:` key -- because a blueprint written against
    either Home Assistant era must import.
    """
    found: list[str] = []
    for key in keys:
        clause = body.get(key)
        for item in _as_list(clause):
            if not isinstance(item, Mapping):
                continue
            term = item.get("platform") or item.get("trigger") or item.get("condition")
            if isinstance(term, str) and term not in found:
                found.append(term)
    return tuple(found)


def _as_list(value: object) -> list[object]:
    """A clause as a list, whether the document spelled it as one or not.

    Home Assistant accepts a bare mapping where a list belongs, and a blueprint
    that does so is not malformed -- so a reading that assumed a list would
    report a real automation as empty.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _services(
    body: Mapping[str, Any], known: frozenset[str] | None
) -> tuple[BehaviourRow, ...]:
    """Every service call in the body, wherever it is nested.

    A call inside a `choose` is still a call, and it is reported with the depth
    it was found at rather than hidden: the engine cannot carry the branch, but
    a person is owed the news that their automation made one, and a reading that
    silently listed only the top-level calls would hide the difference between
    an automation that does one thing and one that does four.
    """
    rows: list[BehaviourRow] = []
    for path, value in _walk(body):
        if not path.endswith("/service"):
            continue
        if not isinstance(value, str) or "." not in value:
            continue
        owner = path.rsplit("/", 1)[0]
        target = _at(body, f"{owner}/target") or _at(body, f"{owner}/data")
        data = _at(body, f"{owner}/data")
        rows.append(
            BehaviourRow(
                key=f"service:{len(rows)}",
                service=value,
                supported=None if known is None else value in known,
                acts_on=_target_keys(target),
                data_keys=tuple(str(key) for key in data)
                if isinstance(data, Mapping)
                else (),
                where=owner or "/",
                depth=owner.count("/"),
            )
        )
    return tuple(rows)


def _at(body: Mapping[str, Any], path: str) -> object:
    """The value at a slash path, or `None`."""
    node: object = body
    for part in path.strip("/").split("/"):
        if part == "":
            continue
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _target_keys(target: object) -> tuple[str, ...]:
    """The entity-row keys a service call's target names.

    A target written as `!input name` is the input's own key; a target written
    as a literal entity id is that entity's. Both are things a person has to
    decide about, and reporting them in one vocabulary is what lets the panel
    draw one table.
    """
    keys: list[str] = []
    for node in _find_markers(target):
        keys.append(f"input:{node}")
    for entity_id in _literal_entity_ids(target):
        keys.append(f"entity:{entity_id}")
    seen: set[str] = set()
    return tuple(key for key in keys if not (key in seen or seen.add(key)))


def _find_markers(node: object) -> Iterable[str]:
    if isinstance(node, Mapping):
        if len(node) == 1 and isinstance(node.get(INPUT_MARKER), str):
            yield str(node[INPUT_MARKER])
            return
        for child in node.values():
            yield from _find_markers(child)
    elif isinstance(node, list):
        for child in node:
            yield from _find_markers(child)


def _literal_entity_ids(node: object) -> Iterable[str]:
    if isinstance(node, Mapping):
        for key, child in node.items():
            if key == "entity_id":
                yield from _entity_ids(child)
            else:
                yield from _literal_entity_ids(child)
    elif isinstance(node, list):
        for child in node:
            yield from _literal_entity_ids(child)


def _dropped(body: Mapping[str, Any], source: Source) -> tuple[str, ...]:
    """What the source does that a pack has no clause for.

    The list is not a fault report; it is the honest half of the reading. An
    automation that branches can be imported, and the branch is what is lost,
    and a person who is not told has been told the module does what their
    automation did.
    """
    found: list[str] = []
    for path, value in _walk(body):
        if not path.endswith(("/choose", "/if", "/repeat", "/parallel")):
            continue
        if not isinstance(value, list):
            continue
        found.append(f"a branch at {path.lstrip('/')}")
    for path, value in _walk(body):
        if not path.endswith(("/service_template", "/template")) or not isinstance(
            value, str
        ):
            continue
        if "{{" in value or "{%" in value:
            found.append(f"a template at {path.lstrip('/')}")
    if source.blueprint:
        for name, declared in source.inputs.items():
            block = declared if isinstance(declared, Mapping) else {}
            selector = block.get("selector")
            selector = selector if isinstance(selector, Mapping) else {}
            if "entity" in selector or "target" in selector or "number" in selector:
                continue
            if (
                "boolean" in selector
                or "time" in selector
                or "select" in selector
                or "text" in selector
            ):
                continue
            found.append(f"the input {name!r}, whose selector a pack has no option for")
    seen: set[str] = set()
    return tuple(item for item in found if not (item in seen or seen.add(item)))


# --------------------------------------------------------------------------
# The decisions, and the manifest they make
# --------------------------------------------------------------------------

#: The slot the corpus uses for each domain, which is what `suggest_slot`
#: offers. It is a *preference*, not a rule: `fan` and `cover` have exactly one
#: slot each, `light` has one, and `binary_sensor` has three -- which is why the
#: suggestion for a binary sensor is `motion_sensor` and the panel is where a
#: person says "no, this one is the door".
_SLOT_FOR_DOMAIN: Mapping[str, str] = {
    "light": "light_group",
    "binary_sensor": "motion_sensor",
    "sensor": "temperature_sensor",
    "climate": "climate_zone",
    "media_player": "media_player",
    "input_select": "scene_selector",
    "cover": "cover",
    "lock": "lock",
    "vacuum": "vacuum",
    "fan": "fan",
}

#: The domain each slot accepts, so a decision can be checked. Read from the
#: catalog by the caller where one is available; this is the fallback for the
#: callers that have none -- the offline tests, and a checkout whose catalog has
#: not been read yet.
_DOMAIN_FOR_SLOT: Mapping[str, str] = {
    slot: domain for domain, slot in _SLOT_FOR_DOMAIN.items()
}


#: Words that pick one slot out of several that accept the same domain.
#:
#: The domain alone is not enough to choose: `sensor` is accepted by three slots
#: and the corpus default is `temperature_sensor`, which is wrong for a sensor
#: whose own name says it measures light. A blueprint's input is *named* by its
#: author -- "Lux Sensor", "Room Humidity" -- and the name carries the one piece
#: of information the selector left out. Matched as whole words so that a
#: variable called `lux_sensor` and a title called "Lux Sensor" both find
#: `ambient_light_sensor`, and a sensor called "Temperature" does not.
_SLOT_HINTS: Mapping[str, tuple[str, ...]] = {
    "ambient_light_sensor": (
        "lux",
        "illuminance",
        "brightness",
        "light_level",
        "ambient",
    ),
    "humidity_sensor": ("humidity", "humid", "moisture", "rh"),
    "temperature_sensor": ("temperature", "temp", "thermostat"),
    "motion_sensor": ("motion", "occupancy", "presence", "movement"),
    "door_contact": ("door", "window", "opening"),
    "leak_sensor": ("leak", "water", "flood"),
    "fridge_contact": ("fridge", "freezer"),
    "light_group": ("light", "lamp", "lights"),
    "media_player": ("media", "speaker", "tv"),
    "cover": ("cover", "blind", "shade", "curtain", "garage"),
    "lock": ("lock",),
    "fan": ("fan",),
    "vacuum": ("vacuum", "roomba"),
    "climate_zone": ("climate", "thermostat", "hvac"),
    "scene_selector": ("scene",),
}


def suggest_slot(
    domain: str,
    *,
    label: str = "",
    slots: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """The slot a domain would most often be bound to, or the empty string.

    `label` is the input's own name -- a blueprint's `name:` or the key it was
    declared under -- and it is used only to choose *between* slots that the
    domain already allows. The slot a name hints at is returned only when it is
    one of the slots that accept `domain`, because a name is a hint and the
    selector is a fact: a number input called "Light Level" must not suggest an
    entity slot, and `slots` is what makes that check the catalog's answer rather
    than this module's guess at one.

    `slots` is `catalog/slots.yaml` as slot name to accepted domains. Without it
    the corpus table below stands in -- which is what the offline callers have,
    and why a suggestion with no catalog is a preference rather than a
    measurement.
    """
    accepted = _slots_accepting(domain, slots)
    if not accepted:
        return ""
    words = {word for word in _words(label) if len(word) > 2}
    if words:
        for slot in accepted:
            if words.intersection(_SLOT_HINTS.get(slot, ())):
                return slot
    preferred = _SLOT_FOR_DOMAIN.get(domain, "")
    return preferred if preferred in accepted else accepted[0]


def _slots_accepting(
    domain: str, slots: Mapping[str, Sequence[str]] | None
) -> tuple[str, ...]:
    """Every slot that accepts `domain`, in a stable order.

    Sorted by name rather than taken from the mapping's own order so that the
    same catalog suggests the same slot on every call: a suggestion that moved
    with the file's key order would make a person's saved module depend on how
    somebody else happened to sort a YAML file.
    """
    if slots:
        return tuple(
            sorted(name for name, domains in slots.items() if domain in tuple(domains))
        )
    return tuple(
        sorted(
            slot for slot, accepted in _DOMAIN_FOR_SLOT.items() if accepted == domain
        )
    )


def _words(text: str) -> list[str]:
    """The lower-case words of a name, split on everything that is not a letter."""
    return [word for word in re.split(r"[^a-z0-9]+", text.lower()) if word]


@dataclass(frozen=True)
class Draft:
    """A manifest, and the artifact it pins."""

    document: Mapping[str, Any]
    #: The artifact's path relative to the pack's own root, and its text.
    artifact_path: str
    artifact_text: str


def draft_module(
    analysis: Analysis,
    plan: Mapping[str, Any],
    *,
    slots: Mapping[str, Sequence[str]] | None = None,
) -> Draft:
    """The manifest a person's decisions make, and the automation it pins.

    `plan` is what the panel sends back, and it is deliberately the same shape
    as `analyse`'s answer with a decision added to every row: a plan that named
    rows the reading did not find would be a manifest built from a document
    nobody read, so the rows are checked against the analysis rather than
    trusted.

    `slots` maps a slot name to the domains it accepts, so a decision can be
    refused before it becomes a pack. `None` means the caller has no catalog and
    the check is skipped, which is the honest degradation for an offline caller
    -- the pack is still validated against the real catalog before it is saved.
    """
    name = _pack_name(plan.get("name"))
    entities = {row.key: row for row in analysis.entities}
    services = {row.key: row for row in analysis.services}

    decisions = _mapping(plan.get("entities"))
    bindings: dict[str, str] = {}
    required: list[str] = []
    optional: list[str] = []
    for key, decision in decisions.items():
        row = entities.get(key)
        if row is None:
            raise AuthoringError(f"{key!r} names nothing this source contains")
        chosen = decision.get("decision")
        if chosen != "slot":
            continue
        slot = str(decision.get("slot") or "")
        if not slot:
            raise AuthoringError(
                f"the entity {row.label!r} was said to be a slot and names none"
            )
        _check_domain(slot, row.domain, slots)
        if slot in required or slot in optional:
            raise AuthoringError(
                f"two entities were bound to the slot {slot!r}; a slot is one "
                "device, and a pack that named it twice would act on one of them"
            )
        (required if decision.get("required") else optional).append(slot)
        bindings[key] = slot

    options: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for key, decision in _mapping(plan.get("values")).items():
        if decision.get("decision") != "setting":
            continue
        row = _value_at(analysis, key)
        option = _option(decision, row)
        if option["key"] in seen_keys:
            raise AuthoringError(
                f"two settings were given the name {option['key']!r}, so one of "
                "them would shadow the other"
            )
        seen_keys.add(option["key"])
        options.append(option)

    behaviours = _behaviours(
        analysis, plan, bindings, services, entities, option_keys=seen_keys
    )
    if not behaviours:
        raise AuthoringError(
            "no service calls were kept, so the module would carry no "
            "behaviour: a module that does nothing is not a module"
        )
    _check_settings_are_read(options, behaviours)

    artifact_path = f"{name}/{name}.yaml"
    document: dict[str, Any] = {
        "name": name,
        "version": str(plan.get("version") or "1.0.0"),
        "description": str(plan.get("description") or analysis.description or name),
        "kind": "module",
        "engine_api": ">=1.0.0 <2.0.0",
        "license": str(plan.get("license") or "mit"),
    }
    # `requires_slots` is emitted even when empty, because the schema's `module`
    # conditional requires the key rather than requiring it to be non-empty. A
    # module with no required slot is a module that acts on what it is given --
    # legitimate, and refused by an absent key.
    document["requires_slots"] = required
    if optional:
        document["optional_slots"] = optional
    document["provides"] = [{"path": artifact_path, "class": "automation"}]
    if options:
        document["options"] = options
    document["behaviours"] = behaviours
    document["i18n"] = {
        "default": {
            "pack": str(plan.get("title") or analysis.title or name),
            "description": str(plan.get("description") or analysis.description or name),
            **{
                behaviour["name"]: str(
                    _plan_behaviour(plan, behaviour["name"]).get("title")
                    or behaviour["name"].replace("_", " ").capitalize()
                )
                for behaviour in behaviours
            },
        }
    }
    return Draft(
        document=document,
        artifact_path=artifact_path,
        artifact_text=_artifact(name, analysis, behaviours, bindings),
    )


def _mapping(value: object) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): dict(child)
        for key, child in value.items()
        if isinstance(child, Mapping)
    }


def _pack_name(value: object) -> str:
    """A pack name from what a person typed.

    Lower-cased and underscored rather than refused, because a person typing
    "Dynamic Lighting" has named their module and the schema's spelling is a
    detail of the file -- refusing it would be the panel asking them to learn a
    pattern. A name that is empty after the cleaning *is* refused: there is
    nothing to call the pack.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if not cleaned:
        raise AuthoringError("the module needs a name")
    if cleaned[0].isdigit():
        cleaned = f"m_{cleaned}"
    return cleaned


def _check_domain(
    slot: str, domain: str, slots: Mapping[str, Sequence[str]] | None
) -> None:
    """Refuse a slot bound to a domain it does not accept, when we can know."""
    if slots is None or not domain:
        return
    accepts = slots.get(slot)
    if accepts is None:
        raise AuthoringError(f"no slot is called {slot!r}")
    if domain not in accepts:
        raise AuthoringError(
            f"the slot {slot!r} accepts {', '.join(accepts)} and the source names "
            f"a {domain}: binding one to the other would be a behaviour that "
            "acts on a device it cannot ask"
        )


def _value_at(analysis: Analysis, key: str) -> ValueRow | None:
    for row in analysis.values:
        if row.key == key:
            return row
    return None


#: How many seconds one of a source's own unit names is. The engine reads a
#: behaviour's `for` in seconds and a manifest's `unit` is display-only, never
#: parsed (`schemas/pack-manifest/1.4.0.json`), so a setting a source measures in
#: minutes has to arrive in seconds or the module waits 60x too little while its
#: control says "minutes".
_SECONDS_PER_UNIT = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
}

#: A title that names the unit the setting used to be in -- "Trigger Interval
#: (minutes)". Dropped when the value is converted, because a control whose
#: number is seconds and whose words are minutes is the defect the conversion
#: exists to remove.
_UNIT_WORDS = re.compile(
    r"\s*\((?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\)\s*$", re.IGNORECASE
)


def _seconds_in(unit: object) -> int | None:
    """How many seconds one `unit` is, or `None` when it is not a length of time.

    An absent unit is a length of time in the manifest's own unit -- seconds --
    because a `duration` *is* a whole number of seconds and says so in its type
    rather than through a unit string. Everything else has to be a word that
    names a length of time: `%` and `K` are units a number row may legitimately
    carry, and waiting for a hundred of them would be waiting for a hundred
    seconds with no clause anywhere able to say otherwise.
    """
    if unit is None or not str(unit).strip():
        return 1
    return _SECONDS_PER_UNIT.get(str(unit).strip().lower())


def _scaled(value: object, seconds: int) -> object:
    """`value` in seconds, and anything that is not a number left alone."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value * seconds
    return value


def _option(decision: Mapping[str, Any], row: ValueRow | None) -> dict[str, Any]:
    """One setting decision as the manifest's `options` clause.

    The option's `type` is checked against the vocabulary's closed set rather
    than passed through, because a value the schema refuses takes the whole pack
    with it and the panel's job is to make that impossible from its own side.

    **A wait is seconds, whatever unit the source counted in.** The engine reads
    a behaviour's `for` as a number of seconds and the manifest's `unit` is a
    label the engine never parses, so a setting the source measures in minutes
    has to be written in seconds here. The MarqBarq blueprint this journey is
    walked with declares `trigger_interval` and `color_transition_minutes` in
    minutes: left alone, either one would produce a control reading "3 minutes"
    over a behaviour that waits three seconds. A unit that names a length of time
    is converted and labelled `seconds`.

    **A unit that names something else is left exactly as it is**, and the
    refusal for it lives in `_check_settings_are_read` rather than here, because
    only that function knows whether anything reads the setting: a `%` value
    nothing waits on is already refused as an option whose control would do
    nothing, and saying "a brightness cannot be a wait" about a setting that was
    never meant to be one would name the wrong fault.
    """
    key = re.sub(
        r"[^a-z0-9_]+", "_", str(decision.get("key") or "").strip().lower()
    ).strip("_")
    if not key:
        raise AuthoringError("a setting needs a name")
    unit = decision.get("unit", row.unit if row else None)
    seconds = _seconds_in(unit) or 1
    declared = str(decision.get("type") or (row.kind if row else "string"))
    if declared not in ("boolean", "integer", "number", "string", "enum", "duration"):
        declared = "string"
    default = decision.get("default", row.default if row else None)
    title = str(decision.get("title") or (row.label if row else key))
    if seconds != 1:
        title = _UNIT_WORDS.sub("", title).strip() or title
    option: dict[str, Any] = {
        "key": key,
        "type": declared,
        "default": _coerced(declared, _scaled(default, seconds)),
        "title": title,
    }
    description = decision.get("description") or (row.description if row else "")
    if description:
        option["description"] = str(description)
    for bound, source in (("minimum", "minimum"), ("maximum", "maximum")):
        value = decision.get(bound, row.__getattribute__(source) if row else None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            option[bound] = value * seconds
    if unit and seconds != 1:
        option["unit"] = "seconds"
    elif unit:
        option["unit"] = str(unit)
    if declared == "enum":
        choices = decision.get("enum") or (list(row.choices) if row else [])
        if not choices:
            raise AuthoringError(f"the setting {key!r} is a choice and offers none")
        option["enum"] = [str(choice) for choice in choices]
    return option


def _coerced(kind: str, value: object) -> Any:
    """`value` as the type the option declares, or a refusal.

    A default that does not match its own type is the one option fault the
    schema cannot describe -- "type integer, default 'soon'" is a legal JSON
    Schema and a setting whose control cannot render -- so it is refused here,
    where the message can name the field.
    """
    if kind == "boolean":
        return bool(value)
    if kind in ("integer", "duration"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AuthoringError(f"{value!r} is not a whole number")
        return int(value)
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AuthoringError(f"{value!r} is not a number")
        return float(value)
    return "" if value is None else str(value)


def _behaviours(
    analysis: Analysis,
    plan: Mapping[str, Any],
    bindings: Mapping[str, str],
    services: Mapping[str, BehaviourRow],
    entities: Mapping[str, EntityRow],
    *,
    option_keys: Collection[str] = (),
) -> list[dict[str, Any]]:
    """The kept service calls as behaviour clauses, in the plan's order.

    The order of a behaviour's `slots` is not cosmetic: the interpreter watches
    the *first* and acts through the *last* (`engine/behaviours/declared.py`),
    so the panel's "what it watches" list is written first and the acted-on slot
    last. A behaviour whose watched list is empty watches the slot it acts on,
    which is the reading's own answer for a call that names only its target.

    **`for` is the one clause here that reads an option**, and it is written only
    when the decision names a setting this module declares: the clause turns a
    `match` from "is open" into "has been open this long" and is the declarative
    interpreter's only way to act on a number, so `for` naming anything else is a
    manifest the validator refuses with a message about a duration that is not
    there. Refusing it here names the behaviour and the setting instead.
    """
    rows: list[dict[str, Any]] = []
    for decision in _plan_behaviours(plan):
        if not decision.get("keep", True):
            continue
        key = str(decision.get("key") or "")
        row = services.get(key)
        if row is None:
            raise AuthoringError(f"{key!r} is not a service call this source makes")
        acted = _acted_slot(decision, row, bindings)
        watched = [
            str(slot)
            for slot in _as_list(decision.get("watched"))
            if isinstance(slot, str) and slot and slot != acted
        ]
        name = _behaviour_name(decision, row)
        behaviour: dict[str, Any] = {"name": name}
        trigger = str(decision.get("trigger") or "")
        if trigger:
            behaviour["trigger"] = trigger
        condition = str(decision.get("condition") or "")
        if condition:
            behaviour["condition"] = condition
        # The hold, and the only clause that reads one of this module's settings.
        hold = str(decision.get("for") or "").strip().lower()
        if hold:
            if hold not in option_keys:
                raise AuthoringError(
                    f"the behaviour {name!r} waits for {hold!r}, which is not a "
                    "setting this module declares, so the hold would name a "
                    "duration that is not there"
                )
            behaviour["for"] = hold
        behaviour["action"] = "service"
        behaviour["priority"] = int(decision.get("priority", 10))
        behaviour["services"] = [row.service]
        behaviour["slots"] = [*watched, acted]
        scope = str(decision.get("scope") or "room")
        behaviour["scope"] = "house" if scope == "house" else "room"
        rows.append(behaviour)
    names = [row["name"] for row in rows]
    if len(set(names)) != len(names):
        raise AuthoringError(
            "two behaviours were given the same name, so one would shadow the "
            "other in the house's flags"
        )
    return rows


def _check_settings_are_read(
    options: Sequence[dict[str, Any]], behaviours: Sequence[Mapping[str, Any]]
) -> None:
    """Refuse a setting nothing reads, and type the ones something does.

    The manifest's own `options` description states the rule this enforces: an
    option is only worth declaring if something reads it, "because the panel will
    render a control for it and the control will do nothing". The declarative
    interpreter reads exactly two things -- a `duration` a behaviour waits out
    through `for` (`engine/behaviours/declared.py`), and a role's reach switch,
    which `live_profiles` declares itself and no manifest writes -- so a setting
    outside those two is a control a person can move with no effect anywhere in
    the house. Refusing it here rather than at install is what lets the refusal
    name the plan's own words: the option list and the behaviour list are both in
    hand, and the message can point at the behaviour that should be waiting.

    **The type is forced to `duration` rather than merely checked.** The one
    reading is a length of time, and the panel's version of the same value may
    have come from a blueprint selector that says `number` with a unit of
    minutes -- a declaration the validator refuses for a `for` that names it,
    with a message about a clause rather than about the field a person set. What
    a wait *is* decides the type, so the wait decides it here.

    **And a wait's default has to be a number of seconds**, which is the second
    half of the same rule. The panel may offer any value row as something to wait
    for, and a blueprint's booleans are rows like any other: naming one produces
    an option whose `type` is a duration and whose `default` is `True`, which
    `validate_manifest` refuses as a mismatched option -- so the refusal here
    names the field rather than the clause, and says what a duration is. The
    panel filters the rows it offers to the ones that can carry a length of time
    (`tabs/dev.ts`); this is the boundary that holds when something else drives
    the plan.

    **And the unit has to name a length of time**, which is the third. A
    blueprint measures its brightness bounds in `%` and its colour temperatures
    in `K`, and both are `number` rows a plan may name; the manifest has no way
    to say that a hundred of *those* is a hundred seconds, so the wait is refused
    rather than written wrong. The conversion of the units that *are* lengths of
    time into seconds happened in `_option`, so a unit arriving here that is not
    one is a unit nothing downstream can honour.
    """
    held = {
        str(behaviour["for"])
        for behaviour in behaviours
        if isinstance(behaviour.get("for"), str) and behaviour.get("for")
    }
    for option in options:
        key = str(option["key"])
        if key not in held:
            raise AuthoringError(
                f"the setting {key!r} is read by nothing, so its control would "
                "do nothing: the engine reads a `duration` a behaviour waits "
                "out -- give a behaviour something to wait for, or leave the "
                "value out of the module"
            )
        unit = option.get("unit")
        if unit is not None and _seconds_in(unit) is None:
            raise AuthoringError(
                f"a behaviour waits for {key!r}, so that setting is a length of "
                f"time in seconds -- but the source measures it in {unit!r}, and "
                "a wait for that many of those is a wait for some other length "
                "entirely. Only a value whose unit names a length of time, or "
                "that has no unit at all, can be waited for."
            )
        default = option.get("default")
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            raise AuthoringError(
                f"a behaviour waits for {key!r}, so that setting is a length of "
                f"time in seconds -- but its default is {default!r}, and a wait "
                "that defaults to anything but a number is a hold nothing can "
                "read. Only a value that is already a number or a length of time "
                "can be waited for."
            )
        option["type"] = "duration"


def _acted_slot(
    decision: Mapping[str, Any], row: BehaviourRow, bindings: Mapping[str, str]
) -> str:
    """The slot one behaviour writes through.

    The panel names it directly when it can. When it does not, the reading's own
    answer -- the target's first ententity that a person mapped -- is used, and a
    call whose target mapped to nothing is refused rather than guessed at.
    """
    named = str(decision.get("slot") or "")
    if named:
        return named
    for key in row.acts_on:
        if key in bindings:
            return bindings[key]
    raise AuthoringError(
        f"the call to {row.service} acts on a device and no slot was chosen for "
        "it, so the behaviour would have nothing to write through"
    )


def _behaviour_name(decision: Mapping[str, Any], row: BehaviourRow) -> str:
    named = re.sub(
        r"[^a-z0-9_]+", "_", str(decision.get("name") or "").strip().lower()
    ).strip("_")
    if named:
        return named
    return row.service.split(".", 1)[-1]


def _plan_behaviour(plan: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    for decision in _plan_behaviours(plan):
        if str(decision.get("name") or "") == name:
            return decision
    return {}


def _plan_behaviours(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = plan.get("behaviours")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _artifact(
    name: str,
    analysis: Analysis,
    behaviours: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, str],
) -> str:
    """The automation file a pack pins, written from the behaviours.

    **It is documentation, and it is generated rather than omitted.** The
    interpreter binds the manifest's clauses and never reads this file -- the
    same division `packs/official/bedtime/bedtime.yaml` is written to -- but the
    schema requires a pack to pin at least one artifact, and this is the artifact
    the module *is*: the automation it came from, restated in the engine's terms.
    A person reading it sees the shape their blueprint became.

    **`trigger` and `action`, singular.** They are Home Assistant's older
    spelling and still the shape `packs/official/bedtime/bedtime.yaml` is written
    in, and they are what `engine.sandbox.file_class` reads a file by: an artifact
    declaring class `automation` must carry one of those two top-level keys or the
    sandbox refuses it as a `class_mismatch`, which is exactly what this function
    produced when it wrote the modern plural spelling. The export direction uses
    the plural shape, because that copy is for Home Assistant to run and this one
    is for the engine to pin; the two are different readers and the file says so.
    """
    lines = [
        f"# Generated by the Open House Dev tab from {analysis.title!r}.",
        "#",
        "# The engine never reads this file: it binds the manifest's `behaviours`,",
        "# and this is those behaviours in Home Assistant's language. It is here",
        "# because a pack pins what it confers, and what this module confers is",
        "# the automation above restated in the engine's vocabulary.",
        "#",
        "# A slot is named where a device would be, exactly as",
        "# `packs/official/bedtime/bedtime.yaml` names its own: what this file",
        "# records is which role each action goes through, not which entity a",
        "# particular room bound to it.",
        f"alias: {analysis.title}",
        "trigger:",
    ]
    for behaviour in behaviours:
        trigger = behaviour.get("trigger", "state")
        lines.append(f"  - trigger: {trigger}")
    lines.append("action:")
    for behaviour in behaviours:
        services = behaviour.get("services", [])
        service = services[0] if services else ""
        slots = [str(slot) for slot in behaviour.get("slots", [])]
        acted = slots[-1] if slots else ""
        lines.append(f"  - service: {service}")
        # The target only when the behaviour acts through a role. A `target:`
        # with an empty `entity_id:` reads as an action on no entity, which is
        # not what a behaviour written without a slot is: it is an action that
        # needs no device, and saying so by *not* naming one is the same rule
        # `automation_documents` below follows for the export.
        if acted:
            lines.append("    target:")
            lines.append(f"      entity_id: {acted}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# The export direction
# --------------------------------------------------------------------------


def automation_documents(
    behaviours: Sequence[Mapping[str, Any]],
    *,
    bindings: Mapping[str, Sequence[str]],
    options: Mapping[str, Any],
    title: str,
) -> tuple[Mapping[str, Any], ...]:
    """A pack's behaviours as the automations Home Assistant would run.

    One automation per behaviour, because a behaviour is one trigger and one
    thing to do and Home Assistant's unit of the same is one automation. A slot
    that resolved to several entities -- a house-scope role reaching every room
    -- becomes a target naming all of them, which is what the engine would have
    actuated, so the export is faithful rather than convenient.

    The alternatives are not offered, and the omission is deliberate: an
    automation carrying a template would be a computation where the pack
    declares a policy, and `catalog/pack-policy.yaml` refuses exactly that
    inside a pack. Emitting it here would put into Home Assistant the thing the
    engine refuses to be.

    **A slot that resolved to nothing is written as a target naming nothing, and
    never as a missing target.** The two are not the same automation to Home
    Assistant: `service: light.turn_off` with no `target` turns off *every* light
    in the house, so the export of a module whose slot this room does not bind
    would be a device-wide action where the module decided nothing -- the one
    translation error that can change what a house does rather than merely fail
    to do it. `bound_slots` says the same thing from its side, and this is the
    line that keeps the two honest.
    """
    documents: list[Mapping[str, Any]] = []
    for behaviour in behaviours:
        name = str(behaviour.get("name", "behaviour"))
        slots = [str(slot) for slot in behaviour.get("slots", [])]
        acted = slots[-1] if slots else ""
        entities = list(bindings.get(acted, ()))
        document: dict[str, Any] = {
            "alias": f"{title}: {name.replace('_', ' ')}",
            "description": (
                f"Exported from the Open House module {title!r}. The module "
                "decides this in the engine; this automation is the same decision "
                "written for Home Assistant to run on its own."
            ),
            "mode": "single",
            "triggers": [{"trigger": str(behaviour.get("trigger") or "state")}],
        }
        if behaviour.get("condition"):
            document["conditions"] = [{"condition": str(behaviour["condition"])}]
        action: dict[str, Any] = {}
        services = [str(service) for service in behaviour.get("services", [])]
        if services:
            action["service"] = services[0]
        # Always a `target`, even when it names nothing: see the docstring -- an
        # absent target is Home Assistant's "every entity of that domain", which
        # is not what a module that resolved no entity decided.
        if acted:
            action["target"] = {
                "entity_id": entities if len(entities) != 1 else entities[0]
            }
        document["actions"] = [action] if action else []
        documents.append(document)
    return tuple(documents)


def automation_text(documents: Sequence[Mapping[str, Any]]) -> str:
    """The exported automations as one YAML document.

    One document with a list would be a `automations.yaml` entry; several would
    be several. Both are wrong for a person pasting into the editor, so the
    answer is the YAML text of the list itself -- what Home Assistant accepts in
    its own YAML mode for a single automation, joined when there is more than
    one.
    """
    if len(documents) == 1:
        return yaml.safe_dump(dict(documents[0]), sort_keys=False, allow_unicode=True)
    return "\n".join(
        yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True)
        for document in documents
    )


def module_text(document: Mapping[str, Any]) -> str:
    """A manifest as YAML, in the spelling the rest of the tree writes.

    `sort_keys=False` because a manifest's clause order is the order a person
    reads it in -- name, then what it is, then what it does -- and alphabetical
    order would put `behaviours` above `name` and make every pack on disk look
    different from every other.
    """
    return yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True, width=88)
