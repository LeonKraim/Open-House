"""Where the registry's files are, resolved from the project and not the cwd.

The registry is one directory with a fixed shape: a schema and a tier list that
are hand-written, a `pointers/` tree that a publisher adds to, a `revocations`
list that a maintainer edits, and the two generated files. The names live here
rather than in each reader, so "the index is `index.json`" is stated once and a
rename is one edit rather than a search.

`default_root` is a function rather than a constant because the suite redirects
`tools.catalog.paths` into a temporary tree; a path bound at import would keep
pointing at the repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.catalog import paths

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DIRECTORY",
    "INDEX_FILENAME",
    "POINTERS_DIRECTORY",
    "POINTER_SCHEMA_FILENAME",
    "REVOCATIONS_FILENAME",
    "REVOKED_FILENAME",
    "TIERS_FILENAME",
    "default_root",
]

#: The registry's directory name, under the project root.
DIRECTORY = "registry"

#: The pointer schema, the tier list and the revocation list, all hand-written.
POINTER_SCHEMA_FILENAME = "pointer.schema.json"
TIERS_FILENAME = "tiers.yaml"
REVOCATIONS_FILENAME = "revocations.yaml"

#: The pointer tree, and the two files the generator writes.
POINTERS_DIRECTORY = "pointers"
INDEX_FILENAME = "index.json"
REVOKED_FILENAME = "revoked.json"


def default_root() -> Path:
    """The committed registry, under the project root of the current build."""
    return paths.ROOT / DIRECTORY
