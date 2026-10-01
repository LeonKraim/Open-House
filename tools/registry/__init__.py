"""The registry: pointer files, a generated index, and the checks publishing runs.

The registry is a repository of *pointers* rather than of packs. A pointer names
where a pack's source lives, which revision of it, the digest that pins the
manifest, and the trust tier it was published under, so the registry never has
to hold a copy of anyone's code. `index.json` is generated from the pointer
tree, `revoked.json` from the revocation list, and both are read back by the
store without a fetch.

The four modules a caller meets are:

- `index` -- the generator and the readers of its two files.
- `checks` -- what CI runs against a publishing pull request: the pointer
  schema, the permission lint over the pack's own manifest, the permission diff
  against the previous version, and the typosquat check.
- `publish` -- export a pack as a template, pin it, and build the prefilled
  pull request.
- `store` -- browse the index off disk, install a pack by digest, and gate an
  update on the permissions it adds.

`pointer` and `tiers` are the two data models the rest read; `layout` names the
files; `errors` is the failure the operations raise when they cannot continue.
The pack CLI's verbs live in `openhouse.pack_verbs` and are reused here rather
than restated, so the check a publisher runs and the check CI runs are one
implementation.
"""

from __future__ import annotations

from .checks import (
    CHECK_NAMES,
    CheckFinding,
    CheckReport,
    Submission,
    check_permission_diff,
    check_permissions,
    check_revocation,
    check_schema,
    check_tier,
    check_typosquat,
    run_checks,
)
from .errors import RegistryError
from .index import (
    Generated,
    Index,
    IndexEntry,
    Revocation,
    build_index,
    generate,
    load_index,
    load_revoked,
    verify_current,
)
from .pointer import Pointer, PointerError, load_pointer, pointer_schema, schema_errors
from .publish import Publication, PullRequest, export_template, publish
from .store import Resolved, Store
from .tiers import TIER_NAMES, Tier, TierError, load_tiers

__all__ = [
    "CHECK_NAMES",
    "TIER_NAMES",
    "CheckFinding",
    "CheckReport",
    "Generated",
    "Index",
    "IndexEntry",
    "Pointer",
    "PointerError",
    "Publication",
    "PullRequest",
    "RegistryError",
    "Resolved",
    "Revocation",
    "Store",
    "Submission",
    "Tier",
    "TierError",
    "build_index",
    "check_permission_diff",
    "check_permissions",
    "check_revocation",
    "check_schema",
    "check_tier",
    "check_typosquat",
    "export_template",
    "generate",
    "load_index",
    "load_pointer",
    "load_revoked",
    "load_tiers",
    "pointer_schema",
    "publish",
    "run_checks",
    "schema_errors",
    "verify_current",
]
