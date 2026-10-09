"""Build the release asset: the integration with the packages it imports inside it.

The integration is thin on purpose -- `engine/`, `ha_adapter/`, `tools/` and the
catalog it reads all live beside `custom_components/` in the repository, so the
simulator, the CLI and the test suite share one copy of each rather than a second
that can drift (see `custom_components/open_house/_bootstrap.py`). A HACS install
copies `custom_components/open_house/` and nothing beside it, so a plain release
of this repository would install an integration whose imports are missing.

This builds the asset that does install: a tree rooted at
`custom_components/open_house/` which contains, next to the integration's own
modules, the packages and the data directories it reads at runtime. `_bootstrap`
recognises that layout and puts the directory on `sys.path`; `const.catalog_root`
finds the catalog in it by the same walk it uses everywhere else.

The tree is built, never committed. Committing a copy under `custom_components/`
is the second source of truth this project refuses, so the copy exists only
inside the archive and is regenerated from the tree on every release.

    python -m tools.package_integration           # writes dist/open_house.zip
    python -m tools.package_integration --keep    # also leaves dist/staging/

`hacs.json` declares `zip_release` and names `open_house.zip`, so a person
installs from a GitHub release that carries this archive as an asset.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The integration, relative to the repository root. Everything in the asset
#: hangs off this path, because it is the one HACS copies into a config dir.
INTEGRATION = Path("custom_components") / "open_house"

#: The first-party packages the integration imports at runtime. Found by walking
#: the imports of the integration and everything it reaches, not by guessing --
#: `sim` is here because `openhouse/` reads its clock, and leaving it out would
#: install an integration that fails on the first scenario it is asked to run.
PACKAGES = ("engine", "ha_adapter", "tools", "openhouse", "sim")

#: The data directories read at runtime. `catalog/` is the vocabulary; `schemas/`
#: is what validates it; `registry/` is what `ha_adapter`'s setup flow reads to
#: offer a module. All three are resolved relative to the tree root.
DATA = ("catalog", "schemas", "registry")

#: Build-time code under `tools/` that must not ship: the Home Assistant probe
#: scripts (they drive a live instance from a developer's machine) and this
#: builder, which is about the repository rather than the integration.
NOT_SHIPPED = (Path("tools") / "ha", Path("tools") / "package_integration.py")

#: This machine's droppings, never the asset's.
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")

ASSET_NAME = "open_house.zip"
DIST = ROOT / "dist"
STAGING = DIST / "staging"


def build(destination: Path = STAGING) -> Path:
    """Write the self-contained tree to `destination` and return the root.

    The root is the directory that holds `custom_components/`, not the integration
    itself: the archive has to carry that prefix, because a HACS install unpacks
    it into the config directory and expects `custom_components/open_house/` to be
    the path that lands there.
    """
    if destination.exists():
        shutil.rmtree(destination)
    target = destination / INTEGRATION
    target.mkdir(parents=True)

    shutil.copytree(ROOT / INTEGRATION, target, dirs_exist_ok=True, ignore=IGNORED)
    for name in (*PACKAGES, *DATA):
        shutil.copytree(ROOT / name, target / name, ignore=IGNORED)

    for relative in NOT_SHIPPED:
        stale = target / relative
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.exists():
            stale.unlink()
    return destination


def archive(root: Path, destination: Path) -> Path:
    """Zip everything under `root`, paths kept relative to it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DIST / ASSET_NAME)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the unzipped tree at dist/staging/ as well as the archive",
    )
    args = parser.parse_args(argv)

    tree = build()
    built = archive(tree, args.out)
    files = sum(1 for path in tree.rglob("*") if path.is_file())
    print(
        f"{built.relative_to(ROOT) if built.is_relative_to(ROOT) else built}: "
        f"{files} files, {built.stat().st_size / 1_000_000:.1f} MB"
    )
    if not args.keep:
        shutil.rmtree(STAGING, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
