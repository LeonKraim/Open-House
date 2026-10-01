"""The ordered file-selection rule list, and the glob that applies it.

Every tracked file in every reference repo is *selected* (it is configuration,
it enters the corpus and receives one artifact class) or *excluded* (it is not,
and it is excluded by a named rule carrying a reason). The rule list is ordered,
first match wins, and the rule that decided a file is recorded on it -- which is
what makes an over-broad exclude visible rather than silent.

Two properties are load-bearing and both come from the class living on the
*rule* rather than being inferred from the file's contents.

The first is that a selected file's class is its deciding rule's class and
nothing else. Inference would need the file, the file lives only in a clone, and
the golden-path and class-pinning checks are required to run in CI with no clone
present. So classification is a pure function of the committed rule list and a
committed path list.

The second is that the partition closes. Patterns overlap unavoidably -- a
vendored `custom_components/**/*.js` bundle matches a vendored-tree rule, a
`custom_components/**` rule and a `**/*.js` rule at once -- so a
"matches exactly one rule" constraint would fail on every real repository, and a
list rewritten to be mutually exclusive would be unreadable for no property
anybody needs. Ordering gives the closure for free: the partition is still total
and disjoint, the deciding rule is still recorded and counted, and the counts
still sum.

The glob is implemented here rather than taken from `fnmatch` or
`pathlib.PurePath.match`, because neither has the semantics this list is written
in. `fnmatch`'s `*` crosses `/`, which would make `config/*` swallow the whole
tree; `PurePath.match`'s `**` behaviour changed in 3.13 and this project runs on
3.12. `**` here means "any number of path segments, including none", so
`**/automations/**` matches `automations/a.yaml` and `config/automations/a.yaml`
alike -- which is what a rule list shared by four differently-laid-out
repositories has to mean.

`{a,b}` alternation is the one extension, for the same reason: four repos keep
the same kind of artifact under different directory names, and a list that
spelled each one out would be a list where one spelling goes stale alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

import yaml

from . import errors, paths
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

RULES_CHECK = "file-rules"

RULES_FILE = "file_rules.yaml"

#: The classes an `authored_paths` list must cover, because a glob convention
#: cannot reach them: an author's own integration or blueprint is not where a
#: directory pattern would put it, so a list that names none of them is a list
#: that has stopped doing the thing it exists for.
REQUIRED_AUTHORED_CLASSES: tuple[str, ...] = ("custom_integration", "blueprint")


@dataclass(frozen=True, slots=True)
class Rule:
    """One rule: what it matches, what it does, and why it exists."""

    order: int
    pattern: str
    kind: str
    artifact_class: str | None
    reason: str

    @property
    def selects(self) -> bool:
        return self.kind == "select"


@dataclass(frozen=True, slots=True)
class AuthoredPath:
    path: str
    artifact_class: str


@dataclass(frozen=True, slots=True)
class AlteredCoreCopy:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class RuleSet:
    rules: tuple[Rule, ...]
    authored_paths: tuple[AuthoredPath, ...]
    altered_core_copies: tuple[AlteredCoreCopy, ...]

    def by_order(self) -> dict[int, Rule]:
        return {rule.order: rule for rule in self.rules}


def _translate(pattern: str) -> str:
    """A glob as a regular expression, with `**` crossing separators.

    `*` stops at a `/`; `**/` matches zero or more whole segments, so `**/x`
    matches `x` at the repository root as well as `a/b/x`. The distinction is
    the whole reason this is written out: a rule list shared by four repos needs
    to say "an automations directory, wherever this repo keeps it".
    """
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                # Zero or more complete segments. `(?:[^/]+/)*` is what makes
                # `**/automations/**` match a repo that keeps automations at the
                # top level and one that keeps them three directories down.
                out.append("(?:[^/]+/)*")
                index += 3
            elif pattern.startswith("**", index):
                out.append(".*")
                index += 2
            else:
                out.append("[^/]*")
                index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        elif char == "[":
            end = index + 1
            if pattern[end : end + 1] in ("!", "^"):
                end += 1
            if pattern[end : end + 1] == "]":
                end += 1
            while end < length and pattern[end] != "]":
                end += 1
            if end >= length:
                # An unclosed bracket is a literal one, as in every other glob.
                out.append(re.escape(char))
                index += 1
            else:
                body = pattern[index + 1 : end]
                if body.startswith(("!", "^")):
                    # Both spellings of negation, normalised to one. A negated
                    # class does not cross a separator, and for the same reason
                    # `*` and `?` stop at one: this rule list is shared by four
                    # repositories and matches paths, so a class that could
                    # stand for a `/` would let one segment's pattern swallow a
                    # whole subtree. Left to the regex engine, `[^y]` would.
                    out.append(f"[^/{body[1:]}]")
                else:
                    # Untouched. `/` is matched by a positively-listed class
                    # only when the pattern names it, which is a thing the
                    # author wrote rather than a thing negation let in.
                    out.append(f"[{body}]")
                index = end + 1
        else:
            out.append(re.escape(char))
            index += 1
    return "".join(out)


def expand(pattern: str) -> tuple[str, ...]:
    """A pattern with `{a,b}` alternation resolved into plain globs.

    Brace alternation is the one extension to the glob language here, and it
    earns its place by what it does to the list: four repositories keep the same
    kind of artifact under different directory names -- `sensor/` in one,
    `sensors/` in another, `misc/` in a third -- and without alternation each
    spelling needs its own rule with its own reason. A rule list where the same
    decision is written out five times is a list where one of the five will be
    missed when the decision changes.

    It is resolved by expansion rather than by a regex group, so a nested brace
    works and an unbalanced one is a literal, which is what every other glob
    implementation does with both.
    """
    start = pattern.find("{")
    if start == -1:
        return (pattern,)
    end = pattern.find("}", start)
    if end == -1:
        return (pattern,)
    head, body, tail = pattern[:start], pattern[start + 1 : end], pattern[end + 1 :]
    out: list[str] = []
    for option in body.split(","):
        out.extend(expand(head + option + tail))
    return tuple(out)


@cache
def _compiled(pattern: str) -> tuple[re.Pattern[str], ...]:
    return tuple(
        re.compile(f"(?:{_translate(alternative)})\\Z")
        for alternative in expand(pattern)
    )


def pattern_matches(pattern: str, path: str) -> bool:
    """Whether one glob matches one repo-relative path."""
    return any(compiled.match(path) for compiled in _compiled(pattern))


def decide(path: str, rules: Sequence[Rule]) -> Rule | None:
    """The first rule in ascending order that matches, or nothing.

    Nothing is a real answer and not a default: a path no rule matches is
    reported naming the path, because a rule list that leaves a hole is a rule
    list whose closure claim is false, and the moment to find out is here rather
    than when reading the counts.
    """
    for rule in sorted(rules, key=lambda item: item.order):
        if pattern_matches(rule.pattern, path):
            return rule
    return None


def partition(
    path_list: Iterable[str], ruleset: RuleSet
) -> tuple[dict[str, Rule], tuple[str, ...]]:
    """Every path's deciding rule, and the paths that no rule decided.

    The two halves are returned together because a hole in the rule list is only
    visible against a real path list: the rule list on its own cannot be complete
    or incomplete, only more or less broad. A path in the second half is a path
    the corpus does not know what to do with, and it is reported by name -- the
    alternative, excluding it silently, is how a rule list that has stopped
    covering a directory looks identical to one that never did.
    """
    decided: dict[str, Rule] = {}
    undecided: list[str] = []
    for path in path_list:
        rule = decide(path, ruleset.rules)
        if rule is None:
            undecided.append(path)
        else:
            decided[path] = rule
    return decided, tuple(undecided)


def _rules_from(document: Mapping[str, object], where: str) -> tuple[Rule, ...]:
    out: list[Rule] = []
    for entry in as_sequence(document.get("rules")):
        row = as_mapping(entry)
        if not row:
            continue
        order = row.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            raise CheckError(
                RULES_CHECK, where, f"a rule carries no integer `order`: {dict(row)}"
            )
        out.append(
            Rule(
                order=order,
                pattern=as_text(row.get("pattern")) or "",
                kind=as_text(row.get("kind")) or "",
                artifact_class=as_text(row.get("class")),
                reason=as_text(row.get("reason")) or "",
            )
        )
    return tuple(sorted(out, key=lambda rule: rule.order))


def load_rules() -> RuleSet:
    """The committed rule list, or `CheckError` if the file cannot be read.

    Every selection in the project is a function of this file, so a version of it
    that could not be read is not something to continue past: continuing would
    mean classifying files by a rule list that is partly missing, and the
    classification is what the corpus is built from.
    """
    path = paths.CATALOG / RULES_FILE
    relative = f"catalog/{RULES_FILE}"
    if not path.is_file():
        raise CheckError(RULES_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(RULES_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CheckError(RULES_CHECK, relative, f"cannot be parsed: {exc}") from exc
    document = as_mapping(loaded)

    authored: list[AuthoredPath] = []
    for entry in as_sequence(document.get("authored_paths")):
        row = as_mapping(entry)
        if not row:
            continue
        authored.append(
            AuthoredPath(
                path=as_text(row.get("path")) or "",
                artifact_class=as_text(row.get("class")) or "",
            )
        )

    altered: list[AlteredCoreCopy] = []
    for entry in as_sequence(document.get("altered_core_copies")):
        row = as_mapping(entry)
        if not row:
            continue
        altered.append(
            AlteredCoreCopy(
                path=as_text(row.get("path")) or "",
                reason=as_text(row.get("reason")) or "",
            )
        )

    return RuleSet(
        rules=_rules_from(document, relative),
        authored_paths=tuple(authored),
        altered_core_copies=tuple(altered),
    )


def check_rules(report: Report) -> None:
    """Every rule-list requirement that can be checked without a repository.

    The list is shared by four repos, so a defect here is a defect in every
    classification the project makes. These are the parts that need nothing from
    a clone: order uniqueness, the class a rule must and must not carry, the
    authored-path entries, and the altered-core copies.
    """
    ruleset = load_rules()
    where = f"catalog/{RULES_FILE}"
    _check_orders(report, ruleset, where)
    _check_classes(report, ruleset, where)
    _check_authored_paths(report, ruleset)
    _check_altered_core_copies(report, ruleset)


def _check_orders(report: Report, ruleset: RuleSet, where: str) -> None:
    seen: dict[int, Rule] = {}
    for rule in ruleset.rules:
        if rule.order in seen:
            other = seen[rule.order]
            report.add(
                RULES_CHECK,
                where,
                f"two rules share `order: {rule.order}` -- {other.pattern!r} and "
                f"{rule.pattern!r}; order is the whole precedence and two rules "
                "cannot hold one place in it",
            )
            continue
        seen[rule.order] = rule


def _check_classes(report: Report, ruleset: RuleSet, where: str) -> None:
    for rule in ruleset.rules:
        if rule.kind not in ("select", "exclude"):
            report.add(
                RULES_CHECK,
                where,
                f"rule {rule.order} has kind {rule.kind!r}; the known kinds are "
                "`select` and `exclude`",
            )
            continue
        if rule.selects and rule.artifact_class is None:
            report.add(
                RULES_CHECK,
                where,
                f"rule {rule.order} ({rule.pattern!r}) selects but carries no "
                "`class`; a selected file's class is its deciding rule's class, "
                "so a select rule without one leaves the file unclassifiable",
            )
        if not rule.selects and rule.artifact_class is not None:
            report.add(
                RULES_CHECK,
                where,
                f"rule {rule.order} ({rule.pattern!r}) excludes but carries "
                f"`class: {rule.artifact_class}`; an exclude rule confers no "
                "class, and one that named a class would be a class nothing "
                "received",
            )
        if rule.selects and rule.artifact_class not in paths.ARTIFACT_CLASSES:
            report.add(
                RULES_CHECK,
                where,
                f"rule {rule.order} ({rule.pattern!r}) confers "
                f"{rule.artifact_class!r}, which is not one of the closed set "
                f"{list(paths.ARTIFACT_CLASSES)}",
            )


def _check_authored_paths(report: Report, ruleset: RuleSet) -> None:
    """The entries are rules in the same list, and they cover the named classes.

    Listed again in `authored_paths` so the coverage requirement is checkable
    without re-deriving which rules are authored -- but "listed again" has to
    mean something, so each entry is matched against the rule list and must find
    a select rule carrying exactly the class the entry declares. An entry with no
    rule behind it would be a path nothing classifies; an entry whose rule
    disagrees would be two answers to one question.
    """
    present = {entry.artifact_class for entry in ruleset.authored_paths}
    for required in REQUIRED_AUTHORED_CLASSES:
        if required not in present:
            report.add(
                RULES_CHECK,
                f"catalog/{RULES_FILE}:authored_paths",
                f"names no path classed `{required}`; an author's own work is not "
                "where a directory convention puts it, so a list that names none "
                f"of it has stopped covering `{required}`",
            )

    for entry in ruleset.authored_paths:
        where = f"catalog/{RULES_FILE}:authored_paths:{entry.path or '<unnamed>'}"
        if not entry.path:
            report.add(RULES_CHECK, where, "carries no `path`")
            continue
        backing = [
            rule
            for rule in ruleset.rules
            if rule.selects and rule.pattern == entry.path
        ]
        if not backing:
            report.add(
                RULES_CHECK,
                where,
                "is not a select rule in `rules`; an authored path is a rule in "
                "the same list at a low order, so that it has a deciding rule to "
                "record and count like any other",
            )
            continue
        classes = {rule.artifact_class for rule in backing}
        if classes != {entry.artifact_class}:
            report.add(
                RULES_CHECK,
                where,
                f"declares class {entry.artifact_class!r}, but its rule in "
                f"`rules` confers {sorted(str(name) for name in classes)}",
            )


def _check_altered_core_copies(report: Report, ruleset: RuleSet) -> None:
    """Altered copies of core files are excluded, and never treated as authored.

    They are the one category where the obvious classification is wrong: the
    repo author changed them, so they look like the author's own work, but they
    did not write them. The entries are recorded separately so the exclusion has
    a reason of its own, and each must actually be excluded -- an entry for a
    path some select rule wins would be a claim the list does not make.
    """
    authored = {entry.path for entry in ruleset.authored_paths}
    for entry in ruleset.altered_core_copies:
        where = f"catalog/{RULES_FILE}:altered_core_copies:{entry.path or '<unnamed>'}"
        if not entry.path:
            report.add(RULES_CHECK, where, "carries no `path`")
            continue
        if entry.path in authored:
            report.add(
                RULES_CHECK,
                where,
                "is listed as an authored path as well as an altered core copy; "
                "the author changed it but did not write it, so the two "
                "classifications cannot both hold",
            )
            continue
        rule = decide(entry.path, ruleset.rules)
        if rule is None:
            report.add(
                RULES_CHECK,
                where,
                "is matched by no rule, so it is neither excluded with a reason "
                "nor selected with a class",
            )
        elif rule.selects:
            report.add(
                RULES_CHECK,
                where,
                f"is selected by rule {rule.order} ({rule.pattern!r}) as "
                f"{rule.artifact_class!r}, but an altered copy of a core file is "
                "neither the repo author's work nor third-party vendored, so it "
                "is excluded rather than attributed",
            )


def classes_of(paths_in: Iterable[str], ruleset: RuleSet) -> dict[str, str | None]:
    """Each path's class, or None where it is excluded. For callers that need
    the class only, without the deciding rule or the parse outcome."""
    out: dict[str, str | None] = {}
    for path in paths_in:
        rule = decide(path, ruleset.rules)
        out[path] = rule.artifact_class if rule is not None and rule.selects else None
    return out
