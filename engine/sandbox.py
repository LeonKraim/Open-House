"""The pack sandbox: what a pack may do, refused before the engine sees it.

`design.md` D5 states the shape this module implements. A pack is a *description
of intent* the engine carries out, never a program the engine runs, so the
interpreter's whole capability surface is "resolve a slot to a bound entity" and
"call a declared service on it" -- and a pack that validates and installs cannot
exceed its grant *because there is no facility to express the excess*, rather
than because a runtime guard declines it. That is why every check here runs at
validation and why none of them reports an incident: a sandbox failure is a
validation failure whose message names the pack.

**Four rules**, one per requirement of `pack-sandbox`:

1. *The declarative subset* -- a behaviour's trigger, condition and action are
   terms the published vocabulary carries, and none of them is a term
   `catalog/pack-policy.yaml` forbids. A pack expresses intent; it does not
   branch, loop, order or assign.
2. *Entity reach* -- a behaviour reaches an entity only through a slot it
   declares and names in its own `slots` clause, and only for the room the pack
   is installed in. A literal entity id, device id or room name is not a slot and
   is refused.
3. *Declared services* -- the services a manifest's behaviours name are the
   pack's whole permission set, and it is computed from those clauses rather than
   written a second time, so it cannot go stale against them. A call outside it
   is refused; a call inside it that is on the banned list is refused; a call
   inside it that is flagged is recorded and installs.
4. *`provides` containment* -- every entry's path resolves to a file inside the
   pack's own directory and the file is of the class the entry declares.

**One closed set of failure codes**, and the distinctions inside it are the point
rather than a diagnostic nicety. A term the vocabulary does not publish is
`unknown_trigger`, `unknown_condition` or `unknown_action`; a term the vocabulary
publishes and the declarative subset forbids is `forbidden_trigger`,
`forbidden_condition` or `forbidden_action` -- published, and refused for a
different reason, so a pack author who wrote `repeat` is not told the vocabulary
lacks a term it has. Every reason a refusal can carry is in `REASONS`,
and a reason is the finest distinction this module draws. It is deliberately not
the *class* a report carries: `pack-cli`'s `validate` publishes a class list of
its own -- `schema`, `unknown term`, `non-declarative term` and the rest -- and
that list is coarser, its `unknown term` covering the three `unknown_*` reasons
here. The grouping is the face's and the face publishes it; a coarser taxonomy
declared *here* would be a second list living in the module rather than in the
report a reader meets it in.

The three rules that read an artifact read it through `engine/vocabulary.py`,
the one module that opens a frozen artifact, so the sandbox has no second
definition of anything the project publishes. Banning a service, flagging one, or
forbidding a term is an edit to `catalog/pack-policy.yaml` and not a release of
this module.

What the sandbox deliberately does not own is the binding layer's business:
`catalog/slots.yaml`'s `accepts_domains` is unread here, so a `light_group` slot
bound to a `cover` is a mis-binding this module passes and does not bless. Its
question is narrower -- whether a command names an entity the pack's own slots
put in its reach at all.

The module is data-in, data-out: it reads no clock, holds no state, and touches
nothing the caller did not hand it except the files a `provides` entry names and
the artifacts the gateway reads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from engine.vocabulary import BehaviourVocabulary, PackPolicy, Vocabulary

#: Every code a refusal carries, in the order a reader meets them: the three
#: kinds of term failure, then what a behaviour may reach, then what it may call,
#: then what an entry may confer. Closed, because a code invented at the point of
#: a check would be a code no reader could enumerate -- and it is the finest
#: distinction this module draws, so nothing here groups them further. The two term
#: families are separate members for the reason the docstring gives: a term the
#: vocabulary publishes and the subset forbids is refused for a different reason
#: than one the vocabulary has never heard of, and a pack author acts differently
#: on each.
REASONS: tuple[str, ...] = (
    "unknown_trigger",
    "unknown_condition",
    "unknown_action",
    "forbidden_trigger",
    "forbidden_condition",
    "forbidden_action",
    "unknown_behaviour",
    "literal_reference",
    "slot_not_declared",
    "slot_not_claimed",
    "not_in_reach",
    "undeclared_service",
    "banned_service",
    "dangling_path",
    "escaping_path",
    "class_mismatch",
)

#: An entity id, a device id or any other `domain.object_id` reference. A slot
#: name never contains a dot -- `catalog/slots.yaml`'s names are drawn from the
#: same closed alphabet the manifest schema's `slots` pattern uses -- so a dotted
#: token in a slot position is a literal rather than a slot, and it is reported
#: as one.
_ENTITY_REFERENCE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")

#: The keys a file of each class carries, most specific first. The class a
#: provided file *is* has to be read from the file, and Home Assistant's own
#: conventions are what the reading uses: a blueprint declares itself, a script
#: is a `sequence`, a dashboard has `views`, an automation has a trigger or an
#: action, a scene has `entities`, a template has `template:`. Anything else is
#: `other`, which is the class a pack may declare when it means exactly that.
_SHAPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("blueprint", ("blueprint",)),
    ("script", ("sequence",)),
    ("dashboard", ("views",)),
    ("automation", ("trigger", "action")),
    ("scene", ("entities",)),
    ("template", ("template",)),
)

#: The keys a `mode` document may carry, from `schemas/mode/1.0.0.json`. Tested
#: as a closed *set* rather than by key presence, because `mode: single` is an
#: ordinary top-level key of an automation and a presence test would classify
#: every automation as a mode.
_MODE_KEYS = frozenset({"name", "description", "exclusive_group"})


class SandboxError(Exception):
    """Base for the failures this module defines."""


class MalformedPackError(SandboxError):
    """A manifest does not carry a field the sandbox reads.

    The failure names the file and the field rather than a bare `TypeError`,
    because a manifest that has been reshaped is a fact about the pack and not a
    mystery inside a check. Which *documents* are valid is `pack-manifest`'s
    business and is decided by the schema; this module decides only whether it
    can read what it was handed.
    """

    def __init__(self, path: Path, field: str) -> None:
        super().__init__(f"{path.as_posix()} carries no usable {field}")
        self.path = path
        self.field = field


@dataclass(frozen=True, slots=True)
class Behaviour:
    """One behaviour of a manifest, projected to the facts the sandbox reads.

    `trigger` and `condition` are optional because the manifest schema makes the
    action the one axis every behaviour carries; `services` and `slots` are what
    1.2.0 widened the definition with, and they are here because rules 2 and 3
    are asking a clause for data the other clauses cannot carry.
    """

    name: str
    action: str
    trigger: str | None
    condition: str | None
    services: tuple[str, ...]
    slots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Provided:
    """One `provides` entry: the path an entry pins and the class it declares."""

    path: str
    declared_class: str


@dataclass(frozen=True, slots=True)
class Pack:
    """A manifest, projected to what the sandbox checks.

    Construct it through `load_pack` or `project_pack`. `path` is kept because
    two rules need it and neither can derive it: rule 2 resolves a behaviour's
    slots for the room the pack lands in, and rule 4 needs the directory the pack
    *is* to decide whether a provided path stays inside it.
    """

    name: str
    path: Path
    requires_slots: tuple[str, ...]
    optional_slots: tuple[str, ...]
    behaviours: tuple[Behaviour, ...]
    provides: tuple[Provided, ...]

    @property
    def directory(self) -> Path:
        """The pack's own directory: the one its manifest file lies in."""
        return self.path.parent.resolve()

    @property
    def declared_slots(self) -> frozenset[str]:
        """Every slot the pack declares, required or optional.

        One set rather than two, because every rule that reads it is asking
        whether the pack said the slot's name at all. Which of the two lists it
        came from is a question for install -- a missing *required* slot skips a
        behaviour and a missing optional one degrades it -- and not for the
        sandbox, whose question is reach.
        """
        return frozenset(self.requires_slots) | frozenset(self.optional_slots)

    @property
    def effective_permissions(self) -> frozenset[str]:
        """Every service the pack's behaviours name, and nothing else.

        Computed from the clauses rather than read from a second list, so a
        behaviour added without the permissions being edited widens the set --
        which is the property the requirement exists to make impossible to lose.
        """
        return frozenset(
            service for behaviour in self.behaviours for service in behaviour.services
        )

    def behaviour(self, name: str) -> Behaviour | None:
        """The named behaviour, or `None` when the pack declares no such one."""
        for behaviour in self.behaviours:
            if behaviour.name == name:
                return behaviour
        return None


@dataclass(frozen=True, slots=True)
class Command:
    """One command a behaviour proposes, as the sandbox sees it.

    The engine's commands are `engine.decision_log.ProposedCommand`s, which carry
    the slot a command acts through and the entities it names; this is that fact
    plus the two the sandbox has to know and a command does not -- which
    behaviour asked, and which service it asks for. It is a dataclass of its own
    rather than a field added to `ProposedCommand` because a command exists in a
    tick and this exists in a check, and Phase 1's record shape is not this
    phase's to widen.
    """

    behaviour: str
    service: str
    slot: str
    entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Refusal:
    """One reason a pack was refused, and the code that reason is.

    The code is the refusal's reason and not a grouping over it: a report carries
    `reason` as the failure's class, so the two names would be one name. It is a
    member of `REASONS`, which is what makes the set enumerable from outside.
    """

    reason: str
    pack: str
    where: str
    message: str


@dataclass(frozen=True, slots=True)
class Flag:
    """One dangerous-but-legitimate permission a pack carries.

    A flag is not a refusal and never becomes one: it is a fact the install
    result surfaces so a person sees what they are about to give a pack, and the
    code path that reads the flagged list is not the code path that reads the
    banned one, so the two cannot be collapsed into each other by accident.
    """

    pack: str
    service: str
    message: str


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """What a sandbox check found: what it refused, and what it flagged."""

    refusals: tuple[Refusal, ...] = ()
    flags: tuple[Flag, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the pack cleared every rule that can refuse it."""
        return not self.refusals

    def merge(self, other: SandboxResult) -> SandboxResult:
        """This result followed by another's, refusals first within each.

        Order is preserved rather than sorted: a reader fixing a pack works down
        the list, and the list is built in the order the rules are stated.
        """
        return SandboxResult(
            refusals=self.refusals + other.refusals,
            flags=self.flags + other.flags,
        )


def project_pack(document: Mapping[str, object], path: Path) -> Pack:
    """The manifest as the sandbox reads it, failing by naming the field.

    Only the fields the checks read are projected. `version`, `kind`,
    `engine_api`, `license`, `i18n`, `dependencies` and `conflicts` are other
    checks' business and are deliberately absent, for the same reason
    `accepts_domains` is absent from the gateway's slot projection: a field
    nothing here consults would invite the belief that something does.
    """
    name = document.get("name")
    if not isinstance(name, str):
        raise MalformedPackError(path, "`name`")
    return Pack(
        name=name,
        path=path,
        requires_slots=_slot_names(path, document, "requires_slots"),
        optional_slots=_slot_names(path, document, "optional_slots"),
        behaviours=_behaviours(path, document),
        provides=_provides(path, document),
    )


def load_pack(path: Path) -> Pack:
    """Read one pack manifest from disk.

    A pack is data, so it is read rather than imported: nothing in this phase
    executes a pack, and a loader that could would be the interpreter feature
    D5 says does not exist.
    """
    if not path.is_file():
        raise MalformedPackError(path, "file")
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise MalformedPackError(path, "mapping")
    return project_pack(cast("Mapping[str, object]", loaded), path)


def check_declarative(
    pack: Pack, published: BehaviourVocabulary, policy: PackPolicy
) -> tuple[Refusal, ...]:
    """Rule 1: every term a behaviour names is published and declarative.

    The two failures are separated here and nowhere else. A term the vocabulary
    publishes but the subset forbids is *not* reported as unknown, because it is
    not: the author wrote a term the project knows and refuses, and telling them
    otherwise would send them looking for a spelling mistake that is not there.
    """
    refusals: list[Refusal] = []
    for behaviour in pack.behaviours:
        for axis, term, forbidden in (
            ("trigger", behaviour.trigger, policy.forbidden_triggers),
            ("condition", behaviour.condition, policy.forbidden_conditions),
            ("action", behaviour.action, policy.forbidden_actions),
        ):
            if term is None:
                continue
            where = f"behaviour {behaviour.name}"
            if not published.publishes(_AXIS_PLURAL[axis], term):
                refusals.append(
                    Refusal(
                        reason=f"unknown_{axis}",
                        pack=pack.name,
                        where=where,
                        message=f"{where} declares the {axis} `{term}`, which "
                        "`behavior-vocabulary` does not publish, so no version "
                        "of the vocabulary can resolve it",
                    )
                )
            elif term in forbidden:
                refusals.append(
                    Refusal(
                        reason=f"forbidden_{axis}",
                        pack=pack.name,
                        where=where,
                        message=f"{where} declares the {axis} `{term}`, which "
                        "the vocabulary publishes and the declarative subset "
                        "forbids: a pack states what it wants, and a term that "
                        "branches, loops, orders or evaluates is a program",
                    )
                )
    return tuple(refusals)


#: The plural the schema uses for each axis's published list. Named here so the
#: mapping from a behaviour's singular clause to the vocabulary's plural list is
#: stated once rather than spelled out at the call site.
_AXIS_PLURAL: Mapping[str, str] = {
    "trigger": "triggers",
    "condition": "conditions",
    "action": "actions",
}


def check_slot_claims(pack: Pack, vocabulary: Vocabulary) -> tuple[Refusal, ...]:
    """Rule 2's static half: a behaviour names slots the pack declares.

    A behaviour naming a slot the pack's own clauses do not declare is reaching
    for something no install will ever bind to it, and a behaviour naming a
    `domain.object_id` is reaching past the binding layer altogether. Both are
    refused; only the second is reported as a literal, and the distinction is
    by shape rather than by a list of domains, because a slot name cannot
    contain a dot and every literal can.
    """
    refusals: list[Refusal] = []
    declared = pack.declared_slots
    for slot in sorted(declared):
        if slot not in vocabulary.slots:
            refusals.append(
                Refusal(
                    reason="slot_not_declared",
                    pack=pack.name,
                    where=f"pack {pack.name}",
                    message=f"`{pack.name}` declares the slot `{slot}`, which "
                    "`catalog/slots.yaml` does not define, so no room can ever "
                    "bind it and the pack is installable nowhere",
                )
            )
    for behaviour in pack.behaviours:
        where = f"behaviour {behaviour.name}"
        for slot in behaviour.slots:
            if _ENTITY_REFERENCE.match(slot):
                refusals.append(
                    Refusal(
                        reason="literal_reference",
                        pack=pack.name,
                        where=where,
                        message=f"{where} names `{slot}`, which is an entity "
                        "reference and not a slot: a pack that names a house's "
                        "entity is a pack that only works in that house and can "
                        "reach past its grant",
                    )
                )
            elif slot not in vocabulary.slots:
                refusals.append(
                    Refusal(
                        reason="slot_not_declared",
                        pack=pack.name,
                        where=where,
                        message=f"{where} reaches through `{slot}`, which "
                        "`catalog/slots.yaml` does not define",
                    )
                )
            elif slot not in declared:
                refusals.append(
                    Refusal(
                        reason="slot_not_declared",
                        pack=pack.name,
                        where=where,
                        message=f"{where} reaches through `{slot}`, which is not "
                        f"a slot `{pack.name}` declares in `requires_slots` or "
                        "`optional_slots`, so no room ever bound it to this pack",
                    )
                )
    return tuple(refusals)


def check_services(pack: Pack, policy: PackPolicy) -> SandboxResult:
    """Rule 3: declared services are the permission set; bans refuse, flags say so.

    The set is computed from the behaviours and is not compared against a second
    list, so there is nothing here to go stale. A banned service refuses the
    pack; a flagged one is recorded and the pack proceeds -- and the two are read
    from two lists by two branches, which is what keeps a flag from ever
    behaving as a ban.
    """
    refusals: list[Refusal] = []
    flags: list[Flag] = []
    for behaviour in pack.behaviours:
        where = f"behaviour {behaviour.name}"
        for service in behaviour.services:
            if policy.bans(service):
                refusals.append(
                    Refusal(
                        reason="banned_service",
                        pack=pack.name,
                        where=where,
                        message=f"{where} calls `{service}`, which "
                        "`catalog/pack-policy.yaml` bans: the service acts on the "
                        "system that hosts the pack rather than on a device in "
                        "the house, which is a refusal and not a flag",
                    )
                )
            elif service in policy.flagged_services:
                flags.append(
                    Flag(
                        pack=pack.name,
                        service=service,
                        message=f"{where} calls `{service}`, which is dangerous "
                        "and legitimate: the pack installs and this is surfaced "
                        "so the permission is granted knowingly",
                    )
                )
    return SandboxResult(refusals=tuple(refusals), flags=tuple(flags))


def check_declared_service(pack: Pack, service: str, where: str) -> Refusal | None:
    """One service call against the pack's computed permission set.

    The refusal names the pack, the service, and the declaration that would have
    admitted it, because "not permitted" without the fix is a message an author
    has to reverse-engineer.
    """
    if service in pack.effective_permissions:
        return None
    return Refusal(
        reason="undeclared_service",
        pack=pack.name,
        where=where,
        message=f"{where} calls `{service}`, which `{pack.name}`'s behaviours do "
        "not declare: the effective permission set is computed from their "
        f"`services` clauses, so declaring `{service}` on one of them is what "
        "would admit this call",
    )


def check_provides(pack: Pack, root: Path) -> tuple[Refusal, ...]:
    """Rule 4: a `provides` path stays inside the pack and carries its class.

    The base is the repository root, which is the schema's own wording for the
    path, and the containment test is what makes resolving against the root
    sound: a repo-relative path is only safe if the answer is still inside the
    pack. Containment is checked before existence, because an absolute path that
    happens to name a real file is a worse fault than a dangling relative one and
    must not be reported as merely missing.
    """
    refusals: list[Refusal] = []
    directory = pack.directory
    for entry in pack.provides:
        where = f"provides {entry.path}"
        resolved = (root / entry.path).resolve()
        if not _inside(resolved, directory):
            refusals.append(
                Refusal(
                    reason="escaping_path",
                    pack=pack.name,
                    where=where,
                    message=f"{where} resolves to {resolved.as_posix()}, which is "
                    f"outside {directory.as_posix()}: a pack pins what it "
                    "confers and nothing else",
                )
            )
            continue
        if not resolved.is_file():
            refusals.append(
                Refusal(
                    reason="dangling_path",
                    pack=pack.name,
                    where=where,
                    message=f"{where} names no file: the repository does not "
                    f"contain {entry.path}, so the pack pins nothing",
                )
            )
            continue
        actual = file_class(resolved)
        if actual != entry.declared_class:
            refusals.append(
                Refusal(
                    reason="class_mismatch",
                    pack=pack.name,
                    where=where,
                    message=f"{where} declares class `{entry.declared_class}` and "
                    f"the file {entry.path} is a `{actual}`: the class is what "
                    "stops a pack conferring an artifact nobody can pin",
                )
            )
    return tuple(refusals)


def file_class(path: Path) -> str:
    """The class a provided file is, read from its own document.

    The reading is a shape test over Home Assistant's own conventions rather
    than a schema lookup, because only `mode` has a schema of its own and the
    other classes are recognized by what they are -- a blueprint declares
    itself, a script is a sequence, a dashboard has views. A file that is not a
    mapping, or a mapping no shape claims, is `other`, which is the one class a
    pack may declare when it means "I am not claiming one of these".
    """
    try:
        document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError):
        return "other"
    if not isinstance(document, Mapping):
        return "other"
    keys = set(document)
    for name, markers in _SHAPES:
        if any(marker in keys for marker in markers):
            return name
    if keys and keys <= _MODE_KEYS and {"name", "description"} <= keys:
        return "mode"
    return "other"


def reach(
    pack: Pack, behaviour: Behaviour, binding: Mapping[str, str]
) -> frozenset[str]:
    """The entities one behaviour can reach in one room.

    Reach is bound to what the house actually supplied, which is the requirement
    and not a convenience: a slot the room binds nowhere contributes nothing, so
    a behaviour reaching only through it is *inert* -- the pack installs, because
    the slot was optional, and nothing happens, because there is no entity to act
    on. An empty result is that case and is not an error; it is the difference
    between a pack that reaches nothing and a pack that reaches something it was
    not granted, which is what `check_commands` refuses.
    """
    return frozenset(
        binding[slot]
        for slot in behaviour.slots
        if slot in pack.declared_slots and slot in binding
    )


def check_commands(
    pack: Pack,
    commands: Sequence[Command],
    binding: Mapping[str, str],
    policy: PackPolicy,
) -> SandboxResult:
    """Rules 2 and 3 applied to the commands a room's binding admits.

    `binding` maps a slot to the entity the room supplied for it, and it is the
    only thing that decides reach: a command acting through a slot the room did
    not bind is inert and produces no refusal, because there is nothing to
    refuse. A command acting through a slot the room *did* bind must name that
    entity and no other -- an entity outside the behaviour's reach is the failure
    this whole rule exists for.
    """
    refusals: list[Refusal] = []
    flags: list[Flag] = []
    for command in commands:
        behaviour = pack.behaviour(command.behaviour)
        if behaviour is None:
            refusals.append(
                Refusal(
                    reason="unknown_behaviour",
                    pack=pack.name,
                    where=f"command from {command.behaviour}",
                    message=f"a command names the behaviour `{command.behaviour}`, "
                    f"which `{pack.name}` does not declare, so nothing grants it "
                    "a reach or a service",
                )
            )
            continue
        where = f"behaviour {behaviour.name}"
        if _ENTITY_REFERENCE.match(command.slot):
            refusals.append(
                Refusal(
                    reason="literal_reference",
                    pack=pack.name,
                    where=where,
                    message=f"{where} commands `{command.slot}`, which is an "
                    "entity reference and not a slot: the command reaches past "
                    "the binding layer rather than through it",
                )
            )
            continue
        if command.slot not in pack.declared_slots:
            refusals.append(
                Refusal(
                    reason="slot_not_declared",
                    pack=pack.name,
                    where=where,
                    message=f"{where} commands through `{command.slot}`, which "
                    f"`{pack.name}` does not declare, so the room never bound "
                    "anything for it to name",
                )
            )
            continue
        if command.slot not in behaviour.slots:
            refusals.append(
                Refusal(
                    reason="slot_not_claimed",
                    pack=pack.name,
                    where=where,
                    message=f"{where} commands through `{command.slot}`, which "
                    "the pack declares but this behaviour does not name in its "
                    "`slots` clause: the reach failure and not a service one, "
                    "because the slot was granted and this behaviour did not "
                    "claim it",
                )
            )
            continue
        supplied = binding.get(command.slot)
        if supplied is None:
            # The room bound nothing here. The behaviour is inert -- nothing is
            # commanded and nothing is refused -- and the command's entities are
            # not examined, because a command that acts on nothing acts on
            # nothing.
            continue
        for entity in command.entities:
            if entity == supplied:
                continue
            # `entities` holds entity ids by definition -- they are what a
            # command acts on -- so a wrong one is a reach failure whether or not
            # it is well formed. The *literal* failure is the other position: a
            # `domain.object_id` written where a slot name belongs.
            refusals.append(
                Refusal(
                    reason="not_in_reach",
                    pack=pack.name,
                    where=where,
                    message=f"{where} commands `{entity}`, which is not the entity "
                    f"`{command.slot}` puts in its reach in this room: a pack "
                    "reaches the entities its own slots were bound to and no "
                    "others",
                )
            )
        undeclared = check_declared_service(pack, command.service, where)
        if undeclared is not None:
            refusals.append(undeclared)
        elif policy.bans(command.service):
            refusals.append(
                Refusal(
                    reason="banned_service",
                    pack=pack.name,
                    where=where,
                    message=f"{where} calls `{command.service}`, which "
                    "`catalog/pack-policy.yaml` bans",
                )
            )
        elif command.service in policy.flagged_services:
            flags.append(
                Flag(
                    pack=pack.name,
                    service=command.service,
                    message=f"{where} calls `{command.service}`, which is "
                    "dangerous and legitimate, so the call proceeds and the "
                    "permission is surfaced",
                )
            )
    return SandboxResult(refusals=tuple(refusals), flags=tuple(flags))


def check_pack(
    pack: Pack, root: Path, vocabulary: Vocabulary, published: BehaviourVocabulary
) -> SandboxResult:
    """Every rule that can be decided from the manifest alone.

    This is what validation runs. The reach rule's second half is not here
    because it needs a room's binding and validation has no room -- which is
    sound rather than a gap: a pack that passes this and installs cannot exceed
    the grant it was checked against, because `check_commands` is the only path
    from a behaviour to an entity and it reads the same manifest.
    """
    refusals = (
        check_declarative(pack, published, vocabulary.pack_policy)
        + check_slot_claims(pack, vocabulary)
        + check_provides(pack, root)
    )
    return SandboxResult(refusals=refusals).merge(
        check_services(pack, vocabulary.pack_policy)
    )


def _inside(path: Path, directory: Path) -> bool:
    """Whether a resolved path lies inside a directory, itself included.

    `Path.is_relative_to` alone would admit the directory itself, which is not a
    file a pack may confer; `is_file` is asked afterwards by the caller, so the
    two questions stay separate rather than one test answering both badly.
    """
    return path == directory or path.is_relative_to(directory)


def _slot_names(
    path: Path, document: Mapping[str, object], field: str
) -> tuple[str, ...]:
    """One slot list, checked to be a list of names."""
    rows = document.get(field, [])
    if rows is None:
        return ()
    if not _is_sequence(rows):
        raise MalformedPackError(path, f"`{field}` list")
    names = [row for row in rows if isinstance(row, str)]
    if len(names) != len(rows):
        raise MalformedPackError(path, f"`{field}` names")
    return tuple(names)


def _behaviours(path: Path, document: Mapping[str, object]) -> tuple[Behaviour, ...]:
    """The manifest's behaviours, projected to the facts the checks read."""
    rows = document.get("behaviours", [])
    if rows is None:
        return ()
    if not _is_sequence(rows):
        raise MalformedPackError(path, "`behaviours` list")
    behaviours: list[Behaviour] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise MalformedPackError(path, f"`behaviours[{index}]` mapping")
        name = row.get("name")
        action = row.get("action")
        if not isinstance(name, str) or not isinstance(action, str):
            raise MalformedPackError(path, f"`behaviours[{index}]` `name` and `action`")
        behaviours.append(
            Behaviour(
                name=name,
                action=action,
                trigger=_optional_term(path, row, "trigger"),
                condition=_optional_term(path, row, "condition"),
                services=_string_list(path, row, "services"),
                slots=_string_list(path, row, "slots"),
            )
        )
    return tuple(behaviours)


def _provides(path: Path, document: Mapping[str, object]) -> tuple[Provided, ...]:
    """The manifest's `provides` entries, as the containment rule reads them."""
    rows = document.get("provides", [])
    if rows is None:
        return ()
    if not _is_sequence(rows):
        raise MalformedPackError(path, "`provides` list")
    entries: list[Provided] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise MalformedPackError(path, f"`provides[{index}]` mapping")
        entry_path = row.get("path")
        declared = row.get("class")
        if not isinstance(entry_path, str) or not isinstance(declared, str):
            raise MalformedPackError(path, f"`provides[{index}]` `path` and `class`")
        entries.append(Provided(path=entry_path, declared_class=declared))
    return tuple(entries)


def _optional_term(path: Path, row: Mapping[str, object], field: str) -> str | None:
    """One optional vocabulary term, which the schema may simply omit."""
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedPackError(path, f"behaviour `{field}`")
    return value


def _string_list(path: Path, row: Mapping[str, object], field: str) -> tuple[str, ...]:
    """One optional list of names, omitted meaning empty."""
    rows = row.get(field, [])
    if rows is None:
        return ()
    if not _is_sequence(rows):
        raise MalformedPackError(path, f"behaviour `{field}` list")
    names = [item for item in rows if isinstance(item, str)]
    if len(names) != len(rows):
        raise MalformedPackError(path, f"behaviour `{field}` names")
    return tuple(names)


def _is_sequence(value: object) -> bool:
    """Whether a YAML value is a list.

    A `Sequence` test alone would admit a string, and a string is iterable: the
    loader would then read a behaviour list of one character per letter and
    report a malformed *row* rather than a malformed list, naming the wrong
    field. `engine/vocabulary.py` makes the same test for the same reason.
    """
    return isinstance(value, Sequence) and not isinstance(value, str)


def refusals_by_reason(result: SandboxResult) -> Mapping[str, tuple[Refusal, ...]]:
    """A result's refusals grouped by reason, each reason's order preserved.

    The keys are `REASONS`, so a caller reporting per-reason counts gets the
    sandbox's own distinctions rather than a face's coarser classes. Every reason
    is a key even when empty, so a caller does not have to decide whether an
    absent key and an empty one mean the same thing -- they do, and this is where
    that is settled.
    """
    grouped: dict[str, list[Refusal]] = {reason: [] for reason in REASONS}
    for refusal in result.refusals:
        grouped[refusal.reason].append(refusal)
    return {reason: tuple(items) for reason, items in grouped.items()}
