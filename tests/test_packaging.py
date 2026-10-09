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
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from typing import TYPE_CHECKING

from tools import package_integration

if TYPE_CHECKING:
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


def _bootstrap_from(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_packaged_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_asset_ships_every_first_party_import(tmp_path: Path) -> None:
    root = package_integration.build(tmp_path / "staging")
    integration = root / package_integration.INTEGRATION
    shipped = [integration, *(root / name for name in package_integration.PACKAGES)]

    needed = _imported_names(*shipped) & FIRST_PARTY
    missing = sorted(name for name in needed if not (integration / name).is_dir())

    assert not missing, (
        f"the asset does not carry {missing}, which the integration imports; "
        "add them to package_integration.PACKAGES"
    )


def test_the_asset_carries_the_data_it_reads(tmp_path: Path) -> None:
    integration = package_integration.build(tmp_path / "staging") / (
        package_integration.INTEGRATION
    )
    for name in package_integration.DATA:
        assert (integration / name).is_dir(), (
            f"{name}/ is read at runtime but not shipped"
        )
    assert (integration / "catalog" / "room_types.yaml").is_file()


def test_the_asset_carries_nothing_this_machine_produced(tmp_path: Path) -> None:
    integration = package_integration.build(tmp_path / "staging") / (
        package_integration.INTEGRATION
    )
    names = [path.name for path in integration.rglob("*")]

    assert "__pycache__" not in names
    assert not any(name.endswith(".pyc") for name in names)
    assert not (integration / "tools" / "ha").exists()


def test_the_archive_root_is_the_path_hacs_copies(tmp_path: Path) -> None:
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
    tmp_path: Path, monkeypatch
) -> None:
    root = package_integration.build(tmp_path / "staging")
    integration = root / package_integration.INTEGRATION

    # No mount, and no env override pointing anywhere real: the only answer left
    # is the packages the integration was shipped with.
    monkeypatch.setenv("OPEN_HOUSE_SRC", str(tmp_path / "not-a-checkout"))
    saved = list(sys.path)
    try:
        module = _bootstrap_from(integration / "_bootstrap.py")
        assert module.shipped_root() == integration.resolve()
        assert module.source_root() == integration.resolve()
    finally:
        sys.path[:] = saved
