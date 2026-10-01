"""Pointer files: one published pack version, pinned by digest.

A pointer is the whole of what the registry stores about a pack: where its
source lives (`repo`), which revision of it (`commit`), where the manifest sits
inside it (`path`), the digest that pins the manifest (`sha256`), and the tier
it was published under. Nothing here is a copy of the pack, which is what keeps
the registry small and keeps a publisher's own repository the source of truth.

The `sha256` is the digest `engine.install.digest` computes -- the canonical
JSON form of the manifest document, prefixed `sha256:` -- rather than a hash of
the file's bytes. A digest over bytes would change for a re-ordered key or a
re-indented line, and the pin would then report a mismatch for a file nobody
edited; over the canonical document it changes exactly when the pack does. Using
the engine's own function rather than a second one here is what lets the store
compare the pointer's digest against the digest an install already records.

`repo: .` is reserved for the repository that hosts the registry itself, which
is how the project's own packs are published without a second checkout.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema.validators import Draft202012Validator

from .layout import POINTER_SCHEMA_FILENAME

__all__ = [
    "SCHEMA_ID",
    "SELF_REPO",
    "Pointer",
    "PointerError",
    "load_pointer",
    "pointer_schema",
    "schema_errors",
]

#: The one reserved `repo` value: the repository that hosts the registry.
SELF_REPO = "."

#: The `$id` the pointer schema publishes itself under. Named here so the
#: loader can refuse a document whose `$id` is not this one, which is the check
#: that stops a schema for something else being read as this schema.
SCHEMA_ID = "https://open-house.invalid/registry/pointer.json"


class PointerError(Exception):
    """A pointer that cannot be read, naming the file and the reason."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path.as_posix()} {reason}")


@dataclass(frozen=True, slots=True)
class Pointer:
    """One published pack version, as the registry records it."""

    name: str
    version: str
    repo: str
    commit: str
    path: str
    sha256: str
    tier: str
    abandoned: bool = False

    def to_document(self) -> dict[str, object]:
        """The pointer as the mapping its file holds.

        `abandoned` is written only when it is set, so a pointer that is not
        abandoned has no key to be read as one -- the absent case and the false
        case stay indistinguishable on disk, which is what keeps a hand-written
        pointer short.
        """
        document: dict[str, object] = {
            "name": self.name,
            "version": self.version,
            "repo": self.repo,
            "commit": self.commit,
            "path": self.path,
            "sha256": self.sha256,
            "tier": self.tier,
        }
        if self.abandoned:
            document["abandoned"] = True
        return document

    @property
    def key(self) -> tuple[str, str]:
        """The identity a pointer is unique under: its name and version."""
        return (self.name, self.version)

    def location(self) -> str:
        """Where the pointer file sits, relative to the registry root."""
        return f"pointers/{self.tier}/{self.name}/{self.version}.yaml"


def load_pointer(path: Path) -> Pointer:
    """Read one pointer file from disk, failing by naming the field."""
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PointerError(path, f"could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PointerError(path, "is not a YAML mapping")
    mapping = cast("Mapping[str, object]", loaded)
    return Pointer(
        name=_text(path, mapping, "name"),
        version=_text(path, mapping, "version"),
        repo=_text(path, mapping, "repo"),
        commit=_text(path, mapping, "commit"),
        path=_text(path, mapping, "path"),
        sha256=_text(path, mapping, "sha256"),
        tier=_text(path, mapping, "tier"),
        abandoned=mapping.get("abandoned", False) is True,
    )


def pointer_schema(registry_root: Path) -> Mapping[str, object]:
    """The pointer schema, read from the registry it belongs to.

    The `$id` is checked rather than assumed, so a file that is a schema for
    something else is refused here instead of validating every pointer against
    the wrong rules and passing.
    """
    path = registry_root / POINTER_SCHEMA_FILENAME
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PointerError(path, f"could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PointerError(path, "is not a JSON object")
    mapping = cast("Mapping[str, object]", loaded)
    if mapping.get("$id") != SCHEMA_ID:
        raise PointerError(
            path, f"is not the pointer schema (its `$id` is {mapping.get('$id')!r})"
        )
    return mapping


def schema_errors(
    document: Mapping[str, object], schema: Mapping[str, object]
) -> tuple[str, ...]:
    """Every way `document` fails `schema`, each naming where it failed.

    Sorted by location so two runs over one document report the same list, and
    each message prefixed with the path that failed, because a finding a reader
    has to locate by hand is most of the work a diagnostic exists to save.
    """
    validator: Any = Draft202012Validator(cast("Any", schema))
    errors: list[Any] = list(validator.iter_errors(cast("Any", document)))
    errors.sort(key=_location)
    return tuple(_message(error) for error in errors)


def _location(error: Any) -> str:
    return "/".join(str(step) for step in error.absolute_path)


def _message(error: Any) -> str:
    path = "/".join(str(step) for step in error.absolute_path)
    return f"{path or '<root>'}: {error.message}"


def _text(path: Path, document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise PointerError(path, f"carries no usable `{field}`")
    return value
