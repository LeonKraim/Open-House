"""Repository layout, resolved from the file rather than the cwd.

Every check needs the same handful of locations, and every check needs them to
mean the same thing regardless of where it was invoked from -- a pre-commit hook
runs from the repository root, a test runs from wherever pytest was started, and
CI runs from a checkout with neither `ressources/` nor `.local/` present. They
are derived here, once, from the location of this file.
"""

from __future__ import annotations

from pathlib import Path

#: Repository root. `paths.py` lives at `<root>/tools/catalog/paths.py`.
ROOT = Path(__file__).resolve().parents[2]

CATALOG = ROOT / "catalog"
SCHEMAS = ROOT / "schemas"
SCHEMA_CATALOG = SCHEMAS / "catalog"
DOCS = ROOT / "docs"
PACKS = ROOT / "packs"

#: The four reference clones. Read-only, gitignored, and absent in CI.
RESSOURCES = ROOT / "ressources"

#: The unfiltered extraction. Gitignored, absent in CI, and deliberately outside
#: `catalog/` -- every data file under `catalog/` must validate against a schema,
#: and this is the one file whose whole point is to hold what the corpus may not.
LOCAL = ROOT / ".local"

#: The eight runtime concepts, each of which gets version directories under
#: `schemas/<concept>/`.
RUNTIME_CONCEPTS: tuple[str, ...] = (
    "room-type",
    "slot",
    "house",
    "pack-manifest",
    "behavior-vocabulary",
    "mode",
    "profile",
    "export-document",
)

#: The artifact classes a selected file can receive, in the order the
#: `reference-catalog` spec enumerates them. `other` is the residual and is
#: deliberately excluded from the class-pinning requirement, since pinning it
#: would commit the corpus to retaining a file whose only justification is that
#: we could not classify it.
ARTIFACT_CLASSES: tuple[str, ...] = (
    "package",
    "automation",
    "script",
    "scene",
    "helper",
    "template",
    "blueprint",
    "dashboard",
    "custom_integration",
    "other",
)

#: Classes that must be pinned by at least one repo's golden file.
PINNABLE_CLASSES: tuple[str, ...] = tuple(c for c in ARTIFACT_CLASSES if c != "other")

#: Parse outcomes, tracked separately from class so class counts still sum.
PARSE_OUTCOMES: tuple[str, ...] = ("parsed", "unparsed", "not_applicable")


def catalog_data_files() -> list[Path]:
    """Every committed data file under `catalog/`, excluding prose.

    Markdown is exempt: `README.md` and `overlap.md` are documents, not data,
    and requiring a schema for them would mean inventing one to satisfy the
    checker.
    """
    out: list[Path] = []
    for path in sorted(CATALOG.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in {".yaml", ".yml", ".json"}:
            out.append(path)
    return out
