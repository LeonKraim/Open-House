"""The result document a surface reports, defined once for every surface.

`control-surface`'s "one schema seen twice" rule is written about a tool's
*inputs* -- the CLI's arguments and the MCP tool's schema -- and this is the other
half of the same claim. The CLI writes `{"operation": ..., "result": ...}` and the
MCP tool returns the same object, so a reader of either face is reading one
document; a result normalised twice would be two answers to "what did this
operation return", which is the drift the registry exists to prevent.

Nothing here interprets a result. `jsonable` is the mechanical normalisation a
document needs before JSON can write it -- a record or a run result writes itself
through `to_document`, a device read is a dataclass, a tuple is a list -- and a
surface that computed anything would be a second implementation of the facade
rather than a face of it. The module is separate from `operations.py` because it
is not derived from the registry descriptor: `input_schema` is what an operation
*takes*, and this is what a call *gives back*, and only the first is a property
of the ten declarations.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING, cast

from sim.scenario import plain

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["jsonable", "result_document"]


def result_document(name: str, result: object) -> dict[str, object]:
    """One operation's result, as the document every surface reports.

    `name` is the operation's own name -- a registry name on the CLI's
    subcommands and on the MCP tools -- so which operation produced a document
    is read off the document rather than remembered from the call.
    """
    return {"operation": name, "result": jsonable(result)}


def jsonable(value: object) -> object:
    """A facade result as a JSON document, without asking what it means.

    Three shapes reach here and each is one line: a record or a run result writes
    itself through `to_document`, a device read is a dataclass whose fields are
    handed to the same normaliser, and a tuple of records is a list. Nothing is
    interpreted -- which is what keeps a surface a surface rather than a second
    implementation of what an operation returns.
    """
    if hasattr(value, "to_document"):
        return plain(value.to_document())  # type: ignore[attr-defined]
    if is_dataclass(value) and not isinstance(value, type):
        # Field by field rather than `dataclasses.asdict`, which deep-copies: a
        # device view holds a read-only mapping proxy, and copying one is a
        # `TypeError` for a reason that has nothing to do with this face.
        document: dict[str, object] = {}
        for field in fields(value):
            document[field.name] = getattr(value, field.name)
        return plain(document)
    if isinstance(value, tuple | list):
        # `isinstance` narrows a tuple to `tuple[Unknown, ...]`, and the cast says
        # the one thing the narrowing leaves open: the members are arbitrary.
        return [jsonable(item) for item in cast("Sequence[object]", value)]
    return plain(value)
