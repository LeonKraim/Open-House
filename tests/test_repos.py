"""The repo identification records -- section 4, task 4.1.

`catalog/repos.yaml` is the project's statement of what each reference repo is
and what it is for. Two kinds of mistake matter, and they fail in different
places. There is the record that is wrong on its own -- an empty field, a
`ha_style` list with a value nobody defined or the same value twice -- and there
is the *set* of records being wrong: a repo the licence file names that has no
record, a record for a repo no licence names, a repo recorded twice.

Testing only the committed file would prove that today's data passes, never that
the check can fail, and a check seen only to pass is a check nobody has seen
work. So the failures are driven on trees the tests build, in the
fixtures-both-ways arrangement `conftest` describes, and the committed file is
asserted separately.

The `ha_version` clause is the one that needs a clone, and it is tested in the
two places it can be: against clones the tests build, where every branch is
reachable, and against the real clones, skipped when they are absent because CI
runs without them. `check_ha_versions` is deliberately *not* in the check
registry -- `tools/catalog/validate.py` runs `check_repos` alone -- so the local
tests drive `check_ha_versions` directly rather than through `validate_all`.

The tests that go through `validate_all` depend on `repos.check_repos` being in
that module's `_CHECKS` tuple; wiring it there is the coordinator's edit, and
until it is made these tests would find no `repos` diagnostics.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import inventory, licenses, paths, repos, validate
from tools.catalog.errors import Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

REPOS_PATH = "catalog/repos.yaml"
LICENCES_PATH = "catalog/licenses.yaml"

#: The four repos, by the owner handle the record's `name` carries. Kept as
#: literals rather than read from `licenses.yaml`: a test that takes its
#: expectation from a file it is checking agrees with any file, including one
#: where a repo was dropped.
EXPECTED_NAMES = {"ccostan", "renemarc", "fwartner", "johnkoht"}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _record(repo: str, **overrides: object) -> dict[str, object]:
    """A record that satisfies every per-field rule, so a test can break one.

    Complete by construction for the reason `tests/test_golden.py` gives: a
    negative test built on an incomplete fixture would also trip the checks the
    complete fixture satisfies, and would go on passing after the behaviour it
    means to test had been removed.

    The parameter is `repo` and not `name`, which is what it was until an empty
    `name` test was written and could not be: the record's own `name` field is
    passed as a keyword override, so a positional parameter of the same name
    makes `_record("x", name="")` a duplicate argument rather than a fixture.
    """
    record: dict[str, object] = {
        "name": repo,
        "repo_url": f"https://github.com/{repo}/example",
        "author": "An Author",
        "purpose": "A purpose.",
        "ha_style": ["package"],
        "scale": "Small: one tracked file.",
        "ha_version": "2026.1.1",
        "best_at": "The one thing it contributes.",
    }
    record.update(overrides)
    return record


def _records(root: Path, records: list[dict[str, object]]) -> None:
    write(
        root,
        REPOS_PATH,
        yaml.safe_dump({"repos": records}, sort_keys=False, allow_unicode=True),
    )


def _licences(root: Path, handles: list[str]) -> None:
    write(
        root,
        LICENCES_PATH,
        yaml.safe_dump(
            {"repos": [{"repo": name} for name in handles]},
            sort_keys=False,
            allow_unicode=True,
        ),
    )


def _tree(root: Path, records: list[dict[str, object]], handles: list[str]) -> None:
    """A repos file and the licence file it is matched against."""
    _records(root, records)
    _licences(root, handles)


def _clone(root: Path, handle: str, name: str, files: dict[str, str]) -> Path:
    """A repository under `ressources/` with those files tracked and that remote.

    A real repository, not a stub: `check_ha_versions` reaches the clone through
    `inventory.discover_clones()`, which reads the handle off the `origin`
    remote, so a fixture that handed it a directory would be testing neither the
    handle match nor the version read.
    """
    clone = root / "ressources" / name
    clone.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q"),
        ("config", "user.email", "clone@example.invalid"),
        ("config", "user.name", "clone"),
        ("config", "commit.gpgsign", "false"),
        ("config", "core.autocrlf", "false"),
    ):
        subprocess.run(["git", *args], cwd=clone, capture_output=True, check=True)
    for relative, text in files.items():
        write(clone, relative, text)
    subprocess.run(["git", "add", "-A"], cwd=clone, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clone"],
        cwd=clone,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", f"https://github.com/{handle}/{name}.git"],
        cwd=clone,
        capture_output=True,
        check=True,
    )
    return clone


def _diagnostics() -> list[tuple[str, str]]:
    """The check's findings, collected the way the hook collects them.

    Through `validate_all` rather than by calling `check_repos` directly, for
    the reason `tests/test_golden.py` gives: the property several of these tests
    are about is that a broken file produces a *report*, and a report is what the
    command at the outermost boundary returns. A direct call would let a
    `CheckError` escape as a traceback and the test would still pass.

    This relies on `repos.check_repos` being one of `validate.py`'s registered
    checks; that registration is a change to a file this task does not own and
    is reported rather than made here.
    """
    report = validate.validate_all()
    return [
        (d.where, d.message) for d in report.diagnostics if d.check == repos.REPOS_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


def _local() -> list[tuple[str, str]]:
    """`check_ha_versions`' findings, driven directly.

    Not through `validate_all`, because the function is local-only and is not in
    the registry: routing it through the outer command would test a wiring this
    module's contract says must not exist.
    """
    report = Report()
    repos.check_ha_versions(report)
    return [
        (d.where, d.message) for d in report.diagnostics if d.check == repos.REPOS_CHECK
    ]


# --------------------------------------------------------------------------
# The committed records
# --------------------------------------------------------------------------


def test_the_committed_records_pass_their_own_checks() -> None:
    assert _diagnostics() == []


def test_the_committed_records_name_the_four_repos_exactly_once_each() -> None:
    """One record per repo -- no more, no fewer -- stated against the names the
    spec gives rather than against the licence file, so a repo dropped from both
    would still be caught."""
    listed = [record.name for record in repos.load_repos()]
    assert set(listed) == EXPECTED_NAMES
    assert len(listed) == len(set(listed))


def test_the_committed_records_and_the_licence_records_agree() -> None:
    """The two files are the project's two statements of which repos it draws
    from; a repo in one and not the other is a source whose grant and identity
    have come apart."""
    recorded = {record.repo for record in licenses.load_licences() if record.repo}
    assert {record.name for record in repos.load_repos()} == recorded


def test_every_committed_field_is_non_empty() -> None:
    for record in repos.load_repos():
        for field in repos.REQUIRED_FIELDS:
            assert getattr(record, field), f"{record.name}: {field}"

        assert record.ha_style, record.name


def test_every_committed_ha_style_is_inside_the_closed_set() -> None:
    for record in repos.load_repos():
        for style in record.ha_style:
            assert style in repos.HA_STYLES, f"{record.name}: {style}"


def test_no_committed_ha_style_list_repeats_a_value() -> None:
    for record in repos.load_repos():
        assert len(record.ha_style) == len(set(record.ha_style)), record.name


def test_renemarc_records_the_three_styles_the_spec_names() -> None:
    """The spec calls renemarc out by name -- split-include, custom-integration
    and appdaemon -- so the one worked example is pinned as a set membership,
    not an order: the list is unordered and asking for an order would be a claim
    nothing can check."""
    renemarc = [record for record in repos.load_repos() if record.name == "renemarc"]
    assert len(renemarc) == 1
    assert set(renemarc[0].ha_style) == {
        "split-include",
        "custom-integration",
        "appdaemon",
    }


def test_the_closed_set_matches_the_schema_enum() -> None:
    """The set is written down twice -- here and in the schema's `enum` -- and a
    check that agreed with a schema widened to admit a style nobody chose would
    be no check at all. This is the drift between the two statements."""
    document = json.loads(
        (paths.SCHEMA_CATALOG / "repos.json").read_bytes().decode("utf-8")
    )
    enum = document["$defs"]["repo"]["properties"]["ha_style"]["items"]["enum"]
    assert set(enum) == set(repos.HA_STYLES)


# --------------------------------------------------------------------------
# A record that is wrong on its own
# --------------------------------------------------------------------------


#: Every scalar field but `name`, which gets its own test: an empty `name` also
#: removes the record from the set, so it produces a second, different finding
#: and cannot share a fixture that asserts exactly one.
SCALAR_FIELDS = tuple(field for field in repos.REQUIRED_FIELDS if field != "name")


@pytest.mark.parametrize("field", sorted(SCALAR_FIELDS))
def test_an_empty_field_names_the_repo_and_the_field(
    fake_root: Path, field: str
) -> None:
    """The spec's scenario: a record with an empty field fails naming the repo
    and the field. Parametrised over every such field, because one field standing
    in for the rest would leave a check that dropped another passing."""
    _tree(fake_root, [_record("x", **{field: ""})], ["x"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith(f"{REPOS_PATH}:x")
    assert f"`{field}` is empty" in message


def test_an_empty_name_is_named_and_drops_the_record_from_the_set(
    fake_root: Path,
) -> None:
    """An empty `name` is two failures at once, and both are real: the field is
    empty, and a record with no name is a repo the set no longer names. Folding
    them into one assertion would hide whichever the check stopped doing."""
    _tree(fake_root, [_record("x", name="")], ["x"])

    messages = _messages()
    assert "`name` is empty" in messages
    assert "no record names `x`" in messages


def test_an_empty_ha_style_list_is_named(fake_root: Path) -> None:
    """A repo has at least one structural style, so an empty list is a record
    that states nothing about how the repo is built."""
    _tree(fake_root, [_record("x", ha_style=[])], ["x"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "carries no `ha_style`" in findings[0][1]


def test_a_style_outside_the_closed_set_is_named(fake_root: Path) -> None:
    _tree(fake_root, [_record("x", ha_style=["package", "nonsense"])], ["x"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "'nonsense'" in findings[0][1]
    assert "outside the closed set" in findings[0][1]


def test_a_repeated_style_is_named(fake_root: Path) -> None:
    """The list is a set of styles the repo exhibits, so a repeat is the same
    claim twice and the value is named rather than the count."""
    _tree(fake_root, [_record("x", ha_style=["package", "package"])], ["x"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "lists 'package' more than once" in findings[0][1]


# --------------------------------------------------------------------------
# A set of records that is wrong
# --------------------------------------------------------------------------


def test_a_repo_with_a_licence_record_and_no_identification_record(
    fake_root: Path,
) -> None:
    _tree(fake_root, [_record("x")], ["x", "y"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "no record names `y`" in findings[0][1]


def test_a_record_for_a_repo_with_no_licence_record(fake_root: Path) -> None:
    _tree(fake_root, [_record("x"), _record("y")], ["x"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "records `y`, which has no licence record" in findings[0][1]


def test_a_repo_recorded_twice_is_named(fake_root: Path) -> None:
    """Both copies pass the per-record checks; nothing but this can see the
    duplicate, and the later copy is not the one a reader trusts."""
    _tree(fake_root, [_record("x"), _record("x")], ["x"])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "appears more than once" in findings[0][1]


# --------------------------------------------------------------------------
# Reading the committed file
# --------------------------------------------------------------------------


def test_a_repos_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else, so an
    unguarded read here would reach it as a traceback and take the findings of
    every other check with it."""
    write(fake_root, REPOS_PATH, "repos: [\n")
    _licences(fake_root, ["x"])

    assert "cannot be parsed" in _messages()


def test_a_missing_repos_file_is_named(fake_root: Path) -> None:
    """An absent file read as an empty one would let every per-record check pass
    on nothing, which on the committed tree is the difference between four
    records being right and all four being gone."""
    _licences(fake_root, ["x"])

    assert "does not exist" in _messages()


def test_the_committed_half_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/`, and the suite runs in a checkout
    without it. The CI half must do its work without reaching for one."""
    assert not paths.RESSOURCES.exists()

    _tree(fake_root, [_record("x", ha_style=["nonsense"])], ["x"])
    assert "outside the closed set" in _messages()


# --------------------------------------------------------------------------
# The ha_version clause -- against clones the tests build
# --------------------------------------------------------------------------


def test_the_version_is_read_from_the_clone_root(fake_root: Path) -> None:
    clone = _clone(fake_root, "x", "x-config", {".HA_VERSION": "2026.1.1\n"})

    assert repos.read_ha_version(clone) == "2026.1.1"


def test_the_version_is_read_under_config_when_that_is_where_it_lives(
    fake_root: Path,
) -> None:
    """CCOSTAN's clone keeps `.HA_VERSION` under `config/`, not at the root --
    the case the task calls out by name, and the reason the read tries more than
    one location rather than assuming the root."""
    clone = _clone(
        fake_root,
        "ccostan",
        "Home-AssistantConfig",
        {"config/.HA_VERSION": "2026.9.4\n"},
    )

    assert repos.read_ha_version(clone) == "2026.9.4"


def test_a_clone_with_no_version_file_reads_as_nothing(fake_root: Path) -> None:
    """None rather than an empty string, so a clone that declares no version is
    not mistaken for one that declares the empty version."""
    clone = _clone(
        fake_root, "x", "x-config", {"configuration.yaml": "default_config:\n"}
    )

    assert repos.read_ha_version(clone) is None


def test_a_recorded_version_that_disagrees_with_the_clone_is_named(
    fake_root: Path,
) -> None:
    _clone(fake_root, "example", "example-config", {".HA_VERSION": "2026.1.1\n"})
    _tree(fake_root, [_record("example", ha_version="2026.0.0")], ["example"])

    findings = _local()
    assert len(findings) == 1, findings
    assert "2026.0.0" in findings[0][1]
    assert "2026.1.1" in findings[0][1]


def test_a_recorded_repo_with_no_clone_is_named(fake_root: Path) -> None:
    """A repo the record names that has no clone cannot have its version
    confirmed, and the run says so rather than passing it silently."""
    _tree(fake_root, [_record("example")], ["example"])

    findings = _local()
    assert len(findings) == 1, findings
    assert "no clone" in findings[0][1]


def test_a_clone_with_no_version_file_is_reported_for_its_record(
    fake_root: Path,
) -> None:
    _clone(fake_root, "example", "example-config", {"configuration.yaml": "x: 1\n"})
    _tree(fake_root, [_record("example")], ["example"])

    findings = _local()
    assert len(findings) == 1, findings
    assert "no readable" in findings[0][1]


# --------------------------------------------------------------------------
# The ha_version clause -- against the real clones, local only
# --------------------------------------------------------------------------

_clones_present = paths.RESSOURCES.is_dir()


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_every_real_clone_declares_its_recorded_version() -> None:
    """The clause on the repositories themselves. Read directly, so the assertion
    states the fact rather than the absence of a diagnostic."""
    clones = inventory.discover_clones()
    for record in repos.load_repos():
        assert repos.read_ha_version(clones[record.name]) == record.ha_version, (
            record.name
        )


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_the_committed_records_match_the_real_clones() -> None:
    assert _local() == []
