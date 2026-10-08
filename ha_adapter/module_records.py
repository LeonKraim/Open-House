"""What a house hosts: one record per module, and the file they live in.

A hosted module is a Home Assistant automation (`module_host.instantiate`) that
Open House created and keeps in step with, and the automation is Home
Assistant's -- it holds the logic and runs it. What Home Assistant cannot hold is
the *contract*: which of the blueprint's internals a person ticked as outputs,
which inputs they filled and with what, and which automation entity the record
belongs to. That is Open House's own data about the module, and this module is
where it is written down and read back.

**One record per module, and the slug is its name.** A module's slug is the name
its outputs are addressed by (`sensor.open_house_<slug>_<key>`) and the name the
panel shows, so it is also the file's key: one module cannot be written twice
under two spellings, and a rename is a visible change rather than a silent
second copy.

**The file is the whole state, and it is read at setup.** There is no database
and no cache: `load` reads one JSON document and a missing file is an empty
house, which is what a fresh install is. This is deliberately not
`ha_adapter/live_modules.py`'s directory of pack manifests: a pack is a
*declaration* the engine interprets, while a hosted module is an automation
somebody else runs, and the two obey different schemas. Sharing a directory
would mean a reader that had to guess which kind of thing it was looking at.

Pure by construction -- `pathlib`, `json` and the standard library -- so the
store can be tested without a Home Assistant, for the reason `module_host` gives.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .module_host import Output
from .pack_authoring import AuthoringError

__all__ = [
    "DEFAULT_VARIANT",
    "FILENAME",
    "ModuleRecord",
    "Variant",
    "from_documents",
    "keeping_answers",
    "load",
    "put",
    "slug",
    "write",
]

#: What the one configuration a module always has is called -- and the name the
#: active one takes when a record is read out of a file written before a module
#: could hold more than one.
DEFAULT_VARIANT = "Default"

#: The file the records live in, inside the directory the caller names --
#: `config/open_house/modules.json`, beside the packs a person authors.
FILENAME = "modules.json"

#: The version of the document this module writes and can read. A number rather
#: than a schema because there is nothing to migrate yet; it exists so that the
#: first change has something to branch on instead of having to guess whether an
#: old file is an old file.
VERSION = 1

#: The shape of a module name. The same rule `module_host._KEY` applies to an
#: output key, and deliberately restated as a *module's* name here rather than
#: imported: a module name becomes part of an entity id exactly as an output key
#: does, and the check belongs where the name is minted.
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ModuleRecord:
    """One hosted module: what it is called, what runs it, and what it publishes.

    `inputs` is kept even though the automation it built already has them baked
    in, because it is how a person's choices are shown back to them and the only
    way to rebuild the automation from the blueprint (`instantiate` is a function
    of the source and these, so a rebuild is possible rather than a re-ask).

    `automation_id` is the `automation.*` entity Home Assistant runs. It is empty
    for a record whose automation is not created yet, which is a real state: the
    record is written so the outputs exist, and the automation arrives after.

    `room_id` is where the module sits, and it is what a *slot* input resolves
    through: a slot names a device the room binds, so a module that reaches
    through one has to be somewhere for there to be an answer. Empty is the
    house itself -- a module that belongs to no room, whose slots resolve at
    house scope exactly as a pack's do. That is the right home for a module
    watching the whole house and the wrong one for a module watching a room,
    which is why the importer asks.
    """

    slug: str
    title: str
    #: The blueprint the module was imported from -- Home Assistant's own path
    #: for it, or empty when the document was pasted rather than picked.
    blueprint: str = ""
    #: The module *definition* this installation was made from, by name, or empty
    #: for a module hosted directly from a document.
    #:
    #: Empty is a real answer and not a missing one: a module a person pasted in,
    #: answered and hosted is an installation with no definition behind it, and
    #: will stay one. Where it is set it says which store row this came from,
    #: which is what lets the store say where a module is installed and what
    #: removing a definition would leave behind.
    definition: str = ""
    #: The room the module sits in, by area id, or empty for the house.
    room_id: str = ""
    #: The `automation.*` entity running this module, or empty when none does.
    automation_id: str = ""
    #: The blueprint's inputs as the person filled them, by input name.
    inputs: Mapping[str, Any] = field(default_factory=dict)
    #: What the module makes public, in the order a person ticked them.
    outputs: tuple[Output, ...] = ()

    # -- what it takes to build the module again --------------------------
    #
    # A module's settings stay settable after it is hosted, and changing one
    # means building the automation again from the same document with the new
    # answer -- so the document, and the person's answers, are kept. They are
    # kept *beside* `inputs` and `outputs` rather than instead of them because
    # the four answer different questions: these two are what the person chose,
    # and those two are what the automation ended up with, which is not always
    # the same tree (a device binding is written as `{entity_id: …}`, and an
    # output's expression is read out of the document).
    #
    #: The document the module was imported from, verbatim. For a blueprint this
    #: is the blueprint as Home Assistant resolved it, not a path to it, so a
    #: module keeps working when the blueprint is edited underneath it -- and
    #: re-importing is how a person takes an updated blueprint.
    source: str = ""
    #: The person's binding per input, by input name, as the panel sent it.
    #: Empty for an input they left on the blueprint's own default.
    bindings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Which of those inputs the module keeps *settable* -- its settings.
    #:
    #: A blueprint asks for a dozen things and a person hosting it usually wants
    #: to answer most of them once and never think about them again. The ones
    #: named here are the ones the module still shows, with the value the person
    #: gave as the value they start at; every other input is answered at import
    #: and then baked into the automation, and nobody sees it again. So this is
    #: the difference between "the module does it this way" and "the module is
    #: set up this way, and here is the dial".
    settings: tuple[str, ...] = ()
    #: The candidates they ticked, each with the output key they gave it. This
    #: is what `declare_outputs` is a function of, so keeping it is what lets a
    #: rebuild re-derive every output's expression from the current bindings
    #: rather than from an answer given when the settings were different.
    picks: tuple[tuple[str, str], ...] = ()
    #: The inputs answered with a **condition** rather than with a value, by input
    #: name: Home Assistant's own condition config, exactly as the person built
    #: it in the condition editor, kept verbatim.
    #:
    #: It is here and not in `bindings` because it is a different kind of thing.
    #: A binding is an answer; this is *logic*, which cannot be an answer: an
    #: automation's input can hold a template because a template is rendered
    #: where it lands, but a trigger's `entity_id` is matched against the real
    #: entities a house has and is never rendered -- so logic written there
    #: installs and never fires, silently. Open House therefore evaluates the
    #: condition itself and publishes the answer as a real entity
    #: (`module_host.derived_entity_id`), and it is *that* the input is bound to.
    #: Keeping the condition is what lets the entity be made, shown, and made
    #: again when the module is built again.
    derived: Mapping[str, Any] = field(default_factory=dict)
    #: The inputs answered **by a flow of nodes**, by input name: the id of the
    #: flow in Node-RED that was pushed for it.
    #:
    #: A third kind of thing, beside a binding and a condition. A flow is not an
    #: answer a person typed and not logic Open House evaluates -- it is logic
    #: that lives in another program, which writes into an entity Open House
    #: made for it (`module_host.flow_entity_id`, spelled from the module and the
    #: input like every other id here). So the input is bound to that entity.
    #:
    #: The *flow id* is kept because it is the one id in the pair that cannot be
    #: worked out again: Node-RED assigns it. Keeping it is what makes saving a
    #: setting update the flow the module already has rather than push a second
    #: one and leave the first running, writing into an entity nothing reads.
    flows: Mapping[str, str] = field(default_factory=dict)
    #: The inputs answered **by a Home Assistant script**, by input name: the
    #: `script.<id>` the module calls to get the value.
    #:
    #: The fourth kind of thing, and the only one that *returns*. A script is
    #: called with `response_variable:` and what it hands back is the value the
    #: input is built with (`module_host.script_binding`) -- nothing Open House
    #: made holds it, so there is no entity to publish and no template of its own
    #: to render. That is what makes this the cast for a computation that is a
    #: *sequence* rather than an expression: an if, a loop, a call to something
    #: else and a value handed back at the end.
    #:
    #: Kept as the id the person picked, because it is *their* script: the panel
    #: opens Home Assistant's own editor on it, and taking the cast away leaves
    #: the script where it is. Open House never writes one.
    scripts: Mapping[str, str] = field(default_factory=dict)

    # -- the same answers, several times over -----------------------------
    #
    # Everything above is *one* set of answers, and a module has only ever had
    # one. These two make it a set of sets: a placed module can be set up one way
    # in the evening and another way in the morning and be *switched* between
    # them, without becoming two modules with two automations and two sets of
    # entities to keep straight.
    #
    #: Every configuration this module holds, by name -- the active one included.
    #:
    #: Read this beside the five fields above rather than instead of them: those
    #: *are* `variants[variant]`, written out flat because they are what a
    #: builder reads and what every reader of this file already knew. The map is
    #: what keeps the configurations a module is *not* using from being lost, and
    #: `keeping_answers` is the single place the two are written together.
    #:
    #: Empty in a record made in memory that has not been built yet, and empty in
    #: a file written before this existed -- which reads back as one
    #: configuration called `DEFAULT_VARIANT` holding exactly the answers the
    #: file carried, because that is what such a module is.
    variants: Mapping[str, Variant] = field(default_factory=dict)
    #: The name of the configuration the five fields above are holding.
    variant: str = DEFAULT_VARIANT

    @property
    def configurations(self) -> tuple[str, ...]:
        """Every configuration this module holds, in the order they were made.

        The active one is in here even in a record that has only just been made
        and not yet built, so a screen showing a configuration can always point
        at it in the list of them.
        """
        names = list(self.variants)
        if self.variant not in names:
            names.insert(0, self.variant)
        return tuple(names)

    @property
    def held_configurations(self) -> Mapping[str, Variant]:
        """Every configuration, the active one read off the five fields above.

        `variants` is the source for the configurations that are *not* active,
        because those exist nowhere else; the active one is the five fields, and
        they win here -- which is the same rule `_record` reads a file by, and
        what makes an edit made on a screen and not yet saved still the thing a
        switch away from it writes back.

        A record that has only just been made has nothing in `variants` at all,
        and this is what gives it a name to answer to either way.
        """
        return {**self.variants, self.variant: Variant.of(self)}

    def __post_init__(self) -> None:
        if not self.variant.strip():
            raise AuthoringError(
                "a module's active configuration needs a name: without one there "
                "is nothing to call the answers the module is currently holding"
            )
        if not _KEY.match(self.slug):
            raise AuthoringError(
                f"{self.slug!r} is not a module name: a module is named lower "
                "case with underscores, because its name is part of the entity "
                "id each of its outputs lives at"
            )

    def as_json(self) -> Mapping[str, Any]:
        """This record as the plain tree the store writes."""
        return {
            "slug": self.slug,
            "title": self.title,
            "blueprint": self.blueprint,
            "definition": self.definition,
            "room_id": self.room_id,
            "automation_id": self.automation_id,
            "inputs": dict(self.inputs),
            "source": self.source,
            "bindings": {name: dict(row) for name, row in self.bindings.items()},
            "settings": list(self.settings),
            "picks": [list(pick) for pick in self.picks],
            # The conditions a person wrote, as Home Assistant's own config --
            # written out rather than summarised, because the entity is built
            # from them and a summarised condition would be an entity answering
            # a question nobody asked.
            "derived": dict(self.derived),
            # The flows, by input: the id of the flow that was pushed. Node-RED's
            # id and not one this integration can work out again, so it is
            # written out as it was given.
            "flows": dict(self.flows),
            # The scripts, by input: the id of the person's own script. Written
            # out as it was given, for the reason the flows are -- an id nobody
            # here can work out again.
            "scripts": dict(self.scripts),
            # The configurations, and which one the five answers above *are*.
            # Written in full rather than as a list of names, because this file is
            # the module's whole state: a name here with its answers nowhere would
            # be a configuration that cannot be switched back to after a restart.
            "variant": self.variant,
            "variants": {
                name: configuration.as_json()
                for name, configuration in self.held_configurations.items()
            },
            "outputs": [
                {
                    "key": output.key,
                    "expression": output.expression,
                    "kind": output.kind,
                    "variables": list(output.variables),
                    # Where the publisher goes, and whether the expression is
                    # already template text: both are read off the document the
                    # module was imported from, so keeping them is what makes the
                    # file a description of the automation rather than a summary
                    # of it.
                    "after": list(output.after),
                    "template": output.template,
                }
                for output in self.outputs
            ],
        }


@dataclass(frozen=True)
class Variant:
    """One configuration of a module: the person's answers, and only those.

    A module already keeps everything it takes to build itself again -- the
    document, and the answers a person gave about it. These six *are* those
    answers, and several of them side by side are what let one placed module be
    set up one way in the evening and another way in the morning without becoming
    two modules with two automations and two sets of entities to keep straight.

    Nothing else is here on purpose. `inputs` and `outputs` are what the
    automation *ended up* with, which is a function of these and the document --
    keeping those per configuration would be keeping one answer in two places
    that could disagree.
    """

    #: The person's binding per input, by input name, as the panel sent it.
    bindings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Which inputs this configuration keeps *settable*.
    settings: tuple[str, ...] = ()
    #: The output candidates that were ticked, each with the key it was given.
    picks: tuple[tuple[str, str], ...] = ()
    #: The inputs answered with a condition, by input name.
    derived: Mapping[str, Any] = field(default_factory=dict)
    #: The inputs answered by a Node-RED flow, by input name: the flow's id.
    flows: Mapping[str, str] = field(default_factory=dict)
    #: The inputs answered by a Home Assistant script, by input name: its id.
    scripts: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, record: ModuleRecord) -> Variant:
        """The configuration `record` is holding right now."""
        return cls(
            bindings=dict(record.bindings),
            settings=tuple(record.settings),
            picks=tuple(record.picks),
            derived=dict(record.derived),
            flows=dict(record.flows),
            scripts=dict(record.scripts),
        )

    def applied_to(self, record: ModuleRecord) -> ModuleRecord:
        """`record` answering this configuration in place of the one it holds.

        The record's name, room, source and automation are not touched: a
        configuration is *how* a module is set up, and switching between them is
        one module answering differently rather than a second module. That is the
        whole point -- the automation and the output entities are found by the
        module's name, so a switch rebuilds into the ones that are already there.
        """
        return replace(
            record,
            bindings=dict(self.bindings),
            settings=tuple(self.settings),
            picks=tuple(self.picks),
            derived=dict(self.derived),
            flows=dict(self.flows),
            scripts=dict(self.scripts),
        )

    def as_json(self) -> Mapping[str, Any]:
        """This configuration as the plain tree the store writes."""
        return {
            "bindings": {name: dict(row) for name, row in self.bindings.items()},
            "settings": list(self.settings),
            "picks": [list(pick) for pick in self.picks],
            "derived": dict(self.derived),
            "flows": dict(self.flows),
            "scripts": dict(self.scripts),
        }


def keeping_answers(record: ModuleRecord) -> ModuleRecord:
    """`record` with the answers it holds written back into its active configuration.

    The record's own five fields are the active configuration -- that is what
    makes every reader of a record a reader of the configuration somebody chose,
    rather than of a map each of them would have to remember to index -- and this
    is the one function that keeps the two in step.

    It is called where a module is *built* rather than where it is edited, because
    a build is where the answers become final: a slot-answered input is resolved
    to a device there, and an input nothing answered grows into a setting. So the
    moment a person's configuration is on file is the moment the module reflecting
    it exists, and there is no window where the two disagree.
    """
    return replace(
        record,
        variants={**record.variants, record.variant: Variant.of(record)},
    )


def slug(title: str) -> str:
    """A module name made out of a title a person chose.

    Lossy on purpose and in the open: `Lights: Evening Scene` becomes
    `lights_evening_scene`, because the name is part of an entity id and an
    entity id has no room for a colon. A title with nothing nameable in it --
    punctuation, or a script's own characters -- is refused rather than turned
    into an empty slug, because an empty name would put every one of its outputs
    at the same entity id as the next module's.
    """
    lowered = re.sub(r"[^a-z0-9]+", "_", title.strip().lower()).strip("_")
    if not _KEY.match(lowered):
        raise AuthoringError(
            f"{title!r} does not have a name in it that a module can be called: "
            "a module name is lower case letters, digits and underscores, "
            "beginning with a letter"
        )
    return lowered


def load(root: Path) -> tuple[ModuleRecord, ...]:
    """Every module this house hosts, or an empty tuple when it hosts none.

    A file that is not there is a house with no modules -- the first run -- and
    that is not a failure. A file that is there and will not parse *is* one, and
    it is raised rather than swallowed: the alternative is a startup that
    silently forgets every module a person made, with their outputs' entities
    gone and their consumers reading unknown, reported nowhere.
    """
    path = Path(root) / FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise AuthoringError(
            f"the module records at {path} could not be read: {error}"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise AuthoringError(
            f"the module records at {path} are not valid JSON: {error}"
        ) from error
    if not isinstance(document, Mapping):
        raise AuthoringError(f"the module records at {path} are not a mapping")
    rows = document.get("modules")
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise AuthoringError(f"the `modules` list at {path} is not a list")
    return tuple(_record(row, path) for row in rows)


def from_documents(
    rows: Sequence[object], *, path: Path | None = None
) -> tuple[ModuleRecord, ...]:
    """Records read from the rows of a document this module did not write.

    The one other reader of a module row: a house profile taken from a house
    carries the records that house was hosting, and putting the profile back is
    writing them again. They arrive as documents and not as records because a
    snapshot is a *file* -- it is stored, exported and read back -- so the shape
    that has to survive the trip is the written one, and this is the door that
    turns it back into the thing that can be written.

    Every row goes through `_record`, the same projection `load` uses, so a
    snapshot that was edited by hand is refused exactly as a records file would
    be rather than being written out as something a later load would reject.
    `path` is only ever for the refusal's message: a row that will not read is
    reported against the file it is on its way to, because that is the file a
    person can go and look at.
    """
    named = path if path is not None else Path(FILENAME)
    return tuple(_record(row, named) for row in rows)


def write(root: Path, records: Sequence[ModuleRecord]) -> Path:
    """Write every record, replacing the file, and answer with the path written.

    The whole file at once rather than an append, because the collection is the
    unit a reader wants: a half-written module list is one a person's outputs
    would be missing from. Blocking, by definition -- the caller runs it in an
    executor, since it is reached from a websocket handler.
    """
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    target = path / FILENAME
    document = {
        "version": VERSION,
        "modules": [record.as_json() for record in records],
    }
    target.write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return target


def put(
    records: Sequence[ModuleRecord], record: ModuleRecord
) -> tuple[ModuleRecord, ...]:
    """`records` with `record` in it: in the place its name already had, else last.

    The order of this file is the order the panel lists modules in, so this is
    the difference between a module keeping its place on the page when it is
    built again and that module jumping to the bottom of the house's list. It
    matters more than it looks: rebuilding is not rare -- every settings save and
    every slot binding rebuilds the modules that reach through it -- and a list
    that reshuffled itself each time would move the row a person was reading.
    """
    out: list[ModuleRecord] = []
    placed = False
    for row in records:
        if row.slug == record.slug:
            out.append(record)
            placed = True
        else:
            out.append(row)
    if not placed:
        out.append(record)
    return tuple(out)


def _record(row: object, path: Path) -> ModuleRecord:
    """One row of the stored document as a record, or a refusal naming the file.

    Every field is checked rather than trusted, because this file is one a person
    may edit by hand -- it is plain JSON beside the house, deliberately -- and a
    record that half-loaded would put an output's entity at a name no publisher
    agrees with.
    """
    if not isinstance(row, Mapping):
        raise AuthoringError(f"{path} carries a module that is not a mapping")
    name = row.get("slug")
    if not isinstance(name, str) or not _KEY.match(name):
        raise AuthoringError(f"{path} carries a module whose name is {name!r}")
    declared = row.get("outputs") or []
    if not isinstance(declared, list):
        raise AuthoringError(
            f"{path} carries {name!r} with outputs that are not a list"
        )
    outputs: list[Output] = []
    for item in declared:
        if not isinstance(item, Mapping):
            raise AuthoringError(
                f"{path} carries {name!r} with an output that is not a mapping"
            )
        key = item.get("key")
        if not isinstance(key, str) or not _KEY.match(key):
            raise AuthoringError(
                f"{path} carries {name!r} with an output called {key!r}, which is "
                "not an output name"
            )
        variables = item.get("variables") or []
        after = item.get("after") or []
        outputs.append(
            Output(
                key=key,
                expression=str(item.get("expression") or ""),
                kind=str(item.get("kind") or "string"),
                variables=tuple(str(variable) for variable in variables)
                if isinstance(variables, list)
                else (),
                # A list of keys and indices, in the order the walk found them,
                # and a `bool` that is `false` for most outputs -- written out
                # rather than defaulted because the file is the module's
                # definition and a reader of it should not have to know which
                # fields the writer leaves out.
                after=tuple(after) if isinstance(after, list) else (),
                template=bool(item.get("template")),
            )
        )
    inputs = row.get("inputs")
    answers = _answers(row)
    # Every configuration the file carries, with the one it names as active
    # written over from the record's own six fields -- those *are* the active
    # configuration (see the `variants` field), and a file written before this
    # existed has nothing else to seed from, so this is also what makes such a
    # file read back as a module with exactly one configuration holding exactly
    # the answers it already carried.
    active = str(row.get("variant") or DEFAULT_VARIANT)
    configurations = dict(_variants(row.get("variants")))
    configurations[active] = answers
    return ModuleRecord(
        slug=name,
        title=str(row.get("title") or name),
        blueprint=str(row.get("blueprint") or ""),
        definition=str(row.get("definition") or ""),
        room_id=str(row.get("room_id") or ""),
        automation_id=str(row.get("automation_id") or ""),
        inputs=dict(inputs) if isinstance(inputs, Mapping) else {},
        outputs=tuple(outputs),
        source=str(row.get("source") or ""),
        bindings=dict(answers.bindings),
        picks=tuple(answers.picks),
        settings=tuple(answers.settings),
        derived=dict(answers.derived),
        flows=dict(answers.flows),
        scripts=dict(answers.scripts),
        variants=configurations,
        variant=active,
    )


def _answers(row: Mapping[str, Any]) -> Variant:
    """The six things a person answers, out of a mapping that carries them.

    One function for both places they are written -- a record's own six fields
    and a named configuration -- because they are the same answers. Reading them
    two ways is how a file could come to carry a binding one reader accepts and
    another refuses.
    """
    bindings = row.get("bindings")
    picks = row.get("picks")
    settings = row.get("settings")
    derived = row.get("derived")
    flows = row.get("flows")
    scripts = row.get("scripts")
    return Variant(
        bindings={
            str(key): dict(value)
            for key, value in (
                bindings.items() if isinstance(bindings, Mapping) else ()
            )
            if isinstance(value, Mapping)
        },
        picks=tuple(
            (str(pick[0]), str(pick[1]))
            for pick in (picks if isinstance(picks, list) else ())
            if isinstance(pick, (list, tuple)) and len(pick) == 2
        ),
        settings=tuple(
            str(name) for name in (settings if isinstance(settings, list) else ())
        ),
        # Passed through as the person built it rather than checked: a condition
        # is Home Assistant's own shape and this module has no business holding a
        # second opinion about what one may contain. A row that is not a mapping
        # is dropped, because an input name is the key this is read by and a
        # condition under no name cannot be built from.
        derived=dict(derived) if isinstance(derived, Mapping) else {},
        # Read as strings under their input names, and a row that holds no id is
        # dropped rather than kept: an empty id names no flow, and an input bound
        # to a flow that does not exist is a module that waits for an answer
        # nothing will ever write.
        flows={
            str(name): str(flow)
            for name, flow in (flows.items() if isinstance(flows, Mapping) else ())
            if flow
        },
        # The person's own scripts, by input, read the same way and dropped the
        # same way. Nothing here writes, pushes or deletes one -- the id is a
        # reference to a script that lives in Home Assistant, so an id that names
        # nothing is a module that calls a script the person may simply have
        # renamed, and dropping it is how the module goes back to waiting rather
        # than failing to start.
        scripts={
            str(name): str(script)
            for name, script in (
                scripts.items() if isinstance(scripts, Mapping) else ()
            )
            if script
        },
    )


def _variants(value: object) -> tuple[tuple[str, Variant], ...]:
    """The named configurations a stored document carries, in the file's order.

    A row without a name is dropped rather than kept, for the same reason a
    condition without one is: the name is what a configuration is switched *by*,
    and answers nobody can name are answers nobody can choose between.
    """
    if not isinstance(value, Mapping):
        return ()
    return tuple(
        (str(name), _answers(body))
        for name, body in value.items()
        if str(name).strip() and isinstance(body, Mapping)
    )
