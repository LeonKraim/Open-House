"""The dependency ledger -- section 5, task 5.4.

`catalog/integrations.yaml` answers one question -- given a dependency the four
setups rely on, what is this project to do with it -- and the answer is worth
nothing unless it is consistent with itself. Two ways of being inconsistent are
the spec's own scenarios, and both are invisible in the file read as prose. A
dependency three or more repos reach for is required, so an entry that records
the count and then disposes of it as `replace` says one thing in `repos` and the
opposite in `disposition`, and nothing in a bare list of dependencies would
catch it. And a `replace` or `avoid` names its successor, so one that names
nothing leaves a later phase with a decision and no destination.

The third failure the tests below pin is the quiet one: two entries carrying the
same name divide that dependency's repos between them, so the count each one
sees is short and the threshold that promotes a dependency to `require` is
defeated without any single entry looking wrong. That is why the fixtures here
are closed -- every entry valid -- so the one finding a test asserts is the one
thing about it that is not.

How the findings are collected. `integrations.check_integrations` is not yet in
`validate._CHECKS`, so these tests cannot route through `validate_all` the way
`tests/test_golden.py` does; they call the check directly and reproduce the same
`CheckError` arm, so a ledger that would reach the pre-commit hook as a
traceback reaches a test as the diagnostic it is meant to be instead. Wiring the
check into the registry is an edit to `tools/catalog/validate.py`, a file this
task does not own, and is reported rather than made.

The ledger is a hand-written extraction -- no check can confirm a repo uses what
the file says it uses without a clone, and CI has none -- so the tests here are
about the file holding together, not about it being exhaustive. The one thing
asserted about the committed data beyond internal consistency is that it carries
each of the three kinds, because an integration, an add-on and a bridge are
different dependencies and the task's clause is that the file hold all three.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import integrations, paths
from tools.catalog.errors import CheckError, Report

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

LEDGER_PATH = "catalog/integrations.yaml"

#: One representative of each kind the requirement names. Kind is not a schema
#: field -- the file records a dependency and what to do with it, not a
#: taxonomy -- so those three clauses are pinned by name here against the
#: committed ledger, which is the only form they can take without a `kind` field
#: the spec does not ask for.
REPRESENTATIVES = {
    "an integration": "hacs",
    "an add-on": "mosquitto",
    "a bridge": "mqtt",
}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _entry(
    name: str,
    repos: tuple[str, ...] = ("x",),
    disposition: str = "require",
    replacement: str | None = None,
) -> dict[str, object]:
    """One entry that satisfies every rule, so a test can break one.

    Complete by construction, for the reason `tests/test_golden.py` gives: a
    negative test built on an incomplete fixture would also trip the rules the
    complete fixture satisfies, and would go on passing after the behaviour it
    means to test had been removed. One repo by default, so nothing trips the
    wide-use rule unless a test asks it to.
    """
    return {
        "name": name,
        "repos": list(repos),
        "disposition": disposition,
        "replacement": replacement,
    }


def _ledger(root: Path, entries: list[dict[str, object]]) -> None:
    write(
        root,
        LEDGER_PATH,
        yaml.safe_dump({"integrations": entries}, sort_keys=False),
    )


def _diagnostics() -> list[tuple[str, str]]:
    """The ledger check's findings, collected the way the hook collects them.

    Called directly rather than through `validate_all`, because the check is not
    yet in `validate._CHECKS`, with the `CheckError` arm reproduced here rather
    than borrowed from it. The arm is not decoration: the property the parse and
    missing-file tests are about is that a broken ledger is a *diagnostic*, and
    without it a `CheckError` would escape as a traceback and the test would
    still pass.
    """
    report = Report()
    try:
        integrations.check_integrations(report)
    except CheckError as exc:
        report.add(exc.check, exc.where, exc.message)
    return [
        (d.where, d.message)
        for d in report.diagnostics
        if d.check == integrations.INTEGRATIONS_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The spec's first scenario: a widely used dependency is required
# --------------------------------------------------------------------------


def test_a_widely_used_dependency_that_is_not_required_names_it_and_its_repos(
    fake_root: Path,
) -> None:
    """The count is the evidence, so the finding names the dependency and the
    repos it counted -- a reader has to be able to check the number against the
    four setups rather than take it on trust."""
    _ledger(
        fake_root,
        [
            _entry(
                "mqtt",
                repos=("a", "b", "c"),
                disposition="replace",
                replacement="mosquitto",
            )
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    where, message = findings[0]
    assert where.endswith(f"{LEDGER_PATH}:mqtt")
    assert "is used by 3 repos (a, b, c)" in message
    assert "disposition is 'replace'" in message
    assert "is `require`" in message


@pytest.mark.parametrize("disposition", ["replace", "avoid"])
def test_a_widely_used_dependency_disposed_of_as_replace_or_avoid_is_reported(
    fake_root: Path, disposition: str
) -> None:
    """Every non-`require` disposition is the same failure. Parametrised so a
    check that only caught one of them cannot pass, and each fixture names a
    successor so the only thing wrong with it is the disposition."""
    _ledger(
        fake_root,
        [
            _entry(
                "zigbee2mqtt",
                repos=("a", "b", "c"),
                disposition=disposition,
                replacement="successor",
            )
        ],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "is used by 3 repos" in findings[0][1]
    assert f"disposition is {disposition!r}" in findings[0][1]


def test_a_dependency_used_by_two_repos_is_below_the_threshold(fake_root: Path) -> None:
    """The rule is "three or more", so two is the boundary's quiet side and must
    not fail: at two, one setup copied from another would be enough to promote a
    habit to a rule."""
    _ledger(
        fake_root,
        [
            _entry(
                "espresense",
                repos=("a", "b"),
                disposition="replace",
                replacement="successor",
            )
        ],
    )

    assert _diagnostics() == [], _messages()


# --------------------------------------------------------------------------
# The spec's second scenario: a replace or avoid names its successor
# --------------------------------------------------------------------------


@pytest.mark.parametrize("disposition", ["replace", "avoid"])
def test_a_non_required_disposition_that_names_no_successor_is_named(
    fake_root: Path, disposition: str
) -> None:
    """The spec's clause, on both dispositions that carry it. A disposition with
    nothing behind it is an opinion, so the finding names the entry and asks for
    the successor."""
    _ledger(fake_root, [_entry("updater", disposition=disposition)])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert findings[0][0].endswith(f"{LEDGER_PATH}:updater")
    assert "names no replacement" in findings[0][1]


def test_a_require_that_names_a_successor_is_named(fake_root: Path) -> None:
    """The clause read backwards. `replacement` holds what took the
    dependency's place, so a `require`, which is still in place, has nothing to
    put there -- an entry with both set answers "what do we do with this" twice
    and in opposite directions."""
    _ledger(
        fake_root,
        [_entry("mqtt", disposition="require", replacement="mosquitto")],
    )

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "is `require` but names a replacement ('mosquitto')" in findings[0][1]


def test_a_successor_that_is_the_dependency_itself_is_named(fake_root: Path) -> None:
    """A successor is what took the dependency's place, and a thing cannot take
    its own -- an entry that names itself has named nothing, which the schema
    cannot see because a non-empty string is all it asks for."""
    _ledger(fake_root, [_entry("zwave", disposition="replace", replacement="zwave")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "names itself ('zwave') as its replacement" in findings[0][1]


# --------------------------------------------------------------------------
# The closed vocabulary, and the entry that is wrong on its own
# --------------------------------------------------------------------------


def test_a_disposition_outside_the_closed_set_is_named(fake_root: Path) -> None:
    """A disposition outside `require`/`replace`/`avoid` records a dependency
    without saying whether the product may take it, which is the one question
    the ledger exists to answer."""
    _ledger(fake_root, [_entry("x", disposition="optional")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "has disposition 'optional'" in findings[0][1]
    assert "not one of" in findings[0][1]


def test_an_entry_with_no_name_is_named(fake_root: Path) -> None:
    """A dependency with no name states nothing about what it is, and the
    finding names the slot so a reader can find the line."""
    _ledger(fake_root, [_entry("")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert findings[0][0].endswith(f"{LEDGER_PATH}:<unnamed>")
    assert "carries no `name`" in findings[0][1]


def test_a_dependency_recorded_twice_is_named(fake_root: Path) -> None:
    """Both entries pass every per-entry check, and nothing but this can see the
    duplicate -- while each sees a repo count short by however many the other
    holds, which is the threshold defeated."""
    _ledger(fake_root, [_entry("mqtt"), _entry("mqtt")])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert findings[0][0].endswith(f"{LEDGER_PATH}:mqtt")
    assert "is recorded twice" in findings[0][1]


def test_an_empty_ledger_records_no_integrations(fake_root: Path) -> None:
    """A ledger that records nothing answers "what may we build on" by silence,
    which reads identically to a ledger whose every entry was deleted."""
    _ledger(fake_root, [])

    findings = _diagnostics()
    assert len(findings) == 1, _messages()
    assert "records no integrations" in findings[0][1]


# --------------------------------------------------------------------------
# Reading the committed file
# --------------------------------------------------------------------------


def test_a_ledger_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: Path,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else, so an
    unguarded read here would reach it as a traceback and take the findings of
    every other check with it."""
    write(fake_root, LEDGER_PATH, "integrations: [\n")

    assert "cannot be parsed" in _messages()


def test_a_missing_ledger_is_named(fake_root: Path) -> None:
    """An absent file read as an empty one would let every entry check pass on
    nothing, which on the committed tree is the difference between a full ledger
    and one that has been removed."""
    assert "does not exist" in _messages()


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    """`fake_root` has no `ressources/`, and the suite runs in a checkout without
    it. The ledger was extracted from the clones once and committed, so the check
    must do its work against the committed file alone."""
    assert not paths.RESSOURCES.exists()

    _ledger(fake_root, [_entry("x", disposition="optional")])
    assert "has disposition 'optional'" in _messages()


# --------------------------------------------------------------------------
# The committed ledger
# --------------------------------------------------------------------------


def test_the_committed_ledger_passes_its_own_checks() -> None:
    assert _diagnostics() == []


def test_the_committed_ledger_records_at_least_one_dependency() -> None:
    """The clause above is also true of an empty file, which is why the ledger
    being non-empty is a separate assertion rather than a comment."""
    assert integrations.load_integrations()


def test_the_committed_ledger_names_each_dependency_once() -> None:
    names = [entry.name for entry in integrations.load_integrations()]
    assert len(names) == len(set(names))


def test_every_committed_widely_used_dependency_is_required() -> None:
    """The spec's first scenario, stated directly against the committed data
    rather than through the check that would report it."""
    for entry in integrations.load_integrations():
        if entry.repo_count >= integrations.WIDE_USE_THRESHOLD:
            assert entry.disposition == "require", entry.name


def test_every_committed_replace_or_avoid_names_a_replacement() -> None:
    """The spec's second scenario, likewise stated against the data."""
    for entry in integrations.load_integrations():
        if entry.disposition in ("replace", "avoid"):
            assert entry.replacement, entry.name


def test_the_committed_ledger_holds_an_integration_an_add_on_and_a_bridge() -> None:
    """The requirement is that the file carry each of the three kinds, and they
    are genuinely different dependencies -- a HACS integration, a Supervisor
    add-on and a bridge to another ecosystem fail and are replaced for different
    reasons. Kind is not a schema field, so the coverage is pinned by name."""
    names = {entry.name for entry in integrations.load_integrations()}
    for kind, name in REPRESENTATIVES.items():
        assert name in names, f"{kind} ({name}) is not recorded"


#: The dependencies a first pass omitted and the sweep across all four clones
#: restored. Each is exactly the kind of evidence the file's header declares
#: counts -- a `platform:` reference, a `custom_components/` directory, or a
#: per-integration config directory -- so their absence was a gap that a later
#: edit could reopen without any test noticing. Pinned by name rather than by a
#: count, because a count would be satisfied by ten unrelated additions.
SWEEP_RESTORED = (
    "feedparser",
    "tgtg",
    "brewdog",
    "meteoalarm",
    "whatsapper",
    "syslog",
    "html5",
    "github_custom",
    "doomsday_clock",
    "rtl4332mqtt",
)


def test_the_ledger_records_the_dependencies_the_sweep_restored() -> None:
    """A ledger that is a sample rather than the evidence it claims to hold is
    the defect the sweep fixed, and nothing in the file reads as wrong when an
    entry is merely missing."""
    names = {entry.name for entry in integrations.load_integrations()}
    missing = [name for name in SWEEP_RESTORED if name not in names]
    assert not missing, missing


def test_the_ios_row_names_the_repos_whose_config_uses_it() -> None:
    """The row is a claim about the clones, and the clone evidence is that
    ccostan's only `ios` line is commented out (`config/packages/ios.yaml`),
    while renemarc includes `homekit`-era `ios:` and johnkoht fires
    `ios.action_fired` and notifies `ios_family`. The row therefore names those
    two and not ccostan; a re-added ccostan would make a two-repo dependency
    read as three and force a `require` it is not."""
    row = next(e for e in integrations.load_integrations() if e.name == "ios")
    assert set(row.repos) == {"johnkoht", "renemarc"}
    assert row.repo_count < integrations.WIDE_USE_THRESHOLD


def test_the_closed_vocabulary_matches_the_schema_enum() -> None:
    """The set is written down twice -- here and in the schema's `enum` -- and a
    check that agreed with an `enum` widened to admit a disposition nobody chose
    would be no check at all. This is the drift between the two statements."""
    document = json.loads(
        (paths.SCHEMA_CATALOG / "integrations.json").read_bytes().decode("utf-8")
    )
    enum = document["$defs"]["disposition"]["enum"]
    assert set(enum) == set(integrations.DISPOSITIONS)
