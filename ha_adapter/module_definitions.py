"""The modules a house *offers*: a blueprint, and the answers that make it reusable.

An imported blueprint used to be hosted straight into a room, and that is one
import per room: the same lighting blueprint in five rooms was five passes over
the import screen, five sets of answers to keep in step by hand. A **definition**
is the same import with the room taken out. What is left is the part that is true
whatever room it lands in -- the document, what each input is answered with *by
default*, which of those answers stay settable, which of the source's values are
published -- and installing it is then a room and one press.

**A definition is a file, and the file is the export.** One definition per file
under `config/open_house/modules/`, holding the blueprint's own text rather than
a path to it, so a house that receives one needs neither the blueprint nor the
network. Handing a module to somebody else is copying a file, and taking one is
dropping it in -- which is the only story about sharing that is honest, because
everything else (a registry, a URL, a fetch) is a service this project does not
run.

**Two files, two questions.** `module_records` is what a house *has* -- the
automations it runs, the rooms they are in, the values they publish. This is what
a house *could have*: nothing here is running, and a definition whose slots no
room has bound is a definition that is entirely fine. The two are named apart for
that reason: a "record" is an installation, a "definition" is a module.

Pure by construction -- `json`, a parser and the standard library -- so a
definition can be read, written and refused without a Home Assistant, for the
reason `module_host` gives.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .module_host import read_module_source
from .module_records import ModuleRecord, Variant
from .pack_authoring import AuthoringError

__all__ = [
    "EXTENSION",
    "KIND",
    "LICENCES",
    "VERSION",
    "ModuleDefinition",
    "follow",
    "from_document",
    "load",
    "load_all",
    "remove",
    "to_document",
    "write",
]


#: The file one definition lives in, inside the directory the caller names:
#: `config/open_house/modules/<slug>.json`. A file each rather than a list in one
#: file, because the file *is* what a person sends to another house -- a module
#: that had to be cut out of a shared document would be a module that cannot be
#: shared by copying, which is the whole mechanism.
EXTENSION = ".json"

#: The key an exported file names itself by, and the version of that file's own
#: shape. A file a person dropped in from anywhere is one this house has to be
#: able to say yes or no to, and the first thing to say is "this is a module, in
#: a shape I know". A number rather than a schema because there is nothing to
#: migrate yet, exactly as in `module_records`.
KIND = "open_house_module"
VERSION = 1

#: The shape of a module name, restated from `module_records._KEY` rather than
#: imported: a name becomes part of an entity id and of an automation's id, so
#: the check belongs where the name is minted, and a definition that arrives from
#: another house is a second place names are minted.
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

#: A version is numbers and dots, and nothing else. Deliberately not a full
#: parser: a person writes this on a form to say "this is my second go at it",
#: and refusing `1.0.0-beta` is the sort of refusal that teaches nothing. What it
#: does catch is a version that is a sentence.
_VERSION = re.compile(r"^\d+(\.\d+)*$")

#: The licence codes a module may carry, in the order the catalog publishes them
#: (`schemas/catalog/licenses.json`, least to most restrictive). The same five a
#: pack manifest uses and not SPDX, for the reason that document gives -- and
#: restated here rather than read at import time because this is a *pure* layer
#: that cannot go looking for a catalog. `tests/test_module_definitions.py` holds
#: the two lists against each other, so a sixth code cannot arrive unnoticed.
LICENCES = ("public_domain", "mit", "apache_2_0", "cc_by_nc_sa", "no_licence")

#: The binding kinds a definition may carry, which are the ones an import screen
#: offers. A definition is imported *before* it is installed, so `slot` is the
#: interesting one and `entity` the one that ties it to the house it was made in.
_BINDING_KINDS = ("literal", "entity", "slot", "output")


@dataclass(frozen=True)
class ModuleDefinition:
    """One module a house offers, and everything needed to install it.

    `source` is the document itself -- a blueprint as Home Assistant resolved it,
    or an automation pasted in -- kept verbatim so an install never has to find
    the blueprint again. `blueprint` is where it came from, and it is provenance
    rather than a reference: a definition whose blueprint has since been edited
    still installs, and re-importing the blueprint is how a person takes the
    update.

    `bindings` is the same `InputBinding` rows a record keeps, and they are the
    definition's **default answers**: what an install starts from, per room. The
    answer that makes a module reusable is `slot` -- "the room's lux sensor" --
    because it is the only one that means something in a room this house has
    never seen. An `entity` answer is a device *from the house that made it*, and
    `pinned` says so, which is what the store tells a person before they hand the
    file to somebody else.

    `settings` and `picks` are the two decisions about exposure the import screen
    already made: which inputs stay settable on the installed module, and which
    of the source's values it publishes. Both are carried here rather than on the
    installation because they are properties of the module a person chose, not of
    the room it happens to be in.
    """

    slug: str
    title: str
    source: str
    description: str = ""
    #: Where the document came from, for a person reading the file later. Empty
    #: when the document was pasted rather than picked.
    blueprint: str = ""
    author: str = ""
    version: str = "1.0.0"
    licence: str = "no_licence"
    #: The default answers, by input name, as the import screen sent them.
    bindings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Which inputs stay settable on an installed module.
    settings: tuple[str, ...] = ()
    #: The candidates that become outputs, each with the key they are called.
    picks: tuple[tuple[str, str], ...] = ()
    #: The inputs answered with a **condition**, by input name, as Home
    #: Assistant's own condition config.
    #:
    #: Here rather than in `bindings` because it is a different kind of answer: a
    #: binding is a value, and a condition is logic that cannot be written into
    #: an input a *trigger* names -- Home Assistant matches a trigger's
    #: `entity_id` against the entities the house has and never renders it, so
    #: logic there installs and never fires. Open House makes the condition into
    #: an entity instead and binds the input to that
    #: (`custom_components/open_house/binary_sensor.py`), which is why it travels
    #: to the installing house as the condition rather than as a device.
    derived: Mapping[str, Any] = field(default_factory=dict)
    #: The inputs answered by a **flow of nodes**, by name -- the *names* and not
    #: the flows, which is the whole of why this travels at all. A flow id belongs
    #: to one Node-RED in one house, so a definition carrying one would be a
    #: definition that installs into a second house pointing at a flow that is not
    #: there. The name says the thing that *is* the module's: this input is not a
    #: value, it is a program. Installing it pushes a flow for that input in
    #: whichever Node-RED is doing the installing.
    flows: tuple[str, ...] = ()
    #: The inputs answered by a **script**, by name -- the names and not the
    #: scripts, for the reason the flows are names: a `script.<id>` belongs to one
    #: Home Assistant, and a definition carrying one would install into a second
    #: house calling a script that is not there. Unlike a flow, this house cannot
    #: make the missing half -- the script is the person's own and Open House
    #: never writes one -- so an input named here arrives with no script behind it
    #: and the installing house answers it, exactly as a slot arrives unbound.
    scripts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _KEY.match(self.slug):
            raise AuthoringError(
                f"{self.slug!r} is not a module name: a module is named lower "
                "case with underscores, because its name is part of the entity "
                "id each of its outputs lives at"
            )
        if not str(self.title).strip():
            raise AuthoringError("a module needs a name a person can read")
        if self.version and not _VERSION.match(str(self.version)):
            raise AuthoringError(
                f"{self.version!r} is not a version: a version is numbers "
                "separated by dots, like 1.0 or 1.2.3"
            )
        if self.licence not in LICENCES:
            raise AuthoringError(
                f"{self.licence!r} is not a licence this house knows: a module "
                f"carries one of {', '.join(LICENCES)}"
            )
        # The document has to *read*: a definition that cannot be installed is a
        # file whose only use is to fail later, in another house, on a screen
        # about rooms. Checked for emptiness first, because "not a mapping" is a
        # true answer about an empty file and a useless one.
        if not self.source.strip():
            raise AuthoringError(
                "a module definition has to carry the document it was made from, "
                "so it can be installed without the blueprint it came from"
            )
        source = read_module_source(self.source)
        self._check_names(source.inputs)
        for key in self.settings:
            if key not in source.inputs:
                raise AuthoringError(
                    f"{key!r} is kept as a setting and the document asks for no "
                    "input by that name, so nothing would set it"
                )
        # A pick is the pair (the candidate, the name it is published under). Only
        # the second has a rule to check: the first names a value in the document
        # -- an input, or a service call's argument -- and is checked where those
        # candidates are read off the source, not here.
        for _candidate, key in self.picks:
            if not _KEY.match(key):
                raise AuthoringError(f"{key!r} is not an output name")

    @property
    def pinned(self) -> bool:
        """Whether this module names a device from the house that defined it.

        An `entity` answer is a device id, and a device id is one house's. The
        module still installs -- in the house it was made in, and here, where the
        entity may well exist too -- but it is not the same *kind* of module as
        one answered with slots, and a person about to send the file to somebody
        else is the person who needs to know that.
        """
        return any(row.get("kind") == "entity" for row in self.bindings.values())

    @property
    def slots(self) -> tuple[str, ...]:
        """The slot names this module reaches through, sorted and deduplicated.

        What an installing room has to be able to answer for the module to run at
        once rather than waiting -- the same question `rooms/available_modules`
        answers for a pack, asked of a definition.
        """
        return tuple(
            sorted(
                {
                    str(row.get("slot"))
                    for row in self.bindings.values()
                    if row.get("kind") == "slot" and row.get("slot")
                }
            )
        )

    def _check_names(self, inputs: Mapping[str, Any]) -> None:
        for name in {*self.bindings, *self.derived, *self.flows, *self.scripts}:
            if name not in inputs:
                raise AuthoringError(
                    f"the module is set up to answer {name!r} and the document "
                    "asks for no input by that name, so the answer would be "
                    "written nowhere"
                )

    def as_json(self) -> Mapping[str, Any]:
        """This definition as the plain tree the file holds."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "blueprint": self.blueprint,
            "author": self.author,
            "version": self.version,
            "licence": self.licence,
            "source": self.source,
            "bindings": {name: dict(row) for name, row in self.bindings.items()},
            "settings": list(self.settings),
            "picks": [list(pick) for pick in self.picks],
            "derived": dict(self.derived),
            "flows": list(self.flows),
            "scripts": list(self.scripts),
        }

    def with_answers(
        self,
        *,
        bindings: Mapping[str, Any] | None,
        settings: Sequence[str] | None,
        casts: Mapping[str, Any] | None = None,
        flows: Sequence[str] | None = None,
        scripts: Sequence[str] | None = None,
    ) -> ModuleDefinition:
        """This definition with an install's own answers laid over its defaults.

        Installing is asking the definition's questions again in one room -- this
        is how a room's answers reach the build. Merged rather than replaced, for
        the reason `modules.async_update` merges: a screen showing one changed
        answer must not drop the other eleven.

        `casts` merges the same way, with one difference: an *empty* condition
        sent for a name is how a cast comes back off, so it is dropped rather
        than stored as nothing.

        `flows` is *replaced* rather than merged, and that is the one place this
        differs from everything else here. A condition is a value that can be
        laid over another; "this input is answered by a flow" is not -- it is a
        property of the input, so a room that sends a set is *saying* which of
        them are, and a room that sends nothing keeps the definition's.

        `scripts` is replaced the same way and for the same reason: which of a
        module's inputs are answered by a program is a property of the input, not
        a value a room lays over the definition's.
        """
        merged: dict[str, Mapping[str, Any]] = {
            name: dict(row) for name, row in self.bindings.items()
        }
        for name, binding in (bindings or {}).items():
            merged[name] = {
                "kind": binding.kind,
                "value": binding.value,
                "module": binding.module,
                "key": binding.key,
                "slot": binding.slot,
            }
        conditions = {
            name: condition
            for name, condition in {**dict(self.derived), **dict(casts or {})}.items()
            if condition
        }
        return replace(
            self,
            bindings=merged,
            derived=conditions,
            settings=self.settings if settings is None else tuple(settings),
            flows=self.flows
            if flows is None
            else tuple(str(one) for one in flows if one),
            scripts=(
                self.scripts
                if scripts is None
                else tuple(str(one) for one in scripts if one)
            ),
        )


def follow(
    before: ModuleDefinition,
    after: ModuleDefinition,
    record: ModuleRecord,
) -> ModuleRecord:
    """One installation as the module it was made from now leaves it.

    **A module is defined once and installed many times, and editing it is
    editing the module** -- the thing every room runs, not the copy that happens
    to be on the screen. That is what this answers: given what the definition
    said (`before`), what it says now (`after`), and one copy of it somewhere,
    the copy as the edit leaves it.

    It cannot be a plain copy of the new answers, because the installation holds
    two kinds of thing and only one of them is the module's. What the module
    decides is what it *is*: the document, which inputs it keeps settable, which
    of its values it publishes, and the conditions and flows its inputs are
    answered with. What the room decides is what it *found*: the device behind a
    slot, the value somebody set on the module, the condition a person wrote
    because their house is not the one the module was written for.

    So the rule is a merge's, and it needs no history to apply: **an answer the
    room never moved is the module's to change; an answer that differs from what
    the definition said before is somebody's own and is left alone.** A room that
    took the module as it came follows it everywhere; a room that made it theirs
    in one place keeps that, and follows in all the others.

    Every *configuration* of the installation follows too, by the same rule and
    for the same reason: which inputs a module exposes is a fact about the module
    rather than about the answers it is currently running, so a second
    configuration cannot keep a setting the module no longer offers.
    """
    inputs = set(read_module_source(after.source).inputs)
    moved = {
        name: _following(configuration, before, after, inputs)
        for name, configuration in record.held_configurations.items()
    }
    active = moved[record.variant]
    return replace(
        # `applied_to` rather than the five fields by hand: what the record holds
        # flat *is* the active configuration, and the one place that rule is
        # written is `Variant`.
        active.applied_to(record),
        source=after.source,
        # Written through `held_configurations` and not through `variants`: a
        # record that has never been built has an active configuration that is
        # nowhere in the map, and the map is what is being replaced here.
        variants=moved,
    )


def _following(
    configuration: Variant,
    before: ModuleDefinition,
    after: ModuleDefinition,
    inputs: set[str],
) -> Variant:
    """One configuration of one installation, as the edited module leaves it.

    `inputs` is what the *new* document asks for, and it is the last word on
    every map here: an answer, a setting, a condition or a flow for an input the
    document no longer declares is not an answer any more, and keeping it would
    be a record that refuses to be built (`_async_build` reads the document and
    finds nothing to write the value into).
    """
    # **The settings are the module's, plus what a build put there -- not merged
    # three ways like the maps below.** "Which of my answers stay dials" is a
    # fact about the module and about nothing the room did, so an edit that
    # exposes an input reaches every installation rather than only the ones that
    # had not already decided; and one that stops exposing it takes the dial away
    # everywhere, which is what the person editing *asked* for.
    #
    # What is *not* the module's is `grown`: the settings a build added because
    # the document declares an input with no default and nobody answered it
    # (`_async_build`'s `unset_settings`). Those are not the definition's to
    # withdraw -- taking them out would leave the module unable to be built at
    # all -- so they ride along beside the definition's own.
    grown = [name for name in configuration.settings if name not in before.settings]
    return Variant(
        bindings={
            name: row
            for name, row in _answered(
                before.bindings, after.bindings, configuration.bindings
            ).items()
            if name in inputs
        },
        settings=tuple(
            name for name in dict.fromkeys((*after.settings, *grown)) if name in inputs
        ),
        # **Replaced, not merged.** Which of a source's values a module publishes
        # is the module's own decision and nothing a room has ever been able to
        # make differently -- `picks` is written by the import screen and by this
        # edit, and by nothing else.
        picks=tuple(after.picks),
        derived={
            name: condition
            for name, condition in _answered(
                before.derived, after.derived, configuration.derived
            ).items()
            if name in inputs
        },
        # The *ids* are this house's and are kept (`configuration.flows.get`); the
        # *names* are the module's, so an input that has stopped being answered by
        # a flow drops off and one that has started is named with no id yet --
        # which is what tells `_async_push_flows` to make it rather than update
        # it. A flow this configuration has and the definition never did is one
        # this house added and is kept, by the same rule as a binding.
        flows={
            name: configuration.flows.get(name, "")
            for name in dict.fromkeys(
                (
                    *after.flows,
                    *(name for name in configuration.flows if name not in before.flows),
                )
            )
            if name in inputs
        },
        # The scripts, by the same rule as the flows, minus the half this house
        # cannot make: the *ids* are this house's and are kept, and the *names*
        # are the module's. A name that has started being answered by a script
        # arrives with no id (`configuration.scripts.get(name, "")`), and the
        # build refuses it -- a script cast with nothing to call is a module that
        # cannot be built, which is the honest answer for a definition installed
        # into a house that has not picked a script for it yet.
        scripts={
            name: configuration.scripts.get(name, "")
            for name in dict.fromkeys(
                (
                    *after.scripts,
                    *(
                        name
                        for name in configuration.scripts
                        if name not in before.scripts
                    ),
                )
            )
            if name in inputs
        },
    )


def _answered(
    before_rows: Mapping[str, Any],
    after_rows: Mapping[str, Any],
    mine: Mapping[str, Any],
) -> Mapping[str, Any]:
    """One map of answers, merged the way `follow` says.

    The three inputs are the definition as it was, the definition as it is, and
    what this installation holds -- for bindings, for conditions, or for anything
    else two layers both have an opinion about.

    A name the definition has *dropped* goes with it, and that is the whole of
    the rule read strictly: an installation still holding the old answer
    verbatim never chose it, so the module withdrawing it takes it away, and the
    input it was for goes back to being one nothing answers -- which is a state
    the build already knows how to handle (it waits, and grows the name back into
    the settings so somebody can fill it in).
    """
    found: dict[str, Any] = {}
    for name in dict.fromkeys((*after_rows, *mine)):
        held = mine.get(name, _NOTHING)
        if name in mine and held != before_rows.get(name, _NOTHING):
            # Theirs: either the definition never said this, or it said something
            # else and this installation is not saying it any more.
            found[name] = held
        elif name in after_rows:
            found[name] = after_rows[name]
    return found


#: What "not here at all" is spelled as while merging: a sentinel rather than
#: `None`, because a `None` an installation really holds -- a row that was
#: answered with nothing -- is a different answer from not having the row.
_NOTHING = object()


def to_document(definition: ModuleDefinition) -> Mapping[str, Any]:
    """The exported file for one definition: what it is, and what it holds.

    Wrapped rather than bare so the file says what it is. A person who finds a
    `.json` in a downloads folder, and a house asked to read one, both need the
    first answer to be "this is a module" rather than a parse error about an
    unexpected key -- and the wrapper is where a future shape gets a number to
    branch on.
    """
    return {KIND: VERSION, "definition": definition.as_json()}


def from_document(document: object) -> ModuleDefinition:
    """One exported file read back as a definition, or a refusal naming the file.

    Every field is checked rather than trusted: this is the one path a document
    from *another house* takes into this one, and a module is a thing that runs
    automations, writes files and publishes entities. A file that half-loaded
    would be a module built from answers nobody gave.
    """
    if not isinstance(document, Mapping):
        raise AuthoringError("the file is not a module: it is not a mapping")
    kind = document.get(KIND)
    if kind is None:
        raise AuthoringError(
            f"the file is not a module: a module file carries a {KIND!r} key, and "
            "this one does not -- it may be a profile, a pack or something else"
        )
    if kind != VERSION:
        raise AuthoringError(
            f"the file is a version {kind!r} module and this house reads version "
            f"{VERSION}"
        )
    row = document.get("definition")
    if not isinstance(row, Mapping):
        raise AuthoringError("the file is a module with no definition in it")
    return _definition(row)


def _definition(row: Mapping[str, Any]) -> ModuleDefinition:
    """One definition's own tree as the dataclass, refusing what is not one."""
    bindings = row.get("bindings") or {}
    if not isinstance(bindings, Mapping):
        raise AuthoringError("the module's answers are not a mapping of inputs")
    answers: dict[str, Mapping[str, Any]] = {}
    for name, binding in bindings.items():
        if not isinstance(binding, Mapping):
            raise AuthoringError(f"the module's answer for {name!r} is not a shape")
        kind = binding.get("kind")
        if kind not in _BINDING_KINDS:
            raise AuthoringError(
                f"the module answers {name!r} with {kind!r}, which is not a way "
                f"this house fills an input: {', '.join(_BINDING_KINDS)}"
            )
        if kind == "slot" and not binding.get("slot"):
            raise AuthoringError(
                f"the module answers {name!r} with a slot and names no slot, so "
                "there is nothing for a room to bind"
            )
        answers[str(name)] = {str(key): value for key, value in binding.items()}
    settings = row.get("settings") or []
    picks = row.get("picks") or []
    # A condition is Home Assistant's own shape and is carried through as the
    # file wrote it: this house has no second opinion to offer about what a
    # condition may contain, and a file from a house with a newer editor than
    # this one must still install.
    derived = row.get("derived") or {}
    if not isinstance(derived, Mapping):
        raise AuthoringError("the module's conditions are not a mapping of inputs")
    # The flow-answered inputs, as *names*. A file from a house that has never
    # seen a flow has none, and a file that names one installs here by pushing a
    # flow in whichever Node-RED this house has.
    flows = row.get("flows") or []
    if not isinstance(flows, (list, tuple)):
        raise AuthoringError("the module's flows are not a list of inputs")
    # The script-answered inputs, as *names*, for the reason the flows are names.
    # A file from a house that has never seen a script cast has none.
    scripts = row.get("scripts") or []
    if not isinstance(scripts, (list, tuple)):
        raise AuthoringError("the module's scripts are not a list of inputs")
    return ModuleDefinition(
        slug=str(row.get("slug") or ""),
        title=str(row.get("title") or ""),
        description=str(row.get("description") or ""),
        blueprint=str(row.get("blueprint") or ""),
        author=str(row.get("author") or ""),
        version=str(row.get("version") or "1.0.0"),
        licence=str(row.get("licence") or "no_licence"),
        source=str(row.get("source") or ""),
        bindings=answers,
        settings=tuple(str(name) for name in settings)
        if isinstance(settings, (list, tuple))
        else (),
        picks=tuple(
            (str(pick[0]), str(pick[1]))
            for pick in (picks if isinstance(picks, (list, tuple)) else ())
            if isinstance(pick, (list, tuple)) and len(pick) == 2
        ),
        derived={str(name): condition for name, condition in derived.items()},
        flows=tuple(str(name) for name in flows if name),
        scripts=tuple(str(name) for name in scripts if name),
    )


def path_of(root: Path, slug: str) -> Path:
    """Where one definition's file lives, so a caller can name it in a message."""
    return Path(root) / f"{slug}{EXTENSION}"


def load_all(root: Path) -> tuple[ModuleDefinition, ...]:
    """Every module this house offers, by name, or an empty tuple for a new house.

    A directory that is not there is a house that has defined none, which is
    every house at setup. A file that is there and will not read *is* a failure
    and is raised rather than skipped: silently dropping it would make a module
    disappear from the store -- and one that somebody had installed into a room
    would be a module the house runs with nothing left to say what it is.
    """
    root = Path(root)
    if not root.is_dir():
        return ()
    found: list[ModuleDefinition] = []
    for path in sorted(root.glob(f"*{EXTENSION}")):
        found.append(load(path))
    return tuple(found)


def load(path: Path) -> ModuleDefinition:
    """One definition's file, or a refusal naming it."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AuthoringError(
            f"the module at {path} could not be read: {error}"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise AuthoringError(
            f"the module at {path} is not valid JSON: {error}"
        ) from error
    definition = from_document(document)
    if f"{definition.slug}{EXTENSION}" != path.name:
        raise AuthoringError(
            f"the module in {path.name} calls itself {definition.slug!r}: a "
            "module's file is named after it, so the two have to agree"
        )
    return definition


def write(root: Path, definition: ModuleDefinition) -> Path:
    """Write one definition, replacing any file of that name, and answer the path.

    Blocking, by definition -- the caller runs it in an executor, since it is
    reached from a websocket handler.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = path_of(root, definition.slug)
    target.write_text(
        json.dumps(to_document(definition), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return target


def remove(root: Path, slug: str) -> Path:
    """Delete one definition's file, and answer the path that is now gone.

    A module that is not there is refused rather than shrugged at: the caller is
    a person pressing a button on a row they are looking at, and a screen that
    says it removed something it did not remove is worse than an error.
    """
    target = path_of(root, slug)
    try:
        target.unlink()
    except FileNotFoundError as error:
        raise AuthoringError(
            f"this house offers no module called {slug!r}, so there is nothing to "
            "remove"
        ) from error
    except OSError as error:
        raise AuthoringError(
            f"the module at {target} could not be removed: {error}"
        ) from error
    return target
