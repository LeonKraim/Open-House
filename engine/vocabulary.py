"""The frozen Phase 0 artifacts, projected to the facts the engine's rules read.

`design.md` D7: the engine binds to the frozen schemas and the corpus rather than
declaring a Phase 1 vocabulary of its own -- a phase-local slot list would be the
second definition of a concept `catalog/slots.yaml` exists to define once, and
the corpus's `required_slots` values are drawn from that file. This module is the
one place that reads those artifacts, so `binding.py` resolves a name against
data and `modes.py` can read the mode definitions from the same gateway when it
needs them (nothing else in the engine opens a catalog file).

It is a module of its own rather than a corner of `binding.py` because the
gateway serves more than one concern: binding reads slots and the house scope
today, modes read `schemas/mode/` in the same phase, the sandbox reads
`catalog/pack-policy.yaml` and every pack reader needs the engine's declared API
version, and future capabilities read the same set. `engine-core`'s
decomposition names the concerns -- binding, config, modes, arbitration,
overrides, rate limits, the log, the veto, the engine -- and the frozen
artifacts are the input all of them share rather than any one of them's.

The projection keeps only the facts a rule reads. `accepts_domains` is
deliberately absent: no requirement in this phase makes the engine check a
binding's domain against the slot it is bound to, and a definition carrying a
field nothing consults would invite exactly that belief. The known domain list
and the catalog's `examples`/`source_repos` are `oh-catalog validate`'s business,
which is where they are checked; re-validating them here would be a second
definition of the same judgement.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import cast

import yaml
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012, Schema

from tools.catalog.schemas import current_version, load_versions

#: Where the artifacts live, relative to the repository root. The root is passed
#: in rather than derived here so the engine holds no opinion about the checkout
#: it runs from, and a test can point a vocabulary at a fixture tree.
_SLOTS_PATH = ("catalog", "slots.yaml")
_ROOM_TYPES_PATH = ("catalog", "room_types.yaml")
_PACK_POLICY_PATH = ("catalog", "pack-policy.yaml")

#: The service-to-state table: for a `domain.service` a manifest may declare, the
#: state the engine's port writes on the entity it acts through. Read separately
#: from `Vocabulary` rather than as a seventh field of it, for the reason
#: `BehaviourVocabulary` gives below: `Vocabulary` is what a *resolution* reads,
#: and this answers a Phase 2 question -- what a declared behaviour's command
#: means -- that no Phase 1 rule asks.
_SERVICE_STATES_PATH = ("catalog", "services.yaml")
_HOUSE_SCHEMA_PATH = ("schemas", "house", "1.0.0.json")
_MODE_SCHEMA_PATH = ("schemas", "mode", "1.0.0.json")

#: The concept whose published versions *are* the engine's API versions, and the
#: directory they live in. The version is not spelled here: a constant in this
#: module would be a second publication of a promise the artifact already makes,
#: and the two would drift the first time one was bumped.
_ENGINE_API_CONCEPT = "engine-api"
_ENGINE_API_DIR = ("schemas", _ENGINE_API_CONCEPT)

#: The concept whose current version publishes the behaviour terms -- the
#: triggers, conditions and actions a pack may name. Read the same way the engine
#: API version is, and for the same reason: the sandbox has to tell a term the
#: vocabulary does not publish from one it publishes and forbids, and it can only
#: do that against the publication rather than against a list in engine code.
_BEHAVIOUR_VOCABULARY_CONCEPT = "behavior-vocabulary"
_BEHAVIOUR_VOCABULARY_DIR = ("schemas", _BEHAVIOUR_VOCABULARY_CONCEPT)
_BEHAVIOUR_VOCABULARY_FIELDS = ("triggers", "conditions", "actions")

#: The concept whose current version is the schema a pack manifest is validated
#: against, read by the same succession rule as the other two.
_MANIFEST_CONCEPT = "pack-manifest"
_MANIFEST_DIR = ("schemas", _MANIFEST_CONCEPT)

#: The artifact that publishes the licence codes, their order and their SPDX
#: identifiers. It is a *schema* rather than a data file -- `catalog/licenses.yaml`
#: is the records -- because the codes are a vocabulary the manifest schema's own
#: `enum` restates, and a vocabulary is published where a reader looks for one.
_LICENCES_PATH = ("schemas", "catalog", "licenses.json")

#: The corpus, which is the only authority on whether a row's expression may be
#: reproduced and under what licence. A manifest's `derives_from` is checked
#: against this file and never against the manifest's own claim about it.
_CORPUS_PATH = ("catalog", "behaviors.yaml")
_CORPUS_ROWS = "behaviors"

#: The hand-written marker. It is a listing rather than an artifact -- no schema
#: describes it -- and it is read here so that `engine/manifest.py` opens nothing
#: itself, the same property `engine/sandbox.py` is held to.
_HANDWRITTEN_PATH = ("packs", "official", "HANDWRITTEN")
_HANDWRITTEN_DIR = ("packs", "official")

#: Every runtime schema is published under this prefix, which is what makes the
#: manifest schema's cross-file `$ref` resolve to a file in this repository rather
#: than to the network. Repeated from `tools/catalog/examples.py` rather than
#: imported: it is the format of the `$id` field *inside* the schemas, which both
#: modules are reading rather than owning.
_SCHEMA_URI_PREFIX = "https://open-house.invalid/schemas/"


class VocabularyError(Exception):
    """Base for the failures this module defines."""


class MissingArtifactError(VocabularyError):
    """An artifact the engine reads is not where it should be."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"the frozen artifact {path.as_posix()} is missing")
        self.path = path


class MalformedArtifactError(VocabularyError):
    """An artifact exists but does not carry the field the engine reads.

    The failure names the file and the field rather than a bare `KeyError`,
    because a catalog that has been reshaped is a fact about the checkout and not
    a mystery inside a resolution.
    """

    def __init__(self, path: Path, field: str) -> None:
        super().__init__(f"{path.as_posix()} carries no usable {field}")
        self.path = path
        self.field = field


@dataclass(frozen=True, slots=True)
class SlotDefinition:
    """One slot of the frozen vocabulary, as the engine's rules see it."""

    #: True when a room of a type providing this slot cannot be set up without
    #: it. This is the flag that decides whether an unbound slot skips a
    #: behaviour or merely degrades it (`engine-core`).
    required: bool


@dataclass(frozen=True, slots=True)
class PackPolicy:
    """`catalog/pack-policy.yaml`, projected to the facts a check reads.

    Four sections, and each is the artifact's to change: banning a service or
    flagging one is an edit to that file and not a release of the engine, which
    is the property that makes the list a policy rather than an interpolation of
    code. Nothing here is a preference -- every entry the file carries states why
    it is there -- but the reasons are deliberately absent from this projection,
    for the same reason `accepts_domains` is absent from `SlotDefinition`: a
    reason is read by a person deciding whether to shorten the list, and no rule
    consults one, so handing it out would advertise a field nothing reads.

    `banned_services` keeps the entries as published rather than resolving them
    to a set of domains, because `domain.service` and `domain.*` are the two
    forms the artifact allows and `bans` is that fact applied to a call. The
    order the file publishes is kept here, so a diagnostic listing bans lists
    them as a reader of the file would find them.
    """

    forbidden_actions: frozenset[str]
    forbidden_conditions: frozenset[str]
    forbidden_triggers: frozenset[str]
    banned_services: tuple[str, ...]
    flagged_services: frozenset[str]
    default_priority: int

    def bans(self, service: str) -> bool:
        """Whether `service` is on the banned list, in either published form.

        The banned list is the only place a ban can come from, so this answers a
        question about the artifact rather than about the engine: a service is
        bannable by editing `catalog/pack-policy.yaml` and by no other act.
        """
        domain = service.partition(".")[0]
        return service in self.banned_services or f"{domain}.*" in self.banned_services


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """The slot vocabulary, the house scope's slots, and the frozen schemas.

    Construct it through `load`; the six fields are what a resolution needs and
    nothing more. `house_slots` is `catalog/room_types.yaml`'s `house.slots` --
    the slots a house makes available at house scope -- and a house document
    naming a house-scope slot outside it fails resolution, because that list is
    the vocabulary's statement of what a house scope may hold.

    `mode_schema` is the mode artifact and the only one there is: there is no
    `catalog/modes.yaml`, so a mode is not read from a catalog but supplied -- by
    a fixture house today, by a pack in Phase 2 -- and validated against
    `schemas/mode/1.0.0.json`, which is `engine/modes.py`'s input. Both schemas
    are handed out as the loaded documents rather than as dataclasses, because
    the engine's use of each is "validate a document against it" and a projection
    would be a second definition of the schema's own shape.

    `engine_api_version` is the current version of the `engine-api` concept --
    the version a manifest's `engine_api` range is checked against -- and it is
    read from the published artifact rather than declared here, so the promise a
    pack is written against has one publication. The packaging version is a
    different number and deliberately not this one.
    """

    slots: Mapping[str, SlotDefinition]
    house_slots: frozenset[str]
    house_schema: Mapping[str, object]
    mode_schema: Mapping[str, object]
    engine_api_version: str
    pack_policy: PackPolicy

    @classmethod
    def load(cls, root: Path) -> Vocabulary:
        """Read the artifacts under `root`, failing by naming what is wrong.

        Each artifact is read where its field is named, so the order the fields
        are declared in *is* the order the artifacts are read: a tree missing
        more than one of them reports the first the class names, and a reader
        who has the field list has the failure order. Hoisting a read above the
        constructor would make the file that happens to be read first decide
        which of a tree's faults is reported, which is a fact about this method
        rather than about the tree.

        **The read is cached on the root's stamp, and the stamp is why.** The
        vocabulary is frozen and `hashable`-in-spirit, and the tree it is read
        from cannot change while Home Assistant runs -- the repository is
        bind-mounted read-only and nothing writes `schemas/` at runtime -- so a
        rebuild that read the six artifacts again would be six `read_text` calls
        on the event loop. A rebuild is not rare: it happens on every binding
        write, every slot-rule write, every module build and every room edit
        (`ha_adapter.live.LiveSession.rebuild` through
        `ha_adapter.composition.build_live_house`), each one a websocket command
        on that loop, where Home Assistant reports a blocking read and asks a
        person to file a bug about it. `_loaded` remembers the projection against
        `_artifacts_stamp(root)`, which is what makes the cache safe rather than
        merely fast: the key carries each artifact's identity (modification time
        and size, `_stamp`) as well as the root, so a checkout whose artifacts
        *do* change on disk -- a developer's, mid-edit -- is re-read while one
        nobody touched is not. A bare `lru_cache` on the path would serve that
        checkout stale forever, which is the reason this is a stamp and not the
        path alone (`ha_adapter.live_modules._stamp` is the same discipline and
        the same rationale). A failed read is not cached: `lru_cache` stores no
        exception, so a tree that is missing an artifact today and complete
        tomorrow is read again.
        """
        return _loaded(root, _artifacts_stamp(root))


#: How many distinct (root, stamp) vocabularies a process remembers. A checkout
#: has one root and a test suite has a few; the bound is only so a process that
#: walked many fixture trees cannot grow without limit, mirroring
#: `ha_adapter.live_modules._CACHE`.
_CACHE = 256


@lru_cache(maxsize=_CACHE)
def _loaded(root: Path, stamp: tuple[tuple[str, int, int], ...]) -> Vocabulary:
    """The projection at `stamp`, already known to be the tree's current stamp.

    The key is `(root, stamp)` rather than the root alone for the reason
    `Vocabulary.load` gives: a stamp carries the artifacts' identity, so a tree
    that changes on disk is re-read. `stamp` is not read here at all -- it exists
    only to be part of the cache key -- which is the property that separates this
    from a check: it is the *reader* that must run at most once per stamp, not a
    validator that must agree with one.
    """
    return Vocabulary(
        slots=_slots(root, _load_yaml(root, _SLOTS_PATH)),
        house_slots=_house_slots(root, _load_yaml(root, _ROOM_TYPES_PATH)),
        house_schema=_schema(root, _HOUSE_SCHEMA_PATH),
        mode_schema=_schema(root, _MODE_SCHEMA_PATH),
        engine_api_version=_engine_api_version(root),
        pack_policy=_pack_policy(root, _load_yaml(root, _PACK_POLICY_PATH)),
    )


def _stamp(path: Path) -> tuple[int, int]:
    """A path's modification time and size, or zeros when it cannot be read.

    A metadata read and not a content read, which is why this is a stamp and not
    a hash: Home Assistant's loop detector reports `read_text` and does not
    report `stat`, so a stamp lets a rebuild decide "the same tree" without the
    blocking call the cache exists to avoid (`ha_adapter.live_modules._stamp`
    states the same, for the same readers).
    """
    try:
        info = path.stat()
    except OSError:
        return (0, 0)
    return (info.st_mtime_ns, info.st_size)


def _artifacts_stamp(root: Path) -> tuple[tuple[str, int, int], ...]:
    """A stamp over every artifact a `Vocabulary.load` reads, under `root`.

    The five fixed files, plus the `engine-api` directory and each version file
    in it: the API version is the *current* one of a concept, so adding,
    removing or editing a version file changes which version a load reaches, and
    a stamp that named only the directory would miss an edit to a file already
    in it (`load_versions` reads each version's `supersedes` to answer
    current-ness, so its *content* is part of the vocabulary). The order is
    fixed -- files, then the directory, then its versions sorted -- so two stamps
    of one tree compare equal and one stamp of two trees does not.
    """
    stamped: list[tuple[str, int, int]] = []
    for parts in (
        _SLOTS_PATH,
        _ROOM_TYPES_PATH,
        _PACK_POLICY_PATH,
        _HOUSE_SCHEMA_PATH,
        _MODE_SCHEMA_PATH,
    ):
        stamped.append((Path(*parts).as_posix(), *_stamp(root.joinpath(*parts))))
    directory = root.joinpath(*_ENGINE_API_DIR)
    stamped.append((Path(*_ENGINE_API_DIR).as_posix(), *_stamp(directory)))
    try:
        versions = sorted(directory.glob("*.json"))
    except OSError:  # pragma: no cover - an unreadable directory is a stamp miss
        versions = []
    for version in versions:
        stamped.append((version.relative_to(root).as_posix(), *_stamp(version)))
    return tuple(stamped)


@dataclass(frozen=True, slots=True)
class BehaviourVocabulary:
    """The behaviour terms one published `behavior-vocabulary` version carries.

    Three sets and not one, because the three axes are three closed lists: a
    term published as an action is not published as a trigger, and a check that
    pooled them would admit `repeat` as a trigger.

    This is deliberately *not* a field of `Vocabulary`. That class is what a
    resolution reads -- slots, the house scope, the schemas, the API version and
    the pack policy -- and every one of its fields answers a question a Phase 1
    rule asks. The behaviour terms answer a Phase 2 question only, and a field
    nothing in Phase 1 consults would advertise exactly the belief `accepts_domains`
    is left out to avoid. It is a second class in the same module rather than a
    second reader of the artifact, which is the property that matters: the
    artifact still has one opener in the engine.
    """

    triggers: frozenset[str]
    conditions: frozenset[str]
    actions: frozenset[str]

    def publishes(self, axis: str, term: str) -> bool:
        """Whether the vocabulary publishes `term` on one of the three axes."""
        return term in getattr(self, axis)


def load_behaviour_vocabulary(root: Path) -> BehaviourVocabulary:
    """The current `behavior-vocabulary` version's terms, under `root`.

    The version is the current one by the same succession rule the catalog asks,
    so a phase that publishes `1.2.0` moves the engine's reading without an edit
    here. An absent directory and a forked one are the same two failures the
    engine API version reports, and for the same reasons.
    """
    directory = root.joinpath(*_BEHAVIOUR_VOCABULARY_DIR)
    versions = load_versions(_BEHAVIOUR_VOCABULARY_CONCEPT, root=root)
    if not versions:
        raise MissingArtifactError(directory)
    current = current_version(versions)
    if current is None:
        raise MalformedArtifactError(directory, "single current version")
    path = current.path
    document = _schema(root, path.relative_to(root).parts)
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        raise MalformedArtifactError(path, "`properties` mapping")
    return BehaviourVocabulary(
        **{
            axis: _published_terms(path, properties, axis)
            for axis in _BEHAVIOUR_VOCABULARY_FIELDS
        }
    )


def load_service_states(root: Path) -> Mapping[str, str]:
    """`catalog/services.yaml`, as a service-to-state table under `root`.

    The engine's port takes a state -- "the light is `on`" -- while a manifest
    declares a service -- "call `light.turn_on`". This is the artifact that knows
    the one from the other, and it is a *mapping* rather than a field of
    `Vocabulary`, because `Vocabulary` is what a resolution reads and this is what
    the pack interpreter reads (`engine/behaviours/declared.py`).

    A service with no row is absent from the table rather than mapped to itself.
    The caller's job is to propose nothing for it: the port cannot perform a
    service that writes no state, and writing the service's own name as a state
    is what this table exists to stop.

    Shape is checked here -- a row must have a string `service` and a non-empty
    string `state` -- and *content* is the schema's business, the same division
    `_pack_policy` draws: `oh-catalog validate` holds the file to
    `schemas/catalog/services.json`, and re-deciding here which service names are
    well formed would be a second definition of that schema.
    """
    path = root.joinpath(*_SERVICE_STATES_PATH)
    document = _load_yaml(root, _SERVICE_STATES_PATH)
    rows = document.get("services")
    if not _is_list(rows):
        raise MalformedArtifactError(path, "`services` list")
    table: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise MalformedArtifactError(path, "service row")
        service = row.get("service")
        state = row.get("state")
        if not isinstance(service, str) or not isinstance(state, str) or not state:
            raise MalformedArtifactError(path, "service row's `service` and `state`")
        if service in table:
            # A duplicate is a fault and not a later-wins: two states for one
            # service is a question this table answers twice, and which answer a
            # reader got would depend on the file's order rather than on a rule.
            raise MalformedArtifactError(path, f"one row for {service!r}")
        table[service] = state
    return table


def _published_terms(
    path: Path, properties: Mapping[str, object], axis: str
) -> frozenset[str]:
    """One axis's published term list, read from the schema's own enum.

    The list is the `items` enum of `properties.<axis>`, which is where the
    vocabulary publishes it and where the manifest schema's `$ref` sends a
    reader. Copying it into a tuple here would be the second list this module
    exists to not have.
    """
    axis_schema = properties.get(axis)
    items = axis_schema.get("items") if isinstance(axis_schema, Mapping) else None
    values = items.get("enum") if isinstance(items, Mapping) else None
    if not _is_list(values):
        raise MalformedArtifactError(path, f"`properties.{axis}.items.enum` list")
    terms = [value for value in values if isinstance(value, str)]
    if len(terms) != len(values):
        raise MalformedArtifactError(path, f"`properties.{axis}.items.enum` names")
    return frozenset(terms)


def _load_yaml(root: Path, parts: tuple[str, ...]) -> Mapping[str, object]:
    path = root.joinpath(*parts)
    if not path.is_file():
        raise MissingArtifactError(path)
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise MalformedArtifactError(path, "mapping")
    return cast("Mapping[str, object]", loaded)


def _slots(root: Path, document: Mapping[str, object]) -> Mapping[str, SlotDefinition]:
    path = root.joinpath(*_SLOTS_PATH)
    rows = document.get("slots")
    if not _is_list(rows):
        raise MalformedArtifactError(path, "`slots` list")
    slots: dict[str, SlotDefinition] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise MalformedArtifactError(path, "slot row")
        name = row.get("name")
        required = row.get("required")
        if not isinstance(name, str) or not isinstance(required, bool):
            raise MalformedArtifactError(path, "slot row's `name` and `required`")
        slots[name] = SlotDefinition(required=required)
    return slots


def _house_slots(root: Path, document: Mapping[str, object]) -> frozenset[str]:
    path = root.joinpath(*_ROOM_TYPES_PATH)
    house = document.get("house")
    if not isinstance(house, Mapping):
        raise MalformedArtifactError(path, "`house` mapping")
    rows = house.get("slots")
    if not _is_list(rows):
        raise MalformedArtifactError(path, "`house.slots` list")
    names = [row for row in rows if isinstance(row, str)]
    if len(names) != len(rows):
        raise MalformedArtifactError(path, "`house.slots` names")
    return frozenset(names)


def _is_list(value: object) -> bool:
    """Whether a YAML value is a list.

    A `Sequence` test alone would admit a string, and a string is iterable: the
    loader would then read a slot list of one character per letter and report a
    malformed *row* rather than a malformed list, naming the wrong field.
    """
    return isinstance(value, Sequence) and not isinstance(value, str)


def _schema(root: Path, parts: tuple[str, ...]) -> Mapping[str, object]:
    """One frozen JSON schema, loaded and checked to be a mapping.

    A schema is read here and handed on unexamined beyond that: the engine's use
    of it is to validate a document, and a loader that inspected it would be
    restating what the validator already decides.
    """
    path = root.joinpath(*parts)
    if not path.is_file():
        raise MissingArtifactError(path)
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise MalformedArtifactError(path, "mapping")
    return cast("Mapping[str, object]", loaded)


def _engine_api_version(root: Path) -> str:
    """The current `engine-api` version published under `root`.

    The succession rule is `tools.catalog.schemas`' and is not restated here: a
    version is current when no other version present names it, and that question
    is asked by the same code the catalog checks ask it with. A concept with no
    versions has no current one and a tree with two heads has no single one, so
    the two failures are reported separately -- an absent directory is a
    checkout's fault and a fork is an artifact's.
    """
    directory = root.joinpath(*_ENGINE_API_DIR)
    versions = load_versions(_ENGINE_API_CONCEPT, root=root)
    if not versions:
        raise MissingArtifactError(directory)
    current = current_version(versions)
    if current is None:
        raise MalformedArtifactError(directory, "single current version")
    return current.version


def _pack_policy(root: Path, document: Mapping[str, object]) -> PackPolicy:
    """The policy file, projected by reading the fields a rule reads.

    Each field is checked for the shape the projection needs rather than taken on
    trust, so a reshaped policy names the file and the field instead of failing
    somewhere inside a check with a `TypeError`. What the *entries* are is the
    schema's business: `oh-catalog validate` holds every data file under
    `catalog/` to `schemas/catalog/pack-policy.json`, and re-deciding here which
    service names are well formed would be a second definition of that schema.
    """
    path = root.joinpath(*_PACK_POLICY_PATH)
    subset = document.get("declarative_subset")
    if not isinstance(subset, Mapping):
        raise MalformedArtifactError(path, "`declarative_subset` mapping")
    priority = document.get("default_priority")
    if not isinstance(priority, int) or isinstance(priority, bool):
        # `bool` is an `int` to `isinstance`, so a `true` written where a rank
        # belongs would otherwise be projected as the rank 1 in silence.
        raise MalformedArtifactError(path, "`default_priority` integer")
    return PackPolicy(
        forbidden_actions=_terms(path, subset, "forbidden_actions"),
        forbidden_conditions=_terms(path, subset, "forbidden_conditions"),
        forbidden_triggers=_terms(path, subset, "forbidden_triggers"),
        banned_services=_services(path, document, "banned_services"),
        flagged_services=frozenset(_services(path, document, "flagged_services")),
        default_priority=priority,
    )


def _terms(path: Path, subset: Mapping[str, object], field: str) -> frozenset[str]:
    """One list of forbidden vocabulary terms, checked to be a list of names."""
    rows = subset.get(field)
    if not _is_list(rows):
        raise MalformedArtifactError(path, f"`declarative_subset.{field}` list")
    names = [row for row in rows if isinstance(row, str)]
    if len(names) != len(rows):
        raise MalformedArtifactError(path, f"`declarative_subset.{field}` names")
    return frozenset(names)


def _services(
    path: Path, document: Mapping[str, object], field: str
) -> tuple[str, ...]:
    """One service section's names, in the order the file publishes them.

    An entry is a mapping carrying its reason rather than a bare service name,
    and the reason is left behind for the same reason the projection leaves them
    all behind.
    """
    rows = document.get(field)
    if not _is_list(rows):
        raise MalformedArtifactError(path, f"`{field}` list")
    out: list[str] = []
    for row in rows:
        service = row.get("service") if isinstance(row, Mapping) else None
        if not isinstance(service, str):
            raise MalformedArtifactError(path, f"`{field}` row's `service`")
        out.append(service)
    return tuple(out)


# --------------------------------------------------------------------------
# What a pack manifest is validated against. Four artifacts rather than one,
# because a manifest is judged by four different authorities -- the schema says
# what a document may be, the licence vocabulary says what a code means, the
# corpus says what a row grants, and the marker says which files a person wrote
# -- and a single projection of them would have to invent a fifth.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestSchema:
    """The current `pack-manifest` version, as the validator's first authority.

    The version travels with the document because a failure has to name it: a
    `1.1.0`-valid pack is refused by `1.2.0`, and a message that named only the
    clause would leave the author guessing which version refused them.
    """

    version: str
    document: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LicenceVocabulary:
    """`schemas/catalog/licenses.json`'s codes, their order and their SPDX names.

    `codes` is the published order, least restrictive first, and it is the order
    the derivation gate compares in -- so the comparison is a fact about the
    artifact and not a ranking written into the engine. `spdx` maps each code to
    its identifier, which is what a consumer reports and what makes
    `spec.txt:56`'s "SPDX licence" true without a second list of identifiers.

    Both come from one file, which is the point: a code added to the enum without
    an identifier, or an identifier for a code that is not published, fails the
    artifact's own schema rather than drifting here.
    """

    codes: tuple[str, ...]
    spdx: Mapping[str, str]

    def rank(self, code: str) -> int:
        """A published code's position in the order, least restrictive first."""
        return self.codes.index(code)

    def more_restrictive(self, code: str, than: str) -> bool:
        """Whether `code` sits further up the published order than `than`."""
        return self.rank(code) > self.rank(than)


@dataclass(frozen=True, slots=True)
class CorpusRow:
    """One row of `catalog/behaviors.yaml`, as the derivation gate reads it.

    Two fields and not the row: `reuse_status` decides whether the row's
    expression may be reproduced at all, and `license` decides what a pack
    deriving from it may claim. Every other field describes the *behaviour* the
    row records, which is what the row is for and not what this question is
    about.
    """

    id: str
    reuse_status: str
    license: str


@dataclass(frozen=True, slots=True)
class ManifestArtifacts:
    """Everything a manifest is checked against, read under one root.

    `root` is kept because the manifest schema references the behaviour
    vocabulary by absolute URI and resolving that reference needs the tree it
    resolves into; nothing here is read lazily and nothing is read twice.

    `handwritten` holds root-relative paths rather than the bare names the marker
    file carries, because the marker's names are relative to one directory and a
    set of bare names would claim a pack of the same filename anywhere in the
    tree was hand-written.
    """

    root: Path
    schema: ManifestSchema
    licences: LicenceVocabulary
    corpus: Mapping[str, CorpusRow]
    handwritten: frozenset[str]


def load_manifest_artifacts(root: Path) -> ManifestArtifacts:
    """Read the four authorities a manifest is validated against, under `root`."""
    return ManifestArtifacts(
        root=root,
        schema=_manifest_schema(root),
        licences=_licences(root),
        corpus=_corpus(root),
        handwritten=_handwritten(root),
    )


def runtime_registry(root: Path) -> Registry[Schema]:
    """A registry resolving the runtime schemas' published `$id`s to files.

    The manifest schema's three behaviour-term clauses are `$ref`s to
    `behavior-vocabulary`, which is what makes "the vocabulary is the only copy"
    checkable rather than promised: the terms a manifest may name are the ones the
    referenced artifact publishes, and a validator that carried its own list would
    be the second copy the requirement forbids.

    Built per call and not at import time, so it follows whatever root it is
    given; a registry built once would answer for the repository no matter which
    tree a caller was validating in.
    """
    return Registry(retrieve=partial(_retrieve, root))


def _retrieve(root: Path, uri: str) -> Resource[Schema]:
    """Resolve a `$ref` naming another committed runtime schema under `root`.

    A reference outside the schemas tree, a path that escapes it, and a file that
    will not parse are all reported as unresolvable rather than allowed to
    propagate: a `$ref` is written by the schema's author and a failure to
    resolve it is the checkout's, so it must not arrive as a traceback in the
    middle of validating somebody's pack.
    """
    if not uri.startswith(_SCHEMA_URI_PREFIX):
        raise NoSuchResource(ref=uri)
    candidate = root.joinpath("schemas", *uri[len(_SCHEMA_URI_PREFIX) :].split("/"))
    try:
        candidate.resolve().relative_to(root.joinpath("schemas").resolve())
    except (OSError, ValueError) as exc:
        raise NoSuchResource(ref=uri) from exc
    if not candidate.is_file():
        raise NoSuchResource(ref=uri)
    loaded = _read_schema(candidate)
    if loaded is None:
        raise NoSuchResource(ref=uri)
    return Resource(contents=loaded, specification=DRAFT202012)


def _read_schema(path: Path) -> Mapping[str, object] | None:
    """A schema file's document, or `None` if it is not usable as one."""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, Mapping) else None


def _manifest_schema(root: Path) -> ManifestSchema:
    """The current `pack-manifest` version under `root`."""
    version, document = _current_schema(root, _MANIFEST_CONCEPT, _MANIFEST_DIR)
    return ManifestSchema(version=version, document=document)


def _licences(root: Path) -> LicenceVocabulary:
    """The published licence codes, in order, with their SPDX identifiers.

    The two are read from the same file and cross-checked here rather than
    trusted: a code with no identifier would make `spdx_for` raise somewhere
    inside a check, and an identifier for an unpublished code would be a mapping
    entry nothing can look up. The artifact's schema forbids both, but a check
    that reads the file directly cannot rely on the schema check having run.
    """
    path = root.joinpath(*_LICENCES_PATH)
    document = _schema(root, _LICENCES_PATH)
    definitions = document.get("$defs")
    if not isinstance(definitions, Mapping):
        raise MalformedArtifactError(path, "`$defs` mapping")
    values = definitions.get("licenceValue")
    codes = values.get("enum") if isinstance(values, Mapping) else None
    if not _is_list(codes) or not all(isinstance(code, str) for code in codes):
        raise MalformedArtifactError(path, "`$defs.licenceValue.enum` list")
    spdx = _spdx_table(path, definitions, tuple(cast("list[str]", codes)))
    return LicenceVocabulary(codes=tuple(cast("list[str]", codes)), spdx=spdx)


def _spdx_table(
    path: Path, definitions: Mapping[str, object], codes: tuple[str, ...]
) -> Mapping[str, str]:
    """The SPDX identifier each published code carries, checked to be complete."""
    spdx = definitions.get("licenceSpdx")
    properties = spdx.get("properties") if isinstance(spdx, Mapping) else None
    if not isinstance(properties, Mapping):
        raise MalformedArtifactError(path, "`$defs.licenceSpdx.properties` mapping")
    table: dict[str, str] = {}
    for code in codes:
        entry = properties.get(code)
        identifier = entry.get("const") if isinstance(entry, Mapping) else None
        if not isinstance(identifier, str):
            raise MalformedArtifactError(path, f"`$defs.licenceSpdx.properties.{code}`")
        table[code] = identifier
    return table


def _corpus(root: Path) -> Mapping[str, CorpusRow]:
    """Every corpus row, keyed by id, with the two facts the gate reads."""
    path = root.joinpath(*_CORPUS_PATH)
    document = _load_yaml(root, _CORPUS_PATH)
    rows = document.get(_CORPUS_ROWS)
    if not _is_list(rows):
        raise MalformedArtifactError(path, f"`{_CORPUS_ROWS}` list")
    corpus: dict[str, CorpusRow] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise MalformedArtifactError(path, "row")
        identifier = row.get("id")
        status = row.get("reuse_status")
        licence = row.get("license")
        if not isinstance(identifier, str):
            raise MalformedArtifactError(path, "row's `id`")
        if not isinstance(status, str) or not isinstance(licence, str):
            raise MalformedArtifactError(path, "row's `reuse_status` and `license`")
        corpus[identifier] = CorpusRow(
            id=identifier, reuse_status=status, license=licence
        )
    return corpus


def _handwritten(root: Path) -> frozenset[str]:
    """The hand-written marker's names, qualified to root-relative paths.

    Comment lines and blank lines are skipped, which is what makes the marker
    file readable by a person -- the check that reads it here and the check that
    reads it in `tools/catalog/examples.py` therefore agree on the format by
    construction rather than by both spelling a parser.

    An absent marker is not an error. The listing names the official packs that
    were written by hand rather than derived, and a checkout that ships no
    official packs has nothing to list -- an empty allowlist is the true answer
    and the engine composes a session from it, where raising would stop the
    integration from loading over a directory it simply does not have. A marker
    that is present but unreadable is still a failure: the listing exists and
    says something, and not being able to read it is not the same as it saying
    nothing.
    """
    path = root.joinpath(*_HANDWRITTEN_PATH)
    if not path.is_file():
        return frozenset()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MalformedArtifactError(path, "readable text") from exc
    prefix = "/".join(_HANDWRITTEN_DIR)
    return frozenset(
        f"{prefix}/{stripped}"
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )


def _current_schema(
    root: Path, concept: str, directory: tuple[str, ...]
) -> tuple[str, Mapping[str, object]]:
    """The current version of a runtime concept and its document.

    The same two failures the engine API version reports, for the same reasons: a
    concept with no versions has no current one, and a tree with two heads has no
    single one. The version is returned beside the document because a failure has
    to be able to name which version refused it.
    """
    versions = load_versions(concept, root=root)
    if not versions:
        raise MissingArtifactError(root.joinpath(*directory))
    current = current_version(versions)
    if current is None:
        raise MalformedArtifactError(
            root.joinpath(*directory), "single current version"
        )
    return current.version, _schema(root, current.path.relative_to(root).parts)
