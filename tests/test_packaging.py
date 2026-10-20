"""The release asset ships everything the integration imports.

`tools/package_integration.py` builds the archive a HACS install unpacks, and
the whole point of that archive is that its imports resolve with nothing beside
it -- no checkout, no `/openhouse-src` mount, no `OPEN_HOUSE_SRC`. So the thing
to test is not that the builder copies the directories it was told to; it is that
the set it was told to copy is the set the integration actually reaches. A
package added to `ha_adapter`'s imports and forgotten in the builder's list would
pass every other test in this suite and install an integration that fails on load
with `ModuleNotFoundError`, which is why the first test here reads the imports
out of the built tree instead of trusting the list.

The second test is the other half: with the tree as an install finds it, the
module that puts packages on `sys.path` resolves to the tree rather than to a
mount. It drives `_bootstrap` directly -- it imports nothing but the standard
library -- so it needs no Home Assistant, and it is the same answer the container
check gives, kept here so a change to the resolution rules fails in CI.

The last three tests are about the panel, which is the one thing the archive
carries that the repository does not have to hand. It is a build artefact
(`panel/dist/` is gitignored), so these tests stand one in rather than depending
on `npm run build` having happened -- the Python job does not build the panel,
and a test that skipped when it had not would be a test that is never run. The
two that matter are the pair: that the bundle lands where the integration looks
for it, and that the builder *refuses* when there is none. The second is the one
this suite was missing when a release shipped with no panel at all, and it is
written as a failing case rather than as a comment because a requirement nothing
exercises is a requirement that comes back.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from typing import TYPE_CHECKING

import pytest

from tools import package_integration

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType

#: The repository's own importable packages: a directory at the root with an
#: `__init__.py`. `custom_components` is excluded because the integration is
#: reached through it rather than shipped *as* it -- the archive's root is
#: `custom_components/open_house/`, not `custom_components/`.
FIRST_PARTY = frozenset(
    entry.name
    for entry in package_integration.ROOT.iterdir()
    if entry.is_dir()
    and (entry / "__init__.py").is_file()
    and entry.name != "custom_components"
)


def _imported_names(*directories: Path) -> set[str]:
    """Every top-level module name imported by any `.py` under `directories`."""
    names: set[str] = set()
    for directory in directories:
        for source in directory.rglob("*.py"):
            tree = ast.parse(source.read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif (
                    isinstance(node, ast.ImportFrom) and not node.level and node.module
                ):
                    names.add(node.module.split(".")[0])
    return names


def _module_from(name: str, path: Path) -> ModuleType:
    """A module of the integration's, loaded by path.

    Loaded by path and not imported, because both modules this suite drives that
    way are deliberately stdlib-only and reachable without Home Assistant
    installed -- which is the property that makes them testable here at all.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A stand-in for the built panel, pointed at by the packager.

    The real file is `panel/dist/open-house-panel.js`: `npm run build` emits it
    and `.gitignore` excludes it, so it is on a machine that has built the panel
    and not in a fresh checkout -- and the Python job never builds one. Standing
    one in keeps these tests from being green or red for a reason that has
    nothing to do with the code, which is what a suite that read the real file
    would be.

    It is patched onto the module rather than passed into `build()` because that
    is how the packager reads it: `PANEL_BUNDLE` is a constant beside `ROOT` and
    `PACKAGES`, so the one path a release depends on is named in one place.
    """
    built = tmp_path / "open-house-panel.js"
    built.write_text("// a built panel\n", "utf-8")
    monkeypatch.setattr(package_integration, "PANEL_BUNDLE", built)
    yield built


def test_the_asset_ships_every_first_party_import(bundle: Path, tmp_path: Path) -> None:
    root = package_integration.build(tmp_path / "staging")
    integration = root / package_integration.INTEGRATION
    shipped = [integration, *(root / name for name in package_integration.PACKAGES)]

    needed = _imported_names(*shipped) & FIRST_PARTY
    missing = sorted(name for name in needed if not (integration / name).is_dir())

    assert not missing, (
        f"the asset does not carry {missing}, which the integration imports; "
        "add them to package_integration.PACKAGES"
    )


def test_the_asset_carries_the_data_it_reads(bundle: Path, tmp_path: Path) -> None:
    integration = package_integration.build(tmp_path / "staging") / (
        package_integration.INTEGRATION
    )
    for name in package_integration.DATA:
        assert (integration / name).is_dir(), (
            f"{name}/ is read at runtime but not shipped"
        )
    assert (integration / "catalog" / "room_types.yaml").is_file()


def test_the_asset_carries_nothing_this_machine_produced(
    bundle: Path, tmp_path: Path
) -> None:
    integration = package_integration.build(tmp_path / "staging") / (
        package_integration.INTEGRATION
    )
    names = [path.name for path in integration.rglob("*")]

    assert "__pycache__" not in names
    assert not any(name.endswith(".pyc") for name in names)
    assert not (integration / "tools" / "ha").exists()


def test_the_archive_root_is_the_path_hacs_copies(bundle: Path, tmp_path: Path) -> None:
    import zipfile

    root = package_integration.build(tmp_path / "staging")
    built = package_integration.archive(root, tmp_path / "asset.zip")
    with zipfile.ZipFile(built) as zf:
        names = zf.namelist()

    prefix = f"{package_integration.INTEGRATION.as_posix()}/"
    assert names, "the archive is empty"
    assert all(name.startswith(prefix) for name in names), (
        "the archive has a stray root"
    )
    assert f"{prefix}manifest.json" in names


def test_bootstrap_resolves_to_the_shipped_packages(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = package_integration.build(tmp_path / "staging")
    integration = root / package_integration.INTEGRATION

    # No mount, and no env override pointing anywhere real: the only answer left
    # is the packages the integration was shipped with.
    monkeypatch.setenv("OPEN_HOUSE_SRC", str(tmp_path / "not-a-checkout"))
    saved = list(sys.path)
    try:
        module = _module_from("_packaged_bootstrap", integration / "_bootstrap.py")
        assert module.shipped_root() == integration.resolve()
        assert module.source_root() == integration.resolve()
    finally:
        sys.path[:] = saved


def test_the_asset_carries_the_panel_where_the_integration_looks(
    bundle: Path, tmp_path: Path
) -> None:
    """The bundle lands at the path the view resolves, not merely somewhere.

    Both halves of the one fact that has to hold for the tab to load: the
    archive carries the file, and it carries it where `panel_bundle` looks. A
    builder that copied it to a directory of its own choosing would pass a test
    that only counted files in the zip.
    """
    integration = package_integration.build(tmp_path / "staging") / (
        package_integration.INTEGRATION
    )
    panel_bundle = _module_from("_panel_bundle", integration / "panel_bundle.py")

    found = panel_bundle.bundle_path(tmp_path / "config", integration)
    assert found is not None, "the asset carries no bundle the view can find"
    assert (
        found.resolve()
        == (
            integration / panel_bundle.SHIPPED_DIRECTORY / panel_bundle.BUNDLE_NAME
        ).resolve()
    )


def test_the_builder_refuses_to_ship_a_panel_less_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release without a panel is refused, not built.

    The failure this guards is not hypothetical and not visible from the
    archive: the asset that shipped had no bundle in it, so the install had a
    sidebar tab whose module URL answered `404`, and nothing about building it
    said so. Refusing is what makes the next one impossible rather than
    unlikely.
    """
    monkeypatch.setattr(
        package_integration, "PANEL_BUNDLE", tmp_path / "never-built.js"
    )

    with pytest.raises(FileNotFoundError, match="npm run build"):
        package_integration.build(tmp_path / "staging")

    assert not (tmp_path / "staging").exists(), (
        "the refusal has to come before anything is written"
    )


def test_the_bundle_is_read_from_the_shipped_copy_before_the_mount(
    tmp_path: Path,
) -> None:
    """The two deployments, in the order the panel has to prefer them.

    A packaged install has only the shipped copy and a checkout has only the
    mount, so neither branch is exercised by running the thing in one of them --
    which is how a resolution rule comes to be wrong in the deployment nobody
    ran. Both are built here, side by side, so the *preference* is the thing
    under test: a bundle shipped with the integration is the one built with its
    commands, and a file in `www/` is whatever was last copied there.
    """
    module = _module_from(
        "_panel_bundle",
        package_integration.ROOT / package_integration.INTEGRATION / "panel_bundle.py",
    )
    config = tmp_path / "config"
    mounted = config / module.MOUNTED_DIRECTORY / module.BUNDLE_NAME
    mounted.parent.mkdir(parents=True)
    mounted.write_text("// the mounted build\n", "utf-8")

    # A checkout: nothing shipped beside the integration, so the mount answers.
    integration = tmp_path / "checkout"
    integration.mkdir()
    assert module.bundle_path(config, integration) == mounted

    # An install: the shipped copy is there, and it wins.
    shipped = integration / module.SHIPPED_DIRECTORY / module.BUNDLE_NAME
    shipped.parent.mkdir()
    shipped.write_text("// the shipped build\n", "utf-8")
    assert module.bundle_path(config, integration) == shipped

    # Neither: no answer, which is the caller's to report rather than a path to
    # a file that is not there. A directory of its own, because the checkout
    # above still carries the copy the second case wrote.
    bare = tmp_path / "bare"
    bare.mkdir()
    assert module.bundle_path(tmp_path / "empty", bare) is None
