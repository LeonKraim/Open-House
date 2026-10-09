"""The modules a house hosts: making them, and the values they publish.

A hosted module is an imported blueprint that stayed a Home Assistant
automation. `ha_adapter.module_host` builds that automation -- it is pure, and it
is where the blueprint is resolved against a person's inputs and where the
make-public steps are appended -- and `ha_adapter.module_records` is the file the
contract is kept in. Neither may name a Home Assistant type. This module is the
part that must: it is where the automation is handed to Home Assistant to run,
where the entity a published output lives at is created, and where the service a
running automation calls is registered.

**The automation is created the way Home Assistant's own editor creates one.**
There is no public API for adding an automation from a component, and inventing
a second way to add one would be a way for a module to exist that the automation
editor cannot see, cannot edit and would delete on its next save. So this writes
`automations.yaml` through `homeassistant.config.AUTOMATION_CONFIG_PATH` --
exactly the file, exactly the shape and exactly the YAML reader and writer that
`components/config/automation.py` uses when a person clicks Save -- and then asks
Home Assistant to reload that one automation by the id it was given. The
blueprint's own body is validated by Home Assistant
(`async_validate_config_item`) on the way in, so a document Home Assistant would
refuse is a module this refuses, with Home Assistant's own reason.

**The module is only reported as hosted once it is really running.** After the
reload, the automation entity is looked for by its id, and its absence is a
refusal rather than a shrug: a house whose `configuration.yaml` does not include
`automations.yaml` would otherwise get a file nothing reads, a record claiming a
module, and a screen saying it worked. When that happens the entry is taken back
out of the file, so the one thing left behind is the message saying why.

**Publishing is a service, and it has to be.** A running automation cannot write
a `sensor`'s state in Home Assistant -- there is no `sensor.set_state` -- so an
output cannot simply be assigned. The make-public step calls
`open_house.publish_output`, that service writes the value onto the module's
runtime, and the output's entity redraws from it. This is the one shape that
makes an output both a real entity and something an automation writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.const import CONF_ID, SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import dump, load_yaml

from ha_adapter import (
    cast_document,
    module_definitions,
    module_host,
    module_records,
    slot_rules,
)
from ha_adapter.module_definitions import ModuleDefinition
from ha_adapter.module_host import InputBinding, config_id_for
from ha_adapter.module_records import (
    ModuleRecord,
    Variant,
    keeping_answers,
)
from ha_adapter.pack_authoring import AuthoringError

from . import node_red
from .const import (
    DEFINITIONS_DIRECTORY,
    DOMAIN,
    MODULES_DIRECTORY,
    SIGNAL_MODULE_REMOVED,
    SIGNAL_MODULES_CHANGED,
)

__all__ = [
    "ModuleHostError",
    "async_add_config",
    "async_define",
    "async_definition",
    "async_definitions",
    "async_deploy",
    "async_detach",
    "async_detach_slot",
    "async_edit",
    "async_export_definition",
    "async_host",
    "async_import_definition",
    "async_rebuild",
    "async_records",
    "async_register_services",
    "async_remove_config",
    "async_remove_definition",
    "async_rename_config",
    "async_switch_config",
    "async_unhost",
    "async_write_records",
    "config_id_for",
    "definitions_root",
    "records_path",
    "records_root",
]

_LOGGER = logging.getLogger(__name__)

#: The automation domain's own name, and the key `hass.data` holds its entity
#: component under. Spelled here rather than imported from the automation
#: integration, because importing a component to read a string is a dependency
#: this module does not need -- the component is guaranteed to be set up by the
#: time anything here runs, since the modules being hosted *are* automations.
AUTOMATION_DOMAIN = "automation"

#: Held across the read-modify-write of `automations.yaml`.
#:
#: The file is the automation editor's, and this integration is a second writer
#: of it. Without the lock two modules hosted at the same moment would each read
#: the file, each add their own entry and each write the whole list back -- so
#: the second write would drop the first module, and the person would have a
#: record for a module whose automation is not there. The lock serialises this
#: integration's own writes; Home Assistant's config view is not a participant,
#: which is the same seam Home Assistant itself writes that file across.
_MUTATION_LOCK = asyncio.Lock()


class ModuleHostError(HomeAssistantError):
    """A module could not be hosted, and why is in the message.

    A `HomeAssistantError` rather than a plain one so the websocket layer hands
    the panel the refusal's own sentence -- what a person reads -- rather than an
    internal error with a traceback beside it.
    """


def records_root(hass: HomeAssistant) -> Path:
    """The directory this house's module records live in, beside its packs."""
    return Path(hass.config.path(*MODULES_DIRECTORY))


def records_path(hass: HomeAssistant) -> Path:
    """The records file itself, for the one caller that has to name it: a log.

    `__init__` reports a records file it could not read, and "which file" is the
    first thing a person needs from that message. Reading it from the store's own
    constant rather than restating the name here is what keeps the report about
    the file the reader actually opened.
    """
    return records_root(hass) / module_records.FILENAME


async def async_records(hass: HomeAssistant) -> tuple[ModuleRecord, ...]:
    """Every module this house hosts. Off the loop, because it is a file read."""
    return await hass.async_add_executor_job(module_records.load, records_root(hass))


async def async_write_records(
    hass: HomeAssistant, records: Sequence[ModuleRecord]
) -> None:
    """Write the whole record list. Off the loop, for the same reason."""
    await hass.async_add_executor_job(
        module_records.write, records_root(hass), tuple(records)
    )


async def _async_amend_records(
    hass: HomeAssistant,
    amend: Callable[[tuple[ModuleRecord, ...]], tuple[ModuleRecord, ...]],
) -> tuple[ModuleRecord, ...]:
    """Read the records, change them, and write them back -- as one step.

    The whole read-modify-write is under `_MUTATION_LOCK`, not just the write,
    because the write is not the racy half: the file holds the *whole* list, so a
    change is `load`, `put`, `write`, and two of those interleaved lose one. Two
    modules hosted at once -- a person pressing twice, a profile activation
    rebuilding beside an edit -- both read the list before either writes, both add
    their own record, and the second write drops the first module: its automation
    is running and nothing records it. Serialising only the `write` would keep the
    file well-formed and still lose the same module, which is why the lock is taken
    here, around all three steps, and why every site that changes the list goes
    through this function rather than reaching for `async_records` and
    `async_write_records` itself.

    Nothing else holds `_MUTATION_LOCK` across a call to any of these sites -- the
    other two users (`_async_retire`, `_async_create_automation`) take it around a
    single edit to `automations.yaml` and let it go -- so `amend` may not call one
    of these sites back, and an `asyncio.Lock` that is not reentrant would deadlock
    on itself if it did.
    """
    async with _MUTATION_LOCK:
        amended = amend(await async_records(hass))
        await async_write_records(hass, amended)
        return amended


# --------------------------------------------------------------------------
# The store: the modules this house offers
#
# A *definition* is an import with the room taken out: the document, the answers
# it starts from, what it publishes, which of its inputs stay settable, and who
# wrote it. What is left is a module that is true of any room, which is what
# makes installing it one press instead of a second pass over the import screen --
# and what makes it one file, so handing it to another house is copying a file.
#
# `ha_adapter.module_definitions` is that file. This is the part that has to know
# where Home Assistant keeps its config directory, and the part that turns a
# definition plus a room into an installation -- which is the same act as hosting
# a document, because a definition *is* a document with its answers attached.
# --------------------------------------------------------------------------


def definitions_root(hass: HomeAssistant) -> Path:
    """The directory the modules this house offers live in, beside its packs."""
    return Path(hass.config.path(*DEFINITIONS_DIRECTORY))


async def async_definitions(hass: HomeAssistant) -> tuple[ModuleDefinition, ...]:
    """Every module this house offers. Off the loop, because it is a file read.

    A file that will not read is answered as a refusal rather than skipped, for
    the reason `module_records.load` gives about the records: a module that
    silently vanished from the store would be a module a person authored and can
    no longer see, with nothing anywhere saying why. The refusal names the file,
    which is the whole of what somebody needs to fix it.
    """
    try:
        return await hass.async_add_executor_job(
            module_definitions.load_all, definitions_root(hass)
        )
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal


async def async_definition(hass: HomeAssistant, module: str) -> ModuleDefinition:
    """One module this house offers, or a refusal naming it.

    Naming it is the point. The caller is a person who pressed a button on a row
    they were looking at, and the failure that matters is not a JSON error -- it
    is that this house does not offer it, which is what the message says.
    """
    for definition in await async_definitions(hass):
        if definition.slug == module:
            return definition
    raise ModuleHostError(
        f"this house offers no module called {module!r}: the store has changed "
        "since the list this was pressed from was drawn"
    )


async def async_define(
    hass: HomeAssistant,
    *,
    title: str,
    text: str,
    blueprint: str = "",
    description: str = "",
    author: str = "",
    version: str = "1.0.0",
    licence: str = "no_licence",
    bindings: Mapping[str, InputBinding] | None = None,
    outputs: Sequence[tuple[str, str]] = (),
    settings: Sequence[str] = (),
    replace: bool = False,
    casts: Mapping[str, Any] | None = None,
    flows: Sequence[str] = (),
    automations: Sequence[str] = (),
) -> ModuleDefinition:
    """Write down a module this house offers, and answer with what was written.

    **This is the import screen's last step, and it starts nothing.** Defining a
    module and installing one are separate acts on purpose: the definition is what
    a person decided the module *is* -- what it is called, what its inputs start
    at, what it publishes -- and installing it is putting that in a room. The
    separation is what makes the same module installable five times.

    Everything is checked before anything is written, by the definition's own
    constructor: the document has to read, every answer has to name an input the
    document asks for, every setting too. A file in the store is therefore a file
    that can be installed, and a file a person hands to somebody else is one that
    says what it is -- which is the whole of what makes the store worth having.

    `replace` is the only way to overwrite a module of the same name, because a
    definition is a thing a person authored: re-importing a blueprint they have
    edited is an update, and it has to be asked for rather than assumed.
    """
    try:
        source = module_host.read_module_source(text)
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    name = module_records.slug(title)
    offered = {definition.slug for definition in await async_definitions(hass)}
    if name in offered and not replace:
        raise ModuleHostError(
            f"this house already offers a module called {name!r}: define it again "
            "with replace to put this one in its place"
        )
    try:
        definition = ModuleDefinition(
            slug=name,
            title=title,
            source=text,
            # The blueprint's own prose when the screen did not supply any: a
            # module's description is the sentence the person who wrote the
            # blueprint wrote about it, and an empty one is a store row nobody
            # can choose from.
            description=description or source.description,
            blueprint=blueprint,
            author=author,
            version=version,
            licence=licence,
            bindings=_bindings_json(bindings),
            settings=tuple(settings),
            picks=tuple(outputs),
            derived=dict(casts or {}),
            # The *names* only: which inputs are answered by a flow is a fact
            # about the module, and the flow itself belongs to one house's
            # Node-RED. See `ModuleDefinition.flows`.
            flows=tuple(str(one) for one in flows if one),
            # The names only, for the reason the flows are: the automation that
            # answers it is made *here* on install, so the definition says *which
            # inputs are answered by an automation* and the installing house makes
            # the helper and the automation. See `ModuleDefinition.automations`.
            automations=tuple(str(name) for name in automations if name),
        )
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    await hass.async_add_executor_job(
        module_definitions.write, definitions_root(hass), definition
    )
    return definition


async def async_deploy(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    room_id: str = "",
    room_name: str = "",
    bindings: Mapping[str, InputBinding] | None = None,
    settings: Sequence[str] | None = None,
    bound: Mapping[str, str] | None = None,
    casts: Mapping[str, Any] | None = None,
    flows: Sequence[str] | None = None,
    automations: Sequence[str] | None = None,
) -> ModuleRecord:
    """Install a module this house offers into a room, or into the house.

    The definition is the starting point and the room is the answer sheet: what is
    sent here is laid over the definition's own answers, so installing into a room
    that answers one question differently does not restate the other eleven, and
    installing into a room that answers nothing gets exactly the module the house
    offers. Both are the same path -- a definition's answers are bindings, and
    hosting has always taken bindings.

    **Every room gets its own module, named after the definition and the room.**
    Two rooms running one definition are two automations with two sets of output
    entities, which is the whole point of installing it twice, and it is why the
    name carries the room: `dim_a_light` in the kitchen is `dim_a_light_kitchen`,
    so the two are told apart in the record, in each output's entity id and on the
    modules screen.

    **The room goes in the title as well as the name, and that is not decoration.**
    Home Assistant derives an automation's *entity id* from its alias, and it makes
    that id unique by numbering -- so two installations of one definition alias'd
    alike become `automation.dim_a_light` and `automation.dim_a_light_2`, told
    apart by nothing but the order they loaded in. The order is the file's, the
    file is rewritten on every change, and the numbering therefore moves: a house
    that swapped them would have the two rooms' automations change places in its
    own list. The config id underneath is a uuid and always was distinct; this is
    about the name a person reads. `room_name` is the room as it is shown rather
    than its id, so an installation reads "Dim a light (Front room)" and not
    "Dim a light (front_room)".
    """
    definition = await async_definition(hass, module)
    # What the room answers with a program by *name* is the definition's business
    # -- which inputs are answered by a program is a fact about the module -- and
    # the ids are the room's, so the same set is read twice for its two halves.
    # See `ModuleDefinition.automations`.
    installed = definition.with_answers(
        bindings=bindings,
        settings=settings,
        casts=casts,
        flows=flows,
        automations=automations,
    )
    name = (
        module_records.slug(f"{definition.slug} {room_id}")
        if room_id
        else definition.slug
    )
    try:
        return await async_host(
            hass,
            entry_id,
            text=installed.source,
            title=(
                f"{installed.title} ({room_name or room_id})"
                if room_id
                else installed.title
            ),
            name=name,
            definition=definition.slug,
            blueprint=definition.blueprint,
            room_id=room_id,
            bindings={
                input_name: InputBinding(**row)
                for input_name, row in installed.bindings.items()
            },
            outputs=installed.picks,
            settings=installed.settings,
            bound=bound,
            casts=installed.derived,
            flows=installed.flows,
            # The names, not the ids: Open House makes the helper and seeds the
            # automation on install, so the room names *which inputs* are
            # automation-answered and this house makes the half that answers them.
            automations=installed.automations,
        )
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal


async def async_remove_definition(
    hass: HomeAssistant, module: str
) -> tuple[ModuleDefinition, tuple[ModuleRecord, ...]]:
    """Take a module out of the store, and answer with what is still installed.

    **Removing the offer does not remove the modules made from it.** An
    installation carries its own copy of the document and its own answers -- which
    is what makes a store module independent of the blueprint it came from -- so a
    house that stops offering a module keeps running the rooms it installed it in.
    Those are answered rather than refused, so the screen can say how many rooms
    that is instead of a person finding out room by room.
    """
    definition = await async_definition(hass, module)
    await hass.async_add_executor_job(
        module_definitions.remove, definitions_root(hass), module
    )
    still = tuple(
        record
        for record in await async_records(hass)
        if record.definition == definition.slug
    )
    return definition, still


async def async_import_definition(
    hass: HomeAssistant, document: object, *, replace: bool = False
) -> tuple[ModuleDefinition, bool]:
    """Take a module file from somebody else, and answer with what was read.

    The one path a document from another house takes into this one, so the file
    is read by `module_definitions` -- which checks every field rather than
    trusting one -- and only then written. The second half of the answer is
    whether a module of that name was already here and has been replaced, because
    "imported" and "imported over the one you had" are different things to have
    done to a house.
    """
    try:
        definition = module_definitions.from_document(document)
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    offered = {row.slug for row in await async_definitions(hass)}
    replaced = definition.slug in offered
    if replaced and not replace:
        raise ModuleHostError(
            f"this house already offers a module called {definition.slug!r}: "
            "import it again with replace to take the incoming one instead"
        )
    await hass.async_add_executor_job(
        module_definitions.write, definitions_root(hass), definition
    )
    return definition, replaced


async def async_export_definition(
    hass: HomeAssistant, module: str
) -> Mapping[str, Any]:
    """One module as the file a person sends somebody else.

    The file the store holds, wrapped so it says what it is. Nothing is
    transformed on the way out, which is what makes an export and an import the
    same document: what a person downloads is what the store wrote, and what
    another house reads is what that house wrote.
    """
    return module_definitions.to_document(await async_definition(hass, module))


# --------------------------------------------------------------------------
# Hosting
# --------------------------------------------------------------------------


async def async_host(
    hass: HomeAssistant,
    entry_id: str,
    *,
    text: str,
    title: str,
    name: str = "",
    definition: str = "",
    blueprint: str = "",
    room_id: str = "",
    bindings: Mapping[str, InputBinding] | None = None,
    outputs: Sequence[tuple[str, str]] = (),
    settings: Sequence[str] = (),
    bound: Mapping[str, str] | None = None,
    casts: Mapping[str, Any] | None = None,
    flows: Sequence[str] = (),
    automations: Sequence[str] = (),
) -> ModuleRecord:
    """Host one imported source as a module, and answer with what was recorded.

    The whole of the import, in the order the pieces depend on each other: the
    source is read, the person's bindings are resolved into the values the
    blueprint's inputs take, the automation is instantiated from those and has
    its make-public steps appended, Home Assistant is asked to run it, and only
    then is the module recorded.

    **Nothing is written down until the automation is running**, which is what
    makes a record trustworthy: a record is a module the house *has*, so a step
    that fails leaves no record to be believed. The one thing a failure can leave
    behind is the automation entry, and the creation path takes that back out
    itself (see `_async_create_automation`).

    `outputs` is what the screen sent back -- each a candidate's own name and the
    key the person called it -- and it may be empty: a module with no outputs is
    a module, and its automation then runs exactly as the blueprint wrote it.

    `settings` is the other half of what the screen decided: the names of the
    inputs the module keeps *settable*. Everything else the blueprint asked for
    is answered here and then fixed inside the automation. The source and the
    person's bindings are stored with the record for the same reason -- a module
    whose settings can be changed is one that has to be built again when they
    are, and `async_update` is that rebuild.

    `room_id` is where the module sits, and `bound` is what that room answers
    with: the entity each slot name resolves to, the room's own binding and the
    house's behind it. Both are needed because an input may be answered with a
    *slot* rather than a device -- the person said "the room's lux sensor" and
    not "this one" -- and a slot is a promise the room has to keep. The record
    keeps the room and the slot name; the automation is built from what the room
    currently answers, and built again whenever that changes.

    The runtime is told about the module and the entity platform is signalled
    before this returns, so the outputs' entities exist by the time the panel
    reads the reply and by the time the automation's first publish arrives --
    which is the first time anything would notice they did not.

    `name` is normally the title, made into a module name, and it is passed
    separately only by the store: a definition installed into a room is named
    after the definition *and* the room, so the same module can run in five rooms
    without the five sharing one name, one automation and one set of outputs.
    `definition` is which store module this came from, empty for a document a
    person pasted in, which is provenance rather than a reference: the module runs
    the copy of the document it kept.

    `casts` is the third kind of answer, beside a binding and a setting: an input
    the person answered with a **condition** -- Home Assistant's own condition
    editor, the one automations and blueprints carry -- rather than with a value.
    It is not a binding because it is not a value: logic cannot be written into
    an input that a *trigger* names (Home Assistant matches a trigger's
    `entity_id` against the entities the house has and never renders it, so a
    condition there installs and never fires, silently). So Open House makes the
    condition into a real entity instead and binds the input to *that* -- see
    `binary_sensor.DerivedConditionSensor` for the making and `_async_build` for
    the binding -- and what is kept here is the condition it was made from.

    `flows` is the fourth: the inputs answered by a **flow of nodes** in Node-RED,
    by name. A flow is not a value either, and it is put to the same use as a
    condition -- an entity the input is bound to -- except that the entity is
    written by another program rather than evaluated by this one. `_async_push_flows`
    is the pushing and the recording.

    `automations` is the fifth: the inputs answered by a **Home Assistant
    automation**, by name. An automation is not a value either, and it is put to
    the same use as a flow -- an entity the input is bound to -- except that the
    writer is a Home Assistant automation of the person's own rather than a program
    in another add-on. Because an automation may set only what a service may set,
    the entity is a *helper* Open House makes (`module_host.helper_entity_id`), and
    Open House also seeds an automation that writes it (to a default, so the person
    sees the syntax and adds their own trigger). `_async_seed_automations` is the
    making and the recording, like `_async_push_flows` for a flow.
    """
    name = name or module_records.slug(title or _title_of(text))
    _refuse_a_different_module(name, blueprint, await async_records(hass))
    record = ModuleRecord(
        slug=name,
        title=title or _title_of(text),
        blueprint=blueprint,
        definition=definition,
        room_id=room_id,
        inputs={},
        source=text,
        bindings=_bindings_json(bindings),
        settings=tuple(settings),
        picks=tuple(outputs),
        derived=dict(casts or {}),
        # The id is filled in by the push below; an input named here is answered
        # by a flow whether or not the push has succeeded, and `_async_build`
        # binds it either way. What a failed push leaves out of the record is the
        # id, so the next save pushes a flow rather than updating one that was
        # never written.
        flows=dict.fromkeys((str(one) for one in flows if one), ""),
        # The ids are filled in by the seeding below, exactly as the flow ids are
        # filled in by the push: an input named here is answered by an automation
        # whether or not the seeding has run, and `_async_build` binds it either
        # way. What a failed seed leaves out is the id, so the next save makes the
        # automation rather than updating one that was never written.
        automations=dict.fromkeys((str(one) for one in automations if one), ""),
    )
    record = await _async_push_flows(
        hass, entry_id, record, dict(bindings or {}), bound
    )
    return await _async_build(hass, entry_id, record, bindings=bindings, bound=bound)


async def async_update(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    bindings: Mapping[str, InputBinding] | None = None,
    settings: Sequence[str] | None = None,
    bound: Mapping[str, str] | None = None,
    casts: Mapping[str, Any] | None = None,
    flows: Sequence[str] | None = None,
    automations: Sequence[str] | None = None,
) -> ModuleRecord:
    """Change a hosted module's settings, and answer with the module as it now is.

    **A module that is set up differently is built again, not patched.** The
    automation is a function of the source and the answers, and the way to change
    one of the answers is to run that function -- so this rebuilds into the same
    automation entry and the same output entities. Patching the numbers inside
    the built document instead would work for a plain value and quietly fail for
    everything else: an entity binding is a template, an output's expression is
    read from the document, and a value may be used in a dozen places the
    document wrote from it.

    Only the settings move. The bindings sent are merged over the ones on the
    record rather than replacing them, so a screen that shows only the exposed
    settings -- which is the whole point of exposing a subset -- cannot drop the
    answers the person gave to everything else. `settings` is replaceable too,
    because which of a module's values a person wants to keep at hand can change
    after they have lived with it.

    `casts`, `flows` and `automations` are the three kinds of *logic* one of those
    settings may be answered with instead of a value, and each is written onto the
    record before the rebuild for the reason the bindings are: the automation is
    built again from the record, so logic held only in the form that sent it would
    be logic the module does not have.
    """
    existing = {row.slug: row for row in await async_records(hass)}
    record = existing.get(module)
    if record is None:
        raise ModuleHostError(
            f"this house hosts no module called {module!r}, so there is nothing to set"
        )
    merged = {
        **{name: InputBinding(**row) for name, row in record.bindings.items()},
        **dict(bindings or {}),
    }
    if settings is not None:
        record = replace(record, settings=tuple(settings))
    if casts is not None:
        # Merged rather than replaced, for the same reason the bindings are: the
        # screen sends the casts it is showing, and a cast it is not showing --
        # because that input is not one of the module's settings -- must not be
        # dropped by a form that never knew about it.
        #
        # An *empty* condition sent for a name is how a cast comes back off:
        # `None`, `{}` and `[]` are all "nothing is written here", which is the
        # same on-demand rule the template box follows, and a name that is not
        # sent at all is one the screen is not showing and means keep.
        record = replace(
            record,
            derived={
                name: condition
                for name, condition in {**dict(record.derived), **dict(casts)}.items()
                if condition
            },
        )
    if automations is not None:
        # Sent rather than merged, and the *set* of inputs is what is sent rather
        # than a map of ids: an automation cast is a flow's twin -- Open House
        # makes the helper, seeds the automation and gives both a home here -- so
        # which inputs are answered this way is this house's set, and a name that
        # drops off it is a cast taken away. The ids on the record are looked up
        # rather than taken from the screen, for the reason the flows' are: what
        # the screen names is the input and what this house holds is the id.
        #
        # A name that drops off is taken out of the house as well -- the seeded
        # automation and the helper go -- for the reason a dropped flow is taken
        # out of Node-RED: both are this house's own objects, and one left behind
        # goes on writing an entity no input reads any more.
        #
        # ...unless another *configuration* names it, which is the flows' rule and
        # is here for the flows' reason: the set sent is the whole truth about the
        # configuration being edited and about no other, so a module switched to a
        # configuration that answers the input another way must not lose the first
        # configuration's automation, or switching back would land on an input
        # nothing writes.
        wanted = {str(one) for one in automations if one}
        elsewhere = {
            automation
            for other, configuration in record.variants.items()
            if other != record.variant
            for automation in configuration.automations.values()
        }
        for gone in [
            name
            for name, automation in record.automations.items()
            if name not in wanted and automation not in elsewhere
        ]:
            await _async_forget_automation(
                hass, record.automations[gone], _helper_id_of(record, gone)
            )
        record = replace(
            record,
            automations={
                name: record.automations.get(name, "") for name in sorted(wanted)
            },
        )
    if flows is not None:
        # The set of inputs answered by a flow is *sent* rather than merged, the
        # way a condition's answer is rather than the way a binding is: a row
        # showing "Node-RED" and a row showing something else are two different
        # rows, so the screen is showing every one of them and the set it sends
        # is the whole truth.
        #
        # A name that has dropped off the set is a flow taken away, and taken
        # away means taken out of Node-RED as well -- a flow left running in the
        # editor would go on calling `open_house.set_flow_value` for a module
        # whose input no longer reads it, which is work nobody can see the point
        # of and nobody can find.
        wanted = {str(one) for one in flows if one}
        # ...unless another *configuration* names it. The set sent is the whole
        # truth about the configuration being edited, and about no other: a
        # module switched to a configuration that answers that input some other
        # way must not take the first configuration's flow out of Node-RED, or
        # switching back would land on a module waiting for an answer nothing
        # writes any more.
        elsewhere = {
            flow
            for other, configuration in record.variants.items()
            if other != record.variant
            for flow in configuration.flows.values()
        }
        for gone in [
            name
            for name, flow in record.flows.items()
            if name not in wanted and flow not in elsewhere
        ]:
            await _async_forget_flow(hass, entry_id, record.flows[gone])
        record = replace(
            record,
            flows={name: record.flows.get(name, "") for name in sorted(wanted)},
        )
        record = await _async_push_flows(hass, entry_id, record, merged, bound)
    return await _async_build(hass, entry_id, record, bindings=merged, bound=bound)


async def async_detach(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    input_name: str,
    title: str = "",
    room_id: str = "",
    trigger: Sequence[str] = (),
    bound: Mapping[str, str] | None = None,
    source_bound: Mapping[str, str] | None = None,
) -> tuple[ModuleRecord, ModuleRecord, tuple[str, ...]]:
    """Give one row's cast a module of its own, and answer with both modules.

    **Both halves happen or neither does.** A detach is two writes: the logic
    becomes a module, and the row that held it comes to point at what that module
    publishes. Done separately they would be two chances to get half a change --
    a row still holding a cast whose logic is also a module (so the same thing is
    worked out twice, and the two can disagree), or a row pointing at a module
    that was never hosted (so the input reads an entity nothing writes, which is
    the one failure this codebase keeps designing against, because it is silent).
    So this is one function, and the panel is given one answer.

    **The row's cast is read from the record rather than from the screen.** The
    record is where the four kinds are kept -- `derived` for a condition, `flows`,
    `automations`, and the binding itself for a template -- and the screen's own view
    of them is derived from the same three fields. Reading the record means a
    detach can only ever act on logic the house *has*: a cast written into a form
    and not yet saved is a cast the module does not hold, which is the same rule
    a flow follows when it says to save first.

    `trigger` is what the person named to start the new module, where the cast
    cannot name it itself -- see `cast_document.detached_document`, which is
    where that judgement lives. `bound` is the *new* module's room's slots and
    `source_bound` the room's of the module the row is on: two rooms, because the
    placement is the person's choice and the module left behind still has to be
    built again where it lives.

    The third part of the answer is what the new module watches, which is the one
    thing about a detach a person cannot see from the row they pressed the button
    on: a condition's own entities are derived from the condition and were never
    typed as a trigger, so the screen is told them rather than left to read the
    new module to find out when it runs.
    """
    source_record = await _hosted_record(hass, module)
    answers = _answers_of(source_record)
    casts = module_host.cast_answers(
        answers,
        conditions=source_record.derived,
        flows=source_record.flows,
        automations=source_record.automations,
    )
    cast = casts.get(input_name)
    if cast is None:
        raise ModuleHostError(
            f"{input_name!r} is answered with a value rather than with logic, so "
            "there is nothing to detach: a condition, a template, a flow or an "
            "automation is what can become a module of its own"
        )
    alias = title or f"{source_record.title}: {input_name}"
    held = answers.get(input_name)
    try:
        detached = cast_document.detached_document(
            title=alias,
            input_name=input_name,
            cast=cast,
            template=str(held.value) if held is not None else "",
            condition=source_record.derived.get(input_name),
            flow_entity=cast_document.flow_entity_for(source_record.slug, input_name),
            # The helper the row is answered through, which the detached module
            # then reads: the row reads an entity a writer fills, and the new
            # module reads the same one -- so what the person's automation sets is
            # the value both of them have.
            automation_entity=_helper_id_of(source_record, input_name) or "",
            trigger=trigger,
        )
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    document = await async_host(
        hass,
        entry_id,
        text=dump(dict(detached.document)),
        title=alias,
        room_id=room_id,
        outputs=[detached.pick],
        bound=bound,
    )
    # The row now answers with what the new module publishes -- the `output`
    # binding that already exists for exactly this ("another module's published
    # value"), so a consumer of the row reads an entity a run writes rather than
    # logic worked out inside somebody else's run.
    #
    # The three records of logic go, and they have to: `_async_build` applies
    # `derived`, `flows` and `automations` *over* the person's answers, so a cast
    # left on the record would overwrite the binding written here and the row would
    # go on holding the logic it was just detached from.
    #
    # A flow is the one that needs saying out loud: it stays in Node-RED, where
    # somebody's nodes are still running and still writing the entity the detached
    # module now *follows*. So it is dropped from the record and not forgotten
    # from the editor -- forgetting it would delete the thing the new module
    # reads. What is left is a flow whose writer is one module's record and whose
    # reader is another's: honest about the direction of the data, and worth
    # knowing when the source module is later saved with the flows it shows.
    left = replace(
        source_record,
        derived={
            name: condition
            for name, condition in source_record.derived.items()
            if name != input_name
        },
        flows={
            name: flow
            for name, flow in source_record.flows.items()
            if name != input_name
        },
        # Dropped from the record and *not* forgotten from the house, which is the
        # flow's rule and is here for the flow's reason: the new module goes on
        # reading the helper, and the person's automation goes on writing it. What
        # the row stops holding is the cast, not the entity -- so taking the helper
        # or the automation away here would take away the thing the module just
        # detached into existence was made to read.
        automations={
            name: automation
            for name, automation in source_record.automations.items()
            if name != input_name
        },
    )
    merged = {
        **answers,
        input_name: InputBinding(
            kind="output", module=document.slug, key=detached.pick[1]
        ),
    }
    return (
        document,
        await _async_build(hass, entry_id, left, bindings=merged, bound=source_bound),
        detached.watched,
    )


async def async_detach_slot(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    slot: str,
    rule: slot_rules.SlotRule,
    title: str = "",
    room_id: str = "",
    trigger: Sequence[str] = (),
    bound: Mapping[str, str] | None = None,
) -> tuple[ModuleRecord, tuple[str, ...]]:
    """Give one slot's rule a module of its own.

    The same act as `async_detach` and the same reason for it: a cast is a small
    piece of logic attached to a row, and detaching it writes that logic into a
    document of its own so the row can stop holding it and point at what the
    module publishes instead. What differs is only *where the logic is kept*. An
    input's cast lives on the module's record (`derived`, `flows`, `automations` and
    the binding), so `async_detach` reads it there. A slot's rule lives in the
    session's settings (`ha_adapter.slot_rules`, written by
    `live_modules.set_slot_rule`) because a slot's *device* is per module and the
    settings are where that is recorded -- so the rule is handed in, read from the
    session by the caller that has one.

    **The caller finishes the job, and it is one synchronous step.** Detaching
    from an input points the row at the new module inside this function, because
    the row is a binding on a record this already rewrites. A slot's row is not:
    its rule and its entity are session settings, and the session belongs to the
    host. So this builds and hosts the module and answers what it watches, and the
    caller clears the rule and points the slot at `sensor.open_house_<slug>_<key>`
    -- no `await` between the two, which is what keeps it the one change the
    docstring of `async_detach` insists on: a slot still holding its rule *and* a
    module publishing it would work the same thing out twice, and the two could
    disagree.

    The *where* is `room_id` and the *what to call it* is `title`, exactly as in
    `async_detach`: they are the two things a cast cannot answer for itself.
    """
    if not slot:
        raise ModuleHostError("a detach needs to be told which slot's rule it is")
    alias = title or f"{module}: {slot}"
    # A script's `when` and a flow's entity are *in* the rule, and neither is the
    # `trigger` a person is asked for: a flow rule already names the entity it
    # follows (that is what the rule is) and a script rule already names what
    # calls it. So the person's list is added to the rule's own, which is the same
    # reading `detached_document` makes for an input.
    try:
        detached = slot_rules.detached(rule, slot=slot, title=alias, trigger=trigger)
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    document = await async_host(
        hass,
        entry_id,
        text=dump(dict(detached.document)),
        title=alias,
        room_id=room_id,
        outputs=[detached.pick],
        bound=bound,
    )
    return document, detached.watched


async def async_edit(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    text: str = "",
    title: str = "",
    blueprint: str = "",
    description: str = "",
    author: str = "",
    version: str = "",
    licence: str = "",
    bindings: Mapping[str, InputBinding] | None = None,
    outputs: Sequence[tuple[str, str]] = (),
    settings: Sequence[str] = (),
    casts: Mapping[str, Any] | None = None,
    flows: Sequence[str] = (),
    automations: Sequence[str] = (),
    bound: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[ModuleRecord, ...]:
    """Edit a module, and every installation of it follows.

    **This is the command that changes the module rather than a copy of it**, and
    that is the whole of what it is for. A hosted module and the thing it was made
    from are two layers -- what a house *runs* and what it *offers* -- and a person
    looking at the module in their kitchen has no way to tell them apart and no
    reason to care. So the card's Edit opens the import screen again on the
    module, and saving there is this: the document and the answers are written
    down again, and every room running it is built again from what was decided.

    **Which answers each room ends up with is the only interesting part**, and it
    is `module_definitions.follow`'s question: an answer a room never moved is the
    module's to change, and one the room did move is the room's. A module that
    starts publishing something new publishes it in every room; a room that
    pointed an input at its own lamp keeps its lamp.

    **A module hosted straight from a document has no definition behind it**, and
    no second installation either -- it *is* the whole module -- so it is edited
    where it stands. Nothing is written to the store: turning it into an offer is
    the separate act of defining it, and doing that here would put a row in
    somebody's store because they changed a setting.

    `bound` is what each room answers its slots with, by room id, because the
    installations being rebuilt are in different rooms and a module that reaches
    through a slot has to be built against the room it is in. Asking here rather
    than being sent one map is what lets an edit reach five rooms at once.
    """
    record = await _hosted_record(hass, module)
    rooms = dict(bound or {})
    if not record.definition:
        return await _async_rebuild_edited(
            hass,
            entry_id,
            [
                (
                    record,
                    replace(
                        record,
                        source=text.strip() or record.source,
                        title=title.strip() or record.title,
                        blueprint=blueprint or record.blueprint,
                        bindings=_bindings_json(bindings),
                        settings=tuple(settings),
                        picks=tuple(outputs),
                        derived=dict(casts or {}),
                        # The ids are kept by name, for the reason `async_update`
                        # keeps them: the name of a flow-answered input is the
                        # module's, and the id is this house's Node-RED's.
                        flows={
                            name: record.flows.get(name, "")
                            for name in _flow_names(flows)
                        },
                        # The ids are kept by name, for the flows' reason: the name
                        # of an automation-answered input is the module's, and the
                        # id is *this* house's -- Open House made the automation --
                        # so what the edit screen says is the whole truth about which
                        # input is answered this way, and an input it has just named
                        # has no id yet and is seeded by the build.
                        automations={
                            name: record.automations.get(name, "")
                            for name in _flow_names(automations)
                        },
                    ),
                )
            ],
            rooms,
        )
    before = await async_definition(hass, record.definition)
    after = _edited(
        before,
        text=text,
        title=title,
        blueprint=blueprint,
        description=description,
        author=author,
        version=version,
        licence=licence,
        bindings=bindings,
        outputs=outputs,
        settings=settings,
        casts=casts,
        flows=flows,
        automations=automations,
    )
    # Written before anything is built, so the store and the house are never two
    # different answers about what this module is. The construction above is what
    # validates -- a document that will not read, an answer naming no input -- and
    # it runs before this line, so a refusal here has written nothing at all.
    await hass.async_add_executor_job(
        module_definitions.write, definitions_root(hass), after
    )
    return await _async_rebuild_edited(
        hass,
        entry_id,
        [
            (
                row,
                replace(
                    module_definitions.follow(before, after, row),
                    title=_retitled(row, before.title, after.title),
                ),
            )
            for row in await async_records(hass)
            if row.definition == before.slug
        ],
        rooms,
    )


def _published(
    picks: Sequence[tuple[str, str]], setting: str, publish: bool
) -> tuple[tuple[str, str], ...]:
    """`module_definitions.published_picks`, with its refusal in this layer's words.

    The rule itself is the adapter's -- it is about candidate names and output
    keys, which is vocabulary that layer owns -- and it refuses in
    `AuthoringError`, as everything there does. What a screen reads is a
    `ModuleHostError`, so the two are told apart here, the same way `_async_build`
    tells them apart everywhere else.
    """
    try:
        return module_definitions.published_picks(picks, setting, publish)
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal


async def async_publish(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    setting: str,
    publish: bool,
    bound: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[ModuleRecord, ...]:
    """Publish one row's logic as a value any automation can read, or stop.

    **This is the whole of "expose it to the rest of the house", as one act.** A
    row answered with logic -- a template, a condition, a flow, an automation -- is
    already holding the thing worth reading, and Open House can already say so:
    `declare_outputs` offers each of them as a candidate, and what a module
    publishes is an ordinary entity (`sensor.open_house_<module>_<key>`). What
    that leaves is a person who wants it having to open the whole import screen
    again to tick one line of a list -- so this is that tick, alone, from the row
    it belongs to.

    It is an edit of the *module* and not of one copy, because a module is
    defined once and installed many times: a value it publishes is published in
    every room running it, or it is not the module's value at all. So both layers
    are written here -- the definition when the module has one, the record either
    way -- and `module_definitions.follow` decides what each installation ends up
    with, exactly as `async_edit` does.

    The key is the row's own name (see `module_definitions.published_key`) because
    a switch has nowhere to type one: a person who wants their outputs named
    differently is describing them, and that is the import screen.
    """
    record = await _hosted_record(hass, module)
    rooms = dict(bound or {})
    if not record.definition:
        # A module hosted straight from a document has no second installation
        # and nothing in the store: it *is* the module, so it is changed where
        # it stands. Same rule as the edit path, for the same reason.
        return await _async_rebuild_edited(
            hass,
            entry_id,
            [
                (
                    record,
                    replace(
                        record,
                        picks=_published(record.picks, setting, publish),
                    ),
                )
            ],
            rooms,
        )
    before = await async_definition(hass, record.definition)
    after = replace(before, picks=_published(before.picks, setting, publish))
    await hass.async_add_executor_job(
        module_definitions.write, definitions_root(hass), after
    )
    return await _async_rebuild_edited(
        hass,
        entry_id,
        [
            (row, module_definitions.follow(before, after, row))
            for row in await async_records(hass)
            if row.definition == before.slug
        ],
        rooms,
    )


async def _async_rebuild_edited(
    hass: HomeAssistant,
    entry_id: str,
    pairs: Sequence[tuple[ModuleRecord, ModuleRecord]],
    rooms: Mapping[str, Mapping[str, str]],
) -> tuple[ModuleRecord, ...]:
    """Build every edited installation again, taking back the flows that went.

    One rebuild per installation is the price of the edit being one module: the
    document may have changed under all of them, so there is no shared answer to
    build once. What is shared is the wiring -- a flow belongs to a *configuration*
    that names it, so a flow this edit has left no configuration naming comes out
    of Node-RED here, and the same flow still named elsewhere stays exactly where
    it is.
    """
    moved: list[ModuleRecord] = []
    for was, here in pairs:
        for flow in sorted(_flow_ids(was) - _flow_ids(here)):
            await _async_forget_flow(hass, entry_id, flow)
        bound = rooms.get(here.room_id)
        here = await _async_push_flows(hass, entry_id, here, _answers_of(here), bound)
        moved.append(
            await _async_build(
                hass, entry_id, here, bindings=_answers_of(here), bound=bound
            )
        )
    return tuple(moved)


def _edited(
    before: ModuleDefinition,
    *,
    text: str,
    title: str,
    blueprint: str,
    description: str,
    author: str,
    version: str,
    licence: str,
    bindings: Mapping[str, InputBinding] | None,
    outputs: Sequence[tuple[str, str]],
    settings: Sequence[str],
    casts: Mapping[str, Any] | None,
    flows: Sequence[str],
    automations: Sequence[str],
) -> ModuleDefinition:
    """`before` as the edit screen has just left it, or a refusal naming why not.

    The slug is the one thing the screen cannot change: a module's name is what
    every installation of it, every output's entity id and every flow in Node-RED
    is spelled from, so renaming it here would be renaming it in five rooms'
    automations rather than editing one module. Everything else is what was sent,
    falling back to what the module already said where nothing was -- so the
    fields the screen does not show (an author, a version) survive an edit rather
    than being quietly blanked.

    **The six answers have no fallback, and that is the difference.** A title
    nobody sent is a title the module already has; an empty set of bindings is a
    module with no answers at all, and it is a thing a person can mean. The
    command refuses a request that leaves one of them out rather than reading the
    two as the same, because for a field that arrives as a whole it cannot tell
    them apart.
    """
    return ModuleDefinition(
        slug=before.slug,
        title=title.strip() or before.title,
        source=text.strip() or before.source,
        blueprint=blueprint or before.blueprint,
        description=description.strip() or before.description,
        author=author.strip() or before.author,
        version=version.strip() or before.version,
        licence=licence or before.licence,
        bindings=_bindings_json(bindings),
        settings=tuple(settings),
        picks=tuple(outputs),
        derived=dict(casts or {}),
        flows=_flow_names(flows),
        # The names only, for the reason the flows are names: the id belongs to the
        # house that made it -- the automation and the helper are both this
        # house's, under ids spelled from these names -- and this definition
        # travels to every room, and to every house a file is sent to (`follow`).
        automations=_flow_names(automations),
    )


def _flow_names(flows: Sequence[str]) -> tuple[str, ...]:
    """The inputs a screen said are answered by a flow, in the order it said them."""
    return tuple(str(one) for one in flows if one)


def _flow_ids(record: ModuleRecord) -> set[str]:
    """Every Node-RED flow id any of a module's configurations names."""
    return {
        flow
        for configuration in record.held_configurations.values()
        for flow in configuration.flows.values()
        if flow
    }


def _retitled(record: ModuleRecord, was: str, now: str) -> str:
    """An installation's title with the module's new title in place of the old.

    An installation is titled after the module *and* the room -- "Dim a light
    (Front room)" -- because Home Assistant derives an automation's entity id from
    its alias, and two rooms running one module alias'd alike would be told apart
    by nothing but the order they loaded in (`async_deploy`). So an edit that
    renames the module renames the front of every installation's title and leaves
    the room's own half exactly as it was found.
    """
    if not was or not record.title.startswith(was):
        return now
    return f"{now}{record.title[len(was) :]}"


# --------------------------------------------------------------------------
# Configurations: the several sets of answers one placed module can hold
#
# A configuration is the person's own answers under a name they chose -- which
# is the five fields `_async_build` reads and nothing else. Everything else on
# a record is identity, provenance, or derived from those, so it is rebuilt
# rather than stored per configuration: switching is *the same act as editing*,
# a rebuild into the same automation entry and the same entities. Two
# configurations running side by side would need two slugs, and with two slugs
# they would be two modules.
#
# The record's own five flat fields are the active configuration's contents, and
# `keeping_answers` writes them back before anything moves them. That is the one
# place the invariant is kept, and it is what makes an edit made on a screen and
# not yet saved belong to the configuration it was made in.
# --------------------------------------------------------------------------

#: How long a configuration's name may be. A cap rather than a shape: the name
#: is the person's own text, shown in a select and sent back in a command, and
#: the only thing it must not be is more than a screen can hold.
CONFIG_NAME_LIMIT = 60


def _config_name(name: object) -> str:
    """A configuration's name as a person wrote it, or a refusal saying why not."""
    written = str(name or "").strip()
    if not written:
        raise ModuleHostError(
            "a configuration needs a name: it is the one thing that tells two of "
            "a module's sets of answers apart"
        )
    if len(written) > CONFIG_NAME_LIMIT:
        raise ModuleHostError(
            f"a configuration's name cannot be longer than {CONFIG_NAME_LIMIT} "
            f"characters, and {written!r} is {len(written)}"
        )
    return written


async def _hosted_record(hass: HomeAssistant, module: str) -> ModuleRecord:
    """The record the house hosts under `module`, or a refusal naming it."""
    for record in await async_records(hass):
        if record.slug == module:
            return record
    raise ModuleHostError(
        f"this house hosts no module called {module!r}, so it has no configurations"
    )


def _answers_of(record: ModuleRecord) -> Mapping[str, InputBinding]:
    """The person's answers as bindings, from the rows the record keeps."""
    return {name: InputBinding(**row) for name, row in record.bindings.items()}


def _named(variants: Mapping[str, Variant], name: str) -> str | None:
    """The configuration already called `name`, ignoring case, if there is one.

    Ignoring case, because "Winter" and "winter" are two rows on a screen that no
    person reading it can tell apart, and which one was meant is not a thing this
    could work out.
    """
    wanted = name.casefold()
    return next((key for key in variants if key.casefold() == wanted), None)


async def _async_store(
    hass: HomeAssistant, entry_id: str, record: ModuleRecord
) -> ModuleRecord:
    """Write one module's record and give it its live home, without rebuilding.

    The half of a change that moves no answers: a renamed or dropped
    configuration is a different *name* over the same module, so the automation
    is already the one it should be, and building it again would be work nobody
    could see the point of.

    `put` rather than appending, so the module keeps its place in the house's
    list: the file's order is the order the panel draws modules in, and renaming
    a configuration should not move the card somebody was reading to the bottom
    of the page.
    """
    await _async_amend_records(
        hass, lambda records: module_records.put(records, record)
    )
    _attach(hass, entry_id, record)
    return record


async def async_switch_config(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    config: str,
    bound: Mapping[str, str] | None = None,
) -> ModuleRecord:
    """Make one of a module's configurations the one the module is running.

    A configuration *is* the person's answers, so switching is the same act as
    editing them: the module is built again from the named answers, into the same
    automation entry and the same entities. Nothing is stopped or started beside
    anything -- there is one module, holding different answers than it held a
    moment ago.

    What the module is holding now is written back to the configuration it came
    from first, so a value changed on the screen and not yet saved is not thrown
    away by switching away from it.
    """
    record = await _hosted_record(hass, module)
    name = _config_name(config)
    target = record.variants.get(name)
    if target is None:
        raise ModuleHostError(f"{module!r} has no configuration called {name!r}")
    moved = replace(target.applied_to(keeping_answers(record)), variant=name)
    # A flow-answered input's flow watches the entity *this* configuration
    # answers that input with, and two configurations may answer the same input
    # with two different entities -- so the incoming one pushes its own flows,
    # over the same flow ids, which is what makes switching back and forth leave
    # one flow rather than a pile of them.
    moved = await _async_push_flows(hass, entry_id, moved, _answers_of(moved), bound)
    return await _async_build(
        hass, entry_id, moved, bindings=_answers_of(moved), bound=bound
    )


async def async_add_config(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    config: str,
    bound: Mapping[str, str] | None = None,
) -> ModuleRecord:
    """Start a new configuration from the running one, and switch the module to it.

    A copy rather than a blank, because the way to a second configuration is to
    change one value of the first: a blank one would be a module with every input
    unanswered, which is not a thing anybody wants a second of.

    The copy's flows are the ones the configuration it came from already pushed,
    which remain correct -- a flow watches the entity its own input's answer
    names, and the answers are the ones just copied.
    """
    record = await _hosted_record(hass, module)
    name = _config_name(config)
    if _named(record.variants, name) is not None:
        raise ModuleHostError(f"{module!r} already has a configuration called {name!r}")
    holding = keeping_answers(record)
    copied = replace(
        holding,
        variant=name,
        variants={**holding.variants, name: holding.variants[holding.variant]},
    )
    # Built rather than merely stored: the copy is the active configuration from
    # this moment, and one that is active but not built would be a module whose
    # automation still holds the answers of the configuration it came from.
    return await _async_build(
        hass, entry_id, copied, bindings=_answers_of(copied), bound=bound
    )


async def async_rename_config(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    config: str,
    to: str,
) -> ModuleRecord:
    """Give one of a module's configurations a different name.

    Nothing about the module moves -- same answers, same automation, same
    entities -- so this is a write and not a build. The name is what the switcher
    shows and what the commands address, and that is the whole of what changes.
    """
    record = await _hosted_record(hass, module)
    name = _config_name(config)
    wanted = _config_name(to)
    if name not in record.variants:
        raise ModuleHostError(f"{module!r} has no configuration called {name!r}")
    clash = _named(record.variants, wanted)
    if clash is not None and clash != name:
        raise ModuleHostError(
            f"{module!r} already has a configuration called {clash!r}"
        )
    holding = keeping_answers(record)
    moved = replace(
        holding,
        variant=wanted if holding.variant == name else holding.variant,
        variants={
            (wanted if key == name else key): value
            for key, value in holding.variants.items()
        },
    )
    return await _async_store(hass, entry_id, moved)


async def async_remove_config(
    hass: HomeAssistant,
    entry_id: str,
    *,
    module: str,
    config: str,
    bound: Mapping[str, str] | None = None,
) -> ModuleRecord:
    """Drop one of a module's configurations, and any flow only it named.

    **The last one cannot go.** A module with no configurations is a module with
    no answers, and there is no such state to be in: a placed module is always
    running something, and the command that means "stop running it" is
    `async_unhost`, which takes the module out of the house.

    Dropping the active one leaves another running, so the module moves to it the
    way a switch does. Dropping an inactive one is a write and no more. A flow
    the dropped configuration was the last to name comes out of Node-RED with it;
    one another configuration still names stays, because it is that
    configuration's flow and switching back to it has to find one.
    """
    record = await _hosted_record(hass, module)
    name = _config_name(config)
    holding = keeping_answers(record)
    if name not in holding.variants:
        raise ModuleHostError(f"{module!r} has no configuration called {name!r}")
    if len(holding.variants) == 1:
        raise ModuleHostError(
            f"{module!r} has only one configuration, called {name!r}: a module "
            "always runs a set of answers, so the way to stop running this one is "
            "to take the module out of the house"
        )
    dropped = holding.variants[name]
    remaining = {key: value for key, value in holding.variants.items() if key != name}
    elsewhere = {
        flow
        for configuration in remaining.values()
        for flow in configuration.flows.values()
    }
    for flow in dict.fromkeys(dropped.flows.values()):
        if flow and flow not in elsewhere:
            await _async_forget_flow(hass, entry_id, flow)
    # An automation-answered input the dropped configuration was the last to name
    # goes the same way, and by the same rule: the automation and the helper were
    # made for *that* row, and one another configuration still names stays, because
    # it is that configuration's and switching back to it has to find one.
    named_elsewhere = {
        automation
        for configuration in remaining.values()
        for automation in configuration.automations.values()
    }
    for gone, automation in dropped.automations.items():
        if automation and automation not in named_elsewhere:
            await _async_forget_automation(
                hass, automation, _helper_id_of(holding, gone)
            )
    if name != holding.variant:
        return await _async_store(hass, entry_id, replace(holding, variants=remaining))
    # The active one is the one going, so the module takes up the first that is
    # left -- the order the person made them in -- and is built from it.
    pickup = next(iter(remaining))
    moved = replace(
        remaining[pickup].applied_to(replace(holding, variants=remaining)),
        variant=pickup,
    )
    moved = await _async_push_flows(hass, entry_id, moved, _answers_of(moved), bound)
    return await _async_build(
        hass, entry_id, moved, bindings=_answers_of(moved), bound=bound
    )


async def _async_seed_automations(
    hass: HomeAssistant, record: ModuleRecord, source: module_host.HostedSource
) -> ModuleRecord:
    """Make what answers every automation-cast input: a helper, and an automation.

    **Two objects per row, and both are seeded rather than owned.** The helper is
    made once, holding the blueprint's own default; the automation is written once,
    with an empty trigger and the working action that sets the helper. After that
    they are the person's -- the helper is in Home Assistant's Helpers list and the
    automation in the editor the row embeds -- and nothing here writes over either
    one again.

    **The record's own id is the record of having seeded.** A name the record
    already carries an id for has been seeded, so a build that happens because a
    room rebound a slot costs a dictionary lookup rather than a look at the house;
    a name with no id has no automation, and both are made. That is also what makes
    a failed seed recoverable: the id is written last, so a build that fails
    half-way left the name open rather than half-answered.

    Seeding is *not* skipped for a module that is waiting for a device: the helper
    and the automation are about the row, not about the run, and a person may want
    to write their automation while the module is still waiting for its room.

    The id is this house's the way a flow id is -- Open House makes the automation,
    so the record keeps its id -- and the input is bound from the helper, which is
    spelled from the same two names (`module_host.helper_entity_id`).
    """
    if not record.automations:
        return record
    made = dict(record.automations)
    for name in record.automations:
        if made.get(name):
            continue
        block = source.inputs.get(name)
        if block is None:
            # The same case the flow loop refuses: a record that names an input
            # the document does not declare, which only happens if somebody edited
            # the source out from under it. Making a helper for an input that does
            # not exist would be an entity nothing reads.
            raise ModuleHostError(
                f"{record.slug!r} has an automation for an input called {name!r}, "
                "and the blueprint it was imported from declares no such input"
            )
        try:
            helper = module_host.helper_for(record.slug, name, block)
        except AuthoringError as refusal:
            raise ModuleHostError(str(refusal)) from refusal
        await _async_make_helper(hass, helper)
        await _async_create_automation(
            hass,
            dict(module_host.helper_automation(record.slug, name, helper)),
            module=record.slug,
        )
        made[name] = module_host.helper_config_id(record.slug, name)
    return replace(record, automations=made)


async def _async_make_helper(hass: HomeAssistant, helper: module_host.Helper) -> None:
    """Make one helper, once, and leave it alone ever after.

    Nothing is written where the helper already is. The id is Open House's own
    spelling of the module's and the input's name, and a person who changed the
    helper's value, its bounds or its name in Home Assistant's own screens keeps
    every one of those: this is the maker, not the keeper.

    **A helper that was made and did not appear is refused**, rather than left as
    an input answered by an entity nothing made -- which is the failure this whole
    layer designs against, because it is silent. The check is the entity registry
    rather than the state machine, so a helper that is known but unavailable still
    counts as made.
    """
    if er.async_get(hass).async_get(helper.entity_id) is not None:
        return
    collection = _helper_collection(hass, helper.domain)
    if collection is None:
        raise ModuleHostError(
            f"this instance cannot make a {helper.domain} helper, and "
            f"{helper.entity_id} is what this module's input is answered "
            "through: Home Assistant's own helpers are the entities an "
            "automation may set"
        )
    try:
        await collection.async_create_item(dict(helper.data))
    except Exception as refusal:
        raise ModuleHostError(
            f"Home Assistant would not make the helper {helper.entity_id} that "
            f"this module's input is answered through: {refusal}"
        ) from refusal
    if er.async_get(hass).async_get(helper.entity_id) is None:
        raise ModuleHostError(
            f"the helper {helper.entity_id} was asked for and did not appear, so "
            "this module's input would read an entity nothing made"
        )


def _helper_collection(hass: HomeAssistant, domain: str) -> Any | None:
    """The live helper collection for `domain`, or `None` if there is not one.

    **Home Assistant offers no way to make a helper from Python, and this is the
    way in.** The `input_*` components are storage-backed collections, and the
    collection is made inside the component's own `async_setup` and handed to
    nothing -- not `hass.data`, not any helper module -- so there is no attribute
    to read it from. What *is* reachable is the websocket command the frontend's
    own Helpers screen calls: one of the four commands registered for a collection
    (`<domain>/list`) is that collection's own bound method, so the object behind
    it is the collection, and `async_create_item` on it is the very call the
    screen's Create button makes.

    Reaching through a bound method is ugly, and it is deliberate. The alternative
    is a second way to make a helper, and a helper Home Assistant did not make is
    one its own screens would not list, would not edit and would not keep -- which
    is the whole of what a helper is here. Every way of not finding it answers
    `None`, and the caller refuses in words that say so rather than writing an
    entity nobody owns.
    """
    commands = hass.data.get("websocket_api")
    if not isinstance(commands, dict):
        return None
    registered = commands.get(f"{domain}/list")
    if not isinstance(registered, tuple) or not registered:
        return None
    collection = getattr(getattr(registered[0], "__self__", None), "storage_collection", None)
    return collection


async def _async_forget_automation(
    hass: HomeAssistant, config_id: str, entity_id: str | None
) -> None:
    """Take one automation-cast input's seeding back out of the house.

    Two objects, because the row had two, and each is answered the way its twin
    is. The **automation** goes the way a module's own does (`_async_retire`):
    withdrawn from `automations.yaml` by its id and reloaded, with a reload that
    finds nothing swallowed rather than raised, because "there was nothing to stop"
    is the answer this wanted either way. The **helper** goes like a flow
    (`_async_forget_flow`): a failure is logged and swallowed, because the input
    has already stopped reading it by the time this runs and refusing the settings
    change somebody asked for would be a worse answer than a stray entity and a
    line in the log.

    The two are passed in rather than read off a record, because a caller dropping
    a *configuration* holds a `Variant` and a caller dropping an input holds a
    record, and the two ids are the whole of what this needs.

    **The cost is real and worth naming**: a helper a person came to use somewhere
    else goes with the row. It is taken away only where it is still spelled as
    Open House's own -- found in the registry by the id this would have made -- so
    a person who moved it out of the way keeps it.
    """
    if not config_id:
        return
    await _async_withdraw_automation(hass, config_id)
    if entity_id is not None:
        await _async_drop_helper(hass, entity_id)


def _helper_id_of(record: ModuleRecord, name: str) -> str | None:
    """The helper an input is answered through, or `None` where that cannot be read.

    Read from the module's own document rather than kept on the record, because
    the document is what says which *kind* of helper the row takes -- and a record
    that kept a second copy of it would be a record that could disagree with the
    blueprint about what the row is.
    """
    try:
        source = module_host.read_module_source(record.source)
        block = source.inputs.get(name)
        if block is None:
            return None
        return module_host.helper_entity_id(record.slug, name, block)
    except AuthoringError:
        return None


async def _async_drop_helper(hass: HomeAssistant, entity_id: str) -> None:
    """Delete one helper Open House made, if it is still where it was left."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or not entry.unique_id:
        return
    collection = _helper_collection(hass, entity_id.split(".", 1)[0])
    if collection is None:
        return
    try:
        await collection.async_delete_item(entry.unique_id)
    except Exception as failure:  # noqa: BLE001
        _LOGGER.warning("a helper could not be removed from Home Assistant: %s", failure)


async def _async_withdraw_automation(hass: HomeAssistant, config_id: str) -> None:
    """Take one entry out of `automations.yaml`, and reload what it was.

    Shared by every removal, because they are the same two steps: the file write,
    which is the only way to change what Home Assistant reads, and the reload of
    that one id, which is what makes the running automation go. A reload that
    finds nothing is the ordinary case rather than a failure -- a module that never
    ran, a seeding that was taken back twice.
    """
    path = Path(hass.config.path(AUTOMATION_CONFIG_PATH))
    async with _MUTATION_LOCK:
        await hass.async_add_executor_job(_withdraw, path, config_id)
    with contextlib.suppress(HomeAssistantError):
        await hass.services.async_call(
            AUTOMATION_DOMAIN, SERVICE_RELOAD, {CONF_ID: config_id}, blocking=True
        )


async def _async_forget_flow(hass: HomeAssistant, entry_id: str, flow_id: str) -> None:
    """Take one flow out of Node-RED, if this instance has a Node-RED at all.

    A failure here is logged and swallowed, which is the one place in this module
    that does that. The reason is that the flow is *already* unreachable from the
    house by the time this runs: the record no longer names it, the input no
    longer reads the entity it writes, and nothing will ever read it again. What
    is left over is a tab in somebody's editor, and refusing the settings change
    they asked for -- which has already been decided and has nothing to do with
    Node-RED -- would be a worse answer than a stray flow and a line in the log
    saying so.
    """
    if not flow_id:
        return
    entry = hass.config_entries.async_get_entry(entry_id)
    client = node_red.async_client(hass, {} if entry is None else entry.options)
    if client is None:
        return
    try:
        await node_red.async_delete_flow(client, flow_id)
    except node_red.NodeRedError as failure:
        _LOGGER.warning("a flow could not be removed from Node-RED: %s", failure)


async def async_rebuild(
    hass: HomeAssistant,
    entry_id: str,
    module: str,
    *,
    bound: Mapping[str, str] | None = None,
) -> ModuleRecord | None:
    """Build a hosted module again from the answers it already has, and record it.

    This is what a *rebinding* calls: a module that reaches through a slot has
    its automation built from whatever the room answers, so pointing the slot at
    a different device is a change the automation has to be told about. Nothing
    about the person's own answers moves -- the bindings and the settings are the
    ones on the record -- which is the whole difference between this and
    `async_update`: a setting is a choice somebody made again, this is the world
    under an unchanged choice moving.

    `None` when the house hosts no such module, rather than a refusal: the caller
    is walking the modules a room has, and one that has since been removed is a
    module to skip rather than an error to raise in the middle of the walk.
    """
    for record in await async_records(hass):
        if record.slug != module:
            continue
        bindings = {name: InputBinding(**row) for name, row in record.bindings.items()}
        return await _async_build(
            hass, entry_id, record, bindings=bindings, bound=bound
        )
    return None


async def async_unhost(hass: HomeAssistant, entry_id: str, module: str) -> ModuleRecord:
    """Take a module out of the house: stop it, forget it, and drop its outputs.

    Three things, and they are three because a module is three. The automation
    goes so nothing it runs goes on calling `open_house.publish_output`; the
    record goes so the house no longer lists it, and so a later import of a
    blueprint of the same name is a fresh module rather than a replacement of
    one; the runtime entry goes so its outputs stop existing, which is the half
    the signal below carries -- the entity platform is what makes them and it is
    the only thing that can take them away.

    **A module that is still waiting is unhosted by the same path.** It has no
    automation to stop and no values to lose, and `_async_retire` treats a
    missing entry as the ordinary case rather than a failure, which is what makes
    one path right for both.

    This is *not* removing the store module the installation came from. A
    definition is what the house offers and an installation is a room running it;
    taking one copy out of one room leaves the offer and every other copy alone.
    """
    records = await async_records(hass)
    found = next((record for record in records if record.slug == module), None)
    if found is None:
        raise ModuleHostError(f"this house hosts no module called {module!r}")
    # The flows first, while the record still names them: after the write below
    # there is nothing left that says which flow belonged to this module, and a
    # flow nobody can name is a flow nobody can take away. **Every
    # configuration's**, not only the active one's: unhosting takes the module
    # out of the house entirely, so a flow left in the editor for a
    # configuration that no longer exists anywhere is a flow calling
    # `open_house.set_flow_value` for an input nothing reads.
    for flow_id in dict.fromkeys(
        [
            *found.flows.values(),
            *(
                flow
                for configuration in found.variants.values()
                for flow in configuration.flows.values()
            ),
        ]
    ):
        await _async_forget_flow(hass, entry_id, flow_id)
    # The seeded automations and their helpers next, and for the same reason the
    # flows went first: after the write below nothing says which of them belonged
    # to this module. Every configuration's, not only the active one's, by the rule
    # above -- and the helper is taken with each, because it was made for that row
    # and an entity nothing reads is worse than one that was never made.
    for name, automation in dict.fromkeys(
        [
            *found.automations.items(),
            *(
                pair
                for configuration in found.variants.values()
                for pair in configuration.automations.items()
            ),
        ]
    ):
        await _async_forget_automation(hass, automation, _helper_id_of(found, name))
    await _async_retire(hass, module)
    await _async_amend_records(
        hass, lambda all_records: tuple(r for r in all_records if r.slug != module)
    )
    runtime = hass.data.get(DOMAIN, {}).get(entry_id)
    if runtime is not None:
        runtime.modules.pop(module, None)
    async_dispatcher_send(hass, SIGNAL_MODULE_REMOVED, module)
    return found


async def _async_build(
    hass: HomeAssistant,
    entry_id: str,
    record: ModuleRecord,
    *,
    bindings: Mapping[str, InputBinding] | None,
    bound: Mapping[str, str] | None = None,
) -> ModuleRecord:
    """Build `record`'s automation from its source and bindings, and record it.

    Shared by hosting and by setting, because they are the same act: the only
    difference between importing a module and changing one of its settings is
    whether there is an automation there already, and that difference is entirely
    inside `_append` -- the entry is keyed by the module's name, so a rebuild
    lands on the automation it came from.
    """
    if not record.source:
        raise ModuleHostError(
            f"the record for {record.slug!r} does not keep the document it was "
            "imported from, so it cannot be built again: import it again from "
            "the blueprint"
        )
    # The person's answers, with every slot turned into the device their room
    # binds for it. A slot nothing has bound yet contributes no value at all --
    # a slot *name* must never be written where a device id belongs -- and is
    # found instead by `unresolved_slots`, which is the branch below that records
    # a module without running it.
    answered = dict(bindings or {})
    # A condition-cast input is bound to the entity Open House makes for it. The
    # condition itself cannot go into the input: logic is rendered where it
    # lands in an automation's body but a trigger's `entity_id` is *matched*
    # against the entities the house has, so a condition written there installs
    # and never fires and says nothing while it does not. So the answer is a real
    # binary sensor and the input points at it -- applied over the person's own
    # bindings because a cast *is* the whole answer, exactly as the template box
    # beside it is.
    for name in record.derived:
        answered[name] = InputBinding(
            kind="entity", value=module_host.derived_entity_id(record.slug, name)
        )
    # A flow-cast input is bound to the entity the flow writes, by the same rule
    # `_bound_value` applies to every other binding: an input that takes a device
    # is given the entity's id, and one that takes a value is given a template
    # reading it, typed the way the input expects. Both are `module_host`'s
    # answer and not a second spelling of it here.
    try:
        source = module_host.read_module_source(record.source)
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    for name in record.flows:
        block = source.inputs.get(name)
        if block is None:
            # A flow recorded for an input the document no longer declares. The
            # record outlives the blueprint only if somebody edited the source
            # out from under it; binding to nothing would be a value written
            # into an input that does not exist.
            raise ModuleHostError(
                f"{record.slug!r} has a Node-RED flow for an input called "
                f"{name!r}, and the blueprint it was imported from declares no "
                "such input"
            )
        answered[name] = module_host.binding_to_entity(
            block, module_host.flow_entity_id(record.slug, name)
        )
    # An automation-cast input is bound to the *helper* the person's automation
    # writes, by exactly the rule a flow-cast input is bound to the entity its
    # flow writes -- one function in `module_host` for the one decision, because
    # the two casts differ in nothing but who the writer is. The helper is made
    # *first*: the seeding below is what makes it, and an input bound to an entity
    # nothing made is the one failure this layer keeps designing against.
    #
    # Seeded rather than made, and the record is what says so: a name the record
    # already carries an id for has been seeded, so a build that happens because a
    # room rebound a slot costs a dictionary lookup rather than a look at the
    # house. See `_async_seed_automations`.
    record = await _async_seed_automations(hass, record, source)
    for name in record.automations:
        # A name the document does not declare is refused by the seeding above,
        # in the same words the flow loop uses for the same case.
        block = source.inputs[name]
        answered[name] = module_host.binding_to_entity(
            block, module_host.helper_entity_id(record.slug, name, block)
        )
    # Handed through as they came rather than copied into a dict: `bound` may be
    # a `BoundSlots`, and copying it would keep only its room view -- which is
    # exactly the wrong half for a global slot. `None` is the only absence, and
    # an *empty* `BoundSlots` is not it (`__len__` is the room's, so an empty
    # room would read falsy and lose the house's own binding with it).
    slots = bound if bound is not None else {}
    resolved = module_host.resolve_slots(answered, slots)
    waiting = module_host.unresolved_slots(answered, slots)
    try:
        values = module_host.bind_inputs(source, dict(resolved))
        # Which rows hold logic rather than a value, so a pick on one of them is
        # a candidate rather than a refusal. Asked of the record and the answers
        # together, because three of the four casts are recorded and a template
        # is the answer itself -- see `module_host.cast_answers`.
        declared = module_host.declare_outputs(
            source,
            values,
            record.picks,
            answered,
            module_host.cast_answers(
                answered,
                conditions=record.derived,
                flows=record.flows,
                automations=record.automations,
            ),
        )
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    # The inputs nothing has a value for: not answered, and no default this
    # module can use. Asked of **every input the document declares** rather than
    # only the ones the person ticked to keep, because the two are the same
    # question here. Unticking "keep as a setting" means "leave it to the
    # blueprint's own default", and an input that takes a device has none to
    # leave it to -- so a module holding an unticked one is a module with a hole
    # in it, refused for a reason nobody can act on. It waits instead, and
    # `settings` below grows by exactly those names: a module waiting for
    # something waits for it to be set *on the module*, which is the settings
    # form and nowhere else.
    #
    # The bindings asked about are the person's answers *including* the slots
    # they named: an input answered with a slot the room has not bound yet is
    # answered, and is waited for as a slot. Counting it here as well would name
    # it twice in the sentence the card writes.
    unset = module_host.unset_settings(source, dict(answered), source.inputs)
    record = replace(
        record,
        settings=tuple(dict.fromkeys((*record.settings, *unset))),
    )
    if module_host.action_key(source.document) is None:
        # A document with a `sequence:` and no `action:` is a *script*, which is
        # a different thing in Home Assistant and not something this hosts. Said
        # here, in one sentence about what the person chose, because Home
        # Assistant's own refusal of it is about a missing key in a document
        # they never saw.
        #
        # Asked of the *document* rather than of an instantiated automation, so
        # that a module still waiting on a device is refused here too: a script
        # is not an automation whatever it is waiting for, and a person who
        # answered a slot should not have to bind one to find that out.
        raise ModuleHostError(
            "this source has no actions for Home Assistant to run as an "
            "automation -- it looks like a script blueprint rather than an "
            "automation one, and Open House hosts automations"
        )

    if waiting or unset:
        # **A module waiting for something is kept, not refused.** Two things it
        # may be waiting for, and they are the same kind of thing: a slot the
        # person named a role for -- "the room's lux sensor" -- which the room
        # has not been given a device for, and an input they chose to answer on
        # the module rather than at import, which the blueprint gives no default
        # for. In both cases the module is recorded, its outputs' entities exist
        # and read unknown, and its automation arrives when the value does: for a
        # slot, `async_rebuild` on binding; for an option, the settings form
        # (`async_update`). Refusing here would make the natural order --
        # import it, then give it what it needs -- impossible, and it is why the
        # import screen's Save is not held off by either.
        await _async_retire(hass, record.slug)
        built = keeping_answers(
            replace(
                record,
                automation_id="",
                inputs=values,
                outputs=declared,
                bindings=_bindings_json(bindings),
            )
        )
        await _async_amend_records(
            hass, lambda records: module_records.put(records, built)
        )
        _attach(hass, entry_id, built)
        return built

    # Only now, with every input the module needs actually answered, is there an
    # automation to build: `instantiate` is the one step that cannot run without
    # the values, and a module waiting on a device has none for the input that
    # wants it.
    try:
        automation = module_host.instantiate(source, values, alias=record.title)
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal
    document = module_host.publish_actions(automation, declared, module=record.slug)
    entity_id = await _async_create_automation(hass, document, module=record.slug)
    built = keeping_answers(
        replace(
            record,
            automation_id=entity_id,
            inputs=values,
            outputs=declared,
            bindings=_bindings_json(bindings),
        )
    )
    await _async_amend_records(hass, lambda records: module_records.put(records, built))
    _attach(hass, entry_id, built)
    return built


async def _async_push_flows(
    hass: HomeAssistant,
    entry_id: str,
    record: ModuleRecord,
    answered: Mapping[str, InputBinding],
    bound: Mapping[str, str] | None,
) -> ModuleRecord:
    """Push the flow behind every flow-answered input, and record its id.

    Each flow is two nodes and the two nodes are the wiring, so pushing one is a
    fact about *this* module: the entity the input's own answer came from -- the
    device the person picked, or whatever the room binds for the slot they named
    -- is what the flow watches, and the entity the module's input reads is what
    the flow writes. Everything between is the person's.

    Refused, with one sentence, when the instance has no Node-RED configured or
    has one with no Home Assistant server node. A cast that quietly did nothing
    would leave a module whose input is bound to a reading nothing writes: it
    installs, it reports `unknown`, and nothing says why.
    """
    if not record.flows:
        return record
    entry = hass.config_entries.async_get_entry(entry_id)
    client = node_red.async_client(hass, {} if entry is None else entry.options)
    if client is None:
        raise ModuleHostError(
            "this module answers an input with a Node-RED flow, and no Node-RED "
            "address has been set: open the Open House integration's settings "
            "and give it the address of your Node-RED"
        )
    resolved = module_host.resolve_slots(
        dict(answered), bound if bound is not None else {}
    )
    pushed = dict(record.flows)
    for name in record.flows:
        binding = resolved.get(name)
        # **`None` is not a refusal.** A row answered by a number or a piece of
        # text has nothing for a state trigger to watch, and that is the ordinary
        # case rather than a mistake: the output node is still pushed, and the
        # person builds whatever starts the flow and wires it in. Refusing here
        # would take the cast away from exactly the inputs it was asked for.
        source = _source_entity(binding)
        try:
            pushed[name] = await node_red.async_push_flow(
                client,
                flow_id=record.flows.get(name, ""),
                label=f"Open House: {record.title}",
                source_entity=source,
                output_entity=module_host.flow_entity_id(record.slug, name),
                module=record.slug,
                name=name,
            )
        except node_red.NodeRedError as refusal:
            raise ModuleHostError(str(refusal)) from refusal
    return replace(record, flows=pushed)


def _source_entity(binding: InputBinding | None) -> str | None:
    """The entity a flow-answered input's own answer names, if it names one.

    The two kinds that do are the two that *are* an entity: a device the person
    picked and another module's published output. A literal is not -- there is
    nothing for a flow to watch -- and a slot is only an entity once its room has
    bound it, which `resolve_slots` has already done by the time this is read.
    """
    if binding is None:
        return None
    if binding.kind == "entity":
        # One entity or nothing. A `target` with `multiple` set carries a *list*
        # of ids, and a flow watches one entity: a trigger given several would be
        # a trigger on whichever of them happened to be written first, which is
        # not a thing a person can be told about a module they built.
        return binding.value if isinstance(binding.value, str) else None
    if binding.kind == "output":
        return module_host.output_entity_id(binding.module, binding.key)
    return None


async def _async_retire(hass: HomeAssistant, module: str) -> None:
    """Take a module's automation out of `automations.yaml`, if it has one.

    A module that goes back to *waiting* -- the room's slot was unbound, so the
    device its inputs were built from is gone -- must stop running. An entry
    still in the file is an automation still calling `open_house.publish_output`
    with whatever it read from a device nothing points it at any more, which is
    a module reporting the past as though it were the present.

    The entry is removed by the same id `_append` writes, and Home Assistant is
    asked to reload that id. A reload that finds nothing is swallowed rather than
    raised: the common case is a module that never ran, and "there was no
    automation to stop" is the answer this wanted either way.
    """
    await _async_withdraw_automation(hass, config_id_for(module))


def _refuse_a_different_module(
    name: str, blueprint: str, hosted: Sequence[ModuleRecord]
) -> None:
    """Refuse a name that another blueprint is already using, and say which.

    Importing the same blueprint again under the same title is a replacement,
    deliberately: that is how a person takes an updated blueprint or fixes an
    answer. But two *different* blueprints imported under one title is not a
    replacement, it is a collision -- the second one takes the first one's
    automation, its record and its output entities, and every consumer bound to
    those outputs silently starts reading a different module's values. The
    person is told, and told which blueprint holds the name.
    """
    for record in hosted:
        if record.slug != name or not blueprint or not record.blueprint:
            continue
        if record.blueprint != blueprint:
            raise ModuleHostError(
                f"this house already hosts a module called {name!r}, imported "
                f"from {record.blueprint}: give this one a different name, or "
                "remove the other one first"
            )


def _title_of(text: str) -> str:
    """The title a document gives itself, for a module hosted without one.

    Read through the same reader the build uses, so the name a module is given
    and the document it is given are the same document's -- and a document that
    will not read is refused here, in the reader's own words, rather than three
    steps later by something that only knows it was handed nothing.
    """
    try:
        return module_host.read_module_source(text).title
    except AuthoringError as refusal:
        raise ModuleHostError(str(refusal)) from refusal


def _bindings_json(
    bindings: Mapping[str, InputBinding] | None,
) -> dict[str, Mapping[str, Any]]:
    """The person's bindings as the plain rows the record stores.

    A record is JSON, so an `InputBinding` is written out field by field --
    including the ones left empty, because `async_update` rebuilds a binding out
    of one of these rows and a field a reader had to guess at would be a module
    built from a binding nobody chose.
    """
    return {
        name: {
            "kind": binding.kind,
            "value": binding.value,
            "module": binding.module,
            "key": binding.key,
            "slot": binding.slot,
            # Where the slot is looked up: the module's own room, or the whole
            # house. Left out, a **global slot** answer -- "the *house's*
            # lights" -- is read back as a room answer and resolves to whatever
            # the room bound under that name, which is a different device in
            # every room and a silent one: the card that set it goes on saying
            # "whole house", because the card is reading the form, not the
            # record.
            "scope": binding.scope,
            # Which part of a split slot, when the answer narrowed to one
            # (`slot_parts`). Written for the reason the docstring gives: a part
            # left out here is a part the module is rebuilt without, so the
            # automation would act on the whole slot's device while the card that
            # set it went on showing the part -- two different devices under one
            # name, and the answer to "which one is live" visible from neither
            # screen.
            "part": binding.part,
        }
        for name, binding in (bindings or {}).items()
    }


@callback
def _attach(hass: HomeAssistant, entry_id: str, record: ModuleRecord) -> None:
    """Give the module a live home, and tell the platform to make its entities.

    Both halves are needed and they are different things: the runtime is what a
    published value is written onto, and the signal is what makes an entity exist
    to show it. A module with outputs but no signal would publish into a runtime
    nothing draws from, and a signal with no runtime would make entities that
    have nowhere to read a value from.

    **A module that is already here is updated in place rather than replaced.**
    A rebuilt module is the same module with a different setting -- same slug,
    same outputs, same entities -- and swapping a fresh `HostedModule` in would
    throw away the values its outputs last published and leave every output's
    entity listening to an object nothing writes to any more. So the record
    moves and the readings stay.
    """
    # Imported here rather than at the top of the module: `runtime` is what the
    # entity platforms import, and this module is imported by the websocket layer
    # that those platforms are started beside. The import is cheap either way;
    # keeping it local is what makes the direction of the dependency readable.
    from .runtime import HostedModule

    runtime = hass.data.get(DOMAIN, {}).get(entry_id)
    if runtime is not None:
        live = runtime.modules.get(record.slug)
        if live is None:
            runtime.modules[record.slug] = HostedModule(record=record)
        else:
            live.record = record
    async_dispatcher_send(hass, SIGNAL_MODULES_CHANGED, record)


async def _async_create_automation(
    hass: HomeAssistant, document: Mapping[str, Any], *, module: str
) -> str:
    """Have Home Assistant run `document` as an automation, and answer its entity.

    Three steps, and each one is Home Assistant's own: the config is validated by
    the validator the automation editor calls, put into `automations.yaml` the way
    that editor's own view writes it, and loaded by asking the automation
    integration to reload the single id. Reusing the editor's path is what keeps
    a hosted module a first-class automation -- visible in the editor, editable
    there, and reloadable by the button a person already knows.

    **The validator is asked, and its answer is thrown away.** Home Assistant's
    own view says why in as many words -- "we just validate, we don't store that
    data because we don't want to store the defaults" -- and it is more than
    tidiness: a validated automation's `variables:` come back as `ScriptVariables`
    objects, which are not YAML and cannot be written to a file at all. So the
    check is run on a deep copy and the *original* document is what is written, so
    what lands in `automations.yaml` is what the blueprint's author wrote rather
    than what a schema expanded it into.
    """
    from homeassistant.components.automation.config import async_validate_config_item

    # The document's own id when it has one, and a name-derived id when it does
    # not. An automation a person already has is imported as *that automation*
    # -- it keeps its id, its history and its place in the editor, and gains the
    # layer that publishes its outputs -- rather than being duplicated into a
    # second automation doing the same job under a new name. A blueprint has no
    # id of its own, which is the other case: `config_id_for` gives it one that
    # is the same every time, so importing it again replaces rather than adds.
    config_id = str(document.get(CONF_ID) or config_id_for(module))
    try:
        checked = await async_validate_config_item(
            hass, config_id, copy.deepcopy(dict(document))
        )
    except Exception as refusal:
        raise ModuleHostError(
            "Home Assistant would not accept the automation this module builds: "
            f"{refusal}"
        ) from refusal
    if checked is None:
        raise ModuleHostError(
            "Home Assistant would not accept the automation this module builds, "
            "and gave no reason"
        )

    path = Path(hass.config.path(AUTOMATION_CONFIG_PATH))
    async with _MUTATION_LOCK:
        await hass.async_add_executor_job(_append, path, config_id, dict(document))
    try:
        await hass.services.async_call(
            AUTOMATION_DOMAIN, SERVICE_RELOAD, {CONF_ID: config_id}, blocking=True
        )
    except HomeAssistantError as refusal:
        async with _MUTATION_LOCK:
            await hass.async_add_executor_job(_withdraw, path, config_id)
        raise ModuleHostError(
            "Home Assistant could not reload the automation this module builds: "
            f"{refusal}"
        ) from refusal

    entity_id = _running(hass, config_id)
    if entity_id is None:
        # The entry is in `automations.yaml` and no automation came of it, which
        # means the file is not one this instance reads -- a `configuration.yaml`
        # without `automation: !include automations.yaml`, most often. Taking the
        # entry back out keeps the failure to the one thing it should be.
        async with _MUTATION_LOCK:
            await hass.async_add_executor_job(_withdraw, path, config_id)
        raise ModuleHostError(
            f"the automation was written to {path.name} and Home Assistant did not "
            "load it, so this instance does not read that file: check that "
            "`configuration.yaml` includes `automation: !include "
            "automations.yaml`, then import again"
        )
    return entity_id


def _running(hass: HomeAssistant, config_id: str) -> str | None:
    """The entity id of the automation holding `config_id`, or `None` if none is.

    Read from the automation component's own entities rather than from the state
    machine, because a record keeps an entity id and the component is the only
    place that knows which id belongs to which configuration.
    """
    component = hass.data.get(AUTOMATION_DOMAIN)
    entities = getattr(component, "entities", None)
    if entities is None:
        return None
    for entity in entities:
        if getattr(entity, "unique_id", None) == config_id:
            return str(entity.entity_id)
    return None


def _append(path: Path, config_id: str, document: Mapping[str, Any]) -> None:
    """Put one automation into `automations.yaml`, keeping the ones already there.

    **An entry with this id is replaced rather than duplicated.** The id is
    derived from the module's name (`config_id`), so importing the same module
    again -- a second pass over the same blueprint, a title typed twice -- lands
    on the entry the first one wrote and rewrites it. Appending instead would
    leave the first automation running beside the second, both publishing to the
    same output entity, with only the newer one named by any record: a module
    nothing can see, quietly overwriting what the visible one says.

    **The whole file is rewritten, which is a real cost and worth naming.** It is
    the file the automation editor owns, and Home Assistant offers no way to add
    one entry to it, so the list is read, one entry is replaced and the list is
    written back. Reading and writing go through Home Assistant's own YAML
    functions -- the ones `components/config/view.py` uses -- so a `!secret` or
    an `!include` is understood rather than failing the read; what they cannot do
    is *preserve* those tags, so a house that keeps a `!secret` in
    `automations.yaml` has it written back resolved the first time a module is
    hosted here. Home Assistant's own editor has the same behaviour, which is why
    this is written down rather than worked around: the fix belongs in Home
    Assistant, and a second YAML writer of our own would be the beginning of two
    files.
    """
    # The id is written *last*, so it wins. The other way round reads as though
    # these are defaults and the document may override them -- and a document
    # that carries its own `id` then does: an automation imported as a module
    # would be written under the id it already had rather than the one this was
    # asked to write, so the reload below would match nothing, and the withdraw
    # that cleans up after a failed reload would not find the entry to take back.
    replacement = {**document, CONF_ID: config_id}
    entries = _entries(path)
    for index, entry in enumerate(entries):
        if entry.get(CONF_ID) == config_id:
            # Rewritten where it stands. Appending the replacement instead would
            # move the module to the bottom of the editor's list every time one
            # of its settings changed -- and this file is read by people as well
            # as by Home Assistant, whose own list is the file's order.
            entries[index] = replacement
            break
    else:
        entries.append(replacement)
    write_utf8_file_atomic(path, dump(entries))


def _withdraw(path: Path, config_id: str) -> None:
    """Take back the entry `_append` added, so a refusal leaves nothing behind."""
    entries = [entry for entry in _entries(path) if entry.get(CONF_ID) != config_id]
    write_utf8_file_atomic(path, dump(entries))


def _entries(path: Path) -> list[dict[str, Any]]:
    """`automations.yaml` as a list of entries, or an empty list when there is none.

    A file that is missing or empty is read as "no automations yet" rather than
    as an error: it is the file the first automation a person saves is written
    into, and Home Assistant itself creates it empty.
    """
    if not path.is_file():
        return []
    loaded = load_yaml(path)
    if not isinstance(loaded, list):
        return []
    return [entry for entry in loaded if isinstance(entry, dict)]


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register `open_house.publish_output`, the one service a module calls.

    Registered once for the instance rather than per entry, because the
    automation that calls it is Home Assistant's and has no idea which config
    entry hosts it -- the service is therefore what finds the module, by
    searching the entries for the one that holds it. A service per entry would
    mean an automation naming an entry id, which is a fact about Open House
    leaking into a document a person may edit.

    The schema is deliberately loose about `value`: an output may be a number, a
    string, a boolean, a list or a mapping, and the output's declared kind only
    decides how the entity shows it. A schema that enumerated them would be a
    second definition of what an output may be, in the one place a library
    update could silently change it.
    """
    if hass.services.has_service(DOMAIN, "publish_output"):
        return

    async def async_publish(call: ServiceCall) -> None:
        module = str(call.data["module"])
        key = str(call.data["key"])
        if not _async_publish(hass, module, key, call.data.get("value")):
            _LOGGER.warning(
                "a module called %r published an output called %r, and this house "
                "hosts no such module, so nothing was written",
                module,
                key,
            )

    async def async_set_flow_value(call: ServiceCall) -> None:
        """`open_house.set_flow_value`: what a pushed flow's output node calls.

        The same shape as `publish_output` and for the same reason: a running
        Node-RED flow cannot assign a Home Assistant entity, so the only way a
        flow can hand a value to a module is by calling a service, and this is
        the one it calls. The value lands on the module's runtime exactly as a
        published output does, under a key that says it came from the flow
        rather than from an output of the same name -- an input and an output
        may share a name, and they are two different readings.

        Refusing quietly, as `publish_output` does: this is called from a flow
        the person is editing, and the honest reading of "no such module" is
        that the flow is a leftover from one they removed, which is a warning
        about the house and not a failure of their flow.

        **Nested, and that is not a style choice.** It needs `hass`, and a
        service callback is called with the call and nothing else, so `hass` has
        to arrive through the enclosing scope or not at all -- written as a
        module-level function it is a `NameError` raised *inside* the call,
        which reaches the person as Node-RED reporting that their flow failed
        for a reason that names a variable in this file.
        """
        module = str(call.data["module"])
        name = str(call.data["input"])
        if not _async_set_flow(hass, module, name, call.data.get("value")):
            _LOGGER.warning(
                "a Node-RED flow set a value for the input %r of a module called "
                "%r, and this house hosts no such module, so nothing was written",
                name,
                module,
            )

    hass.services.async_register(DOMAIN, "publish_output", async_publish)
    hass.services.async_register(DOMAIN, "set_flow_value", async_set_flow_value)


def _async_set_flow(hass: HomeAssistant, module: str, name: str, value: Any) -> bool:
    """Write what a flow answered onto the module that asked for it."""
    for runtime in hass.data.get(DOMAIN, {}).values():
        hosted = getattr(runtime, "modules", None)
        if not hosted or module not in hosted:
            continue
        hosted[module].publish(f"flow:{name}", value)
        return True
    return False


def _async_publish(hass: HomeAssistant, module: str, key: str, value: Any) -> bool:
    """Write one published value onto the module that owns it.

    The module is found by searching the entries, and the search answers `False`
    rather than raising when nothing hosts it. That is deliberate: this service
    is called *by a running automation*, so a traceback here is a traceback in
    somebody's automation trace -- and the honest reading of "this house does not
    host that module" is that the automation is a leftover, which is a warning
    about the house rather than a failure of the automation.
    """
    for runtime in hass.data.get(DOMAIN, {}).values():
        hosted = getattr(runtime, "modules", None)
        if not hosted or module not in hosted:
            continue
        hosted[module].publish(key, value)
        return True
    return False
