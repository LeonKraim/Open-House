"""The ordered file-selection rule list -- section 3, task 3.1.

The rule list is the one artifact every classification in the corpus is a
function of, so its properties are tested on trees built to violate them. A rule
list that only ever ran against the committed file would have been seen to work
and never seen to fail, and the failures that matter here are the quiet ones: an
order that two rules share, a select rule that confers no class, a hole in the
list that swallows a file nobody named.

What is *not* here is the same set of properties against a real repository. A
rule list can be internally consistent and still leave a tracked file undecided,
and only a path list can tell the difference -- so the closure over each repo's
`git ls-files` is exercised by the walker's tests, and the per-decision counts
are asserted there against the committed inventory. This file is the half of the
rule that is a pure function of the rule list and nothing else, which is also
why it runs in CI with no clone present.

The glob is hand-written -- `fnmatch` lets `*` cross a separator and
`PurePath.match` changed its `**` semantics in 3.13 -- so its behaviour is
tested directly rather than only through the rules that happen to use it.
"""

from __future__ import annotations

import pytest
import yaml

from tools.catalog import paths, rules, validate

from .conftest import write

RULES_PATH = "catalog/file_rules.yaml"


def _rule(
    order: int,
    pattern: str,
    kind: str = "select",
    artifact_class: str | None = "package",
    reason: str = "a reason",
) -> dict[str, object]:
    """One rule as a document entry.

    The class is written through as given rather than normalised against the
    kind, because several of the tests below are *about* a rule whose class and
    kind disagree, and a builder that silently made them agree could not express
    the violation it is meant to produce.
    """
    return {
        "order": order,
        "pattern": pattern,
        "kind": kind,
        "class": artifact_class,
        "reason": reason,
    }


def _exclude(order: int, pattern: str, reason: str = "a reason") -> dict[str, object]:
    return _rule(order, pattern, kind="exclude", artifact_class=None, reason=reason)


def _document(
    entries: list[dict[str, object]],
    authored: list[dict[str, str]] | None = None,
    altered: list[dict[str, str]] | None = None,
) -> str:
    document: dict[str, object] = {"rules": entries}
    if authored is not None:
        document["authored_paths"] = authored
    if altered is not None:
        document["altered_core_copies"] = altered
    return yaml.safe_dump(document, sort_keys=False)


def _commit_rules(root: object, text: str) -> None:
    write(root, RULES_PATH, text)  # type: ignore[arg-type]


def _diagnostics() -> list[tuple[str, str]]:
    """The rule check's findings, collected the way the hook collects them.

    Through `validate_all` rather than by calling `check_rules` directly: the
    property under test for several of these is that a broken rule file produces
    a *report*, and a report is what the command at the outermost boundary
    returns. Calling the check directly would let a `CheckError` escape as a
    traceback and the test would still be green.
    """
    report = validate.validate_all()
    return [
        (d.where, d.message) for d in report.diagnostics if d.check == rules.RULES_CHECK
    ]


def _messages() -> str:
    """Every finding, with the location it names.

    The location is included rather than dropped because several of the checks
    below report a path *as* their location and say nothing about it in the
    message: "this entry names no rule" is only actionable if the entry is named.
    """
    return "\n".join(f"{where}: {message}" for where, message in _diagnostics())


# --------------------------------------------------------------------------
# The glob
# --------------------------------------------------------------------------


def test_a_double_star_crosses_separators_and_a_single_one_does_not() -> None:
    assert rules.pattern_matches("**/automations/**", "automations/a.yaml")
    assert rules.pattern_matches("**/automations/**", "config/automations/a.yaml")
    assert rules.pattern_matches("**/automations/**", "a/b/c/automations/d/e.yaml")
    assert not rules.pattern_matches("config/*.yaml", "config/sub/a.yaml")
    assert rules.pattern_matches("config/*.yaml", "config/a.yaml")


def test_alternation_expands_one_pattern_into_several_globs() -> None:
    assert rules.expand("**/{sensor,sensors}/**") == (
        "**/sensor/**",
        "**/sensors/**",
    )
    assert rules.pattern_matches("**/{sensor,sensors}/**", "sensors/a.yaml")
    assert rules.pattern_matches("**/{sensor,sensors}/**", "x/sensor/a.yaml")
    assert not rules.pattern_matches("**/{sensor,sensors}/**", "binary_sensors/a.yaml")


def test_a_character_class_is_a_set_of_single_characters() -> None:
    """Positive classes only, deliberately: negation has its own test below.

    An earlier version of this test advertised negation in its name while
    asserting nothing but positive classes, so deleting the negated branch of
    the translator left the suite green. The name was the defect.
    """
    assert rules.pattern_matches(
        "**/*.[Dd][Ii][Ss][Aa][Bb][Ll][Ee][Dd]", "a/x.disabled"
    )
    assert rules.pattern_matches(
        "**/*.[Dd][Ii][Ss][Aa][Bb][Ll][Ee][Dd]", "a/x.DISABLED"
    )
    assert not rules.pattern_matches(
        "**/*.[Dd][Ii][Ss][Aa][Bb][Ll][Ee][Dd]", "a/x.yaml"
    )
    # An unclosed bracket is a literal, as in every other glob implementation.
    assert rules.pattern_matches("**/a[b", "a[b")


def test_a_negated_character_class_excludes_its_set() -> None:
    """Both spellings, because the translator normalises one into the other and
    a test of one form would not notice the other being dropped."""
    assert rules.pattern_matches("**/x[!y].yaml", "a/xz.yaml")
    assert not rules.pattern_matches("**/x[!y].yaml", "a/xy.yaml")
    assert rules.pattern_matches("**/x[^y].yaml", "a/xz.yaml")
    assert not rules.pattern_matches("**/x[^y].yaml", "a/xy.yaml")


def test_a_negated_character_class_crosses_no_separator() -> None:
    """The rule the other classes already obey, applied to this one.

    `*` and `?` stop at a `/` because the rule list is shared by four repos and
    matches paths; a class that could stand for a separator would let one
    segment's pattern swallow a whole subtree, which is the failure `**/` exists
    to make explicit rather than accidental. Handed straight to the regex engine,
    `[^y]` would cross, so this is a behaviour the translator has to produce
    rather than one it inherits.
    """
    assert not rules.pattern_matches("**/x[!y].yaml", "a/x/yz.yaml")
    assert not rules.pattern_matches("**/x[^y].yaml", "a/x/yz.yaml")


def test_a_question_mark_matches_one_character_and_crosses_no_separator() -> None:
    """`?` is part of the glob language the task names, and nothing drove it."""
    assert rules.pattern_matches("a?.yaml", "ab.yaml")
    assert not rules.pattern_matches("a?.yaml", "abc.yaml")
    assert not rules.pattern_matches("a?.yaml", "a.yaml")
    assert not rules.pattern_matches("a?.yaml", "a/b.yaml")


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


def test_a_path_matching_two_select_rules_takes_the_lower_order() -> None:
    ruleset = rules.RuleSet(
        rules=(
            rules.Rule(20, "**/a/**", "select", "helper", "later"),
            rules.Rule(10, "**/a/b.yaml", "select", "automation", "earlier"),
        ),
        authored_paths=(),
        altered_core_copies=(),
    )
    decided, undecided = rules.partition(["a/b.yaml"], ruleset)
    assert undecided == ()
    # The class is the lower-order rule's, and that rule is the one recorded --
    # the count has to close over the rule that actually decided.
    assert decided["a/b.yaml"].order == 10
    assert decided["a/b.yaml"].artifact_class == "automation"


def test_an_overlapping_vendored_bundle_is_decided_and_not_a_failure() -> None:
    """The spec's own example: a vendored `.js` matched by three rules at once."""
    ruleset = rules.RuleSet(
        rules=(
            rules.Rule(30, "**/custom_components/**", "exclude", None, "vendored"),
            rules.Rule(43, "**/*.js", "exclude", None, "front-end"),
            rules.Rule(31, "**/www/**", "exclude", None, "assets"),
        ),
        authored_paths=(),
        altered_core_copies=(),
    )
    decided, undecided = rules.partition(["x/www/custom_components/a.js"], ruleset)
    assert undecided == ()
    assert decided["x/www/custom_components/a.js"].order == 30


def test_a_file_no_rule_matches_is_reported_by_name() -> None:
    ruleset = rules.RuleSet(
        rules=(rules.Rule(10, "**/*.yaml", "select", "package", "yaml"),),
        authored_paths=(),
        altered_core_copies=(),
    )
    decided, undecided = rules.partition(["a/b.yaml", "go2rtc-1.9.9"], ruleset)
    assert set(decided) == {"a/b.yaml"}
    assert undecided == ("go2rtc-1.9.9",)


# --------------------------------------------------------------------------
# The list's own consistency
# --------------------------------------------------------------------------


def test_two_rules_sharing_an_order_name_both(fake_root: object) -> None:
    _commit_rules(
        fake_root,
        _document(
            [
                _rule(10, "**/a/**", reason="first"),
                _rule(10, "**/b/**", reason="second"),
            ]
        ),
    )
    messages = _messages()
    assert "**/a/**" in messages
    assert "**/b/**" in messages
    assert "share `order: 10`" in messages


def test_a_select_rule_with_no_class_names_the_rule_and_its_order(
    fake_root: object,
) -> None:
    _commit_rules(fake_root, _document([_rule(17, "**/a/**", artifact_class=None)]))
    messages = _messages()
    assert "rule 17" in messages
    assert "selects but carries no `class`" in messages


def test_an_exclude_rule_carrying_a_class_names_the_rule_and_its_order(
    fake_root: object,
) -> None:
    _commit_rules(
        fake_root,
        _document([_rule(18, "**/a/**", kind="exclude", artifact_class="helper")]),
    )
    messages = _messages()
    assert "rule 18" in messages
    assert "excludes but carries" in messages


def test_a_select_rule_conferring_a_class_outside_the_closed_set(
    fake_root: object,
) -> None:
    _commit_rules(
        fake_root, _document([_rule(19, "**/a/**", artifact_class="dashboard_config")])
    )
    messages = _messages()
    assert "not one of the closed set" in messages
    assert "rule 19" in messages


def test_a_kind_that_is_neither_select_nor_exclude(fake_root: object) -> None:
    _commit_rules(
        fake_root, _document([_rule(20, "**/a/**", kind="ignore", artifact_class=None)])
    )
    assert "the known kinds are" in _messages()


def test_a_file_name_carrying_a_non_integer_order_is_a_check_error(
    fake_root: object,
) -> None:
    document = _document([_rule(10, "**/a/**")]).replace("order: 10", "order: first")
    _commit_rules(fake_root, document)
    assert "no integer `order`" in _messages()


def test_a_rules_file_that_will_not_parse_is_a_diagnostic_not_a_traceback(
    fake_root: object,
) -> None:
    """The pre-commit hook catches `CheckError` and nothing else.

    An unguarded `yaml.safe_load` here would escape `check_rules`, escape
    `validate_all`, and reach the hook as a traceback -- and because the report
    is never rendered, every other check's findings would be lost with it.
    """
    _commit_rules(fake_root, "rules:\n  - order: 1\n   pattern: 'x'\n")
    assert "cannot be parsed" in _messages()


def test_a_missing_rules_file_is_a_diagnostic_not_an_empty_rule_set(
    fake_root: object,
) -> None:
    assert "does not exist" in _messages()


# --------------------------------------------------------------------------
# Authored paths and altered core copies
# --------------------------------------------------------------------------


def _authored_document(
    entries: list[dict[str, object]],
    authored: list[dict[str, str]],
    altered: list[dict[str, str]] | None = None,
) -> str:
    return _document(entries, authored=authored, altered=altered)


GUARD = "**/custom_components/tesla_charge_guard/**"
BLUEPRINT = "**/blueprints/automation/fwartner/**"
CUSTOM = "**/custom_components/github_custom/**"

AUTHORED_OK = [
    _rule(10, GUARD, artifact_class="custom_integration"),
    _rule(12, BLUEPRINT, artifact_class="blueprint"),
]
AUTHORED_ENTRIES = [
    {"path": GUARD, "class": "custom_integration"},
    {"path": BLUEPRINT, "class": "blueprint"},
]


@pytest.mark.parametrize(
    ("kept", "missing"),
    [
        (AUTHORED_ENTRIES[1], "custom_integration"),
        (AUTHORED_ENTRIES[0], "blueprint"),
    ],
)
def test_authored_paths_missing_a_required_class_names_the_class(
    fake_root: object, kept: dict[str, str], missing: str
) -> None:
    """Both halves of the requirement, one at a time.

    Parametrised because the requirement is a conjunction and the first version
    of this test dropped only the `custom_integration` entry. Removing
    `blueprint` from `REQUIRED_AUTHORED_CLASSES` therefore left the suite green,
    so half of what task 3.1 asks for was being enforced by nothing.
    """
    _commit_rules(fake_root, _authored_document(AUTHORED_OK, [kept]))
    messages = _messages()
    assert f"names no path classed `{missing}`" in messages
    assert f"stopped covering `{missing}`" in messages


def test_an_authored_path_with_no_backing_rule_fails(fake_root: object) -> None:
    """The entry is a rule in the same list, or it is a claim about nothing."""
    _commit_rules(
        fake_root,
        _authored_document(
            AUTHORED_OK,
            [
                AUTHORED_ENTRIES[0],
                {"path": "**/blueprints/automation/nobody/**", "class": "blueprint"},
            ],
        ),
    )
    messages = _messages()
    assert "**/blueprints/automation/nobody/**" in messages
    assert "is not a select rule in `rules`" in messages


def test_an_authored_path_whose_rule_confers_another_class_fails(
    fake_root: object,
) -> None:
    _commit_rules(
        fake_root,
        _authored_document(
            AUTHORED_OK,
            [AUTHORED_ENTRIES[0], {"path": BLUEPRINT, "class": "helper"}],
        ),
    )
    messages = _messages()
    assert "declares class 'helper'" in messages
    assert "confers ['blueprint']" in messages


def test_an_altered_core_copy_that_is_also_an_authored_path_fails(
    fake_root: object,
) -> None:
    _commit_rules(
        fake_root,
        _authored_document(
            AUTHORED_OK,
            AUTHORED_ENTRIES,
            [{"path": GUARD, "reason": "an altered copy"}],
        ),
    )
    assert "as an authored path as well as an altered core copy" in _messages()


def test_an_altered_core_copy_that_a_select_rule_wins_fails(
    fake_root: object,
) -> None:
    """The whole point of the category: it is excluded, never attributed.

    The path here is one no `authored_paths` entry names, so the only thing
    wrong with it is that a select rule decides it -- which is the failure this
    test is about. The other branch, a path that is both, is the test above.
    """
    _commit_rules(
        fake_root,
        _authored_document(
            [*AUTHORED_OK, _rule(14, CUSTOM, artifact_class="custom_integration")],
            AUTHORED_ENTRIES,
            [{"path": CUSTOM, "reason": "a copy"}],
        ),
    )
    messages = _messages()
    assert "is selected by rule 14" in messages
    assert "an altered copy of a core file is" in messages


def test_an_altered_core_copy_matched_by_no_rule_fails(fake_root: object) -> None:
    _commit_rules(
        fake_root,
        _authored_document(
            AUTHORED_OK,
            AUTHORED_ENTRIES,
            [{"path": "**/custom_components/nothing/**", "reason": "a copy"}],
        ),
    )
    assert "is matched by no rule" in _messages()


def test_an_altered_core_copy_excluded_by_its_own_rule_passes(
    fake_root: object,
) -> None:
    _commit_rules(
        fake_root,
        _authored_document(
            [*AUTHORED_OK, _exclude(20, CUSTOM, reason="an altered copy")],
            AUTHORED_ENTRIES,
            [{"path": CUSTOM, "reason": "an altered copy"}],
        ),
    )
    assert _messages() == ""


# --------------------------------------------------------------------------
# The committed rule list
# --------------------------------------------------------------------------


def test_the_committed_rule_list_passes_its_own_checks(real_root: object) -> None:
    assert _diagnostics() == []


def test_the_committed_rules_are_ordered_and_ordered_uniquely() -> None:
    ruleset = rules.load_rules()
    orders = [rule.order for rule in ruleset.rules]
    assert orders == sorted(orders)
    assert len(orders) == len(set(orders))


def test_the_committed_authored_entries_are_backed_by_rules() -> None:
    ruleset = rules.load_rules()
    by_pattern = {rule.pattern: rule for rule in ruleset.rules}
    for entry in ruleset.authored_paths:
        rule = by_pattern[entry.path]
        assert rule.selects
        assert rule.artifact_class == entry.artifact_class


def test_the_committed_altered_copies_are_excluded_and_not_authored() -> None:
    ruleset = rules.load_rules()
    authored = {entry.path for entry in ruleset.authored_paths}
    for entry in ruleset.altered_core_copies:
        assert entry.path not in authored
        rule = rules.decide(entry.path, ruleset.rules)
        assert rule is not None and not rule.selects


def test_every_committed_rule_carries_a_reason(real_root: object) -> None:
    """An exclude rule with no reason is how configuration is swallowed."""
    for rule in rules.load_rules().rules:
        assert rule.reason.strip(), f"rule {rule.order} carries no reason"


def test_the_rule_list_is_discovered_as_a_catalog_data_file(real_root: object) -> None:
    """A rule list the data-file check does not see is a rule list that is
    schema-checked nowhere, and `check_rules` would still be green."""
    assert paths.CATALOG / rules.RULES_FILE in paths.catalog_data_files()
