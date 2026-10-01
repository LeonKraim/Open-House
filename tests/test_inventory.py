"""The file inventory -- section 3, task 3.3.

The inventory is the only artifact built by reading the four clones, and the
property it has to have is that it closes: selected plus excluded equals the
repo's tracked count, every file names the rule that decided it, and the per-rule
and per-class maps cover exactly the files beside them. A generator that dropped
a file would produce a corpus that is quietly smaller, and nothing in a corpus
defined as "whatever the rules select" can be noticed to be missing from itself.

So the closure is tested twice, in the two places it can fail. Against a
repository the test builds, where the file list is known and every branch of the
walker can be reached; and against the real clones, where the number that matters
is the one `git ls-files` actually prints -- skipped when the clones are absent,
since CI runs without them and the whole point of committing this artifact is
that CI never needs them.

The third part is the check that runs in CI: `catalog/inventory.json` cannot be
recounted there, so it has to be self-closing, and the tests below break it one
number at a time to confirm each reconciliation is load-bearing.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest
import yaml

from tools.catalog import inventory, licenses, paths, rules, validate
from tools.catalog.errors import CheckError

from .conftest import write

if TYPE_CHECKING:
    from pathlib import Path

RULES_PATH = "catalog/file_rules.yaml"
INVENTORY_PATH = "catalog/inventory.json"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _rule(
    order: int,
    pattern: str,
    kind: str = "select",
    artifact_class: str | None = "package",
    reason: str = "a reason",
) -> dict[str, object]:
    return {
        "order": order,
        "pattern": pattern,
        "kind": kind,
        "class": artifact_class,
        "reason": reason,
    }


def _exclude(order: int, pattern: str, reason: str = "a reason") -> dict[str, object]:
    return _rule(order, pattern, kind="exclude", artifact_class=None, reason=reason)


def _rules(root: Path, entries: list[dict[str, object]]) -> None:
    write(root, RULES_PATH, yaml.safe_dump({"rules": entries}, sort_keys=False))


def _rules_with_authored(
    root: Path,
    entries: list[dict[str, object]],
    authored: list[dict[str, object]],
) -> None:
    write(
        root,
        RULES_PATH,
        yaml.safe_dump({"rules": entries, "authored_paths": authored}, sort_keys=False),
    )


def _licences(root: Path, handles: list[str]) -> None:
    write(
        root,
        "catalog/licenses.yaml",
        yaml.safe_dump(
            {"repos": [{"repo": name} for name in handles]}, sort_keys=False
        ),
    )


def _clone(root: Path, handle: str, name: str, files: dict[str, str]) -> Path:
    """A repository under `ressources/`, with those files tracked.

    A real repository and not a stub, because `build()` reads the clone through
    `git ls-files`: the closure this task is about is a closure over what git
    reports, and a fixture that handed the walker a list would be testing the
    walker's arithmetic against its own input.
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


GOOD_YAML = "automation:\n  - alias: a\n    trigger: []\n"
BAD_YAML = "automation:\n  - alias: a\n   trigger: []\n"
PYTHON = "DOMAIN = 'x'\n"


def _simple_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    """One clone and its licence record, one rule per kind of file."""
    contents = (
        files
        if files is not None
        else {
            "automations.yaml": GOOD_YAML,
            "script.py": PYTHON,
            "notes.md": "# notes\n",
        }
    )
    clone = _clone(root, "example", "example-config", contents)
    _licences(root, ["example"])
    return clone


def _record(repo: dict[str, object], path: str) -> dict[str, object]:
    for entry in repo["files"]:  # type: ignore[union-attr]
        if entry["path"] == path:
            return entry  # type: ignore[return-value]
    raise AssertionError(f"{path} is not in the inventory")


def _diagnostics() -> list[tuple[str, str]]:
    """The check's findings, collected the way the hook collects them.

    Through `validate_all` rather than by calling `check_inventory` directly,
    for the reason the other test modules give: the property several of these are
    about is that a broken file produces a *report*, and a report is what the
    command at the outermost boundary returns. Calling the check directly would
    let a `CheckError` escape as a traceback and the test would still pass.
    """
    report = validate.validate_all()
    return [
        (d.where, d.message)
        for d in report.diagnostics
        if d.check == inventory.INVENTORY_CHECK
    ]


def _messages() -> str:
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The closure, over a repository the test builds
# --------------------------------------------------------------------------


def test_selected_and_excluded_close_over_the_clones_tracked_files(
    fake_root: Path,
) -> None:
    """The task's own verification clause, on files whose count is known."""
    clone = _simple_repo(fake_root)
    _rules(
        fake_root,
        [
            _rule(10, "**/*.yaml", artifact_class="automation"),
            _exclude(20, "**/*.py", reason="code"),
            _exclude(30, "**/*.md", reason="prose"),
        ],
    )

    document = inventory.build()
    (repo,) = document["repos"]  # type: ignore[misc]
    counted = repo["counts"]

    tracked = inventory.tracked_files(clone)
    assert counted["tracked"] == len(tracked) == 3
    assert counted["selected"] + counted["excluded"] == counted["tracked"]
    assert counted["excluded"] == 2


def test_every_tracked_file_appears_in_the_file_list(fake_root: Path) -> None:
    """The closure is over the list, not only over the count -- a count can be
    right while a file is missing from the list and another is duplicated."""
    clone = _simple_repo(fake_root)
    _rules(fake_root, [_rule(10, "**/*", artifact_class="package")])

    document = inventory.build()
    (repo,) = document["repos"]  # type: ignore[misc]
    assert [entry["path"] for entry in repo["files"]] == list(
        inventory.tracked_files(clone)
    )


def test_a_tracked_file_no_rule_matches_is_named_and_stops_the_run(
    fake_root: Path,
) -> None:
    _simple_repo(fake_root)
    _rules(fake_root, [_rule(10, "**/*.yaml", artifact_class="automation")])

    with pytest.raises(CheckError) as caught:
        inventory.build()
    assert "notes.md" in str(caught.value)
    assert "matched by no rule" in str(caught.value)


def test_a_paths_class_and_rule_come_from_the_lower_order_match(
    fake_root: Path,
) -> None:
    _simple_repo(fake_root, {"automations.yaml": GOOD_YAML})
    _rules(
        fake_root,
        [
            _rule(30, "**/*.yaml", artifact_class="package"),
            _rule(10, "**/automations*", artifact_class="automation"),
        ],
    )

    document = inventory.build()
    (repo,) = document["repos"]  # type: ignore[misc]
    entry = _record(repo, "automations.yaml")
    assert entry["class"] == "automation"
    assert entry["rule"] == 10


# --------------------------------------------------------------------------
# Parse outcome
# --------------------------------------------------------------------------


def test_a_selected_file_that_parses_is_recorded_parsed(fake_root: Path) -> None:
    _simple_repo(fake_root, {"automations.yaml": GOOD_YAML})
    _rules(fake_root, [_rule(10, "**/*.yaml", artifact_class="automation")])

    (repo,) = inventory.build()["repos"]  # type: ignore[misc]
    entry = _record(repo, "automations.yaml")
    assert entry["parse"] == "parsed"
    assert entry["error"] is None


def test_an_unparseable_file_keeps_its_class_and_records_the_error(
    fake_root: Path,
) -> None:
    """Task 3.7's clause, in the walker: the file keeps its class, is recorded
    `unparsed` with the error, and still counts in the class total."""
    _simple_repo(fake_root, {"automations.yaml": BAD_YAML})
    _rules(fake_root, [_rule(10, "**/*.yaml", artifact_class="automation")])

    (repo,) = inventory.build()["repos"]  # type: ignore[misc]
    entry = _record(repo, "automations.yaml")
    assert entry["class"] == "automation"
    assert entry["parse"] == "unparsed"
    assert entry["error"]
    assert "line" in entry["error"]
    assert repo["by_class"]["automation"] == 1
    assert repo["by_parse"]["unparsed"] == 1


def test_an_unparsed_file_is_not_an_inventory_failure(fake_root: Path) -> None:
    """Task 3.7's exit-0 clause, at the layer that decides it.

    An unparsed file is a fact about the corpus rather than a defect in it: it
    keeps its class and stays in the counts, so the validator has nothing to
    refuse. The test above says where the file *is*; this one says it is allowed
    to be there, and without it the two obvious over-readings -- refusing the
    file, or dropping it from `by_class` so the totals still look tidy -- would
    both pass.
    """
    _simple_repo(fake_root, {"automations.yaml": BAD_YAML})
    _rules(fake_root, [_rule(10, "**/*.yaml", artifact_class="automation")])
    write(fake_root, INVENTORY_PATH, inventory.render(inventory.build()))

    assert not _messages()


def test_home_assistant_tags_are_not_parse_failures(fake_root: Path) -> None:
    """`!include`, `!secret` and `!input` are HA's, and `yaml.safe_load` raises
    on all three. A walker using it would record most of a real configuration as
    unparseable."""
    body = (
        "group: !include groups.yaml\n"
        "mqtt:\n  password: !secret mqtt_password\n"
        "automation: !include_dir_merge_list automations/\n"
    )
    _simple_repo(fake_root, {"configuration.yaml": body})
    _rules(fake_root, [_rule(10, "**/*.yaml", artifact_class="package")])

    (repo,) = inventory.build()["repos"]  # type: ignore[misc]
    assert _record(repo, "configuration.yaml")["parse"] == "parsed"


def test_a_selected_file_that_is_not_a_yaml_document_is_not_applicable(
    fake_root: Path,
) -> None:
    _simple_repo(fake_root, {"policy.py": PYTHON, "manifest.json": "{}\n"})
    _rules(fake_root, [_rule(10, "**/*", artifact_class="custom_integration")])

    (repo,) = inventory.build()["repos"]  # type: ignore[misc]
    for path in ("policy.py", "manifest.json"):
        entry = _record(repo, path)
        assert entry["selected"] is True
        assert entry["parse"] == "not_applicable"
        assert entry["error"] is None


def test_an_excluded_file_is_never_parsed(fake_root: Path) -> None:
    """Not "this did not parse" but "we did not parse this": the malformed file
    is excluded, so the walker never opens it and there is no error to record."""
    _simple_repo(fake_root, {"broken.yaml": BAD_YAML})
    _rules(fake_root, [_exclude(10, "**/*.yaml", reason="not configuration")])

    (repo,) = inventory.build()["repos"]  # type: ignore[misc]
    entry = _record(repo, "broken.yaml")
    assert entry["selected"] is False
    assert entry["parse"] == "not_applicable"
    assert entry["error"] is None


def test_a_selected_file_that_cannot_be_read_as_utf8_is_unparsed(
    fake_root: Path,
) -> None:
    clone = _simple_repo(fake_root, {"x.yaml": GOOD_YAML})
    (clone / "x.yaml").write_bytes(b"\xff\xfe\x00a")
    subprocess.run(["git", "add", "-A"], cwd=clone, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "binary"],
        cwd=clone,
        capture_output=True,
        check=True,
    )
    _rules(fake_root, [_rule(10, "**/*.yaml", artifact_class="package")])

    (repo,) = inventory.build()["repos"]  # type: ignore[misc]
    entry = _record(repo, "x.yaml")
    assert entry["parse"] == "unparsed"
    assert "cannot be read" in entry["error"]


# --------------------------------------------------------------------------
# Repo identity
# --------------------------------------------------------------------------


def test_the_handle_comes_from_the_remote_and_not_the_directory_name(
    fake_root: Path,
) -> None:
    """The directory names do not contain the handles -- renemarc's clone is
    `home-assistant-config` -- so a table mapping one to the other would be a
    second spelling of the corpus's identity, free to disagree with the first."""
    _simple_repo(fake_root)
    assert inventory.clone_handle(fake_root / "ressources" / "example-config") == (
        "example"
    )


def test_the_handle_is_lowercased(fake_root: Path) -> None:
    """GitHub handles are case-insensitive and the remote spells CCOSTAN's in
    capitals, while `licenses.yaml`, `repos.yaml`, `docs/reference/` and the
    golden filenames all use the lowercase form."""
    _clone(fake_root, "CCOSTAN", "Home-AssistantConfig", {"a.yaml": GOOD_YAML})
    assert (
        inventory.clone_handle(fake_root / "ressources" / "Home-AssistantConfig")
        == "ccostan"
    )


def test_a_repo_recorded_with_no_clone_is_named(fake_root: Path) -> None:
    _licences(fake_root, ["example", "absent"])
    _rules(fake_root, [_rule(10, "**/*", artifact_class="package")])

    with pytest.raises(CheckError) as caught:
        inventory.build()
    assert "absent" in str(caught.value)


def test_a_clone_with_no_licence_record_is_named(fake_root: Path) -> None:
    _simple_repo(fake_root)
    _licences(fake_root, [])
    _rules(fake_root, [_rule(10, "**/*", artifact_class="package")])

    with pytest.raises(CheckError) as caught:
        inventory.build()
    assert "example" in str(caught.value)


def test_two_clones_claiming_one_handle_is_named(fake_root: Path) -> None:
    _clone(fake_root, "example", "one", {"a.yaml": GOOD_YAML})
    _clone(fake_root, "example", "two", {"b.yaml": GOOD_YAML})

    with pytest.raises(CheckError) as caught:
        inventory.discover_clones()
    assert "both report the owner" in str(caught.value)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_inventory_is_byte_identical_across_two_builds(fake_root: Path) -> None:
    _simple_repo(fake_root)
    _rules(
        fake_root,
        [
            _rule(10, "**/*.yaml", artifact_class="automation"),
            _exclude(20, "**/*.py", reason="code"),
            _exclude(30, "**/*.md", reason="prose"),
        ],
    )

    assert inventory.render(inventory.build()) == inventory.render(inventory.build())


def test_the_repos_are_recorded_in_the_licence_files_order(fake_root: Path) -> None:
    """A stable order, which for four repos means an order somebody chose.
    `licenses.yaml` is where the repos are recorded, so it is where the order
    comes from -- taking it from the directory listing would make the committed
    file's byte order depend on the filesystem."""
    _clone(fake_root, "zulu", "z-config", {"a.yaml": GOOD_YAML})
    _clone(fake_root, "alpha", "a-config", {"a.yaml": GOOD_YAML})
    _licences(fake_root, ["zulu", "alpha"])
    _rules(fake_root, [_rule(10, "**/*", artifact_class="package")])

    assert [repo["repo"] for repo in inventory.build()["repos"]] == ["zulu", "alpha"]  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Self-closure of the committed artifact
# --------------------------------------------------------------------------


def _committed(fake_root: Path) -> dict[str, object]:
    """Build a well-formed inventory in the fixture and write it out."""
    _simple_repo(fake_root)
    _rules(
        fake_root,
        [
            _rule(10, "**/*.yaml", artifact_class="automation"),
            _exclude(20, "**/*.py", reason="code"),
            _exclude(30, "**/*.md", reason="prose"),
        ],
    )
    document = inventory.build()
    write(fake_root, INVENTORY_PATH, inventory.render(document))
    return document


def _rewrite(fake_root: Path, document: dict[str, object]) -> None:
    write(fake_root, INVENTORY_PATH, json.dumps(document, indent=2) + "\n")


def test_a_well_formed_inventory_passes(fake_root: Path) -> None:
    _committed(fake_root)
    assert _diagnostics() == []


def test_the_stored_inventory_is_valid_json_after_a_round_trip(fake_root: Path) -> None:
    document = _committed(fake_root)
    _rewrite(fake_root, json.loads(json.dumps(document)))
    assert _diagnostics() == []


def test_a_tracked_count_that_disagrees_with_the_file_list(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["counts"]["tracked"] = 99  # type: ignore[index]
    _rewrite(fake_root, document)

    messages = _messages()
    assert "declares 99 tracked file(s) and lists 3" in messages


def test_selected_and_excluded_that_do_not_sum_to_tracked(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["counts"]["excluded"] = 1  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "do not sum to the 3 tracked" in _messages()


def test_a_class_count_that_disagrees_with_the_files(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["by_class"]["automation"] = 2  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "declares 2 for 'automation' against 1 file(s)" in _messages()


def test_a_class_omitted_from_the_map_is_reported(fake_root: Path) -> None:
    """The schema permits an absent key, since a class with no files is absent
    rather than zero -- so the check is what makes a key that went missing from
    a populated map visible."""
    document = _committed(fake_root)
    del document["repos"][0]["by_class"]["automation"]  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "omits 'automation', which 1 file(s) carry" in _messages()


def test_a_selected_count_the_file_list_does_not_support(fake_root: Path) -> None:
    """Every map is a second view of one partition, so a `counts.selected` that
    the files cannot be made to add up to is caught by the maps rather than by
    the counts -- the counts only have to sum among themselves."""
    document = _committed(fake_root)
    document["repos"][0]["counts"]["selected"] = 2  # type: ignore[index]
    document["repos"][0]["counts"]["excluded"] = 1  # type: ignore[index]
    _rewrite(fake_root, document)

    messages = _messages()
    assert "`by_class` covers 1 file(s) against 2 it should" in messages
    assert "`by_parse` covers 1 file(s) against 2 it should" in messages


def test_a_rule_count_that_disagrees_with_the_files(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["by_rule"]["20"] = {  # type: ignore[index]
        "decided": 5,
        "selected": 0,
        "excluded": 5,
    }
    _rewrite(fake_root, document)

    assert "declares 5 decided against" in _messages()


def test_a_rules_split_is_checked_against_the_files_not_against_its_sum(
    fake_root: Path,
) -> None:
    """`decided == selected + excluded` is true of any entry whatever it says.

    So a map whose split had been guessed would satisfy every check that only
    compared the three to each other, which is why each is counted separately
    from the file list. This is the mutation that says so: `decided` and
    `excluded` are raised together, which leaves the identity intact and moves
    both away from the files.
    """
    document = _committed(fake_root)
    entry = document["repos"][0]["by_rule"]["20"]  # type: ignore[index]
    entry["decided"] += 1
    entry["excluded"] += 1
    _rewrite(fake_root, document)

    assert "excluded against" in _messages()


def test_a_rule_entry_that_is_not_a_mapping_is_reported(fake_root: Path) -> None:
    """The schema admits a mapping here and nothing stops a hand edit making one
    an integer, and the pre-commit hook may not answer that with a traceback."""
    document = _committed(fake_root)
    document["repos"][0]["by_rule"]["20"] = 5  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "does not hold the counts for that rule" in _messages()


def test_an_unparsed_file_with_no_error_is_reported(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["files"][0]["parse"] = "unparsed"  # type: ignore[index]
    document["repos"][0]["files"][0]["error"] = None  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "recorded `unparsed` with no `error`" in _messages()


def test_an_error_against_a_file_that_parsed_is_reported(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["files"][0]["error"] = "something"  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "the error field is non-null exactly where the outcome is `unparsed`" in (
        _messages()
    )


def test_a_selected_file_with_no_class_is_reported(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["files"][0]["class"] = None  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "is selected but carries no `class`" in _messages()


def test_an_excluded_file_carrying_a_class_is_reported(fake_root: Path) -> None:
    document = _committed(fake_root)
    for entry in document["repos"][0]["files"]:  # type: ignore[index,union-attr]
        if entry["selected"] is False:
            entry["class"] = "package"
            break
    _rewrite(fake_root, document)

    assert "is excluded but carries `class: package`" in _messages()


def test_the_rule_list_deciding_nothing_anywhere_is_reported(fake_root: Path) -> None:
    """The clause that gives the rule list its teeth: a rule matching nothing
    reads as coverage while covering nothing."""
    _committed(fake_root)
    _rules(
        fake_root,
        [
            _rule(10, "**/*.yaml", artifact_class="automation"),
            _exclude(20, "**/*.py", reason="code"),
            _exclude(30, "**/*.md", reason="prose"),
            _rule(40, "**/*.toml", artifact_class="package"),
        ],
    )

    assert "rule 40 ('**/*.toml') decided none of the tracked files" in _messages()


def test_a_rule_recorded_in_no_rule_list_is_reported(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["repos"][0]["files"][0]["rule"] = 99  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "decided by rule 99, which is not in" in _messages()


def test_a_repo_with_a_licence_record_and_no_inventory_block(
    fake_root: Path,
) -> None:
    _committed(fake_root)
    _licences(fake_root, ["example", "second"])

    assert "records nothing for `second`" in _messages()


def test_a_repo_in_the_inventory_with_no_licence_record(fake_root: Path) -> None:
    _committed(fake_root)
    _licences(fake_root, [])

    assert "records `example`, which has no licence record" in _messages()


def test_totals_that_disagree_with_the_repo_blocks(fake_root: Path) -> None:
    document = _committed(fake_root)
    document["totals"]["selected"] = 99  # type: ignore[index]
    _rewrite(fake_root, document)

    assert "totals declare 99 selected against 1 summed over the repos" in _messages()


def test_a_missing_inventory_is_a_diagnostic_not_a_traceback(fake_root: Path) -> None:
    _licences(fake_root, [])
    _rules(fake_root, [])
    assert "does not exist" in _messages()


def test_an_inventory_that_will_not_parse_is_a_diagnostic(fake_root: Path) -> None:
    write(fake_root, INVENTORY_PATH, "{not json")
    assert "is not valid JSON" in _messages()


def test_the_check_reads_no_clone(fake_root: Path) -> None:
    assert not paths.RESSOURCES.exists()
    _licences(fake_root, [])
    _rules(fake_root, [])
    # The check runs and reports the absent file; nothing reached for a clone.
    assert _messages() != ""


# --------------------------------------------------------------------------
# The real clones -- local only
# --------------------------------------------------------------------------

_clones_present = paths.RESSOURCES.is_dir()


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_every_real_repo_closes_over_its_own_git_ls_files() -> None:
    """The clause on the repositories themselves, where the count is git's."""
    ruleset = rules.load_rules()
    clones = inventory.discover_clones()
    recorded = [record.repo for record in licenses.load_licences() if record.repo]
    assert recorded, "no licence records to walk"

    for repo in recorded:
        clone = clones[repo]
        tracked = inventory.tracked_files(clone)
        decided = inventory.decide_repo(repo, clone, ruleset)
        counted = decided.counts
        assert counted["tracked"] == len(tracked)
        assert counted["selected"] + counted["excluded"] == len(tracked)
        assert len(decided.files) == len(tracked)
        assert sum(decided.by_class.values()) == counted["selected"]
        assert sum(decided.by_parse.values()) == counted["selected"]
        assert (
            sum(entry["decided"] for entry in decided.by_rule.values())
            == counted["tracked"]
        )
        assert (
            sum(entry["selected"] for entry in decided.by_rule.values())
            == counted["selected"]
        )
        # The clause the spec's closure scenario names: excluded is reported per
        # deciding rule, not left for the reader to reconstruct.
        assert (
            sum(entry["excluded"] for entry in decided.by_rule.values())
            == counted["excluded"]
        )


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_every_real_rule_decides_at_least_one_file() -> None:
    """`file_rules.yaml` was narrowed against these four repositories until this
    held, and three rules were deleted for failing it."""
    ruleset = rules.load_rules()
    clones = inventory.discover_clones()
    seen: set[int] = set()
    for repo in (record.repo for record in licenses.load_licences() if record.repo):
        decided = inventory.decide_repo(repo, clones[repo], ruleset)
        seen |= {record.rule for record in decided.files}

    unseen = sorted({rule.order for rule in ruleset.rules} - seen)
    assert unseen == [], f"rules deciding nothing: {unseen}"


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_every_selected_real_file_is_configuration() -> None:
    """The spec's definition of selected: "a configuration file". Prose and
    licence files are not, whatever directory they sit in.

    Asserted over the real corpus rather than over a fixture because the way it
    breaks is a select rule whose pattern is broader than the artifacts it names
    -- the authored-path rules are directory globs, and one of them was already
    found to be swallowing each integration's `README.md`.
    """
    ruleset = rules.load_rules()
    clones = inventory.discover_clones()
    prose: list[str] = []
    for repo in (record.repo for record in licenses.load_licences() if record.repo):
        decided = inventory.decide_repo(repo, clones[repo], ruleset)
        for record in decided.files:
            if not record.selected:
                continue
            name = record.path.rsplit("/", 1)[-1]
            if record.path.endswith((".md", ".mdc")) or name.startswith(
                ("LICENSE", "COPYING", "NOTICE")
            ):
                prose.append(f"{repo}:{record.path}")
    assert prose == [], f"prose selected into the corpus: {prose}"


@pytest.mark.skipif(not _clones_present, reason="the reference clones are absent")
def test_every_committed_authored_path_is_tracked() -> None:
    """`authored_paths` is hand-written evidence, and this is what makes it true.

    A glob conforms to its declared class whether or not anything matches it, so
    the rule-list half of this check cannot see an entry pointing at a path that
    exists nowhere. Deciding it needs `git ls-files`, so it lives with the local
    closure check and no CI job performs it.
    """
    assert (
        inventory.untracked_authored_paths(
            inventory.discover_clones(), rules.load_rules()
        )
        == ()
    )


def test_an_authored_path_no_clone_tracks_is_named(fake_root: Path) -> None:
    """The failing half of the check above, and the reason it is not a formality.

    Without this the check could be an empty tuple by accident -- every pattern
    matching nothing, or the loop never running -- and the real-corpus test above
    would still be green.
    """
    _clone(fake_root, "x", "repo-x", {"automations.yaml": GOOD_YAML})
    _licences(fake_root, ["x"])
    _rules_with_authored(
        fake_root,
        [_rule(10, "automations.yaml", artifact_class="automation")],
        [
            {"path": "automations.yaml", "class": "automation"},
            {"path": "custom_components/ghost/**", "class": "custom_integration"},
        ],
    )

    with pytest.raises(CheckError) as raised:
        inventory.build()

    message = raised.value.message
    assert "'custom_components/ghost/**'" in message
    assert "'automations.yaml'" not in message, (
        "the path that is tracked was named as untracked"
    )


def test_an_authored_path_tracked_in_another_clone_is_not_reported(
    fake_root: Path,
) -> None:
    """Read across the clones together, and not one clone at a time.

    An authored path belongs to the repo whose author wrote it, so a pattern for
    one author's tree matches nothing in the other three. A per-clone reading
    would report three defects for every correct entry, and the list would be
    unusable.
    """
    first = _clone(fake_root, "x", "repo-x", {"automations.yaml": GOOD_YAML})
    second = _clone(
        fake_root, "y", "repo-y", {"custom_components/mine/manifest.json": "{}\n"}
    )
    _rules_with_authored(
        fake_root,
        [
            _rule(10, "automations.yaml", artifact_class="automation"),
            _rule(11, "custom_components/mine/**", artifact_class="custom_integration"),
        ],
        [
            {"path": "automations.yaml", "class": "automation"},
            {"path": "custom_components/mine/**", "class": "custom_integration"},
        ],
    )

    assert (
        inventory.untracked_authored_paths(
            {"x": first, "y": second}, rules.load_rules()
        )
        == ()
    )
