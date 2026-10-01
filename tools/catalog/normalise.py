"""The stage A extraction: the fact stores, and the verbatim store beside them.

`catalog/raw-behaviors.json` is a record per selected file in every reference
repo, and it is committed. Every one of those records is built by `extract`,
whose output dictionary has exactly the keys `schemas/catalog/fact-fields.yaml`
allows and nothing else -- the type is fixed enough that the emitted value cannot
carry authored text, and the schema closes the other direction with
`additionalProperties: false`. The withheld text -- the aliases, display names
and comments a source author wrote -- goes to `.local/raw-verbatim.json`, which
is gitignored and outside `catalog/`, and the two stores differ in that one
respect. `catalog/hardcoded_refs.yaml` is emitted from the same pass.

The classification rule is structural, and it is the only place the extraction
has to *decide* anything. A `domain.identifier` string that is the value of a
`service` or `action` key is a `service_call`; a reference anywhere else -- under
`target`, `entity_id`, a mapping key, or bare in an action's argument -- is an
`entity_ref`. Service calls are recorded but never enter an entity identifier
set, because the provenance gate in task 4.6 compares a shipped row's prose
against the entity references of the raw records it cites, and `light.turn_on`
appears in every lighting automation in all four repos: counting it as an
identifier would reject a legitimate CCOSTAN row for a name johnkoht also spells.

Jinja templates are where the rule earns its keep, and where a naive
implementation goes wrong. A template is a *string* to YAML, so the references in
it are not reachable by walking structure; they are found by reading the string.
These repos use templates more than any other style -- 770 selected files, the
single largest source of references -- so a rule that missed them would leave the
hardcoding audit incomplete in most of the corpus, which is exactly what
`spec.txt` asks the audit not to be. Three sub-rules, and the third is the one a
first draft misses:

- a quoted literal inside a `{{ }}` block that matches the entity pattern is a
  reference, and it is a `service_call` when the call it is the argument of is
  `service(` or `action(`, or when the whole block is itself the value of a
  `service`/`action` key, and an `entity_ref` otherwise -- the second signal is
  what keeps `action: '{{ "hassio.host_reboot" if ... }}'` from reading two
  service names as entities;
- attribute access on the `states` global -- `states.light.kitchen.state` -- has
  no string argument at all, so it is read as the two attribute segments after
  `states` and recorded as the `entity_ref` `light.kitchen`;
- a `!secret`, `!include` or `!input` argument is *not* a reference, and
  `automations.yaml` is why the distinction is drawn by the loader rather than by
  a pattern: it matches the entity pattern exactly, so a walk that read tag
  arguments would report a file path as an entity in most packages in the corpus.
  `ha_yaml.Tag` is what makes that structural rather than a name list.

The vocabulary terms are the block kinds an artifact uses, read from the same
walk: the `platform`/`trigger` of a trigger block, the `condition` of a condition
block, and the discriminating key of an action block (`service` and `action` both
normalise to `service`, since they are one operation spelled two ways -- this is
the normalisation the design calls for, and it is what makes an old `service:`
automation comparable with a modern `action:` one). The term *set* is learned
here; publishing it as the vocabulary's `1.1.0` is task 7.1, which is why this
module writes terms and does not freeze them.

Everything in this module reads the clones and is therefore local. The committed
outputs are what CI validates; nothing here runs in a job that lacks
`ressources/`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import yaml

from . import behaviors, errors, facts, ha_yaml, inventory, lexicon, licenses, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

NORMALISE_CHECK = "normalise"
HARDCODED_REFS_CHECK = "hardcoded-refs"

#: The committed fact store, the gitignored verbatim store, and the second
#: committed facts store, by filename. The verbatim name is the one task 3.8
#: singles out: it is what the prose gate reads, so its determinism matters most.
RAW_BEHAVIORS_FILE = "raw-behaviors.json"
VERBATIM_FILE = "raw-verbatim.json"
HARDCODED_REFS_FILE = "hardcoded_refs.yaml"

#: A `domain.object_id` as Home Assistant spells one, and the same pattern the
#: `entity_ref` fact-shape commits. Repeated here rather than read from
#: `fact-shapes.yaml` because a service name (`light.turn_on`) is spelled the same
#: way and has to be classified before it is known to be a service or an entity;
#: reading the shape set would only answer the question this pattern is asked to
#: pose. A test asserts the two agree, so the repetition cannot drift.
ENTITY_REF_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

#: A vocabulary term: what `behavior-vocabulary` `1.0.0` declares an element of an
#: axis to be. Also repeated from the runtime schema rather than read, for the
#: same reason and under the same test.
TERM_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: A `{{ ... }}` block, DOTALL so a block may span lines (these repos indent
#: multi-line templates) and non-greedy so two blocks on one line stay two.
_TEMPLATE_BLOCK_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

#: A quoted literal inside a block. Both quote kinds, matched with a backreference
#: so `"a'b"` is one literal rather than ending at the apostrophe.
_TEMPLATE_LITERAL_RE = re.compile(r"(['\"])([^'\"]*)\1")

#: Attribute access on the `states` global: `states.light.kitchen_ceiling` and
#: then any further attributes. Anchored on the global because the entity literal
#: is the two segments immediately after `states` -- a rule over the whole
#: dotted chain would read `states.light` and report a domain that does not exist.
_STATES_ATTR_RE = re.compile(r"\bstates\.([a-z_]+)\.([a-z0-9_]+)")

#: What stands immediately before a literal that makes it a service call rather
#: than an entity. Anchored at the end because the text passed in is the block up
#: to the literal's opening quote.
_TEMPLATE_SERVICE_RE = re.compile(r"\b(?:service|action)\s*\(\s*$")

#: Keys whose string value is a service name. `action` is the modern spelling of
#: `service` and names the same operation; both are here so an old automation and
#: a new one classify the same way.
SERVICE_KEYS: tuple[str, ...] = ("service", "action")

#: Keys that introduce a trigger block. `trigger` is also the discriminating key
#: *inside* a modern trigger, which is why block detection requires a list value
#: and term extraction reads the scalar: one name, two positions, told apart by
#: the shape of the value rather than by where it sits.
TRIGGER_KEYS: tuple[str, ...] = ("trigger", "triggers")
CONDITION_KEYS: tuple[str, ...] = ("condition", "conditions")
ACTION_KEYS: tuple[str, ...] = ("action", "actions", "sequence")

#: The keys a trigger block uses to name its kind, in the order they are tried.
TRIGGER_TERM_KEYS: tuple[str, ...] = ("platform", "trigger")

#: The keys an action block uses to name its kind, in priority order, mapped to
#: the term they produce. `action` and `service` collapse to one term because they
#: are one operation; everything else keeps its own name. `device_id` becomes
#: `device` so the term matches the trigger and condition spelling of the same
#: concept, which is what lets a behaviour be compared across repos.
ACTION_TERMS: tuple[tuple[str, str], ...] = (
    ("service", "service"),
    ("action", "service"),
    ("delay", "delay"),
    ("wait_template", "wait_template"),
    ("wait_for_trigger", "wait_for_trigger"),
    ("choose", "choose"),
    ("if", "if"),
    ("repeat", "repeat"),
    ("parallel", "parallel"),
    ("stop", "stop"),
    ("variables", "variables"),
    ("condition", "condition"),
    ("event", "event"),
    ("fire_event", "fire_event"),
    ("device_id", "device"),
    ("scene", "scene"),
)

#: Keys whose string value is a display name rather than an identifier. Collected
#: for the verbatim store only; the committed record has nowhere to put them.
DISPLAY_KEYS: tuple[str, ...] = ("alias", "name", "title", "friendly_name")

#: The domains whose reference is a fixed thing rather than a slot candidate, and
#: the constant each maps to. `time` and `device_id` are in the closed set but are
#: not domains, so they can never be inferred here; they are named in task 5.3.
CONSTANT_DOMAINS: dict[str, str] = {
    "device_tracker": "device_tracker",
    "person": "person",
    "sun": "sun",
}

#: The domains that are house-level rather than attached to a room. This is a
#: judgement and the module records it as one: the closed set the audit admits is
#: `room | house`, a `sun.sun` reference is not a room's, and the room *tokens*
#: that would let the inference be made from the object id live in
#: `catalog/rooms.yaml`, which task 5.1 writes and which does not exist yet. So the
#: inference is made from the domain alone and is revisited in 5.3, where the role
#: lexicon that maps a reference to a slot is.
HOUSE_DOMAINS: frozenset[str] = frozenset(
    {"sun", "person", "device_tracker", "zone", "weather", "calendar"}
)

#: Each repo's naming-convention label, as a slug. These are *our* labels, not
#: anything copied from a source: a label is a fact about the shape of a repo's
#: identifiers, and the fact-shape it is admitted by cannot hold a sentence. The
#: set is fixed when task 3.6 sees the data, which is why an unknown repo raises
#: rather than defaulting -- a default would silently label a fifth repo
#: `unknown` and read as coverage.
NAMING_CONVENTIONS: dict[str, str] = {
    "ccostan": "opaque_fixture_ids",
    "johnkoht": "domain_room_function",
    "fwartner": "german_compounded",
    "renemarc": "room_function_descriptive",
}

_SLUG_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """A normalised identifier with no dot, admitted by the `slug` fact-shape.

    Lowercased and with every run outside `[a-z0-9]` collapsed to one underscore,
    because a record `id` is a fact about a file and not its path: the path is a
    separate field, admitted by membership in the selected-path set, and the id
    exists so a shipped row has a short token to cite.
    """
    return _SLUG_SPLIT_RE.sub("_", text.lower()).strip("_")


@dataclass(frozen=True, slots=True)
class Extraction:
    """One selected file, normalised: the facts, and the text they withhold."""

    repo: str
    path: str
    artifact_class: str
    record_id: str
    triggers: tuple[str, ...]
    conditions: tuple[str, ...]
    actions: tuple[str, ...]
    entity_refs: tuple[str, ...]
    service_calls: tuple[str, ...]
    aliases: tuple[str, ...]
    names: tuple[str, ...]
    comments: tuple[str, ...]

    def as_record(self) -> dict[str, object]:
        """The committed record. Its key set is the fact allowlist, and only it.

        Written as a literal rather than derived from the allowlist on purpose:
        this is the one place a key could be added to the committed store, so it
        is the place a test reads. The schema generated from the allowlist is what
        makes that test fail in the other direction.
        """
        return {
            "id": self.record_id,
            "path": self.path,
            "class": self.artifact_class,
            "triggers": list(self.triggers),
            "conditions": list(self.conditions),
            "actions": list(self.actions),
            "slots": [],
            "entity_refs": list(self.entity_refs),
            "unclaimed": None,
        }

    def as_verbatim(self) -> dict[str, object]:
        """The same record, unfiltered: the committed facts plus the withheld text.

        This is the one respect in which the two stores differ, and it is the
        whole licence position expressed as a mechanism -- the committed store has
        no field these three could be written into, so the split is a property of
        the schemas rather than of this function's good behaviour.
        """
        return {
            **self.as_record(),
            "aliases": list(self.aliases),
            "names": list(self.names),
            "comments": list(self.comments),
        }


def _template_references(
    block: str, *, service_value: bool = False
) -> tuple[set[str], set[str]]:
    """The entity and service references inside one `{{ }}` block.

    Returned separately rather than as one set because the classification is the
    point: a literal that is the argument of a service call is excluded from the
    entity set, and a caller that merged the two could not tell a handled case
    from an unhandled one.

    A literal is a service call on either of two signals. The first is local to
    the block: the call the argument belongs to is `service(` or `action(`. The
    second is the block's own structural position, carried in by `service_value`
    -- the whole template is the value of a `service` or `action` key, so every
    literal in it names a service. The second signal is not decorative: the
    corpus writes `action: '{{ "hassio.host_reboot" if ... else
    "homeassistant.restart" }}'`, and without it those two service names would
    be read as entity references -- the exact confusion between the two that
    this classifier exists to prevent.
    """
    entities: set[str] = set()
    services: set[str] = set()
    for match in _TEMPLATE_LITERAL_RE.finditer(block):
        literal = match.group(2)
        if not ENTITY_REF_RE.match(literal):
            continue
        if service_value or _TEMPLATE_SERVICE_RE.search(block[: match.start()]):
            services.add(literal)
        else:
            entities.add(literal)
    for attr in _STATES_ATTR_RE.finditer(block):
        entities.add(f"{attr.group(1)}.{attr.group(2)}")
    return entities, services


def _string_references(
    text: str, entities: set[str], services: set[str], *, service_value: bool
) -> None:
    """Every reference a single scalar string carries, into the two sets.

    Template blocks first, and the plain pattern only if there is no block: a
    string that is one template is not also a bare `domain.identifier`, and
    testing it both ways would double-count a literal that a block already
    classified.
    """
    found = False
    for block in _TEMPLATE_BLOCK_RE.finditer(text):
        found = True
        block_entities, block_services = _template_references(
            block.group(1), service_value=service_value
        )
        entities |= block_entities
        services |= block_services
    if found:
        return
    if ENTITY_REF_RE.match(text):
        (services if service_value else entities).add(text)


def _walk(
    node: object, entities: set[str], services: set[str], *, service_value: bool = False
) -> None:
    """One node of a parsed document, classified by structural position.

    A `ha_yaml.Tag` is a leaf here, and that is the second half of the reason the
    loader exists: `!include automations.yaml` carries the argument
    `automations.yaml`, which matches the entity pattern exactly, so a walk that
    descended into tag arguments would report most packages in the corpus as
    referencing an entity called `automations.yaml`.
    """
    if isinstance(node, ha_yaml.Tag):
        return
    if isinstance(node, str):
        _string_references(node, entities, services, service_value=service_value)
        return
    if isinstance(node, dict):
        for key, value in as_mapping(cast("object", node)).items():
            if ENTITY_REF_RE.match(key):
                entities.add(key)
            _walk(value, entities, services, service_value=key in SERVICE_KEYS)
        return
    for item in as_sequence(node):
        _walk(item, entities, services, service_value=service_value)


def classify_references(document: object) -> tuple[frozenset[str], frozenset[str]]:
    """Every reference in a document, split into entities and service calls.

    Public because task 3.5's clause is stated over the two outcomes separately:
    `service: light.turn_on` yields a `service_call` and enters *no* entity set,
    and a templated service call does not either. A function that returned one
    merged set could not be asked that question.
    """
    entities: set[str] = set()
    services: set[str] = set()
    _walk(document, entities, services)
    return frozenset(entities), frozenset(services)


def entity_identifier_set(document: object) -> frozenset[str]:
    """The entity references only. What the provenance gate in task 4.6 reads."""
    return classify_references(document)[0]


def _term(value: object) -> str | None:
    text = as_text(value)
    if text is not None and TERM_RE.match(text):
        return text
    return None


def _trigger_term(item: object) -> str | None:
    block = as_mapping(item)
    for key in TRIGGER_TERM_KEYS:
        term = _term(block.get(key))
        if term is not None:
            return term
    return None


def _condition_term(item: object) -> str | None:
    return _term(as_mapping(item).get("condition"))


def _action_term(item: object) -> str | None:
    block = as_mapping(item)
    for key, term in ACTION_TERMS:
        if key in block:
            return term
    return None


def _collect_terms(
    node: object,
    triggers: set[str],
    conditions: set[str],
    actions: set[str],
) -> None:
    """Every block kind the document uses, walking the whole tree.

    A block is recognised by its introductory key *and* by that key's value being
    a list, because in this dialect the same key is also the discriminator inside
    a block -- `trigger: state` sits inside the list under `trigger:` -- and
    reading the discriminator as a block would look for a kind where the kind is a
    binding. Recursion continues through every value so a `choose` inside an
    action still contributes its condition kinds, and a `sequence` inside a script
    still contributes its action kinds.
    """
    for key, value in as_mapping(node).items():
        items = as_sequence(value)
        if not items:
            continue
        if key in TRIGGER_KEYS:
            triggers |= {term for term in map(_trigger_term, items) if term}
        elif key in CONDITION_KEYS:
            conditions |= {term for term in map(_condition_term, items) if term}
        elif key in ACTION_KEYS:
            actions |= {term for term in map(_action_term, items) if term}
    for value in as_mapping(node).values():
        _collect_terms(value, triggers, conditions, actions)
    for item in as_sequence(node):
        _collect_terms(item, triggers, conditions, actions)


def vocabulary(
    document: object,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The trigger, condition and action terms a document uses, each sorted."""
    triggers: set[str] = set()
    conditions: set[str] = set()
    actions: set[str] = set()
    _collect_terms(document, triggers, conditions, actions)
    return tuple(sorted(triggers)), tuple(sorted(conditions)), tuple(sorted(actions))


def _display_text(node: object, aliases: set[str], names: set[str]) -> None:
    """Every display name the document carries, split by the two kinds.

    `alias` and `name` are separated rather than pooled because the verbatim store
    is read by a prose gate that has to name what it matched, and a reader of a
    finding wants to know whether a phrase was an automation's alias or a
    helper's name.
    """
    for key, value in as_mapping(node).items():
        text = as_text(value)
        if text:
            if key == "alias":
                aliases.add(text)
            elif key == "name":
                names.add(text)
    for value in as_mapping(node).values():
        _display_text(value, aliases, names)
    for item in as_sequence(node):
        _display_text(item, aliases, names)


def _comments(text: str) -> tuple[str, ...]:
    """The full-line comments a file carries, de-duplicated and sorted.

    Full-line only. An inline `#` is a YAML value in some of these files -- an
    unquoted string with a hash in it is a parse error, but a templated one is not
    -- and a rule that split on the character would eventually cut a template in
    half and record the tail as prose.
    """
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            if body:
                found.add(body)
    return tuple(sorted(found))


def extract(repo: str, path: str, artifact_class: str, text: str | None) -> Extraction:
    """Normalise one selected file, parsed or not.

    `text` is None when the file is not a YAML document (a dashboard's resources,
    a custom integration's `manifest.json`) or could not be read; the record is
    still emitted, because the record exists per *selected file* and a file with no
    extractable structure is a record with empty terms, not a missing record. A
    file that will not parse keeps its comments -- they are read from the text and
    not from the document -- and gets no terms, which is the honest outcome: a
    document the loader refused has no block kinds anyone can name.
    """
    document: object = None
    if text is not None:
        parsed, error = ha_yaml.parse(text)
        if error is None:
            document = parsed

    triggers: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    entities: frozenset[str] = frozenset()
    services: frozenset[str] = frozenset()
    aliases: set[str] = set()
    names: set[str] = set()
    if document is not None:
        triggers, conditions, actions = vocabulary(document)
        entities, services = classify_references(document)
        _display_text(document, aliases, names)

    return Extraction(
        repo=repo,
        path=path,
        artifact_class=artifact_class,
        record_id=slug(f"{repo} {path}"),
        triggers=triggers,
        conditions=conditions,
        actions=actions,
        entity_refs=tuple(sorted(entities)),
        service_calls=tuple(sorted(services)),
        aliases=tuple(sorted(aliases)),
        names=tuple(sorted(names)),
        comments=_comments(text) if text is not None else (),
    )


def _naming_convention(repo: str) -> str:
    label = NAMING_CONVENTIONS.get(repo)
    if label is None:
        raise CheckError(
            NORMALISE_CHECK,
            repo,
            "has no naming-convention label; every hardcoded-refs entry carries "
            "its repo's label so the same concept can be compared across repos, "
            "and a repo the label set does not name is one this pass cannot "
            "describe",
        )
    return label


def _scope(entity_ref: str) -> str:
    domain = entity_ref.split(".", 1)[0]
    return "house" if domain in HOUSE_DOMAINS else "room"


def _hardcoded_entry(repo: str, entity_ref: str) -> dict[str, object]:
    """One entry of the hardcoding audit, from the reference and its repo.

    Two fields carry the resolution and only one of them is ever set: `slot`
    where the role lexicon can read a slot out of the reference's domain and
    name, `constant` where the domain is one of the fixed things -- `sun`,
    `person`, `device_tracker`. Both are facts the extraction can derive; an
    entry the lexicon cannot place is dropped in `build` rather than committed
    with both null, because the requirement is that every committed entry maps
    to one or the other and the full reference set already lives on each raw
    record's `entity_refs`. The first lexicon candidate is taken where a
    reference has several -- `binary_sensor.front_door_leak` is evidence for
    both a contact and a leak sensor -- and the choice is the lexicon's order
    rather than a precedence invented here.
    """
    domain, _, object_id = entity_ref.partition(".")
    candidates = lexicon.candidates(domain, object_id)
    return {
        "entity_ref": entity_ref,
        "repo": repo,
        "scope": _scope(entity_ref),
        "naming_convention": _naming_convention(repo),
        "slot": candidates[0] if candidates else None,
        "constant": CONSTANT_DOMAINS.get(domain),
    }


# --------------------------------------------------------------------------
# Building the stores -- local only
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectedFile:
    """One selected file the inventory decided, with the class it conferred."""

    repo: str
    path: str
    artifact_class: str


def _selected_files() -> list[SelectedFile]:
    """Every selected file in the committed inventory, with its repo.

    Read from `catalog/inventory.json` rather than re-walked from the clones,
    because the class a record carries is the class its deciding rule conferred
    and the rule list has already been applied there. Re-deciding would be a second
    application of one rule list, and the two could disagree without either being
    wrong about the thing it looked at.

    Parsed here rather than through `inventory`'s own reader: the inventory owns
    the *shape* of its file and this module only consumes two fields of it, so a
    reader added to `inventory` for this caller would be a second, narrower
    interpretation of a document that already has one.
    """
    document = inventory.load_inventory()
    out: list[SelectedFile] = []
    for entry in as_sequence(document.get("repos")):
        row = as_mapping(entry)
        repo = as_text(row.get("repo")) or ""
        for item in as_sequence(row.get("files")):
            record = as_mapping(item)
            if record.get("selected") is not True:
                continue
            out.append(
                SelectedFile(
                    repo=repo,
                    path=as_text(record.get("path")) or "",
                    artifact_class=as_text(record.get("class")) or "other",
                )
            )
    return out


def _read(repo_dir: object, path: str) -> str | None:
    """A selected file's text, or None when it is not a readable document."""
    from pathlib import Path

    if not isinstance(repo_dir, Path):
        return None
    try:
        return errors.read_text(repo_dir / path)
    except (OSError, UnicodeDecodeError):
        return None


def _disambiguate(extractions: list[Extraction]) -> list[Extraction]:
    """Give every record a unique id, by appending an index where two collide.

    Collisions are possible because the slug drops the difference between `a/b_c`
    and `a_b/c`, and the ids have to be unique: a shipped row cites raw ids, and
    two records behind one id would make a citation ambiguous. The suffix is
    applied in sorted order so the assignment is deterministic, which task 3.8
    requires of every extraction output.
    """
    seen: dict[str, int] = {}
    out: list[Extraction] = []
    for extraction in extractions:
        count = seen.get(extraction.record_id, 0)
        seen[extraction.record_id] = count + 1
        if count == 0:
            out.append(extraction)
            continue
        out.append(
            Extraction(
                **{
                    **{
                        field: getattr(extraction, field)
                        for field in extraction.__slots__  # type: ignore[attr-defined]
                    },
                    "record_id": f"{extraction.record_id}_{count + 1}",
                }
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class Stores:
    """The three artifacts one sweep produces."""

    raw_behaviors: dict[str, object]
    verbatim: dict[str, object]
    hardcoded_refs: dict[str, object]


#: The artifact classes whose file is expected to carry a behaviour. A file of
#: one of these that produced no trigger, condition or action term is
#: `malformed` rather than `non_behavior_file`: the extraction read it and found
#: nothing to call a behaviour, which is a different fact from a dashboard or a
#: helper declaration that was never meant to hold one.
BEHAVIOUR_CLASSES: frozenset[str] = frozenset({"automation", "script", "blueprint"})


def unclaimed_reason(record: Mapping[str, object], claimed: set[str]) -> str | None:
    """Why a raw record is not a shipped behaviour, or None when a row claims it.

    The register is a relation between the two committed stores and not a fact
    about one file, which is why it is derived here from the record and the set
    of ids `catalog/behaviors.yaml` cites rather than written by the extraction.
    Recomputing it is what makes `catalog/raw-behaviors.json` reproducible from
    committed code: the values are a function of the corpus, and an edit to a
    row moves the marker without anyone hand-writing it.
    """
    record_id = as_text(record.get("id")) or ""
    if record_id in claimed:
        return None
    has_terms = any(
        as_sequence(record.get(key)) for key in ("triggers", "conditions", "actions")
    )
    if (as_text(record.get("class")) or "") in BEHAVIOUR_CLASSES and not has_terms:
        return "malformed"
    return "non_behavior_file"


def apply_unclaimed_register(
    records: Sequence[Mapping[str, object]],
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """The records with their `unclaimed` marker filled from the corpus.

    One pass, over both stores, so the committed facts store and the gitignored
    verbatim store carry the same marker and differ only in the withheld text
    the verbatim store adds -- the exact one respect the spec says separates
    them.
    """
    claimed = set(behaviors.claimed_ids(rows))
    out: list[dict[str, object]] = []
    for record in records:
        row = dict(record)
        row["unclaimed"] = unclaimed_reason(record, claimed)
        out.append(row)
    return out


def build() -> Stores:
    """Run the extraction over every selected file in every clone.

    The repo set is the inventory's and the clones' set has to agree with it: a
    repo with no clone cannot be extracted, and a clone nothing recorded is a
    repository whose files would enter the corpus with no class behind them. The
    agreement is asserted rather than assumed for the reason the inventory asserts
    it -- a corpus that is quietly smaller is the failure mode both checks exist to
    prevent.
    """
    clones = inventory.discover_clones()
    recorded = [record.repo for record in licenses.load_licences() if record.repo]
    missing = [repo for repo in recorded if repo not in clones]
    if missing:
        raise CheckError(
            NORMALISE_CHECK,
            "ressources",
            f"no clone for {missing}; the extraction reads every recorded repo, so "
            "a missing one produces a committed store smaller than the licence "
            "records describe",
        )

    extractions = [
        extract(
            record.repo,
            record.path,
            record.artifact_class,
            _read(clones[record.repo], record.path),
        )
        for record in _selected_files()
    ]
    extractions = _disambiguate(extractions)

    rows = behaviors.load_behaviors()
    records = apply_unclaimed_register(
        [extraction.as_record() for extraction in extractions], rows
    )
    verbatim = apply_unclaimed_register(
        [extraction.as_verbatim() for extraction in extractions], rows
    )

    seen_refs: dict[tuple[str, str], dict[str, object]] = {}
    for extraction in extractions:
        for entity_ref in extraction.entity_refs:
            key = (extraction.repo, entity_ref)
            if key in seen_refs:
                continue
            entry = _hardcoded_entry(extraction.repo, entity_ref)
            # An entry the lexicon cannot place and no constant covers is not a
            # hardcoded reference the audit can hold: the requirement is that
            # every committed entry maps to a slot or a constant, and the
            # reference itself is not lost -- it is still on the raw record's
            # `entity_refs`, which is the complete set the audit is drawn from.
            if entry["slot"] is None and entry["constant"] is None:
                continue
            seen_refs[key] = entry
    refs = [seen_refs[key] for key in sorted(seen_refs)]

    return Stores(
        raw_behaviors={"records": records, "change_notice": None},
        verbatim={"records": verbatim, "change_notice": None},
        hardcoded_refs={"refs": refs},
    )


def render_json(document: Mapping[str, object]) -> str:
    """A store as the bytes that get committed.

    Sorted keys so the output depends on the data and not on the order a dict
    happened to be built in, and a trailing newline like every other file in the
    project. `ensure_ascii=False` so a path or a name in the verbatim store is the
    text it was, byte for byte, which the prose gate's comparison depends on.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_hardcoded_refs(document: Mapping[str, object]) -> str:
    """The hardcoding audit as YAML, with the header a reader needs.

    Written with an explicit header rather than a bare dump, because the file is a
    committed deliverable a person reads and the header is where the licence
    position is stated -- facts only, no alias, display name, comment or YAML
    fragment from a source. The entries themselves are dumped from the allowlisted
    field set and nothing else.
    """
    header = (
        "# The hardcoding audit: every distinct entity reference the extraction found.\n"
        "#\n"
        "# Generated from the clone-reading pass (task 3.6). Facts only, for all\n"
        "# four repos: a `domain.object_id`, a source repo, an inferred scope and a\n"
        "# naming-convention label. No entry carries an alias, a display name, a\n"
        "# comment or a YAML fragment from its source, and the entry field set is\n"
        "# closed by `schemas/catalog/hardcoded_refs.json`, which is generated from\n"
        "# `schemas/catalog/fact-fields.yaml` so that a field able to hold authored\n"
        "# text cannot be added informally. Every entry carries a `slot` from the\n"
        "# controlled vocabulary of `catalog/slots.yaml` or a `constant` from the\n"
        "# spec's closed set; an entry the lexicon places nowhere is not committed,\n"
        "# because the reference itself is still on its raw record's `entity_refs`.\n"
    )
    body = yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
    return header + body


def write_stores() -> Stores:
    """Build the stores and write them to their three locations.

    The verbatim store goes under `.local/`, which is outside `catalog/` and
    gitignored: every data file under `catalog/` must validate against a schema,
    and this is the one store the corpus validator is required to reject.
    """
    stores = build()
    paths.CATALOG.mkdir(parents=True, exist_ok=True)
    paths.LOCAL.mkdir(parents=True, exist_ok=True)
    (paths.CATALOG / RAW_BEHAVIORS_FILE).write_text(
        render_json(stores.raw_behaviors), encoding="utf-8", newline="\n"
    )
    (paths.LOCAL / VERBATIM_FILE).write_text(
        render_json(stores.verbatim), encoding="utf-8", newline="\n"
    )
    (paths.CATALOG / HARDCODED_REFS_FILE).write_text(
        render_hardcoded_refs(stores.hardcoded_refs), encoding="utf-8", newline="\n"
    )
    return stores


# --------------------------------------------------------------------------
# Reading the committed audit store -- runs in CI
# --------------------------------------------------------------------------


def _load_hardcoded_refs() -> list[dict[str, object]]:
    """The committed audit entries, or `CheckError` if the file cannot be read.

    Raising rather than returning `[]`, for the reason `licenses.load_licences`
    gives: a corrupt file read as an empty one reports nothing, which is the
    failure this whole section exists to prevent arriving through its own guard.
    """
    path = paths.CATALOG / HARDCODED_REFS_FILE
    relative = f"catalog/{HARDCODED_REFS_FILE}"
    if not path.is_file():
        raise CheckError(HARDCODED_REFS_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(
            HARDCODED_REFS_CHECK, relative, f"cannot be read: {exc}"
        ) from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(
            HARDCODED_REFS_CHECK, relative, f"cannot be parsed: {exc}"
        ) from exc
    document = as_mapping(loaded)
    return [
        row for row in (as_mapping(e) for e in as_sequence(document.get("refs"))) if row
    ]


def _raw_entity_union() -> set[str]:
    """Every entity reference the committed record store carries, pooled.

    The audit and the record store are two views of one extraction, so an entry
    whose `entity_ref` is absent from this union is a reference the classifier
    never called an entity -- which is exactly what a service call written into
    the audit reads as. A service name is spelled `domain.object_id` the same way
    an entity is, so the pattern cannot tell them apart; the pool can, because a
    service call is recorded in no record's `entity_refs`.
    """
    path = paths.CATALOG / RAW_BEHAVIORS_FILE
    relative = f"catalog/{RAW_BEHAVIORS_FILE}"
    if not path.is_file():
        raise CheckError(
            HARDCODED_REFS_CHECK,
            relative,
            "does not exist; the audit's provenance is resolved against the "
            "record store, so an entry cannot be checked without it",
        )
    try:
        document = as_mapping(json.loads(errors.read_text(path)))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckError(
            HARDCODED_REFS_CHECK, relative, f"cannot be read as JSON: {exc}"
        ) from exc
    union: set[str] = set()
    for record in as_sequence(document.get("records")):
        for text in (
            as_text(item) for item in as_sequence(as_mapping(record).get("entity_refs"))
        ):
            if text:
                union.add(text)
    return union


def check_hardcoded_refs(report: Report) -> None:
    """The hardcoding audit against the record store it was drawn from.

    Every clause here is a fact about the entry that the schema cannot state,
    because stating it needs a second file: that the reference is one the
    extraction *classified as an entity* and not a service call, that its scope
    is the one its domain implies, and that its repo's naming-convention label is
    the label that repo actually carries. The schema closes the entry field set;
    this closes the entry's meaning.

    The one clause that is *not* here is the one task 3.6 defers to task 5.3:
    that every entry resolves to a slot or a justified constant. It needs
    `catalog/slots.yaml` populated, so it lives in `slots.check_slots`, which
    names the reference and its source repo; the generated entry schema carries
    the same requirement structurally as an entry-level `oneOf`.
    """
    entries = _load_hardcoded_refs()
    union = _raw_entity_union()
    allowed = set(facts.hardcoded_fields())

    for entry in entries:
        ref = as_text(entry.get("entity_ref")) or "<unnamed>"
        repo = as_text(entry.get("repo")) or "<unnamed>"
        where = f"catalog/{HARDCODED_REFS_FILE}:{ref}@{repo}"

        for key in sorted(set(entry) - allowed):
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                f"carries `{key}`, which is not one of the audit entry's fields "
                f"{sorted(allowed)}; the entry field set is closed, so a field "
                "able to hold an alias, a display name, a comment or a YAML "
                "fragment from a source cannot be added informally",
            )
        for key in sorted(allowed - set(entry)):
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                f"omits `{key}`; every audit entry carries the closed field set "
                f"{sorted(allowed)}",
            )

        if not ENTITY_REF_RE.match(ref):
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                "is not a `domain.object_id`; an audit entry names an entity "
                "reference as Home Assistant spells one",
            )
        elif ref not in union:
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                "is not an entity reference the extraction recorded in any repo; "
                "a service call is spelled the same way and is the reference this "
                "clause exists to keep out of the audit",
            )

        label = NAMING_CONVENTIONS.get(repo)
        if label is None:
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                f"names repo {repo!r}, which has no naming-convention label; a "
                "repo the label set does not name is one the audit cannot "
                "describe, so the entry is checked against nothing",
            )
        elif as_text(entry.get("naming_convention")) != label:
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                f"carries naming_convention "
                f"{entry.get('naming_convention')!r}, but `{repo}` is labelled "
                f"{label!r}; the label is what makes one repo's identifiers "
                "comparable with another's",
            )

        scope = as_text(entry.get("scope"))
        if scope not in ("room", "house"):
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                f"carries scope {scope!r}, which is neither `room` nor `house`",
            )
        elif ENTITY_REF_RE.match(ref) and scope != _scope(ref):
            report.add(
                HARDCODED_REFS_CHECK,
                where,
                f"carries scope {scope!r}, but its domain infers {_scope(ref)!r}; "
                "the scope is read off the domain so the same concept compares "
                "across repos, and a hand-written scope that disagrees is a "
                "second opinion where one is derived",
            )
