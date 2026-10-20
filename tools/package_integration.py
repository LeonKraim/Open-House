"""Build the release asset: the integration with the packages it imports inside it.

The integration is thin on purpose -- `engine/`, `ha_adapter/`, `tools/` and the
catalog it reads all live beside `custom_components/` in the repository, so the
simulator, the CLI and the test suite share one copy of each rather than a second
that can drift (see `custom_components/open_house/_bootstrap.py`). A HACS install
copies `custom_components/open_house/` and nothing beside it, so a plain release
of this repository would install an integration whose imports are missing.

This builds the asset that does install: a tree rooted at the integration itself,
carrying, next to the integration's own modules, the packages and the data
directories it reads at runtime and the built sidebar panel. `_bootstrap`
recognises that layout and puts the directory on `sys.path`;
`const.catalog_root` finds the catalog in it by the same walk it uses
everywhere else; `panel_bundle` finds the bundle in it by the same question.
The panel is the one item here that is not source -- it is a build artefact, so
it has to exist before this runs, and `build()` refuses rather than shipping an
archive whose sidebar tab would load nothing.

The root is the integration and not the repository's `custom_components/`,
because that is what HACS unpacks an asset *into*: see `build()` for why the
archive must not carry the prefix it looks like it should.

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

#: The integration, relative to the repository root. This is the source this
#: reads from, *not* the shape of what it writes: the asset's root is the
#: integration's contents, and `build()` says why.
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

#: The built sidebar panel: one file, emitted by `npm run build` in `panel/`.
#:
#: It is shipped for the same reason the packages are -- a HACS install copies
#: `custom_components/open_house/` and nothing beside it -- and it is the one
#: item here that is not source: `panel/dist/` is gitignored, so it is not in a
#: fresh checkout and it is *not* something this builder can produce. That is
#: why `build()` refuses to run without it rather than shipping an archive
#: without it: the release that made this file's existence a requirement
#: installed an integration whose sidebar tab loaded nothing at all, because the
#: only thing that ever put a bundle where the integration reads one was a mount
#: in the development compose file.
PANEL_BUNDLE = ROOT / "panel" / "dist" / "open-house-panel.js"

#: Where that copy goes inside the integration, and the name is not this
#: module's to choose: `custom_components/open_house/panel_bundle.py` is what
#: looks for it at runtime, and the two have to agree or the archive carries a
#: file nothing reads.
PANEL_DIRECTORY = Path("panel")

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

    The root *is* the integration: `manifest.json` sits at the top of what this
    writes, with the packages and the panel beside it. The prefix the repository
    uses -- `custom_components/open_house/` -- must not appear in the archive,
    and that is not the layout the source tree suggests.

    HACS unpacks a `zip_release` asset *verbatim* into the integration's
    directory. `repositories/integration.py` sets `localpath` to
    `<config>/custom_components/<domain>`, and `repositories/base.py`'s
    `async_download_zip_file` is a bare `zip_file.extractall(self.content.path.local)`
    -- no first-component stripping, unlike the repository-zip path beside it,
    which drops `filename.split("/")[1:]`. So an archive rooted at
    `custom_components/open_house/` lands at
    `config/custom_components/open_house/custom_components/open_house/manifest.json`,
    one level below anything Home Assistant scans: the install reports success,
    and the integration is not there. `alexa_media.zip`, a known-good
    `zip_release` integration, is rooted the same way this is -- `manifest.json`
    at the top.

    Raises `FileNotFoundError` when the panel has not been built, which is the
    one thing this cannot copy from the repository because the repository does
    not have it. Refusing is the whole point: an archive without a bundle is a
    HACS install whose sidebar tab loads nothing, and nothing about the build
    would have said so.
    """
    if not PANEL_BUNDLE.is_file():
        # Named the way `main` names what it wrote: relative to the repository
        # when it is in one, absolute when it is not, because this is read by a
        # person about to run a command and a path they cannot paste is worse
        # than a long one.
        shown = (
            PANEL_BUNDLE.relative_to(ROOT)
            if PANEL_BUNDLE.is_relative_to(ROOT)
            else PANEL_BUNDLE
        )
        raise FileNotFoundError(
            f"{shown} does not exist, and a release without the panel installs "
            "an integration whose sidebar tab loads nothing; run "
            "`npm run build` in panel/ first"
        )

    if destination.exists():
        shutil.rmtree(destination)

    shutil.copytree(ROOT / INTEGRATION, destination, dirs_exist_ok=True, ignore=IGNORED)
    for name in (*PACKAGES, *DATA):
        shutil.copytree(ROOT / name, destination / name, ignore=IGNORED)

    # One file and not the directory it sits in, because that directory is
    # `panel/` -- the source tree, with its `node_modules`, its scripts and its
    # screenshots -- and what the integration reads is the one built file.
    shipped_panel = destination / PANEL_DIRECTORY
    shipped_panel.mkdir()
    shutil.copy2(PANEL_BUNDLE, shipped_panel / PANEL_BUNDLE.name)

    for relative in NOT_SHIPPED:
        stale = destination / relative
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

    try:
        tree = build()
    except FileNotFoundError as error:
        # A sentence and not a traceback: the one thing this can fail on is a
        # panel that has not been built, which is a step to take rather than a
        # bug to read a stack for.
        print(error, file=sys.stderr)
        return 2
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
