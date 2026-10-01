"""The checks a publishing pull request runs, and what each one answers.

Five checks, each answering a question the others do not:

- `schema` -- is this pointer a pointer at all. A malformed pointer is a file
  the store cannot read, which is the one failure that makes every later
  question moot.
- `permissions` -- what can this pack do. The pack is validated against its own
  schema and against the capability sandbox, reusing `openhouse.pack_verbs`
  rather than restating the rules; the tier then decides whether the dangerous
  permissions it carries are acceptable, and a banned service is refused in
  every tier.
- `permission-diff` -- what can this version do that the last one could not. The
  diff is `openhouse.pack_verbs.diff_permissions`, so the CI check and the CLI
  verb cannot come to different answers. Any gain is a finding: an update that
  widens a pack's grant is exactly the update a person has to look at, and a
  silent one is what the gating exists to prevent.
- `typosquat` -- is this name confusable with one already published, which is
  how a malicious pack borrows a trusted name.
- `revocation` -- has this pack, version or digest been revoked.

`run_checks` composes them into one report against one submission. It collects
everything rather than stopping at the first failure, because the fixes differ
-- a schema failure is a file rewritten, a permission gain is a decision someone
has to make -- and a publisher acting on the first line of a list would fix one
thing and resubmit into the next.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openhouse import pack_verbs

from .index import Revocation, load_revocations
from .layout import default_root
from .pointer import Pointer, pointer_schema, schema_errors
from .tiers import load_tiers

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .tiers import Tier

__all__ = [
    "CHECK_NAMES",
    "CheckFinding",
    "CheckReport",
    "Submission",
    "check_permission_diff",
    "check_permissions",
    "check_revocation",
    "check_schema",
    "check_tier",
    "check_typosquat",
    "run_checks",
]

#: The check names a report can carry, closed so a reader can enumerate them.
CHECK_NAMES: tuple[str, ...] = (
    "schema",
    "tier",
    "permissions",
    "permission-diff",
    "typosquat",
    "revocation",
)

#: Characters a pack name may separate words with. Stripped before two names are
#: compared, because `motion-light` and `motion_light` read as the same name to
#: a person and must not read as two to the check.
_SEPARATORS = str.maketrans("", "", "-_.")

#: Characters that are visually confusable, folded to one representative before
#: a comparison. This is the cheap half of a homoglyph check: it catches the
#: digit-for-letter swaps a name-squatter reaches for first, and it deliberately
#: does not attempt the Unicode confusables table, which belongs to a dependency
#: this project does not carry.
_CONFUSABLES = str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "8": "b"})


@dataclass(frozen=True, slots=True)
class CheckFinding:
    """One reason a submission failed, and which check found it."""

    check: str
    where: str
    message: str

    def to_document(self) -> dict[str, object]:
        return {"check": self.check, "where": self.where, "message": self.message}


@dataclass(frozen=True, slots=True)
class Submission:
    """One pointer under review, and the pack it resolves to.

    `root` is the repository the pack's `provides` paths are written against --
    the schema calls them *repo-relative* -- and it is the same base the engine
    resolves them against at install, so a check and the install it stands in
    for cannot answer differently. The sandbox's containment rule is what makes
    resolving against that tree safe.
    """

    pointer: Pointer
    manifest: Path
    root: Path
    previous: Path | None = None

    @property
    def where(self) -> str:
        """The submission named the way a report names it."""
        return f"{self.pointer.name} {self.pointer.version}"


@dataclass(frozen=True, slots=True)
class CheckReport:
    """Everything one submission's checks found."""

    submission: str
    findings: tuple[CheckFinding, ...]

    @property
    def ok(self) -> bool:
        """Whether the submission cleared every check that ran."""
        return not self.findings

    def to_document(self) -> dict[str, object]:
        return {
            "submission": self.submission,
            "passed": self.ok,
            "findings": [finding.to_document() for finding in self.findings],
        }


def check_schema(
    document: Mapping[str, object], registry_root: Path | None = None
) -> tuple[CheckFinding, ...]:
    """Whether `document` is a pointer the schema accepts."""
    root = default_root() if registry_root is None else registry_root
    schema = pointer_schema(root)
    name = document.get("name")
    where = name if isinstance(name, str) else "<pointer>"
    return tuple(
        CheckFinding(check="schema", where=where, message=message)
        for message in schema_errors(document, schema)
    )


def check_tier(pointer: Pointer, tiers: Mapping[str, Tier]) -> tuple[CheckFinding, ...]:
    """Whether the pointer names a tier that exists."""
    if pointer.tier in tiers:
        return ()
    return (
        CheckFinding(
            check="tier",
            where=pointer.tier,
            message=f"names the tier {pointer.tier!r}, which is not one of "
            f"{tuple(tiers)}",
        ),
    )


def check_revocation(
    pointer: Pointer, revocations: Sequence[Revocation]
) -> tuple[CheckFinding, ...]:
    """Whether the pack, version or digest has been revoked."""
    return tuple(
        CheckFinding(
            check="revocation",
            where=f"{pointer.name} {pointer.version}",
            message=f"is revoked: {revocation.reason}",
        )
        for revocation in revocations
        if revocation.revokes(pointer)
    )


def check_permissions(
    submission: Submission, tiers: Mapping[str, Tier], registry_root: Path | None = None
) -> tuple[CheckFinding, ...]:
    """What the pack may do, and whether its tier accepts that.

    The pack's own schema and the capability sandbox are read through
    `openhouse.pack_verbs.validate_directory`, which is the surface's own
    validator: a second implementation here would be a second answer to "is this
    pack well-formed", and the check that matters is the one the author already
    runs. Each finding it returns carries its class, so a banned service and a
    schema failure stay distinguishable in the report.

    The tier's one rule is about *flagged* services -- dangerous but legitimate
    ones -- and not about banned ones, which no tier permits: a community pack
    is accepted without review only because it carries no dangerous permission,
    and that is what makes "no review" a safe default for it.
    """
    report = pack_verbs.validate_directory(
        submission.manifest.parent, root=submission.root
    )
    return _permission_findings(submission, report, tiers)


def _permission_findings(
    submission: Submission,
    report: pack_verbs.ValidateReport,
    tiers: Mapping[str, Tier],
) -> tuple[CheckFinding, ...]:
    relative = submission.manifest.name
    findings: list[CheckFinding] = []
    for manifest in report.reports:
        if Path(manifest.path).name != relative:
            continue
        for finding in manifest.findings:
            findings.append(
                CheckFinding(
                    check=f"permissions:{finding.klass}",
                    where=submission.where,
                    message=finding.message,
                )
            )
        tier = tiers.get(submission.pointer.tier)
        if manifest.flags and tier is not None and not tier.permits_flagged:
            findings.append(
                CheckFinding(
                    check="permissions",
                    where=submission.where,
                    message=(
                        f"carries the dangerous permission(s) "
                        f"{sorted(manifest.flags)}, which the {tier.name} tier does "
                        "not permit: a pack accepted without review must carry no "
                        "dangerous permission"
                    ),
                )
            )
    if not report.reports:
        findings.append(
            CheckFinding(
                check="permissions",
                where=submission.where,
                message="no manifest was read from the pack, so nothing was checked",
            )
        )
    return tuple(findings)


def check_permission_diff(
    submission: Submission, tiers: Mapping[str, Tier]
) -> tuple[CheckFinding, ...]:
    """What the pack gained over its previous version, and whether that matters.

    Computed by `openhouse.pack_verbs.diff_permissions`, the same verb the CLI
    exposes, so a publisher and CI cannot disagree about what was gained. A gain
    is always a finding: an update that widens a pack's grant is the update a
    person has to look at, and the tier decides only how the *dangerous* ones
    are described. A lost permission is reported by the verb and is not a
    finding here, because narrowing a grant needs no one's review.
    """
    if submission.previous is None:
        return ()
    diff = pack_verbs.diff_permissions(submission.previous, submission.manifest)
    tier = tiers.get(submission.pointer.tier)
    findings: list[CheckFinding] = []
    for gain in diff.gained:
        declared = gain.behaviour or "(no behaviour declares it)"
        danger = ""
        if gain.klass != "plain" and tier is not None:
            danger = {
                "banned": f", which is banned and no {tier.name} pack may call",
                "flagged": f", which is dangerous and must be accepted in the {tier.name} tier",
            }.get(gain.klass, "")
        findings.append(
            CheckFinding(
                check="permission-diff",
                where=f"{submission.pointer.name} {submission.pointer.version}",
                message=(
                    f"adds the permission {gain.service!r} declared by {declared}"
                    f"{danger}; an update that widens a grant needs a person's "
                    "review before it is published"
                ),
            )
        )
    return tuple(findings)


def check_typosquat(
    name: str, existing: Sequence[str], where: str | None = None
) -> tuple[CheckFinding, ...]:
    """Whether `name` is confusable with a pack already published.

    Three ways a name borrows another's: it differs only by separators, it is
    one edit away, or it differs only in visually confusable characters. The
    first two are the common cases and the third catches `m0tion-light` beside
    `motion-light`. An exact match is not a finding here -- republishing a name
    is what a version bump is for, and the index handles that.
    """
    subject = where if where is not None else name
    folded = _folded(name)
    finders = tuple(existing)
    findings: list[CheckFinding] = []
    for other in sorted(set(finders)):
        if other == name:
            continue
        if _folded(other) == folded:
            findings.append(
                CheckFinding(
                    check="typosquat",
                    where=subject,
                    message=f"reads as {other!r} once separators are ignored",
                )
            )
            continue
        if _distance(folded, _folded(other)) == 1:
            findings.append(
                CheckFinding(
                    check="typosquat",
                    where=subject,
                    message=f"is one character from the published name {other!r}",
                )
            )
            continue
        if _confusable(name) == _confusable(other):
            findings.append(
                CheckFinding(
                    check="typosquat",
                    where=subject,
                    message=f"differs from the published name {other!r} only by "
                    "confusable characters",
                )
            )
    return tuple(findings)


def run_checks(
    submission: Submission,
    *,
    existing_names: Sequence[str] = (),
    registry_root: Path | None = None,
) -> CheckReport:
    """Every check, against one submission, as one report.

    The names to compare against are the caller's rather than read from the
    index here, because the submission is being checked *before* it is indexed:
    deriving the existing set from the file the submission will join would make
    a first-ever pack compare against itself.
    """
    root = default_root() if registry_root is None else registry_root
    tiers = load_tiers(root)
    findings = (
        check_schema(submission.pointer.to_document(), root)
        + check_tier(submission.pointer, tiers)
        + check_revocation(submission.pointer, load_revocations(root))
        + check_permissions(submission, tiers)
        + check_permission_diff(submission, tiers)
        + check_typosquat(submission.pointer.name, existing_names, submission.where)
    )
    return CheckReport(submission=submission.where, findings=findings)


def _folded(name: str) -> str:
    return name.lower().translate(_SEPARATORS)


def _confusable(name: str) -> str:
    return name.lower().translate(_SEPARATORS).translate(_CONFUSABLES)


def _distance(left: str, right: str) -> int:
    """The Levenshtein distance between two names.

    Iterative and two rows wide, because the names compared here are short and
    the cache a fully recursive form would need is the whole reason to prefer
    this shape in a check that runs over every published name.
    """
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, source in enumerate(left, start=1):
        current = [index]
        for offset, target in enumerate(right, start=1):
            current.append(
                min(
                    previous[offset] + 1,
                    current[offset - 1] + 1,
                    previous[offset - 1] + (source != target),
                )
            )
        previous = current
    return previous[-1]
