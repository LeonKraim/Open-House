"""The pack verbs: `validate` over a directory, `diff-permissions` over a pair.

`spec.txt:60` names three verbs and all three are here:

- `validate_directory` answers "is every pack in this directory well-formed",
  against the schema and the sandbox, with each failure carrying its **class**.
- `diff_permissions` answers a question neither the schema nor `validate` asks:
  "what can this pack do that the last one could not". It reads two manifests,
  needs no house and no install, and runs no validation.
- `test_pack` installs a pack into a fresh session per scenario and runs each
  one, keeping "passed" and "no scenario ran" apart.

**Where a pack's scenarios live is the caller's answer and not this module's.**
The requirement says `test` runs "the scenarios a pack ships", and nothing in the
package says where those are -- a pack directory holds pinned artefacts beside
its manifest and `sim.scenario`'s loader takes any path rather than a convention,
so a verb that guessed a location would be this phase inventing a format. The
verb takes the scenario paths a caller names, expands a directory as a corpus,
and reports a pack no scenario names as `untested` rather than as a pass.

**The classes are the reason `validate` is a verb and not a boolean.** "Invalid"
collapses remedies that are not alike: a schema failure is a file rewritten
against the current version, a banned service is a policy decision someone has to
make, and a licence failure is a grant that has to be obtained. So a failure
carries a class from this phase's own list where one fits and its own refusal
reason where none does -- the requirement says the list is what this phase
distinguishes, not that its checks produce no others.

**A verb never reports a pack it did not read.** Two consequences, both
deliberate: an empty directory is a failure rather than a pass, because "no packs
found" and "every pack is fine" must not be the same output; and a file in the
directory that will not parse is a finding rather than a skip, because a
candidate the verb could not read is the one case where silence reads as
approval.

The two verbs sit outside `OPERATIONS` for the reason `SCENARIO_ENTRY` does:
`control-surface` fixes that registry at the ten operations `spec.txt` enumerates
and `tests/test_operations.py` asserts the equality, so a verb joins the surface
the way the scenario runner did -- a descriptor of its own, generated onto each
face from one definition -- rather than by widening a closed set that check is
right to keep closed.

**`derive_packs` is here as well**, which is a wider reading of this module's
name than "verb" and is deliberate: task 8.1 asks for the derivation over
`catalog/behaviors.yaml` and the only paths this phase owns are `packs/official/`,
`scenarios/`, new files under `engine/behaviours/` and this file. `tools/` is
frozen, so the derivation cannot live beside the catalog checks it belongs with,
and a private helper inside a test module is not a thing the shipped tree can be
regenerated from. The compromise is stated rather than hidden: the emitter and
the report live in the same file as the verbs, reading the corpus through
`tools.catalog.behaviors.load_behaviors` so no second parser of that file exists.

The derivation's *shape* is where this phase's one unanswered question is
settled, and it is settled the way the corpus rather than the requirement reads:
a derived pack reproduces a row's **expression**, and eighteen of the nineteen
reusable rows carry none -- `expression` is source-derived structure and is
withheld unless every source repo's *code* grant is reusable, and the extraction
populated it once. So a candidate row that carries no expression is skipped and
named, rather than packed as a shell with an invented behaviour: task 8.1 forbids
"inventing a hand-written pack under a derived label" for an `ideas_only` row and
the same sentence forbids it here. The requirement's report names two skip reasons
and the corpus needs four, and the four are enumerated in `SKIP_REASONS` with the
reason each is reachable.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TypeGuard, cast

import yaml

import engine.sandbox as pack_sandbox
import sim.scenario as sim_scenario
from engine import manifest as engine_manifest
from engine.vocabulary import (
    BehaviourVocabulary,
    ManifestArtifacts,
    PackPolicy,
    Vocabulary,
    VocabularyError,
    load_behaviour_vocabulary,
    load_manifest_artifacts,
)
from openhouse import packs as pack_installation
from openhouse import scenarios as openhouse_scenarios
from tools.catalog import behaviors as catalog_behaviors
from tools.catalog import paths

__all__ = [
    "EXIT_OK",
    "EXIT_PACK_FAILURE",
    "EXIT_USAGE",
    "GENERATED_MARKER",
    "OUTCOMES",
    "SKIP_REASONS",
    "CorpusBound",
    "DerivationReport",
    "DerivedPack",
    "Finding",
    "Gain",
    "ManifestReport",
    "PackTestReport",
    "PermissionDiff",
    "ScenarioOutcome",
    "SkippedRow",
    "UsageError",
    "ValidateReport",
    "derive_packs",
    "diff_permissions",
    "test_pack",
    "validate_directory",
]

#: The three statuses a verb exits with, and the requirement's own three: a run
#: that succeeded, a pack that failed its checks, and a caller who named
#: something that is not there. They are distinct because "this pack is broken"
#: and "I called this wrong" have different authors.
EXIT_OK = 0
EXIT_PACK_FAILURE = 1
EXIT_USAGE = 2


class UsageError(Exception):
    """The caller named something that cannot be read, as distinct from a pack."""

    def __init__(self, about: str, reason: str) -> None:
        self.about = about
        self.reason = reason
        super().__init__(f"{about}: {reason}")


#: The classes this phase distinguishes, and the reason each folds into one. The
#: keys are exactly `engine.manifest.REASONS` and `engine.sandbox.REASONS` -- the
#: sets each module enumerates -- so this map cannot invent a reason no check
#: produces. A reason absent from the map is reported under its own name rather
#: than bucketed into a class that would misdescribe it.
_CLASSES: dict[str, str] = {
    # engine.manifest.REASONS
    "schema": "schema",
    "unknown_term": "unknown_term",
    "unknown_licence": "licence",
    "licence_too_restrictive": "licence",
    "ideas_only_source": "derivation",
    "unknown_source_row": "derivation",
    "handwritten_derivation": "derivation",
    # engine.sandbox.REASONS
    "unknown_trigger": "unknown_term",
    "unknown_condition": "unknown_term",
    "unknown_action": "unknown_term",
    "unknown_behaviour": "unknown_term",
    "forbidden_trigger": "non_declarative_term",
    "forbidden_condition": "non_declarative_term",
    "forbidden_action": "non_declarative_term",
    "literal_reference": "literal_entity",
    "undeclared_service": "undeclared_service",
    "banned_service": "banned_service",
    "dangling_path": "provides",
    "escaping_path": "provides",
    "class_mismatch": "provides",
}


def _klass(reason: str) -> str:
    """The class a reason is reported under, or the reason itself.

    The fallback is the point of the sentence above: a structural failure no
    listed class claims -- a slot the pack never declared, a behaviour reaching
    outside its own room -- is still reported *with* a class and its constraint
    named, rather than arriving unlabelled.
    """
    return _CLASSES.get(reason, reason)


@dataclass(frozen=True, slots=True)
class Finding:
    """One reason a manifest is not valid, located and classified."""

    at: str
    klass: str
    message: str

    def to_document(self) -> dict[str, object]:
        return {"at": self.at, "class": self.klass, "message": self.message}


@dataclass(frozen=True, slots=True)
class ManifestReport:
    """One manifest's outcome: what it is, and everything found against it."""

    path: str
    name: str
    findings: tuple[Finding, ...] = ()
    flags: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_document(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "valid": self.ok,
            "findings": [finding.to_document() for finding in self.findings],
            "flags": list(self.flags),
        }


@dataclass(frozen=True, slots=True)
class ValidateReport:
    """Every manifest a directory holds, and what was found against each.

    `unreadable` is carried separately from `reports` because the two kinds of
    absence differ: a manifest that failed is in `reports` with its findings, and
    a file nothing could open is named in `unreadable` so it cannot be mistaken
    for one of the ones that passed.
    """

    directory: str
    reports: tuple[ManifestReport, ...] = ()
    unreadable: tuple[str, ...] = field(default=())

    @property
    def checked(self) -> int:
        return len(self.reports)

    @property
    def ok(self) -> bool:
        """Whether every manifest was read and none failed. Nothing is a no."""
        return self.checked > 0 and all(report.ok for report in self.reports)

    def to_document(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "checked": self.checked,
            "valid": self.ok,
            "manifests": [report.to_document() for report in self.reports],
            "unreadable": list(self.unreadable),
        }


@dataclass(frozen=True, slots=True)
class Gain:
    """A permission the later manifest declares and the earlier one did not."""

    service: str
    behaviour: str | None
    klass: str

    def to_document(self) -> dict[str, object]:
        return {
            "service": self.service,
            "behaviour": self.behaviour,
            "class": self.klass,
        }


@dataclass(frozen=True, slots=True)
class PermissionDiff:
    """What a pack gained and lost between two versions of itself."""

    before: str
    after: str
    gained: tuple[Gain, ...] = ()
    lost: tuple[str, ...] = ()

    @property
    def narrows(self) -> bool:
        """Whether the later version lost a permission. Reported, not warned on."""
        return bool(self.lost)

    def to_document(self) -> dict[str, object]:
        return {
            "before": self.before,
            "after": self.after,
            "gained": [gain.to_document() for gain in self.gained],
            "lost": list(self.lost),
        }


def _where(root: Path | None) -> Path:
    """The repository the schemas, the vocabulary and the policy are read from."""
    return paths.ROOT if root is None else root


def _candidates(directory: Path) -> tuple[list[Path], list[str]]:
    """Every YAML file under `directory`, and the ones that could not be opened.

    Sorted by path and not by directory order, so two runs over one tree report
    the same list -- which is what makes a report comparable rather than merely
    similar.
    """
    present: list[Path] = []
    unreadable: list[str] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix not in {".yaml", ".yml"}:
            continue
        relative = path.relative_to(directory).as_posix()
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable.append(relative)
            continue
        present.append(path)
    return present, unreadable


def _parsed(path: Path) -> object | None:
    """A file's contents, or `None` when it is not YAML this project can read."""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _is_manifest(document: object) -> TypeGuard[dict[str, object]]:
    """Whether a parsed document claims to be a pack manifest.

    `kind` and `name` together, because `kind` alone is a key no pinned artefact
    in this tree carries and `name` alone is on nearly all of them. A document
    that is neither is a file sitting beside a pack rather than a pack, and is
    not reported: it was not skipped, it was never a candidate. The manifest
    schema requires both, so nothing a validator would accept is passed over.
    """
    return isinstance(document, dict) and "kind" in document and "name" in document


def validate_directory(
    directory: str | Path, *, root: Path | None = None
) -> ValidateReport:
    """Validate every pack manifest in `directory` against the schema and sandbox.

    Raises `UsageError` when `directory` is not a directory, which is the case
    the requirement calls a usage error rather than a pack failure: no pack is
    named invalid, because no pack was reached.

    `root` is the tree a manifest's `provides` paths are written against, and it
    is not the same thing as the project -- a `provides` path is resolved against
    a tree and then required to land inside the pack's own directory, so a
    directory of packs outside the repository needs `root` pointed at itself.
    The schema, the licence list, the corpus and the vocabulary are the
    *project's* and are read from `tools.catalog.paths.ROOT` whatever `root`
    says, because they are what every pack everywhere is judged against and a
    caller validating a copied tree must not be able to weaken that by accident.
    Two roots rather than one for the reason `engine.sandbox.check_pack` takes
    them separately: resolution is the tree's, judgement is the project's.
    """
    base = Path(directory)
    if not base.is_dir():
        raise UsageError(str(base), "is not a directory, so no pack was read")

    where = _where(root)
    project = _where(None)
    vocabulary = Vocabulary.load(project)
    artifacts = load_manifest_artifacts(project)
    published = load_behaviour_vocabulary(project)

    reports: list[ManifestReport] = []
    present, unreadable = _candidates(base)
    for path in present:
        relative = path.relative_to(base).as_posix()
        document = _parsed(path)
        if document is None:
            # A candidate that will not parse is reported rather than dropped: a
            # file in a pack directory that nothing can read is the one case
            # where silence would be taken for approval.
            reports.append(
                ManifestReport(
                    path=relative,
                    name=path.stem,
                    findings=(
                        Finding(
                            at="<document>",
                            klass="schema",
                            message="is not YAML this project can read, so it "
                            "cannot be validated",
                        ),
                    ),
                )
            )
            continue
        if not _is_manifest(document):
            continue
        reports.append(
            _validate_one(
                path, relative, document, where, vocabulary, artifacts, published
            )
        )
    return ValidateReport(
        directory=base.as_posix(), reports=tuple(reports), unreadable=tuple(unreadable)
    )


def _validate_one(
    path: Path,
    relative: str,
    document: dict[str, object],
    where: Path,
    vocabulary: Vocabulary,
    artifacts: ManifestArtifacts,
    published: BehaviourVocabulary,
) -> ManifestReport:
    """One manifest, against the schema and then the sandbox.

    Both run even when the first finds something. A manifest written against the
    previous version fails the schema and may still be structurally readable, and
    a caller fixing a pack wants the whole list rather than the first line of it
    -- which is the difference between this verb and `install_pack`, whose job is
    to refuse and stop.
    """
    findings: list[Finding] = []
    flags: tuple[str, ...] = ()

    verdict = engine_manifest.validate_manifest(
        engine_manifest.Manifest(path=path, document=document),
        artifacts,
        vocabulary,
    )
    for failure in verdict.failures:
        findings.append(
            Finding(
                at=failure.path or "<document>",
                klass=_klass(failure.reason),
                message=failure.message,
            )
        )

    try:
        projected = pack_sandbox.load_pack(path)
    except pack_sandbox.MalformedPackError as exc:
        findings.append(Finding(at="<document>", klass="schema", message=str(exc)))
        projected = None
    if projected is not None:
        result = pack_sandbox.check_pack(projected, where, vocabulary, published)
        for refusal in result.refusals:
            findings.append(
                Finding(
                    at=refusal.where,
                    klass=_klass(refusal.reason),
                    message=refusal.message,
                )
            )
        flags = tuple(flag.service for flag in result.flags)

    return ManifestReport(
        path=relative,
        name=str(document["name"]),
        findings=tuple(findings),
        flags=flags,
    )


def diff_permissions(
    before: str | Path, after: str | Path, *, root: Path | None = None
) -> PermissionDiff:
    """What `after` may do that `before` could not, and the other way round.

    Computed from each manifest's own behaviours and from nothing else, so a
    behaviour added without a permission list being edited shows up as a gain --
    which is the update this verb exists to make visible. No house is opened and
    no validation runs: the question is asked before installation is decided, and
    a verb that needed an install would be answering it too late to matter.
    """
    old = _manifest_pack(before)
    new = _manifest_pack(after)
    policy = Vocabulary.load(_where(root)).pack_policy

    was = old.effective_permissions
    now = new.effective_permissions
    gained = tuple(
        Gain(
            service=service,
            behaviour=_declared_by(new, service),
            klass=_gain_class(policy, service),
        )
        for service in sorted(now - was)
    )
    return PermissionDiff(
        before=Path(before).as_posix(),
        after=Path(after).as_posix(),
        gained=gained,
        lost=tuple(sorted(was - now)),
    )


def _manifest_pack(path: str | Path) -> pack_sandbox.Pack:
    """A manifest projected to the facts the sandbox reads, or a usage error."""
    source = Path(path)
    if not source.is_file():
        raise UsageError(str(source), "is not a file, so no permissions were read")
    try:
        return pack_sandbox.load_pack(source)
    except pack_sandbox.MalformedPackError as exc:
        raise UsageError(str(source), f"is not a readable pack: {exc}") from exc


def _declared_by(pack: pack_sandbox.Pack, service: str) -> str | None:
    """The first behaviour whose `services` clause declares `service`.

    The first rather than all of them, because the requirement asks the diff to
    name the behaviour clause that declares it -- one name a reader can go to.
    Manifest order, not sorted, so which one it is does not change with how the
    file happens to be laid out.
    """
    for behaviour in pack.behaviours:
        if service in behaviour.services:
            return behaviour.name
    return None


def _gain_class(policy: PackPolicy, service: str) -> str:
    """Whether a gained permission is banned, flagged, or ordinary.

    A gain is classified *before* it is installed, which is the whole of what
    this verb adds over reading two files: the same classification `install_pack`
    would apply, asked early enough to act on.
    """
    if policy.bans(service):
        return "banned"
    if service in policy.flagged_services:
        return "flagged"
    return "plain"


# -- 8.1: the derivation over the corpus ------------------------------------


#: Every reason a candidate row can be skipped, closed for the reason
#: `engine.manifest.REASONS` is closed: a caller reporting the bound groups by
#: these, and a reason invented at the point of a skip is a reason no caller can
#: enumerate. Four rather than the two the requirement names, and each is
#: reachable:
#:
#: - `ideas_only` -- the row's own `reuse_status`, which is the corpus's
#:   statement that its expression may not be reproduced. Sixty-four rows.
#: - `licence_too_restrictive` -- the row's licence is `no_licence`, the code
#:   that grants nothing, so no pack may be derived from it whatever its status
#:   says. No row of the committed corpus reaches this, because a row's status
#:   is derived from its licence and the two cannot disagree there; it is the arm
#:   a corpus edited by hand lands in, and a test drives it.
#: - `no_expression` -- the row is reusable and licence-clear and the corpus
#:   carries no expression to reproduce, or carries one a single behaviour clause
#:   cannot hold. Eighteen rows, and the reason the derived set is one pack and
#:   not nineteen.
#: - `refused` -- the pack the derivation built is refused by the schema or by
#:   the sandbox. A pack that cannot validate is not written at all, and the row
#:   is named with the refusal rather than a half-made file appearing.
SKIP_REASONS: tuple[str, ...] = (
    "ideas_only",
    "licence_too_restrictive",
    "no_expression",
    "refused",
)

#: The one `reuse_status` a row must carry for its expression to ground a pack.
_REUSABLE = "reusable"

#: The licence code that grants nothing to reproduce. Named rather than compared
#: by rank, because the code that means "no grant" is a fact about the vocabulary
#: and not the top of an order that could grow past it.
_NOTHING_GRANTED = "no_licence"

#: The first line of every file the derivation writes, which is also what the
#: sweep that keeps two runs from accumulating reads. A marker rather than an
#: extension, for the reason `packs/official/HANDWRITTEN` is a marker: the
#: derivation may only remove the files it can prove it wrote.
GENERATED_MARKER = (
    "# Generated by `openhouse.pack_verbs.derive_packs` from `catalog/behaviors.yaml`"
    " -- do not edit by hand."
)

#: The axes a corpus row's expression carries, in the order the manifest's
#: behaviour clause carries them.
_EXPRESSION_AXES: tuple[str, ...] = ("trigger", "condition", "action")

#: The clauses every derived pack states, and the one it deliberately does not.
#: `priority` is absent because `catalog/pack-policy.yaml` publishes the default
#: and a derived pack has no rank of its own to state; `engine_api` is the range
#: every pack this phase ships declares.
_DERIVED_VERSION = "1.0.0"
_DERIVED_ENGINE_API = ">=1.0.0 <2.0.0"

#: The class every derived pack pins its artefact as. The artefact is written as
#: an automation because that is what it is, and `engine.sandbox.file_class` reads
#: the class off the file rather than trusting this constant.
_DERIVED_CLASS = "automation"

#: `yaml.safe_dump`'s line width, pinned so two runs of PyYAML's own defaults
#: cannot fold a long description two ways.
_DUMP_WIDTH = 88


@dataclass(frozen=True, slots=True)
class DerivedPack:
    """One pack the derivation produced, and the bytes it produced for it.

    The two texts are carried rather than rendered again by the writer, so that
    "two runs produce byte-identical packs" is a property of the report and can
    be asserted without writing anything, and so that the tree on disk can be
    compared against the report that claims to have produced it.
    """

    row: str
    name: str
    license: str
    manifest_path: str
    artefact_path: str
    manifest_text: str
    artefact_text: str

    def to_document(self) -> dict[str, object]:
        return {
            "row": self.row,
            "name": self.name,
            "license": self.license,
            "manifest": self.manifest_path,
            "artefact": self.artefact_path,
        }


@dataclass(frozen=True, slots=True)
class SkippedRow:
    """One candidate row no pack was derived from, and the reason why."""

    row: str
    reason: str
    message: str

    def to_document(self) -> dict[str, object]:
        return {"row": self.row, "reason": self.reason, "message": self.message}


@dataclass(frozen=True, slots=True)
class CorpusBound:
    """The corpus's size, as a measurement rather than a promise.

    The requirement's own numbers -- nineteen reusable rows against sixty-four
    `ideas_only` ones -- are what a reader is asked to trust, and a report that
    restated them as constants would keep saying nineteen after the corpus
    changed. Counting them here is what makes the derived set's size a stated
    consequence of the corpus.
    """

    rows: int
    reusable: int
    ideas_only: int
    licences: Mapping[str, int]

    def to_document(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "reusable": self.reusable,
            "ideas_only": self.ideas_only,
            "licences": dict(self.licences),
        }


@dataclass(frozen=True, slots=True)
class DerivationReport:
    """What the derivation produced, what it skipped, and the corpus behind it.

    `accounted` is the field that makes "it does not silently cap its output"
    checkable: every row of the corpus is either in `emitted` or in `skipped`, so
    a reader who adds the two gets the corpus's own size back.
    """

    destination: str
    bound: CorpusBound
    emitted: tuple[DerivedPack, ...] = ()
    skipped: tuple[SkippedRow, ...] = ()
    written: bool = False

    @property
    def ok(self) -> bool:
        """Whether the derivation produced anything. Nothing is not a pass."""
        return bool(self.emitted)

    @property
    def accounted(self) -> int:
        """Every row the report speaks for, emitted and skipped together."""
        return len(self.emitted) + len(self.skipped)

    def reasons(self) -> Mapping[str, int]:
        """How many rows each skip reason accounts for, every reason a key."""
        return {
            reason: sum(1 for row in self.skipped if row.reason == reason)
            for reason in SKIP_REASONS
        }

    def to_document(self) -> dict[str, object]:
        return {
            "destination": self.destination,
            "written": self.written,
            "bound": self.bound.to_document(),
            "accounted": self.accounted,
            "emitted": [pack.to_document() for pack in self.emitted],
            "skipped": [row.to_document() for row in self.skipped],
            "skipped_by_reason": dict(self.reasons()),
        }


def derive_packs(
    destination: str | Path | None = None,
    *,
    root: Path | None = None,
    write: bool = True,
) -> DerivationReport:
    """Emit one pack per corpus row whose expression may be reproduced.

    `root` is the tree the derived packs' `provides` paths are written against,
    and it is the project's by default -- the same second root `validate_directory`
    takes and for the same reason: resolution is the tree's, judgement is the
    project's. `destination` defaults to `<root>/packs/derived` and must be inside
    `root`, because a `provides` path is a path within a tree and a pack outside
    it could not name the file it confers.

    Raising `UsageError` for a destination outside the tree and for a corpus the
    derivation could not read is the same stance the two verbs take: a path that
    is wrong is the caller's mistake, and an empty corpus read as "nothing to
    derive" would report a clean run over a file nothing opened.

    With `write` the derived tree is written and the derivation's own previous
    output is swept first, so a pack whose row left the corpus does not survive as
    a file nothing accounts for. Only marked files are swept: the tree may hold a
    person's note beside the generated packs, and the marker is what tells them
    apart.
    """
    where = _where(root)
    target = (
        Path(destination) if destination is not None else where / "packs" / "derived"
    )
    relative = _relative(target, where)
    if relative is None:
        raise UsageError(
            str(target),
            f"is not inside {where.as_posix()}, so a `provides` path could not "
            "name the packs it would hold",
        )

    project = _where(None)
    artifacts = load_manifest_artifacts(project)
    vocabulary = Vocabulary.load(project)
    published = load_behaviour_vocabulary(project)

    rows = catalog_behaviors.load_behaviors()
    if not rows:
        raise UsageError(
            "catalog/behaviors.yaml", "holds no rows, so nothing was derived"
        )

    emitted: list[DerivedPack] = []
    skipped: list[SkippedRow] = []
    for row in rows:
        identifier = _row_text(row, "id")
        if identifier is None:
            raise UsageError(
                "catalog/behaviors.yaml",
                "holds a row with no `id`, so the row cannot be named in a report",
            )
        outcome = _derive_row(
            row, identifier, relative, artifacts, vocabulary, published
        )
        if isinstance(outcome, DerivedPack):
            emitted.append(outcome)
        else:
            skipped.append(outcome)

    report = DerivationReport(
        destination=target.as_posix(),
        bound=_bound(rows),
        emitted=tuple(emitted),
        skipped=tuple(skipped),
    )
    if not write:
        return report
    _write(where, target, report.emitted)
    return replace(report, written=True)


def _derive_row(
    row: Mapping[str, object],
    identifier: str,
    relative: str,
    artifacts: ManifestArtifacts,
    vocabulary: Vocabulary,
    published: BehaviourVocabulary,
) -> DerivedPack | SkippedRow:
    """One corpus row's outcome: a pack, or the reason there is none.

    The pack is built and then *validated* before it is returned, against the two
    authorities the shipped tree is judged by. That is not belt-and-braces: a row
    whose expression named a banned service would produce a pack the sandbox
    refuses, and a derivation that wrote it would be putting a file in the shipped
    tree that `validate` reports as broken. A pack that cannot pass is a row
    skipped with the refusal named.
    """
    status = _row_text(row, "reuse_status") or ""
    if status != _REUSABLE:
        return SkippedRow(
            row=identifier,
            reason="ideas_only",
            message=(
                f"the row's `reuse_status` is {status!r}; only a `{_REUSABLE}` row "
                "may ground a derived pack, so this row may inform a hand-written "
                "pack and no pack is derived from it"
            ),
        )

    licence = _row_text(row, "license") or ""
    codes = artifacts.licences.codes
    if licence == _NOTHING_GRANTED or licence not in codes:
        return SkippedRow(
            row=identifier,
            reason="licence_too_restrictive",
            message=(
                f"the row is licensed {licence!r}, which grants nothing to "
                "reproduce; the published codes are "
                f"{', '.join(codes)}, and a pack derived from this row would "
                "claim a grant the source does not make"
            ),
        )

    terms = _expression(row.get("expression"))
    if terms is None:
        return SkippedRow(
            row=identifier,
            reason="no_expression",
            message=(
                "the corpus carries no expression for this row, or carries one a "
                "single behaviour clause cannot hold, so there is no expression to "
                "reproduce and no pack is derived from it"
            ),
        )
    trigger, condition, services = terms

    name = identifier.replace(".", "_")
    behaviour = identifier.rsplit(".", 1)[-1]
    requires = _names(row, "required_slots")
    optional = _names(row, "optional_slots")
    description = _row_text(row, "description") or identifier
    title = _row_text(row, "name") or identifier

    provides_path = f"{relative}/{name}/{name}.yaml"
    artefact = _artefact_document(title, trigger, services, requires)
    document = _manifest_document(
        identifier=identifier,
        name=name,
        behaviour=behaviour,
        title=title,
        description=description,
        licence=licence,
        requires=requires,
        optional=optional,
        trigger=trigger,
        condition=condition,
        services=services,
        provides_path=provides_path,
    )

    refusal = _refusal(
        document, artefact, relative, name, artifacts, vocabulary, published
    )
    if refusal is not None:
        return SkippedRow(row=identifier, reason="refused", message=refusal)

    provenance = _provenance(row, identifier, title)
    return DerivedPack(
        row=identifier,
        name=name,
        license=licence,
        manifest_path=f"{relative}/{name}.yaml",
        artefact_path=provides_path,
        manifest_text=_render(document, provenance),
        artefact_text=_render(artefact, provenance),
    )


def _refusal(
    document: dict[str, object],
    artefact: dict[str, object],
    relative: str,
    name: str,
    artifacts: ManifestArtifacts,
    vocabulary: Vocabulary,
    published: BehaviourVocabulary,
) -> str | None:
    """The first refusal the pack gets, judged against a copy of what it will be.

    A copy, because one of the sandbox's four rules is that every `provides` path
    names a file inside the pack -- and a rule about a file cannot be asked of a
    document. So the pack is written into a scratch tree laid out exactly as the
    derived tree is, judged there, and the scratch thrown away; what survives is
    the answer, and the real tree is written only if the answer was "nothing".
    Judging it in place instead would mean a refused pack had already been written
    to the shipped tree by the time it was refused.
    """
    with tempfile.TemporaryDirectory(prefix="openhouse-derive-") as scratch_text:
        scratch = Path(scratch_text)
        manifest_path = scratch / relative / f"{name}.yaml"
        artefact_path = scratch / relative / name / f"{name}.yaml"
        artefact_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(_render(document, ""), encoding="utf-8")
        artefact_path.write_text(_render(artefact, ""), encoding="utf-8")
        manifest = engine_manifest.Manifest(path=manifest_path, document=document)
        verdict = engine_manifest.validate_manifest(manifest, artifacts, vocabulary)
        if not verdict.ok:
            return verdict.failures[0].message
        result = pack_sandbox.check_pack(
            pack_sandbox.project_pack(document, manifest_path),
            scratch,
            vocabulary,
            published,
        )
        if not result.ok:
            return result.refusals[0].message
    return None


def _manifest_document(
    *,
    identifier: str,
    name: str,
    behaviour: str,
    title: str,
    description: str,
    licence: str,
    requires: tuple[str, ...],
    optional: tuple[str, ...],
    trigger: str | None,
    condition: str | None,
    services: tuple[str, ...],
    provides_path: str,
) -> dict[str, object]:
    """The manifest one row grounds, as the document the schema judges.

    The clauses that come from the row are the row's own -- its slots, its
    licence, its title and description, the terms its expression names -- and the
    clauses that come from the pack format are this phase's. `derives_from` names
    the one row, so the pack's provenance travels with the pack rather than only
    in the report of the machine that ran the derivation.
    """
    clause: dict[str, object] = {"name": behaviour}
    if trigger is not None:
        clause["trigger"] = trigger
    if condition is not None:
        clause["condition"] = condition
    clause["action"] = "service"
    clause["services"] = list(services)
    clause["slots"] = list(requires)
    return {
        "name": name,
        "version": _DERIVED_VERSION,
        "description": description,
        "kind": "behavior",
        "engine_api": _DERIVED_ENGINE_API,
        "license": licence,
        "requires_slots": list(requires),
        "optional_slots": list(optional),
        "provides": [{"path": provides_path, "class": _DERIVED_CLASS}],
        "behaviours": [clause],
        "i18n": {
            "default": {"pack": title, "description": description, behaviour: title}
        },
        "derives_from": [identifier],
    }


def _artefact_document(
    title: str,
    trigger: str | None,
    services: tuple[str, ...],
    requires: tuple[str, ...],
) -> dict[str, object]:
    """The file a derived pack pins, written as the automation it is.

    The class is read off this document rather than declared into it: it carries
    a `trigger` when the row's expression named one and an `action` always, which
    is what `engine.sandbox.file_class` reads as an automation. The entity the
    act names is the pack's own slot and never a device id, because a derived pack
    is installed into a room it has never seen.
    """
    document: dict[str, object] = {"alias": title}
    if trigger is not None:
        document["trigger"] = [{"platform": trigger}]
    action = requires[-1] if requires else None
    entries: list[dict[str, object]] = []
    for service in services:
        entry: dict[str, object] = {"service": service}
        if action is not None:
            entry["target"] = {"entity_id": action}
        entries.append(entry)
    document["action"] = entries
    return document


def _expression(
    value: object,
) -> tuple[str | None, str | None, tuple[str, ...]] | None:
    """The terms a row's expression names, or `None` when it names none a pack holds.

    Four ways an expression is not reproducible, and they are one answer because
    they are one fact -- there is nothing here to reproduce as a pack:

    - the corpus carries none at all, which is the case for eighteen of the
      nineteen reusable rows;
    - it names no action term, and a behaviour's act is what a pack is for, so a
      `service` clause with an empty permission list is a pack that cannot do
      anything;
    - it names more than one trigger term, or more than one condition term, and
      the manifest's behaviour clause carries exactly one of each. Truncating a
      row to its first would be the silent edit this derivation exists not to
      make, and the alternative -- one pack per term -- would be inventing a
      second pack the row does not ground.
    """
    if not isinstance(value, Mapping):
        return None
    expression = cast("Mapping[str, object]", value)
    axes = {axis: _names(expression, axis) for axis in _EXPRESSION_AXES}
    trigger = axes["trigger"]
    condition = axes["condition"]
    actions = axes["action"]
    if not actions or len(trigger) > 1 or len(condition) > 1:
        return None
    return (
        trigger[0] if trigger else None,
        condition[0] if condition else None,
        actions,
    )


def _provenance(row: Mapping[str, object], identifier: str, title: str) -> str:
    """The comment every derived file carries: the row, its sources, its duties.

    Attribution is an obligation the corpus records per row, and a pack copied out
    of this repository takes its provenance with it only if the provenance is in
    the pack. The `derives_from` clause names the row and this names the row's
    sources and what the corpus says a reuse owes them.
    """
    repos = ", ".join(_names(row, "source_repos")) or "no recorded source"
    obligations = ", ".join(_names(row, "obligations")) or "none"
    return (
        f"Derived from the corpus row `{identifier}` ({title}), after {repos}.\n"
        f"Obligations the corpus records for that row: {obligations}."
    )


def _render(document: Mapping[str, object], provenance: str) -> str:
    """A document as the text a derived file holds, marker and provenance first."""
    header = "\n".join(f"# {line}" if line else "#" for line in provenance.splitlines())
    body = yaml.safe_dump(
        dict(document),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=_DUMP_WIDTH,
    )
    return f"{GENERATED_MARKER}\n{header}\n{body}"


def _bound(rows: Sequence[Mapping[str, object]]) -> CorpusBound:
    """The corpus's size, counted rather than restated."""
    reusable = [row for row in rows if _row_text(row, "reuse_status") == _REUSABLE]
    licences: dict[str, int] = {}
    for row in reusable:
        code = _row_text(row, "license") or "unknown"
        licences[code] = licences.get(code, 0) + 1
    return CorpusBound(
        rows=len(rows),
        reusable=len(reusable),
        ideas_only=len(rows) - len(reusable),
        licences={code: licences[code] for code in sorted(licences)},
    )


def _write(where: Path, target: Path, emitted: tuple[DerivedPack, ...]) -> None:
    """Write the derived tree over the derivation's own previous output.

    The sweep is by marker and not by extension, and it runs before anything is
    written: a file the derivation cannot prove it wrote is left alone, and the
    directories its own files were the last occupants of are removed, so two runs
    over one corpus leave one tree rather than the first run's leftovers plus the
    second's.
    """
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(target.rglob("*")):
        if path.is_file() and _generated(path):
            path.unlink()
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for pack in emitted:
        for relative, text in (
            (pack.manifest_path, pack.manifest_text),
            (pack.artefact_path, pack.artefact_text),
        ):
            destination = where / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8", newline="")


def _generated(path: Path) -> bool:
    """Whether a file carries the derivation's marker on its first line."""
    try:
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
    except (OSError, UnicodeDecodeError):
        return False
    return first == GENERATED_MARKER


def _relative(path: Path, root: Path) -> str | None:
    """A path relative to a root, or `None` when it is not under it.

    The same reading `engine.manifest` makes for the hand-written marker, and for
    the same reason: a path outside the tree answers "no" rather than raising a
    `ValueError` about the derivation instead of about the caller's argument.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _row_text(row: Mapping[str, object], field: str) -> str | None:
    """A row's own string field, or `None` when it is absent or not a string."""
    value = row.get(field)
    return value if isinstance(value, str) else None


def _names(row: Mapping[str, object], field: str) -> tuple[str, ...]:
    """A row's list of names, with anything that is not a string dropped.

    The corpus schema guarantees the list and its items, and this still filters:
    it reads a file rather than a document that schema has already accepted, and
    a row edited by hand should produce a skipped row and not a `TypeError` from
    somewhere inside a renderer.
    """
    value = row.get(field)
    if not isinstance(value, list):
        return ()
    items = cast("list[object]", value)
    return tuple(item for item in items if isinstance(item, str))


# -- 10.2: running a pack against the scenarios a caller names ----------------


#: How a scenario ended when a pack was under test. `untested` is deliberately
#: not one of them: a pack no scenario reaches has no outcome, and the report
#: carries that as a property rather than as a class, because "the run passed"
#: and "no run happened" are the one pair this verb exists to keep apart.
OUTCOMES: tuple[str, ...] = ("passed", "failed", "fixture_error", "pack_error")


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """One scenario's run against a pack, or why it did not run.

    The classes name different authors, which is why they are not one "failed": a
    `fixture_error` is a scenario that would not load and is the corpus's to fix,
    a `pack_error` is the house refusing to install the pack and is the pack's,
    and `failed` is a run that happened and disagreed with its own assertions and
    is the scenario's.
    """

    scenario: str
    outcome: str
    message: str = ""

    @property
    def passed(self) -> bool:
        """Whether the run happened and its assertions held."""
        return self.outcome == "passed"

    def to_document(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "outcome": self.outcome,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PackTestReport:
    """What running one pack against a set of scenarios said.

    `untested` is a verdict and `ok` is false when it holds: a pack no scenario
    names has been demonstrated by nothing, and reporting that as a pass is the
    one answer a test verb must not give -- the same reason `validate` refuses to
    call an empty directory fine.
    """

    manifest: str
    name: str
    outcomes: tuple[ScenarioOutcome, ...] = ()

    @property
    def untested(self) -> bool:
        """Whether no scenario was named, or none of the ones named loaded."""
        return not self.outcomes

    @property
    def ok(self) -> bool:
        """Whether at least one scenario ran and every one of them passed."""
        return bool(self.outcomes) and all(outcome.passed for outcome in self.outcomes)

    def to_document(self) -> dict[str, object]:
        return {
            "manifest": self.manifest,
            "name": self.name,
            "tested": not self.untested,
            "passed": self.ok,
            "outcomes": [outcome.to_document() for outcome in self.outcomes],
        }


def test_pack(
    manifest: str | Path,
    *,
    scenarios: Sequence[str | Path] = (),
    root: Path | None = None,
    seed: int | None = None,
    started_at: datetime | None = None,
) -> PackTestReport:
    """Install `manifest` into a fresh session per scenario and run each one.

    A session per scenario and not one session for the set, because a scenario's
    `given` block names the house it runs against and a corpus is a set of runs
    rather than one run continued -- the same reason `openhouse.scenarios` opens
    one session per scenario. The pack is installed after the session is opened
    and before the first step, so a house the pack does not belong to is a
    `pack_error` for that scenario rather than a step that fails for a reason the
    scenario never wrote.

    `seed` and `started_at` override every scenario's own `given`, applied by
    opening the session at them rather than by rewriting the scenario, which is
    the override rule `openhouse.scenarios` states once and this verb inherits.

    A manifest that cannot be read, and a root whose vocabulary cannot be read,
    are `UsageError`s: the caller named them, and neither is a fact about a
    scenario or a pack.
    """
    where = _where(root)
    path = Path(manifest)
    try:
        loaded = pack_installation.load_manifest(path)
    except pack_installation.PackError as error:
        raise UsageError(str(path), error.reason) from error
    try:
        vocabulary = Vocabulary.load(where)
    except VocabularyError as error:
        raise UsageError(str(where), str(error)) from error

    name = _row_text(loaded, "name") or path.stem
    outcomes: list[ScenarioOutcome] = []
    for target in scenarios:
        try:
            corpus = _scenarios(Path(target))
        except sim_scenario.LoadError as error:
            outcomes.append(ScenarioOutcome(str(target), "fixture_error", str(error)))
            continue
        for scenario in corpus:
            outcomes.append(_run_scenario(scenario, path, vocabulary, seed, started_at))
    return PackTestReport(str(path), name, tuple(outcomes))


def _scenarios(target: Path) -> tuple[sim_scenario.Scenario, ...]:
    """What a path names: the scenario itself, or every one inside a directory."""
    if target.is_dir():
        return sim_scenario.load_directory(target)
    return (sim_scenario.load_scenario(target),)


def _run_scenario(
    scenario: sim_scenario.Scenario,
    manifest: Path,
    vocabulary: Vocabulary,
    seed: int | None,
    started_at: datetime | None,
) -> ScenarioOutcome:
    """One scenario's run under a freshly installed pack, or the class it failed as."""
    try:
        session = openhouse_scenarios.open_scenario(
            scenario, vocabulary=vocabulary, seed=seed, started_at=started_at
        )
        session.install_pack(str(manifest))
    except pack_installation.PackError as error:
        return ScenarioOutcome(scenario.path, "pack_error", str(error))
    try:
        sim_scenario.run_scenario(scenario, session)
    except sim_scenario.ScenarioFailed as failure:
        return ScenarioOutcome(scenario.path, "failed", failure.report.summary())
    return ScenarioOutcome(scenario.path, "passed")
