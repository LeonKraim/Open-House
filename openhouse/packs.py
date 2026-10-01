"""Pack manifests: load one, validate it, check what it needs against a house.

This was the whole of `install_pack` in Phase 1 and is one of its four checks
now. What lives here is the schema half -- loading a manifest and validating it
against the current `pack-manifest` version, and checking a `requires_slots`
entry against the house the session is opened against -- and it stays here
because it is the schema's reader, not the lifecycle's. Phase 2 added the three
checks that run around it: the capability sandbox (`engine/sandbox.py`), the
resolution against the installed set (`engine/install.py`) and the record that
survives a snapshot. `openhouse/facade.py`'s `install_pack` is where the four are
ordered.

`check_slots` reads only `requires_slots`, and that is deliberate rather than an
omission: an *optional* slot no vocabulary declares is refused by the sandbox's
own slot-claim rule, so the two checks each own a case the other does not read
and neither is reachable only through the other.

Installing does not enable. A manifest that declares behaviours installs with
every one of them still disabled, because activation is opt-in
(`product-invariants`) and installation is not activation.

The schema is read through `tools.catalog.schemas`, so *which* version of
`pack-manifest` is current is the catalog's answer rather than a second answer
here; the retrieval and validation below is the same idiom `tools/catalog`
uses, kept local so the surface imports the schema *reader* and not the whole
tooling package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from jsonschema.protocols import Validator
from jsonschema.validators import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012, Schema

from engine.binding import House
from tools.catalog import paths
from tools.catalog.narrow import as_mapping
from tools.catalog.schemas import current_version, load_versions

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jsonschema.exceptions import ValidationError

__all__ = [
    "PackError",
    "check_slots",
    "load_manifest",
    "manifest_errors",
]

#: Every runtime schema is published under this prefix, and a `$ref` that names
#: anything else is not one this project wrote.
SCHEMA_URI_PREFIX = "https://open-house.invalid/schemas/"

#: The concept this module installs. Named once, because it is written three
#: times otherwise -- in the schema lookup, in the failure messages and in the
#: scope note -- and two of those three could drift from the third.
CONCEPT = "pack-manifest"


class PackError(Exception):
    """A manifest that will not install, naming the pack and the reason.

    The pack and the reason are carried apart from the message because a caller
    branching on the failure needs one without parsing the other, and because a
    diagnostic that said only "a slot is missing" would leave the reader to find
    which of a manifest's slots it meant.
    """

    def __init__(self, pack: str, reason: str) -> None:
        self.pack = pack
        self.reason = reason
        super().__init__(f"the pack {pack!r} will not install: {reason}")


def load_manifest(path: str | Path) -> Mapping[str, object]:
    """Read a manifest from `path` as a mapping, or fail naming the file.

    A safe loader, so a manifest is data and cannot construct anything; a
    document that is not a mapping is refused here rather than left for the
    schema, because "this pack is a list" is not a schema violation a reader
    would expect to hunt for in a validator's output.
    """
    source = Path(path)
    try:
        loaded: object = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PackError(str(source), f"it could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PackError(str(source), "it is not a YAML mapping")
    return cast("Mapping[str, object]", loaded)


def manifest_errors(document: Mapping[str, object]) -> tuple[str, ...]:
    """Every way `document` fails the current `pack-manifest` schema.

    The current version is `tools.catalog.schemas`' answer and not a version
    spelled here, so publishing a `1.2.0` changes what this validates against
    without a second place to remember. Each message is prefixed with the
    location that failed, because a finding a reader has to locate by hand is
    most of the work a diagnostic exists to save.
    """
    current = current_version(load_versions(CONCEPT))
    if current is None:
        raise PackError(
            "<schema>",
            f"no single current {CONCEPT} schema is published to validate against",
        )
    validator = cast(
        "Validator", Draft202012Validator(current.document, registry=_registry())
    )
    errors: list[ValidationError] = list(validator.iter_errors(cast("Any", document)))
    return tuple(_message(error) for error in sorted(errors, key=_location))


def check_slots(manifest: Mapping[str, object], house: House) -> None:
    """Fail if any slot `manifest` requires is one `house` cannot supply.

    Two ways a required slot can be missing, and they are different findings: a
    name no slot file declares means the pack cannot be installed *anywhere*
    (`schemas/pack-manifest` says so in the field's own description), while a
    name the vocabulary carries and this house binds nowhere means the pack is
    right and the house is not the one for it. Both name the pack and the slot.
    """
    pack = str(manifest.get("name", "<unnamed>"))
    required = manifest.get("requires_slots")
    if not isinstance(required, list):
        return
    for slot in cast("list[object]", required):
        if not isinstance(slot, str):
            continue
        if slot not in house.vocabulary.slots:
            raise PackError(
                pack,
                f"it requires the slot {slot!r}, which the slot vocabulary does "
                "not declare, so no house can supply it",
            )
        if not _supplied(house, slot):
            raise PackError(
                pack,
                f"it requires the slot {slot!r}, which this house binds nowhere",
            )


def _supplied(house: House, slot: str) -> bool:
    """Whether any room binds `slot`.

    A house-scope slot is not a second way to supply one: `resolve_slot`
    collects a house-scope slot's members from the rooms, so a slot named at
    house scope with no room binding to it resolves to nothing, and a pack
    requiring it would install against an empty group. The house's
    `house_scope_slots` list therefore says which slots may be read at house
    scope, not which ones the house actually has.
    """
    return any(slot in room.bindings for room in house.rooms)


def _location(error: ValidationError) -> list[object]:
    return list(error.absolute_path)


def _message(error: ValidationError) -> str:
    path = "/".join(str(step) for step in error.absolute_path)
    return f"{path or '<root>'}: {error.message}"


def _registry() -> Registry[Schema]:
    """A registry resolving the runtime schemas' published `$id`s to files.

    Built per call rather than at import time so it follows whatever root
    `tools.catalog.paths` is pointed at -- the suite redirects that root into a
    temporary tree, and a registry built once would keep resolving against the
    repository.
    """
    return Registry(retrieve=_retrieve)


def _retrieve(uri: str) -> Resource[Schema]:
    """Resolve a `$ref` naming another committed runtime schema.

    Anything that will not resolve -- an absent file, one that will not parse,
    or a path that escapes `schemas/` -- is reported as unresolvable rather than
    raised through, so a malformed neighbor reaches the caller as a diagnostic
    about the manifest being installed rather than as a traceback about a file
    the caller never named.
    """
    if not uri.startswith(SCHEMA_URI_PREFIX):
        raise NoSuchResource(ref=uri)
    candidate = paths.SCHEMAS / uri[len(SCHEMA_URI_PREFIX) :]
    try:
        candidate.resolve().relative_to(paths.SCHEMAS.resolve())
    except (OSError, ValueError) as exc:
        raise NoSuchResource(ref=uri) from exc
    if not candidate.is_file():
        raise NoSuchResource(ref=uri)
    try:
        loaded: object = json.loads(candidate.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NoSuchResource(ref=uri) from exc
    if not isinstance(loaded, dict):
        raise NoSuchResource(ref=uri)
    return Resource(
        contents=as_mapping(cast("Mapping[str, object]", loaded)),
        specification=DRAFT202012,
    )
