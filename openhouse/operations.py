"""The ten operations, defined once and drawn from by every surface.

`control-surface` states the rule this module exists to make checkable: three
surfaces describing the same ten operations is three places for them to disagree,
and the disagreement that matters is silent -- a subcommand the MCP server lacks,
or a parameter accepted on one face and dropped on another, is a difference a
caller discovers only by trying. So the descriptor lives here, once, and the CLI
subcommands and the MCP tools are *generated* from it rather than declared beside
it.

The set is closed and matches `spec.txt`'s enumeration, which is the sentence
naming `advance_time, set_state, user_action, inject_fault, snapshot/restore,
get_decision_log, install_pack, export_config, import_config`. The enumeration
writes the fourth pair as `snapshot/restore`, so a check that read it would see
nine tokens where the registry holds ten: the pairing is expanded into its two
operations, and the check asserts the expansion rather than assuming it.

Two things are deliberately **not** in `OPERATIONS`, and both are named here
rather than left to a reader to notice:

- the **house-control facility** (`HOUSE_CONTROL` below) -- `add_entity`,
  `remove_entity`, `set_availability` and `restart`. These are the control face
  of the `HouseAdapter` port, not operations on the engine's decisions; the
  composition root exposes them so a scenario can build and provoke the house,
  and `spec.txt` fixes the registry at its ten decision-driving operations, so a
  restart mid-absence is expressible without an eleventh.
- the **`run_scenario` client entry point** (`scenario-runner`), which is a
  composed driver *over* the ten operations rather than an eleventh.

Both are excluded from the registry comparison for that reason; the check
compares decision-driving operations only.

Nothing here enables a behaviour. `install_pack` validates a manifest and checks
its slots and does not activate what it installs, because installation is not
activation (`product-invariants`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from openhouse.facade import OpenHouse

__all__ = [
    "HOUSE_CONTROL",
    "HOUSE_CONTROL_NAMES",
    "OPERATIONS",
    "OPERATION_NAMES",
    "Operation",
    "Parameter",
    "input_schema",
]

#: The JSON Schema types a parameter may carry. The set is closed because both
#: surfaces derive their signature from it -- Typer's annotation and the MCP
#: tool's JSON Schema -- and a type outside it would be one only one face could
#: express.
Kind = Literal["string", "integer", "number", "boolean", "object", "array"]


@dataclass(frozen=True, slots=True)
class Parameter:
    """One argument of one operation, as all three faces declare it.

    `default` is read only when `required` is false; a parameter that is required
    and carries a default would be a contradiction the surfaces would resolve
    differently, so the registry check rejects one.
    """

    name: str
    kind: Kind
    description: str
    required: bool = True
    default: object = None


@dataclass(frozen=True, slots=True)
class Operation:
    """One name of the surface: what it is called, what it takes, what it does.

    `handler` takes the open session first and the operation's parameters as
    keywords, so invoking an operation is `operation.handler(session, **values)`
    on every face. It is a callable rather than the name of a facade method
    because "every operation in the registry is callable through the facade" is
    then a property of calling it, not of a string resolving.

    `scope` is required of every operation and is surfaced through the CLI's
    `--help` and the MCP tool's description, so a caller meets the boundary
    rather than only a reader of the spec. Three operations name a later phase
    in it, because each is a deliberate subset of a feature `spec.txt` gives to
    that phase.
    """

    name: str
    summary: str
    scope: str
    parameters: tuple[Parameter, ...]
    handler: Callable[..., object]


# The handlers. Each is one line of forwarding on purpose: the facade owns the
# behaviour and a handler that computed anything would be a second implementation
# of it, which is the drift the registry exists to prevent.


def _advance_time(session: OpenHouse, *, minutes: float) -> object:
    return session.advance_time(minutes=minutes)


def _set_state(session: OpenHouse, *, entity_id: str, state: str) -> object:
    return session.set_state(entity_id, state)


def _user_action(session: OpenHouse, *, entity_id: str, state: str) -> object:
    return session.user_action(entity_id, state)


def _inject_fault(session: OpenHouse, *, entity_id: str, fault: str) -> object:
    return session.inject_fault(entity_id, fault)


def _snapshot(session: OpenHouse) -> object:
    return session.snapshot()


def _restore(session: OpenHouse, *, document: Mapping[str, object]) -> object:
    return session.restore(document)


def _get_decision_log(session: OpenHouse, *, window: int | None = None) -> object:
    return session.get_decision_log(window=window)


def _install_pack(session: OpenHouse, *, manifest: str) -> object:
    return session.install_pack(manifest)


def _export_config(session: OpenHouse) -> object:
    return session.export_config()


def _import_config(session: OpenHouse, *, document: Mapping[str, object]) -> object:
    return session.import_config(document)


# The house-control facility: the control face of the `HouseAdapter` port, which
# builds and provokes the house. Exposed by the composition root outside the
# registry, and named as step verbs by the scenario runner alongside the ten.


def _add_entity(
    session: OpenHouse,
    *,
    entity_id: str,
    state: str,
    attributes: Mapping[str, object] | None = None,
) -> object:
    return session.add_entity(entity_id, state, attributes=attributes)


def _remove_entity(session: OpenHouse, *, entity_id: str) -> object:
    return session.remove_entity(entity_id)


def _set_availability(session: OpenHouse, *, entity_id: str, available: bool) -> object:
    return session.set_availability(entity_id, available)


def _restart(session: OpenHouse) -> object:
    return session.restart()


_ENTITY = Parameter("entity_id", "string", "The entity the change is made to.")
_STATE = Parameter("state", "string", "The state written to the entity.")
_ATTRIBUTES = Parameter(
    "attributes",
    "object",
    "Attribute values written with the state, if any.",
    required=False,
    default=None,
)

#: The ten operations, in the order `spec.txt` enumerates them with the
#: `snapshot/restore` pair expanded. A `Mapping` built from this tuple so the
#: order is the enumeration's and the names are the keys.
OPERATIONS: Mapping[str, Operation] = {
    operation.name: operation
    for operation in (
        Operation(
            name="advance_time",
            summary="Advance the session's virtual clock and tick the engine.",
            scope=(
                "Phase 1: the only way time moves. Returns the decisions the "
                "tick produced."
            ),
            parameters=(
                Parameter(
                    "minutes",
                    "number",
                    "How far to advance. Zero is a step the engine takes without "
                    "ticking.",
                ),
            ),
            handler=_advance_time,
        ),
        Operation(
            name="set_state",
            summary="Write an entity's state as the world changing.",
            scope=(
                "Phase 1: a `world`-origin change, which the engine must be able "
                "to tell from its own actuation. A `lock` or `cover` written "
                "into an open position is refused by the engine's veto."
            ),
            parameters=(_ENTITY, _STATE),
            handler=_set_state,
        ),
        Operation(
            name="user_action",
            summary="Write an entity's state as a person doing it.",
            scope=(
                "Phase 1: the only operation that produces a `user`-origin "
                "change, which is what manual-override detection reads and what "
                "admits a direct unlock."
            ),
            parameters=(_ENTITY, _STATE),
            handler=_user_action,
        ),
        Operation(
            name="inject_fault",
            summary="Make an entity report a fault.",
            scope=(
                "Phase 1: a `fault`-origin change. The fault names what the "
                "device reports; the engine reads it and does not act on it."
            ),
            parameters=(
                _ENTITY,
                Parameter("fault", "string", "The fault the entity reports."),
            ),
            handler=_inject_fault,
        ),
        Operation(
            name="snapshot",
            summary="Return the session's runtime state as a document.",
            scope=(
                "Phase 1: the runtime `Snapshot` -- entities, engine state and "
                "the simulator's positions. It carries no decision record, "
                "because the log is history rather than state."
            ),
            parameters=(),
            handler=_snapshot,
        ),
        Operation(
            name="restore",
            summary="Reconstitute a session from a snapshot document.",
            scope=(
                "Phase 1: a document whose `snapshot_version` the build does not "
                "understand is refused rather than partly applied."
            ),
            parameters=(
                Parameter(
                    "document",
                    "object",
                    "A document `snapshot` produced.",
                ),
            ),
            handler=_restore,
        ),
        Operation(
            name="get_decision_log",
            summary="Read a window of the decision log, oldest first.",
            scope=(
                "Phase 1: the records are the engine's own, unreshaped. The "
                "window defaults to the log's bound."
            ),
            parameters=(
                Parameter(
                    "window",
                    "integer",
                    "How many of the most recent records to return.",
                    required=False,
                    default=None,
                ),
            ),
            handler=_get_decision_log,
        ),
        Operation(
            name="install_pack",
            summary="Validate a pack manifest, resolve it against the house.",
            scope=(
                "Phase 1's operation, completed in Phase 2. The manifest is "
                "validated against the `pack-manifest` schema and its "
                "`requires_slots` are checked against the house, as Phase 1 did; "
                "the capability sandbox then refuses a pack that leaves the "
                "declarative subset, calls a banned service or pins a path "
                "outside itself, and the pack is resolved against the packs "
                "already installed -- dependencies, conflicts and cycles, with "
                "every reason a refusal found rather than the first. The install "
                "record is kept as session state, so what arrived and why "
                "survives a snapshot. Installing does not enable what the pack "
                "declares: activation is a separate act, and no operation on this "
                "surface is one."
            ),
            parameters=(
                Parameter(
                    "manifest",
                    "string",
                    "Path to the pack manifest to install.",
                ),
            ),
            handler=_install_pack,
        ),
        Operation(
            name="export_config",
            summary="Write the house configuration out.",
            scope=(
                "Phase 1: the house and its bindings, validating against "
                "`schemas/house/1.0.0.json`. The export document that carries "
                "registry ids beside entity ids, for the Phase 3 re-link, is "
                "Phase 3's and is not this."
            ),
            parameters=(),
            handler=_export_config,
        ),
        Operation(
            name="import_config",
            summary="Reconstitute a session's house from an exported one.",
            scope=(
                "Phase 1: the inverse of `export_config` over the house "
                "configuration. It accepts no field outside the house schema, "
                "so a Phase 3 export cannot be imported by accident."
            ),
            parameters=(
                Parameter(
                    "document",
                    "object",
                    "A document `export_config` produced.",
                ),
            ),
            handler=_import_config,
        ),
    )
}

#: The house-control facility, in the order `house-adapter` lists the port's
#: operations. Outside the registry, and compared separately from it.
HOUSE_CONTROL: Mapping[str, Operation] = {
    operation.name: operation
    for operation in (
        Operation(
            name="add_entity",
            summary="Add a device to the house.",
            scope=(
                "Phase 1: the port's own operation, exposed so a scenario can "
                "build a house. Not a registry operation."
            ),
            parameters=(_ENTITY, _STATE, _ATTRIBUTES),
            handler=_add_entity,
        ),
        Operation(
            name="remove_entity",
            summary="Remove a device from the house.",
            scope=("Phase 1: the port's own operation. Not a registry operation."),
            parameters=(_ENTITY,),
            handler=_remove_entity,
        ),
        Operation(
            name="set_availability",
            summary="Mark a device available or unavailable.",
            scope=(
                "Phase 1: availability is a field of its own, so a device can be "
                "unavailable without its state being read as off. Not a registry "
                "operation."
            ),
            parameters=(
                _ENTITY,
                Parameter("available", "boolean", "Whether the device answers."),
            ),
            handler=_set_availability,
        ),
        Operation(
            name="restart",
            summary="Return the house to its defined startup condition.",
            scope=(
                "Phase 1: an unavailable device is still unavailable and not "
                "off after a restart. Not a registry operation, and not how a "
                "snapshot is restored."
            ),
            parameters=(),
            handler=_restart,
        ),
    )
}

#: The registry's names in enumeration order.
OPERATION_NAMES: tuple[str, ...] = tuple(OPERATIONS)

#: The house-control facility's names in port order.
HOUSE_CONTROL_NAMES: tuple[str, ...] = tuple(HOUSE_CONTROL)


def input_schema(operation: Operation) -> dict[str, object]:
    """The operation's parameters as a JSON Schema object.

    The MCP tool's input schema is derived from the registry descriptor rather
    than written beside it, which is what keeps the CLI's arguments and the
    tool's inputs one schema seen twice (`control-surface`).
    """
    properties: dict[str, object] = {}
    required: list[str] = []
    for parameter in operation.parameters:
        properties[parameter.name] = {
            "type": parameter.kind,
            "description": parameter.description,
        }
        if parameter.required:
            required.append(parameter.name)
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    else:
        schema["required"] = []
    return schema
