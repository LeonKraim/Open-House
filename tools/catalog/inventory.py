"""The file inventory: every tracked path in every reference repo, decided.

This is the one artifact produced by reading the four clones. It walks each
repo's `git ls-files`, applies the committed rule list to every path, and records
for each one whether it was selected, the class its deciding rule conferred, the
rule's `order`, and whether the file parsed. `catalog/inventory.json` is that
record, committed, and the walker is never run in CI -- the clones are
`gitignore`d, two of them grant no licence to redistribute, and a gate pinned to
four third-party repositories fails when they change rather than when we do.

So the module has two halves and they run in different places. `build()` reads
the clones and is local. `check_inventory()` reads the committed file and runs in
`oh-catalog validate`, which is why the closure it asserts is over the *record*
rather than over a repository: the file has to demonstrate its own completeness,
because nothing in CI can recount it.

The parse outcome is decided by the file's suffix and not by its artifact class.
A `.yaml` file is parsed and is `parsed` or `unparsed`; anything else is
`not_applicable`, because there is no YAML document in it to read. An excluded
file is `not_applicable` too -- the walker never opens it, and "we did not parse
this" is a different fact from "this did not parse". A dashboard is therefore
`parsed`: it is a YAML document and it does parse, and the separate fact that a
dashboard yields no behaviour records belongs to the extraction, not here. That
distinction is the reason `parse` is a field of its own rather than a fourth
class -- the two questions have different answers.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import errors, ha_yaml, licenses, paths, rules
from .errors import CheckError, Report
from .narrow import as_mapping, as_sequence, as_text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

INVENTORY_CHECK = "inventory"

INVENTORY_FILE = "inventory.json"

#: The suffixes parsed as documents. Everything else is `not_applicable`.
YAML_SUFFIXES: tuple[str, ...] = (".yaml", ".yml")

#: What `parse` means for a file the walker never opened.
NOT_PARSED = "not_applicable"


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    selected: bool
    artifact_class: str | None
    parse: str
    rule: int
    error: str | None

    def as_document(self) -> dict[str, object]:
        return {
            "path": self.path,
            "selected": self.selected,
            "class": self.artifact_class,
            "parse": self.parse,
            "rule": self.rule,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RepoInventory:
    repo: str
    files: tuple[FileRecord, ...]

    @property
    def counts(self) -> dict[str, int]:
        selected = sum(1 for record in self.files if record.selected)
        return {
            "tracked": len(self.files),
            "selected": selected,
            "excluded": len(self.files) - selected,
        }

    @property
    def by_class(self) -> dict[str, int]:
        """Selected files per class. Absent rather than zero for a class with no
        files, because the check that reconciles the sum is what makes a class
        dropping out visible."""
        return dict(
            Counter(
                record.artifact_class
                for record in self.files
                if record.selected and record.artifact_class is not None
            )
        )

    @property
    def by_rule(self) -> dict[str, dict[str, int]]:
        """Each deciding rule's partition of the files it decided.

        Three counts rather than one, because three closures are read off this
        map and none of them implies the others. `decided` is taken over every
        decided file, excluded and selected alike, so that a rule which selected
        files is present rather than absent -- and absent is exactly how a rule
        that decided nothing reads, which is the ambiguity the map exists to
        remove. That makes `decided` what closes over `git ls-files`.
        `excluded` is the figure the closure clause names per deciding rule, and
        it cannot be read off `decided`; `selected` rides along so the three can
        be checked against each other, since `decided` is their sum by
        construction and a map that got two of the three right from different
        files would otherwise pass.

        `selected` comes from a second pass rather than from subtracting, which
        is deliberate: subtracting would make the identity true by fiat and the
        check that reads it vacuous.
        """
        decided = Counter(record.rule for record in self.files)
        selected = Counter(record.rule for record in self.files if record.selected)
        return {
            str(order): {
                "decided": count,
                "selected": selected.get(order, 0),
                "excluded": count - selected.get(order, 0),
            }
            for order, count in sorted(decided.items())
        }

    @property
    def by_parse(self) -> dict[str, int]:
        """Selected files per parse outcome, so the map sums to `selected`."""
        return dict(Counter(record.parse for record in self.files if record.selected))

    def as_document(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "counts": self.counts,
            "files": [record.as_document() for record in self.files],
            "by_class": self.by_class,
            "by_rule": self.by_rule,
            "by_parse": self.by_parse,
        }


# --------------------------------------------------------------------------
# Reading the clones -- local only
# --------------------------------------------------------------------------


def _git(repo_dir: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise CheckError(
            INVENTORY_CHECK,
            f"ressources/{repo_dir.name}",
            f"`git {' '.join(args)}` failed: {message}",
        )
    return result.stdout


def clone_handle(repo_dir: Path) -> str:
    """The owner handle of a clone, read from its `origin` remote.

    Read from the remote rather than taken from the directory name, because the
    directory names do not contain the handles -- `ressources/Home-AssistantConfig`
    is CCOSTAN's and `ressources/hassio-config` is johnkoht's -- and a table
    mapping one to the other would be a second place the corpus's identity is
    written down, free to disagree with the first.
    """
    url = (
        _git(repo_dir, "config", "--get", "remote.origin.url")
        .decode("utf-8", errors="replace")
        .strip()
    )
    path = url.removesuffix(".git").removesuffix("/")
    parts = path.replace(":", "/").split("/")
    # Lowercased because the remote spells it `CCOSTAN` and every other record in
    # the project spells it `ccostan`: GitHub handles are case-insensitive, so the
    # two are the same repository, and the project picked one spelling in
    # `catalog/README.md` for `licenses.yaml`, `repos.yaml` and the golden
    # filenames. A handle taken verbatim from the remote would match none of them.
    if len(parts) < 2 or not parts[-2]:
        raise CheckError(
            INVENTORY_CHECK,
            f"ressources/{repo_dir.name}",
            f"has an `origin` remote {url!r} that names no owner; the handle is "
            "what ties a clone to its licence record and its golden file, so a "
            "clone without one is a clone nothing else can be matched to",
        )
    return parts[-2].lower()


def discover_clones() -> dict[str, Path]:
    """Every clone under `ressources/`, keyed by owner handle.

    A directory is a clone when it has a `.git`; anything else in `ressources/`
    is ignored rather than reported, since the directory is gitignored scratch
    space and a stray file there is not a claim about the corpus.
    """
    found: dict[str, Path] = {}
    if not paths.RESSOURCES.is_dir():
        return found
    for child in sorted(paths.RESSOURCES.iterdir()):
        if not (child / ".git").exists():
            continue
        handle = clone_handle(child)
        if handle in found:
            raise CheckError(
                INVENTORY_CHECK,
                "ressources",
                f"{child.name!r} and {found[handle].name!r} both report the "
                f"owner {handle!r}; one of them is not the repository it says "
                "it is",
            )
        found[handle] = child
    return found


def tracked_files(repo_dir: Path) -> tuple[str, ...]:
    """The clone's `git ls-files`, sorted, verbatim.

    `-z` rather than newline-separated output for one reason: git quotes a path
    containing non-ASCII bytes by default, writing `caf\\303\\251.yaml` for
    `café.yaml`, and a quoted path is not a path the rule list can match. `-z`
    turns the quoting off. The paths in these four repositories happen to be
    ASCII, so the difference is invisible today -- which is exactly the kind of
    thing that stops being true in the one repository nobody re-checks.
    """
    raw = _git(repo_dir, "ls-files", "-z")
    try:
        names = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CheckError(
            INVENTORY_CHECK,
            f"ressources/{repo_dir.name}",
            f"has a tracked path that is not UTF-8: {exc}",
        ) from exc
    return tuple(sorted(name for name in names.split("\0") if name))


def parse_outcome(repo_dir: Path, record_path: str) -> tuple[str, str | None]:
    """Whether a selected file parsed, and the error if it did not."""
    if not record_path.endswith(YAML_SUFFIXES):
        return NOT_PARSED, None
    try:
        text = errors.read_text(repo_dir / record_path)
    except (OSError, UnicodeDecodeError) as exc:
        return "unparsed", f"cannot be read: {exc}"
    _, error = ha_yaml.parse(text)
    if error is not None:
        return "unparsed", error
    return "parsed", None


def decide_repo(repo: str, repo_dir: Path, ruleset: rules.RuleSet) -> RepoInventory:
    """One repo's every tracked path, decided in rule order.

    A path no rule matches is a failure rather than a skip. The spec's closure is
    over the whole `git ls-files` output, and the only way to close over it while
    dropping a file is to have stopped closing over it -- so the file is named,
    with the repo it is in, and the run stops.
    """
    records: list[FileRecord] = []
    undecided: list[str] = []
    for tracked in tracked_files(repo_dir):
        rule = rules.decide(tracked, ruleset.rules)
        if rule is None:
            undecided.append(tracked)
            continue
        if rule.selects:
            outcome, error = parse_outcome(repo_dir, tracked)
        else:
            outcome, error = NOT_PARSED, None
        records.append(
            FileRecord(
                path=tracked,
                selected=rule.selects,
                artifact_class=rule.artifact_class if rule.selects else None,
                parse=outcome,
                rule=rule.order,
                error=error,
            )
        )
    if undecided:
        raise CheckError(
            INVENTORY_CHECK,
            f"ressources/{repo_dir.name}",
            f"{len(undecided)} tracked file(s) are matched by no rule, the first "
            f"being {undecided[0]!r}; a rule list that leaves a hole is a rule "
            "list whose closure claim is false, and the hole is invisible in "
            "every count taken over the files that did match",
        )
    return RepoInventory(repo=repo, files=tuple(records))


def untracked_authored_paths(
    repos: Mapping[str, Path], ruleset: rules.RuleSet
) -> tuple[str, ...]:
    """The authored paths that match no tracked file in any repo, sorted.

    `authored_paths` is hand-written evidence: each entry is a claim that an
    author wrote that path. A claim about a path that exists nowhere is a claim
    about nothing, and it is the one failure of that list the rule list cannot
    see, because `rules.check_rules` never consults a path list and could not --
    a glob conforms to its declared class whether or not anything matches it.

    Across the four repos together rather than one at a time: an authored path
    belongs to the repo whose author wrote it, so a pattern for one repo's tree
    legitimately matches nothing in the other three, and a per-repo reading would
    report three defects for every correct entry.

    This is a local check, and the split is the spec's rather than a convenience.
    Deciding it needs `git ls-files`, and the boundary is drawn at what actually
    needs the clones: the spec names exactly two local checks and says no CI job
    performs this read. The CI half of the same list -- that each entry has a
    backing rule carrying its declared class, and that at least one entry is
    `custom_integration` and one is `blueprint` -- is in `rules._check_authored_paths`.

    Returns rather than raises, so that a caller can report every untracked path
    in one diagnostic instead of one run per path.
    """
    tracked = [tracked_files(directory) for directory in repos.values()]
    return tuple(
        sorted(
            entry.path
            for entry in ruleset.authored_paths
            if entry.path
            and not any(
                rules.pattern_matches(entry.path, path)
                for paths_in_repo in tracked
                for path in paths_in_repo
            )
        )
    )


def build() -> dict[str, object]:
    """The whole inventory, ready to be written.

    The repo set is `licenses.yaml`'s and the clones' set has to agree with it in
    both directions: a recorded repo with no clone cannot be walked, and a clone
    nothing recorded is a repository whose files would enter the corpus with no
    licence record and no golden file behind them.
    """
    ruleset = rules.load_rules()
    clones = discover_clones()
    recorded = [record.repo for record in licenses.load_licences() if record.repo]

    missing = [repo for repo in recorded if repo not in clones]
    if missing:
        raise CheckError(
            INVENTORY_CHECK,
            "ressources",
            f"no clone for {missing}; every recorded repo is walked, so a "
            "missing one produces a corpus that is smaller than the one the "
            "licence records describe",
        )
    unknown = sorted(set(clones) - set(recorded))
    if unknown:
        raise CheckError(
            INVENTORY_CHECK,
            "ressources",
            f"{unknown} are cloned but recorded in no licence record, so their "
            "files would enter the corpus with no licence behind them",
        )

    untracked = untracked_authored_paths(clones, ruleset)
    if untracked:
        raise CheckError(
            INVENTORY_CHECK,
            f"catalog/{rules.RULES_FILE}:authored_paths",
            f"{list(untracked)} match no tracked file in any of the clones; an "
            "authored path is the claim that an author wrote that path, and a "
            "claim about a path that exists nowhere is a claim about nothing",
        )

    repos = [decide_repo(repo, clones[repo], ruleset) for repo in recorded]
    totals = {
        "tracked": sum(repo.counts["tracked"] for repo in repos),
        "selected": sum(repo.counts["selected"] for repo in repos),
        "excluded": sum(repo.counts["excluded"] for repo in repos),
    }
    return {"repos": [repo.as_document() for repo in repos], "totals": totals}


def render(document: Mapping[str, object]) -> str:
    """The inventory as the bytes that get committed.

    `ensure_ascii=False` so that a path is the path, and `sort_keys=False`
    because the document is assembled in a fixed order already -- re-sorting it
    would put `by_class` before `files` and make the file harder to read without
    making it any more deterministic than it is. A trailing newline because every
    other file in the project has one.
    """
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_inventory() -> Path:
    """Build the inventory and write it to `catalog/inventory.json`."""
    target = paths.CATALOG / INVENTORY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(build()), encoding="utf-8", newline="\n")
    return target


# --------------------------------------------------------------------------
# Reading the committed file -- runs in CI
# --------------------------------------------------------------------------


def load_inventory() -> Mapping[str, object]:
    """The committed inventory, or `CheckError` if it cannot be read."""
    path = paths.CATALOG / INVENTORY_FILE
    relative = f"catalog/{INVENTORY_FILE}"
    if not path.is_file():
        raise CheckError(INVENTORY_CHECK, relative, "does not exist")
    try:
        text = errors.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CheckError(INVENTORY_CHECK, relative, f"cannot be read: {exc}") from exc
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CheckError(
            INVENTORY_CHECK, relative, f"is not valid JSON: {exc}"
        ) from exc
    document = as_mapping(loaded)
    if not document:
        raise CheckError(INVENTORY_CHECK, relative, "is not a JSON object")
    return document


def check_inventory(report: Report) -> None:
    """Everything the committed inventory has to say about itself.

    It is generated from four repositories CI cannot see, so its completeness
    cannot be recounted -- it has to be *self-closing*. Every number in it is
    therefore reconciled against the file list beside it, and the union of the
    per-rule counts against the rule list itself. A generator bug that dropped a
    file, or a rule change that left a rule deciding nothing, shows up as
    arithmetic that does not add up rather than as a corpus that is quietly
    smaller.
    """
    document = load_inventory()
    where = f"catalog/{INVENTORY_FILE}"

    repos = as_sequence(document.get("repos"))
    if not repos:
        report.add(INVENTORY_CHECK, where, "records no repos")

    seen_rule_orders: set[int] = set()
    total_seen = {"tracked": 0, "selected": 0, "excluded": 0}
    for entry in repos:
        row = as_mapping(entry)
        repo_where = f"{where}:{as_text(row.get('repo')) or '<unnamed>'}"
        counted = _check_repo(report, repo_where, row, seen_rule_orders)
        for name in total_seen:
            total_seen[name] += counted[name]

    _check_totals(report, where, as_mapping(document.get("totals")), total_seen)
    _check_rules_all_seen(report, seen_rule_orders)
    _check_repo_set(report, where, repos)


def _check_repo(
    report: Report,
    where: str,
    row: Mapping[str, object],
    seen_rule_orders: set[int],
) -> dict[str, int]:
    """One repo block, reconciled. Returns the counts it declared, so the totals
    can be checked against the sum of the blocks rather than against a
    separately-kept number."""
    declared = as_mapping(row.get("counts"))
    tracked = _int_or_none(declared.get("tracked"))
    selected = _int_or_none(declared.get("selected"))
    excluded = _int_or_none(declared.get("excluded"))

    files = as_sequence(row.get("files"))
    classes: Counter[str] = Counter()
    parses: Counter[str] = Counter()
    rules_seen: Counter[str] = Counter()
    rules_selected: Counter[str] = Counter()
    rules_excluded: Counter[str] = Counter()
    for index, entry in enumerate(files):
        record = as_mapping(entry)
        path = as_text(record.get("path")) or f"<entry {index}>"
        file_where = f"{where}:{path}"

        is_selected = record.get("selected")
        artifact_class = record.get("class")
        parse = as_text(record.get("parse"))
        error = record.get("error")
        order = _int_or_none(record.get("rule"))

        if not isinstance(is_selected, bool):
            report.add(INVENTORY_CHECK, file_where, "has no boolean `selected`")
            continue
        if is_selected and artifact_class is None:
            report.add(
                INVENTORY_CHECK,
                file_where,
                "is selected but carries no `class`; a selected file's class is "
                "its deciding rule's class, so a selected file without one has "
                "no class to count under",
            )
        if not is_selected and artifact_class is not None:
            report.add(
                INVENTORY_CHECK,
                file_where,
                f"is excluded but carries `class: {artifact_class}`; an excluded "
                "file receives no class and needs no exception entry",
            )
        if isinstance(artifact_class, str):
            if artifact_class not in paths.ARTIFACT_CLASSES:
                report.add(
                    INVENTORY_CHECK,
                    file_where,
                    f"carries class {artifact_class!r}, which is not one of the "
                    f"closed set {list(paths.ARTIFACT_CLASSES)}",
                )
            else:
                classes[artifact_class] += 1
        if parse is None or parse not in paths.PARSE_OUTCOMES:
            report.add(
                INVENTORY_CHECK,
                file_where,
                f"carries parse outcome {parse!r}, which is not one of "
                f"{list(paths.PARSE_OUTCOMES)}",
            )
        elif is_selected:
            parses[parse] += 1
        if parse == "unparsed" and not (isinstance(error, str) and error.strip()):
            report.add(
                INVENTORY_CHECK,
                file_where,
                "is recorded `unparsed` with no `error`; the error is what makes "
                "the outcome actionable, and a file that will not parse is "
                "otherwise indistinguishable from one nobody looked at",
            )
        if parse != "unparsed" and error is not None:
            report.add(
                INVENTORY_CHECK,
                file_where,
                f"records error {error!r} against parse outcome {parse!r}; the "
                "error field is non-null exactly where the outcome is `unparsed`",
            )
        if order is None:
            report.add(INVENTORY_CHECK, file_where, "has no integer `rule` order")
        else:
            rules_seen[str(order)] += 1
            if is_selected:
                rules_selected[str(order)] += 1
            else:
                rules_excluded[str(order)] += 1
            seen_rule_orders.add(order)

    if tracked is not None and len(files) != tracked:
        report.add(
            INVENTORY_CHECK,
            where,
            f"declares {tracked} tracked file(s) and lists {len(files)}; the "
            "closure over `git ls-files` is the list, so a count that disagrees "
            "with it is a closure over something else",
        )
    if (
        selected is not None
        and excluded is not None
        and tracked is not None
        and selected + excluded != tracked
    ):
        report.add(
            INVENTORY_CHECK,
            where,
            f"declares {selected} selected and {excluded} excluded, which do not "
            f"sum to the {tracked} tracked it also declares",
        )

    _reconcile(
        report, where, "by_class", as_mapping(row.get("by_class")), classes, selected
    )
    _reconcile(
        report, where, "by_parse", as_mapping(row.get("by_parse")), parses, selected
    )
    _reconcile_rules(
        report,
        where,
        as_mapping(row.get("by_rule")),
        rules_seen,
        rules_selected,
        rules_excluded,
        len(files),
    )

    return {
        "tracked": tracked or 0,
        "selected": selected or 0,
        "excluded": excluded or 0,
    }


def _reconcile(
    report: Report,
    where: str,
    field: str,
    declared: Mapping[str, object],
    counted: Counter[str],
    expected_sum: int | None,
) -> None:
    """A per-key count map against the file list it was taken from.

    Both directions, and both matter. A key whose declared count disagrees with
    the list is a map that was not taken from this list. A key missing from the
    map is a count that has gone to zero and been dropped rather than reported --
    the schema admits that, so it is the check that has to notice.
    """
    for key, value in declared.items():
        number = _int_or_none(value)
        if number is None:
            report.add(
                INVENTORY_CHECK, f"{where}:{field}", f"{key!r} has no integer count"
            )
            continue
        actual = counted.get(key, 0)
        if number != actual:
            report.add(
                INVENTORY_CHECK,
                f"{where}:{field}",
                f"declares {number} for {key!r} against {actual} file(s) "
                "actually carrying it",
            )
    for key in sorted(set(counted) - set(declared)):
        report.add(
            INVENTORY_CHECK,
            f"{where}:{field}",
            f"omits {key!r}, which {counted[key]} file(s) carry; a count missing "
            "from the map is indistinguishable from a count that is zero",
        )
    total = sum(counted.values())
    if expected_sum is not None and total != expected_sum:
        report.add(
            INVENTORY_CHECK,
            where,
            f"`{field}` covers {total} file(s) against {expected_sum} it should; "
            "the map and the list beside it are two views of one partition and "
            "they have to cover the same files",
        )


def _reconcile_rules(
    report: Report,
    where: str,
    declared: Mapping[str, object],
    decided: Counter[str],
    selected: Counter[str],
    excluded: Counter[str],
    expected_decided: int,
) -> None:
    """The per-rule map against the file list, one count at a time.

    Three counters rather than one, because the map makes three claims and they
    fail separately. `decided` is the closure over `git ls-files`, and the
    counter it is compared with is taken over every decided file, so a rule
    missing from the map is a rule the check cannot tell from one that decided
    nothing. `excluded` is the per-deciding-rule figure the closure clause names,
    reported rather than left for the reader to reconstruct from `selected`.

    Comparing all three is not redundancy: `decided == selected + excluded` is an
    arithmetic identity *within* an entry, so an entry that took `decided` from
    the file list and guessed the split would satisfy every check that only
    compared the sum. Only counting each from the list can catch that.

    A rule whose entry is not a mapping at all is reported and skipped rather
    than indexed into, because the schema admits any value here and a traceback
    from the pre-commit hook is not a diagnostic.
    """
    fields = (("decided", decided), ("selected", selected), ("excluded", excluded))
    for key, value in declared.items():
        counts = as_mapping(value)
        if not counts:
            report.add(
                INVENTORY_CHECK,
                f"{where}:by_rule",
                f"{key!r} does not hold the counts for that rule; an entry is a "
                "mapping carrying `decided`, `selected` and `excluded`",
            )
            continue
        for field, counted in fields:
            number = _int_or_none(counts.get(field))
            if number is None:
                report.add(
                    INVENTORY_CHECK,
                    f"{where}:by_rule:{key}",
                    f"declares no integer `{field}`",
                )
                continue
            actual = counted.get(key, 0)
            if number != actual:
                report.add(
                    INVENTORY_CHECK,
                    f"{where}:by_rule:{key}",
                    f"declares {number} {field} against {actual} file(s) the rule "
                    "decided",
                )
        for extra in sorted(set(counts) - {name for name, _ in fields}):
            report.add(
                INVENTORY_CHECK,
                f"{where}:by_rule:{key}",
                f"carries {extra!r}, which is not one of `decided`, `selected` or "
                "`excluded`",
            )
    for key in sorted(set(decided) - set(declared)):
        report.add(
            INVENTORY_CHECK,
            f"{where}:by_rule",
            f"omits {key!r}, which decided {decided[key]} file(s); a rule missing "
            "from the map is indistinguishable from a rule that decided nothing",
        )
    declared_total = sum(
        number
        for number in (
            _int_or_none(as_mapping(value).get("decided"))
            for value in declared.values()
        )
        if number is not None
    )
    if declared_total != expected_decided:
        report.add(
            INVENTORY_CHECK,
            where,
            f"`by_rule` covers {declared_total} file(s) against the "
            f"{expected_decided} listed; the map and the list beside it are two "
            "views of one partition and they have to cover the same files",
        )


def _check_totals(
    report: Report,
    where: str,
    declared: Mapping[str, object],
    counted: Mapping[str, int],
) -> None:
    for name, value in counted.items():
        number = _int_or_none(declared.get(name))
        if number is None:
            report.add(INVENTORY_CHECK, where, f"totals carry no `{name}`")
        elif number != value:
            report.add(
                INVENTORY_CHECK,
                where,
                f"totals declare {number} {name} against {value} summed over the "
                "repos; the total is what a reader takes the corpus's size from "
                "without adding up four blocks",
            )


def _check_rules_all_seen(report: Report, seen: set[int]) -> None:
    """The clause the rule list's teeth are in: no rule unseen.

    Every rule in the list decided at least one file.

    This is the clause that gives a rule list its teeth. `file_rules.yaml` was
    narrowed over the four repositories until every rule matched something, and
    three rules were deleted for matching nothing at all -- an archive rule, a
    templated-YAML rule and a service-unit rule, each of which read as coverage
    and decided no file in any of the four repos. A rule that decides nothing is
    not harmless: it is a line a reader will take as evidence that some case is
    handled, and the count that would have shown otherwise is a zero nobody
    prints.
    """
    ruleset = rules.load_rules()
    known = {rule.order for rule in ruleset.rules}
    for order in sorted(known - seen):
        rule = ruleset.by_order()[order]
        report.add(
            INVENTORY_CHECK,
            "catalog",
            f"rule {order} ({rule.pattern!r}) decided none of the tracked files in "
            "any repo; a rule matching nothing reads as coverage while covering "
            "nothing",
        )
    for order in sorted(seen - known):
        report.add(
            INVENTORY_CHECK,
            "catalog",
            f"the inventory records files decided by rule {order}, which is not "
            "in `catalog/file_rules.yaml`; the per-rule counts would no longer "
            "close over the list they were produced from",
        )


def _check_repo_set(report: Report, where: str, repos: Sequence[object]) -> None:
    """The inventory covers exactly the repos the licence records name."""
    recorded = {record.repo for record in licenses.load_licences() if record.repo}
    listed = {
        name
        for name in (as_text(as_mapping(entry).get("repo")) for entry in repos)
        if name
    }
    for repo in sorted(recorded - listed):
        report.add(
            INVENTORY_CHECK,
            where,
            f"records nothing for `{repo}`, which has a licence record; a repo "
            "with a licence record and no inventory is a repo whose files were "
            "never classified and whose absence no count shows",
        )
    for repo in sorted(listed - recorded):
        report.add(
            INVENTORY_CHECK,
            where,
            f"records `{repo}`, which has no licence record; its files would "
            "enter the corpus with no licence behind them",
        )


def _int_or_none(value: object) -> int | None:
    """An integer, or None. `bool` is excluded despite being an `int` subclass,
    because `true` in a position expecting a count is a document that means
    something else."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
