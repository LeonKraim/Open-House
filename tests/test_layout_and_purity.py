"""Layout and engine purity -- task 1.3.

Every invariant here is tested twice: once on a fixture that violates it, and
once on a fixture that does not. The admitting case is not decoration. A check
that rejected every import under `engine/` would pass every violating fixture in
this file and be useless, so each rejection test is paired with the nearest
legal construct that must survive it.

The assertions read `Diagnostic.where` -- the artifact at fault -- and only fall
back to the message for the part that *is* the finding, such as which module was
imported. Matching the wording of a complaint would make rewording it a
behavioural change, which it is not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tools.catalog import invariants, paths, validate
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

PURITY = "engine-purity"
LAYOUT = "layout"


def _diagnostics(report: Report, check: str) -> list[str]:
    return [d.where for d in report.diagnostics if d.check == check]


def _purity_report() -> Report:
    report = Report()
    invariants.check_engine_purity(report)
    return report


def test_committed_tree_is_clean(real_root: Path) -> None:
    """The repository as committed satisfies both invariants."""
    report = Report()
    invariants.check_layout(report)
    invariants.check_engine_purity(report)
    assert report.diagnostics == [], report.render()


def test_engine_import_of_homeassistant_inside_try_is_rejected(fake_root: Path) -> None:
    """A guarded import is still an import.

    The guard is the whole reason this fixture exists: it is how a forbidden
    dependency gets into an engine while looking absent to a reader, and an
    implementation that walked module-level statements only would miss it.
    """
    write(
        fake_root,
        "engine/guarded.py",
        "try:\n    import homeassistant\nexcept ImportError:\n    "
        "homeassistant = None\n",
    )
    report = _purity_report()
    assert _diagnostics(report, PURITY) == ["engine/guarded.py"]
    assert "homeassistant" in report.render()


def test_engine_import_of_homeassistant_under_type_checking_is_rejected(
    fake_root: Path,
) -> None:
    """`TYPE_CHECKING` is the other way the same import hides."""
    write(
        fake_root,
        "engine/typed.py",
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from homeassistant.core import HomeAssistant\n",
    )
    report = _purity_report()
    assert _diagnostics(report, PURITY) == ["engine/typed.py"]
    assert "homeassistant" in report.render()


def test_engine_import_of_homeassistant_submodule_is_rejected(fake_root: Path) -> None:
    write(
        fake_root, "engine/sub.py", "import homeassistant.helpers.config_validation\n"
    )
    report = _purity_report()
    assert _diagnostics(report, PURITY) == ["engine/sub.py"]
    assert "homeassistant" in report.render()


def test_engine_import_of_undeclared_package_is_rejected(fake_root: Path) -> None:
    """A third-party import the `dependencies` array does not name.

    The fixture's `pyproject.toml` declares `pyyaml`; `requests` is the same
    shape of import and is not declared.
    """
    write(fake_root, "engine/extra.py", "import requests\n")
    report = _purity_report()
    assert _diagnostics(report, PURITY) == ["engine/extra.py"]
    assert "requests" in report.render()


def test_engine_import_of_declared_dependency_is_allowed(fake_root: Path) -> None:
    write(fake_root, "engine/ok.py", "import yaml\n")
    assert _purity_report().diagnostics == []


def test_engine_import_of_stdlib_and_first_party_is_allowed(fake_root: Path) -> None:
    write(
        fake_root,
        "engine/plain.py",
        "import json\nfrom pathlib import Path\nfrom sim import device\n",
    )
    assert _purity_report().diagnostics == []


def test_purity_reads_the_declared_list_and_not_a_hardcoded_one(
    fake_root: Path,
) -> None:
    """The dependency list is data, so adding to it admits the import.

    This is the test that stops the check from being a list of names someone
    typed into the checker, which would drift from `pyproject.toml` silently.
    """
    write(fake_root, "engine/extra.py", "import requests\n")
    assert _diagnostics(_purity_report(), PURITY) == ["engine/extra.py"]

    write(
        fake_root,
        "pyproject.toml",
        '[project]\nname = "fixture"\nversion = "0.0.0"\n'
        'dependencies = ["pyyaml>=6.0", "requests>=2.32"]\n',
    )
    assert _purity_report().diagnostics == []


def test_missing_package_marker_is_reported(fake_root: Path) -> None:
    (fake_root / "panel").mkdir()
    report = Report()
    invariants.check_layout(report)
    assert "engine" in _diagnostics(report, LAYOUT)
    assert any(
        d.where == "engine" and "py.typed" in d.message for d in report.diagnostics
    )


def test_a_package_that_does_not_import_is_reported(fake_root: Path) -> None:
    """Markers are not importability.

    A package can carry `__init__.py` and `py.typed` and still raise on import,
    and every later phase imports these packages before it does anything else,
    so a check that only looked at the files would pass a tree that does not
    load. The error is named, not merely the fact of failure.
    """
    write(fake_root, "engine/__init__.py", 'raise RuntimeError("boom")\n')
    write(fake_root, "engine/py.typed", "")
    report = Report()
    invariants.check_layout(report)
    messages = [d.message for d in report.diagnostics if d.where == "engine"]
    assert messages == ["python package does not import: RuntimeError: boom"]


def test_missing_panel_directory_is_reported(fake_root: Path) -> None:
    """`panel/` is checked for existence only, not for Python markers.

    The assertion is over the whole list of messages for `panel` rather than the
    last one, because a dict keyed by `where` would keep only the final
    diagnostic and would therefore pass even if `panel` had wrongly been treated
    as a Python package and picked up marker complaints.
    """
    report = Report()
    invariants.check_layout(report)
    assert [d.message for d in report.diagnostics if d.where == "panel"] == [
        "required directory is missing"
    ]


def test_a_non_python_module_is_not_asked_for_python_markers(fake_root: Path) -> None:
    """`panel/` and `packs/official/` are data, not packages.

    Both are present and neither carries `__init__.py` or `py.typed`. If either
    were in `TYPED_PACKAGES` the check would invent two faults for a tree that is
    exactly right, which is the failure a "required marker" check makes by
    default and the reason this case is asserted rather than assumed.
    """
    for package in ("engine", "ha_adapter", "sim", "custom_components"):
        write(fake_root, f"{package}/__init__.py", "")
        write(fake_root, f"{package}/py.typed", "")
    (fake_root / "panel").mkdir()
    write(fake_root, "packs/official/example-house.yaml", "house: {}\n")
    report = Report()
    invariants.check_layout(report)
    assert report.diagnostics == [], report.render()


def test_layout_passes_on_a_complete_tree(fake_root: Path) -> None:
    for package in ("engine", "ha_adapter", "sim", "custom_components"):
        write(fake_root, f"{package}/__init__.py", "")
        write(fake_root, f"{package}/py.typed", "")
    (fake_root / "panel").mkdir()
    write(fake_root, "packs/official/.gitkeep", "")
    report = Report()
    invariants.check_layout(report)
    assert report.diagnostics == [], report.render()


def test_declared_import_names_falls_back_when_metadata_is_unreadable(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented fallback, exercised rather than asserted in prose.

    `packages_distributions()` reads installed metadata, which can be unreadable
    on a broken environment. The fallback is the conservative direction: it may
    reject an import that would have resolved, but it can never admit one that is
    genuinely undeclared, and that is the direction the purity rule is for. So
    `yaml`, whose distribution the fallback can no longer resolve, is now
    rejected -- and the test says that is the expected behaviour rather than a
    bug, which is the whole point of pinning it.
    """

    def unreadable() -> dict[str, list[str]]:
        raise ValueError("metadata is unreadable")

    monkeypatch.setattr(
        invariants.importlib.metadata, "packages_distributions", unreadable
    )
    assert invariants.declared_import_names() == {"pyyaml"}

    write(fake_root, "engine/ok.py", "import yaml\n")
    assert _diagnostics(_purity_report(), PURITY) == ["engine/ok.py"]


def test_an_unreadable_dependency_list_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The validator is the pre-commit hook; a bad `pyproject.toml` must be named.

    `tomllib` raises `TOMLDecodeError` and a missing file raises `OSError`, and
    neither is a `CheckError`. Unhandled, they escape `validate_all` and the
    command users run before every commit dies with a stack trace instead of
    saying which file is wrong.
    """
    write(fake_root, "engine/anything.py", "import json\n")
    write(fake_root, "pyproject.toml", "this is not toml =\n")
    report = validate.validate_all()
    assert "pyproject.toml" in [d.where for d in report.diagnostics]


def test_paths_module_is_not_confused_with_the_catalog_directory() -> None:
    """`tools/` exists so `tools.catalog` and `catalog/` are unambiguous.

    `test_paths.py`-shaped, and kept here because it is the same claim the layout
    check makes: the package that reads `catalog/` is not the directory it reads.
    """
    assert paths.CATALOG.name == "catalog"
    assert paths.ROOT.name != "tools"
